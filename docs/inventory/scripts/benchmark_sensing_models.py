"""Benchmark candidate correction models for the demand sensing layer.

Deliberately NOT sensing_model.py -- per the next-steps guide, don't
commit to one model class (currently hardcoded to LGBMRegressor there)
while still comparing candidates. This script is read-only with respect
to sensing_model.py; nothing here changes production until you've picked
a winner and manually swap the model class in SensingModel.train().

Usage:
    python benchmark_sensing_models.py

Reads:  training_table.parquet   (built by build_sensing_training_table.py)
Writes: benchmark_results.csv    (appended to, one row per model per run --
                                   so you can track whether new features,
                                   e.g. events/weather, actually helped)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # -> back/

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
import lightgbm as lgb
import xgboost as xgb

from app.inventory.forecasting.sensing_features import FEATURE_COLUMNS

DATA_DIR = Path(__file__).resolve().parent / "data"  # docs/inventory/scripts/data
RESULTS_PATH = DATA_DIR / "benchmark_results.csv"
RANDOM_STATE = 42  # fixed so train/test split is reproducible across runs
TEST_SIZE = 0.2


def _mape(y_true, y_pred):
    """Mean absolute percentage error, guarding against actual_demand == 0.

    MAPE blows up (or is undefined) for near-zero actuals, which is common
    for slow sku/store pairs -- per the guide, treat this as a secondary
    metric and sanity-check it against MAE rather than trusting it alone.
    Rows with actual_demand == 0 are excluded from the MAPE calc (they're
    already fully captured by MAE/RMSE) and the excluded count is reported.
    """
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    nonzero = y_true != 0
    n_excluded = (~nonzero).sum()
    if nonzero.sum() == 0:
        return float("nan"), n_excluded
    mape = float(np.mean(np.abs((y_true[nonzero] - y_pred[nonzero]) / y_true[nonzero])) * 100)
    return mape, n_excluded


def _score(y_true, y_pred, label):
    mae = mean_absolute_error(y_true, y_pred)
    rmse = mean_squared_error(y_true, y_pred) ** 0.5
    mape, n_excluded = _mape(y_true, y_pred)
    print(f"  {label:<22} MAE={mae:8.3f}  RMSE={rmse:8.3f}  "
          f"MAPE={mape:6.2f}%  (excluded {n_excluded} zero-actual rows from MAPE)")
    return mae, rmse, mape


def main():
    training_df = pd.read_parquet(DATA_DIR / "training_table.parquet")
    print(f"Loaded {len(training_df)} training rows")

    X = training_df[FEATURE_COLUMNS]
    y = training_df["actual_demand"]
    baseline_pred_all = training_df["baseline_demand"]

    X_train, X_test, y_train, y_test, baseline_train, baseline_test = train_test_split(
        X, y, baseline_pred_all, test_size=TEST_SIZE, random_state=RANDOM_STATE
    )
    print(f"Train: {len(X_train)} rows / Test: {len(X_test)} rows "
          f"(random_state={RANDOM_STATE}, test_size={TEST_SIZE})\n")

    candidates = {
        "lightgbm": lgb.LGBMRegressor(
            n_estimators=300, num_leaves=31, learning_rate=0.05, random_state=RANDOM_STATE
        ),
        "xgboost": xgb.XGBRegressor(
            n_estimators=300, max_depth=6, learning_rate=0.05, random_state=RANDOM_STATE
        ),
        "randomforest": RandomForestRegressor(
            n_estimators=300, random_state=RANDOM_STATE, n_jobs=-1
        ),
    }

    trained_at = pd.Timestamp.now().isoformat()
    results = []

    print("Results (held-out test set):")
    baseline_mae, baseline_rmse, baseline_mape = _score(y_test, baseline_test, "baseline (no correction)")
    results.append({
        "model_name": "baseline_no_correction", "mae": baseline_mae, "rmse": baseline_rmse,
        "mape": baseline_mape, "trained_at": trained_at,
        "n_train_rows": len(X_train), "n_test_rows": len(X_test),
    })

    for name, model in candidates.items():
        model.fit(X_train, y_train)
        y_pred = model.predict(X_test)
        mae, rmse, mape = _score(y_test, y_pred, name)
        results.append({
            "model_name": name, "mae": mae, "rmse": rmse, "mape": mape,
            "trained_at": trained_at, "n_train_rows": len(X_train), "n_test_rows": len(X_test),
        })

    results_df = pd.DataFrame(results)

    # Decision-rule sanity check, printed but not enforced -- you make the
    # call, this just flags it loudly if something looks off before you do.
    print()
    for row in results_df.itertuples():
        if row.model_name == "baseline_no_correction":
            continue
        if row.mae >= baseline_mae:
            print(f"  WARNING: {row.model_name} MAE ({row.mae:.3f}) does not beat "
                  f"raw baseline ({baseline_mae:.3f}) -- investigate before treating it "
                  f"as a candidate winner.")

    best = results_df[results_df.model_name != "baseline_no_correction"].sort_values("mae").iloc[0]
    print(f"\nLowest test MAE: {best.model_name} ({best.mae:.3f}, "
          f"vs baseline {baseline_mae:.3f})")

    # Append (not overwrite) so results are comparable across runs -- e.g.
    # re-run after adding events/weather features to see if they helped.
    if Path(RESULTS_PATH).exists():
        existing = pd.read_csv(RESULTS_PATH)
        results_df = pd.concat([existing, results_df], ignore_index=True)
    results_df.to_csv(RESULTS_PATH, index=False)
    print(f"\nAppended {len(results)} rows to {RESULTS_PATH}")


if __name__ == "__main__":
    main()