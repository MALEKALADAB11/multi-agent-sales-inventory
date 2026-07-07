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

from app.sales.core.state import SalesAgentState
from .react_tools import REACT_ANALYST_TOOLS
from app.core.config import DEFAULT_STORE_ID

logger = logging.getLogger(__name__)

OLLAMA_URL        = os.getenv("OLLAMA_BASE_URL",   "http://localhost:11434")
OLLAMA_MODEL      = os.getenv("OLLAMA_MODEL",      "llama3.2:latest")
ANALYST_LLM_MODEL = os.getenv("ANALYST_LLM_MODEL", "openai/gpt-4o-mini")

MAX_REACT_STEPS  = 4
CLOSING_HOUR     = 20


# ══════════════════════════════════════════════════════════════════════════════
# LLM Factory — OpenRouter FAST tier (outil calling rapide) ou Ollama fallback
# ══════════════════════════════════════════════════════════════════════════════

def _build_llm():
    """
    Construit le LLM pour l'AnalystAgent ReAct.
    Force Ollama local — mesuré empiriquement plus rapide (~46s) que le tier
    gratuit OpenRouter fast (nemotron-120b, 80-133s constaté) pour une boucle
    ReAct qui fait jusqu'à MAX_REACT_STEPS aller-retours réseau séquentiels.
    Un modèle 120B partagé/gratuit est un mauvais choix pour ce rôle — le reste
    du pipeline (stratège, guardrail, coach) continue d'utiliser OpenRouter via
    LLM_PROVIDER=openrouter, qui reste adapté aux appels uniques.
    """
    try:
        from langchain_ollama import ChatOllama
        return ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_URL, temperature=0.0,
                          num_predict=400, num_ctx=2048)
    except ImportError:
        pass

    # Fallback : factory centralisée (OpenRouter) si Ollama indisponible
    try:
        from app.inventory.utils.llm_factory import get_fast_llm
        return get_fast_llm()
    except Exception:
        return None


def _has_tool_calling() -> bool:
    return os.getenv("OPENROUTER_API_KEY", "") != "" or os.getenv("LLM_PROVIDER", "") == "openrouter"


# ══════════════════════════════════════════════════════════════════════════════
# Système de prompt ReAct
# ══════════════════════════════════════════════════════════════════════════════

REACT_SYSTEM_PROMPT = """\
Tu es l'Agent Analyste Séries Temporelles Robuste pour Ooredoo Tunisie (magasin {store_id}).
Objectif journalier : {daily_target} TND. Heure actuelle : {current_time}.

Tu disposes de 11 outils d'analyse statistique avancée. Ta mission : produire une analyse
temps réel complète couvrant performance actuelle, prévisions multi-horizon, anomalies et stock.

## CYCLE REACT — à chaque step :
1. Observe les données disponibles
2. Raisonne : que manque-t-il ?
3. Appelle un outil pour obtenir l'information
4. Observe le résultat et itère

## OUTILS DISPONIBLES :
### Bloc 1 — Performance instantanée (appelle dans cet ordre)
- fetch_live_pos            : CA actuel, TX, panier moyen — PREMIER APPEL OBLIGATOIRE
- compute_eod_forecast      : Prévision EOD ensemble 4 méthodes (linéaire+saisonnier+vélocité+TimesFM)
- compute_realtime_gap      : Gap vs objectif + urgency_score + faisabilité

### Bloc 2 — Analyse intraday
- get_intraday_trend        : Vélocité, accélération, z-score heure courante, peak hours
- get_historical_comparison : Comparaison même DOW sur 4 semaines

### Bloc 3 — Séries temporelles avancées (NOUVEAUX)
- detect_sales_anomalies    : Détection anomalies z-score sur 28j de données horaires
- compute_ts_decomposition  : STL-like : tendance 7j + saisonnalité DOW + autocorrélation lag-7
- forecast_multi_horizon    : Prévisions J+1h, J+3h, EOD, demain avec IC à 80%

### Bloc 4 — Stock & Vélocité produit (lien temps réel ventes→stock)
- get_stock_alerts          : Ruptures + CA à risque
- analyze_product_velocity  : Vélocité SKU (unités/jour) + jours avant rupture par produit

### Bloc 5 — Contexte saisonnier
- get_seasonal_context      : Facteurs saisonniers 3 ans (DOW, mois, événements Ramadan/Eid)

## STRATÉGIE D'APPEL (MAX {max_steps} APPELS) :
1. fetch_live_pos → toujours premier
2. compute_eod_forecast (avec current_ca de l'étape 1)
3. compute_realtime_gap (avec eod_weighted + daily_target)
4. analyze_product_velocity → lien ventes temps réel ↔ stock
5. detect_sales_anomalies OU compute_ts_decomposition selon urgence
6. forecast_multi_horizon pour planification multi-horizon

## RÈGLES MÉTIER :
- Si stock_urgency_boost > 0 : ajoute-le à urgency_score (max 1.0)
- Si nb_ruptures >= 2 et urgency_level "LOW" : monte à "MEDIUM"
- Si trend_signal "DECELERATING" et gap_pct > 20 : monte urgence d'un niveau
- Si overall_z_score > 2 (journée atypique) : mentionne-le dans analyst_summary
- Si days_to_stockout <= 2 pour un top-produit : inclure dans strategy_query

## FORMAT DE CONCLUSION (JSON strict, dernier message) :
{{
  "urgency_level":       "LOW|MEDIUM|HIGH|CRITICAL",
  "urgency_score":       0.0..1.0,
  "gap_pct":             float,
  "gap_amount":          float,
  "eod_forecast":        float,
  "daily_target":        float,
  "coverage_pct":        float,
  "nb_ruptures":         int,
  "nb_critical_stock":   int,
  "revenue_at_risk_tnd": float,
  "trend_signal":        "ACCELERATING|DECELERATING|STABLE|UNKNOWN",
  "seasonal_factor":     float,
  "autocorrelation_lag7": float,
  "is_atypical_day":     bool,
  "overall_z_score":     float,
  "forecast_eod_ci_low":  float,
  "forecast_eod_ci_high": float,
  "nb_urgent_products":  int,
  "analyst_summary":     "résumé en français 2-3 phrases : performance + tendance + anomalies + stock urgents",
  "strategy_query":      "requête pour le stratège : gap + tendance + produits urgents + contexte saisonnier"
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
Tu es l'Agent Analyste Séries Temporelles Robuste Ooredoo {store_id}.
Objectif: {daily_target} TND | Heure: {current_time}

À chaque step, réponds avec UN JSON parmi :
  {{"thought":"...", "action":"nom_outil", "args":{{"param":"valeur"}}}}
  ou pour conclure :
  {{"thought":"...", "action":"FINISH", "result":{{...résultat final...}}}}

Outils disponibles:
  fetch_live_pos, compute_eod_forecast, compute_realtime_gap,
  get_intraday_trend, get_seasonal_context, get_historical_comparison, get_stock_alerts,
  detect_sales_anomalies, compute_ts_decomposition, forecast_multi_horizon, analyze_product_velocity

Ordre recommandé: fetch_live_pos → compute_eod_forecast → compute_realtime_gap →
                  analyze_product_velocity → detect_sales_anomalies → forecast_multi_horizon
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
    store_id = state.get("store_id") or state.get("pos_data", {}).get("store_id", DEFAULT_STORE_ID)
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

    # Fallback si réponse vide, incomplète, ou EOD aberrant
    ca_now = float(pos_data.get("current_revenue", 0) or 0)
    eod_returned = float(react_result.get("eod_forecast", 0) or 0)
    if not react_result.get("urgency_level") or eod_returned <= 0 or eod_returned < ca_now * 0.5:
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
        # Séries temporelles avancées
        "seasonal_factor":    float(react_result.get("seasonal_factor", 1.0)),
        "trend_signal":       react_result.get("trend_signal", "UNKNOWN"),
        "autocorrelation_lag7": float(react_result.get("autocorrelation_lag7", 0.0)),
        "is_atypical_day":    bool(react_result.get("is_atypical_day", False)),
        "overall_z_score":    float(react_result.get("overall_z_score", 0.0)),
        "forecast_ci": {
            "eod_ci_low":  float(react_result.get("forecast_eod_ci_low", 0)),
            "eod_ci_high": float(react_result.get("forecast_eod_ci_high", 0)),
        },
        "react_source":       react_result.get("_source", "unknown"),
        # Stock + vélocité produit (lien ventes→stock temps réel)
        "inventory_snapshot": {
            "nb_ruptures":         int(react_result.get("nb_ruptures", 0)),
            "nb_critical_stock":   int(react_result.get("nb_critical_stock", 0)),
            "revenue_at_risk_tnd": float(react_result.get("revenue_at_risk_tnd", 0.0)),
            "nb_urgent_products":  int(react_result.get("nb_urgent_products", 0)),
        },
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
