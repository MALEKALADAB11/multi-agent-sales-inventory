import asyncio
import asyncpg
import random
from datetime import datetime, date, timedelta

PRODUCTS = [
    {"sku": "PHN-IPH-15",  "name": "iPhone 15",         "cat": "Smartphone", "price": 2999.0, "margin": 0.18},
    {"sku": "PHN-SAM-S24", "name": "Samsung Galaxy S24", "cat": "Smartphone", "price": 1999.0, "margin": 0.15},
    {"sku": "ACC-BUD-001", "name": "Wireless Earbuds",   "cat": "Accessory",  "price": 199.0,  "margin": 0.35},
    {"sku": "ACC-WAT-001", "name": "Smartwatch Lite",    "cat": "Accessory",  "price": 449.0,  "margin": 0.22},
    {"sku": "FBR-BOX-001", "name": "Fiber Box Standard", "cat": "Internet",   "price": 99.0,   "margin": 0.42},
    {"sku": "SIM-PREP-001", "name": "Prepaid SIM",       "cat": "SIM",        "price": 9.0,    "margin": 0.80},
    {"sku": "SIM-POST-001", "name": "Postpaid SIM",      "cat": "SIM",        "price": 19.0,   "margin": 0.75},
    {"sku": "RCH-MOB-010",  "name": "Mobile Recharge 10","cat": "Recharge",   "price": 10.0,   "margin": 0.50},
    {"sku": "RCH-MOB-020",  "name": "Mobile Recharge 20","cat": "Recharge",   "price": 20.0,   "margin": 0.50},
]

ADVISORS = [
    {"id": "adv-kb", "ca_target": 2000, "weight": 0.35},
    {"id": "adv-sm", "ca_target": 2000, "weight": 0.28},
    {"id": "adv-at", "ca_target": 2000, "weight": 0.22},
    {"id": "adv-lk", "ca_target": 2000, "weight": 0.15},
]

async def seed():
    conn = await asyncpg.connect(
        "postgresql://asc_user:asc_password@localhost:5433/asc_db"
    )

    today = date.today()
    await conn.execute("""
        DELETE FROM pos_transactions
        WHERE transaction_ts::date = $1
    """, today)
    print("Cleared today's transactions")

    inserted = 0
    now = datetime.now()

    for hour_offset in range(8):
        hour = 9 + hour_offset
        n_transactions = random.randint(4, 12)

        for _ in range(n_transactions):
            adv = random.choices(
                ADVISORS,
                weights=[a["weight"] for a in ADVISORS]
            )[0]

            product = random.choice(PRODUCTS)

            minute = random.randint(0, 59)
            second = random.randint(0, 59)
            ts = datetime(today.year, today.month, today.day, hour, minute, second)

            if ts > now:
                continue

            await conn.execute("""
                INSERT INTO pos_transactions
                  (store_id, advisor_id, sku, product_name,
                   category, amount, units, margin_rate, transaction_ts)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """,
                "store-lac2",
                adv["id"],
                product["sku"],
                product["name"],
                product["cat"],
                product["price"],
                1,
                product["margin"],
                ts
            )
            inserted += 1

    rows = await conn.fetch("""
        SELECT
            a.name,
            a.advisor_code,
            COALESCE(SUM(p.amount), 0) as ca,
            COUNT(p.id) as nb
        FROM advisors a
        LEFT JOIN pos_transactions p
            ON p.advisor_id = a.id
            AND p.transaction_ts::date = $1
        WHERE a.store_id = 'store-lac2'
        GROUP BY a.name, a.advisor_code
        ORDER BY ca DESC
    """, today)

    print(f"\n✅ Inserted {inserted} transactions for today\n")
    print(f"{'Advisor':<20} {'CA':>10} {'Transactions':>15}")
    print("-" * 48)
    for r in rows:
        print(f"{r['name']:<20} {float(r['ca']):>10.0f} DT {r['nb']:>10} tx")

    total = sum(float(r['ca']) for r in rows)
    print("-" * 48)
    print(f"{'TOTAL':<20} {total:>10.0f} DT")
    print(f"\nTarget: 8,000 DT · Attainment: {round(total/8000*100, 1)}%")

    await conn.close()

if __name__ == "__main__":
    asyncio.run(seed())