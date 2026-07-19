"""
seed_objectifs.py
===================
Seeds sales.objectifs <- data/processed/objectifs.csv

Good news: this one's a clean match, I was wrong earlier saying it had no
table — sales.objectifs exists in the baseline with exactly this grain
(store_id x date_objectif). agent_id is nullable in the table and
objectifs.csv is store-level only, so agent_id is left NULL for every row
(matches the table's own design — per-agent targets are a separate,
unpopulated dimension, not a gap in this loader).

Run: python db/seeds/seed_objectifs.py
Re-run safe: existing rows deleted first.
"""
import asyncio
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _seed_common import get_pool, DATA_PROCESSED

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


async def main():
    df = pd.read_csv(DATA_PROCESSED / "objectifs.csv")
    records = [
        (
            r["store_id"], pd.to_datetime(r["date"]).date(),
            float(r["objectif_ca"]), int(r["objectif_transactions"]),
            float(r["objectif_panier_moyen"]),
        )
        for _, r in df.iterrows()
    ]

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM sales.objectifs")
        await conn.copy_records_to_table(
            "objectifs", schema_name="sales",
            columns=["store_id", "date_objectif", "objectif_ca",
                     "objectif_transactions", "objectif_panier_moyen"],
            records=records,
        )
    await pool.close()
    logger.info(f"Done: sales.objectifs — {len(records):,} rows (agent_id left NULL, store-level only)")


if __name__ == "__main__":
    asyncio.run(main())
