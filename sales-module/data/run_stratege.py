import asyncio
import logging
from dotenv import load_dotenv

load_dotenv()

# ── Format correct ────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%H:%M:%S"
)

from data.mock_provider import get_data_provider
from modules.coaching.agents.analyst.agent import get_analyst_agent
from modules.coaching.agents.stratege.agent import get_stratege_agent

async def main():
    store_id = "OOR_LAC_01"
    provider = get_data_provider()

    pos_data    = await provider.fetch_pos_data(store_id)
    pos_history = await provider.fetch_pos_history(store_id)
    prediction  = await provider.fetch_timesfm_prediction(store_id)

    initial_state = {
        "pos_data":           pos_data,
        "pos_history":        pos_history,
        "timesfm_prediction": prediction,
        "feedback_history":   [],
    }

    # ── Agent Analyste ────────────────────────────────────
    print("\n" + "="*60)
    print("  🔍 AGENT ANALYSTE")
    print("="*60)
    analyst = get_analyst_agent()
    state   = await analyst.ainvoke(initial_state)
    print(f"  Urgence  : {state['urgency_level']}")
    print(f"  Gap      : {state['gap_objectif']:.1f}%")
    print(f"  Résumé   : {state.get('analyst_summary','')[:80]}")

    # ── Agent Stratège ────────────────────────────────────
    print("\n" + "="*60)
    print("  🎯 AGENT STRATÈGE")
    print("="*60)
    stratege = get_stratege_agent()
    state    = await stratege.ainvoke(state)

    ctx = state.get("external_context", {}).get("summary", {})
    print(f"  Météo      : {ctx.get('weather_icon','?')} {ctx.get('weather_label','?')}")
    print(f"  Effet      : {ctx.get('weather_effect', 0):+.0%}")
    print(f"  Férié      : {ctx.get('is_holiday', False)}")
    print(f"  Cause      : {state.get('cause_racine','?')}")
    print(f"  Stratégie  : {state.get('strategie','?')[:100]}")
    print()
    print("  Actions :")
    for a in state.get("strategie_actions", []):
        print(f"    {a.get('priorite','')}) {a.get('action','')}")
        print(f"       → {a.get('produit_cible','')}")
    print()
    print("  Heatmap réelle :")
    hm = state.get("context_heatmap", {})
    print(f"    Weather : {hm.get('weather','?')}")
    print(f"    Traffic : {hm.get('traffic','?')}")
    print(f"    Risk    : {hm.get('risk','?')}")
    print()
    print("  Signaux contextuels :")
    for s in state.get("context_signals", []):
        print(f"    [{s.get('level','').upper()}] {s.get('label','')}")

asyncio.run(main())