"""
Script de test + affichage graphe LangGraph — Agent Analyste Ooredoo.
"""
import asyncio
import logging
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)

from modules.coaching.agents.analyst.agent import build_analyst_graph
from data.postgres_provider import get_data_provider
from modules.coaching.agents.analyst.agent import get_analyst_agent


# ─────────────────────────────────────────────
# Affichage du graphe LangGraph
# ─────────────────────────────────────────────

def display_graph():
    print("\n" + "="*60)
    print("  GRAPHE LANGGRAPH — AGENT ANALYSTE")
    print("="*60)

    graph = build_analyst_graph()
    compiled = graph.compile()

    # Affichage ASCII dans le terminal
    compiled.get_graph().print_ascii()

   


# ─────────────────────────────────────────────
# Test agent
# ─────────────────────────────────────────────

async def test_store(store_id: str):
    print(f"\n{'='*60}")
    print(f"  TEST — {store_id}")
    print(f"{'='*60}")

    provider = get_data_provider()
    pos_data = await provider.fetch_pos_data(store_id)

    initial_state = {
        "pos_data": pos_data,
        "pos_history": [],
        "timesfm_prediction": None,
        "feedback_history": [],
    }

    agent = get_analyst_agent()
    result = await agent.ainvoke(initial_state)

    print(f"  Store       : {pos_data.get('store_name', store_id)}")
    print(f"  CA actuel   : {pos_data.get('current_revenue', 0):,.0f} TND")
    print(f"  Objectif    : {pos_data.get('daily_target', 0):,.0f} TND")
    print(f"  Gap         : {result.get('gap_objectif', 0):.1f}%")
    print(f"  Urgence     : {result.get('urgency_level', 'N/A')}")
    print(f"  Score       : {result.get('urgency_score', 0)}")
    print(f"  Route vers  : {result.get('route_to', 'N/A')}")
    print(f"  Résumé      : {result.get('analyst_summary', 'N/A')}")


async def main():
    # 1. Affiche le graphe
    display_graph()

    # 2. Lance le test sur un store
    await test_store("OOR_LAC_01")


asyncio.run(main())
