"""
Tests unitaires de l'Agent Analyste.
"""
import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime

from sales_module.core.agents.analyst.tools import (
    compute_gap_metrics,
    compute_urgency,
    summarize_pos_history,
)
from sales_module.core.agents.analyst.nodes import (
    node_compute_gap,
    node_detect_urgency,
)


# ─── Tests outils ───

def test_compute_gap_metrics_high():
    result = compute_gap_metrics(
        current_revenue=5000.0,
        daily_target=10000.0,
        forecast_end_of_day=7000.0,
    )
    assert result["gap_percentage"] == pytest.approx(50.0)
    assert result["gap_amount"] == pytest.approx(5000.0)
    assert result["forecast_gap_coverage_pct"] == pytest.approx(40.0)
    assert not result["forecast_covers_gap"]


def test_compute_gap_metrics_achieved():
    result = compute_gap_metrics(
        current_revenue=10000.0,
        daily_target=10000.0,
        forecast_end_of_day=10500.0,
    )
    assert result["gap_percentage"] == 0.0
    assert result["forecast_covers_gap"] is True


def test_urgency_high():
    level, score = compute_urgency(
        gap_pct=45.0,
        coverage_pct=30.0,
        current_hour=17,
        hours_remaining=3.0,
    )
    assert level == "HIGH"
    assert score > 0.5


def test_urgency_low():
    level, score = compute_urgency(
        gap_pct=5.0,
        coverage_pct=95.0,
        current_hour=10,
        hours_remaining=10.0,
    )
    assert level == "LOW"


def test_summarize_pos_history_empty():
    result = summarize_pos_history([])
    assert result["total_revenue"] == 0.0
    assert result["total_transactions"] == 0


def test_summarize_pos_history():
    history = [
        {"revenue": 100.0, "minutes_ago": 30, "transaction_time": datetime.now()},
        {"revenue": 200.0, "minutes_ago": 70, "transaction_time": datetime.now()},
        {"revenue": 150.0, "minutes_ago": 10, "transaction_time": datetime.now()},
    ]
    result = summarize_pos_history(history)
    assert result["total_revenue"] == pytest.approx(450.0)
    assert result["total_transactions"] == 3
    assert result["revenue_last_hour"] == pytest.approx(250.0)  # 30min + 10min


# ─── Tests nodes (async) ───

@pytest.mark.asyncio
async def test_node_compute_gap():
    state = {
        "pos_data": {"store_id": "S001", "daily_target": 10000.0},
        "pos_history": [
            {"revenue": 1000.0, "minutes_ago": 60, "transaction_time": datetime.now()},
            {"revenue": 2000.0, "minutes_ago": 120, "transaction_time": datetime.now()},
        ],
    }
    result = await node_compute_gap(state)
    assert "gap_objectif" in result
    assert "gap_amount" in result
    assert result["gap_objectif"] == pytest.approx(70.0)


@pytest.mark.asyncio
async def test_node_detect_urgency_high():
    state = {
        "pos_data": {
            "store_id": "S001",
            "daily_target": 10000.0,
            "current_revenue": 3000.0,
            "closing_hour": 20,
        },
        "timesvm_prediction": {
            "forecast_end_of_day": 6000.0,
        },
        "gap_objectif": 70.0,
        "gap_amount": 7000.0,
    }
    result = await node_detect_urgency(state)
    assert result["urgency_level"] == "HIGH"
    assert result["urgency_score"] > 0.5
