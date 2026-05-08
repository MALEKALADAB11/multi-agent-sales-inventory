"""
Nodes LangGraph de l'Agent Analyste.
"""
import json
import logging
from datetime import datetime
import os

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from core.config import get_config
from core.state import SalesAgentState
from data.mock_provider import get_data_provider
from .prompts import ANALYST_SYSTEM_PROMPT, ANALYST_USER_PROMPT

logger = logging.getLogger(__name__)
config = get_config()


# ─────────────────────────────────────────────
# LLM — Ollama (défini une seule fois)
# ─────────────────────────────────────────────

def get_llm() -> ChatOllama:
    """Retourne une instance ChatOllama avec le modèle configuré."""
    return ChatOllama(
        model=os.getenv("OLLAMA_MODEL", "llama3.2:latest"),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        temperature=0.1,
        num_predict=200,
        num_ctx=1024,
    )


# ─────────────────────────────────────────────
# Node 1 — Réception POS
# ─────────────────────────────────────────────

async def node_receive_pos(state: SalesAgentState) -> dict:
    pos_data = state.get("pos_data", {})
    store_id = pos_data.get("store_id", "OOR_LAC_01")

    logger.info(f"[ANALYST] Node 1 — Réception POS store={store_id}")

    provider = get_data_provider()

    if not pos_data.get("daily_target"):
        pos_data = await provider.fetch_pos_data(store_id)

    pos_history = state.get("pos_history", [])
    if not pos_history:
        pos_history = await provider.fetch_pos_history(store_id)

    logger.info(f"[ANALYST] Node 1 — {len(pos_history)} transactions chargées")

    return {
        "pos_data":    pos_data,
        "pos_history": pos_history,
    }


# ─────────────────────────────────────────────
# Node 2 — Calcul Gap
# ─────────────────────────────────────────────

async def node_compute_gap(state: SalesAgentState) -> dict:
    pos_data    = state.get("pos_data", {})
    pos_history = state.get("pos_history", [])

    current_revenue = pos_data.get("current_revenue", 0.0)
    daily_target    = pos_data.get("daily_target", config.default_daily_target)

    total_tx          = len(pos_history)
    revenue_last_hour = sum(
        tx.get("revenue", 0) for tx in pos_history
        if tx.get("minutes_ago", 999) <= 60
    )
    revenue_last_2h   = sum(
        tx.get("revenue", 0) for tx in pos_history
        if tx.get("minutes_ago", 999) <= 120
    )

    by_category: dict[str, float] = {}
    for tx in pos_history:
        cat = tx.get("product_category", "Autre")
        by_category[cat] = by_category.get(cat, 0) + tx.get("revenue", 0)

    gap_amount = max(0.0, daily_target - current_revenue)
    gap_pct    = (gap_amount / daily_target * 100) if daily_target > 0 else 0.0

    history_summary = {
        "total_transactions":  total_tx,
        "total_revenue":       current_revenue,
        "revenue_last_hour":   revenue_last_hour,
        "revenue_last_2h":     revenue_last_2h,
        "avg_transaction_value": current_revenue / total_tx if total_tx > 0 else 0.0,
        "revenue_by_category": by_category,
    }

    logger.info(
        f"[ANALYST] Node 2 — Gap: {gap_pct:.1f}% "
        f"| Montant: {gap_amount:.0f} TND "
        f"| CA actuel: {current_revenue:.0f} TND"
    )

    return {
        "gap_objectif": gap_pct,
        "gap_amount":   gap_amount,
        "pos_data": {
            **pos_data,
            "history_summary": history_summary,
        }
    }


# ─────────────────────────────────────────────
# Node 3 — Prévision TimesFM
# ─────────────────────────────────────────────

async def node_call_timesfm(state: SalesAgentState) -> dict:
    pos_data = state.get("pos_data", {})
    store_id = pos_data.get("store_id", "OOR_LAC_01")

    logger.info(f"[ANALYST] Node 3 — Prévision TimesFM pour {store_id}")

    provider   = get_data_provider()
    prediction = await provider.fetch_timesfm_prediction(store_id)

    logger.info(
        f"[ANALYST] Node 3 — Prévision fin journée: "
        f"{prediction.get('forecast_end_of_day', 0):.0f} TND"
    )

    return {"timesfm_prediction": prediction}


# ─────────────────────────────────────────────
# Node 4 — Détection Urgence
# ─────────────────────────────────────────────

async def node_detect_urgency(state: SalesAgentState) -> dict:
    pos_data   = state.get("pos_data", {})
    prediction = state.get("timesfm_prediction", {})

    current_revenue = pos_data.get("current_revenue", 0.0)
    daily_target    = pos_data.get("daily_target", config.default_daily_target)
    forecast_eod    = prediction.get("forecast_end_of_day", current_revenue)

    gap_amount = max(0.0, daily_target - current_revenue)
    gap_pct    = (gap_amount / daily_target * 100) if daily_target > 0 else 0.0

    if gap_amount > 0:
        coverage_pct = min(100.0, ((forecast_eod - current_revenue) / gap_amount) * 100)
    else:
        coverage_pct = 100.0

    current_hour    = datetime.now().hour
    closing_hour    = pos_data.get("closing_hour", 20)
    hours_remaining = max(0.0, closing_hour - current_hour)

    time_pressure    = min(1.0, max(0.0, (current_hour - 8) / 10))
    gap_score        = min(1.0, gap_pct / 50.0)
    coverage_penalty = max(0.0, (100 - coverage_pct) / 100) * 0.3
    urgency_score    = round(
        min(1.0, (gap_score * 0.5) + (time_pressure * 0.3) + coverage_penalty), 3
    )

    if gap_pct > 30 and coverage_pct < 80:
        urgency_level = "HIGH"
    elif gap_pct > 15:
        urgency_level = "MEDIUM"
    else:
        urgency_level = "LOW"

    if hours_remaining < 2 and gap_pct > 10:
        urgency_level = "HIGH"
        urgency_score = max(urgency_score, 0.85)

    logger.info(
        f"[ANALYST] Node 4 — Urgence: {urgency_level} "
        f"| Score: {urgency_score} "
        f"| Couverture prévision: {coverage_pct:.1f}% "
        f"| Temps restant: {hours_remaining:.1f}h"
    )

    return {
        "urgency_level": urgency_level,
        "urgency_score": urgency_score,
        "gap_objectif":  gap_pct,
        "gap_amount":    gap_amount,
        "pos_data": {
            **pos_data,
            "forecast_end_of_day":       forecast_eod,
            "forecast_gap_coverage_pct": coverage_pct,
            "hours_remaining":           hours_remaining,
            "current_hour":              current_hour,
        }
    }


# ─────────────────────────────────────────────
# Node 5 — Synthèse LLM (Ollama)
# ─────────────────────────────────────────────

async def node_llm_summary(state: SalesAgentState) -> dict:
    pos_data       = state.get("pos_data", {})
    prediction     = state.get("timesfm_prediction", {})
    urgency_level  = state.get("urgency_level", "MEDIUM")
    gap_pct        = state.get("gap_objectif", 0.0)
    gap_amount     = state.get("gap_amount", 0.0)
    history_summary = pos_data.get("history_summary", {})

    user_msg = ANALYST_USER_PROMPT.format(
        pos_data=json.dumps({
            "store_id":            pos_data.get("store_id"),
            "store_name":          pos_data.get("store_name", ""),
            "current_revenue_tnd": pos_data.get("current_revenue"),
            "daily_target_tnd":    pos_data.get("daily_target"),
            "gap_pct":             round(gap_pct, 1),
            "gap_amount_tnd":      round(gap_amount, 0),
            "hours_remaining":     pos_data.get("hours_remaining"),
            "nb_transactions":     history_summary.get("total_transactions"),
            "avg_transaction_tnd": round(
                history_summary.get("avg_transaction_value", 0), 0
            ),
        }, indent=2),
        pos_history_summary=json.dumps(
            history_summary.get("revenue_by_category", {}), indent=2
        ),
        timesfm_prediction=json.dumps({
            "forecast_end_of_day_tnd": prediction.get("forecast_end_of_day"),
            "forecast_remaining_tnd":  prediction.get("forecast_remaining"),
            "coverage_pct":            round(
                pos_data.get("forecast_gap_coverage_pct", 0), 1
            ),
        }, indent=2),
        current_time=datetime.now().strftime("%H:%M"),
        daily_target=pos_data.get("daily_target", 0),
    )

    # ── Appel LLM ────────────────────────────────────────
    llm = get_llm()
    analyst_summary = ""

    # Remplacer uniquement le bloc try/parse JSON :

    try:
        logger.info("[ANALYST] Node 5 — Appel LLM Ollama Llama3...")
        response = await llm.ainvoke([
            SystemMessage(content=ANALYST_SYSTEM_PROMPT),
            HumanMessage(content=user_msg),
        ])

        content = response.content.strip()

        # Nettoyage markdown
        if "```" in content:
            parts   = content.split("```")
            content = parts[1] if len(parts) > 1 else parts[0]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        # ── Parse JSON → extraire analyst_summary ────────────
        try:
            analysis        = json.loads(content)
            analyst_summary = analysis.get("analyst_summary", "")
            key_insights    = analysis.get("key_insights", [])

            # Si vide → construire depuis les champs JSON
            if not analyst_summary:
                gap  = analysis.get("gap_percentage", gap_pct)
                eod  = analysis.get("timesfm_end_of_day_forecast", 0)
                ul   = analysis.get("urgency_level", urgency_level)
                analyst_summary = (
                    f"Gap de {gap:.1f}%. "
                    f"Forecast EOD : {eod:,.0f} TND. "
                    f"Urgence {ul}."
                )

            logger.info(
                f"[ANALYST] Node 5 — Synthèse OK | "
                f"{len(key_insights)} insights | "
                f"{len(analyst_summary)} chars"
            )

        except json.JSONDecodeError:
            # Texte libre → utiliser directement
            analyst_summary = content
            logger.info(f"[ANALYST] Node 5 — Synthèse OK (texte) | {len(analyst_summary)} chars")



    except Exception as e:
        logger.warning(f"[ANALYST] LLM fallback: {e}")

        forecast_eod = prediction.get("forecast_end_of_day", 0)
        hours_rem    = pos_data.get("hours_remaining", 0)

        if urgency_level == "HIGH":
            analyst_summary = (
                f"Gap critique de {gap_pct:.1f}% ({gap_amount:.0f} TND restants). "
                f"Prévision fin journée {forecast_eod:,.0f} TND — insuffisante. "
                f"Action immédiate requise sur les {hours_rem:.0f}h restantes."
            )
        elif urgency_level == "MEDIUM":
            analyst_summary = (
                f"Gap de {gap_pct:.1f}% à surveiller "
                f"({gap_amount:.0f} TND restants). "
                f"Prévision EOD : {forecast_eod:,.0f} TND. "
                f"Stratégie recommandée sur {hours_rem:.0f}h."
            )
        else:
            analyst_summary = (
                f"Performance correcte — gap {gap_pct:.1f}%. "
                f"CA en bonne progression. "
                f"Prévision EOD : {forecast_eod:,.0f} TND. "
                f"Objectif atteignable."
            )

    return {
        "analyst_summary": analyst_summary,
        "route_to":        "strategie",
    }


# ─────────────────────────────────────────────
# Routing conditionnel
# ─────────────────────────────────────────────

def route_after_analysis(state: SalesAgentState) -> str:
    urgency = state.get("urgency_level", "MEDIUM")
    gap_pct = state.get("gap_objectif", 0.0)

    if urgency == "HIGH" or gap_pct > 44:
        return "agent_stratege"
    elif urgency == "MEDIUM":
        return "agent_stratege"
    else:
        return "agent_coach"