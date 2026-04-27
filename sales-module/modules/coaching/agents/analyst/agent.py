"""
Agent Analyste — Construction du sous-graphe LangGraph.
"""
import logging
from langgraph.graph import StateGraph, END

from core.state import SalesAgentState
from .nodes import (
    node_receive_pos,
    node_compute_gap,
    node_call_timesfm,
    node_detect_urgency,
    node_llm_summary,
    route_after_analysis,
)

logger = logging.getLogger(__name__)


def build_analyst_graph() -> StateGraph:
    graph = StateGraph(SalesAgentState)

    graph.add_node("receive_pos", node_receive_pos)
    graph.add_node("compute_gap", node_compute_gap)
    graph.add_node("call_timesfm", node_call_timesfm)
    graph.add_node("detect_urgency", node_detect_urgency)
    graph.add_node("llm_summary", node_llm_summary)

    graph.set_entry_point("receive_pos")
    graph.add_edge("receive_pos", "compute_gap")
    graph.add_edge("compute_gap", "call_timesfm")
    graph.add_edge("call_timesfm", "detect_urgency")
    graph.add_edge("detect_urgency", "llm_summary")
    graph.add_edge("llm_summary", END)

    logger.info("[ANALYST] Graphe LangGraph construit avec 5 nodes.")
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