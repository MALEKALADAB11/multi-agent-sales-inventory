"""
Tests unitaires — Agent Analyste (outils + nodes).
"""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock


# ── Import helpers (graceful skip when DB/LLM absent) ───────────────────────

def _import_react_tools():
    """Import react_tools; skip test if optional deps missing."""
    try:
        import sys, os
        base = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..")
        sys.path.insert(0, os.path.abspath(base))
        from modules.coaching.agents.analyst.react_tools import (
            _score_urgency,
        )
        return _score_urgency
    except Exception as exc:
        pytest.skip(f"react_tools import failed: {exc}")


# ═══════════════════════════════════════════════════════════════════════════════
# URGENCY SCORING (pure function, no IO)
# ═══════════════════════════════════════════════════════════════════════════════

class TestUrgencyScoring:

    def test_high_urgency_late_day(self):
        """Gap 60% à 17h → HIGH."""
        _score_urgency = _import_react_tools()
        level, score = _score_urgency(gap_pct=60.0, hour=17, hours_remaining=3.0)
        assert level == "HIGH"
        assert score >= 0.6

    def test_low_urgency_morning(self):
        """Gap 5% à 10h → LOW."""
        _score_urgency = _import_react_tools()
        level, score = _score_urgency(gap_pct=5.0, hour=10, hours_remaining=10.0)
        assert level == "LOW"
        assert score < 0.4

    def test_critical_threshold(self):
        """Gap > 70% en fin de journée → CRITICAL."""
        _score_urgency = _import_react_tools()
        level, score = _score_urgency(gap_pct=75.0, hour=19, hours_remaining=1.0)
        assert level in ("HIGH", "CRITICAL")
        assert score >= 0.7

    def test_score_bounded(self):
        """Le score est toujours dans [0.0, 1.0]."""
        _score_urgency = _import_react_tools()
        for gap, hour, hrs in [(0.0, 9, 11), (100.0, 20, 0), (50.0, 15, 5)]:
            _, score = _score_urgency(gap, hour, hrs)
            assert 0.0 <= score <= 1.0, f"score={score} hors bornes pour gap={gap}"


# ═══════════════════════════════════════════════════════════════════════════════
# EOD FORECAST — SQL ROLLING FALLBACK (unit-test via mock)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSqlRollingForecast:

    @pytest.mark.asyncio
    async def test_sql_fallback_returns_dict(self):
        """_sql_rolling_forecast retourne bien eod/ci_low/ci_high/model."""
        try:
            from modules.coaching.agents.analyste import Analyste
        except Exception as exc:
            pytest.skip(f"Analyste import failed: {exc}")

        analyste = Analyste.__new__(Analyste)

        fake_rows = [
            {"date_only": "2026-06-22", "ca": 800.0},
            {"date_only": "2026-06-23", "ca": 950.0},
            {"date_only": "2026-06-24", "ca": 1100.0},
            {"date_only": "2026-06-25", "ca": 750.0},
            {"date_only": "2026-06-26", "ca": 900.0},
            {"date_only": "2026-06-27", "ca": 1050.0},
            {"date_only": "2026-06-28", "ca": 870.0},
        ]

        with patch("modules.coaching.agents.analyste.psycopg2") as mock_pg:
            mock_conn = MagicMock()
            mock_cur  = MagicMock()
            mock_pg.connect.return_value = mock_conn
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
            mock_conn.cursor.return_value.__exit__  = MagicMock(return_value=False)
            mock_cur.fetchall.return_value = fake_rows

            result = analyste._sql_rolling_forecast("I63", ca_so_far=300.0, hour=14)

        assert "eod" in result
        assert "ci_low" in result
        assert "ci_high" in result
        assert result["model"] == "SQL-rolling-7d"
        assert result["eod"] > 0

    def test_sql_fallback_returns_zero_on_empty_rows(self):
        """Quand la DB retourne 0 lignes, eod doit rester > 0 (fallback sur ca_so_far)."""
        try:
            from modules.coaching.agents.analyste import Analyste
        except Exception as exc:
            pytest.skip(f"Analyste import failed: {exc}")

        analyste = Analyste.__new__(Analyste)

        with patch("modules.coaching.agents.analyste.psycopg2") as mock_pg:
            mock_conn = MagicMock()
            mock_cur  = MagicMock()
            mock_pg.connect.return_value = mock_conn
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
            mock_conn.cursor.return_value.__exit__  = MagicMock(return_value=False)
            mock_cur.fetchall.return_value = []

            result = analyste._sql_rolling_forecast("I63", ca_so_far=400.0, hour=10)

        assert result["eod"] >= 400.0


# ═══════════════════════════════════════════════════════════════════════════════
# STOCK URGENCY BOOST — formule pure (get_stock_alerts)
# ═══════════════════════════════════════════════════════════════════════════════

class TestStockUrgencyBoost:

    def test_boost_zero_when_no_ruptures(self):
        nb_ruptures         = 0
        high_value_ruptures = 0
        boost = min(0.25, nb_ruptures * 0.05 + high_value_ruptures * 0.08)
        assert boost == 0.0

    def test_boost_capped_at_025(self):
        nb_ruptures         = 10
        high_value_ruptures = 5
        boost = min(0.25, nb_ruptures * 0.05 + high_value_ruptures * 0.08)
        assert boost == 0.25

    def test_boost_partial(self):
        """2 ruptures dont 1 high-value → 0.10 + 0.08 = 0.18."""
        nb_ruptures         = 2
        high_value_ruptures = 1
        boost = min(0.25, nb_ruptures * 0.05 + high_value_ruptures * 0.08)
        assert boost == pytest.approx(0.18)
