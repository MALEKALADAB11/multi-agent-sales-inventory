"""
Tests de la pipeline de prévision globale — sans base ni modèle entraîné.

Trois propriétés sont vérifiées, par ordre d'importance :

1. **Cohérence entraînement ↔ inférence.** Si `build_inference_row` ne produit
   pas exactement les features que `build_feature_frame` a servies à
   l'entraînement, le backtest reste flatteur pendant que la production
   dérive en silence. C'est le bug le plus coûteux de ce type de pipeline.
2. **Absence de fuite.** Les features d'une date ne doivent pas bouger quand on
   modifie la valeur cible de cette date.
3. **Dégradation gracieuse.** Sans modèle sur disque, la cascade doit retomber
   sur le moteur statistique sans lever.
"""
import numpy as np
import pandas as pd
import pytest

from app.sales.forecasting.backtest import compute_metrics
from app.sales.forecasting.features import (
    ALL_FEATURES, FEATURE_COLUMNS, MIN_HISTORY_DAYS, SCALE_COLUMN,
    build_feature_frame, build_inference_row,
)


def _panel(n_days: int = 200, stores=("I63", "M10"), seed: int = 7) -> pd.DataFrame:
    """Panel synthétique à saisonnalité hebdomadaire et échelles très différentes."""
    rng = np.random.default_rng(seed)
    dow_factor = np.array([0.8, 0.9, 1.0, 1.0, 1.2, 1.5, 0.6])
    rows = []
    dates = pd.date_range("2025-01-01", periods=n_days, freq="D")
    for k, store in enumerate(stores):
        base = 300.0 * (1 + 5 * k)          # I63 ≈ 300, M10 ≈ 1800
        for i, d in enumerate(dates):
            ca = base * dow_factor[d.dayofweek] * (1 + rng.normal(0, 0.08))
            rows.append({"store_id": store, "date_only": d,
                         "ca_total": max(0.0, ca), "observed": True})
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Cohérence entraînement ↔ inférence
# ══════════════════════════════════════════════════════════════════════════════

class TestTrainInferenceParity:

    def test_inference_row_matches_training_row(self):
        panel = _panel()
        feats = build_feature_frame(panel, horizons=(1,))

        store = "I63"
        grp = panel[panel["store_id"] == store].sort_values("date_only")
        target_date = grp["date_only"].iloc[-1]

        expected = feats[(feats["store_id"] == store) & (feats["date"] == target_date)]
        assert len(expected) == 1, "la date cible doit exister dans le frame d'entraînement"

        # À l'inférence, on ne connaît la série que jusqu'à la veille.
        history = grp["ca_total"].to_numpy()[:-1].tolist()
        last_date = grp["date_only"].iloc[-2]
        row, scale = build_inference_row(history, last_date, horizon=1,
                                         store_id=store,
                                         store_categories=sorted(panel["store_id"].unique()))

        assert row is not None
        assert scale == pytest.approx(float(expected[SCALE_COLUMN].iloc[0]), rel=1e-9)

        for col in FEATURE_COLUMNS:
            a = float(row[col].iloc[0])
            b = float(expected[col].iloc[0])
            if np.isnan(a) and np.isnan(b):
                continue
            assert a == pytest.approx(b, rel=1e-6, abs=1e-9), f"features divergentes sur {col}"

    def test_inference_row_has_every_model_column(self):
        panel = _panel()
        grp = panel[panel["store_id"] == "I63"].sort_values("date_only")
        row, _ = build_inference_row(grp["ca_total"].tolist(),
                                     grp["date_only"].iloc[-1], horizon=1,
                                     store_id="I63", store_categories=["I63", "M10"])
        assert list(row.columns) == list(ALL_FEATURES)

    def test_unknown_store_becomes_missing_category(self):
        """Une boutique absente de l'entraînement ne doit pas faire exploser l'inférence."""
        panel = _panel()
        grp = panel[panel["store_id"] == "I63"].sort_values("date_only")
        row, _ = build_inference_row(grp["ca_total"].tolist(),
                                     grp["date_only"].iloc[-1], horizon=1,
                                     store_id="ZZ99", store_categories=["I63", "M10"])
        assert row is not None
        assert pd.isna(row["store_cat"].iloc[0])


# ══════════════════════════════════════════════════════════════════════════════
# 2. Absence de fuite
# ══════════════════════════════════════════════════════════════════════════════

class TestNoLeakage:

    def test_features_ignore_their_own_target(self):
        panel = _panel()
        store, feats_a = "I63", build_feature_frame(_panel(), horizons=(1,))

        tampered = panel.copy()
        last_i = tampered[tampered["store_id"] == store].index[-1]
        # ×3 et non ×50 : au-delà du ratio 8 la ligne serait écartée comme
        # aberrante et le test comparerait le vide au vide.
        tampered.loc[last_i, "ca_total"] *= 3.0
        feats_b = build_feature_frame(tampered, horizons=(1,))

        target_date = panel[panel["store_id"] == store]["date_only"].iloc[-1]
        a = feats_a[(feats_a["store_id"] == store) & (feats_a["date"] == target_date)]
        b = feats_b[(feats_b["store_id"] == store) & (feats_b["date"] == target_date)]

        for col in FEATURE_COLUMNS:
            va, vb = float(a[col].iloc[0]), float(b[col].iloc[0])
            if np.isnan(va) and np.isnan(vb):
                continue
            assert va == pytest.approx(vb, rel=1e-9), f"{col} a bougé avec la cible → fuite"

    def test_horizon_2_uses_older_observations_than_horizon_1(self):
        feats = build_feature_frame(_panel(), horizons=(1, 2))
        store = "I63"
        date = feats[feats["store_id"] == store]["date"].max()
        h1 = feats[(feats["store_id"] == store) & (feats["date"] == date) & (feats["horizon"] == 1)]
        h2 = feats[(feats["store_id"] == store) & (feats["date"] == date) & (feats["horizon"] == 2)]
        assert len(h1) == 1 and len(h2) == 1
        assert float(h1["lag_1"].iloc[0]) != pytest.approx(float(h2["lag_1"].iloc[0]))


# ══════════════════════════════════════════════════════════════════════════════
# 3. Robustesse et repli
# ══════════════════════════════════════════════════════════════════════════════

class TestGracefulDegradation:

    def test_short_history_returns_none(self):
        short = [100.0] * (MIN_HISTORY_DAYS - 1)
        row, scale = build_inference_row(short, pd.Timestamp("2026-01-31"))
        assert row is None and scale == 0.0

    def test_flat_zero_series_returns_none(self):
        """Une boutique à l'arrêt donne une échelle nulle : ratio impossible."""
        row, scale = build_inference_row([0.0] * 120, pd.Timestamp("2026-01-31"))
        assert row is None

    def test_forecast_daily_series_without_model_uses_statistics(self):
        from app.sales.coaching.agents.analyst.ts_engine import forecast_daily_series
        rng = np.random.default_rng(3)
        series = [800 + 200 * np.sin(i / 7 * 2 * np.pi) + rng.normal(0, 30) for i in range(150)]
        out = forecast_daily_series(series, horizon=2)   # ni last_date ni store_id
        assert out["engine"] in ("holt_winters_seasonal7", "statsforecast_autoets")
        assert len(out["forecast"]) == 2

    def test_scale_is_store_specific(self):
        """Deux boutiques d'échelles opposées doivent produire des ratios comparables."""
        feats = build_feature_frame(_panel(), horizons=(1,))
        medians = feats.groupby("store_id")["ratio"].median()
        assert medians.max() / medians.min() < 1.5, "la normalisation n'égalise pas les régimes"


# ══════════════════════════════════════════════════════════════════════════════
# 4. Métriques
# ══════════════════════════════════════════════════════════════════════════════

class TestMetrics:

    def test_wape_is_sum_abs_error_over_sum_actual(self):
        m = compute_metrics(np.array([100.0, 200.0]), np.array([110.0, 180.0]))
        assert m["wape"] == pytest.approx(10.0)      # 30 / 300
        assert m["mae"] == pytest.approx(15.0)

    def test_bias_sign_is_meaningful(self):
        low = compute_metrics(np.array([100.0, 100.0]), np.array([80.0, 80.0]))
        assert low["bias"] < 0, "une sous-prévision doit produire un biais négatif"

    def test_mase_against_naive(self):
        m = compute_metrics(np.array([100.0, 100.0]), np.array([110.0, 90.0]), naive_mae=20.0)
        assert m["mase"] == pytest.approx(0.5)

    def test_empty_input_is_safe(self):
        assert compute_metrics(np.array([]), np.array([]))["n"] == 0
