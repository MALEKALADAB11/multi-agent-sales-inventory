"""
Tests unitaires — HITL router.
Utilise un mock asyncpg pour ne pas nécessiter de vraie DB.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI


# ── App de test isolée ────────────────────────────────────────────────────────

def _make_app():
    app = FastAPI()
    try:
        from hitl_router import router
        app.include_router(router)
    except ImportError as e:
        pytest.skip(f"hitl_router import failed: {e}")
    return app


# ── Helpers mock asyncpg ──────────────────────────────────────────────────────

def _mock_pool(rows=None, execute_result="UPDATE 1"):
    pool = AsyncMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__  = AsyncMock(return_value=False)
    if rows is not None:
        conn.fetch    = AsyncMock(return_value=rows)
        conn.fetchrow = AsyncMock(return_value={"id": "abc-123"})
    conn.execute = AsyncMock(return_value=execute_result)
    return pool


# ═══════════════════════════════════════════════════════════════════════════════
# submit_hitl_review helper
# ═══════════════════════════════════════════════════════════════════════════════

class TestSubmitHitlReview:

    @pytest.mark.asyncio
    async def test_returns_review_id(self):
        try:
            from hitl_router import submit_hitl_review
        except ImportError as e:
            pytest.skip(str(e))

        mock_pool = _mock_pool()

        with patch("hitl_router._get_pool", return_value=mock_pool):
            result = await submit_hitl_review(
                store_id="I63",
                cycle_id="cycle-001",
                urgency_level="CRITICAL",
                gap_pct=62.0,
                critique_score=0.45,
                critique_feedback="Seulement 2 actions | RAG absent",
                strategie_summary="Gap 62% — Focus terminaux premium.",
                actions=[{"priorite": 1, "action": "Proposer iPhone", "produit_cible": "iPhone 16 Pro"}],
                source="sales",
            )
        assert result == "abc-123"

    @pytest.mark.asyncio
    async def test_returns_none_on_db_error(self):
        try:
            from hitl_router import submit_hitl_review
        except ImportError as e:
            pytest.skip(str(e))

        bad_pool = AsyncMock()
        bad_pool.acquire.side_effect = Exception("DB down")

        with patch("hitl_router._get_pool", return_value=bad_pool):
            result = await submit_hitl_review(
                store_id="I63", cycle_id="c1", urgency_level="HIGH",
                gap_pct=50.0, critique_score=0.5, critique_feedback="OK",
                strategie_summary="Test", actions=[], source="sales",
            )
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/v1/hitl/pending
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetPending:

    def test_returns_reviews_list(self):
        app = _make_app()
        fake_rows = [
            {
                "id": "rev-001", "store_id": "I63", "cycle_id": "c1",
                "urgency_level": "CRITICAL", "gap_pct": 62.0,
                "critique_score": 0.45, "critique_feedback": "NOK",
                "strategie_summary": "Gap critique", "actions": "[]",
                "source": "sales", "created_at": "2026-06-29T10:00:00",
            }
        ]

        with patch("hitl_router._get_pool", return_value=_mock_pool(rows=fake_rows)):
            client = TestClient(app)
            resp = client.get("/api/v1/hitl/pending")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["reviews"][0]["id"] == "rev-001"

    def test_empty_returns_zero(self):
        app = _make_app()

        with patch("hitl_router._get_pool", return_value=_mock_pool(rows=[])):
            client = TestClient(app)
            resp = client.get("/api/v1/hitl/pending")

        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/hitl/validate/{id}
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidateReview:

    def test_approve_ok(self):
        app = _make_app()

        with patch("hitl_router._get_pool", return_value=_mock_pool(execute_result="UPDATE 1")):
            client = TestClient(app)
            resp = client.post(
                "/api/v1/hitl/validate/rev-001",
                json={"decision": "approved", "approver_name": "Manager A"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    def test_invalid_decision_rejected(self):
        app = _make_app()

        with patch("hitl_router._get_pool", return_value=_mock_pool()):
            client = TestClient(app)
            resp = client.post(
                "/api/v1/hitl/validate/rev-001",
                json={"decision": "maybe", "approver_name": "Manager B"},
            )

        assert resp.status_code == 400

    def test_not_found_returns_404(self):
        app = _make_app()

        with patch("hitl_router._get_pool", return_value=_mock_pool(execute_result="UPDATE 0")):
            client = TestClient(app)
            resp = client.post(
                "/api/v1/hitl/validate/nonexistent",
                json={"decision": "approved", "approver_name": "Manager C"},
            )

        assert resp.status_code == 404
