"""
evals/tests/test_run_inventory_recommendations_offline.py
===========================================================
Test de câblage hors-ligne pour run_inventory_recommendations.py.

Ne teste PAS la qualité des recommandations (ça, c'est le rôle du run réel
avec de vraies clés LLM). Teste que le SCRIPT lui-même est correct :
  - build_state / build_context_string produisent les bonnes clés/valeurs
  - _corrupt_one_number modifie effectivement un nombre
  - run() agrège correctement sur les 15 cas du dataset réel
  - la comparaison fallback (richesse) fonctionne
  - run_sanity_checks / check_judge_determinism tournent sans erreur

Comment : on bouchonne les deux seules choses qu'un environnement sans
secrets/API (CI, sandbox local) ne peut pas atteindre :
  1. create_decide_node / get_smart_llm (le vrai code de production, qui a
     besoin d'une vraie DB/LLM) → remplacé par un decide_node factice mais
     déterministe, dérivé directement du state (pas de hasard).
  2. judge_inventory_answer (le vrai juge, qui a besoin d'une clé API) →
     remplacé par une règle déterministe simple : un chiffre du texte absent
     du contexte fait chuter `ancrage`, un texte commençant par "[fallback]"
     fait chuter `richesse`. Assez pour vérifier que le SCRIPT réagit
     correctement à ces deux critères, sans vérifier la qualité du vrai juge.

Lancer :  python -m pytest tests/test_run_inventory_recommendations_offline.py -q
      ou : python tests/test_run_inventory_recommendations_offline.py
"""
from __future__ import annotations

import re
import sys
import types
import unittest


def _install_fake_app_modules():
    """Injecte des modules factices dans sys.modules pour
    app.inventory.agents.decision.nodes et app.inventory.utils.llm_factory,
    sans toucher au disque ni exiger la vraie codebase app/."""

    def _fake_create_decide_node(llm, use_llm: bool):
        def decide_node(state):
            base = state["baseline_report"]
            risk = base["risk_assessment"]["level"]
            days = base["metrics"]["days_of_stock_remaining"]
            qty = base["metrics"]["formula_order_qty"]
            lead = base["stock"]["lead_time_avg_days"]
            product = base.get("product_name", state["sku"])

            if base["risk_assessment"].get("overstock_flag"):
                action = "HOLD"
            elif risk == "CRITICAL" and days < lead:
                action = "EXPEDITE"
            elif risk in ("CRITICAL", "HIGH"):
                action = "ORDER"
            elif risk == "MEDIUM":
                action = "MONITOR"
            else:
                action = "HOLD"

            if use_llm:
                text = (
                    f"Commander {qty} unites de {product} — le stock tombe a "
                    f"{days} jours contre un delai de livraison de {lead} jours."
                )
                source = "llm"
            else:
                text = f"[fallback] Action recommandee: {action}. Stock: {days}j. Delai: {lead}j."
                source = "rule_based_fallback"

            return {**state, "decision": {
                "action": action,
                "order_qty": int(qty) if action in ("ORDER", "EXPEDITE") else None,
                "urgency": "immediate" if action in ("ORDER", "EXPEDITE") else "none",
                "confidence": "high",
                "recommendation_text": text,
                "reasoning_source": source,
            }}
        return decide_node

    nodes_mod = types.ModuleType("app.inventory.agents.decision.nodes")
    nodes_mod.create_decide_node = _fake_create_decide_node

    llm_factory_mod = types.ModuleType("app.inventory.utils.llm_factory")
    llm_factory_mod.get_smart_llm = lambda: object()

    for name, mod in [
        ("app", types.ModuleType("app")),
        ("app.inventory", types.ModuleType("app.inventory")),
        ("app.inventory.agents", types.ModuleType("app.inventory.agents")),
        ("app.inventory.agents.decision", types.ModuleType("app.inventory.agents.decision")),
        ("app.inventory.agents.decision.nodes", nodes_mod),
        ("app.inventory.utils", types.ModuleType("app.inventory.utils")),
        ("app.inventory.utils.llm_factory", llm_factory_mod),
    ]:
        sys.modules.setdefault(name, mod)


_install_fake_app_modules()

from evals import run_inventory_recommendations as rir  # noqa: E402
from evals.judge import INVENTORY_CRITERIA, Judgment  # noqa: E402


def _fake_judge_inventory_answer(scenario, recommendation_text, *, context="",
                                 expected_behaviors=None, avoid_model="",
                                 skip_models=(), max_retries=0):
    numbers_in_text = set(re.findall(r"\b\d{2,}\b", recommendation_text))
    numbers_in_context = set(re.findall(r"\b\d{2,}\b", context))
    hallucinated = bool(numbers_in_text - numbers_in_context)

    scores = {c: 5 for c in INVENTORY_CRITERIA}
    scores["ancrage"] = 1 if hallucinated else 5
    scores["richesse"] = 1 if recommendation_text.startswith("[fallback]") else 5

    judge_model = "fake/judge-a" if "fake/judge-a" not in skip_models else "fake/judge-b"
    return Judgment(scores=scores, hallucination=hallucinated, verdict="stub",
                    judge_model=judge_model)


class TestInventoryEvalWiring(unittest.TestCase):
    def setUp(self):
        self._real_judge = rir.judge_inventory_answer
        self._real_save = rir.save_results
        rir.judge_inventory_answer = _fake_judge_inventory_answer
        rir.save_results = lambda name, payload: None  # no disk I/O in this test

    def tearDown(self):
        rir.judge_inventory_answer = self._real_judge
        rir.save_results = self._real_save

    def test_build_state_and_context(self):
        cases = rir.load_dataset("inventory_recommendations.json")
        case = cases[0]
        state = rir.build_state(case)
        self.assertIs(state["baseline_report"], case["baseline_report"])
        self.assertIn("sku", state)
        self.assertIn("store_id", state)
        ctx = rir.build_context_string(case)
        self.assertIn("baseline_report", ctx)
        self.assertIn(case["sku"], ctx)  # sku must be present for ancrage grounding

    def test_corrupt_one_number(self):
        text = "Commander 45 unites aujourd'hui."
        corrupted = rir._corrupt_one_number(text)
        self.assertIsNotNone(corrupted)
        self.assertNotEqual(corrupted, text)
        self.assertNotIn("45", corrupted)

    def test_dataset_has_15_cases_across_categories(self):
        cases = rir.load_dataset("inventory_recommendations.json")
        self.assertEqual(len(cases), 15)
        categories = {c["category"] for c in cases}
        self.assertEqual(categories, {
            "critical_stockout", "normal_reorder", "monitor_situations",
            "hold_overstock", "context_signal_cases", "escalation_cases", "edge_cases",
        })
        fallback_anchors = [c for c in cases if c.get("compare_fallback")]
        self.assertGreaterEqual(len(fallback_anchors), 2)

    def test_full_run_aggregates_correctly(self):
        summary = rir.run(use_judge=True, run_sanity=True, run_determinism=True)
        self.assertEqual(summary["n_cases"], 15)
        self.assertEqual(summary["n_success"], 15)
        self.assertEqual(set(summary["judge_scores_mean"]), set(INVENTORY_CRITERIA))
        self.assertIsNotNone(summary["judge_global_mean"])

    def test_richesse_anchor_discriminates(self):
        summary = rir.run(use_judge=True, run_sanity=False, run_determinism=False)
        self.assertEqual(len(summary["fallback_comparison"]), 2)
        for comp in summary["fallback_comparison"]:
            self.assertGreater(comp["llm_richesse"], comp["rule_based_richesse"])

    def test_sanity_checks_discriminate_richesse_and_ancrage(self):
        cases = rir.load_dataset("inventory_recommendations.json")
        decide_llm, decide_rb = rir._build_decide_nodes()
        results = rir.run_sanity_checks(cases, decide_llm, decide_rb)
        self.assertTrue(results["richesse"]["discriminates"])
        if results["ancrage"] is not None:
            self.assertTrue(results["ancrage"]["discriminates"])

    def test_determinism_check_is_stable_for_a_fixed_deterministic_judge(self):
        cases = rir.load_dataset("inventory_recommendations.json")
        decide_llm, _ = rir._build_decide_nodes()
        case = cases[0]
        state = rir.build_state(case)
        result = decide_llm(state)
        text = result["decision"]["recommendation_text"]
        ctx = rir.build_context_string(case)
        outcome = rir.check_judge_determinism(case, text, ctx, n_repeats=2)
        self.assertTrue(outcome["stable"])

    def test_dry_run_does_not_raise(self):
        cases = rir.load_dataset("inventory_recommendations.json")
        decide_llm, _ = rir._build_decide_nodes()
        rir.dry_run(cases, decide_llm, n=2)  # smoke test: must not raise


if __name__ == "__main__":
    unittest.main()
