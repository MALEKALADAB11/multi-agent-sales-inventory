"""
Agent Analyste — v4 déterministe temps réel.

Graphe :
  receive_pos → validate_data → load_memory
    → ts_analysis          ← VRAI moteur séries temporelles (Holt-Winters
                              saisonnier + profil intraday + gap horaire),
                              zéro LLM dans le chemin critique (< 1s)
    → compare_with_memory  ← tendance vs cycles précédents
  → build_strategy_query → save_memory → END
"""

import logging
from langgraph.graph import StateGraph, END

from app.sales.core.state import SalesAgentState
from .nodes import (
    node_receive_pos,
    node_validate_data,
    node_load_memory,
    node_compare_with_memory,
    node_build_strategy_query,
    node_save_memory,
)
from .ts_node import node_ts_analysis

logger = logging.getLogger(__name__)

_analyst_agent = None


def build_analyst_graph() -> StateGraph:
    graph = StateGraph(SalesAgentState)

    # ── Nodes pipeline (inchangés)
    graph.add_node("receive_pos",         node_receive_pos)
    graph.add_node("validate_data",       node_validate_data)
    graph.add_node("load_memory",         node_load_memory)

    # ── Node analytique déterministe — forecast EOD + gap horaire + urgence
    graph.add_node("ts_analyst",          node_ts_analysis)
    graph.add_node("compare_with_memory", node_compare_with_memory)

    # ── Nodes sortie (inchangés)
    graph.add_node("build_strategy_query", node_build_strategy_query)
    graph.add_node("save_memory",          node_save_memory)

    # ── Edges
    graph.set_entry_point("receive_pos")
    graph.add_edge("receive_pos",          "validate_data")
    graph.add_edge("validate_data",        "load_memory")
    graph.add_edge("load_memory",          "ts_analyst")
    graph.add_edge("ts_analyst",           "compare_with_memory")
    graph.add_edge("compare_with_memory",  "build_strategy_query")
    graph.add_edge("build_strategy_query", "save_memory")
    graph.add_edge("save_memory",          END)

    logger.info("[ANALYST] Graphe v4 construit — 7 nodes, moteur TS déterministe (ts_analysis)")
    return graph


def compile_analyst_agent(checkpointer=None):
    return build_analyst_graph().compile(checkpointer=checkpointer)


def get_analyst_agent():
    global _analyst_agent
    if _analyst_agent is None:
        _analyst_agent = compile_analyst_agent()
    return _analyst_agent
