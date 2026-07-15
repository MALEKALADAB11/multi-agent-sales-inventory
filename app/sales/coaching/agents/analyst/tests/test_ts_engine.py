"""Tests unitaires du moteur séries temporelles de l'Analyste (sans DB)."""

import numpy as np
import pytest

from app.sales.coaching.agents.analyst.ts_engine import (
    _backtest_mape,
    _classify_hour,
    _cum_share,
    _hw_gridsearch,
    _trend_signal,
    forecast_daily_series,
)


def _seasonal_series(n=120, base=1000.0, noise=30.0, seed=42):
    """Série journalière synthétique avec saisonnalité hebdo marquée."""
    rng = np.random.default_rng(seed)
    dow_factor = np.array([0.8, 0.9, 1.0, 1.0, 1.2, 1.5, 0.6])  # semaine type
    return [
        base * dow_factor[i % 7] + rng.normal(0, noise)
        for i in range(n)
    ]


class TestHoltWinters:
    def test_forecast_captures_weekly_seasonality(self):
        series = _seasonal_series()
        out = forecast_daily_series(series, horizon=7)
        assert out["engine"] in ("holt_winters_seasonal7", "statsforecast_autoets")
        fc = out["forecast"]
        assert len(fc) == 7
        # Le jour fort (facteur 1.5) doit rester nettement > jour faible (0.6)
        assert max(fc) > 1.5 * min(fc)

    def test_backtest_wape_low_on_clean_seasonal_series(self):
        y = np.asarray(_seasonal_series(noise=20.0), dtype=float)
        params = _hw_gridsearch(y, period=7)
        wape = _backtest_mape(y, 7, params)
        assert wape < 12.0, f"WAPE {wape}% trop élevé sur série propre"

    def test_forecast_non_negative(self):
        out = forecast_daily_series([5, 0, 3, 0, 1, 0, 0] * 10, horizon=5)
        assert all(v >= 0 for v in out["forecast"])
        assert all(v >= 0 for v in out["ci_low"])

    def test_short_history_fallback(self):
        out = forecast_daily_series([100.0] * 10, horizon=2)
        assert out["engine"] == "mean_fallback"
        assert out["forecast"][0] == pytest.approx(100.0)

    def test_ci_contains_point_forecast(self):
        out = forecast_daily_series(_seasonal_series(), horizon=3)
        for lo, pt, hi in zip(out["ci_low"], out["forecast"], out["ci_high"]):
            assert lo <= pt <= hi


class TestHourlyGapDetection:
    def test_classify_alert_on_deep_deviation(self):
        assert _classify_hour(-0.5, 0.0) == "ALERT"
        assert _classify_hour(0.0, -2.5) == "ALERT"

    def test_classify_watch_on_moderate_deviation(self):
        assert _classify_hour(-0.25, -0.5) == "WATCH"

    def test_classify_ok_and_over(self):
        assert _classify_hour(0.05, 0.2) == "OK"
        assert _classify_hour(0.5, 2.5) == "OVER"

    def test_cum_share_floor(self):
        assert _cum_share({8: 0.0, 9: 0.0}, 9) == pytest.approx(0.02)
        assert _cum_share({8: 0.1, 9: 0.2, 15: 0.7}, 9) == pytest.approx(0.3)


class TestTrendSignal:
    def _entry(self, h, expected, actual):
        return {"hour": h, "expected": expected, "actual": actual}

    def test_accelerating(self):
        ledger = [self._entry(h, 100, a) for h, a in [(10, 80), (11, 100), (12, 130)]]
        assert _trend_signal(ledger) == "ACCELERATING"

    def test_decelerating(self):
        ledger = [self._entry(h, 100, a) for h, a in [(10, 130), (11, 100), (12, 70)]]
        assert _trend_signal(ledger) == "DECELERATING"

    def test_stable(self):
        ledger = [self._entry(h, 100, a) for h, a in [(10, 100), (11, 105), (12, 98)]]
        assert _trend_signal(ledger) == "STABLE"

    def test_unknown_when_insufficient(self):
        assert _trend_signal([self._entry(10, 100, 90)]) == "UNKNOWN"
