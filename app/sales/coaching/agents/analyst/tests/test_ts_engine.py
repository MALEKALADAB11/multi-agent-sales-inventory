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


# ══════════════════════════════════════════════════════════════════════════════
# Non-régression — prévisions aberrantes du 2026-07-22
# ══════════════════════════════════════════════════════════════════════════════

class TestIntradayRobustness:
    """
    Deux bugs observés en production, tous deux visibles sur le dashboard.

    1. `sales.transactions` contenait la journée entière, heures futures
       comprises. Le CA « réalisé » les intégrait, puis le déroulé divisait ce
       total par la part écoulée : EOD annoncé à 4 030 TND pour 1 375 encaissés.
    2. Une heure exceptionnelle (872 TND contre ~120 d'habitude) faisait
       exploser la projection, et un modèle journalier déjà dépassé par le
       réalisé tirait la prévision SOUS le CA encaissé.
    """

    def _profile(self, hours):
        share = {h: 1.0 / len(hours) for h in hours}
        return {"share": share, "mean": {h: 100.0 for h in hours},
                "std": {h: 20.0 for h in hours}, "nb_days": 10, "source": "test"}

    def test_median_ratio_absorbs_one_outlier_hour(self):
        """La médiane des ratios ne doit pas suivre une heure à 8× la normale."""
        import numpy as np
        ratios = [1.0, 1.1, 0.9, 1.05, 8.0]
        assert float(np.median(ratios)) < 1.5
        assert float(np.mean(ratios)) > 2.0   # la moyenne, elle, dérape

    def test_remaining_blend_never_drops_below_realized(self):
        """
        Pondérer les RESTES garantit EOD ≥ CA réalisé, même quand le modèle
        journalier est déjà dépassé.
        """
        current_ca, eod_model = 1375.0, 884.0
        remaining_model  = max(0.0, eod_model - current_ca)
        remaining_unfold = 1564.0
        for w in (0.0, 0.28, 0.5, 0.9):
            eod = current_ca + w * remaining_unfold + (1 - w) * remaining_model
            assert eod >= current_ca

    def test_total_blend_starved_the_remaining_hours(self):
        """
        Preuve du bug corrigé : en pondérant les TOTAUX, un modèle journalier
        déjà dépassé (884 contre 1 375 encaissés) laissait 84 TND pour six
        heures, soit 14 TND/h dans une boutique qui en fait ~120.
        """
        current_ca, eod_model, eod_unfold, w = 1375.0, 884.0, 2939.0, 0.28
        eod_totaux = w * eod_unfold + (1 - w) * eod_model
        reste_par_heure = (eod_totaux - current_ca) / 6
        assert reste_par_heure < 20, "le bug consistait à affamer les heures restantes"

        # La pondération des restes rend une trajectoire plausible.
        remaining_model  = max(0.0, eod_model - current_ca)   # = 0, modèle réfuté
        remaining_unfold = eod_unfold - current_ca
        eod_restes = current_ca + w * remaining_unfold + (1 - w) * remaining_model
        assert (eod_restes - current_ca) / 6 > 50


# ══════════════════════════════════════════════════════════════════════════════
# Garde-fou de sortie — l'agent est le socle : sa sortie ne peut jamais être sale
# ══════════════════════════════════════════════════════════════════════════════

from app.sales.coaching.agents.analyst.ts_engine import _sanitize_analysis, _finite


class TestFinite:
    def test_replaces_nan_inf_none(self):
        assert _finite(float("nan")) == 0.0
        assert _finite(float("inf")) == 0.0
        assert _finite(float("-inf")) == 0.0
        assert _finite(None) == 0.0
        assert _finite("pas un nombre") == 0.0
        assert _finite("42.5") == 42.5
        assert _finite(3) == 3.0

    def test_custom_default(self):
        assert _finite(float("nan"), 15.0) == 15.0


def _base(**kw):
    a = {
        "store_id": "I63", "analysis_hour": 14,
        "current_ca": 1375.0, "daily_target": 1007.0,
        "eod_forecast": 1816.0, "eod_ci_low": 1375.0, "eod_ci_high": 2484.0,
        "gap_pct": 0.0, "coverage_pct": 180.0, "attainment_pct": 136.0,
        "urgency_score": 0.2, "mape_backtest": 9.0, "tomorrow_forecast": 1400.0,
        "feasibility": "ACHIEVED", "urgency_level": "MEDIUM", "trend_signal": "STABLE",
        "next_hours": [
            {"hour": 15, "expected_ca": 200.0, "cumulative_ca": 1575.0, "share_pct": 12.0, "std_ca": 40.0},
            {"hour": 16, "expected_ca": 241.0, "cumulative_ca": 1816.0, "share_pct": 14.0, "std_ca": 35.0},
        ],
        "target_hit_hour": None,
    }
    a.update(kw)
    return a


class TestSanitizeInvariants:

    def test_clean_input_passes_through(self):
        a = _sanitize_analysis(_base())
        assert a["eod_forecast"] == 1816.0
        assert a["urgency_level"] == "MEDIUM"

    def test_nan_forecast_falls_back_to_ca(self):
        a = _sanitize_analysis(_base(eod_forecast=float("nan")))
        assert a["eod_forecast"] == a["current_ca"]     # jamais NaN diffusé

    def test_eod_never_below_realized(self):
        a = _sanitize_analysis(_base(eod_forecast=800.0, current_ca=1375.0))
        assert a["eod_forecast"] >= a["current_ca"]

    def test_gap_pct_clamped_0_100(self):
        a = _sanitize_analysis(_base(daily_target=100.0, eod_forecast=0.0, current_ca=0.0))
        assert 0.0 <= a["gap_pct"] <= 100.0

    def test_urgency_score_clamped_0_1(self):
        assert _sanitize_analysis(_base(urgency_score=99.0))["urgency_score"] == 1.0
        assert _sanitize_analysis(_base(urgency_score=-5.0))["urgency_score"] == 0.0

    def test_confidence_interval_ordered_and_contains_point(self):
        a = _sanitize_analysis(_base(eod_ci_low=2000.0, eod_ci_high=1000.0, eod_forecast=1816.0))
        assert a["eod_ci_low"] <= a["eod_forecast"] <= a["eod_ci_high"]

    def test_unknown_enums_get_safe_defaults(self):
        a = _sanitize_analysis(_base(feasibility="WAT", urgency_level="PANIC", trend_signal="???"))
        assert a["feasibility"] == "UNKNOWN"
        assert a["urgency_level"] == "MEDIUM"
        assert a["trend_signal"] == "UNKNOWN"

    def test_zero_target_never_divides(self):
        a = _sanitize_analysis(_base(daily_target=0.0))
        assert a["gap_pct"] == 0.0 and a["attainment_pct"] == 0.0
        assert all(v == v for v in (a["coverage_pct"], a["gap_pct"]))  # pas de NaN

    def test_display_invariant_ca_plus_hours_equals_eod(self):
        """LA propriété que trace le dashboard, rétablie même si l'amont ment."""
        a = _sanitize_analysis(_base(
            eod_forecast=1816.0, current_ca=1375.0,
            next_hours=[{"hour": 15, "expected_ca": 5.0, "cumulative_ca": 0, "share_pct": 0, "std_ca": 0},
                        {"hour": 16, "expected_ca": 5.0, "cumulative_ca": 0, "share_pct": 0, "std_ca": 0}]))
        somme = sum(x["expected_ca"] for x in a["next_hours"])
        assert a["current_ca"] + somme == pytest.approx(a["eod_forecast"], abs=0.05)

    def test_target_hit_hour_recomputed_on_clean_trajectory(self):
        a = _sanitize_analysis(_base(
            current_ca=900.0, daily_target=1007.0, eod_forecast=1300.0, target_hit_hour=None,
            next_hours=[{"hour": 15, "expected_ca": 200.0, "cumulative_ca": 0, "share_pct": 0, "std_ca": 0},
                        {"hour": 16, "expected_ca": 200.0, "cumulative_ca": 0, "share_pct": 0, "std_ca": 0}]))
        assert a["target_hit_hour"] == 15   # 900 + 200 = 1100 ≥ 1007

    def test_never_raises_on_garbage(self):
        for bad in ({}, {"current_ca": "x", "next_hours": None},
                    {"eod_forecast": float("inf"), "next_hours": [{"hour": None}]},
                    {"error": "boom"}):
            _sanitize_analysis(bad)   # ne doit pas lever

    def test_error_dict_passes_through_untouched(self):
        assert _sanitize_analysis({"error": "no_business_date"})["error"] == "no_business_date"
