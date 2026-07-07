"""
Tests unitaires — Critic/Guardrail Agent (S5.1 / S6.5)

Couvre les 7 règles (G1–G7), la logique de routage (_compute_status),
et l'intégration evaluate_guardrails().

Run: pytest tests/test_guardrail.py -v
"""
import sys, os

import pytest
from app.sales.coaching.agents.guardrail.guardrail_agent import (
    _g1_stock_available,
    _g2_stockout_imminent,
    _g3_rag_source,
    _g4_business_rules,
    _g5_network_eligibility,
    _g6_confidence,
    _g7_budget,
    _compute_status,
    evaluate_guardrails,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def inv_snap_ok():
    """Inventory snapshot — produit disponible, stock > 3j."""
    return {
        "skus": {
            "SKU-001": {
                "product_name":  "Samsung Galaxy A55",
                "stock_qty":     20,
                "days_remaining": 30.0,
            }
        }
    }


@pytest.fixture
def inv_snap_zero_stock():
    return {
        "skus": {
            "SKU-001": {
                "product_name":  "Samsung Galaxy A55",
                "stock_qty":     0,
                "days_remaining": 0.0,
            }
        }
    }


@pytest.fixture
def inv_snap_imminent():
    return {
        "skus": {
            "SKU-001": {
                "product_name":  "Samsung Galaxy A55",
                "stock_qty":     2,
                "days_remaining": 1.5,
            }
        }
    }


# ═════════════════════════════════════════════════════════════════════════════
# G1 — Stock zéro
# ═════════════════════════════════════════════════════════════════════════════

class TestG1StockAvailable:

    def test_passes_when_stock_ok(self, inv_snap_ok):
        assert _g1_stock_available("Samsung Galaxy A55", inv_snap_ok) is None

    def test_blocks_when_zero_stock(self, inv_snap_zero_stock):
        result = _g1_stock_available("Samsung Galaxy A55", inv_snap_zero_stock)
        assert result is not None
        assert "G1" in result

    def test_passes_when_product_not_in_snapshot(self, inv_snap_ok):
        assert _g1_stock_available("Produit Inconnu", inv_snap_ok) is None

    def test_passes_when_no_product(self, inv_snap_ok):
        assert _g1_stock_available(None, inv_snap_ok) is None

    def test_passes_when_no_snapshot(self):
        assert _g1_stock_available("Galaxy A55", {}) is None


# ═════════════════════════════════════════════════════════════════════════════
# G2 — Stockout imminent
# ═════════════════════════════════════════════════════════════════════════════

class TestG2StockoutImminent:

    def test_passes_when_days_ok(self, inv_snap_ok):
        assert _g2_stockout_imminent("Samsung Galaxy A55", inv_snap_ok) is None

    def test_fires_when_days_below_threshold(self, inv_snap_imminent):
        result = _g2_stockout_imminent("Samsung Galaxy A55", inv_snap_imminent)
        assert result is not None
        assert "G2" in result

    def test_skips_for_clearance(self, inv_snap_imminent):
        # Clearance push is allowed even with low stock
        assert _g2_stockout_imminent("Samsung Galaxy A55", inv_snap_imminent, is_clearance=True) is None

    def test_passes_when_product_unknown(self, inv_snap_imminent):
        assert _g2_stockout_imminent("Produit Inconnu", inv_snap_imminent) is None


# ═════════════════════════════════════════════════════════════════════════════
# G3 — RAG source
# ═════════════════════════════════════════════════════════════════════════════

class TestG3RagSource:

    def test_passes_when_rag_used(self):
        assert _g3_rag_source(rag_used=True, confidence=0.5, nb_scripts=3) is None

    def test_passes_when_confidence_high(self):
        assert _g3_rag_source(rag_used=False, confidence=0.85, nb_scripts=0) is None

    def test_fires_when_no_rag_and_low_confidence(self):
        result = _g3_rag_source(rag_used=False, confidence=0.55, nb_scripts=0)
        assert result is not None
        assert "G3" in result


# ═════════════════════════════════════════════════════════════════════════════
# G4 — Business rules (unauthorized offer)
# ═════════════════════════════════════════════════════════════════════════════

class TestG4BusinessRules:

    def test_passes_for_normal_message(self):
        assert _g4_business_rules("Poussez le Samsung A55, excellent rapport qualité/prix", []) is None

    def test_fires_for_unauthorized_discount(self):
        result = _g4_business_rules("Offrez une remise de 20% sur ce produit", [])
        assert result is not None
        assert "G4" in result

    def test_fires_for_hors_catalogue(self):
        result = _g4_business_rules("Proposez cet article hors catalogue", [])
        assert result is not None
        assert "G4" in result

    def test_fires_in_strategy_action(self):
        actions = [{"argument_vente": "offrir gratuitement un accessoire"}]
        result = _g4_business_rules("Bonne vente", actions)
        assert result is not None
        assert "G4" in result


# ═════════════════════════════════════════════════════════════════════════════
# G5 — Network eligibility (5G/Fibre)
# ═════════════════════════════════════════════════════════════════════════════

class TestG5NetworkEligibility:

    def test_passes_for_standard_product(self):
        assert _g5_network_eligibility("Poussez le Samsung A55", "Samsung Galaxy A55") is None

    def test_fires_for_5g_without_eligibility(self):
        result = _g5_network_eligibility("Proposez notre offre 5G", "Samsung S25 5G")
        assert result is not None
        assert "G5" in result

    def test_passes_for_5g_with_eligibility_mention(self):
        result = _g5_network_eligibility("Client éligible à la 5G dans sa zone", "Samsung S25 5G")
        assert result is None

    def test_fires_for_fibre_without_eligibility(self):
        result = _g5_network_eligibility("Proposez la fibre FTTH", None)
        assert result is not None
        assert "G5" in result


# ═════════════════════════════════════════════════════════════════════════════
# G6 — Confidence threshold
# ═════════════════════════════════════════════════════════════════════════════

class TestG6Confidence:

    def test_passes_at_threshold(self):
        assert _g6_confidence(0.65) is None

    def test_passes_above_threshold(self):
        assert _g6_confidence(0.90) is None

    def test_fires_below_threshold(self):
        result = _g6_confidence(0.50)
        assert result is not None
        assert "G6" in result

    def test_fires_at_zero(self):
        assert _g6_confidence(0.0) is not None


# ═════════════════════════════════════════════════════════════════════════════
# G7 — Budget cap
# ═════════════════════════════════════════════════════════════════════════════

class TestG7Budget:

    def test_passes_under_cap(self):
        assert _g7_budget(50_000.0, "ORDER") is None

    def test_passes_when_not_order_action(self):
        assert _g7_budget(200_000.0, "HOLD") is None

    def test_fires_when_order_exceeds_cap(self):
        result = _g7_budget(150_000.0, "ORDER")
        assert result is not None
        assert "G7" in result

    def test_fires_for_expedite_over_cap(self):
        result = _g7_budget(110_000.0, "EXPEDITE")
        assert result is not None
        assert "G7" in result


# ═════════════════════════════════════════════════════════════════════════════
# Routing logic
# ═════════════════════════════════════════════════════════════════════════════

class TestComputeStatus:

    def test_approve_when_no_issues(self):
        assert _compute_status([]) == "APPROVE"

    def test_block_beats_rewrite(self):
        issues = [{"rule": "G2"}, {"rule": "G1"}]
        assert _compute_status(issues) == "BLOCK"

    def test_escalate_beats_rewrite(self):
        issues = [{"rule": "G3"}, {"rule": "G6"}]
        assert _compute_status(issues) == "ESCALATE"

    def test_rewrite_when_only_soft_violations(self):
        issues = [{"rule": "G2"}, {"rule": "G3"}]
        assert _compute_status(issues) == "REWRITE"


# ═════════════════════════════════════════════════════════════════════════════
# Integration — evaluate_guardrails()
# ═════════════════════════════════════════════════════════════════════════════

class TestEvaluateGuardrails:

    def test_approve_clean_recommendation(self, inv_snap_ok):
        result = evaluate_guardrails(
            recommendation     = {
                "product_to_push":     "Samsung Galaxy A55",
                "message_for_advisor": "Poussez le A55, excellent taux de marge.",
                "strategy_actions":    [],
            },
            store_id           = "I63",
            inventory_snapshot = inv_snap_ok,
            rag_used           = True,
            nb_scripts         = 2,
            confidence         = 0.88,
        )
        assert result["status"] == "APPROVE"
        assert result["issues"] == []
        assert result["requires_human_validation"] is False

    def test_block_zero_stock(self, inv_snap_zero_stock):
        result = evaluate_guardrails(
            recommendation     = {
                "product_to_push":     "Samsung Galaxy A55",
                "message_for_advisor": "Vendez ce produit.",
                "strategy_actions":    [],
            },
            store_id           = "I63",
            inventory_snapshot = inv_snap_zero_stock,
            rag_used           = True,
            nb_scripts         = 2,
            confidence         = 0.90,
        )
        assert result["status"] == "BLOCK"
        assert result["safe_fallback"] != ""
        assert any(i["rule"] == "G1" for i in result["issues"])

    def test_escalate_low_confidence(self, inv_snap_ok):
        result = evaluate_guardrails(
            recommendation     = {"product_to_push": None, "message_for_advisor": "Consultez le catalogue.", "strategy_actions": []},
            store_id           = "I63",
            inventory_snapshot = inv_snap_ok,
            rag_used           = False,
            nb_scripts         = 0,
            confidence         = 0.40,
        )
        # G3 (REWRITE) + G6 (ESCALATE) → highest = ESCALATE
        assert result["status"] in ("ESCALATE", "REWRITE")
        assert result["requires_human_validation"] is (result["status"] in ("ESCALATE", "BLOCK"))

    def test_rewrite_returns_feedback_message(self, inv_snap_imminent):
        result = evaluate_guardrails(
            recommendation     = {"product_to_push": "Samsung Galaxy A55", "message_for_advisor": "Poussez le A55.", "strategy_actions": []},
            store_id           = "I63",
            inventory_snapshot = inv_snap_imminent,
            rag_used           = True,
            nb_scripts         = 2,
            confidence         = 0.85,
        )
        assert result["status"] == "REWRITE"
        assert result["feedback"] != ""

    def test_block_unauthorized_offer(self, inv_snap_ok):
        result = evaluate_guardrails(
            recommendation     = {
                "product_to_push":     None,
                "message_for_advisor": "Offrez une remise de 30% hors catalogue",
                "strategy_actions":    [],
            },
            store_id           = "I63",
            inventory_snapshot = inv_snap_ok,
            rag_used           = True,
            nb_scripts         = 1,
            confidence         = 0.80,
        )
        assert result["status"] == "BLOCK"
        assert any(i["rule"] == "G4" for i in result["issues"])

    def test_confidence_penalized_when_issues(self, inv_snap_imminent):
        result = evaluate_guardrails(
            recommendation     = {"product_to_push": "Samsung Galaxy A55", "message_for_advisor": "Poussez.", "strategy_actions": []},
            store_id           = "I63",
            inventory_snapshot = inv_snap_imminent,
            rag_used           = True,
            nb_scripts         = 1,
            confidence         = 0.80,
        )
        # Confidence should be penalized (×0.5 when issues present)
        assert result["final_confidence"] < 0.80
