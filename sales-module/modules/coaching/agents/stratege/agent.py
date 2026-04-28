"""
Agent Stratège — Graphe LangGraph.
"""
import logging
from langgraph.graph import StateGraph, END

from core.state import SalesAgentState
from .nodes import (
    node_fetch_context,
    node_analyze_root_cause,
    node_generate_strategy,
    node_build_output,
)

logger = logging.getLogger(__name__)


def build_stratege_graph() -> StateGraph:
    """
    Flow :
    fetch_context → analyze_root_cause → generate_strategy → build_output → END
    """
    graph = StateGraph(SalesAgentState)

    graph.add_node("fetch_context",       node_fetch_context)
    graph.add_node("analyze_root_cause",  node_analyze_root_cause)
    graph.add_node("generate_strategy",   node_generate_strategy)
    graph.add_node("build_output",        node_build_output)

    graph.set_entry_point("fetch_context")
    graph.add_edge("fetch_context",      "analyze_root_cause")
    graph.add_edge("analyze_root_cause", "generate_strategy")
    graph.add_edge("generate_strategy",  "build_output")
    graph.add_edge("build_output",       END)

    logger.info("[STRATEGE] Graphe LangGraph construit avec 4 nodes.")
    return graph


def compile_stratege_agent(checkpointer=None):
    return build_stratege_graph().compile(checkpointer=checkpointer)


_stratege_agent = None

def get_stratege_agent():
    global _stratege_agent
    if _stratege_agent is None:
        _stratege_agent = compile_stratege_agent()
    return _stratege_agent