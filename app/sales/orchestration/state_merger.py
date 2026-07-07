"""
state_merger.py — Fusion état Sales ↔ Inventory (Phase 2)
==========================================================
Pattern : Fan-In / Merge
  • Lit le snapshot stock depuis Redis (publié par InventoryOrchestrator)
  • Filtre les produits disponibles (stock > 0 dynamiquement)
  • Injecte inventory_snapshot + stock_filtered_products dans SalesAgentState
  • Guardrail G9 (STOCK_INTEGRITY) : retire les SKUs en rupture de la liste
    des recommandations Stratège avant génération
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Seuil « rupture » : stock <= 0 → produit exclu des recommandations
RUPTURE_THRESHOLD = 0


class StateMerger:
    """
    Fusionne les données inventory dans l'état Sales au début de chaque cycle.

    Utilisé par SupervisorAgent (Phase 4) après le fan-out parallèle
    Analyst + Inventory. En Phase 2, il est appelé dans CycleOrchestrator
    juste avant l'appel au Stratège.
    """

    def __init__(self, state_bus=None, max_snapshot_age_s: int = 600):
        """
        state_bus     : instance StateBus (injectée ou lazy-loaded)
        max_snapshot_age_s : snapshot > 10 min → ignoré (données trop vieilles)
        """
        self._bus              = state_bus
        self._max_age_s        = max_snapshot_age_s

    def _get_bus(self):
        if self._bus is None:
            from app.sales.core.state_bus import get_state_bus
            self._bus = get_state_bus()
        return self._bus

    # ── Point d'entrée principal ─────────────────────────────────────────────

    def merge_into_state(
        self,
        state: Dict[str, Any],
        store_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Lit le snapshot Redis et enrichit l'état avec :
          • inventory_snapshot  — données brutes SKU → {stock_qty, risk_level, ...}
          • stock_filtered_products — liste SKUs disponibles (stock > 0) triés par dispo

        Retourne l'état enrichi (copie). Si Redis est indisponible ou snapshot
        trop vieux, l'état est retourné sans modification (mode dégradé).
        """
        sid = store_id or state.get("store_id") or state.get("pos_data", {}).get("store_id", "")
        if not sid:
            logger.warning("[StateMerger] store_id manquant — skip merge")
            return state

        bus      = self._get_bus()
        snapshot = bus.read_inventory_snapshot(sid)

        if snapshot is None:
            logger.info("[StateMerger] Aucun snapshot Redis pour %s — mode dégradé", sid)
            return {**state, "inventory_snapshot": None, "stock_filtered_products": []}

        # Vérification fraîcheur
        if self._is_stale(snapshot.timestamp):
            logger.warning(
                "[StateMerger] Snapshot trop vieux (%s) — ignoré",
                snapshot.timestamp,
            )
            return {**state, "inventory_snapshot": None, "stock_filtered_products": []}

        # Construire la liste filtrée (tout est dynamique depuis Redis)
        available = self._filter_available(snapshot.skus)

        logger.info(
            "[StateMerger] Snapshot mergé : %s | %d SKUs dispo / %d total | %d ruptures",
            sid,
            len(available),
            snapshot.total_skus,
            snapshot.rupture_count,
        )

        snapshot_dict = {
            "store_id":       snapshot.store_id,
            "timestamp":      snapshot.timestamp,
            "skus":           snapshot.skus,
            "critical_count": snapshot.critical_count,
            "rupture_count":  snapshot.rupture_count,
            "healthy_count":  snapshot.healthy_count,
            "total_skus":     snapshot.total_skus,
        }

        return {
            **state,
            "inventory_snapshot":      snapshot_dict,
            "stock_filtered_products": available,
        }

    # ── G9 STOCK_INTEGRITY — filtrage guardrail ──────────────────────────────

    def apply_g9_guardrail(
        self,
        strategie_actions: List[Dict[str, Any]],
        inventory_snapshot: Optional[Dict[str, Any]],
        substitution_map:  Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Guardrail G9 — STOCK_INTEGRITY :
        Retire ou substitue les produits recommandés en rupture de stock.

        Paramètres :
          strategie_actions  : actions générées par le Stratège
          inventory_snapshot : snapshot Redis (dict avec "skus")
          substitution_map   : {product_name → alternative} fourni par DB
                               Toujours dynamique — jamais codé en dur ici

        Retourne les actions avec produits valides (substitution si possible).
        """
        if not inventory_snapshot:
            return strategie_actions

        skus_data      = inventory_snapshot.get("skus", {})
        name_to_stock  = self._build_name_to_stock_map(skus_data)
        subs           = substitution_map or {}

        cleaned = []
        for action in strategie_actions:
            product = action.get("produit_cible", "")
            stock   = self._get_stock_for_product(product, name_to_stock)

            if stock is None:
                # Produit non tracé dans inventory → garder (accessoire, forfait, service)
                cleaned.append(action)
                continue

            if stock > RUPTURE_THRESHOLD:
                cleaned.append(action)
                continue

            # Rupture → chercher substitution dynamique
            substitute = subs.get(product)
            if substitute:
                sub_stock = self._get_stock_for_product(substitute, name_to_stock)
                if sub_stock is not None and sub_stock > RUPTURE_THRESHOLD:
                    logger.info(
                        "[G9] Substitution : '%s' (rupture) → '%s' (stock=%d)",
                        product, substitute, sub_stock,
                    )
                    action = {
                        **action,
                        "produit_cible":  substitute,
                        "argument_vente": (
                            f"[Substitut] {action.get('argument_vente', '')} "
                            f"(stock={sub_stock} unité(s))"
                        ),
                        "_g9_substituted": True,
                    }
                    cleaned.append(action)
                    continue

            # Rupture sans substitution → retirer l'action
            logger.warning(
                "[G9] Action retirée : '%s' en rupture (stock=%d) sans substitut",
                product, stock,
            )

        if not cleaned and strategie_actions:
            # Fallback : si tout est en rupture, garder la première action mais marquer
            logger.warning("[G9] Toutes actions retirées — fallback vers première action originale")
            cleaned = [strategie_actions[0]]

        return cleaned

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _filter_available(self, skus_data: Dict[str, Any]) -> List[str]:
        """
        Retourne la liste triée des SKUs ayant stock > 0.
        Tri : healthy > critical > warning (par stock décroissant dans chaque groupe).
        Tout est dynamique — zéro valeur codée en dur.
        """
        available = []
        for sku, info in skus_data.items():
            qty = int(info.get("stock_qty", 0))
            if qty > RUPTURE_THRESHOLD:
                available.append((sku, qty, info.get("risk_level", "unknown")))

        # Tri : priorité healthy, puis par quantité décroissante
        priority = {"healthy": 0, "warning": 1, "critical": 2, "unknown": 3}
        available.sort(key=lambda x: (priority.get(x[2], 3), -x[1]))
        return [sku for sku, _, _ in available]

    def _is_stale(self, timestamp: str) -> bool:
        if not timestamp:
            return True
        try:
            ts  = datetime.fromisoformat(timestamp)
            age = (datetime.utcnow() - ts).total_seconds()
            return age > self._max_age_s
        except Exception:
            return False

    @staticmethod
    def _build_name_to_stock_map(
        skus_data: Dict[str, Any],
    ) -> Dict[str, int]:
        """
        Construit {product_name_lower: stock_qty} depuis le snapshot.
        Permet de matcher par nom de produit (Stratège utilise les noms).
        """
        mapping = {}
        for sku, info in skus_data.items():
            name  = str(info.get("product_name", "")).strip().lower()
            qty   = int(info.get("stock_qty", 0))
            # Alias SKU aussi
            mapping[sku.lower()] = qty
            if name:
                mapping[name] = qty
        return mapping

    @staticmethod
    def _get_stock_for_product(
        product: str,
        name_to_stock: Dict[str, int],
    ) -> Optional[int]:
        """
        Cherche le stock d'un produit par nom (correspondance partielle).
        Retourne None si produit non trouvé dans inventory.
        """
        if not product:
            return None
        plow = product.strip().lower()
        # Correspondance exacte
        if plow in name_to_stock:
            return name_to_stock[plow]
        # Correspondance partielle (ex: "iPhone 16 Pro" dans "Apple iPhone 16 Pro 256GB")
        for key, qty in name_to_stock.items():
            if plow in key or key in plow:
                return qty
        return None


# ── Singleton ────────────────────────────────────────────────────────────────

_merger: Optional[StateMerger] = None


def get_state_merger() -> StateMerger:
    global _merger
    if _merger is None:
        _merger = StateMerger()
    return _merger
