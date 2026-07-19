"""
test_seeds.py
=============
Quick verification that all seeded tables have data after running seed scripts.
Runs SELECT COUNT(*) on every table touched by db/seeds/*.py scripts.

Run: python db/seeds/test_seeds.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _seed_common import get_pool


# All tables touched by seed scripts (from run_all_seeds.py _CLEAR_ORDER)
# plus tables populated by generate_multistore_data.py
SEEDED_TABLES = [
    "supply.supplier_products",
    "inventory.promotions",
    "inventory.events",
    "inventory.sales_history",
    "inventory.stock_history",
    "inventory.stock_levels",           # added by generate_multistore_data.py
    "sales.transactions",               # added by generate_multistore_data.py
    "agent_kpi_daily",                  # added by generate_multistore_data.py
    "sales.agents",
    "market.competitor_pricing",
    "sales.coaching_scripts",
    "sales.objectifs",
    "inventory.business_objectives",
    "market.mnp_flows",
    "inventory.product_master",
    "sales.produits",
    "sales.boutiques",
    "market.competitors",
    "supply.suppliers",
]


async def main():
    pool = await get_pool()
    async with pool.acquire() as conn:
        print("── Seed Verification " + "─" * 40)
        print(f"{'Table':<40} {'Rows':>10}")
        print("-" * 52)
        
        total_rows = 0
        empty_tables = []
        
        for table in SEEDED_TABLES:
            try:
                result = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                print(f"{table:<40} {result:>10,}")
                total_rows += result
                if result == 0:
                    empty_tables.append(table)
            except Exception as e:
                print(f"{table:<40} ERROR: {e}")
                empty_tables.append(table)
        
        print("-" * 52)
        print(f"{'TOTAL':<40} {total_rows:>10,}")
        
        if empty_tables:
            print(f"\n⚠️  {len(empty_tables)} table(s) are empty or errored:")
            for table in empty_tables:
                print(f"   - {table}")
            return 1
        else:
            print("\n✅ All seeded tables have data.")
            return 0
    
    await pool.close()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
