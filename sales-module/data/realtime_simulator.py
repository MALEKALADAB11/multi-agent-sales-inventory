import asyncio
import random
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

PRODUCTS = [
    {"sku": "IPH16PRO",  "name": "iPhone 16 Pro",    "category": "Smartphone", "amount": 1299.0, "weight": 0.03},
    {"sku": "SAMA55",    "name": "Samsung A55",       "category": "Smartphone", "amount": 499.0,  "weight": 0.08},
    {"sku": "AIRPDP3",   "name": "AirPods Pro 3",     "category": "Accessory",  "amount": 279.0,  "weight": 0.08},
    {"sku": "APLWTCH",   "name": "Apple Watch S10",   "category": "Accessory",  "amount": 449.0,  "weight": 0.05},
    {"sku": "FIB2GPRO",  "name": "Fiber Box 2G Pro",  "category": "Internet",   "amount": 49.0,   "weight": 0.40},
    {"sku": "ASRPREM",   "name": "Premium Insurance", "category": "Service",    "amount": 9.0,    "weight": 0.36},
]

ADVISORS = [
    {"id": "adv-kb", "weight": 0.35},
    {"id": "adv-sm", "weight": 0.28},
    {"id": "adv-at", "weight": 0.22},
    {"id": "adv-lk", "weight": 0.15},
]


class RealtimeSimulator:
    """
    Génère des transactions toutes les N secondes
    et les injecte dans JsonDataService.
    """

    def __init__(self, json_svc, interval_seconds: int = 15):
        self.json_svc = json_svc
        self.interval = interval_seconds
        self.running  = False
        self.task     = None
        self.total_injected = 0

    def start(self):
        if not self.running:
            self.running = True
            self.task    = asyncio.create_task(self._loop())
            logger.info(
                "RealtimeSimulator started — new transaction every %ds",
                self.interval
            )

    def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
        logger.info("RealtimeSimulator stopped")

    async def _loop(self):
        while self.running:
            try:
                await asyncio.sleep(self.interval)
                self._inject_transaction()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Simulator error: %s", e)

    def _inject_transaction(self):
        # Choisir advisor et produit aléatoirement
        adv     = random.choices(
            ADVISORS,
            weights=[a["weight"] for a in ADVISORS]
        )[0]
        product = random.choices(
            PRODUCTS,
            weights=[p["weight"] for p in PRODUCTS]
        )[0]

        # Créer la transaction
        tx = {
            "advisor_id":   adv["id"],
            "sku":          product["sku"],
            "product_name": product["name"],
            "category":     product["category"],
            "amount":       product["amount"],
            "units":        1,
            "hour":         datetime.now().hour
        }

        # Injecter dans le service JSON
        self.json_svc.add_transaction(tx)
        self.total_injected += 1

        logger.info(
            "TX injected — advisor=%s product=%s amount=%.0f DT (total=%d)",
            adv["id"], product["sku"], product["amount"],
            self.total_injected
        )