"""
seed_promotions.py
====================
Seeds inventory.promotions <- data/processed/promotions.csv

product_name isn't a column in promotions.csv — looked up from products.csv
by sku instead of left NULL, since inventory.promotions.product_name exists
and a NULL there would break anything downstream that reads promotions
without joining sales.produits itself.

Dropped (no column in inventory.promotions): campaign_name,
affected_categories, min_purchase_qty, channel, is_active.

Run: python db/seeds/seed_promotions.py
Re-run safe: existing rows deleted first.

Fix (2026-07-09): optional text fields (category, promo_type, scope,
looked-up product_name) now pass through _seed_common.clean() so a blank
CSV cell (pandas NaN) becomes NULL instead of crashing asyncpg's text
codec with "expected str, got float".
"""
import asyncio
import logging
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _seed_common import get_pool, DATA_PROCESSED, clean

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def normalize_discount_pct(value):
    """Clamp discount percentages to the NUMERIC(5,2) range used by the DB."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return 0.0
    lower = Decimal("-999.99")
    upper = Decimal("999.99")
    if amount < lower:
        return float(lower)
    if amount > upper:
        return float(upper)
    return float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


async def main():
    promos = pd.read_csv(DATA_PROCESSED / "promotions.csv")
    products = pd.read_csv(DATA_PROCESSED / "products.csv", usecols=["sku", "product_name"])
    name_lookup = products.set_index("sku")["product_name"].to_dict()

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM inventory.promotions")
        for _, r in promos.iterrows():
            sku = int(r["sku"]) if pd.notna(r["sku"]) else None
            await conn.execute(
                """
                INSERT INTO inventory.promotions
                    (promo_id, promo_name, start_date, end_date, sku,
                     product_name, category, discount_pct, promo_type, scope)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                """,
                r["promo_id"], r["promo_name"],
                pd.to_datetime(r["start_date"]).date(), pd.to_datetime(r["end_date"]).date(),
                sku, clean(name_lookup.get(sku)), clean(r.get("category")),
                normalize_discount_pct(r.get("discount_pct")),
                clean(r.get("promo_type")), clean(r.get("scope")) or "national",
            )
    await pool.close()
    logger.info(f"Done: inventory.promotions — {len(promos)} rows")


if __name__ == "__main__":
    asyncio.run(main())
