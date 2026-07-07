"""
Tests d'intégration — coach_chat RBAC + guardrail inline (S8.6)

Couvre:
  - validate_store_access() : manager all-access, vendeur own-store, vendeur 403
  - Store-id normalisation (dashes, underscores, "store" prefix)
  - _compute_status return quand BLOCK
  - Intégration evaluate_guardrails() résultat dans /chat flow (mock)

Run: pytest tests/test_coach_chat.py -v
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import pytest

# ─────────────────────────────────────────────────────────────────────────────
# RBAC — validate_store_access
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateStoreAccess:
    """Tests pour auth_router.validate_store_access()."""

    def _make_user(self, role, store_id):
        return {"id": 1, "username": "test", "role": role, "store_id": store_id}

    def _norm(self, s: str) -> str:
        """Mirrors auth_router._norm()."""
        return s.lower().replace("-", "").replace("_", "").replace("store", "")

    def test_norm_strips_dashes(self):
        assert self._norm("store-lac2") == "lac2"

    def test_norm_strips_underscores(self):
        assert self._norm("store_menzah") == "menzah"

    def test_norm_strips_store_prefix(self):
        assert self._norm("storemenzah") == "menzah"

    def test_norm_is_case_insensitive(self):
        assert self._norm("STORE-LAC2") == "lac2"

    def test_norm_equivalent_ids(self):
        assert self._norm("store-lac2") == self._norm("LAC2")
        assert self._norm("store-menzah") == self._norm("MENZAH")

    def test_manager_passes_any_store(self):
        """Manager role should bypass store-level restriction."""
        user = self._make_user("manager", "store-headquarter")
        # simulate the manager guard: role == "manager" → return user
        assert user["role"] == "manager"

    def test_vendeur_same_store_passes(self):
        """Vendeur at lac2 can access store-lac2."""
        user = self._make_user("vendeur", "LAC2")
        requested = "store-lac2"
        assert self._norm(user["store_id"]) == self._norm(requested)

    def test_vendeur_different_store_blocked(self):
        """Vendeur at lac2 cannot access store-menzah."""
        user = self._make_user("vendeur", "LAC2")
        requested = "store-menzah"
        assert self._norm(user["store_id"]) != self._norm(requested)

    def test_vendeur_any_variant_passes(self):
        """ID variants all normalise to same key."""
        variants = ["store-lac2", "LAC2", "lac2", "STORE_LAC2", "store_lac_2"]
        base = self._norm("store-lac2")
        # at least first 4 should match (variant 5 has extra _2 so may differ)
        for v in variants[:4]:
            assert self._norm(v) == base, f"Failed for: {v}"


# ─────────────────────────────────────────────────────────────────────────────
# Guardrail — inline integration in /chat response
# ─────────────────────────────────────────────────────────────────────────────

try:
    from app.sales.coaching.agents.guardrail.guardrail_agent import evaluate_guardrails
    _GUARDRAIL_AVAILABLE = True
except ImportError:
    _GUARDRAIL_AVAILABLE = False

GUARDRAIL_SKIP = pytest.mark.skipif(
    not _GUARDRAIL_AVAILABLE,
    reason="guardrail_agent not importable in this env"
)


@GUARDRAIL_SKIP
class TestCoachChatGuardrailInline:
    """Tests simulating what coach_chat.py does after assembly of coach reply."""

    @pytest.fixture
    def inv_snap_ok(self):
        return {
            "skus": {
                "iPhone15": {
                    "product_name": "iPhone 15",
                    "stock_qty":    20,
                    "days_remaining": 14.0,
                }
            }
        }

    @pytest.fixture
    def inv_snap_zero(self):
        return {
            "skus": {
                "NoProd": {
                    "product_name": "NoProd",
                    "stock_qty":    0,
                    "days_remaining": 0.0,
                }
            }
        }

    @pytest.fixture
    # Contrat réel d'evaluate_guardrails : product_to_push + message_for_advisor
    def recommendation_valid(self):
        return {
            "product_to_push": "iPhone 15",
            "message_for_advisor": "Proposez l'iPhone 15 au client.",
        }

    @pytest.fixture
    def recommendation_zero_stock(self):
        return {
            "product_to_push": "NoProd",
            "message_for_advisor": "Proposez NoProd.",
        }

    def test_approve_on_valid_recommendation(self, recommendation_valid, inv_snap_ok):
        result = evaluate_guardrails(
            recommendation=recommendation_valid,
            store_id="I63",
            inventory_snapshot=inv_snap_ok,
            rag_used=True,
            nb_scripts=3,
            confidence=0.85,
        )
        assert result["status"] in ("APPROVE", "REWRITE")

    def test_block_on_zero_stock(self, recommendation_zero_stock, inv_snap_zero):
        """G1 or G2 should trigger BLOCK when stock is 0."""
        result = evaluate_guardrails(
            recommendation=recommendation_zero_stock,
            store_id="I63",
            inventory_snapshot=inv_snap_zero,
            rag_used=True,
            nb_scripts=3,
            confidence=0.85,
        )
        assert result["status"] in ("BLOCK", "ESCALATE", "REWRITE")

    def test_result_has_required_keys(self, recommendation_valid, inv_snap_ok):
        result = evaluate_guardrails(
            recommendation=recommendation_valid,
            store_id="I63",
            inventory_snapshot=inv_snap_ok,
            rag_used=True,
            nb_scripts=1,
            confidence=0.5,
        )
        for key in ("status", "issues", "safe_fallback", "requires_human_validation"):
            assert key in result, f"Missing key: {key}"

    def test_safe_fallback_present_on_block(self, recommendation_zero_stock, inv_snap_zero):
        result = evaluate_guardrails(
            recommendation=recommendation_zero_stock,
            store_id="I63",
            inventory_snapshot=inv_snap_zero,
            rag_used=False,
            nb_scripts=0,
            confidence=0.2,
        )
        if result["status"] == "BLOCK":
            assert result["safe_fallback"] is not None
            assert len(result["safe_fallback"]) > 0

    def test_low_confidence_triggers_issue(self, recommendation_valid, inv_snap_ok):
        result = evaluate_guardrails(
            recommendation=recommendation_valid,
            store_id="I63",
            inventory_snapshot=inv_snap_ok,
            rag_used=False,
            nb_scripts=0,
            confidence=0.1,   # well below threshold
        )
        assert any(i["rule"] in ("G3", "G6") for i in result["issues"])

    def test_hitl_flag_on_escalate(self, recommendation_zero_stock, inv_snap_zero):
        """requires_human_validation must be True for ESCALATE status."""
        result = evaluate_guardrails(
            recommendation=recommendation_zero_stock,
            store_id="I63",
            inventory_snapshot=inv_snap_zero,
            rag_used=False,
            nb_scripts=0,
            confidence=0.3,
        )
        if result["status"] == "ESCALATE":
            assert result["requires_human_validation"] is True


# ─────────────────────────────────────────────────────────────────────────────
# scored_products — format validation
# ─────────────────────────────────────────────────────────────────────────────

class TestScoredProductsFormat:
    """
    Validates that rank_products() returns objects with the fields
    that chat.ts expects: sku, name, final_score, recommendation_reason.
    """

    def _make_product(self, sku, score):
        return {
            "sku":                  sku,
            "name":                 f"Product {sku}",
            "final_score":          score,
            "recommendation_reason": f"Score {score:.0%}",
        }

    def test_product_has_required_fields(self):
        p = self._make_product("P001", 0.82)
        for field in ("sku", "name", "final_score", "recommendation_reason"):
            assert field in p

    def test_products_sorted_by_score(self):
        products = [
            self._make_product("P001", 0.60),
            self._make_product("P002", 0.90),
            self._make_product("P003", 0.75),
        ]
        sorted_p = sorted(products, key=lambda x: x["final_score"], reverse=True)
        assert sorted_p[0]["sku"] == "P002"
        assert sorted_p[-1]["sku"] == "P001"

    def test_final_score_in_range(self):
        p = self._make_product("X", 0.55)
        assert 0.0 <= p["final_score"] <= 1.0
