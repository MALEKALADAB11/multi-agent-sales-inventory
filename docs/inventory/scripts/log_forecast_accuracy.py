import sys, asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.inventory.tools.internal.stock_tools import _query
from app.inventory.repositories.inventory_repo import InventoryRepo


async def main():
    # Read: forecasts made for yesterday, joined against yesterday's actual sales.
    # Sync/read-only via stock_tools._query, same split as the rest of this guide (1.6/1.7).
    rows = _query("""
        SELECT f.sku, f.store_id, f.forecast_date,
               f.baseline_demand, f.corrected_demand,
               COALESCE(s.quantity_sold, 0) AS actual_demand
        FROM inventory.demand_forecast f
        LEFT JOIN inventory.sales_history s
          ON s.sku = f.sku AND s.store_id = f.store_id AND s.record_date = f.forecast_date
        WHERE f.forecast_date = CURRENT_DATE - INTERVAL '1 day'
          AND (f.baseline_demand IS NOT NULL OR f.corrected_demand IS NOT NULL)
    """)

    if not rows:
        print("No forecasts to score for yesterday")
        return

    repo = InventoryRepo()
    await repo.connect()

    async with repo.pool.acquire() as conn:
        for row in rows:
            baseline = row.get("baseline_demand")
            corrected = row.get("corrected_demand")
            actual = row.get("actual_demand") or 0.0

            baseline_error = abs(float(baseline) - float(actual)) if baseline is not None else None
            corrected_error = abs(float(corrected) - float(actual)) if corrected is not None else None

            await conn.execute("""
                INSERT INTO inventory.forecast_accuracy
                    (sku, store_id, forecast_date, baseline_demand, corrected_demand,
                     actual_demand, baseline_error, corrected_error)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            """,
                row["sku"], row["store_id"], row["forecast_date"],
                baseline, corrected, actual, baseline_error, corrected_error,
            )

    await repo.close()
    print(f"Logged accuracy for {len(rows)} forecast rows")


if __name__ == "__main__":
    asyncio.run(main())
