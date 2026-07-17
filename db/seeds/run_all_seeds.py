"""
run_all_seeds.py
==================
Runs every db/seeds/*.py script in FK-safe order:

  1. seed_market_reference.py  (suppliers, competitors, market.events — no deps)
  2. seed_reference_data.py    (produits/product_master/boutiques/agents/
                                 supplier_products — supplier_products FKs suppliers)
  3. seed_promotions.py        (FKs produits.sku)
  4. seed_history.py           (FKs produits.sku + boutiques.store_id)
  5. seed_objectifs.py         (FKs boutiques.store_id — no strict FK in schema
                                 but logically depends on stores existing)
  6. seed_coaching_scripts.py  (independent — needs data/processed/coaching_scripts.csv
                                 already produced by merge_coaching_scripts.py)
  7. seed_static_data.py       (independent, hardcoded constants)

Run: python db/seeds/run_all_seeds.py

Fix (2026-07-09): re-running the full suite against a DB that already has
data crashed with ForeignKeyViolationError — e.g. seed_boutiques's own
`DELETE FROM sales.boutiques` failing because sales.agents (from the
PREVIOUS run) still had rows pointing at those stores via
agents_store_id_fkey. Every seed_*.py script deletes its own table right
before reinserting, which only works on an empty database — it doesn't
account for child rows left over in *other* tables by earlier runs.
sales.produits has the same exposure (children: supply.supplier_products,
inventory.promotions, inventory.events, inventory.sales_history,
inventory.stock_history) and so does market.competitors (child:
market.competitor_pricing) — this run just hadn't reached them yet.

Rather than hand-order deletes inside five separate files (fragile — the
next new FK breaks it again), this now does ONE upfront pass here, in
strict child-before-parent order across every table any seed script
touches, before a single INSERT happens anywhere. Each seed_*.py script's
own `DELETE FROM <its table>` then runs against an already-empty table —
harmless, order no longer matters at that point.

Deliberately NOT using TRUNCATE ... CASCADE: CASCADE would also wipe
tables these scripts don't own and don't reseed (sales.transactions_rt,
sales.transactions, coaching.coaching_events, customer.nps_csat,
inventory.stock_levels, supply.transfers, inventory.demand_forecast) —
real/live data outside this project's scope, not something a reseed
should ever touch. The list below is deliberately explicit instead.
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _seed_common import get_pool

import seed_market_reference
import seed_reference_data
import seed_promotions
import seed_history
import seed_objectifs
import seed_coaching_scripts
import seed_static_data

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# Every table any seed_*.py script deletes-then-inserts, in strict
# child-before-parent order. Children (tables with an FK pointing at
# another table in this list) always come before the table they
# reference. Independent/standalone tables can go anywhere.
_CLEAR_ORDER = [
    # children first
    "supply.supplier_products",     # FKs sales.produits, supply.suppliers
    "inventory.promotions",         # FKs sales.produits
    "inventory.events",             # FKs sales.produits, sales.boutiques
    "inventory.sales_history",      # FKs sales.produits, sales.boutiques
    "inventory.stock_history",      # FKs sales.produits, sales.boutiques
    "inventory.stock_levels",       # FKs sales.produits (added to allow produits delete)
    "sales.transactions",           # FKs sales.agents (added to allow agents delete)
    "agent_kpi_daily",              # FKs sales.agents (added by generate_multistore_data.py)
    "sales.agents",                 # FKs sales.boutiques
    "market.competitor_pricing",    # FKs market.competitors
    "sales.coaching_scripts",       # no FK, order-independent
    "sales.objectifs",              # no FK, order-independent
    "inventory.business_objectives",  # no FK, order-independent
    "market.mnp_flows",             # no FK, order-independent
    "inventory.product_master",     # no FK, order-independent
    # parents last
    "sales.produits",
    "sales.boutiques",
    "market.competitors",
    "supply.suppliers",
]


async def clear_seeded_tables():
    pool = await get_pool()
    async with pool.acquire() as conn:
        for table in _CLEAR_ORDER:
            await conn.execute(f"DELETE FROM {table}")
    await pool.close()
    logger.info(f"  cleared {len(_CLEAR_ORDER)} tables (child-before-parent order)")


async def main():
    logger.info("── pre-clear (child-before-parent) " + "─" * 15)
    await clear_seeded_tables()

    steps = [
        ("market reference",  seed_market_reference.main),
        ("reference data",    seed_reference_data.main),
        ("promotions",        seed_promotions.main),
        ("sales/stock history", seed_history.main),
        ("objectifs",          seed_objectifs.main),
        ("coaching scripts",   seed_coaching_scripts.main),
        ("static data",        seed_static_data.main),
    ]
    for label, fn in steps:
        logger.info(f"── {label} " + "─" * (50 - len(label)))
        try:
            await fn()
        except FileNotFoundError as e:
            logger.warning(f"  ⚠ skipped ({label}): {e}")

    logger.info("All seed steps attempted.")


if __name__ == "__main__":
    asyncio.run(main())
