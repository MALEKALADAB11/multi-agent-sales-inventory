"""
Agent Coach — Construction du sous-graphe LangGraph v3.
Flow simplifié (sans node_invoke_stratege) :
    load_context
        ↓
    rag_search
        ↓
    load_advisor_history
        ↓
    generate_conseil
        ↓
    save_conseil
        ↓
       END
"""
import logging
from langgraph.graph import StateGraph, END
from core.state import SalesAgentState
from .nodes import (
    node_load_context,
    node_rag_search,
    node_load_advisor_history,
    node_generate_conseil,
    node_save_conseil,
)

logger = logging.getLogger(__name__)


def build_coach_graph() -> StateGraph:
    graph = StateGraph(SalesAgentState)

    graph.add_node("load_context",         node_load_context)
    graph.add_node("rag_search",           node_rag_search)
    graph.add_node("load_advisor_history", node_load_advisor_history)
    graph.add_node("generate_conseil",     node_generate_conseil)
    graph.add_node("save_conseil",         node_save_conseil)

    graph.set_entry_point("load_context")
    graph.add_edge("load_context",         "rag_search")
    graph.add_edge("rag_search",           "load_advisor_history")
    graph.add_edge("load_advisor_history", "generate_conseil")
    graph.add_edge("generate_conseil",     "save_conseil")
    graph.add_edge("save_conseil",         END)

    logger.info("[COACH] Graphe LangGraph v3 — 5 nodes")
    return graph


def compile_coach_agent(checkpointer=None):
    return build_coach_graph().compile(checkpointer=checkpointer)


_coach_agent = None

def get_coach_agent():
    global _coach_agent
    if _coach_agent is None:
        _coach_agent = compile_coach_agent()
    return _coach_agent