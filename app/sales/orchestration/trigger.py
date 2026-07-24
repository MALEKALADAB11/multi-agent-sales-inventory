import asyncio
import logging
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)


class CronTrigger:
    """
    Déclenche un cycle agent toutes les N minutes sur chaque magasin actif.
    Le déclenchement événementiel (alerte critique → cycle immédiat) est
    assuré par AlertCycleTrigger (alert_trigger.py).
    """

    def __init__(
        self,
        orchestrator,
        interval_minutes: int = 15,
        store_id: str = "store-lac2",
        store_ids: Optional[List[str]] = None,
    ):
        self.orchestrator      = orchestrator
        self.interval_seconds  = interval_minutes * 60
        # store_ids prime ; store_id gardé pour compat (appels existants)
        self.store_ids         = list(store_ids) if store_ids else [store_id]
        self.store_id          = self.store_ids[0]
        self.running           = False
        self.task              = None
        self.last_result       = None

    def start(self):
        if not self.running:
            self.running = True
            self.task    = asyncio.create_task(self._loop())
            logger.info(
                "CronTrigger started — cycle every %d min on %d store(s): %s",
                self.interval_seconds // 60,
                len(self.store_ids),
                ", ".join(self.store_ids),
            )

    def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()

    async def _loop(self):
        # Premier cycle immédiatement au démarrage
        await self._run_cycle("startup")

        while self.running:
            await asyncio.sleep(self.interval_seconds)
            if self.running:
                await self._run_cycle("cron")

    async def _run_cycle(self, triggered_by: str):
        for store_id in self.store_ids:
            if not self.running and triggered_by != "startup":
                break
            try:
                logger.info(
                    "CronTrigger firing — trigger=%s store=%s time=%s",
                    triggered_by,
                    store_id,
                    datetime.utcnow().strftime("%H:%M:%S")
                )
                result = await self.orchestrator.run_cycle(
                    store_id     = store_id,
                    triggered_by = triggered_by
                )
                self.last_result = result

            except Exception as e:
                logger.error("CronTrigger error (store=%s): %s", store_id, e)

    async def fire_now(self, triggered_by: str = "manual",
                       store_id: Optional[str] = None) -> dict:
        """
        Déclenche un cycle immédiatement — appelé par l'API.

        `store_id` permet de cibler une boutique précise ; sans lui, la première
        boutique suivie. L'ancienne version ignorait le paramètre et lançait
        toujours la boutique par défaut, quel que soit ce que l'appelant
        demandait.
        """
        result = await self.orchestrator.run_cycle(
            store_id     = store_id or self.store_id,
            triggered_by = triggered_by,
        )
        self.last_result = result
        return result
