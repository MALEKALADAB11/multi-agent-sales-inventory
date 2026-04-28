import asyncio
import random
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Products mapped to actual inventory CSV SKUs ──────────────────────────────
# Weights reflect realistic sales frequency for a Tunisian telecom store.
# Recharges & SIMs sell most; tablets & 5G routers sell least.
PRODUCTS = [
    # Phones — high value, low frequency
    {"sku": "PHN-IPH-15",   "name": "iPhone 15",            "category": "Smartphone",  "amount": 2999.0, "weight": 0.005},
    {"sku": "PHN-SAM-S24",  "name": "Samsung Galaxy S24",   "category": "Smartphone",  "amount": 1999.0, "weight": 0.008},
    {"sku": "PHN-XIA-13",   "name": "Xiaomi 13",            "category": "Smartphone",  "amount": 1299.0, "weight": 0.010},
    {"sku": "PHN-OPP-F5",   "name": "Oppo F5",              "category": "Smartphone",  "amount":  799.0, "weight": 0.010},
    {"sku": "PHN-BUD-001",  "name": "Budget Smartphone",    "category": "Smartphone",  "amount":  399.0, "weight": 0.015},

    # Tablets — high value, very low frequency
    {"sku": "TAB-IPD-AIR",  "name": "iPad Air",             "category": "Tablet",      "amount": 1999.0, "weight": 0.002},
    {"sku": "TAB-SAM-S9",   "name": "Samsung Tab S9",       "category": "Tablet",      "amount": 1299.0, "weight": 0.003},
    {"sku": "TAB-LEN-P11",  "name": "Lenovo Tab P11",       "category": "Tablet",      "amount":  899.0, "weight": 0.004},

    # Accessories — medium value, medium frequency
    {"sku": "ACC-WAT-002",  "name": "Smartwatch Pro",       "category": "Accessory",   "amount":  599.0, "weight": 0.010},
    {"sku": "ACC-WAT-001",  "name": "Smartwatch Lite",      "category": "Accessory",   "amount":  449.0, "weight": 0.015},
    {"sku": "ACC-BUD-002",  "name": "Wireless Earbuds Pro", "category": "Accessory",   "amount":  299.0, "weight": 0.015},
    {"sku": "ACC-BUD-001",  "name": "Wireless Earbuds",     "category": "Accessory",   "amount":  199.0, "weight": 0.020},
    {"sku": "ACC-CAM-001",  "name": "Action Camera",        "category": "Accessory",   "amount":  149.0, "weight": 0.010},
    {"sku": "ACC-CHG-001",  "name": "Fast Charger",         "category": "Accessory",   "amount":   79.0, "weight": 0.070},
    {"sku": "ACC-CAS-001",  "name": "Phone Case",           "category": "Accessory",   "amount":   49.0, "weight": 0.080},

    # Routers — medium-high value, low frequency
    {"sku": "RTR-5G-002",   "name": "5G Router Pro",        "category": "Router",      "amount":  799.0, "weight": 0.004},
    {"sku": "RTR-5G-001",   "name": "5G Router",            "category": "Router",      "amount":  599.0, "weight": 0.005},
    {"sku": "RTR-4G-002",   "name": "4G Router Plus",       "category": "Router",      "amount":  399.0, "weight": 0.006},
    {"sku": "RTR-4G-001",   "name": "4G Router",            "category": "Router",      "amount":  299.0, "weight": 0.008},

    # Fiber boxes — medium value, medium frequency
    {"sku": "FBR-BOX-003",  "name": "Fiber Box Premium",    "category": "Internet",    "amount":  199.0, "weight": 0.020},
    {"sku": "FBR-BOX-002",  "name": "Fiber Box Plus",       "category": "Internet",    "amount":  149.0, "weight": 0.025},
    {"sku": "FBR-BOX-001",  "name": "Fiber Box Standard",   "category": "Internet",    "amount":   99.0, "weight": 0.030},

    # SIM cards — low value, high frequency
    {"sku": "SIM-ESIM-001", "name": "eSIM Activation",      "category": "SIM",         "amount":   29.0, "weight": 0.060},
    {"sku": "SIM-HOLI-001", "name": "Holiday SIM",          "category": "SIM",         "amount":   49.0, "weight": 0.030},
    {"sku": "SIM-POST-001", "name": "Postpaid SIM",         "category": "SIM",         "amount":   19.0, "weight": 0.040},
    {"sku": "SIM-PREP-001", "name": "Prepaid SIM",          "category": "SIM",         "amount":    9.0, "weight": 0.150},

    # Recharges — very low value, very high frequency
    {"sku": "RCH-INT-050",  "name": "Internet Recharge 50", "category": "Recharge",    "amount":   50.0, "weight": 0.050},
    {"sku": "RCH-STR-001",  "name": "Store Recharge",       "category": "Recharge",    "amount":   30.0, "weight": 0.040},
    {"sku": "RCH-MOB-020",  "name": "Mobile Recharge 20",   "category": "Recharge",    "amount":   20.0, "weight": 0.100},
    {"sku": "RCH-MOB-010",  "name": "Mobile Recharge 10",   "category": "Recharge",    "amount":   10.0, "weight": 0.120},
]

ADVISORS = [
    {"id": "adv-kb", "weight": 0.35},
    {"id": "adv-sm", "weight": 0.28},
    {"id": "adv-at", "weight": 0.22},
    {"id": "adv-lk", "weight": 0.15},
]


class RealtimeSimulator:
    """
    Generates one transaction every N seconds and injects it into JsonDataService.
    Also notifies the inventory module via on_sale callback so stock levels
    are depleted in real time.
    """

    def __init__(self, json_svc, interval_seconds: int = 15):
        self.json_svc = json_svc
        self.interval = interval_seconds
        self.running  = False
        self.task     = None
        self.total_injected = 0
        # Set from main.py — called on every sale.
        # Signature: on_sale(store_id: str, sku: str, units: int) -> None
        self.on_sale = None

    def start(self):
        if not self.running:
            self.running = True
            self.task    = asyncio.create_task(self._loop())
            logger.info(
                "RealtimeSimulator started — new transaction every %ds",
                self.interval,
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
        adv = random.choices(
            ADVISORS,
            weights=[a["weight"] for a in ADVISORS],
        )[0]
        product = random.choices(
            PRODUCTS,
            weights=[p["weight"] for p in PRODUCTS],
        )[0]

        tx = {
            "advisor_id":   adv["id"],
            "sku":          product["sku"],
            "product_name": product["name"],
            "category":     product["category"],
            "amount":       product["amount"],
            "units":        1,
            "hour":         datetime.now().hour,
        }

        self.json_svc.add_transaction(tx)
        self.total_injected += 1

        logger.info(
            "TX injected — advisor=%s product=%s amount=%.0f DT (total=%d)",
            adv["id"], product["sku"], product["amount"],
            self.total_injected,
        )

        # Notify inventory module — main.py wires this to InventoryDataCache
        if self.on_sale:
            try:
                store_id = self.json_svc.get_store().get("id", "STORE-001")
                self.on_sale(store_id, product["sku"], tx["units"])
            except Exception as e:
                logger.warning("on_sale callback error: %s", e)