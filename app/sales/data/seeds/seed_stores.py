import asyncio
import asyncpg
from datetime import date


STORES = [
    {
        "id":         "store-lac2",
        "store_code": "lac2",
        "name":       "Lac 2",
        "city":       "Tunis",
        "capacity":   20,
        "active":     True
    }
]

ADVISORS = [
    {
        "id":           "adv-kb",
        "store_id":     "store-lac2",
        "advisor_code": "kb",
        "name":         "Karim Benali",
        "role":         "Smartphones 5G",
        "avatar_color": "#6C5CE7",
        "coach_score":  0.91,
        "active":       True
    },
    {
        "id":           "adv-sm",
        "store_id":     "store-lac2",
        "advisor_code": "sm",
        "name":         "Sara Moulai",
        "role":         "Fiber · Pro Offers",
        "avatar_color": "#00B894",
        "coach_score":  0.73,
        "active":       True
    },
    {
        "id":           "adv-at",
        "store_id":     "store-lac2",
        "advisor_code": "at",
        "name":         "Amine Tazi",
        "role":         "Accessories",
        "avatar_color": "#F9A825",
        "coach_score":  0.44,
        "active":       True
    },
    {
        "id":           "adv-lk",
        "store_id":     "store-lac2",
        "advisor_code": "lk",
        "name":         "Leila Khadri",
        "role":         "Retention · CRM",
        "avatar_color": "#2D9CDB",
        "coach_score":  0.28,
        "active":       True
    }
]

TARGETS = [
    {"advisor_id": "adv-kb", "ca_target": 2000.0, "units_target": 18},
    {"advisor_id": "adv-sm", "ca_target": 2000.0, "units_target": 8},
    {"advisor_id": "adv-at", "ca_target": 2000.0, "units_target": 9},
    {"advisor_id": "adv-lk", "ca_target": 2000.0, "units_target": 6},
]


async def seed():
    conn = await asyncpg.connect(
        "postgresql://asc_user:asc_password@localhost:5432/asc_db"
    )

    for store in STORES:
        await conn.execute("""
            INSERT INTO stores (id, store_code, name, city, capacity, active)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (store_code) DO NOTHING
        """, store["id"], store["store_code"], store["name"],
             store["city"], store["capacity"], store["active"])

    for adv in ADVISORS:
        await conn.execute("""
            INSERT INTO advisors
              (id, store_id, advisor_code, name, role, avatar_color, coach_score, active)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (advisor_code) DO NOTHING
        """, adv["id"], adv["store_id"], adv["advisor_code"],
             adv["name"], adv["role"], adv["avatar_color"],
             adv["coach_score"], adv["active"])

    today = date.today()
    for t in TARGETS:
        await conn.execute("""
            INSERT INTO daily_targets
              (store_id, advisor_id, target_date, ca_target, units_target)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (advisor_id, target_date) DO NOTHING
        """, "store-lac2", t["advisor_id"], today,
             t["ca_target"], t["units_target"])

    await conn.close()
    print("✅ Seed complete — stores, advisors, targets inserted")


if __name__ == "__main__":
    asyncio.run(seed())
