import asyncio
import json
import random
from datetime import datetime, timedelta
from aiokafka import AIOKafkaProducer

PRODUCTS = [
    {"sku": "PHN-IPH-15",  "name": "iPhone 15",         "cat": "Smartphone", "price": 2999, "margin": 0.18},
    {"sku": "PHN-SAM-S24", "name": "Samsung Galaxy S24", "cat": "Smartphone", "price": 1999, "margin": 0.15},
    {"sku": "ACC-BUD-001", "name": "Wireless Earbuds",   "cat": "Accessory",  "price": 199,  "margin": 0.35},
    {"sku": "ACC-WAT-001", "name": "Smartwatch Lite",    "cat": "Accessory",  "price": 449,  "margin": 0.22},
    {"sku": "FBR-BOX-001", "name": "Fiber Box Standard", "cat": "Internet",   "price": 99,   "margin": 0.42},
    {"sku": "SIM-PREP-001", "name": "Prepaid SIM",       "cat": "SIM",        "price": 9,    "margin": 0.80},
    {"sku": "SIM-POST-001", "name": "Postpaid SIM",      "cat": "SIM",        "price": 19,   "margin": 0.75},
    {"sku": "RCH-MOB-010",  "name": "Mobile Recharge 10","cat": "Recharge",   "price": 10,   "margin": 0.50},
    {"sku": "RCH-MOB-020",  "name": "Mobile Recharge 20","cat": "Recharge",   "price": 20,   "margin": 0.50},
]

ADVISORS = ["adv-kb", "adv-sm", "adv-at", "adv-lk"]

async def simulate(n_transactions: int = 50):
    producer = AIOKafkaProducer(
        bootstrap_servers="localhost:9092",
        value_serializer=lambda v: json.dumps(v).encode("utf-8")
    )
    await producer.start()

    now = datetime.utcnow()
    for i in range(n_transactions):
        product = random.choice(PRODUCTS)
        await producer.send("pos.transactions", {
            "store_id":       "store-lac2",
            "advisor_id":     random.choice(ADVISORS),
            "sku":            product["sku"],
            "product_name":   product["name"],
            "category":       product["cat"],
            "amount":         product["price"],
            "units":          1,
            "margin_rate":    product["margin"],
            "transaction_ts": (now - timedelta(minutes=random.randint(0, 480))).isoformat()
        })
        await asyncio.sleep(0.05)

    await producer.stop()
    print(f"✅ Simulated {n_transactions} POS transactions sent to Kafka")

if __name__ == "__main__":
    asyncio.run(simulate(50))