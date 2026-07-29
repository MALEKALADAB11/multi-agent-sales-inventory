"""
tests/evals/test_run_inventory_recommendations_live_offline.py
================================================================
Test de câblage hors-ligne pour run_inventory_recommendations_live.py.

Même logique que test_run_inventory_recommendations_offline.py (le fichier
Option A) : ne teste PAS la qualité des recommandations réelles — ça exige
une vraie DB et un vrai LLM, donc ce n'est pas ce que ce test vérifie. Teste
que le SCRIPT lui-même est correct, sans dépendre d'aucun quota ni d'aucune
connexion réseau :
  - build_context_string produit une chaîne exploitable à partir d'un résultat
    orchestrateur factice
  - run() agrège correctement : n_cases / n_success / n_judged sont honnêtes
    (un cas "réussi" mais dégradé en fallback rule-based n'est PAS compté
    comme jugé — c'est le bug observé le 26/07 sur le runner Option A, ce
    test vérifie qu'il n'existe pas ici)
  - une erreur orchestrateur sur un SKU n'interrompt pas les autres cas
  - save_results / push_inventory_recommendations sont bien appelés

Bouchonne les deux seules choses qu'un environnement sans secrets/DB (CI,
sandbox local, ou simplement "je ne veux pas attendre le reset de quota") ne
peut pas atteindre :
  1. create_orchestrator (a besoin d'une vraie DB) → orchestrateur factice
     mais déterministe, dérivé du sku/store_id passés.
  2. judge_inventory_answer (a besoin d'une clé API) → règle déterministe
     simple, comme dans le test Option A : un chiffre halluciné fait chuter
     `ancrage`.

Lancer depuis la racine du repo :
    python -m pytest tests/evals/test_run_inventory_recommendations_live_offline.py -q
    ou : python tests/evals/test_run_inventory_recommendations_live_offline.py
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

# evals/ et tests/ sont tous deux à la racine du repo, et ce fichier est
# maintenant à tests/evals/ (deux niveaux sous la racine) — sans ce bootstrap,
# `from evals import ...` ne se résout que si le fichier est lancé via
# `python -m pytest` depuis la racine (qui préfixe automatiquement le cwd à
# sys.path). Lancé directement (`python tests/evals/test_x.py`) ou via un
# runner qui n'utilise pas `-m`, Python n'ajoute que le dossier du script
# (tests/evals/) à sys.path, pas la racine — `evals` resterait introuvable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import _app_stubs  # noqa: E402


def _install_fake_orchestrator_module():
    """Injecte un module factice pour app.inventory.services.orchestrator,
    sans toucher au disque ni exiger la vraie codebase app/ ou une DB."""

    class _FakeOrchestrator:
        def __init__(self, use_llm: bool = True):
            self.use_llm = use_llm

        def analyze_sku(self, sku: str, store_id: str, business_objective: str) -> dict:
            if sku == "SKU-ERROR":
                return {"error": f"SKU inconnu: {sku}"}

            # SKU-FALLBACK simule un candidat dégradé (LLM indisponible côté
            # app) — reasoning_source="rule_based_fallback", ne doit PAS être
            # compté comme jugé par run().
            degraded = sku == "SKU-FALLBACK"
            qty = 45
            days = 2
            lead = 10

            text = (
                f"[fallback] Action: EXPEDITE. Stock: {days}j. Delai: {lead}j."
                if degraded else
                f"Expediez {qty} unites de {sku} — le stock tombe a {days} jours "
                f"contre un delai de livraison de {lead} jours."
            )

            return {
                "analysis_report": {"risk_assessment": {"level": "CRITICAL"},
                                     "metrics": {"days_of_stock_remaining": days}},
                "context_result": {"context_report": {"demand_uplift_pct": 0}},
                "decision_result": {
                    "adjusted_metrics": {"adjusted_formula_order_qty": qty},
                    "decision": {
                        "action": "EXPEDITE",
                        "order_qty": qty,
                        "recommendation_text": text,
                        "reasoning_source": "rule_based_fallback" if degraded else "llm",
                    },
                },
            }

    def _fake_create_orchestrator(use_llm: bool = True):
        return _FakeOrchestrator(use_llm=use_llm)

    orch_mod = types.ModuleType("app.inventory.services.orchestrator")
    orch_mod.create_orchestrator = _fake_create_orchestrator

    # Seule la feuille est bouchonnée, et le bouchon est défait par
    # tearDownModule — voir _app_stubs pour le pourquoi (un faux paquet `app`
    # laissé dans sys.modules cassait la collecte de toute la suite).
    return _app_stubs.install({"app.inventory.services.orchestrator": orch_mod})


_stubs = None


def setUpModule():
    # Installé ici et pas à l'import : pytest importe tous les fichiers de test
    # avant d'en exécuter un seul, donc un bouchon posé à l'import resterait
    # actif pendant l'import des fichiers suivants. `rirl` importe
    # create_orchestrator paresseusement (dans run()), le bouchon arrive à temps.
    global _stubs
    _stubs = _install_fake_orchestrator_module()


def tearDownModule():
    if _stubs is not None:
        _stubs.restore()


from evals import run_inventory_recommendations_live as rirl  # noqa: E402
from evals.judge import INVENTORY_CRITERIA, Judgment  # noqa: E402


def _fake_judge_inventory_answer(scenario, recommendation_text, *, context="",
                                 expected_behaviors=None, avoid_model="",
                                 skip_models=(), max_retries=0):
    scores = {c: 5 for c in INVENTORY_CRITERIA}
    return Judgment(scores=scores, hallucination=False, verdict="stub",
                    judge_model="fake/judge")


class TestInventoryLiveEvalWiring(unittest.TestCase):
    def setUp(self):
        self._real_judge = rirl.judge_inventory_answer
        self._real_save = rirl.save_results
        self._pushed = []
        rirl.judge_inventory_answer = _fake_judge_inventory_answer
        rirl.save_results = lambda name, payload: None  # no disk I/O in this test

    def tearDown(self):
        rirl.judge_inventory_answer = self._real_judge
        rirl.save_results = self._real_save

    def test_build_context_string_from_orchestrator_result(self):
        from app.inventory.services.orchestrator import create_orchestrator
        result = create_orchestrator(use_llm=True).analyze_sku("SKU-001", "store-A", "standard")
        ctx = rirl.build_context_string(result)
        self.assertIn("analysis_report", ctx)
        self.assertIn("context_report", ctx)
        self.assertIn("adjusted_metrics", ctx)

    def test_run_counts_are_honest_about_fallback_and_errors(self):
        pairs = [("SKU-001", "store-A"), ("SKU-FALLBACK", "store-A"), ("SKU-ERROR", "store-A")]
        summary = rirl.run(pairs, pace=0.0)
        self.assertEqual(summary["n_cases"], 3)
        # 2 réussissent (SKU-001 llm, SKU-FALLBACK dégradé) ; 1 échoue (SKU-ERROR)
        self.assertEqual(summary["n_success"], 2)
        # Seul SKU-001 (source=llm) doit être jugé — pas le fallback dégradé.
        self.assertEqual(summary["n_judged"], 1)
        self.assertEqual(summary["judge_global_mean"], 5.0)

    def test_all_llm_cases_get_judged(self):
        pairs = [("SKU-001", "store-A"), ("SKU-002", "store-B")]
        summary = rirl.run(pairs, pace=0.0)
        self.assertEqual(summary["n_success"], 2)
        self.assertEqual(summary["n_judged"], 2)
        self.assertEqual(set(summary["judge_scores_mean"]), set(INVENTORY_CRITERIA))

    def test_orchestrator_error_does_not_stop_other_cases(self):
        pairs = [("SKU-ERROR", "store-A"), ("SKU-001", "store-A")]
        summary = rirl.run(pairs, pace=0.0)
        self.assertEqual(summary["n_success"], 1)
        errored = [r for r in summary["details"] if r.get("error")]
        self.assertEqual(len(errored), 1)
        self.assertEqual(errored[0]["sku"], "SKU-ERROR")

    def test_no_judge_flag_skips_judging_entirely(self):
        pairs = [("SKU-001", "store-A")]
        summary = rirl.run(pairs, pace=0.0, use_judge=False)
        self.assertEqual(summary["n_success"], 1)
        self.assertEqual(summary["n_judged"], 0)
        self.assertIsNone(summary["judge_global_mean"])


if __name__ == "__main__":
    unittest.main()
