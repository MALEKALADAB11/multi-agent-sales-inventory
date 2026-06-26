"""
Agent Analyste — Architecture ReAct (Reason + Act).

Graphe :
  receive_pos → validate_data → load_memory
    → react_analyst   ← VRAI agent ReAct (remplace 6 nodes statiques)
  → build_strategy_query → save_memory → END
"""

import logging
from langgraph.graph import StateGraph, END

from core.state import SalesAgentState
from .nodes import (
    node_receive_pos,
    node_validate_data,
    node_load_memory,
    node_build_strategy_query,
    node_save_memory,
)
from .react_analyst import node_react_analyst

logger = logging.getLogger(__name__)

_analyst_agent = None


def build_analyst_graph() -> StateGraph:
    graph = StateGraph(SalesAgentState)

    # ── Nodes pipeline (inchangés)
    graph.add_node("receive_pos",         node_receive_pos)
    graph.add_node("validate_data",       node_validate_data)
    graph.add_node("load_memory",         node_load_memory)

    # ── Node ReAct — remplace feature_engineering + compute_gap + call_timesfm
    #                + compare_with_memory + detect_urgency + llm_summary
    graph.add_node("react_analyst",       node_react_analyst)

    # ── Nodes sortie (inchangés)
    graph.add_node("build_strategy_query", node_build_strategy_query)
    graph.add_node("save_memory",          node_save_memory)

    # ── Edges
    graph.set_entry_point("receive_pos")
    graph.add_edge("receive_pos",          "validate_data")
    graph.add_edge("validate_data",        "load_memory")
    graph.add_edge("load_memory",          "react_analyst")
    graph.add_edge("react_analyst",        "build_strategy_query")
    graph.add_edge("build_strategy_query", "save_memory")
    graph.add_edge("save_memory",          END)

    logger.info("[ANALYST] Graphe ReAct construit — 7 nodes (react_analyst remplace 6 nodes statiques)")
    return graph


def compile_analyst_agent(checkpointer=None):
    return build_analyst_graph().compile(checkpointer=checkpointer)


def get_analyst_agent():
    global _analyst_agent
    if _analyst_agent is None:
        _analyst_agent = compile_analyst_agent()
    return _analyst_agent
