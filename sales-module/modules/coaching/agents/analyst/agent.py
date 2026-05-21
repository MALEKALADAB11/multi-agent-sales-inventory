"""
Agent Analyste — Sous-graphe LangGraph renforcé.
PostgreSQL + Logs + Analyst Memory.
"""

import logging
from langgraph.graph import StateGraph, END

from core.state import SalesAgentState
from .nodes import (
    node_receive_pos,
    node_validate_data,
    node_load_memory,
    node_feature_engineering,
    node_compute_gap,
    node_call_timesfm,
    node_compare_with_memory,
    node_detect_urgency,
    node_llm_summary,
    node_build_strategy_query,
    node_save_memory,
)

logger = logging.getLogger(__name__)


def build_analyst_graph() -> StateGraph:
    graph = StateGraph(SalesAgentState)

    graph.add_node("receive_pos", node_receive_pos)
    graph.add_node("validate_data", node_validate_data)
    graph.add_node("load_memory", node_load_memory)
    graph.add_node("feature_engineering", node_feature_engineering)
    graph.add_node("compute_gap", node_compute_gap)
    graph.add_node("call_timesfm", node_call_timesfm)
    graph.add_node("compare_with_memory", node_compare_with_memory)
    graph.add_node("detect_urgency", node_detect_urgency)
    graph.add_node("llm_summary", node_llm_summary)
    graph.add_node("build_strategy_query", node_build_strategy_query)
    graph.add_node("save_memory", node_save_memory)

    graph.set_entry_point("receive_pos")

    graph.add_edge("receive_pos", "validate_data")
    graph.add_edge("validate_data", "load_memory")
    graph.add_edge("load_memory", "feature_engineering")
    graph.add_edge("feature_engineering", "compute_gap")
    graph.add_edge("compute_gap", "call_timesfm")
    graph.add_edge("call_timesfm", "compare_with_memory")
    graph.add_edge("compare_with_memory", "detect_urgency")
    graph.add_edge("detect_urgency", "llm_summary")
    graph.add_edge("llm_summary", "build_strategy_query")
    graph.add_edge("build_strategy_query", "save_memory")
    graph.add_edge("save_memory", END)

    logger.info("[ANALYST] Graphe LangGraph Memory construit avec 11 nodes.")
    return graph


def compile_analyst_agent(checkpointer=None):
    graph = build_analyst_graph()
    return graph.compile(checkpointer=checkpointer)


_analyst_agent = None


def get_analyst_agent():
    global _analyst_agent

    if _analyst_agent is None:
        _analyst_agent = compile_analyst_agent()

    return _analyst_agent