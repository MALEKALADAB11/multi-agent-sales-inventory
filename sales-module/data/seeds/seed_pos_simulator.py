import asyncio
import json
import random
from datetime import datetime, timedelta
from aiokafka import AIOKafkaProducer

PRODUCTS = [
    {"sku": "IPH16PRO",  "name": "iPhone 16 Pro",    "cat": "Smartphone", "price": 1299, "margin": 0.18},
    {"sku": "SAMA55",    "name": "Samsung A55",       "cat": "Smartphone", "price": 699,  "margin": 0.15},
    {"sku": "AIRPDP3",   "name": "AirPods Pro 3",     "cat": "Accessory",  "price": 279,  "margin": 0.35},
    {"sku": "APLWTCH",   "name": "Apple Watch S10",   "cat": "Accessory",  "price": 449,  "margin": 0.22},
    {"sku": "FIB2GPRO",  "name": "Fiber Box 2G Pro",  "cat": "Internet",   "price": 49,   "margin": 0.42},
    {"sku": "ASRPREM",   "name": "Premium Insurance", "cat": "Service",    "price": 9,    "margin": 0.80},
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