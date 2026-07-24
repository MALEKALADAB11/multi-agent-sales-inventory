"""
Déclenchement de l'Analyste sur nouvelle vente — deux étages.

POURQUOI DEUX ÉTAGES
────────────────────
Faire tourner un cycle agent complet à chaque encaissement est intenable : une
boutique active enregistre plusieurs ventes par minute, et un cycle complet
mobilise le Stratège et le Coach, donc des appels LLM facturés de plusieurs
secondes. À l'inverse, se contenter d'un cron de 15 minutes laisse le vendeur
devant un chiffre périmé juste après avoir encaissé.

La sortie est de séparer ce qui est bon marché de ce qui est coûteux :

  ÉTAGE 1 — recalcul analytique          ~20 s après la dernière vente
    `ts_engine.analyze_store()` seul : déterministe, moins d'une seconde, aucun
    LLM. Rafraîchit CA, prévision EOD, prévision horaire, gap, urgence, et
    pousse le tout au frontend. C'est le « temps réel » perçu par le vendeur.

  ÉTAGE 2 — cycle agent complet          seulement si la situation a changé
    Stratège + Coach + Guardrail. Déclenché uniquement quand l'étage 1 révèle
    un changement matériel : l'urgence monte d'un cran, le gap bouge de plus de
    `gap_delta` points, la faisabilité se dégrade — ou à défaut, au battement de
    cœur périodique. Un conseil ne se réécrit pas parce qu'un accessoire à
    9 TND vient d'être vendu.

COALESCENCE
───────────
Les ventes sont fusionnées par boutique sur une fenêtre de debounce : dix ventes
en dix secondes déclenchent **un** recalcul, pas dix. La fenêtre redémarre à
chaque vente, avec un plafond dur (`max_wait_s`) pour qu'un flux continu de
ventes ne repousse pas indéfiniment le recalcul.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

_URGENCY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
_FEASIBILITY_RANK = {"ACHIEVED": 0, "ACHIEVABLE": 1, "CHALLENGING": 2,
                     "VERY_HARD": 3, "CLOSED": 3, "UNKNOWN": 1}


@dataclass
class TriggerConfig:
    debounce_s: float = float(os.getenv("SALE_TRIGGER_DEBOUNCE_S", "20"))
    max_wait_s: float = float(os.getenv("SALE_TRIGGER_MAX_WAIT_S", "60"))
    heartbeat_s: float = float(os.getenv("SALE_TRIGGER_HEARTBEAT_S", "900"))
    # Garde-fou de coût : jamais deux cycles complets rapprochés sur une boutique,
    # même si l'urgence oscille autour d'un seuil.
    min_full_cycle_s: float = float(os.getenv("SALE_TRIGGER_MIN_FULL_S", "180"))
    gap_delta_pts: float = float(os.getenv("SALE_TRIGGER_GAP_DELTA", "5"))
    # Période de réveil de la boucle. 0 = déduite du debounce, bornée à [1 s, 5 s]
    # en production ; les tests la forcent bas pour rester déterministes.
    tick_s: float = 0.0


@dataclass
class _StoreState:
    pending_sales: int = 0
    pending_amount: float = 0.0
    first_pending_at: float = 0.0
    last_sale_at: float = 0.0
    last_analysis_at: float = 0.0
    last_full_cycle_at: float = 0.0
    last_urgency: Optional[str] = None
    last_gap_pct: Optional[float] = None
    last_feasibility: Optional[str] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SaleEventTrigger:
    """
    Écoute les ventes et pilote les deux étages.

    `on_analysis(store_id, analysis)` est appelé après chaque recalcul de
    l'étage 1 — c'est le point de branchement du broadcast WebSocket.
    `run_full_cycle(store_id, reason)` déclenche l'étage 2.
    """

    def __init__(
        self,
        on_analysis: Optional[Callable[[str, dict], Awaitable[None]]] = None,
        run_full_cycle: Optional[Callable[[str, str], Awaitable[None]]] = None,
        config: Optional[TriggerConfig] = None,
    ):
        self.cfg = config or TriggerConfig()
        self.on_analysis = on_analysis
        self.run_full_cycle = run_full_cycle
        self._stores: dict[str, _StoreState] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.stats = {"sales_seen": 0, "analyses": 0, "full_cycles": 0, "errors": 0}

    # ── Cycle de vie ─────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "[SALE-TRIGGER] démarré — debounce %.0fs · plafond %.0fs · "
            "battement %.0fs · cycle complet au plus tous les %.0fs",
            self.cfg.debounce_s, self.cfg.max_wait_s,
            self.cfg.heartbeat_s, self.cfg.min_full_cycle_s,
        )

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    # ── Entrée : une vente vient d'être encaissée ────────────────────────────

    def notify_sale(self, store_id: str, amount: float = 0.0) -> None:
        """
        Point d'entrée synchrone, appelable depuis un callback POS.

        Ne fait qu'incrémenter un compteur : aucun await, aucune I/O. Le coût
        d'une vente pour le déclencheur est donc négligeable, ce qui permet de
        le brancher sans risque sur le chemin d'encaissement.
        """
        if not store_id:
            return
        st = self._stores.setdefault(store_id, _StoreState())
        now = time.monotonic()
        if st.pending_sales == 0:
            st.first_pending_at = now
        st.pending_sales += 1
        st.pending_amount += float(amount or 0.0)
        st.last_sale_at = now
        self.stats["sales_seen"] += 1

    def track_store(self, store_id: str) -> None:
        """Suit une boutique même sans vente, pour que le battement s'applique."""
        self._stores.setdefault(store_id, _StoreState())

    # ── Boucle ───────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        tick = self.cfg.tick_s or max(1.0, min(5.0, self.cfg.debounce_s / 4))
        while self._running:
            try:
                await asyncio.sleep(tick)
                now = time.monotonic()
                for store_id, st in list(self._stores.items()):
                    if self._should_analyze(st, now):
                        asyncio.create_task(self._analyze(store_id, st))
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.stats["errors"] += 1
                logger.error("[SALE-TRIGGER] boucle : %s", e)

    def _should_analyze(self, st: _StoreState, now: float) -> bool:
        if st.lock.locked():
            return False
        if st.pending_sales > 0:
            quiet = now - st.last_sale_at >= self.cfg.debounce_s
            capped = now - st.first_pending_at >= self.cfg.max_wait_s
            return quiet or capped
        # Aucune vente : le battement garde le chiffre vivant en heure creuse.
        return now - st.last_analysis_at >= self.cfg.heartbeat_s

    # ── Étage 1 ──────────────────────────────────────────────────────────────

    async def _analyze(self, store_id: str, st: _StoreState) -> None:
        if st.lock.locked():
            return
        async with st.lock:
            nb, amount = st.pending_sales, st.pending_amount
            st.pending_sales, st.pending_amount = 0, 0.0
            st.last_analysis_at = time.monotonic()

            try:
                from app.sales.coaching.agents.analyst.ts_engine import analyze_store
                t0 = time.time()
                analysis = await analyze_store(store_id)
                if analysis.get("error"):
                    raise RuntimeError(analysis["error"])
            except Exception as e:
                self.stats["errors"] += 1
                logger.warning("[SALE-TRIGGER] analyse %s impossible : %s", store_id, e)
                return

            self.stats["analyses"] += 1
            logger.info(
                "[SALE-TRIGGER] %s — %d vente(s) %.0f TND → EOD %.0f | gap %.1f%% | "
                "urgence %s | %.0f ms",
                store_id, nb, amount, analysis["eod_forecast"], analysis["gap_pct"],
                analysis["urgency_level"], (time.time() - t0) * 1000,
            )

            if self.on_analysis:
                try:
                    await self.on_analysis(store_id, analysis)
                except Exception as e:
                    logger.warning("[SALE-TRIGGER] diffusion %s : %s", store_id, e)

            reason = self._full_cycle_reason(st, analysis)
            st.last_urgency = analysis["urgency_level"]
            st.last_gap_pct = float(analysis["gap_pct"])
            st.last_feasibility = analysis.get("feasibility")

            if reason:
                await self._fire_full_cycle(store_id, st, reason)

    # ── Étage 2 ──────────────────────────────────────────────────────────────

    def _full_cycle_reason(self, st: _StoreState, analysis: dict) -> Optional[str]:
        """Retourne la raison de déclencher un cycle complet, `None` sinon."""
        now = time.monotonic()
        since_full = now - st.last_full_cycle_at

        if st.last_full_cycle_at == 0.0:
            return "premier_cycle"
        if since_full < self.cfg.min_full_cycle_s:
            return None          # garde-fou de coût, prioritaire sur tout le reste

        urgency = analysis["urgency_level"]
        if (_URGENCY_RANK.get(urgency, 0)
                > _URGENCY_RANK.get(st.last_urgency or "LOW", 0)):
            return f"urgence {st.last_urgency}→{urgency}"

        feas = analysis.get("feasibility")
        if (_FEASIBILITY_RANK.get(feas, 1)
                > _FEASIBILITY_RANK.get(st.last_feasibility or "ACHIEVABLE", 1)):
            return f"faisabilité {st.last_feasibility}→{feas}"

        gap = float(analysis["gap_pct"])
        if st.last_gap_pct is not None and abs(gap - st.last_gap_pct) >= self.cfg.gap_delta_pts:
            return f"gap {st.last_gap_pct:.1f}%→{gap:.1f}%"

        if since_full >= self.cfg.heartbeat_s:
            return "battement"
        return None

    async def _fire_full_cycle(self, store_id: str, st: _StoreState, reason: str) -> None:
        if not self.run_full_cycle:
            return
        st.last_full_cycle_at = time.monotonic()
        self.stats["full_cycles"] += 1
        logger.info("[SALE-TRIGGER] cycle complet %s — motif : %s", store_id, reason)
        try:
            await self.run_full_cycle(store_id, reason)
        except Exception as e:
            self.stats["errors"] += 1
            logger.error("[SALE-TRIGGER] cycle complet %s : %s", store_id, e)

    # ── Introspection ────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """État exposé au monitoring."""
        return {
            "running": self._running,
            "config": {
                "debounce_s": self.cfg.debounce_s,
                "max_wait_s": self.cfg.max_wait_s,
                "heartbeat_s": self.cfg.heartbeat_s,
                "min_full_cycle_s": self.cfg.min_full_cycle_s,
                "gap_delta_pts": self.cfg.gap_delta_pts,
            },
            "stats": dict(self.stats),
            "stores": {
                sid: {"pending_sales": st.pending_sales,
                      "last_urgency": st.last_urgency,
                      "last_gap_pct": st.last_gap_pct}
                for sid, st in self._stores.items()
            },
        }


_TRIGGER: Optional[SaleEventTrigger] = None


def get_sale_trigger() -> Optional[SaleEventTrigger]:
    return _TRIGGER


def init_sale_trigger(on_analysis=None, run_full_cycle=None,
                      config: Optional[TriggerConfig] = None) -> SaleEventTrigger:
    global _TRIGGER
    if _TRIGGER is None:
        _TRIGGER = SaleEventTrigger(on_analysis, run_full_cycle, config)
    return _TRIGGER
