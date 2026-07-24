"""
Tests unitaires — Agent Analyste v4 (surface réellement exécutée).

Le moteur statistique est couvert par test_ts_engine.py. Ce fichier couvre ce
qui l'entoure : le graphe compilé, le prompt de résumé, et la comparaison
inter-cycles de la mémoire.

Historique : ce fichier testait `react_tools._score_urgency` et une classe
`Analyste._sql_rolling_forecast` qui n'existent plus — le mode ReAct n'a jamais
été câblé dans le graphe v4 et a été supprimé.
"""
import pytest

from app.sales.coaching.agents.analyst.agent import build_analyst_graph
from app.sales.coaching.agents.analyst.prompts import (
    ANALYST_SUMMARY_SYSTEM_PROMPT,
    build_summary_prompt,
)
from app.sales.coaching.agents.analyst.tools import compare_with_memory


# ══════════════════════════════════════════════════════════════════════════════
# GRAPHE — la topologie v4 est figée
# ══════════════════════════════════════════════════════════════════════════════

class TestAnalystGraph:

    EXPECTED_NODES = {
        "receive_pos", "validate_data", "load_memory", "ts_analyst",
        "compare_with_memory", "build_strategy_query", "save_memory",
    }

    def test_graph_has_exactly_seven_nodes(self):
        graph = build_analyst_graph()
        assert set(graph.nodes) - {"__start__", "__end__"} == self.EXPECTED_NODES

    def test_graph_compiles(self):
        assert build_analyst_graph().compile() is not None

    def test_no_react_node(self):
        """Garde-fou : le mode ReAct ne doit pas revenir sans décision explicite."""
        assert not any("react" in n for n in build_analyst_graph().nodes)


# ══════════════════════════════════════════════════════════════════════════════
# PROMPT DE RÉSUMÉ — le LLM reformule, il ne calcule pas
# ══════════════════════════════════════════════════════════════════════════════

def _analysis(**overrides) -> dict:
    base = {
        "store_id": "I63", "analysis_hour": 14,
        "current_ca": 580.0, "daily_target": 1007.0, "attainment_pct": 57.6,
        "eod_forecast": 890.0, "eod_ci_low": 820.0, "eod_ci_high": 960.0,
        "mape_backtest": 4.4, "model_engine": "holt_winters_seasonal7",
        "gap_pct": 11.6, "gap_eod": 117.0,
        "trend_signal": "DECELERATING", "feasibility": "CHALLENGING",
        "hours_remaining": 6,
        "hourly_gaps": [
            {"hour": 11, "deviation_pct": -38.0},
            {"hour": 12, "deviation_pct": -22.5},
        ],
    }
    base.update(overrides)
    return base


class TestSummaryPrompt:

    def test_system_prompt_forbids_recomputation(self):
        assert "recalcules rien" in ANALYST_SUMMARY_SYSTEM_PROMPT
        assert "2 phrases" in ANALYST_SUMMARY_SYSTEM_PROMPT

    def test_prompt_carries_every_figure(self):
        out = build_summary_prompt(_analysis())
        for expected in ("I63", "14h", "580", "1007", "890", "11.6%", "117",
                         "DECELERATING", "CHALLENGING", "holt_winters_seasonal7"):
            assert expected in out, f"{expected!r} absent du prompt"

    def test_gap_hours_are_formatted(self):
        out = build_summary_prompt(_analysis())
        assert "11h -38%" in out and "12h -22%" in out

    def test_gap_hours_empty_says_aucune(self):
        assert "aucune" in build_summary_prompt(_analysis(hourly_gaps=[]))

    def test_gap_hours_truncated_to_four(self):
        gaps = [{"hour": h, "deviation_pct": -30.0} for h in range(9, 19)]
        out = build_summary_prompt(_analysis(hourly_gaps=gaps))
        assert "13h" not in out.split("Heures en retard")[1].split("\n")[0]

    def test_missing_keys_do_not_raise(self):
        """Le fallback linéaire produit un dict incomplet — il ne doit pas casser."""
        assert build_summary_prompt({"store_id": "I63"})


# ══════════════════════════════════════════════════════════════════════════════
# MÉMOIRE — comparaison entre cycles
# ══════════════════════════════════════════════════════════════════════════════

class TestCompareWithMemory:

    def test_no_memory_returns_defaults(self):
        out = compare_with_memory({"gap_pct": 20.0}, {"count": 0, "latest": {}})
        assert isinstance(out, dict)

    def test_detects_worsening_gap(self):
        current = {"gap_pct": 40.0, "current_revenue": 300.0, "avg_ticket": 50.0}
        memory = {"count": 1, "latest": {"gap_pct": 20.0, "current_revenue": 500.0,
                                         "avg_ticket": 60.0}}
        out = compare_with_memory(current, memory)
        assert out.get("gap_trend") in ("worsening", "degrading", "up", "increasing")
