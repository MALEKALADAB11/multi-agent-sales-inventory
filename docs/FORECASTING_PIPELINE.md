# Demand Forecasting Pipeline

This document explains the full demand sensing / forecasting pipeline: what
each file does, what's already been run (so you don't repeat multi-hour
jobs by accident), and how the daily/every-2-days automation is set up.

If you're pulling this repo for the first time: **read the "One-time steps
— already done" section before running anything.** Some of these scripts
take hours and their output is already committed/available; rerunning them
is wasted time and, in one case, actively wrong (it would overwrite a
model comparison result).

---

## How it fits together

```
sales.transactions (live, per-sale)
        |
        v  [daily]
sync_sales_history_from_transactions.py
        |
        v
inventory.sales_history (daily aggregate: sku, store, day -> quantity_sold)
        |
        v  [every 2 days]
run_baseline_batch.py  ---------->  inventory.demand_forecast.baseline_demand
        |                            (30 days out, MSTL time-series model)
        |
        v  [daily]
run_sensing_job.py  -------------->  inventory.demand_forecast.corrected_demand
   (loads sensing_model_v1.ubj,        (7 days out, XGBoost correction on
    reads recent sales/promos/            top of the baseline)
    events/weather)
        |
        v  [daily, from day 2 onward]
log_forecast_accuracy.py  -------->  inventory.forecast_accuracy
   (scores yesterday's forecast vs yesterday's actual sales)
```

Two separate models are involved:
- **Baseline model**: MSTL time-series forecast (`timeseries_engine.py`),
  trained implicitly per-pair at run time from up to 730 days of history.
  Learns seasonality (day-of-week, month, etc.) per sku/store pair.
- **Sensing/correction model**: a trained XGBoost model
  (`sensing_model.py`) that adjusts the baseline using short-term signals
  the baseline can't see — recent actual sales, active promotions,
  upcoming events, weather. Trained once offline, then loaded and reused
  every day by `run_sensing_job.py`.

---

## File reference

### Data seeding / demo (not part of the live pipeline)
- **`generate_multistore_data.py`** — synthetic data generator for demo
  stores (`M22`, `M15`, `M18`, `I14`). Invents plausible transactions using
  hardcoded seasonality/demand tables — **not real sales data**. Not
  scheduled, not part of the daily pipeline chain, but see caveat below.

  **Important caveat:** this script writes a *fixed* window of transactions
  (`end_date = date.today()` at the moment you run it, going back
  `--days`) — it does NOT keep producing new "today" transactions on its
  own. It's the only thing that has ever put rows into `sales.transactions`
  for these 4 stores (unlike `I63`/`M10`, which has real, continuously
  arriving POS data). So once you stop rerunning this, those 4 stores stop
  getting new transaction rows — `sync_sales_history_from_transactions.py`
  will keep running without errors, but will just be re-aggregating the
  same aging, static data every day until it eventually falls outside the
  sync/sensing lookback windows and those stores go stale again. If you
  want the demo stores to keep looking "live" indefinitely, rerun this
  periodically with a small `--days`; if they're just throwaway test data,
  no action needed — it's expected they'll eventually stop updating.

### Output data folder
All pipeline-generated data files (`backfill_baseline.parquet`,
`training_table.parquet`, `benchmark_results.csv`, `weather_cache.parquet`,
`weather_impute_medians.json`) live in **`docs/inventory/scripts/data/`**
— not the repo root, and not wherever a script happens to be run from.

Each script computes this path itself via
`Path(__file__).resolve().parent / "data"`, anchored to the script's own
file location rather than the process's current working directory. This
matters because these scripts get launched from different places (by hand
from the repo root, vs. Task Scheduler using
`Start in: docs/inventory/scripts`) — a plain relative path like
`"training_table.parquet"` would resolve to a different actual folder
depending on which one launched it. Anchoring to `__file__` makes it
resolve to the same `docs/inventory/scripts/data/` folder either way.

`sensing_model_v1.ubj` is the one exception — it stays in
`app/inventory/forecasting/models/`, not `docs/inventory/scripts/data/`,
since that's where `sensing_model.py`/`train_sensing_model.py` already
expect it and where the rest of the `app/` code looks for it.

### One-time training pipeline (offline, produces the trained model)
- **backfill script** → produces `backfill_baseline.parquet`. **Already
  run — took ~19 hours. Do not rerun.** The output parquet file is what
  matters; if you have it, you're done with this step.
- **`build_sensing_training_table.py`** — turns `backfill_baseline.parquet`
  into `training_table.parquet` (adds features via `sensing_features.py`).
  Already run once.
- **`benchmark_sensing_models.py`** — compares candidate algorithms on
  `training_table.parquet`. Already run — **result: XGBoost beat
  LightGBM**, which is why `sensing_model.py` uses XGBoost. If you rerun
  this and get a different winner, `sensing_model.py`'s `train()` method
  needs to be updated to match — it does NOT auto-detect the winner.
- **`train_sensing_model.py`** — trains `SensingModel` on
  `training_table.parquet`, saves `sensing_model_v1.ubj`. Already run.
  Rerun this only if you want to retrain (e.g. new training data, or after
  changing `sensing_model.py`/`sensing_features.py`). Not scheduled —
  retraining is a deliberate manual decision, not automatic.

### Library code (imported by the scripts below, never run directly)
- **`sensing_model.py`** — the `SensingModel` class: `train()`,
  `predict()`, `save()`, `load()`. Wraps XGBoost. `predict()` clamps
  output to `[0, 3x baseline_demand]` as a sanity bound.
- **`sensing_features.py`** — `build_feature_row()`, the single source of
  truth for what a "feature row" looks like. Used by both the offline
  training table builder and the live `run_sensing_job.py` — keeping
  train and serve on the same feature logic is the whole point of this
  file existing separately.

### Live pipeline scripts (scheduled — see schedule below)
- **`sync_sales_history_from_transactions.py`** — aggregates
  `sales.transactions` (live, per-transaction) into `inventory.sales_history`
  (daily aggregate) so the rest of the pipeline has current data.
  `sales_history` was originally seeded once from a 3-year CSV
  (2023-01-01 → 2026-03-01) for training purposes only — it was never
  meant to stay current on its own, hence this sync job. Safe to rerun
  (replaces its own sync window, never touches the CSV-seeded history).
  Supports `--days N` for a wider one-time backfill if a gap ever opens up
  again; daily runs use the default window with no arguments.
- **`run_baseline_batch.py`** — MSTL forecast, 30 days out, per sku/store
  pair with ≥90 days of sales history. Writes `baseline_demand`.
  `TOP_N_PAIRS` + `MUST_INCLUDE_STORES` control which pairs run each time
  (see "Config knobs" below) — writes incrementally per pair, so a crash
  mid-run only loses in-flight pairs, not the whole run.
- **`run_sensing_job.py`** — loads `sensing_model_v1.ubj`, corrects the
  next 7 days of baseline forecasts using recent sales/promo/event/weather
  features. Writes `corrected_demand`.
- **`log_forecast_accuracy.py`** — compares yesterday's forecast
  (baseline + corrected) against yesterday's actual sales, logs error to
  `inventory.forecast_accuracy`. First useful run is the day *after*
  forecast history starts existing — a "nothing to score" message on day 1
  is expected, not broken.

---

## Config knobs worth knowing about

**`run_baseline_batch.py`:**
```python
TOP_N_PAIRS = 500          # None = full run (~4,262 pairs, hours).
                            # 500 = partial run, much faster. Adjust based
                            # on how much time you have.
MUST_INCLUDE_STORES = {"I14", "M15", "M18", "M22"}
                            # Guarantees these stores get a baseline slot
                            # regardless of sales volume ranking. Without
                            # this, low-volume/newer stores can get
                            # silently excluded from TOP_N_PAIRS cuts —
                            # which then means run_sensing_job.py has
                            # nothing to correct for them (this happened
                            # once, hence the explicit guarantee).
```
A pair still needs ≥90 days of sales history to get a baseline forecast
even if it's in `MUST_INCLUDE_STORES` — that filter lives inside
`_process_one_pair` and can't be bypassed by the pair-selection config.

**`run_sensing_job.py`:**
```python
SENSING_HORIZON_DAYS = 7   # Keep this at 7 (or lower) — beyond ~7 days
                            # out, "recent" features (recent_actual_avg,
                            # stockout_flag_7d) would be looking at a
                            # future window with no real data yet.
```

---

## Scheduled tasks (Windows Task Scheduler)

All 4 tasks below use the same pattern:
- **Program/script:** `cmd.exe`
- **Start in:** `C:\Users\amani\Documents\multi-agent-sales-inventory\docs\inventory\scripts`
- **Add arguments:** `/c "...\venv\Scripts\python.exe" SCRIPT.py > logs\LOG.log 2>&1`
  (must use the venv's `python.exe`, full path — not bare `python`, and
  must go through `cmd.exe` for the `>` log redirect to actually work)
- **Settings tab:** ✅ "Run task as soon as possible after a scheduled
  start is missed" (laptop being off at trigger time is expected/common;
  this makes it catch up instead of silently skipping the day)

Make sure a `logs\` folder exists in the scripts directory before the
first run, or the redirect fails silently.

| Task name | Script | Time | Recurrence | Depends on |
|---|---|---|---|---|
| Sales History Sync | `sync_sales_history_from_transactions.py` | 1:00 PM | Daily | — |
| Baseline Forecast Batch | `run_baseline_batch.py` | 1:20 PM | **Every 2 days** | Sales History Sync |
| Sensing Job | `run_sensing_job.py` | 2:20 PM | Daily | Baseline Forecast Batch |
| Forecast Accuracy Log | `log_forecast_accuracy.py` | 2:40 PM | Daily | Sensing Job (previous day's) |

**Why Sensing is a full hour after Baseline, not 30 min:** actual
`run_baseline_batch.py` runtime isn't confirmed and will grow over time as
more days sync into `sales_history` and more pairs clear the 90-day
history threshold. If Sensing starts before Baseline finishes, it doesn't
crash — it just silently corrects fewer pairs than it should that day
(only pairs Baseline had already reached get a fresh forecast to correct).
A generous 1-hour gap avoids that until real runtimes are known. To tune
this down later: check `logs\baseline.log`'s modified time relative to
1:20 PM over a few runs, take the worst case, add ~50% buffer, and narrow
the gap accordingly.

**It's fine to turn on Forecast Accuracy Log from day one**, even though
it won't find anything to score until the following day — it just prints
"No forecasts to score for yesterday" and exits cleanly, no error.

**Why Baseline is every 2 days, not daily:** each run produces 30 days of
forecast, and `run_sensing_job.py` only ever needs the next 7 of those —
so daily reruns aren't required for correctness. The real ceiling is
about every ~23 days (below that, the 30-day forecast horizon starts
running out and sensing has fewer future days to correct). Every 2 days
is a middle ground: keeps forecasts reasonably fresh without the
CPU-heavy baseline run interrupting daily work.

**Why the others stay daily:** sync and sensing both depend on "last N
days being current" every single day — there's no equivalent slack.
Accuracy logging scores yesterday specifically, so it has to run daily
too, once forecast history exists.

To change Baseline's recurrence: task Properties → **Triggers tab** →
edit the trigger → the Daily trigger type has a **"Recur every: __ days"**
field — set to `2`.

---

## Quick health checks

Baseline coverage today:
```sql
SELECT COUNT(*) AS total_rows, COUNT(DISTINCT (sku, store_id)) AS pairs_done
FROM inventory.demand_forecast
WHERE baseline_generated_at::date = CURRENT_DATE;
```

Corrected forecasts today:
```sql
SELECT COUNT(*) AS corrected_rows, COUNT(DISTINCT (sku, store_id)) AS pairs_corrected
FROM inventory.demand_forecast
WHERE corrected_generated_at::date = CURRENT_DATE;
```

Is `sales_history` staying current (should track ~today, not fall behind):
```sql
SELECT MAX(record_date) AS latest_synced_day FROM inventory.sales_history;
```
