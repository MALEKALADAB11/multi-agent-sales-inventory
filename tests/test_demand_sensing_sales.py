"""
test_demand_sensing_sales.py
=============================
Read-only integration tests confirming the demand-sensing forecast
(inventory.demand_forecast: baseline_demand / corrected_demand) actually
reaches the SALES coaching module — not just the inventory agents, which
test_demand_sensing.py already covers.

Companion to test_demand_sensing.py. Same philosophy:
  - Never runs the pipeline (backfill / run_baseline_batch / run_sensing_job).
  - Only reads whatever is already in the DB right now.
  - A skip means "no coverage yet for this pair", not "the code is broken".

Specifically this proves the fix described in the coaching integration work:
supply.reorder_params.demande_moy_jour (dead table, no active population
script) was replaced by inventory.demand_forecast
(COALESCE(corrected_demand, baseline_demand, demand_24h)) in:
  - coach_chat._load_inventory_context_sync()      — two raw SQL queries
  - cross_domain_tools.get_demand_forecast_batch()  — new tool
  - cross_domain_tools.get_recommendable_products()  — days_to_stockout

Run with:
    pytest test_demand_sensing_sales.py -v -m integration
"""
import sys
from datetime import date
from pathlib import Path

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    pass

import pytest

# Same Windows DLL load-order fix used in test_demand_sensing.py: torch /
# coreforecast must load before pandas/numpy in any process that will
# eventually import timeseries_engine.py — which fetch_node's module
# (app.inventory.agents.analysis.nodes) does at import time.
try:
    import torch  # noqa: F401
    import coreforecast.exponentially_weighted  # noqa: F401
except ImportError:
    pass  # not on Windows / not installed — the DLL issue doesn't apply

import pandas as pd

try:
    from app.sales.coaching.agents.coach import coach_chat
    from app.sales.coaching.agents.coach.cross_domain_tools import (
        get_demand_forecast_batch, get_recommendable_products,
    )
    from app.inventory.agents.analysis.nodes import fetch_node
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # -> back/
    from app.sales.coaching.agents.coach import coach_chat
    from app.sales.coaching.agents.coach.cross_domain_tools import (
        get_demand_forecast_batch, get_recommendable_products,
    )
    from app.inventory.agents.analysis.nodes import fetch_node

pytestmark = pytest.mark.integration


def _conn():
    """Raw connection using the same DB_CFG coach_chat.py itself uses —
    so verification queries always point at the exact DB being tested,
    even if POSTGRES_HOST/DB/USER/PASSWORD env vars differ from the
    localhost/ooredoo_sales/postgres/root defaults hardcoded in
    cross_domain_tools.py's asyncpg calls.
    """
    return psycopg2.connect(**coach_chat.DB_CFG, connect_timeout=10)


def _query(sql, params=None):
    conn = _conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or ())
        return cur.fetchall()
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# Fixtures — real (sku, store_id) pairs in whatever state the DB is
# actually in, never assumed
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def demand_forecast_pair():
    """Any (sku, store_id) with a usable future forecast row — the
    minimum needed to exercise get_demand_forecast_batch()."""
    rows = _query("""
        SELECT sku, store_id FROM inventory.demand_forecast
        WHERE COALESCE(corrected_demand, baseline_demand, demand_24h) IS NOT NULL
          AND forecast_date >= CURRENT_DATE
        ORDER BY forecast_date LIMIT 1
    """)
    if not rows:
        pytest.skip(
            "No usable rows in inventory.demand_forecast for a future date — "
            "nothing to test the sales-side readers against yet."
        )
    return rows[0]["sku"], rows[0]["store_id"]


@pytest.fixture(scope="module")
def stock_alert_pair(demand_forecast_pair):
    """A (sku, store_id) that BOTH has a forecast row AND actually lands in
    coach_chat's low-stock alert query result — i.e. among the 6 LOWEST-qty
    skus for its store, not merely qty <= 15 anywhere in that store.

    NOTE: an earlier version of this fixture only checked qty <= 15, which
    is necessary but not sufficient — _load_inventory_context_sync's query
    does `ORDER BY qty ASC LIMIT 6`, so a qty<=15 sku can still be excluded
    if 6+ other skus in the same store have even lower stock. Replicate the
    same ranking here (via ROW_NUMBER PARTITION BY store_id) so the picked
    pair is guaranteed to actually appear in ctx["alerts"].
    """
    rows = _query("""
        WITH ranked AS (
            SELECT sl.sku, sl.store_id,
                   COALESCE(sl.quantity_available, sl.quantity, 0) AS qty,
                   ROW_NUMBER() OVER (
                       PARTITION BY sl.store_id
                       ORDER BY COALESCE(sl.quantity_available, sl.quantity, 0) ASC
                   ) AS rn
            FROM inventory.stock_levels sl
        )
        SELECT r.sku, r.store_id, r.qty
        FROM ranked r
        JOIN inventory.demand_forecast df
          ON df.sku = r.sku AND df.store_id = r.store_id
         AND df.forecast_date >= CURRENT_DATE
         AND COALESCE(df.corrected_demand, df.baseline_demand, df.demand_24h) IS NOT NULL
        WHERE r.rn <= 6 AND r.qty <= 15
        LIMIT 1
    """)
    if not rows:
        pytest.skip(
            "No sku is both forecast-covered AND among the 6 lowest-stock "
            "skus for its store — can't exercise coach_chat's alert-query "
            "LATERAL join end to end with the current DB state."
        )
    return rows[0]["sku"], rows[0]["store_id"]


# ═══════════════════════════════════════════════════════════════════════
# Layer 1 — cross_domain_tools.get_demand_forecast_batch (pure function,
# easiest to isolate)
# ═══════════════════════════════════════════════════════════════════════

def test_get_demand_forecast_batch_matches_manual_average(demand_forecast_pair):
    sku, store_id = demand_forecast_pair
    result = get_demand_forecast_batch([str(sku)], store_id, days=7)

    assert str(sku) in result, (
        f"get_demand_forecast_batch returned nothing for {sku}@{store_id} "
        f"even though inventory.demand_forecast has a covering row."
    )

    expected_row = _query("""
        SELECT AVG(COALESCE(corrected_demand, baseline_demand, demand_24h)) AS avg_demand
        FROM inventory.demand_forecast
        WHERE store_id = %s AND sku = %s
          AND forecast_date >= CURRENT_DATE
          AND forecast_date < CURRENT_DATE + INTERVAL '7 days'
          AND COALESCE(corrected_demand, baseline_demand, demand_24h) IS NOT NULL
    """, (store_id, int(sku)))
    expected = float(expected_row[0]["avg_demand"])

    assert result[str(sku)] == pytest.approx(expected), (
        f"get_demand_forecast_batch()={result[str(sku)]} does not match the "
        f"manual AVG(COALESCE(corrected_demand, baseline_demand, demand_24h))"
        f"={expected} for {sku}@{store_id}."
    )


def test_get_demand_forecast_batch_omits_uncovered_sku():
    """A sku with no demand_forecast rows at all must be absent from the
    dict (not present with a misleading 0.0 / None), per the function's
    documented contract — the caller supplies its own fallback."""
    result = get_demand_forecast_batch(["999999999"], "S01", days=7)
    assert "999999999" not in result


# ═══════════════════════════════════════════════════════════════════════
# Layer 2 — cross_domain_tools.get_recommendable_products actually uses
# the forecast for days_to_stockout, not just the repo's static field
# ═══════════════════════════════════════════════════════════════════════

def test_recommendable_products_days_to_stockout_uses_forecast(demand_forecast_pair):
    sku, store_id = demand_forecast_pair
    products = get_recommendable_products(store_id, gap_amount=100, max_products=50)
    if not products:
        pytest.skip(f"No recommendable products returned for store {store_id}.")

    matching = [p for p in products if p["sku"] == str(sku)]
    if not matching:
        pytest.skip(
            f"{sku}@{store_id} has a forecast but isn't in the recommendable "
            f"list (e.g. zero stock) — nothing to cross-check here."
        )
    product = matching[0]

    demand_by_sku = get_demand_forecast_batch([str(sku)], store_id, days=7)
    avg_daily_demand = demand_by_sku.get(str(sku))
    assert avg_daily_demand, "fixture guarantees a forecast row but batch lookup found none."

    expected_days = round(product["stock_current"] / avg_daily_demand, 1)
    assert product["days_to_stockout"] == pytest.approx(expected_days), (
        f"days_to_stockout={product['days_to_stockout']} does not match "
        f"stock_current/avg_daily_demand={expected_days} — get_recommendable_products "
        f"isn't actually using the demand-sensing forecast for this sku."
    )


# ═══════════════════════════════════════════════════════════════════════
# Layer 3 — coach_chat._load_inventory_context_sync's raw SQL end to end
# (the actual regression target: it used to read the dead
# supply.reorder_params.demande_moy_jour column)
# ═══════════════════════════════════════════════════════════════════════

def test_load_inventory_context_alerts_use_demand_forecast(stock_alert_pair):
    sku, store_id = stock_alert_pair
    ctx = coach_chat._load_inventory_context_sync(store_id)

    matching = [a for a in ctx["alerts"] if str(a.get("nom", "")) or True]
    assert ctx["alerts"], f"No alerts returned for {store_id} despite the fixture pair."

    # Recompute "jours" independently, the same way the LATERAL-joined
    # query does, and confirm at least one alert lines up — this is what
    # actually proves the query is reading demand_forecast, not silently
    # falling back to the 0.5 default (which reorder_params.demande_moy_jour
    # being empty would also produce, masking a regression).
    expected_row = _query("""
        SELECT COALESCE(sl.quantity_available, sl.quantity, 0) AS qty,
               COALESCE(fc.demand, 0.5) AS vel
        FROM inventory.stock_levels sl
        LEFT JOIN LATERAL (
            SELECT COALESCE(df.corrected_demand, df.baseline_demand, df.demand_24h) AS demand
            FROM inventory.demand_forecast df
            WHERE df.sku = sl.sku AND df.store_id = sl.store_id
              AND df.forecast_date >= CURRENT_DATE
            ORDER BY df.forecast_date ASC
            LIMIT 1
        ) fc ON TRUE
        WHERE sl.sku = %s AND sl.store_id = %s
    """, (int(sku), store_id))
    assert expected_row, f"stock_levels row disappeared for {sku}@{store_id} mid-test."

    stock = float(expected_row[0]["qty"] or 0)
    vel = float(expected_row[0]["vel"] or 0.5)
    assert vel != 0.5, (
        f"LATERAL join fell back to the 0.5 default for {sku}@{store_id} even "
        f"though the fixture guarantees a covering demand_forecast row — "
        f"check the join condition (sku/store_id/forecast_date types)."
    )
    expected_jours = round(stock / vel, 0) if vel > 0 and stock > 0 else 0

    found = [a for a in ctx["alerts"] if a["qty"] == int(stock)]
    assert found, (
        f"No alert row in _load_inventory_context_sync's output matches "
        f"stock={stock} for {sku}@{store_id} — can't cross-check jours."
    )
    assert any(a["jours"] == expected_jours for a in found), (
        f"None of the matching alerts have jours={expected_jours} "
        f"(computed from the live demand_forecast vel={vel}) — "
        f"_load_inventory_context_sync may still be using a stale/default vel."
    )


# ═══════════════════════════════════════════════════════════════════════
# Layer 4 — cross-agent consistency: inventory's fetch_node() and sales'
# get_demand_forecast_batch() are two INDEPENDENT readers of
# inventory.demand_forecast (neither calls the other). This proves they
# agree numerically for the same (sku, store) instead of quietly drifting
# apart, e.g. one silently reading a stale COALESCE priority.
# ═══════════════════════════════════════════════════════════════════════

def test_cross_agent_forecast_consistency(demand_forecast_pair):
    """Compare the inventory agent's forecast value against the sales
    tool's forecast value for the exact same forecast row.

    Only performs the hard numeric comparison when it can line up the
    *same single row* on both sides: fetch_node() surfaces its nearest
    forecast_date, and get_demand_forecast_batch(days=1) always windows
    from CURRENT_DATE — so the two only isolate the same row when that
    nearest date IS today. If the nearest forecast starts later (e.g. the
    baseline hasn't been re-run today), skip rather than comparing a
    single inventory value against a differently-windowed sales figure,
    which would be a meaningless apples-to-oranges check, not a real
    consistency test. (Each side's correctness against the DB is already
    covered independently by test_demand_sensing.py and the tests above.)
    """
    sku, store_id = demand_forecast_pair

    fetch_result = fetch_node({"sku": sku, "store_id": store_id})
    fetch_data = fetch_result["fetch_data"]
    if fetch_data["forecast_source"] != "demand_sensing_db":
        pytest.skip(
            f"fetch_node used '{fetch_data['forecast_source']}' instead of "
            f"the DB forecast for {sku}@{store_id} — nothing to cross-check "
            f"against the sales-side reader for this pair."
        )

    forecast_df = fetch_data["forecast_df"]
    nearest_date = pd.Timestamp(forecast_df.iloc[0]["date"]).date()
    if nearest_date != date.today():
        pytest.skip(
            f"Nearest forecast_date for {sku}@{store_id} is {nearest_date}, "
            f"not today ({date.today()}) — a 1-day sales-side window can't "
            f"isolate the same single row fetch_node used, so an exact "
            f"comparison here would be meaningless. Not a failure: run the "
            f"baseline/sensing batch today and re-run this test if you want "
            f"the exact cross-check to execute."
        )

    inventory_val = float(forecast_df.iloc[0]["predicted_demand"])
    sales_val = get_demand_forecast_batch([str(sku)], store_id, days=1).get(str(sku))

    assert sales_val is not None, (
        f"get_demand_forecast_batch found nothing for {sku}@{store_id} even "
        f"though fetch_node found a DB forecast dated today — the two "
        f"readers disagree on whether a forecast exists for this date."
    )
    assert inventory_val == pytest.approx(sales_val), (
        f"Inventory-side value ({inventory_val}) and sales-side value "
        f"({sales_val}) for {sku}@{store_id}'s forecast dated today "
        f"diverge — fetch_node and get_demand_forecast_batch are no longer "
        f"reading the same COALESCE(corrected_demand, baseline_demand, "
        f"demand_24h) row from inventory.demand_forecast."
    )