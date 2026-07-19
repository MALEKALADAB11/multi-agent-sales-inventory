"""
seed_coaching_scripts.py
===========================
Seeds sales.coaching_scripts <- data/processed/coaching_scripts.csv

Prerequisite: run merge_coaching_scripts.py first to produce that file from
coaching_scripts_ooredoo.csv + coaching_scripts_200.json (see that script's
own docstring for the two source paths).

Dropped (no column in sales.coaching_scripts): script_id (table has its own
serial id), origin, tags, legacy_pg_id.

Note: store_id had a hardcoded DEFAULT 'I63' in the original schema, dropped
by migration 0002 — so every row here needs an explicit store_id, including
the JSON-sourced rows that used the "ALL" convention (national scope, not a
real store — no FK constraint on this column so it inserts fine as literal
text, just don't expect it to join against sales.boutiques).

Run: python db/seeds/seed_coaching_scripts.py
Re-run safe: existing rows deleted first.

Fix (2026-07-09): optional text fields now pass through
_seed_common.clean() so a blank CSV cell (pandas NaN) becomes NULL
instead of tripping asyncpg's text codec ("expected str, got float").
"""
import asyncio
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _seed_common import get_pool, DATA_PROCESSED, clean, clean_trunc

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


async def main():
    path = DATA_PROCESSED / "coaching_scripts.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run merge_coaching_scripts.py first "
            "(needs coaching_scripts_ooredoo.csv + coaching_scripts_200.json)."
        )
    df = pd.read_csv(path)

    records = [
        (
            r["store_id"], clean(r.get("categorie")), clean(r.get("situation")), clean(r.get("action")),
            clean(r.get("produit_cible")), clean(r.get("argument_vente")), clean_trunc(r.get("impact_observe"), 100, field="impact_observe"),
            int(r["heure_min"]) if pd.notna(r.get("heure_min")) else None,
            int(r["heure_max"]) if pd.notna(r.get("heure_max")) else None,
            int(r["jour_semaine"]) if pd.notna(r.get("jour_semaine")) else None,
            clean(r.get("source")),
        )
        for _, r in df.iterrows()
    ]

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM sales.coaching_scripts")
        await conn.copy_records_to_table(
            "coaching_scripts", schema_name="sales",
            columns=["store_id", "categorie", "situation", "action", "produit_cible",
                     "argument_vente", "impact_observe", "heure_min", "heure_max",
                     "jour_semaine", "source"],
            records=records,
        )
    await pool.close()
    logger.info(f"Done: sales.coaching_scripts — {len(records):,} rows")


if __name__ == "__main__":
    asyncio.run(main())
