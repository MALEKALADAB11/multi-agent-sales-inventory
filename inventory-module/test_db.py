"""
test_db.py
Run from inventory-module/ to verify everything works end to end.

    python test_db.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from db.repositories.inventory_repo import InventoryRepo


async def test():
    repo = InventoryRepo()
    await repo.connect()

    print("\n── Products ─────────────────────────────")
    products = await repo.get_all_products()
    print(f"  Count: {len(products)}")
    if products:
        print(f"  Sample: {products[0]['sku']} — {products[0]['product_name']}")

    print("\n── Stores ───────────────────────────────")
    stores = await repo.get_all_stores()
    print(f"  Count: {len(stores)}")
    if stores:
        print(f"  Sample: {stores[0]['store_id']} — {stores[0]['store_name']}")

    print("\n── Pending alerts ───────────────────────")
    alerts = await repo.get_pending_alerts()
    print(f"  Count: {len(alerts)}")

    print("\n── Pending recommendations ──────────────")
    recs = await repo.get_pending_recommendations()
    print(f"  Count: {len(recs)}")

    print("\n── Active promotions (today) ─────────────")
    promos = await repo.get_active_promotions()
    print(f"  Count: {len(promos)}")

    await repo.close()
    print("\n✓ All OK\n")


if __name__ == "__main__":
    asyncio.run(test())
