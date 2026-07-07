"""
test_supply_suggestions.py
===========================
Unit tests for the agent-suggested purchase order flow added to
db/repositories/supply_repo.py (SUGGERE statut, approve/reject, stock
reception on RECU). Mocks psycopg2 entirely — no live DB required.

Run from inventory-module/:
    pytest test_supply_suggestions.py -v
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.inventory.repositories.supply_repo import (  # noqa: E402
    SyncPurchaseOrderRepo,
    PurchaseOrderTransitionError,
)


def _cursor_mock(fetchone_results):
    """A cursor whose fetchone() returns successive values from fetchone_results."""
    cur = MagicMock()
    cur.fetchone.side_effect = list(fetchone_results)
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    return cur


def _conn_mock(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


@patch("db.repositories.supply_repo.psycopg2.connect")
def test_create_suggestion_skips_if_po_already_exists(mock_connect):
    existing_cursor = _cursor_mock([{"po_id": "already-there"}])
    mock_connect.return_value = _conn_mock(existing_cursor)

    result = SyncPurchaseOrderRepo.create_suggestion_from_recommendation("reco-1")

    assert result is None


@patch("db.repositories.supply_repo.psycopg2.connect")
def test_create_suggestion_inserts_with_suggere_and_agent_source(mock_connect):
    dedup_cursor = _cursor_mock([None])
    mock_connect.side_effect = [
        _conn_mock(dedup_cursor),  # get_purchase_order_by_recommendation()
    ]

    reco_row = {
        "recommendation_id": "reco-1", "sku": "SKU123", "store_id": "I63",
        "urgency": "immediate", "confidence": 0.82, "quantity": 10,
        "unit_cost": 50.0, "lead_time_days": 14, "product_name": "Terminal X",
    }
    created_row = {"po_id": "po-1", "sku": "SKU123", "store_id": "I63", "statut": "SUGGERE"}

    main_cursor = _cursor_mock([reco_row, created_row])
    mock_connect.side_effect = None
    mock_connect.return_value = _conn_mock(main_cursor)

    with patch.object(SyncPurchaseOrderRepo, "get_purchase_order_by_recommendation", return_value=None):
        result = SyncPurchaseOrderRepo.create_suggestion_from_recommendation("reco-1", agent_run_id="run-1")

    assert result["po_id"] == "po-1"
    assert result["product_name"] == "Terminal X"

    insert_call = main_cursor.execute.call_args_list[-1]
    sql, params = insert_call.args
    assert "SUGGERE" in sql and "'AGENT'" in sql
    assert params[0] == "SKU123"  # sku
    assert "run-1" in params      # agent_decision_id


@patch("db.repositories.supply_repo.psycopg2.connect")
def test_approve_suggestion_rejects_non_suggere_state(mock_connect):
    cur = _cursor_mock([{"statut": "BROUILLON", "recommendation_id": "reco-1"}])
    mock_connect.return_value = _conn_mock(cur)

    with pytest.raises(PurchaseOrderTransitionError):
        SyncPurchaseOrderRepo.approve_suggestion("po-1", decided_by="manager1")


@patch("db.repositories.supply_repo.psycopg2.connect")
def test_approve_suggestion_flips_recommendation_and_po(mock_connect):
    select_row  = {"statut": "SUGGERE", "recommendation_id": "reco-1"}
    update_row  = {"po_id": "po-1", "statut": "BROUILLON"}
    cur = _cursor_mock([select_row, update_row])
    mock_connect.return_value = _conn_mock(cur)

    result = SyncPurchaseOrderRepo.approve_suggestion("po-1", decided_by="manager1")

    assert result["statut"] == "BROUILLON"
    executed_sql = " ".join(c.args[0] for c in cur.execute.call_args_list)
    assert "inventory.recommendations" in executed_sql
    assert "'approved'" in executed_sql or "approved" in str(cur.execute.call_args_list[1].args[1])


@patch("db.repositories.supply_repo.psycopg2.connect")
def test_reject_suggestion_flips_recommendation_and_po(mock_connect):
    select_row = {"statut": "SUGGERE", "recommendation_id": "reco-1"}
    update_row = {"po_id": "po-1", "statut": "ANNULE"}
    cur = _cursor_mock([select_row, update_row])
    mock_connect.return_value = _conn_mock(cur)

    result = SyncPurchaseOrderRepo.reject_suggestion("po-1", decided_by="manager1")

    assert result["statut"] == "ANNULE"


@patch("db.repositories.supply_repo.psycopg2.connect")
def test_update_status_to_recu_increments_stock_and_logs_movement(mock_connect):
    po_row       = {"statut": "EXPEDIE", "sku": 123, "store_id": "I63", "quantite_commandee": 20}
    updated_po   = {"po_id": "po-1", "statut": "RECU", "sku": 123, "store_id": "I63"}
    stock_row    = {"quantity": 45}

    cur = _cursor_mock([po_row, updated_po, stock_row, None])
    mock_connect.return_value = _conn_mock(cur)

    result = SyncPurchaseOrderRepo.update_status("po-1", "RECU")

    assert result["statut"] == "RECU"

    calls = cur.execute.call_args_list
    stock_update_call = next(c for c in calls if "inventory.stock_levels" in c.args[0])
    assert "123" in [str(p) for p in stock_update_call.args[1]] or 123 in stock_update_call.args[1]

    movement_insert_call = next(c for c in calls if "supply.stock_movements" in c.args[0])
    assert "RECEPTION_BC" in movement_insert_call.args[0]
