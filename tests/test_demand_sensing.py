"""
test_demand_sensing.py
=======================
Read-only integration tests confirming the demand-sensing forecast
(inventory.demand_forecast: baseline_demand / corrected_demand) is
actually wired into and used by the analysis agent — not just that the
pipeline scripts ran and wrote rows.

IMPORTANT: this file never calls backfill_baseline_forecasts.py,
run_baseline_batch.py, run_sensing_job.py, or train_sensing_model.py.
It only reads whatever is already in the DB right now. If a test skips
with "no corrected_demand rows found", that's real information (the
sensing job hasn't run / hasn't covered any pair yet) — it is NOT a
reason to run the pipeline again from this file.

Run with:
    pytest test_demand_sensing.py -v -m integration

All tests are marked `integration` since they hit a live Postgres
connection via the existing connection pool in stock_tools.py.
"""
import sys
from pathlib import Path

# Same Windows DLL load-order fix used throughout this codebase
# (backfill.py, backfill_baseline_forecasts.py, run_baseline_batch.py):
# torch / coreforecast must be imported before pandas/numpy in any
# process that will eventually import timeseries_engine.py — which
# nodes.py does at module import time. If pandas loads first here
# (e.g. because pytest or another test module imports it before this
# file does), importing nodes.py below can crash the whole test run
# with a native access violation instead of a normal Python traceback.
try:
    import torch  # noqa: F401
    import coreforecast.exponentially_weighted  # noqa: F401
except ImportError:
    pass  # not on Windows / not installed — the DLL issue doesn't apply

import pytest
import pandas as pd

# Adjust import path the same way the docs/inventory/scripts/*.py files do,
# in case this test isn't run from a location where `app` is already
# importable (e.g. run directly with `python test_demand_sensing.py`
# instead of via pytest from the project root).
try:
    from app.inventory.tools.internal.stock_tools import (
        _query, get_forecast, get_forecast_data, _DataCache,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # -> back/
    from app.inventory.tools.internal.stock_tools import (
        _query, get_forecast, get_forecast_data, _DataCache,
    )

from app.inventory.agents.analysis.nodes import fetch_node, compute_node
from app.inventory.config import settings as inv_settings

pytestmark = pytest.mark.integration


# ═══════════════════════════════════════════════════════════════════════
# Fixtures — find real pairs in whatever state the DB is actually in,
# never assume specific SKUs exist
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def sensing_covered_pair():
    """A (sku, store_id) with at least one future baseline_demand row —
    i.e. the batch baseline pipeline has covered this pair, whether or
    not the sensing correction has run on top of it yet."""
    rows = _query("""
        SELECT sku, store_id FROM inventory.demand_forecast
        WHERE baseline_demand IS NOT NULL AND forecast_date >= CURRENT_DATE
        ORDER BY forecast_date LIMIT 1
    """)
    if not rows:
        pytest.skip(
            "No rows in inventory.demand_forecast with baseline_demand set "
            "for a future date — nothing to test against. This means the "
            "baseline batch hasn't covered any pair with dates still ahead "
            "of CURRENT_DATE, not that the integration code is broken."
        )
    return rows[0]["sku"], rows[0]["store_id"]


@pytest.fixture(scope="module")
def sensing_corrected_pair():
    """A (sku, store_id) with an actual corrected_demand value — i.e. the
    sensing correction job has run for this pair. May legitimately not
    exist yet (per the sample data shared, corrected_demand is NULL
    everywhere right now) — tests using this fixture skip, not fail, if so."""
    rows = _query("""
        SELECT sku, store_id FROM inventory.demand_forecast
        WHERE corrected_demand IS NOT NULL AND forecast_date >= CURRENT_DATE
        ORDER BY forecast_date LIMIT 1
    """)
    if not rows:
        pytest.skip(
            "No corrected_demand rows found yet — run_sensing_job.py hasn't "
            "produced output for any pair (or none are still date-valid). "
            "This is a legitimate 'not run yet' state, not a bug — skipping "
            "the correction-priority assertion rather than failing it."
        )
    return rows[0]["sku"], rows[0]["store_id"]


@pytest.fixture(scope="module")
def uncovered_pair():
    """A (sku, store_id) that has stock but NO row at all in
    inventory.demand_forecast for a future date — used to confirm the
    fallback-to-live-TS path still works for pairs outside the batch's
    TOP_N_PAIRS coverage, instead of crashing or silently returning
    nothing."""
    rows = _query("""
        SELECT sl.sku, sl.store_id
        FROM inventory.stock_levels sl
        LEFT JOIN inventory.demand_forecast df
          ON df.sku = sl.sku AND df.store_id = sl.store_id
         AND df.forecast_date >= CURRENT_DATE
        WHERE df.sku IS NULL
        LIMIT 1
    """)
    if not rows:
        pytest.skip(
            "Every sku/store pair with stock already has a demand_forecast "
            "row — no uncovered pair available to test the fallback path."
        )
    return rows[0]["sku"], rows[0]["store_id"]


# ═══════════════════════════════════════════════════════════════════════
# Layer 1 — DB state sanity (no app code involved)
# ═══════════════════════════════════════════════════════════════════════

def test_demand_forecast_table_has_baseline_data():
    """Sanity check before testing anything else: the baseline batch
    actually wrote rows. If this fails, every test below will too, for
    the boring reason that there's no data — check that first."""
    row = _query(
        "SELECT COUNT(*) AS n FROM inventory.demand_forecast "
        "WHERE baseline_demand IS NOT NULL",
        fetch="one",
    )
    assert row and row["n"] > 0, (
        "inventory.demand_forecast has zero baseline_demand rows — "
        "the baseline batch hasn't written anything yet."
    )


# ═══════════════════════════════════════════════════════════════════════
# Layer 2 — stock_tools.py readers (isolated from nodes.py)
# ═══════════════════════════════════════════════════════════════════════

def test_get_forecast_data_reads_from_demand_forecast_table(sensing_covered_pair):
    sku, store_id = sensing_covered_pair
    df = get_forecast_data(sku, store_id)
    assert not df.empty, f"get_forecast_data returned nothing for {sku}@{store_id}"
    assert {"baseline_demand", "corrected_demand", "correction_method"} <= set(df.columns)
    assert df["baseline_demand"].notna().any()

    # predicted_demand must equal corrected_demand where set, else baseline_demand
    for _, row in df.iterrows():
        expected = row["corrected_demand"] if pd.notna(row["corrected_demand"]) else row["baseline_demand"]
        if pd.notna(expected):
            assert row["predicted_demand"] == pytest.approx(float(expected)), (
                f"predicted_demand={row['predicted_demand']} does not match "
                f"COALESCE(corrected_demand, baseline_demand)={expected} "
                f"on {row.get('date')}"
            )


def test_get_forecast_alias_matches_underlying_data(sensing_covered_pair):
    """get_forecast() is the alias analysis/nodes.py actually imports —
    confirm it isn't silently diverging from get_forecast_data()."""
    sku, store_id = sensing_covered_pair
    df = get_forecast(sku, store_id)
    assert not df.empty
    assert "predicted_demand" in df.columns
    assert "baseline_demand" in df.columns, (
        "get_forecast() lost the baseline_demand column — fetch_node's "
        "has_sensing_forecast check depends on this column existing."
    )


def test_datacache_forecast_shim_reads_real_forecast_not_sales_actuals(sensing_covered_pair):
    """Regression test for the bug fixed in _DataCache.forecast(): it used
    to relabel sales_history.quantity_sold as predicted_demand. Confirm the
    values now actually come from inventory.demand_forecast."""
    sku, store_id = sensing_covered_pair
    _DataCache.invalidate()  # ensure a fresh read, not a stale cached copy
    df = _DataCache.forecast()
    assert not df.empty, "_DataCache.forecast() returned nothing at all"

    sku_str = str(sku)
    pair_rows = df[(df["sku"] == sku_str) & (df["store_id"] == store_id)]
    assert not pair_rows.empty, (
        f"_DataCache.forecast() has no rows for {sku}@{store_id} even though "
        f"inventory.demand_forecast does — check the WHERE clause / date filter."
    )
    assert "baseline_demand" in pair_rows.columns, (
        "_DataCache.forecast() doesn't expose baseline_demand — this usually "
        "means it's still reading sales_history instead of demand_forecast."
    )


# ═══════════════════════════════════════════════════════════════════════
# Layer 3 — fetch_node's routing decision (the actual integration point)
# ═══════════════════════════════════════════════════════════════════════

def test_fetch_node_prefers_db_forecast_when_available(sensing_covered_pair):
    sku, store_id = sensing_covered_pair
    result = fetch_node({"sku": sku, "store_id": store_id})
    fetch_data = result["fetch_data"]

    assert fetch_data["forecast_source"] == "demand_sensing_db", (
        f"fetch_node used '{fetch_data['forecast_source']}' instead of the "
        f"persisted DB forecast for {sku}@{store_id}, even though "
        f"inventory.demand_forecast has data for this pair — the priority "
        f"flip in fetch_node isn't taking effect."
    )
    # A live TS call should NOT have run when the DB forecast was used —
    # this is the actual point of the fix (avoid discarding the richer
    # DB signal in favor of a plain live recompute).
    assert fetch_data["ts_result"] == {}, (
        "ts_result is non-empty even though a DB forecast was used — "
        "the live TS engine ran (and its output was then ignored), which "
        "means the priority order isn't skipping it as intended."
    )


def test_fetch_node_forecast_df_matches_db_values(sensing_covered_pair):
    sku, store_id = sensing_covered_pair
    result = fetch_node({"sku": sku, "store_id": store_id})
    forecast_df = result["fetch_data"]["forecast_df"]
    db_df = get_forecast_data(sku, store_id)

    assert not forecast_df.empty
    # Compare the first forecasted value directly against the DB row —
    # if fetch_node quietly rebuilt its own numbers instead of passing
    # the DB ones through, this will catch it.
    first_db_val = float(db_df.iloc[0]["predicted_demand"])
    first_fetch_val = float(forecast_df.iloc[0]["predicted_demand"])
    assert first_fetch_val == pytest.approx(first_db_val), (
        f"fetch_node's forecast_df value ({first_fetch_val}) doesn't match "
        f"the DB's predicted_demand ({first_db_val}) — forecast_df is not "
        f"actually the DB data."
    )


def test_fetch_node_prioritizes_corrected_over_baseline(sensing_corrected_pair):
    """Only runs if a real corrected_demand row exists (skips otherwise —
    see the fixture). This is the test that specifically proves the
    sensing correction, not just the baseline, reaches the agent."""
    sku, store_id = sensing_corrected_pair
    db_row = _query("""
        SELECT corrected_demand, baseline_demand FROM inventory.demand_forecast
        WHERE sku = %s AND store_id = %s AND corrected_demand IS NOT NULL
        ORDER BY forecast_date LIMIT 1
    """, (sku, store_id), fetch="one")

    result = fetch_node({"sku": sku, "store_id": store_id})
    forecast_df = result["fetch_data"]["forecast_df"]
    first_val = float(forecast_df.iloc[0]["predicted_demand"])

    assert first_val == pytest.approx(float(db_row["corrected_demand"])), (
        f"Expected corrected_demand ({db_row['corrected_demand']}) to win, "
        f"but fetch_node produced {first_val} — check the COALESCE priority "
        f"hasn't regressed to preferring baseline_demand."
    )


def test_fetch_node_falls_back_to_live_ts_for_uncovered_pair(uncovered_pair):
    """A pair the batch pipeline hasn't reached yet must not crash, and
    must not falsely report 'demand_sensing_db' as its source."""
    sku, store_id = uncovered_pair
    result = fetch_node({"sku": sku, "store_id": store_id})
    fetch_data = result["fetch_data"]

    assert fetch_data["forecast_source"] in ("live_ts_engine", "fallback_flat"), (
        f"Expected a fallback source for an uncovered pair, got "
        f"'{fetch_data['forecast_source']}'."
    )
    assert not fetch_data["forecast_df"].empty, (
        "forecast_df is empty for an uncovered pair — the fallback path "
        "should still produce something (live TS or flat placeholder)."
    )


# ═══════════════════════════════════════════════════════════════════════
# Layer 4 — compute_node actually uses the DB-sourced numbers
# ═══════════════════════════════════════════════════════════════════════

def test_compute_node_uses_db_forecast_end_to_end(sensing_covered_pair):
    sku, store_id = sensing_covered_pair
    fetch_result = fetch_node({"sku": sku, "store_id": store_id})
    state = {"sku": sku, "store_id": store_id, **fetch_result}

    compute_result = compute_node(state)
    metrics = compute_result["computed_metrics"]

    assert metrics["forecast"]["forecast_source"] == "demand_sensing_db", (
        "computed_metrics doesn't carry forecast_source through from "
        "fetch_node — check compute_node still reads it from fetch_data."
    )
    assert metrics["forecast"]["avg_daily_demand"] > 0, (
        "avg_daily_demand computed as 0 — likely fell through to an empty "
        "forecast_df somewhere despite forecast_source claiming DB data."
    )


# ═══════════════════════════════════════════════════════════════════════
# Layer 5 — settings.py constants match what the pipeline scripts
# actually produce (static check, no DB needed)
# ═══════════════════════════════════════════════════════════════════════

def test_sensing_model_path_extension_matches_saved_model():
    """train_sensing_model.py saves sensing_model_v1.ubj — settings.py
    used to default to .txt, which would silently point at a file that
    doesn't exist if anything ever loads the model via settings instead
    of a hardcoded path."""
    assert str(inv_settings.SENSING_MODEL_PATH).endswith(".ubj"), (
        f"SENSING_MODEL_PATH={inv_settings.SENSING_MODEL_PATH!r} doesn't "
        f"match the .ubj file train_sensing_model.py actually saves."
    )


def test_sensing_horizon_days_matches_run_sensing_job_default():
    """run_sensing_job.py hardcodes SENSING_HORIZON_DAYS = 7 with a
    documented reason (recent_actual_avg / stockout_flag_7d need real
    past actuals). settings.py's default should agree, in case anything
    else starts reading it instead of the hardcoded value."""
    assert inv_settings.SENSING_HORIZON_DAYS == 7, (
        f"settings.SENSING_HORIZON_DAYS={inv_settings.SENSING_HORIZON_DAYS} "
        f"disagrees with run_sensing_job.py's hardcoded value of 7."
    )
