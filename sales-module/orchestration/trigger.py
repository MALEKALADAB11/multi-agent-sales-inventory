import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class CronTrigger:
    """
    Déclenche un cycle agent toutes les N minutes.
    Aussi déclenché si un événement critique arrive
    (stock alert, grosse transaction...).
    """

    def __init__(
        self,
        orchestrator,
        interval_minutes: int = 15,
        store_id: str = "store-lac2"
    ):
        self.orchestrator      = orchestrator
        self.interval_seconds  = interval_minutes * 60
        self.store_id          = store_id
        self.running           = False
        self.task              = None
        self.last_result       = None

    def start(self):
        if not self.running:
            self.running = True
            self.task    = asyncio.create_task(self._loop())
            logger.info(
                "CronTrigger started — cycle every %d min",
                self.interval_seconds // 60
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
        try:
            logger.info(
                "CronTrigger firing — trigger=%s time=%s",
                triggered_by,
                datetime.utcnow().strftime("%H:%M:%S")
            )
            result = await self.orchestrator.run_cycle(
                store_id     = self.store_id,
                triggered_by = triggered_by
            )
            self.last_result = result

        except Exception as e:
            logger.error("CronTrigger error: %s", e)

    async def fire_now(self, triggered_by: str = "manual") -> dict:
        """Déclenche un cycle immédiatement — appelé par l'API."""
        result = await self.orchestrator.run_cycle(
            store_id     = self.store_id,
            triggered_by = triggered_by
        )
        self.last_result = result
        return result