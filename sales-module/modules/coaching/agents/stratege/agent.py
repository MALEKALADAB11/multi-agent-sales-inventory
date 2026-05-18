"""
Agent Stratège — Graphe LangGraph v2.
======================================
Flow :
    fetch_context       → météo + fériés + événements
        ↓
    rag_search          → recherche Milvus scripts similaires
        ↓
    analyze_context     → cause racine + facteurs contextuels
        ↓
    generate_strategy   → LLM + RAG → actions concrètes
        ↓
    build_output        → formatage final pour le frontend
        ↓
       END
"""
import logging
from langgraph.graph import StateGraph, END
from core.state import SalesAgentState
from .nodes import (
    node_fetch_context,
    node_rag_search,
    node_analyze_context,
    node_generate_strategy,
    node_build_output,
)

logger = logging.getLogger(__name__)


def build_stratege_graph() -> StateGraph:
    graph = StateGraph(SalesAgentState)

    # ── Nodes ──────────────────────────────────────────────
    graph.add_node("fetch_context",     node_fetch_context)
    graph.add_node("rag_search",        node_rag_search)
    graph.add_node("analyze_context",   node_analyze_context)
    graph.add_node("generate_strategy", node_generate_strategy)
    graph.add_node("build_output",      node_build_output)

    # ── Edges ──────────────────────────────────────────────
    graph.set_entry_point("fetch_context")
    graph.add_edge("fetch_context",     "rag_search")
    graph.add_edge("rag_search",        "analyze_context")
    graph.add_edge("analyze_context",   "generate_strategy")
    graph.add_edge("generate_strategy", "build_output")
    graph.add_edge("build_output",      END)

    logger.info("[STRATEGE] Graphe LangGraph construit — 5 nodes.")
    return graph


def compile_stratege_agent(checkpointer=None):
    return build_stratege_graph().compile(checkpointer=checkpointer)


_stratege_agent = None


def get_stratege_agent():
    global _stratege_agent
    if _stratege_agent is None:
        _stratege_agent = compile_stratege_agent()
    return _stratege_agent