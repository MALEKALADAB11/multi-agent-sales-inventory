"""
react_analyst.py — Agent Analyste ReAct (Reason + Act) temps réel.

Remplace les 6 nodes statiques (feature_engineering → detect_urgency → llm_summary)
par un cycle ReAct autonome :

    Observation → Thought → Action(outil) → Observation → … → Final Answer

Le LLM choisit dynamiquement quels outils appeler, dans quel ordre,
et recalcule gap/EOD à chaque itération depuis le POS live.

LLM : OpenRouter gpt-4o-mini (tool calling rapide) avec fallback Ollama.
Max   : 6 itérations pour rester sous 30s en prod.
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from core.state import SalesAgentState
from .react_tools import REACT_ANALYST_TOOLS

logger = logging.getLogger(__name__)

OLLAMA_URL   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL",   "llama3.2:latest")

MAX_REACT_STEPS  = 6
CLOSING_HOUR     = 20


# ══════════════════════════════════════════════════════════════════════════════
# LLM Factory — OpenRouter FAST tier (outil calling rapide) ou Ollama fallback
# ══════════════════════════════════════════════════════════════════════════════

def _build_llm():
    """
    Construit le LLM pour l'AnalystAgent ReAct.
    Utilise la factory centralisée (OpenRouter FAST tier) avec fallback Ollama.
    """
    import sys
    from pathlib import Path
    # Accès à la factory inventory-module si path non encore chargé
    inv_path = str(Path(__file__).resolve().parents[6] / "inventory-module")
    if inv_path not in sys.path:
        sys.path.insert(0, inv_path)

    try:
        from src.utils.llm_factory import get_fast_llm
        return get_fast_llm()
    except Exception:
        pass

    # Fallback : Ollama si llm_factory indisponible
    try:
        from langchain_ollama import ChatOllama
        return ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_URL, temperature=0.0,
                          num_predict=400, num_ctx=2048)
    except ImportError:
        return None


def _has_tool_calling() -> bool:
    return os.getenv("OPENROUTER_API_KEY", "") != "" or os.getenv("LLM_PROVIDER", "") == "openrouter"


# ══════════════════════════════════════════════════════════════════════════════
# Système de prompt ReAct
# ══════════════════════════════════════════════════════════════════════════════

REACT_SYSTEM_PROMPT = """\
Tu es l'Agent Analyste Séries Temporelles pour Ooredoo Tunisie (magasin {store_id}).
Objectif journalier : {daily_target} TND. Heure actuelle : {current_time}.

## CYCLE REACT — à chaque step :
1. Observe les données disponibles
2. Raisonne : que manque-t-il ?
3. Appelle un outil pour obtenir l'information
4. Observe le résultat et itère

## OUTILS DISPONIBLES :
- fetch_live_pos          : CA actuel, TX, panier moyen — appelle EN PREMIER et si tu veux rafraîchir
- compute_eod_forecast    : Prévision EOD (linéaire + saisonnier + TimesFM)
- compute_realtime_gap    : Gap vs objectif + urgency_score — appelle APRÈS forecast
- get_intraday_trend      : Vélocité et accélération de l'heure courante
- get_seasonal_context    : Facteurs saisonniers (DOW, mois, événements) depuis 3 ans d'historique
- get_historical_comparison : Comparaison avec même jour semaine passée

## RÈGLES :
- Appelle toujours fetch_live_pos en premier
- Appelle compute_eod_forecast avec le CA retourné par fetch_live_pos
- Appelle compute_realtime_gap avec l'eod_weighted de compute_eod_forecast
- Enrichis avec get_seasonal_context et get_intraday_trend pour affiner l'analyse
- Max {max_steps} appels d'outils au total

## FORMAT DE CONCLUSION (JSON strict, dernier message) :
{{
  "urgency_level":  "LOW|MEDIUM|HIGH|CRITICAL",
  "urgency_score":  0.0..1.0,
  "gap_pct":        float,
  "gap_amount":     float,
  "eod_forecast":   float,
  "daily_target":   float,
  "coverage_pct":   float,
  "analyst_summary": "résumé en français pour les conseillers (2-3 phrases)",
  "strategy_query": "requête courte pour l'agent stratège",
  "seasonal_factor": float,
  "trend_signal":   "ACCELERATING|DECELERATING|STABLE|UNKNOWN"
}}
"""


# ══════════════════════════════════════════════════════════════════════════════
# Dispatching des outils (compatible async)
# ══════════════════════════════════════════════════════════════════════════════

_TOOL_MAP = {t.name: t for t in REACT_ANALYST_TOOLS}


async def _call_tool(tool_name: str, tool_args: dict) -> str:
    t = _TOOL_MAP.get(tool_name)
    if t is None:
        return json.dumps({"error": f"unknown tool: {tool_name}"})
    try:
        result = await t.ainvoke(tool_args)
        return result if isinstance(result, str) else json.dumps(result, default=str)
    except Exception as e:
        logger.warning("[react_analyst/tool] %s(%s): %s", tool_name, tool_args, e)
        return json.dumps({"error": str(e)})


# ══════════════════════════════════════════════════════════════════════════════
# Mode A — ReAct avec tool calling natif (OpenRouter)
# ══════════════════════════════════════════════════════════════════════════════

async def _react_with_tool_calling(store_id: str, daily_target: float, llm) -> dict:
    from langgraph.prebuilt import create_react_agent

    system = REACT_SYSTEM_PROMPT.format(
        store_id=store_id,
        daily_target=int(daily_target),
        current_time=datetime.now().strftime("%H:%M"),
        max_steps=MAX_REACT_STEPS,
    )

    agent = create_react_agent(llm, REACT_ANALYST_TOOLS)

    result = await agent.ainvoke({
        "messages": [
            SystemMessage(content=system),
            HumanMessage(content=(
                f"Analyse les ventes du magasin {store_id} maintenant. "
                f"Objectif: {daily_target} TND. "
                f"Commence par fetch_live_pos, puis calcule le forecast et le gap. "
                f"Conclus avec le JSON final."
            )),
        ]
    }, config={"recursion_limit": MAX_REACT_STEPS * 2 + 2})

    # Extraire le JSON final du dernier message AI
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if hasattr(msg, "content") and isinstance(msg.content, str):
            content = msg.content.strip()
            if "{" in content and "urgency_level" in content:
                start = content.find("{")
                end   = content.rfind("}") + 1
                try:
                    return json.loads(content[start:end])
                except json.JSONDecodeError:
                    pass

    return {}


# ══════════════════════════════════════════════════════════════════════════════
# Mode B — ReAct manuel (Ollama, sans tool calling natif)
# ══════════════════════════════════════════════════════════════════════════════

MANUAL_REACT_PROMPT = """\
Tu es l'Agent Analyste Séries Temporelles Ooredoo {store_id}.
Objectif: {daily_target} TND | Heure: {current_time}

À chaque step, réponds avec UN JSON parmi :
  {{"thought":"...", "action":"nom_outil", "args":{{"param":"valeur"}}}}
  ou pour conclure :
  {{"thought":"...", "action":"FINISH", "result":{{...résultat final...}}}}

Outils: fetch_live_pos, compute_eod_forecast, compute_realtime_gap,
        get_intraday_trend, get_seasonal_context, get_historical_comparison

Règle: fetch_live_pos en premier, puis compute_eod_forecast, puis compute_realtime_gap.
Max {max_steps} steps.

Step 1 — commence maintenant :"""


async def _react_manual(store_id: str, daily_target: float, llm) -> dict:
    """ReAct loop manuel pour LLM sans function calling."""
    system = MANUAL_REACT_PROMPT.format(
        store_id=store_id,
        daily_target=int(daily_target),
        current_time=datetime.now().strftime("%H:%M"),
        max_steps=MAX_REACT_STEPS,
    )

    messages = [HumanMessage(content=system)]
    observations: list[str] = []

    for step in range(MAX_REACT_STEPS):
        try:
            response = await llm.ainvoke(messages)
            raw = response.content.strip() if response.content else ""
        except Exception as e:
            logger.warning("[react_analyst/manual] LLM invoke failed step %d: %s", step, e)
            break

        # Parser la réponse JSON du LLM
        parsed: dict = {}
        if "{" in raw:
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            try:
                parsed = json.loads(raw[start:end])
            except json.JSONDecodeError:
                pass

        action = parsed.get("action", "")
        args   = parsed.get("args", {})

        if action == "FINISH":
            return parsed.get("result", {})

        if not action or action not in _TOOL_MAP:
            # LLM confused — break with what we have
            if "result" in parsed:
                return parsed["result"]
            break

        # Appel de l'outil
        obs = await _call_tool(action, args)
        observations.append(f"[{action}] → {obs}")

        # Continuer la conversation
        messages.append(AIMessage(content=raw))
        messages.append(HumanMessage(content=f"Observation: {obs}\nStep {step+2}:"))

    # Fallback si la boucle se termine sans FINISH
    return {}


# ══════════════════════════════════════════════════════════════════════════════
# Fallback statique si ReAct échoue totalement
# ══════════════════════════════════════════════════════════════════════════════

def _fallback_result(store_id: str, pos_data: dict) -> dict:
    ca     = float(pos_data.get("current_revenue", 0) or 0)
    target = float(pos_data.get("daily_target", 1007) or 1007)
    now_h  = datetime.now().hour
    hours_left = max(0, CLOSING_HOUR - now_h)
    hours_done = max(1, now_h - 9)
    rate   = ca / max(hours_done, 1)
    eod    = ca + rate * hours_left
    gap    = max(0.0, target - eod)
    gap_pct = round(gap / max(target, 1) * 100, 1)

    if gap_pct > 30:
        urgency, score = "HIGH", 0.75
    elif gap_pct > 15:
        urgency, score = "MEDIUM", 0.50
    else:
        urgency, score = "LOW", 0.20

    return {
        "urgency_level":  urgency,
        "urgency_score":  score,
        "gap_pct":        gap_pct,
        "gap_amount":     round(gap, 2),
        "eod_forecast":   round(eod),
        "daily_target":   target,
        "coverage_pct":   round(eod / max(target, 1) * 100, 1),
        "analyst_summary": (
            f"CA {ca:.0f}/{target:.0f} TND (EOD estimé {eod:.0f} TND). "
            f"Gap {gap_pct:.1f}% — urgence {urgency}."
        ),
        "strategy_query": f"gap {gap_pct:.0f}% urgence {urgency}",
        "seasonal_factor": 1.0,
        "trend_signal":   "UNKNOWN",
        "_source":        "fallback_static",
    }


# ══════════════════════════════════════════════════════════════════════════════
# NODE principal — inséré dans le graphe LangGraph
# ══════════════════════════════════════════════════════════════════════════════

async def node_react_analyst(state: SalesAgentState) -> dict:
    """
    Remplace les 6 nodes statiques (feature_engineering … llm_summary)
    par un cycle ReAct autonome avec outils séries temporelles.
    """
    t0       = time.time()
    store_id = state.get("store_id") or state.get("pos_data", {}).get("store_id", "I63")
    pos_data = state.get("pos_data") or {}
    memory   = state.get("memory_insights") or {}
    target   = float(pos_data.get("daily_target", 1007) or 1007)

    logger.info("[ANALYST ReAct] Démarrage — store=%s target=%s TND", store_id, int(target))

    react_result: dict = {}
    llm_mode = "none"

    try:
        llm = _build_llm()
        if _has_tool_calling():
            llm_mode = f"openrouter/{ANALYST_LLM_MODEL}"
            react_result = await _react_with_tool_calling(store_id, target, llm)
        else:
            llm_mode = f"ollama/{OLLAMA_MODEL}/manual"
            react_result = await _react_manual(store_id, target, llm)
    except Exception as e:
        logger.warning("[ANALYST ReAct] LLM failed (%s): %s — using static fallback", llm_mode, e)

    # Fallback si réponse vide ou incomplète
    if not react_result.get("urgency_level"):
        react_result = _fallback_result(store_id, pos_data)
        react_result["_source"] = "fallback_static"
    else:
        react_result["_source"] = llm_mode

    elapsed = round((time.time() - t0) * 1000)
    logger.info(
        "[ANALYST ReAct] ✓ %s | urgence=%s | gap=%.1f%% | EOD=%s TND | %dms",
        store_id,
        react_result.get("urgency_level", "?"),
        float(react_result.get("gap_pct", 0)),
        int(react_result.get("eod_forecast", 0)),
        elapsed,
    )

    # Fusionner avec le state existant
    return {
        **state,
        # Champs attendus par les nodes suivants (build_strategy_query, save_memory)
        "urgency_level":      react_result.get("urgency_level", "LOW"),
        "urgency_score":      float(react_result.get("urgency_score", 0.2)),
        "gap_objectif":       float(react_result.get("gap_pct", 0)),
        "gap_amount":         float(react_result.get("gap_amount", 0)),
        "forecast_eod":       float(react_result.get("eod_forecast", 0)),
        "coverage":           float(react_result.get("coverage_pct", 100)),
        "analyst_summary":    react_result.get("analyst_summary", ""),
        "route_to":           "strategie",
        # Champs enrichis pour le stratège
        "seasonal_factor":    float(react_result.get("seasonal_factor", 1.0)),
        "trend_signal":       react_result.get("trend_signal", "UNKNOWN"),
        "react_source":       react_result.get("_source", "unknown"),
        # Champs compatibilité anciens nodes (pour save_memory)
        "analysis_features":  {
            "nb_transactions": int(pos_data.get("nb_transactions_today", 0)),
            "avg_ticket":      float(pos_data.get("avg_ticket", 0)),
            "gap_pct":         float(react_result.get("gap_pct", 0)),
            "top_categories":  pos_data.get("top_categories", []),
            "hourly_trend":    pos_data.get("hourly_ca", {}),
        },
        "metrics": {
            **(state.get("metrics") or {}),
            "react_ms":        elapsed,
            "react_steps":     react_result.get("_steps", 0),
            "llm_mode":        llm_mode,
        },
    }
