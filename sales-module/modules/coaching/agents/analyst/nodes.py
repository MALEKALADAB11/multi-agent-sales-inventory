"""
nodes.py — Agent Analyste LangGraph
PostgreSQL + Logs + Analyst Memory + Fallback contrôlé.
"""

import json
import logging
import os
import time
from datetime import datetime

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from agent_logger import AgentLogger
from core.config import get_config
from core.state import SalesAgentState
from data.postgres_provider import get_data_provider, normalize_store_id
from .prompts import ANALYST_SYSTEM_PROMPT, ANALYST_USER_PROMPT
from .tools import (
    load_analyst_memory,
    save_analyst_memory,
    compare_with_memory,
    build_analyst_memory_payload,
)

logger = logging.getLogger(__name__)
config = get_config()

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:latest")


def _cycle_id(state: dict) -> str:
    return (state.get("metrics") or {}).get("cycle_id") or state.get("cycle_id", "unknown")


def _store_id(state: dict) -> str:
    sid = (state.get("pos_data") or {}).get("store_id") or state.get("store_id", "I63")
    return normalize_store_id(sid)


def _update_metrics(state: dict, key: str, value) -> dict:
    metrics = dict(state.get("metrics") or {})
    metrics[key] = value
    metrics["nodes_executed"] = int(metrics.get("nodes_executed", 0)) + 1
    return metrics


def get_llm() -> ChatOllama:
    return ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_URL,
        temperature=0.1,
        num_predict=350,
        num_ctx=2048,
    )


async def node_receive_pos(state: SalesAgentState) -> dict:
    cid = _cycle_id(state)
    sid = _store_id(state)
    log = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("receive_pos", state)
    t0 = time.time()

    try:
        provider = get_data_provider()

        pos_data = await provider.fetch_pos_data(sid)
        pos_history = await provider.fetch_pos_history(sid)

        duration = (time.time() - t0) * 1000

        output = {
            **state,
            "store_id": sid,
            "pos_data": pos_data,
            "pos_history": pos_history,
        }

        log.node_done(
            "receive_pos",
            log_id,
            output,
            duration,
            {
                "store_id": sid,
                "nb_transactions": len(pos_history),
                "ca_today": pos_data.get("current_revenue", 0),
                "business_date": pos_data.get("business_date"),
            },
        )

        metrics = _update_metrics(state, "analyste_receive_ms", round(duration))

        logger.info(
            f"[ANALYST] Node receive_pos — store={sid} | "
            f"TX={len(pos_history)} | CA={pos_data.get('current_revenue', 0):.0f} TND"
        )

        return {**output, "metrics": metrics}

    except Exception as e:
        duration = (time.time() - t0) * 1000
        log.node_error("receive_pos", log_id, e, state)
        log.fallback("receive_pos", log_id, str(e), duration)

        errors = list(state.get("errors") or [])
        errors.append(f"receive_pos postgres error: {e}")

        fallback_pos = {
            "store_id": sid,
            "daily_target": 1007,
            "daily_target_tnd": 1007,
            "current_revenue": 0,
            "current_revenue_tnd": 0,
            "nb_transactions_today": 0,
            "avg_ticket": 0,
            "hourly_ca": {},
            "current_hour": datetime.now().hour,
            "snapshot_time": datetime.now().strftime("%H:%M"),
            "closing_hour": 20,
            "source": "postgres_fallback",
            "data_status": "unavailable",
        }

        logger.warning(f"[ANALYST] receive_pos fallback — {e}")

        return {
            **state,
            "store_id": sid,
            "pos_data": fallback_pos,
            "pos_history": [],
            "errors": errors,
        }


async def node_validate_data(state: SalesAgentState) -> dict:
    cid = _cycle_id(state)
    sid = _store_id(state)
    log = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("validate_data", state)
    t0 = time.time()

    warnings = list(state.get("warnings") or [])
    errors = list(state.get("errors") or [])

    pos_data = state.get("pos_data") or {}
    pos_history = state.get("pos_history") or []

    daily_target = float(pos_data.get("daily_target", 0) or 0)
    current_revenue = float(pos_data.get("current_revenue", 0) or 0)

    if daily_target <= 0:
        warnings.append("daily_target invalide ou manquant")

    if current_revenue < 0:
        errors.append("current_revenue négatif")

    if not pos_history:
        warnings.append("aucune transaction POS trouvée")

    if pos_data.get("data_status") == "unavailable":
        warnings.append("données PostgreSQL indisponibles")
        errors.append("source POS indisponible")

    sale_ids = [tx.get("sale_id") for tx in pos_history if tx.get("sale_id")]
    duplicates = len(sale_ids) - len(set(sale_ids))

    negative_amounts = [
        tx for tx in pos_history
        if float(tx.get("revenue", tx.get("revenue_tnd", 0)) or 0) < 0
    ]

    unknown_products = [
        tx for tx in pos_history
        if not tx.get("product_code") and not tx.get("cod_prod")
    ]

    if duplicates > 0:
        warnings.append(f"{duplicates} doublons transaction détectés")

    if negative_amounts:
        errors.append(f"{len(negative_amounts)} transactions avec montant négatif")

    if unknown_products:
        warnings.append(f"{len(unknown_products)} produits inconnus")

    data_quality = {
        "is_valid": len(errors) == 0,
        "warnings_count": len(warnings),
        "errors_count": len(errors),
        "transactions_count": len(pos_history),
        "duplicates_count": duplicates,
        "negative_amounts_count": len(negative_amounts),
        "unknown_products_count": len(unknown_products),
        "data_status": pos_data.get("data_status", "available"),
    }

    duration = (time.time() - t0) * 1000

    output = {
        **state,
        "data_quality": data_quality,
        "warnings": warnings,
        "errors": errors,
    }

    log.node_done("validate_data", log_id, output, duration, data_quality)

    metrics = _update_metrics(state, "analyste_validate_ms", round(duration))

    logger.info(
        f"[ANALYST] Node validate_data — valid={data_quality['is_valid']} "
        f"| warnings={len(warnings)} | errors={len(errors)}"
    )

    return {**output, "metrics": metrics}


async def node_load_memory(state: SalesAgentState) -> dict:
    cid = _cycle_id(state)
    sid = _store_id(state)
    log = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("load_memory", state)
    t0 = time.time()

    try:
        memory = await load_analyst_memory(sid, limit=5)

        duration = (time.time() - t0) * 1000
        output = {**state, "analyst_memory": memory}

        log.node_done(
            "load_memory",
            log_id,
            output,
            duration,
            {
                "memory_count": memory.get("count", 0),
                "has_latest": bool(memory.get("latest")),
            },
        )

        metrics = _update_metrics(state, "analyste_memory_load_ms", round(duration))

        logger.info(
            f"[ANALYST MEMORY] Loaded {memory.get('count', 0)} memories for {sid}"
        )

        return {**output, "metrics": metrics}

    except Exception as e:
        duration = (time.time() - t0) * 1000
        log.node_error("load_memory", log_id, e, state)

        warnings = list(state.get("warnings") or [])
        warnings.append(f"memory load failed: {e}")

        return {
            **state,
            "warnings": warnings,
            "analyst_memory": {"store_id": sid, "count": 0, "latest": {}, "history": []},
        }


async def node_feature_engineering(state: SalesAgentState) -> dict:
    cid = _cycle_id(state)
    sid = _store_id(state)
    log = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("feature_engineering", state)
    t0 = time.time()

    pos_data = state.get("pos_data") or {}
    pos_history = state.get("pos_history") or []

    by_hour = {}
    by_category = {}
    by_advisor = {}
    top_products = {}

    for tx in pos_history:
        revenue = float(tx.get("revenue", tx.get("revenue_tnd", 0)) or 0)

        hour = tx.get("hour") or tx.get("heure")
        if hour is None:
            try:
                hour = int(str(tx.get("time", "00:00")).split(":")[0])
            except Exception:
                hour = datetime.now().hour

        category = tx.get("product_category", "Autre")
        advisor = tx.get("agent_name", tx.get("advisor", "Inconnu"))
        product = tx.get("product_name", tx.get("des_produit", "Produit inconnu"))

        by_hour[int(hour)] = by_hour.get(int(hour), 0) + revenue
        by_category[category] = by_category.get(category, 0) + revenue

        if advisor not in by_advisor:
            by_advisor[advisor] = {"ca": 0, "nb_tx": 0}
        by_advisor[advisor]["ca"] += revenue
        by_advisor[advisor]["nb_tx"] += 1

        if product not in top_products:
            top_products[product] = {"ca": 0, "nb_tx": 0}
        top_products[product]["ca"] += revenue
        top_products[product]["nb_tx"] += 1

    current_revenue = float(pos_data.get("current_revenue", 0) or 0)
    nb_tx = len(pos_history)
    avg_ticket = round(current_revenue / nb_tx, 2) if nb_tx else 0

    top_categories = sorted(
        [{"category": k, "ca": round(v, 2)} for k, v in by_category.items()],
        key=lambda x: x["ca"],
        reverse=True,
    )[:5]

    top_advisors = sorted(
        [
            {
                "advisor": k,
                "ca": round(v["ca"], 2),
                "nb_tx": v["nb_tx"],
                "avg_ticket": round(v["ca"] / v["nb_tx"], 2) if v["nb_tx"] else 0,
            }
            for k, v in by_advisor.items()
        ],
        key=lambda x: x["ca"],
        reverse=True,
    )[:5]

    top_products_list = sorted(
        [
            {
                "product": k,
                "ca": round(v["ca"], 2),
                "nb_tx": v["nb_tx"],
            }
            for k, v in top_products.items()
        ],
        key=lambda x: x["ca"],
        reverse=True,
    )[:5]

    features = {
        "ca_by_hour": by_hour,
        "ca_by_category": by_category,
        "top_categories": top_categories,
        "top_advisors": top_advisors,
        "top_products": top_products_list,
        "avg_ticket": avg_ticket,
        "nb_transactions": nb_tx,
    }

    duration = (time.time() - t0) * 1000

    output = {
        **state,
        "analysis_features": features,
        "pos_data": {
            **pos_data,
            "analysis_features": features,
        },
    }

    log.node_done(
        "feature_engineering",
        log_id,
        output,
        duration,
        {
            "nb_tx": nb_tx,
            "avg_ticket": avg_ticket,
            "top_category": top_categories[0]["category"] if top_categories else None,
        },
    )

    metrics = _update_metrics(state, "analyste_features_ms", round(duration))

    logger.info(
        f"[ANALYST] Node feature_engineering — TX={nb_tx} | panier={avg_ticket} TND"
    )

    return {**output, "metrics": metrics}


async def node_compute_gap(state: SalesAgentState) -> dict:
    cid = _cycle_id(state)
    sid = _store_id(state)
    log = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("compute_gap", state)
    t0 = time.time()

    pos_data = state.get("pos_data", {})
    pos_history = state.get("pos_history", [])

    current_revenue = float(pos_data.get("current_revenue", 0))
    daily_target = float(pos_data.get("daily_target", config.default_daily_target))
    current_hour = datetime.now().hour

    revenue_last_1h = sum(
        float(tx.get("revenue", 0) or 0)
        for tx in pos_history
        if tx.get("minutes_ago", 999) <= 60
    )

    revenue_last_2h = sum(
        float(tx.get("revenue", 0) or 0)
        for tx in pos_history
        if tx.get("minutes_ago", 999) <= 120
    )

    nb_tx = len(pos_history)
    avg_tx = current_revenue / nb_tx if nb_tx > 0 else 0

    by_category = {}
    for tx in pos_history:
        cat = tx.get("product_category", "Autre")
        by_category[cat] = by_category.get(cat, 0) + float(tx.get("revenue", 0) or 0)

    gap_amount = max(0.0, daily_target - current_revenue)
    gap_pct = (gap_amount / daily_target * 100) if daily_target > 0 else 0.0
    attainment = round((current_revenue / daily_target * 100), 1) if daily_target > 0 else 0.0

    hours_left = max(1, 20 - current_hour)
    required_rate = gap_amount / hours_left

    history_summary = {
        "total_transactions": nb_tx,
        "total_revenue": current_revenue,
        "revenue_last_1h": revenue_last_1h,
        "revenue_last_2h": revenue_last_2h,
        "avg_transaction": round(avg_tx, 2),
        "revenue_by_category": by_category,
        "required_rate_per_h": round(required_rate, 2),
    }

    duration = (time.time() - t0) * 1000

    output = {
        **state,
        "gap_objectif": round(gap_pct, 2),
        "gap_amount": round(gap_amount, 2),
        "attainment": attainment,
        "pos_data": {
            **pos_data,
            "history_summary": history_summary,
            "current_hour": current_hour,
            "hours_remaining": hours_left,
            "required_rate_tnd": required_rate,
        },
    }

    log.node_done(
        "compute_gap",
        log_id,
        output,
        duration,
        {
            "gap_pct": round(gap_pct, 1),
            "attainment": attainment,
            "gap_amount": round(gap_amount),
            "required_rate": round(required_rate),
        },
    )

    metrics = _update_metrics(state, "analyste_gap_ms", round(duration))

    logger.info(
        f"[ANALYST] Node compute_gap — Gap={gap_pct:.1f}% "
        f"({gap_amount:.0f} TND) | CA={current_revenue:.0f}/{daily_target:.0f}"
    )

    return {**output, "metrics": metrics}


async def node_call_timesfm(state: SalesAgentState) -> dict:
    cid = _cycle_id(state)
    sid = _store_id(state)
    log = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("call_timesfm", state)
    t0 = time.time()

    pos_data = state.get("pos_data", {})

    if pos_data.get("data_status") == "unavailable":
        warnings = list(state.get("warnings") or [])
        warnings.append("forecast non exécuté car données POS indisponibles")

        return {
            **state,
            "warnings": warnings,
            "timesfm_prediction": {
                "forecast_end_of_day": 0.0,
                "forecast_end_of_day_tnd": 0.0,
                "forecast_remaining": 0.0,
                "mape": 99.0,
                "source": "forecast_skipped_pos_unavailable",
            },
            "forecast_eod": 0.0,
            "forecast_mape": 99.0,
            "coverage": 0.0,
        }

    try:
        provider = get_data_provider()
        prediction = await provider.fetch_timesfm_prediction(sid)

        forecast_eod = float(prediction.get("forecast_end_of_day", 0))
        mape = float(prediction.get("mape", 14.3))
        ca_today = float(pos_data.get("current_revenue", 0))
        gap_amount = float(state.get("gap_amount", 0))

        if gap_amount > 0:
            coverage = min(100.0, ((forecast_eod - ca_today) / gap_amount) * 100)
        else:
            coverage = 100.0

        duration = (time.time() - t0) * 1000

        output = {
            **state,
            "timesfm_prediction": prediction,
            "forecast_eod": forecast_eod,
            "forecast_mape": mape,
            "coverage": round(coverage, 1),
        }

        log.node_done(
            "call_timesfm",
            log_id,
            output,
            duration,
            {
                "forecast_eod": round(forecast_eod),
                "mape": mape,
                "coverage": round(coverage, 1),
            },
        )

        metrics = _update_metrics(state, "analyste_timesfm_ms", round(duration))

        logger.info(
            f"[ANALYST] Node call_timesfm — EOD={forecast_eod:.0f} TND "
            f"| MAPE={mape:.1f}% | couverture={coverage:.1f}%"
        )

        return {**output, "metrics": metrics}

    except Exception as e:
        duration = (time.time() - t0) * 1000
        log.node_error("call_timesfm", log_id, e, state)
        log.fallback("call_timesfm", log_id, str(e), duration)

        warnings = list(state.get("warnings") or [])
        warnings.append(f"TimesFM/Postgres fallback utilisé: {e}")

        logger.warning(f"[ANALYST] call_timesfm fallback — {e}")

        return {
            **state,
            "warnings": warnings,
            "timesfm_prediction": {
                "forecast_end_of_day": 0.0,
                "forecast_end_of_day_tnd": 0.0,
                "forecast_remaining": 0.0,
                "mape": 99.0,
                "source": "forecast_unavailable",
            },
            "forecast_eod": 0.0,
            "forecast_mape": 99.0,
            "coverage": 0.0,
        }


async def node_compare_with_memory(state: SalesAgentState) -> dict:
    cid = _cycle_id(state)
    sid = _store_id(state)
    log = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("compare_with_memory", state)
    t0 = time.time()

    pos_data = state.get("pos_data") or {}
    features = state.get("analysis_features") or {}
    memory = state.get("analyst_memory") or {}

    current = {
        "current_revenue": pos_data.get("current_revenue", 0),
        "gap_pct": state.get("gap_objectif", 0),
        "urgency_level": state.get("urgency_level", "LOW"),
        "avg_ticket": features.get("avg_ticket", 0),
    }

    insights = compare_with_memory(current, memory)

    duration = (time.time() - t0) * 1000

    output = {
        **state,
        "memory_insights": insights,
    }

    log.node_done(
        "compare_with_memory",
        log_id,
        output,
        duration,
        {
            "gap_trend": insights.get("gap_trend"),
            "revenue_trend": insights.get("revenue_trend"),
            "ticket_trend": insights.get("ticket_trend"),
        },
    )

    metrics = _update_metrics(state, "analyste_memory_compare_ms", round(duration))

    logger.info(
        f"[ANALYST MEMORY] gap_trend={insights.get('gap_trend')} "
        f"revenue_trend={insights.get('revenue_trend')}"
    )

    return {**output, "metrics": metrics}


async def node_detect_urgency(state: SalesAgentState) -> dict:
    cid = _cycle_id(state)
    sid = _store_id(state)
    log = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("detect_urgency", state)
    t0 = time.time()

    pos_data = state.get("pos_data", {})
    features = state.get("analysis_features") or {}
    memory_insights = state.get("memory_insights") or {}

    if pos_data.get("data_status") == "unavailable":
        duration = (time.time() - t0) * 1000

        output = {
            **state,
            "urgency_level": "CRITICAL",
            "urgency_score": 1.0,
        }

        log.node_done(
            "detect_urgency",
            log_id,
            output,
            duration,
            {
                "urgency_level": "CRITICAL",
                "urgency_score": 1.0,
                "reason": "pos_data_unavailable",
            },
        )

        metrics = _update_metrics(state, "analyste_urgency_ms", round(duration))
        logger.warning("[ANALYST] Données POS indisponibles — urgence CRITICAL")

        return {**output, "metrics": metrics}

    gap_pct = float(state.get("gap_objectif", 0))
    coverage = float(state.get("coverage", 100.0))
    current_hour = datetime.now().hour
    hours_left = max(0.0, 20 - current_hour)

    avg_ticket = float(features.get("avg_ticket", 0) or 0)
    nb_tx = int(features.get("nb_transactions", 0) or 0)

    gap_score = min(1.0, gap_pct / 60.0)
    time_pressure = min(1.0, max(0.0, (current_hour - 8) / 12))
    forecast_risk = max(0.0, (100 - coverage) / 100)

    activity_risk = 0.0
    if current_hour >= 10 and nb_tx <= 2:
        activity_risk = 0.4
    elif current_hour >= 12 and nb_tx <= 5:
        activity_risk = 0.25

    basket_risk = 0.0
    if avg_ticket and avg_ticket < 30 and gap_pct > 10:
        basket_risk = 0.20

    memory_risk = 0.0
    if memory_insights.get("gap_trend") == "worsening":
        memory_risk += 0.10
    if memory_insights.get("revenue_trend") == "down":
        memory_risk += 0.05

    urgency_score = round(
        min(
            1.0,
            gap_score * 0.35
            + time_pressure * 0.20
            + forecast_risk * 0.20
            + activity_risk * 0.10
            + basket_risk * 0.10
            + memory_risk,
        ),
        3,
    )

    if gap_pct > 45 and coverage < 70:
        urgency_level = "CRITICAL"
    elif gap_pct > 30 and coverage < 85:
        urgency_level = "HIGH"
    elif gap_pct > 15 or hours_left < 3:
        urgency_level = "MEDIUM"
    else:
        urgency_level = "LOW"

    if hours_left < 2 and gap_pct > 10:
        urgency_level = "HIGH" if urgency_level != "CRITICAL" else "CRITICAL"
        urgency_score = max(urgency_score, 0.85)

    duration = (time.time() - t0) * 1000

    output = {
        **state,
        "urgency_level": urgency_level,
        "urgency_score": urgency_score,
        "pos_data": {
            **pos_data,
            "hours_remaining": hours_left,
            "current_hour": current_hour,
            "forecast_gap_coverage_pct": coverage,
        },
    }

    log.node_done(
        "detect_urgency",
        log_id,
        output,
        duration,
        {
            "urgency_level": urgency_level,
            "urgency_score": urgency_score,
            "gap_pct": round(gap_pct, 1),
            "hours_left": hours_left,
            "coverage": round(coverage, 1),
            "avg_ticket": avg_ticket,
            "memory_gap_trend": memory_insights.get("gap_trend"),
        },
    )

    metrics = _update_metrics(state, "analyste_urgency_ms", round(duration))

    logger.info(
        f"[ANALYST] Node detect_urgency — Urgence={urgency_level} "
        f"| Score={urgency_score:.3f} | Gap={gap_pct:.1f}%"
    )

    return {**output, "metrics": metrics}


async def node_llm_summary(state: SalesAgentState) -> dict:
    cid = _cycle_id(state)
    sid = _store_id(state)
    log = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("llm_summary", state)
    t0 = time.time()

    pos_data = state.get("pos_data", {})
    features = state.get("analysis_features") or {}
    memory_insights = state.get("memory_insights") or {}

    urgency_level = state.get("urgency_level", "MEDIUM")
    gap_pct = float(state.get("gap_objectif", 0.0))
    gap_amount = float(state.get("gap_amount", 0.0))
    forecast_eod = float(state.get("forecast_eod", 0.0))
    coverage = float(state.get("coverage", 100.0))
    history_summary = pos_data.get("history_summary", {})
    hours_left = float(pos_data.get("hours_remaining", 8))
    ca_today = float(pos_data.get("current_revenue", 0))
    daily_target = float(pos_data.get("daily_target", 1007))

    if pos_data.get("data_status") == "unavailable":
        analyst_summary = (
            "Les données POS PostgreSQL sont indisponibles pour cette boutique. "
            "Le cycle Analyste est marqué CRITICAL afin d’éviter une recommandation basée sur des données vides."
        )

        duration = (time.time() - t0) * 1000
        output = {
            **state,
            "analyst_summary": analyst_summary,
            "route_to": "strategie",
        }

        metrics = _update_metrics(state, "analyste_llm_ms", round(duration))

        return {**output, "metrics": metrics}

    user_msg = ANALYST_USER_PROMPT.format(
        pos_data=json.dumps(
            {
                "store_id": sid,
                "business_date": pos_data.get("business_date"),
                "current_revenue_tnd": round(ca_today),
                "daily_target_tnd": round(daily_target),
                "gap_pct": round(gap_pct, 1),
                "gap_amount_tnd": round(gap_amount),
                "hours_remaining": round(hours_left, 1),
                "nb_transactions": history_summary.get("total_transactions", 0),
                "avg_transaction_tnd": round(history_summary.get("avg_transaction", 0)),
                "avg_ticket": features.get("avg_ticket", 0),
                "revenue_last_1h": round(history_summary.get("revenue_last_1h", 0)),
                "required_rate_per_h": round(history_summary.get("required_rate_per_h", 0)),
                "backend_urgency_level": urgency_level,
                "memory_insights": memory_insights,
            },
            indent=2,
            ensure_ascii=False,
        ),
        pos_history_summary=json.dumps(
            history_summary.get("revenue_by_category", {}),
            indent=2,
            ensure_ascii=False,
        ),
        timesfm_prediction=json.dumps(
            {
                "forecast_end_of_day_tnd": round(forecast_eod),
                "coverage_pct": round(coverage, 1),
                "mape": round(state.get("forecast_mape", 14.3), 1),
            },
            indent=2,
            ensure_ascii=False,
        ),
        current_time=datetime.now().strftime("%H:%M"),
        daily_target=round(daily_target),
    )

    analyst_summary = ""
    llm_ok = False

    try:
        logger.info(f"[ANALYST] Node llm_summary — Appel LLM ({OLLAMA_MODEL})")
        llm = get_llm()
        response = await llm.ainvoke(
            [
                SystemMessage(content=ANALYST_SYSTEM_PROMPT),
                HumanMessage(content=user_msg),
            ]
        )

        content = response.content.strip()

        if "```" in content:
            parts = content.split("```")
            content = parts[1] if len(parts) > 1 else parts[0]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        try:
            analysis = json.loads(content)
            analyst_summary = analysis.get("analyst_summary", "")

            llm_urgency = analysis.get("urgency_level", urgency_level)
            if llm_urgency != urgency_level:
                logger.warning(
                    f"[ANALYST] LLM urgency ignored: {llm_urgency} != backend {urgency_level}"
                )

            if not analyst_summary:
                analyst_summary = _make_fallback_summary(
                    gap_pct,
                    urgency_level,
                    ca_today,
                    daily_target,
                    forecast_eod,
                    memory_insights,
                )

            llm_ok = True

        except json.JSONDecodeError:
            if len(content) > 20:
                analyst_summary = content[:400]
                llm_ok = True
            else:
                raise ValueError("Réponse LLM trop courte")

    except Exception as e:
        logger.warning(f"[ANALYST] Node llm_summary fallback: {str(e)[:80]}")
        analyst_summary = _make_fallback_summary(
            gap_pct,
            urgency_level,
            ca_today,
            daily_target,
            forecast_eod,
            memory_insights,
        )

    duration = (time.time() - t0) * 1000

    output = {
        **state,
        "analyst_summary": analyst_summary,
        "route_to": "strategie",
    }

    log.node_done(
        "llm_summary",
        log_id,
        output,
        duration,
        {
            "llm_ok": llm_ok,
            "summary_len": len(analyst_summary),
            "urgency": urgency_level,
        },
    )

    metrics = dict(state.get("metrics") or {})
    metrics["analyste_llm_ms"] = round(duration)
    metrics["nodes_executed"] = int(metrics.get("nodes_executed", 0)) + 1
    metrics["llm_calls"] = int(metrics.get("llm_calls", 0)) + (1 if llm_ok else 0)

    logger.info(
        f"[ANALYST] Node llm_summary — done | urgency={urgency_level} | "
        f"summary={analyst_summary[:70]}..."
    )

    return {**output, "metrics": metrics}


async def node_build_strategy_query(state: SalesAgentState) -> dict:
    cid = _cycle_id(state)
    sid = _store_id(state)
    log = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("build_strategy_query", state)
    t0 = time.time()

    urgency = state.get("urgency_level", "LOW")
    gap_pct = float(state.get("gap_objectif", 0) or 0)
    gap_amount = float(state.get("gap_amount", 0) or 0)
    forecast_eod = float(state.get("forecast_eod", 0) or 0)

    pos_data = state.get("pos_data") or {}
    features = state.get("analysis_features") or {}
    memory_insights = state.get("memory_insights") or {}

    top_categories = features.get("top_categories", [])
    top_products = features.get("top_products", [])

    category_text = " ".join([c["category"] for c in top_categories[:3]])
    product_text = " ".join([p["product"] for p in top_products[:3]])

    daily_target = float(pos_data.get("daily_target", 0) or 0)

    if pos_data.get("data_status") == "unavailable":
        strategy_intent = "vérifier source données POS avant toute recommandation"
        strategy_query = (
            "données POS indisponibles vérifier PostgreSQL éviter recommandation commerciale "
            "basée sur données vides alerte monitoring"
        )
    else:
        if gap_pct < 20 and forecast_eod >= daily_target:
            strategy_intent = "optimiser panier moyen et upsell accessoires sans alerte critique"
        elif gap_pct >= 20:
            strategy_intent = "accélérer ventes pour combler retard objectif"
        else:
            strategy_intent = "maintenir performance et convertir trafic"

        if urgency in ["HIGH", "CRITICAL"]:
            intent = "action immédiate urgence closing objectif retard critique"
        elif urgency == "MEDIUM":
            intent = "optimisation ventes relance panier moyen objectif à surveiller"
        else:
            intent = "maintenir performance upsell cross-sell opportunité"

        strategy_query = (
            f"{intent} {strategy_intent} "
            f"gap {gap_pct:.1f}% montant restant {gap_amount:.0f} TND "
            f"forecast fin journée {forecast_eod:.0f} TND "
            f"panier moyen {features.get('avg_ticket', 0)} TND "
            f"tendance gap {memory_insights.get('gap_trend', 'unknown')} "
            f"catégories {category_text} "
            f"produits {product_text}"
        ).strip()

    duration = (time.time() - t0) * 1000

    analyst_output = {
        "agent": "analyst",
        "store_id": sid,
        "cycle_id": cid,
        "status": "success" if not state.get("errors") else "partial_success",
        "business_date": pos_data.get("business_date"),
        "current_revenue": pos_data.get("current_revenue", 0),
        "daily_target": pos_data.get("daily_target", 0),
        "gap_amount": gap_amount,
        "gap_pct": gap_pct,
        "forecast_eod": forecast_eod,
        "coverage": state.get("coverage", 0),
        "urgency_level": urgency,
        "urgency_score": state.get("urgency_score", 0),
        "avg_ticket": features.get("avg_ticket", 0),
        "nb_transactions": features.get("nb_transactions", 0),
        "top_categories": top_categories,
        "top_products": top_products,
        "top_advisors": features.get("top_advisors", []),
        "data_quality": state.get("data_quality", {}),
        "memory_insights": memory_insights,
        "strategy_intent": strategy_intent,
        "rag_query": strategy_query,
        "strategy_query": strategy_query,
        "summary": state.get("analyst_summary", ""),
        "logs_saved": True,
        "next_agent": "stratege",
    }

    output = {
        **state,
        "rag_query": strategy_query,
        "strategy_intent": strategy_intent,
        "route_to": "strategie",
        "analyst_output": analyst_output,
    }

    log.node_done(
        "build_strategy_query",
        log_id,
        output,
        duration,
        {
            "rag_query": strategy_query[:200],
            "strategy_intent": strategy_intent,
            "route_to": "strategie",
        },
    )

    metrics = _update_metrics(state, "analyste_strategy_query_ms", round(duration))

    logger.info(
        f"[ANALYST] Node build_strategy_query — query='{strategy_query[:80]}...'"
    )

    return {**output, "metrics": metrics}


async def node_save_memory(state: SalesAgentState) -> dict:
    cid = _cycle_id(state)
    sid = _store_id(state)
    log = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("save_memory", state)
    t0 = time.time()

    try:
        memory_payload = build_analyst_memory_payload(state)
        saved = await save_analyst_memory(
            store_id=sid,
            cycle_id=cid,
            memory_data=memory_payload,
            memory_type="cycle_summary",
        )

        duration = (time.time() - t0) * 1000

        output = {
            **state,
            "analyst_memory_saved": saved,
        }

        log.node_done(
            "save_memory",
            log_id,
            output,
            duration,
            {
                "memory_saved": saved,
                "gap_pct": memory_payload.get("gap_pct"),
                "urgency": memory_payload.get("urgency_level"),
            },
        )

        metrics = _update_metrics(state, "analyste_memory_save_ms", round(duration))

        logger.info(f"[ANALYST MEMORY] saved={saved} cycle={cid}")

        return {**output, "metrics": metrics}

    except Exception as e:
        duration = (time.time() - t0) * 1000
        log.node_error("save_memory", log_id, e, state)

        warnings = list(state.get("warnings") or [])
        warnings.append(f"memory save failed: {e}")

        return {
            **state,
            "warnings": warnings,
            "analyst_memory_saved": False,
        }


def _make_fallback_summary(
    gap_pct: float,
    urgency: str,
    ca_today: float,
    daily_target: float,
    forecast_eod: float,
    memory_insights: dict | None = None,
) -> str:
    memory_insights = memory_insights or {}
    trend = memory_insights.get("gap_trend")

    trend_text = ""
    if trend == "improving":
        trend_text = " La tendance s'améliore par rapport au cycle précédent."
    elif trend == "worsening":
        trend_text = " La tendance se dégrade par rapport au cycle précédent."

    if urgency in ["HIGH", "CRITICAL"]:
        return (
            f"Gap critique {gap_pct:.1f}% ({daily_target - ca_today:.0f} TND restants). "
            f"CA {ca_today:.0f}/{daily_target:.0f} TND. "
            f"Forecast EOD {forecast_eod:.0f} TND — action immédiate requise."
            f"{trend_text}"
        )

    if urgency == "MEDIUM":
        return (
            f"Gap {gap_pct:.1f}% à surveiller. "
            f"CA {ca_today:.0f}/{daily_target:.0f} TND. "
            f"Forecast EOD : {forecast_eod:.0f} TND. Stratégie recommandée."
            f"{trend_text}"
        )

    return (
        f"Performance correcte — gap {gap_pct:.1f}%. "
        f"CA {ca_today:.0f}/{daily_target:.0f} TND. "
        f"Forecast EOD : {forecast_eod:.0f} TND. Objectif atteignable."
        f"{trend_text}"
    )


def route_after_analysis(state: SalesAgentState) -> str:
    return "agent_stratege"