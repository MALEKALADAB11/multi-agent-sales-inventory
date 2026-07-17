# Demand Sensing Layer — Implementation Guide (v4, file-by-file, confirmed)

**Self-contained** — written for a fresh session with no prior chat context. Repo root referenced below is `back/`. All paths are relative to that root.

**What changed from v3**: `stock_tools.py`, `inventory_repo.py`, and `nodes.py` have now all been read directly. Section 2 is resolved (not a question anymore). Two real bugs surfaced that v3 couldn't have known about (see 1.5 and 1.6 below). The async/sync repo split is resolved. All code in Section 5 is written against the actual function signatures, not assumed ones.

---

## 0. Scope decision: why inventory-only for now, and how to extend to sales later

**Why separate right now**: `app/sales/mcp/timefm/tools.py` (Prophet) forecasts aggregate **store revenue in TND** for coaching/EOD tracking. This guide's pipeline forecasts **per-SKU unit demand**. Different grain (store-level vs SKU-level), different unit (currency vs units), different consumers (`app/sales/coaching/agents/analyst/`, `app/api/forecast.py` vs `app/inventory/agents/`). Merging them now would mean redesigning both at once with no time budget for either.

**How to extend later without a rewrite** — do this now, costs nothing: write the two new modules in Section 4 (`sensing_features.py`, `sensing_model.py`) generically — take a target column name and a grain (`sku+store` vs `store`) as parameters instead of hardcoding "SKU demand." Then extending to sales later is:
- New folder `app/sales/forecasting/` mirroring `app/inventory/forecasting/`, reusing the same `SensingModel` class (move to `app/core/forecasting/` if/when you do this) with `target_col="revenue"`, `grain=["store_id"]`.
- Replace Prophet's `implied_rate` logic in `tools.py` with: Prophet (or the inventory baseline engine) as Layer 1, then the shared sensing model as Layer 2, both writing to a `sales.revenue_forecast` table mirroring `inventory.demand_forecast`.
- Distinct, later project — don't start it now.

---

## 1. Confirmed codebase facts

**1.1 — Baseline engine**: `app/inventory/forecasting/timeseries_engine.py` exports a function `forecast` (imported in `nodes.py` as `from ...timeseries_engine import forecast as ts_forecast, extract_series_from_sales`) — ensemble StatsForecast/Chronos/numpy, `season_length=7` hardcoded per the earlier review (weekly-only). **Not independently re-verified this session** — the file itself wasn't provided, only its call sites in `nodes.py`. Grep it directly before editing.

**1.2 — Legacy TimesFM**: `app/inventory/forecasting/timesfm_forecaster.py`, meant to be run by `docs/inventory/scripts/generate_forecast.py` — confirmed broken imports, cannot currently execute. Not worth fixing.

**1.3 — Table `inventory.demand_forecast`**: `id, sku, store_id, forecast_date, demand_24h, confidence_low, confidence_high, model_version, created_at`, FK-constrained since migration `0006`. A unique index on `(sku, store_id, forecast_date)` **is already confirmed to exist** — `InventoryRepo.insert_forecast()` already does `ON CONFLICT (sku, store_id, forecast_date) DO UPDATE` today and this is live, working code. (v3 flagged this as unverified — it's now settled, no new index needed in the `0009` migration.)

**1.4 — RESOLVED: `fetch_node`'s forecast source** (was Section 2 in v3, now closed — see Section 2 below).

**1.5 — NEW FINDING: the seasonality fix in v3's Step 0 was incomplete.** `nodes.py` line 99 calls `get_sales_history(sku, store_id)` with no `days` argument, so it uses `stock_tools.py`'s default `days=365`. Line 116 then calls `extract_series_from_sales(sales_df, sku, store_id, days_back=90)`. Bumping `days_back` to 730 inside `timeseries_engine.py` (v3's Step 0) does **nothing** on its own — `sales_df` itself is capped at 365 days before it ever reaches that function, because of line 99. Both call sites in `nodes.py` need to change together with the engine, or the annual-seasonality fix is silently inert. Fixed in Step 0 below.

**1.6 — NEW FINDING: `stock_tools.py` is read-only by design, and its `_query()` helper never commits.** The module docstring says "Lit TOUT depuis PostgreSQL — zero CSV" (reads only), and a full-file grep for `commit`/`autocommit`/`INSERT`/`UPDATE` returns nothing — every function in the file is a `SELECT`. `_query()` (line 78) never calls `conn.commit()`. This is fine as-is because nothing writes through it today, but it's a trap: if anyone later adds an `INSERT`/`UPDATE` through `_query()` expecting it to persist, it will silently no-op (psycopg2 connections default to non-autocommit). **Do not write demand-forecast rows through `stock_tools.py`.** All writes for this feature go through `InventoryRepo` (the async class in `inventory_repo.py`), which already commits correctly via `asyncpg`'s auto-commit-per-statement behavior on its existing methods (confirmed — `start_agent_run`, `complete_agent_run`, `insert_forecast` all already work this way today).

**1.7 — The repo has two unrelated DB layers, not one.** `stock_tools.py` runs its own `psycopg2` `ThreadedConnectionPool` directly (`_get_pool()`/`_query()`), completely separate from `inventory_repo.py`. And `inventory_repo.py` itself has **two classes**: `InventoryRepo` (async, `asyncpg`, instantiated as `repo = InventoryRepo(); await repo.connect()`) and `SyncInventoryRepo` (sync, `psycopg2`, all `@staticmethod`s, its own separate `_conn()`). `SyncInventoryRepo` has **no forecast methods at all** — `insert_forecast()` and `get_forecasts_for_date()` only exist on the async `InventoryRepo`. `fetch_node` uses `SyncInventoryRepo` (only for `get_active_objective()`) and `stock_tools` (for stock/product/sales/forecast reads) — it never touches async `InventoryRepo` directly. This shapes the whole design below: reads in the request path stay in `stock_tools.py` (already sync, already wired in); writes from batch jobs go through async `InventoryRepo` wrapped in `asyncio.run()` since batch jobs aren't in a request path and can afford to be async.

**1.8 — `InventoryRepo.get_forecasts_for_date(forecast_date, store_id=None)` is not what its name suggests.** It takes a single `forecast_date` and optional `store_id`, joins to `inventory.products`, and returns **all SKUs for that one date** (used for a report/dashboard view, ordered by `demand_24h DESC`). It is not "next N days for one SKU." v3's Step 2 and Step 11 assumed the latter and would have broken this existing method's contract for whatever already calls it. **Do not modify this method's signature.** Add a new one instead (Step 2 below).

**1.9** — `app/inventory/agents/context/` is almost certainly what populates `inventory.context_adjustments` (the "context agent," planned/external, not reactive). Not re-verified this session.

**1.10** — Migrations run `0001` → `0008`. Next one is `0009`.

---

## 2. RESOLVED: what `fetch_node` actually calls

Confirmed directly from `nodes.py`. The forecast source is a **three-tier fallback**, not a single call:

1. **Primary — in-memory `TimeSeriesEngine`.** If `_TS_ENGINE_AVAILABLE` and `sales_df` isn't empty: `series = extract_series_from_sales(sales_df, sku, store_id, days_back=90)`, then `ts_result = ts_forecast(series, horizon=30)`. If this succeeds, `forecast_df` is built from `ts_result["forecast_values"]` and **neither DB path below is ever called.** This is the common case today.
2. **Fallback (A) — `stock_tools.get_forecast(sku, store_id)`**, only reached `if not ts_result:` (engine unavailable or threw). This is an alias for `get_forecast_data()`, which joins `inventory.sales_history` + `inventory.stock_history` and renames `quantity_sold` → `predicted_demand`. Confirmed by direct read: it **never queries `inventory.demand_forecast`**. It's relabeled actuals, exactly as suspected.
3. **Fallback of the fallback** — if `get_forecast()` also returns empty, a flat constant (`predicted_demand = 1.0` × 30 days).

So: `fetch_node` is not "reading a real forecast vs. a fake one" as a binary choice — most calls today recompute a fresh in-memory forecast per SKU, live, from `timeseries_engine.py`, and only fall back to the fake one when that fails. This means **the batch pipeline you're building doesn't just fix a wrong data source — it removes the need for a live recompute on every single node invocation.** That's the real payoff of Section 6's wiring, not just correctness.

---

## 3. Model choice — decided, not a menu

- **Baseline**: `TimeSeriesEngine` (`app/inventory/forecasting/timeseries_engine.py`), fixed for seasonality (Step 0, corrected per 1.5). Not TimesFM (dead script).
- **Sensing**: LightGBM regression (`lightgbm.LGBMRegressor`), predicting `corrected_demand` directly. Add `lightgbm` to `requirements.txt`.

---

## 4. Complete file manifest

### Files to MODIFY
| File | Change |
|---|---|
| `app/inventory/agents/analysis/nodes.py` | **Two separate edits, do both together (1.5)**: line 99 `get_sales_history(sku, store_id)` → add `days=730`; line 116 `days_back=90` → `days_back=730`. Then later (Section 6, after batch jobs are live) rewire the fetch order itself. |
| `app/inventory/forecasting/timeseries_engine.py` | Seasonality: single-seasonality StatsForecast models → `MSTL(season_length=[7, 365])`. **Not independently verified this session (1.1)** — grep for `season_length`/`days_back` first. |
| `app/inventory/repositories/inventory_repo.py` | On the **async `InventoryRepo` class only**: extend `insert_forecast()`'s dict/SQL to accept `baseline_demand`/`corrected_demand`/`correction_method`/`correction_features`; **add a new method** `get_forecast_range(sku, store_id, days=30)` — do not touch `get_forecasts_for_date()` (1.8). No changes to `SyncInventoryRepo` — it isn't the path used here (1.7). |
| `app/inventory/tools/internal/stock_tools.py` | `get_forecast_data()`: query `inventory.demand_forecast` instead of joining `sales_history`/`stock_history`. This is the path `fetch_node`'s fallback actually reaches (Section 2), so fixing it here fixes the fallback with no `nodes.py` change needed for that part. Read-only, no new writes here (1.6). |
| `app/inventory/config/settings.py` (or `inventory_settings.py` — **file not provided, name unconfirmed, check which one holds runtime constants**) | Add `SENSING_MODEL_PATH`, `BASELINE_DAYS_BACK = 730`, `SENSING_HORIZON_DAYS = 14`. |
| `requirements.txt` | Add `lightgbm`. |

### Files to CREATE
| File | Purpose |
|---|---|
| `db/migrations/versions/0009_demand_sensing.py` | Schema migration — new columns + `forecast_accuracy` table. No unique-index addition needed (1.3). |
| `app/inventory/forecasting/sensing_features.py` | Feature extraction — shared by training and production. |
| `app/inventory/forecasting/sensing_model.py` | `SensingModel` class — train/predict/save/load LightGBM. |
| `app/inventory/forecasting/backfill.py` | Walk-forward historical baseline generator. |
| `app/inventory/forecasting/models/` (directory) | Trained model artifacts, e.g. `sensing_model_v1.txt`. |
| `docs/inventory/scripts/backfill_baseline_forecasts.py` | One-time runner — calls `backfill.py`. |
| `docs/inventory/scripts/build_sensing_training_table.py` | One-time runner — joins backfill + signals + actuals into a training table. |
| `docs/inventory/scripts/train_sensing_model.py` | Trains and saves the LightGBM model. |
| `docs/inventory/scripts/run_baseline_batch.py` | Replaces broken `generate_forecast.py` — nightly/monthly baseline job, writes `baseline_demand`. Async (1.7). |
| `docs/inventory/scripts/run_sensing_job.py` | Daily/intraday production job — writes `corrected_demand`. Async (1.7). |
| `docs/inventory/scripts/log_forecast_accuracy.py` | Nightly — compares past predictions to actuals. |

### File to RETIRE (do not fix, do not delete yet)
| File | Why |
|---|---|
| `docs/inventory/scripts/generate_forecast.py` | Broken imports, superseded by `run_baseline_batch.py`. Leave in place, stop scheduling it. |

---

## 5. Step-by-step

### Step 0 — `nodes.py` + `timeseries_engine.py`: fix seasonality (both files, together — 1.5)

```python
# app/inventory/agents/analysis/nodes.py, line 99 — WAS:
sales_df = get_sales_history(sku, store_id)
# CHANGE TO:
sales_df = get_sales_history(sku, store_id, days=730)

# same file, line 116 — WAS:
series = extract_series_from_sales(sales_df, sku, store_id, days_back=90)
# CHANGE TO:
series = extract_series_from_sales(sales_df, sku, store_id, days_back=730)
```

```python
# app/inventory/forecasting/timeseries_engine.py — wherever StatsForecast models
# are constructed, e.g.:
#   from statsforecast.models import AutoARIMA, AutoETS, AutoCES
#   models = [AutoARIMA(season_length=7), AutoETS(season_length=7), AutoCES(season_length=7)]
# replace with:
from statsforecast.models import MSTL
models = [MSTL(season_length=[7, 365])]
```
Keep the Chronos-Bolt / numpy fallback chain as-is. **Do this file's edit first, verify against the real file** — 1.1 is unconfirmed this session.

### Step 1 — `db/migrations/versions/0009_demand_sensing.py`
```python
"""Add baseline/corrected forecast columns + forecast_accuracy table.

Backward-compatible: existing demand_24h/confidence_low/confidence_high/
model_version columns on inventory.demand_forecast are untouched.

Revision ID: 0009
Revises: 0008
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE inventory.demand_forecast
          ADD COLUMN baseline_demand         NUMERIC,
          ADD COLUMN corrected_demand        NUMERIC,
          ADD COLUMN correction_method       TEXT,
          ADD COLUMN correction_features     JSONB,
          ADD COLUMN baseline_generated_at   TIMESTAMPTZ,
          ADD COLUMN corrected_generated_at  TIMESTAMPTZ;

        CREATE TABLE inventory.forecast_accuracy (
            id              SERIAL PRIMARY KEY,
            sku             INTEGER NOT NULL,
            store_id        TEXT NOT NULL,
            forecast_date   DATE NOT NULL,
            baseline_demand NUMERIC,
            corrected_demand NUMERIC,
            actual_demand   NUMERIC,
            baseline_error  NUMERIC,
            corrected_error NUMERIC,
            logged_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_forecast_accuracy_sku_store
            ON inventory.forecast_accuracy (sku, store_id, forecast_date);
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE inventory.forecast_accuracy;
        ALTER TABLE inventory.demand_forecast
          DROP COLUMN baseline_demand, DROP COLUMN corrected_demand,
          DROP COLUMN correction_method, DROP COLUMN correction_features,
          DROP COLUMN baseline_generated_at, DROP COLUMN corrected_generated_at;
    """)
```
Run with `alembic upgrade head`. No unique-index migration needed — confirmed already present (1.3).

### Step 2 — `inventory_repo.py`: extend `InventoryRepo` (async class only, `asyncpg` `$n` placeholders — 1.7, 1.8)

```python
# app/inventory/repositories/inventory_repo.py — extend the EXISTING insert_forecast
# method on class InventoryRepo. Replace its body with:

async def insert_forecast(self, forecast: dict) -> None:
    async with self.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO inventory.demand_forecast
                (sku, store_id, forecast_date, demand_24h,
                 confidence_low, confidence_high, model_version,
                 baseline_demand, corrected_demand, correction_method,
                 correction_features, baseline_generated_at, corrected_generated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,
                    CASE WHEN $8::numeric IS NOT NULL THEN now() END,
                    CASE WHEN $9::numeric IS NOT NULL THEN now() END)
            ON CONFLICT (sku, store_id, forecast_date) DO UPDATE SET
                demand_24h             = COALESCE(EXCLUDED.demand_24h, inventory.demand_forecast.demand_24h),
                confidence_low         = COALESCE(EXCLUDED.confidence_low, inventory.demand_forecast.confidence_low),
                confidence_high        = COALESCE(EXCLUDED.confidence_high, inventory.demand_forecast.confidence_high),
                model_version          = COALESCE(EXCLUDED.model_version, inventory.demand_forecast.model_version),
                baseline_demand        = COALESCE(EXCLUDED.baseline_demand, inventory.demand_forecast.baseline_demand),
                corrected_demand       = COALESCE(EXCLUDED.corrected_demand, inventory.demand_forecast.corrected_demand),
                correction_method      = COALESCE(EXCLUDED.correction_method, inventory.demand_forecast.correction_method),
                correction_features    = COALESCE(EXCLUDED.correction_features, inventory.demand_forecast.correction_features),
                baseline_generated_at  = COALESCE(EXCLUDED.baseline_generated_at, inventory.demand_forecast.baseline_generated_at),
                corrected_generated_at = COALESCE(EXCLUDED.corrected_generated_at, inventory.demand_forecast.corrected_generated_at)
        """,
            forecast["sku"], forecast["store_id"], forecast["forecast_date"],
            forecast.get("demand_24h"), forecast.get("confidence_low"), forecast.get("confidence_high"),
            forecast.get("model_version"),
            forecast.get("baseline_demand"), forecast.get("corrected_demand"),
            forecast.get("correction_method"),
            json.dumps(forecast["correction_features"]) if forecast.get("correction_features") else None,
        )

# ADD a new method — do not repurpose get_forecasts_for_date() (1.8), it already
# means something else (all-SKU report for one date) and something else may call it.
async def get_forecast_range(self, sku, store_id: str, days: int = 30) -> list[dict]:
    """Forward-looking forecast rows for one SKU/store, for use by fetch_node's
    persisted-forecast path (Section 6)."""
    async with self.pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT forecast_date,
                   COALESCE(corrected_demand, baseline_demand, demand_24h) AS predicted_demand,
                   baseline_demand, corrected_demand, correction_method
            FROM inventory.demand_forecast
            WHERE sku = $1 AND store_id = $2 AND forecast_date >= CURRENT_DATE
            ORDER BY forecast_date LIMIT $3
        """, sku, store_id, days)
        return [dict(r) for r in rows]
```
Add `import json` at the top of `inventory_repo.py` if not already present.

### Step 3 — Fix `stock_tools.py` (read path used by `fetch_node`'s fallback — confirmed always relevant, not conditional)

```python
# app/inventory/tools/internal/stock_tools.py — replace get_forecast_data()'s body

def get_forecast_data(sku, store_id: str = DEFAULT_STORE_ID, days: int = 30) -> pd.DataFrame:
    sku = int(sku)
    rows = _query("""
        SELECT forecast_date AS date,
               COALESCE(corrected_demand, baseline_demand) AS predicted_demand,
               baseline_demand, corrected_demand, correction_method
        FROM inventory.demand_forecast
        WHERE sku = %s AND store_id = %s AND forecast_date >= CURRENT_DATE
        ORDER BY forecast_date LIMIT %s
    """, (sku, store_id, days))
    df = pd.DataFrame(rows)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    return df
```
This is a `SELECT` only, so `_query()`'s missing-commit issue (1.6) doesn't apply here — no write, nothing to commit. Leave `get_forecast()` (the alias in the same file) as-is — with `predicted_demand` already named correctly by the query above, its rename-if-present logic becomes a harmless no-op.

### Step 4 — `app/inventory/forecasting/sensing_features.py` (new)
```python
"""Feature extraction for the demand sensing model.

Used both by the offline training pipeline (docs/inventory/scripts/
build_sensing_training_table.py) and the production job
(docs/inventory/scripts/run_sensing_job.py) — keep this the single
source of truth for what a "feature row" looks like so train/serve
never drift apart.
"""
import pandas as pd

FEATURE_COLUMNS = [
    "baseline_demand", "recent_actual_avg", "active_promo",
    "promo_discount_pct", "upcoming_promo_7d", "stockout_flag_7d",
    "day_of_week",
]


def build_feature_row(sku, store_id, forecast_date, baseline_demand,
                       sales_df, promotions_df, stock_df) -> dict:
    recent = sales_df[
        (sales_df.sku == sku) & (sales_df.store_id == store_id)
        & (sales_df.record_date >= forecast_date - pd.Timedelta(days=7))
        & (sales_df.record_date < forecast_date)
    ]
    active_promo = promotions_df[
        (promotions_df.sku == sku)
        & (promotions_df.start_date <= forecast_date)
        & (promotions_df.end_date >= forecast_date)
    ]
    upcoming_promo = promotions_df[
        (promotions_df.sku == sku)
        & (promotions_df.start_date > forecast_date)
        & (promotions_df.start_date <= forecast_date + pd.Timedelta(days=7))
    ]
    stockout_recent = stock_df[
        (stock_df.sku == sku) & (stock_df.store_id == store_id)
        & (stock_df.record_date >= forecast_date - pd.Timedelta(days=7))
        & (stock_df.record_date < forecast_date)
    ]["is_stockout"].any() if len(stock_df) else False

    return {
        "baseline_demand": baseline_demand,
        "recent_actual_avg": recent["quantity_sold"].mean() if len(recent) else 0.0,
        "active_promo": int(len(active_promo) > 0),
        "promo_discount_pct": float(active_promo["discount_pct"].max()) if len(active_promo) else 0.0,
        "upcoming_promo_7d": int(len(upcoming_promo) > 0),
        "stockout_flag_7d": int(bool(stockout_recent)),
        "day_of_week": pd.Timestamp(forecast_date).dayofweek,
    }
```

### Step 5 — `app/inventory/forecasting/sensing_model.py` (new)
```python
"""Trained correction model for the demand sensing layer."""
import lightgbm as lgb
import numpy as np
from .sensing_features import FEATURE_COLUMNS


class SensingModel:
    def __init__(self, booster: lgb.Booster | None = None):
        self.booster = booster

    def train(self, training_df, n_estimators=300, num_leaves=31, learning_rate=0.05):
        X = training_df[FEATURE_COLUMNS]
        y = training_df["actual_demand"]
        model = lgb.LGBMRegressor(
            n_estimators=n_estimators, num_leaves=num_leaves, learning_rate=learning_rate
        )
        model.fit(X, y)
        self.booster = model.booster_
        return self

    def predict(self, features: dict, baseline_demand: float) -> float:
        X = np.array([[features[c] for c in FEATURE_COLUMNS]])
        pred = float(self.booster.predict(X)[0])
        return max(0.0, min(pred, baseline_demand * 3 if baseline_demand > 0 else pred))

    def save(self, path: str):
        self.booster.save_model(path)

    @classmethod
    def load(cls, path: str) -> "SensingModel":
        return cls(booster=lgb.Booster(model_file=path))
```

### Step 6 — `app/inventory/forecasting/backfill.py` (new — import fixed, 1.1/Section 2)
```python
"""Walk-forward historical baseline generation — one-time bootstrap for
training data. Re-runs the (fixed) baseline engine as-of each historical
date using only data available before that date."""
import pandas as pd
# CONFIRMED via nodes.py's own import: the function is named `forecast` inside
# timeseries_engine.py, aliased on import. v3 guessed `ts_forecast` as the real
# name — it isn't; match nodes.py's pattern exactly:
from .timeseries_engine import forecast as ts_forecast


def backfill_baseline(sku_store_pairs, start_date, end_date, sales_df, horizon=30, step_days=7):
    results = []
    for sku, store_id in sku_store_pairs:
        for as_of_date in pd.date_range(start_date, end_date, freq=f"{step_days}D"):
            history = sales_df[
                (sales_df.sku == sku) & (sales_df.store_id == store_id)
                & (sales_df.record_date < as_of_date)
            ]
            if len(history) < 90:
                continue
            forecast = ts_forecast(history, horizon=horizon)
            for i, val in enumerate(forecast["forecast_values"]):
                results.append({
                    "sku": sku, "store_id": store_id, "as_of_date": as_of_date,
                    "forecast_date": as_of_date + pd.Timedelta(days=i + 1),
                    "baseline_demand": val,
                })
    return pd.DataFrame(results)
```

### Step 7 — `docs/inventory/scripts/backfill_baseline_forecasts.py` (new, read-only, stays sync)
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # -> back/

import pandas as pd
from app.inventory.forecasting.backfill import backfill_baseline
from app.inventory.tools.internal.stock_tools import _query

def main():
    sales_df = pd.DataFrame(_query(
        "SELECT * FROM inventory.sales_history WHERE record_date >= CURRENT_DATE - INTERVAL '18 months'"
    ))
    pairs = sales_df[["sku", "store_id"]].drop_duplicates().itertuples(index=False, name=None)
    result = backfill_baseline(list(pairs), sales_df.record_date.min(),
                                sales_df.record_date.max() - pd.Timedelta(days=30), sales_df)
    result.to_parquet("backfill_baseline.parquet")
    print(f"Backfilled {len(result)} rows")

if __name__ == "__main__":
    main()
```
**Import path**: run `print(Path(__file__).resolve())` first to confirm `.parents[3]` actually lands on `back/` in your checkout — this exact class of mistake is what broke `generate_forecast.py`. This script is read-only (`_query` used only for `SELECT`), so 1.6's commit issue doesn't apply.

### Step 8 — `docs/inventory/scripts/build_sensing_training_table.py` (new)
Loads `backfill_baseline.parquet`, joins against `sales_history`/`promotions`/`stock_history` (read via `stock_tools._query`, same as Step 7) using `sensing_features.build_feature_row()` per row plus the actual `quantity_sold` on `forecast_date` as the label, saves `training_table.parquet`. Same pattern as Step 7 — read-only, stays sync.

### Step 9 — `docs/inventory/scripts/train_sensing_model.py` (new)
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pandas as pd
from app.inventory.forecasting.sensing_model import SensingModel

def main():
    training_df = pd.read_parquet("training_table.parquet")
    model = SensingModel().train(training_df)
    out_path = "app/inventory/forecasting/models/sensing_model_v1.txt"
    model.save(out_path)
    print(f"Saved model to {out_path}")

if __name__ == "__main__":
    main()
```

### Step 10 — `docs/inventory/scripts/run_baseline_batch.py` (new, replaces `generate_forecast.py` — ASYNC, 1.7)
```python
import sys, asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pandas as pd
from app.inventory.forecasting.timeseries_engine import forecast as ts_forecast, extract_series_from_sales
from app.inventory.tools.internal.stock_tools import _query
from app.inventory.repositories.inventory_repo import InventoryRepo

async def main():
    repo = InventoryRepo()
    await repo.connect()

    sales_df = pd.DataFrame(_query(
        "SELECT * FROM inventory.sales_history WHERE record_date >= CURRENT_DATE - INTERVAL '730 days'"
    ))
    pairs = sales_df[["sku", "store_id"]].drop_duplicates().itertuples(index=False, name=None)

    for sku, store_id in pairs:
        series = extract_series_from_sales(sales_df, sku, store_id, days_back=730)
        if len(series) < 90:
            continue
        result = ts_forecast(series, horizon=30)
        for i, val in enumerate(result["forecast_values"]):
            forecast_date = pd.Timestamp.now().date() + pd.Timedelta(days=i + 1)
            await repo.insert_forecast({
                "sku": sku, "store_id": store_id, "forecast_date": forecast_date,
                "baseline_demand": float(val), "model_version": "baseline_mstl_v1",
            })

    await repo.close()

if __name__ == "__main__":
    asyncio.run(main())
```
Schedule wherever `generate_forecast.py` was supposed to run (check `docker-compose.yml`/cron — not in the files provided, verify directly). Same `days_back=730` fix as Step 0 applies here too — it's a fresh in-memory call to the same functions.

### Step 11 — `docs/inventory/scripts/run_sensing_job.py` (new — daily production job, ASYNC, 1.7/1.8)
```python
import sys, asyncio, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pandas as pd
from app.inventory.forecasting.sensing_model import SensingModel
from app.inventory.forecasting.sensing_features import build_feature_row
from app.inventory.repositories.inventory_repo import InventoryRepo
from app.inventory.tools.internal.stock_tools import _query

MODEL_PATH = "app/inventory/forecasting/models/sensing_model_v1.txt"
SENSING_HORIZON_DAYS = 14

async def main():
    model = SensingModel.load(MODEL_PATH)
    repo = InventoryRepo()
    await repo.connect()

    sales_df = pd.DataFrame(_query("SELECT * FROM inventory.sales_history WHERE record_date >= CURRENT_DATE - INTERVAL '30 days'"))
    promotions_df = pd.DataFrame(_query("SELECT * FROM inventory.promotions"))
    stock_df = pd.DataFrame(_query("SELECT * FROM inventory.stock_history WHERE record_date >= CURRENT_DATE - INTERVAL '14 days'"))
    pairs = sales_df[["sku", "store_id"]].drop_duplicates().itertuples(index=False, name=None)

    for sku, store_id in pairs:
        # get_forecast_range, not get_forecasts_for_date — see 1.8
        baseline_rows = await repo.get_forecast_range(sku, store_id, days=SENSING_HORIZON_DAYS)
        for row in baseline_rows:
            if row["baseline_demand"] is None:
                continue
            features = build_feature_row(sku, store_id, row["forecast_date"],
                                          row["baseline_demand"], sales_df, promotions_df, stock_df)
            corrected = model.predict(features, row["baseline_demand"])
            await repo.insert_forecast({
                "sku": sku, "store_id": store_id, "forecast_date": row["forecast_date"],
                "corrected_demand": corrected,
                "correction_method": "sensing_model_v1",
                "correction_features": features,
            })

    await repo.close()

if __name__ == "__main__":
    asyncio.run(main())
```
Schedule daily (or more often for faster stockout reaction).

### Step 12 — `docs/inventory/scripts/log_forecast_accuracy.py` (new, nightly)
Compares `inventory.demand_forecast` rows whose `forecast_date = CURRENT_DATE - 1` against `inventory.sales_history.quantity_sold` for that date, inserts into `inventory.forecast_accuracy`. Read via `stock_tools._query` (sync, read-only), write via `InventoryRepo` (async) — same split as everywhere else in this guide. Straightforward SQL join + insert, same shape as Steps 7/10.

---

## 6. Wiring it into `fetch_node`

Once Steps 10–11 have run at least once (so `baseline_demand`/`corrected_demand` are populated for real SKUs), the fix in **Step 3** already makes `fetch_node`'s existing fallback path (`stock_tools.get_forecast()`) read the real forecast instead of relabeled actuals — no `nodes.py` change required for that alone.

The bigger win is reordering the priority so the DB read isn't just a fallback:
```python
# nodes.py fetch_node — new priority order once the batch pipeline has run at least once:
#   1. stock_tools.get_forecast() (now reads inventory.demand_forecast, Step 3)
#   2. in-memory TimeSeriesEngine, only if the DB read is empty for this SKU/store
#      (new SKU, batch job hasn't reached it yet)
#   3. flat constant, unchanged
```
This flips today's order (in-memory first, DB fallback) so most requests become a cheap DB read instead of a live per-SKU recompute — which was the actual cost problem, per Section 2's finding. Keep the in-memory path as the safety net for the first few weeks while batch coverage ramps up.

---

## 7. Evaluation

Query `inventory.forecast_accuracy` for WMAPE of `corrected_demand` vs `baseline_demand` vs `actual_demand`, rolled up by category and store. Don't consider the sensing layer "done" until `corrected` beats `baseline`, especially in weeks containing a stockout or promo.
