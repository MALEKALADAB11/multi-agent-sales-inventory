"""
alert_trigger.py — Déclenchement événementiel des cycles agents.
=================================================================
Ferme le gap "données flux" : jusqu'ici les cycles ne partaient que du
CronTrigger (15 min) ou d'un appel manuel /api/cycle. Ce module souscrit
au bus d'alertes Redis (pattern alerts:store:*:stock) et déclenche un
cycle de coaching immédiat sur le magasin concerné quand une alerte
critique arrive (rupture détectée par le simulateur RT ou par le
DecisionAgent inventory).

Garde-fous anti-tempête :
  - seul le channel :stock est écouté — les alertes :sales et :cross sont
    PRODUITES par les cycles sales eux-mêmes (boucle infinie sinon) ;
  - debounce par magasin (ALERT_CYCLE_DEBOUNCE_S, défaut 180 s) ;
  - seules les priorités URGENT/HIGH déclenchent un cycle.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = float(os.getenv("ALERT_CYCLE_DEBOUNCE_S", "180"))
TRIGGER_PRIORITIES = {"URGENT", "HIGH"}
_STOCK_PATTERN = "alerts:store:*:stock"


class AlertCycleTrigger:
    """
    Souscrit en pattern (psubscribe) aux alertes stock de TOUS les magasins
    et appelle orchestrator.run_cycle(store_id, triggered_by="alert:stock")
    avec debounce par magasin.
    """

    def __init__(self, orchestrator, redis_url: Optional[str] = None):
        self.orchestrator = orchestrator
        if redis_url is None:
            try:
                from app.sales.core.config import get_config
                redis_url = get_config().redis_url
            except Exception:
                redis_url = "redis://localhost:6379"
        self._url = redis_url
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_fire: Dict[str, float] = {}   # store_id → monotonic ts
        self.cycles_fired = 0

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._listen_loop())
            logger.info(
                "[AlertCycleTrigger] démarré — pattern=%s debounce=%.0fs",
                _STOCK_PATTERN, DEBOUNCE_SECONDS,
            )

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    # ── Listener ─────────────────────────────────────────────────────────────

    async def _listen_loop(self) -> None:
        """Boucle d'écoute avec reconnexion automatique (backoff 5s)."""
        while self._running:
            try:
                await self._subscribe_and_listen()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning("[AlertCycleTrigger] Redis KO (%s) — retry 5s", e)
                await asyncio.sleep(5)

    async def _subscribe_and_listen(self) -> None:
        import redis.asyncio as aredis
        client = aredis.from_url(
            self._url, decode_responses=True, socket_connect_timeout=3
        )
        pubsub = client.pubsub()
        try:
            await pubsub.psubscribe(_STOCK_PATTERN)
            logger.info("[AlertCycleTrigger] souscrit à %s", _STOCK_PATTERN)
            async for message in pubsub.listen():
                if not self._running:
                    break
                if message.get("type") != "pmessage":
                    continue
                await self._handle_message(message)
        finally:
            try:
                await pubsub.punsubscribe(_STOCK_PATTERN)
                await client.aclose()
            except Exception:
                pass

    async def _handle_message(self, message: dict) -> None:
        try:
            alert = json.loads(message["data"])
        except (json.JSONDecodeError, TypeError):
            return

        priority = alert.get("priority", "")
        store_id = alert.get("store_id") or self._store_from_channel(
            message.get("channel", "")
        )
        if not store_id or priority not in TRIGGER_PRIORITIES:
            return

        now  = time.monotonic()
        last = self._last_fire.get(store_id, 0.0)
        if now - last < DEBOUNCE_SECONDS:
            logger.debug(
                "[AlertCycleTrigger] alerte %s ignorée (debounce %.0fs restants)",
                store_id, DEBOUNCE_SECONDS - (now - last),
            )
            return

        self._last_fire[store_id] = now
        self.cycles_fired += 1
        logger.info(
            "[AlertCycleTrigger] 🔥 alerte %s priority=%s (%s) → cycle immédiat",
            store_id, priority, alert.get("product_name") or alert.get("sku", "?"),
        )
        # Cycle en tâche de fond — ne bloque pas l'écoute des alertes suivantes
        asyncio.create_task(self._fire(store_id, alert))

    async def _fire(self, store_id: str, alert: dict) -> None:
        try:
            await self.orchestrator.run_cycle(
                store_id=store_id,
                triggered_by=f"alert:{alert.get('type', 'stock')}",
            )
        except Exception as e:
            logger.error("[AlertCycleTrigger] cycle échoué pour %s : %s", store_id, e)

    @staticmethod
    def _store_from_channel(channel: str) -> Optional[str]:
        # channel = "alerts:store:{store_id}:stock"
        parts = channel.split(":")
        return parts[2] if len(parts) >= 4 else None
