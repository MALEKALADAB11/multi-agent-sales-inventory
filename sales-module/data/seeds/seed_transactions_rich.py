import asyncio
import asyncpg
import random
from datetime import datetime, date, timedelta


PRODUCTS = [
    {"sku": "IPH16PRO",  "name": "iPhone 16 Pro",    "cat": "Smartphone", "price": 1299.0, "margin": 0.18},
    {"sku": "SAMA55",    "name": "Samsung A55",       "cat": "Smartphone", "price": 699.0,  "margin": 0.15},
    {"sku": "AIRPDP3",   "name": "AirPods Pro 3",     "cat": "Accessory",  "price": 279.0,  "margin": 0.35},
    {"sku": "APLWTCH",   "name": "Apple Watch S10",   "cat": "Accessory",  "price": 449.0,  "margin": 0.22},
    {"sku": "FIB2GPRO",  "name": "Fiber Box 2G Pro",  "cat": "Internet",   "price": 49.0,   "margin": 0.42},
    {"sku": "ASRPREM",   "name": "Premium Insurance", "cat": "Service",    "price": 9.0,    "margin": 0.80},
]

ADVISORS = [
    {"id": "adv-kb", "ca_target": 2000, "weight": 0.35},
    {"id": "adv-sm", "ca_target": 2000, "weight": 0.28},
    {"id": "adv-at", "ca_target": 2000, "weight": 0.22},
    {"id": "adv-lk", "ca_target": 2000, "weight": 0.15},
]


async def seed():
    conn = await asyncpg.connect(
        "postgresql://asc_user:asc_password@localhost:5432/asc_db"
    )

    # Supprimer les transactions du jour pour repartir propre
    today = date.today()
    await conn.execute("""
        DELETE FROM pos_transactions
        WHERE transaction_ts::date = $1
    """, today)
    print("Cleared today's transactions")

    inserted = 0
    now = datetime.now()

    # Générer des transactions sur les 8 dernières heures
    for hour_offset in range(8):
        hour = 9 + hour_offset  # 9h → 16h
        n_transactions = random.randint(4, 12)

        for _ in range(n_transactions):
            # Choisir advisor selon son poids
            adv = random.choices(
                ADVISORS,
                weights=[a["weight"] for a in ADVISORS]
            )[0]

            # Choisir produit
            product = random.choice(PRODUCTS)

            # Timestamp aléatoire dans l'heure
            minute  = random.randint(0, 59)
            second  = random.randint(0, 59)
            ts      = datetime(today.year, today.month, today.day,
                               hour, minute, second)

            # Ne pas insérer dans le futur
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

    # Résumé par advisor
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