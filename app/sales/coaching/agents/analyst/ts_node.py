"""
ts_node.py — Node LangGraph de l'Analyste temps réel (déterministe).
====================================================================
Remplace node_react_analyst : plus aucune boucle LLM dans le chemin critique.
Le moteur ts_engine fait 100% du travail analytique (< 1s) :
  forecast EOD (Holt-Winters saisonnier + déroulé intraday), détection de gap
  horaire, prévision heures suivantes + demain, urgence, faisabilité.

Un enrichissement LLM du résumé est possible via ANALYST_LLM_SUMMARY=1
(timeout borné, jamais bloquant — le résumé statistique reste le fallback).
"""

import asyncio
import logging
import os
import time
from datetime import datetime

from app.core.config import DEFAULT_STORE_ID
from app.sales.core.state import SalesAgentState
from app.sales.data.postgres_provider import normalize_store_id
from .ts_engine import analyze_store
from .nodes import AgentLogger, _cycle_id, _store_id, _get_or_create_trace, _lf_span

logger = logging.getLogger(__name__)

LLM_SUMMARY_ENABLED = os.getenv("ANALYST_LLM_SUMMARY", "0") == "1"
LLM_SUMMARY_TIMEOUT = float(os.getenv("ANALYST_LLM_TIMEOUT", "8"))


async def _maybe_enrich_summary(analysis: dict) -> str:
    """Résumé LLM optionnel, borné dans le temps. Fallback : résumé statistique."""
    base = analysis.get("summary", "")
    if not LLM_SUMMARY_ENABLED:
        return base
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_ollama import ChatOllama
        llm = ChatOllama(
            model=os.getenv("OLLAMA_MODEL", "llama3.2:latest"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=0.1, num_predict=200, num_ctx=2048,
        )
        gaps = ", ".join(
            f"{e['hour']}h {e['deviation_pct']:+.0f}%" for e in analysis.get("hourly_gaps", [])[:4]
        ) or "aucun"
        prompt = (
            f"Analyse Ooredoo {analysis['store_id']} à {analysis['analysis_hour']}h : "
            f"CA {analysis['current_ca']:.0f}/{analysis['daily_target']:.0f} TND, "
            f"EOD prévu {analysis['eod_forecast']:.0f} TND (MAPE {analysis['mape_backtest']}%), "
            f"gap {analysis['gap_pct']}%, tendance {analysis['trend_signal']}, "
            f"heures en retard : {gaps}, faisabilité {analysis['feasibility']}. "
            f"Rédige un résumé analyste en 2 phrases françaises, factuel, orienté action."
        )
        resp = await asyncio.wait_for(
            llm.ainvoke([SystemMessage(content="Tu es analyste retail senior."),
                         HumanMessage(content=prompt)]),
            timeout=LLM_SUMMARY_TIMEOUT,
        )
        text = (resp.content or "").strip()
        return text if 30 < len(text) < 600 else base
    except Exception as e:
        logger.debug("[TS-NODE] LLM summary skipped: %s", e)
        return base


async def node_ts_analysis(state: SalesAgentState) -> dict:
    """Analyse temps réel déterministe — remplace 6 nodes + boucle ReAct."""
    cid = _cycle_id(state)
    sid = _store_id(state)
    log = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("ts_analysis", state)
    t0 = time.time()
    trace = _get_or_create_trace(state)

    pos_data = state.get("pos_data") or {}

    try:
        analysis = await analyze_store(sid)
        if analysis.get("error"):
            raise RuntimeError(analysis["error"])
    except Exception as e:
        duration = (time.time() - t0) * 1000
        log.node_error("ts_analysis", log_id, e, state)
        _lf_span(trace, "ts_analysis", {"store_id": sid},
                 {"error": str(e)[:200]}, duration, "ERROR")
        logger.warning("[TS-NODE] analyze_store failed (%s) — fallback linéaire", e)
        analysis = _linear_fallback(sid, pos_data)

    summary = await _maybe_enrich_summary(analysis)
    duration = (time.time() - t0) * 1000

    # Compatibilité : timesfm_prediction reste consommé par coach/API existants
    prediction_compat = {
        "forecast_end_of_day":     analysis["eod_forecast"],
        "forecast_end_of_day_tnd": analysis["eod_forecast"],
        "confidence_interval": {"low": analysis["eod_ci_low"], "high": analysis["eod_ci_high"]},
        "mape":            analysis["mape_backtest"],
        "model_version":   analysis.get("model_engine", "ts_engine"),
        "tomorrow":        analysis.get("tomorrow_forecast", 0),
        "source":          "ts_engine",
    }

    _lf_span(trace, "ts_analysis",
        input_data={"store_id": sid, "hour": analysis.get("analysis_hour")},
        output_data={
            "eod":       analysis["eod_forecast"],
            "mape":      analysis["mape_backtest"],
            "engine":    analysis.get("model_engine"),
            "gap_pct":   analysis["gap_pct"],
            "urgency":   analysis["urgency_level"],
            "trend":     analysis.get("trend_signal"),
            "alert_hours": analysis.get("nb_alert_hours", 0),
        },
        latency_ms=duration,
        level="WARNING" if analysis["urgency_level"] in ("HIGH", "CRITICAL") else "DEFAULT",
    )
    log.node_done("ts_analysis", log_id, analysis, duration, {
        "eod": analysis["eod_forecast"], "gap_pct": analysis["gap_pct"],
        "urgency": analysis["urgency_level"], "engine": analysis.get("model_engine"),
    })
    logger.info(
        "[ANALYST TS] %s | EOD=%.0f [%s–%s] TND (MAPE %.1f%%, %s) | gap=%.1f%% | "
        "urgence=%s | tendance=%s | %d h en retard | %.0fms",
        sid, analysis["eod_forecast"], analysis["eod_ci_low"], analysis["eod_ci_high"],
        analysis["mape_backtest"], analysis.get("model_engine", "?"),
        analysis["gap_pct"], analysis["urgency_level"],
        analysis.get("trend_signal", "?"), len(analysis.get("hourly_gaps", [])), duration,
    )

    metrics = dict(state.get("metrics") or {})
    metrics["analyste_ts_ms"] = round(duration)
    metrics["ts_engine"] = analysis.get("model_engine", "unknown")
    metrics["nodes_executed"] = int(metrics.get("nodes_executed", 0)) + 1

    return {
        **state,
        # Champs consommés par build_strategy_query / save_memory / stratège
        "urgency_level":   analysis["urgency_level"],
        "urgency_score":   float(analysis["urgency_score"]),
        "gap_objectif":    float(analysis["gap_pct"]),
        "gap_amount":      float(analysis["gap_eod"]),
        "forecast_eod":    float(analysis["eod_forecast"]),
        "forecast_mape":   float(analysis["mape_backtest"]),
        "coverage":        float(analysis["coverage_pct"]),
        "attainment":      float(analysis.get("attainment_pct", 0)),
        "analyst_summary": summary,
        "timesfm_prediction": prediction_compat,
        "route_to":        "strategie",
        # Nouveaux signaux temps réel
        "ts_analysis":     analysis,
        "trend_signal":    analysis.get("trend_signal", "UNKNOWN"),
        "hourly_gaps":     analysis.get("hourly_gaps", []),
        "next_hours_forecast": analysis.get("next_hours", []),
        "feasibility":     analysis.get("feasibility", "UNKNOWN"),
        # Compat anciens consommateurs
        "analysis_features": {
            "nb_transactions": int(pos_data.get("nb_transactions_today", 0)),
            "avg_ticket":      float(pos_data.get("avg_ticket", 0)),
            "gap_pct":         float(analysis["gap_pct"]),
            "hourly_trend":    [{"hour": e["hour"], "revenue": e["actual"]}
                                for e in analysis.get("hourly_ledger", [])],
            "top_categories":  (state.get("analysis_features") or {}).get("top_categories", []),
            "top_products":    (state.get("analysis_features") or {}).get("top_products", []),
        },
        "metrics": metrics,
    }


def _linear_fallback(sid: str, pos_data: dict) -> dict:
    """Dernier recours si PostgreSQL est injoignable : extrapolation simple."""
    ca = float(pos_data.get("current_revenue", 0) or 0)
    target = float(pos_data.get("daily_target", 1007) or 1007)
    now_h = datetime.now().hour
    hours_left = max(0, 20 - now_h)
    rate = ca / max(1, now_h - 8)
    eod = round(ca + rate * hours_left, 2)
    gap = round(max(0.0, target - eod), 2)
    gap_pct = round(gap / target * 100, 1) if target > 0 else 0.0
    urgency = "HIGH" if gap_pct > 30 else "MEDIUM" if gap_pct > 15 else "LOW"
    return {
        "store_id": sid, "business_date": None, "analysis_hour": now_h,
        "current_ca": ca, "daily_target": target,
        "attainment_pct": round(ca / target * 100, 1) if target > 0 else 0,
        "eod_forecast": eod, "eod_model": eod, "eod_unfold": eod,
        "eod_ci_low": round(eod * 0.85, 2), "eod_ci_high": round(eod * 1.15, 2),
        "tomorrow_forecast": 0.0, "mape_backtest": 30.0,
        "model_engine": "linear_fallback", "model_params": {},
        "history_days": 0, "profile_source": "none", "profile_days": 0,
        "gap_eod": gap, "gap_pct": gap_pct,
        "coverage_pct": round(eod / target * 100, 1) if target > 0 else 0,
        "hours_remaining": hours_left, "ca_per_hour_needed": None,
        "catchup_capacity": 0.0, "feasibility": "UNKNOWN",
        "hourly_ledger": [], "hourly_gaps": [], "nb_alert_hours": 0,
        "trend_signal": "UNKNOWN", "next_hours": [],
        "urgency_level": urgency, "urgency_score": min(1.0, gap_pct / 50),
        "summary": (
            f"[Mode dégradé] CA {ca:.0f}/{target:.0f} TND. "
            f"EOD estimé {eod:.0f} TND (extrapolation linéaire). Gap {gap_pct:.1f}%."
        ),
    }
