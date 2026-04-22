
import asyncio
import json
import logging
from aiokafka import AIOKafkaConsumer
from core.config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()


class POSConsumer:
    """Consomme les transactions POS depuis Kafka et les écrit en PostgreSQL."""

    def __init__(self, postgres_repo):
        self.repo     = postgres_repo
        self.consumer = None
        self.running  = False

    async def start(self):
        self.consumer = AIOKafkaConsumer(
            settings.topic_pos,
            bootstrap_servers = settings.kafka_brokers,
            group_id          = settings.kafka_group_id,
            value_deserializer= lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset = "earliest"
        )
        await self.consumer.start()
        self.running = True
        logger.info("✅ POSConsumer started — listening on %s", settings.topic_pos)
        await self._consume()

    async def _consume(self):
        async for msg in self.consumer:
            if not self.running:
                break
            try:
                await self._process(msg.value)
            except Exception as e:
                logger.error("POSConsumer error: %s", e)

    async def _process(self, payload: dict):
        """Normalise et persiste une transaction POS."""
        required = {"store_id", "advisor_id", "sku", "amount", "transaction_ts"}
        if not required.issubset(payload):
            logger.warning("POS payload missing fields: %s", payload)
            return

        await self.repo.insert_pos_transaction(
            store_id       = payload["store_id"],
            advisor_id     = payload["advisor_id"],
            sku            = payload["sku"],
            product_name   = payload.get("product_name", ""),
            category       = payload.get("category", ""),
            amount         = float(payload["amount"]),
            units          = int(payload.get("units", 1)),
            margin_rate    = payload.get("margin_rate"),
            transaction_ts = payload["transaction_ts"]
        )

    async def stop(self):
        self.running = False
        if self.consumer:
            await self.consumer.stop()


class FeedbackConsumer:
    """Consomme les retours manager (approve/reject) depuis Kafka."""

    def __init__(self, coaching_repo):
        self.repo     = coaching_repo
        self.consumer = None

    async def start(self):
        self.consumer = AIOKafkaConsumer(
            settings.topic_feedback,
            bootstrap_servers = settings.kafka_brokers,
            group_id          = settings.kafka_group_id,
            value_deserializer= lambda v: json.loads(v.decode("utf-8")),
        )
        await self.consumer.start()
        await self._consume()

    async def _consume(self):
        async for msg in self.consumer:
            try:
                payload = msg.value
                await self.repo.update_card_status(
                    card_id = payload["card_id"],
                    status  = payload["action"],     # approve | reject
                    manager = payload.get("manager_id")
                )
            except Exception as e:
                logger.error("FeedbackConsumer error: %s", e)