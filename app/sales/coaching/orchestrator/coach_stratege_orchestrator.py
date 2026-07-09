"""
CoachStrategeOrchestrator
==========================
Orchestrateur résilient entre CoachAgent et StrategistAgent.

Garantit que le CoachAgent ne plante JAMAIS même si le Stratège est lent ou down.

Pattern :
  1. Cache LRU (30 min) — si gap_pct stable, retour <1ms
  2. Invoke avec timeout 30s
  3. Retry × 3 avec backoff exponentiel
  4. Fallback 3 niveaux (stale cache → actions contextuelles → template statique)
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Dataclass de sortie ────────────────────────────────────────────────────────

@dataclass
class StrategieOutput:
    actions:    List[Dict[str, Any]]
    source:     str        # "success" | "cached" | "stale_cache" | "fallback"
    cached:     bool       = False
    latency_ms: float      = 0.0
    confidence: float      = 0.8
    # Contexte riche produit par le Stratège (cause racine, résumé, focus
    # produits, météo réelle…) — consommé par le Coach Chat pour ancrer
    # ses réponses dans le vrai contexte boutique.
    extras:     Dict[str, Any] = field(default_factory=dict)
    nb_actions: int        = field(init=False)

    def __post_init__(self):
        self.nb_actions = len(self.actions)


def _extract_extras(result: Dict[str, Any]) -> Dict[str, Any]:
    """Extrait du state final du Stratège le contexte utile au Coach."""
    summary = ((result.get("external_context") or {}).get("summary")) or {}
    return {
        "cause_racine":      result.get("cause_racine", ""),
        "strategie_summary": result.get("strategie", ""),
        "focus_produits":    result.get("focus_produits", []) or [],
        "message_manager":   result.get("message_manager", ""),
        "weather_label":     summary.get("weather_label", ""),
        "weather_effect":    summary.get("weather_effect", 0.0),
        "is_holiday":        summary.get("is_holiday", False),
        "holiday_name":      summary.get("holiday_name", ""),
        "critique_score":    result.get("critique_score", 0.0),
        "rag_used":          result.get("rag_used", False),
        "nb_rag_scripts":    result.get("nb_rag_scripts", 0),
    }


# ── Cache LRU simple avec TTL ──────────────────────────────────────────────────

class StrategyCache:
    """Cache mémoire LRU avec TTL — évite les appels répétitifs au Stratège."""

    def __init__(self, ttl_seconds: int = 1800, max_size: int = 50):
        self._cache: Dict[str, tuple] = {}   # key → (output, timestamp)
        self._ttl     = ttl_seconds
        self._max_size = max_size
        self._hits  = 0
        self._misses = 0

    def _key(self, store_id: str, gap_pct: float) -> str:
        return f"{store_id}:{round(gap_pct, 1)}"

    def get(self, store_id: str, gap_pct: float) -> Optional[StrategieOutput]:
        k = self._key(store_id, gap_pct)
        entry = self._cache.get(k)
        if entry:
            output, ts = entry
            if time.time() - ts < self._ttl:
                self._hits += 1
                return output
        self._misses += 1
        return None

    def get_stale(self, store_id: str, gap_pct: float) -> Optional[StrategieOutput]:
        """Retourne une entrée expirée si elle existe (fallback L1)."""
        k = self._key(store_id, gap_pct)
        entry = self._cache.get(k)
        return entry[0] if entry else None

    def set(self, store_id: str, gap_pct: float, output: StrategieOutput) -> None:
        if len(self._cache) >= self._max_size:
            oldest = min(self._cache.items(), key=lambda x: x[1][1])
            del self._cache[oldest[0]]
        k = self._key(store_id, gap_pct)
        self._cache[k] = (output, time.time())

    def stats(self) -> Dict[str, Any]:
        total = self._hits + self._misses
        return {
            "hits":     self._hits,
            "misses":   self._misses,
            "hit_rate": round(self._hits / total, 3) if total > 0 else 0.0,
            "size":     len(self._cache),
        }


# ── Orchestrateur principal ────────────────────────────────────────────────────

class CoachStrategeOrchestrator:
    """
    Orchestrateur résilient Coach → Stratège.

    Utilisation :
        orchestrator = CoachStrategeOrchestrator(stratege_agent)
        result = await orchestrator.invoke(state)
        # result.actions toujours non-vide
    """

    def __init__(
        self,
        stratege_agent,
        timeout:     float = 30.0,
        max_retries: int   = 3,
        ttl_cache:   int   = 1800,
    ):
        self.stratege    = stratege_agent
        self.timeout     = timeout
        self.max_retries = max_retries
        self.cache       = StrategyCache(ttl_seconds=ttl_cache)

    async def invoke(self, state: Dict[str, Any]) -> StrategieOutput:
        store_id = (state.get("pos_data") or {}).get("store_id", "ALL")
        gap_pct  = float(state.get("gap_objectif", 0))
        urgency  = state.get("urgency_level", "MEDIUM")
        t0       = time.time()

        # ── 1. Cache hit ──────────────────────────────────────────────────────
        cached = self.cache.get(store_id, gap_pct)
        if cached:
            logger.info(
                "[ORCHESTRATOR] Cache HIT — %s gap=%.1f%% → %d actions <1ms",
                store_id, gap_pct, cached.nb_actions,
            )
            cached.cached     = True
            cached.source     = "cached"
            cached.latency_ms = (time.time() - t0) * 1000
            return cached

        # ── 2. Invoke avec retry + timeout ────────────────────────────────────
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                result = await asyncio.wait_for(
                    self.stratege.ainvoke(state),
                    timeout=self.timeout,
                )
                actions = result.get("strategie_actions") or []
                if actions:
                    output = StrategieOutput(
                        actions    = actions,
                        source     = "success",
                        cached     = False,
                        latency_ms = (time.time() - t0) * 1000,
                        confidence = 0.9,
                        extras     = _extract_extras(result),
                    )
                    self.cache.set(store_id, gap_pct, output)
                    logger.info(
                        "[ORCHESTRATOR] Stratège OK — %d actions | attempt=%d | %.0fms",
                        len(actions), attempt + 1, output.latency_ms,
                    )
                    return output

            except asyncio.TimeoutError:
                last_error = TimeoutError(f"timeout {self.timeout}s attempt {attempt+1}")
                logger.warning("[ORCHESTRATOR] Timeout attempt %d/%d", attempt + 1, self.max_retries)
            except Exception as e:
                last_error = e
                logger.warning("[ORCHESTRATOR] Error attempt %d/%d: %s", attempt + 1, self.max_retries, e)

            if attempt < self.max_retries - 1:
                await asyncio.sleep(2 ** attempt)

        # ── 3. Fallback ───────────────────────────────────────────────────────
        logger.warning(
            "[ORCHESTRATOR] Stratège indisponible après %d tentatives — fallback activé",
            self.max_retries,
        )

        # L1 : cache expiré (stale)
        stale = self.cache.get_stale(store_id, gap_pct)
        if stale:
            logger.info("[ORCHESTRATOR] Fallback L1 — cache expiré utilisé")
            stale.source     = "stale_cache"
            stale.cached     = True
            stale.latency_ms = (time.time() - t0) * 1000
            return stale

        # L2 + L3 : produits DB ou actions génériques
        return self._build_fallback(gap_pct, urgency, (time.time() - t0) * 1000, store_id)

    def _build_fallback(
        self, gap_pct: float, urgency: str, latency_ms: float,
        store_id: str = "ALL",
    ) -> StrategieOutput:
        """
        Fallback dynamique : lit les produits recommendables depuis la DB.
        Si la DB est aussi indisponible → retourne 3 actions génériques sans produit fixé.
        Zéro produit, prix ou SKU codé en dur.
        """
        actions = self._fetch_fallback_from_db(store_id, gap_pct, urgency)

        if not actions:
            # Dernier recours : actions génériques sans mention de produit spécifique
            actions = self._generic_actions(gap_pct, urgency)

        return StrategieOutput(
            actions    = actions,
            source     = "fallback",
            cached     = False,
            latency_ms = latency_ms,
            confidence = 0.5,
        )

    def _fetch_fallback_from_db(
        self, store_id: str, gap_pct: float, urgency: str,
    ) -> List[Dict[str, Any]]:
        """Requête DB pour récupérer les produits top-marge disponibles en stock."""
        try:
            from app.inventory.repositories.inventory_repo import SyncInventoryRepo

            products = SyncInventoryRepo.get_recommendable_products(
                store_id=store_id, min_stock=1, limit=6,
            ) or []

            if not products:
                return []

            # Tri : urgence élevée → priorité aux stocks critiques (écouler)
            #        urgence faible  → priorité à la marge
            if urgency in ("CRITICAL", "HIGH") or gap_pct > 30:
                products.sort(key=lambda p: (
                    -int(p.get("stock_current", 0) <= 5),  # critiques d'abord
                    -float(p.get("margin_pct", 0)),
                ))
            else:
                products.sort(key=lambda p: -float(p.get("margin_pct", 0)))

            actions = []
            for i, p in enumerate(products[:3], start=1):
                nom    = str(p.get("nom") or p.get("name") or p.get("sku", "Produit"))
                prix   = float(p.get("prix_ttc") or p.get("price") or 0)
                margin = float(p.get("margin_pct") or 0)
                stock  = int(p.get("stock_current") or p.get("stock_qty") or 0)
                promo  = bool(p.get("active_promo", False))

                if stock <= 5:
                    action = f"Écouler en priorité — stock critique ({stock} unité(s))"
                    arg    = f"Dernières unités disponibles — {prix:.0f} TND"
                elif promo:
                    action = f"Mettre en avant la promotion active"
                    arg    = f"Offre spéciale — {prix:.0f} TND"
                else:
                    action = f"Proposer systématiquement en cross-sell"
                    arg    = f"Marge {margin:.0f}% — {prix:.0f} TND"

                actions.append({
                    "priorite":       i,
                    "action":         action,
                    "produit_cible":  nom,
                    "argument_vente": arg,
                    "impact_estime":  f"+{prix:.0f} TND",
                })

            logger.info("[ORCHESTRATOR] Fallback DB — %d produits récupérés", len(actions))
            return actions

        except Exception as e:
            logger.debug("[ORCHESTRATOR] Fallback DB indisponible: %s", e)
            return []

    @staticmethod
    def _generic_actions(gap_pct: float, urgency: str) -> List[Dict[str, Any]]:
        """Actions génériques sans mention de produit — utilisé si DB down."""
        if gap_pct > 30 or urgency in ("CRITICAL", "HIGH"):
            return [
                {"priorite": 1, "action": "Proposer le produit premium le mieux margé en stock",
                 "produit_cible": "Terminal premium", "argument_vente": "Meilleure offre disponible",
                 "impact_estime": "Impact élevé"},
                {"priorite": 2, "action": "Convertir recharges en forfait data",
                 "produit_cible": "Forfait data", "argument_vente": "Plus de data au même prix",
                 "impact_estime": "Récurrent mensuel"},
                {"priorite": 3, "action": "Assurance sur chaque vente terminal",
                 "produit_cible": "Assurance", "argument_vente": "Remplacement rapide garanti",
                 "impact_estime": "Marge haute"},
            ]
        else:
            return [
                {"priorite": 1, "action": "Assurance sur chaque vente terminal",
                 "produit_cible": "Assurance", "argument_vente": "Remplacement rapide garanti",
                 "impact_estime": "Marge haute"},
                {"priorite": 2, "action": "Proposer internet fixe à chaque client",
                 "produit_cible": "Box internet", "argument_vente": "Fibre rapide, installation incluse",
                 "impact_estime": "Récurrent mensuel"},
                {"priorite": 3, "action": "Cross-sell accessoire sur chaque terminal vendu",
                 "produit_cible": "Accessoire", "argument_vente": "Protection indispensable",
                 "impact_estime": "Complément de vente"},
            ]

    def get_stats(self) -> Dict[str, Any]:
        return self.cache.stats()
