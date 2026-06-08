"""
nodes.py — Agent Analyste LangGraph
PostgreSQL uniquement — vw_pos_enriched, vw_ca_par_boutique, vw_stock_enriched
+ Logs + Analyst Memory + Fallback contrôlé.
"""

import json
import logging
import os
import time
from datetime import datetime

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

# ── Logger compatible avec les 3 variantes d'import ──────────────────────────
try:
    from agent_logger import AgentLogger
except ImportError:
    try:
        from shared_module.agent_logger import AgentLogger
    except ImportError:
        class AgentLogger:
            def __init__(self, *a, **k): pass
            def node_start(self, *a, **k): return None
            def node_done(self, *a, **k): pass
            def node_error(self, *a, **k): pass
            def fallback(self, *a, **k): pass

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

OLLAMA_URL   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:latest")



# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
# NODE 1 — receive_pos  (PostgreSQL uniquement)
# ══════════════════════════════════════════════════════════════════════════════

async def node_receive_pos(state: SalesAgentState) -> dict:
    cid = _cycle_id(state)
    sid = _store_id(state)
    log = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("receive_pos", state)
    t0 = time.time()

    pos_data    = None
    pos_history = []
    source_used = "unknown"

    # ── PostgreSQL uniquement (vw_pos_enriched + vw_ca_par_boutique) ──────────
    try:
        provider    = get_data_provider()
        pos_data    = await provider.fetch_pos_data(sid)
        pos_history = await provider.fetch_pos_history(sid)
        source_used = "postgresql"
        logger.info(
            f"[ANALYST PG] Node receive_pos — store={sid} | "
            f"DATE={pos_data.get('business_date','?')} | "
            f"CA={pos_data.get('current_revenue',0):.0f} TND | "
            f"TX={pos_data.get('nb_transactions_today',0)}"
        )
    except Exception as e_pg:
        duration = (time.time() - t0) * 1000
        log.node_error("receive_pos", log_id, e_pg, state)
        errors = list(state.get("errors") or [])
        errors.append(f"receive_pos error: {e_pg}")
        pos_data = {
            "store_id":              sid,
            "daily_target":          1007,
            "daily_target_tnd":      1007,
            "current_revenue":       0,
            "current_revenue_tnd":   0,
            "nb_transactions_today": 0,
            "avg_ticket":            0,
            "hourly_ca":             {},
            "current_hour":          datetime.now().hour,
            "snapshot_time":         datetime.now().strftime("%H:%M"),
            "closing_hour":          20,
            "source":                "fallback",
            "data_status":           "unavailable",
        }
        pos_history = []
        source_used = "fallback"
        logger.warning(f"[ANALYST] receive_pos fallback — {e_pg}")
        return {
            **state,
            "store_id":    sid,
            "pos_data":    pos_data,
            "pos_history": pos_history,
            "errors":      errors,
        }

    duration = (time.time() - t0) * 1000
    output = {
        **state,
        "store_id":    sid,
        "pos_data":    pos_data,
        "pos_history": pos_history,
    }

    log.node_done(
        "receive_pos", log_id, output, duration,
        {
            "store_id":      sid,
            "source":        source_used,
            "nb_transactions": len(pos_history),
            "ca_today":      pos_data.get("current_revenue", 0),
            "business_date": pos_data.get("business_date"),
        },
    )

    metrics = _update_metrics(state, "analyste_receive_ms", round(duration))
    logger.info(
        f"[ANALYST] Node receive_pos — store={sid} | "
        f"TX={len(pos_history)} | CA={pos_data.get('current_revenue', 0):.0f} TND | "
        f"source={source_used}"
    )
    return {**output, "metrics": metrics}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 2 — validate_data  (inchangé)
# ══════════════════════════════════════════════════════════════════════════════

async def node_validate_data(state: SalesAgentState) -> dict:
    cid = _cycle_id(state)
    sid = _store_id(state)
    log = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("validate_data", state)
    t0 = time.time()

    warnings = list(state.get("warnings") or [])
    errors   = list(state.get("errors")   or [])

    pos_data    = state.get("pos_data")    or {}
    pos_history = state.get("pos_history") or []

    daily_target    = float(pos_data.get("daily_target",    0) or 0)
    current_revenue = float(pos_data.get("current_revenue", 0) or 0)

    if daily_target   <= 0: warnings.append("daily_target invalide ou manquant")
    if current_revenue < 0: errors.append("current_revenue négatif")
    if not pos_history:     warnings.append("aucune transaction POS trouvée")
    if pos_data.get("data_status") == "unavailable":
        warnings.append("données POS indisponibles")
        errors.append("source POS indisponible")

    sale_ids   = [tx.get("sale_id") for tx in pos_history if tx.get("sale_id")]
    duplicates = len(sale_ids) - len(set(sale_ids))

    negative_amounts  = [tx for tx in pos_history
                         if float(tx.get("revenue", tx.get("revenue_tnd", 0)) or 0) < 0]
    unknown_products  = [tx for tx in pos_history
                         if not tx.get("product_code") and not tx.get("cod_prod")]

    if duplicates        > 0: warnings.append(f"{duplicates} doublons détectés")
    if negative_amounts:      errors.append(f"{len(negative_amounts)} montants négatifs")
    if unknown_products:      warnings.append(f"{len(unknown_products)} produits inconnus")

    data_quality = {
        "is_valid":              len(errors) == 0,
        "warnings_count":        len(warnings),
        "errors_count":          len(errors),
        "transactions_count":    len(pos_history),
        "duplicates_count":      duplicates,
        "negative_amounts_count":len(negative_amounts),
        "unknown_products_count":len(unknown_products),
        "data_status":           pos_data.get("data_status", "available"),
    }

    duration = (time.time() - t0) * 1000
    output   = {**state, "data_quality": data_quality, "warnings": warnings, "errors": errors}

    log.node_done("validate_data", log_id, output, duration, data_quality)
    metrics = _update_metrics(state, "analyste_validate_ms", round(duration))

    logger.info(
        f"[ANALYST] Node validate_data — valid={data_quality['is_valid']} "
        f"| warnings={len(warnings)} | errors={len(errors)}"
    )
    return {**output, "metrics": metrics}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 3 — load_memory  (inchangé)
# ══════════════════════════════════════════════════════════════════════════════

async def node_load_memory(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("load_memory", state)
    t0     = time.time()

    try:
        memory   = await load_analyst_memory(sid, limit=5)
        duration = (time.time() - t0) * 1000
        output   = {**state, "analyst_memory": memory}

        log.node_done("load_memory", log_id, output, duration,
                      {"memory_count": memory.get("count", 0),
                       "has_latest":   bool(memory.get("latest"))})

        metrics = _update_metrics(state, "analyste_memory_load_ms", round(duration))
        logger.info(f"[ANALYST MEMORY] Loaded {memory.get('count',0)} memories for {sid}")
        return {**output, "metrics": metrics}

    except Exception as e:
        duration = (time.time() - t0) * 1000
        log.node_error("load_memory", log_id, e, state)
        warnings = list(state.get("warnings") or [])
        warnings.append(f"memory load failed: {e}")
        return {**state, "analyst_memory": {"count": 0, "latest": {}, "history": []},
                "warnings": warnings}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 4 — feature_engineering  (inchangé)
# ══════════════════════════════════════════════════════════════════════════════

async def node_feature_engineering(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("feature_engineering", state)
    t0     = time.time()

    pos_data    = state.get("pos_data")    or {}
    pos_history = state.get("pos_history") or []

    current_revenue = float(pos_data.get("current_revenue", 0) or 0)
    daily_target    = float(pos_data.get("daily_target",    0) or 0)
    nb_tx           = int(  pos_data.get("nb_transactions_today", len(pos_history)) or 0)
    avg_ticket      = round(current_revenue / max(nb_tx, 1), 2)
    hourly_ca       = pos_data.get("hourly_ca", {})

    # Calcul tendance horaire
    hours_sorted = sorted(int(h) for h in hourly_ca.keys())
    hourly_trend = []
    for h in hours_sorted:
        hourly_trend.append({"hour": h, "revenue": round(hourly_ca.get(h, 0), 2)})

    # Catégories top depuis pos_history
    by_cat: dict[str, float] = {}
    by_prod: dict[str, float] = {}
    by_agent: dict[str, float] = {}

    for tx in pos_history:
        rev  = float(tx.get("revenue", tx.get("revenue_tnd", 0)) or 0)
        cat  = tx.get("product_category", "Autre")
        prod = tx.get("product_name",     tx.get("product_code", "Inconnu"))
        agt  = tx.get("agent_name",       tx.get("agent_id",     "Inconnu"))

        if rev > 0:
            by_cat[cat]   = by_cat.get(cat,   0) + rev
            by_prod[prod] = by_prod.get(prod, 0) + rev
            by_agent[agt] = by_agent.get(agt,  0) + rev

    top_categories = sorted(
        [{"category": k, "revenue": round(v, 2), "pct": round(v / max(current_revenue, 1) * 100, 1)}
         for k, v in by_cat.items()],
        key=lambda x: -x["revenue"]
    )[:5]

    top_products = sorted(
        [{"product": k, "revenue": round(v, 2)}
         for k, v in by_prod.items()],
        key=lambda x: -x["revenue"]
    )[:5]

    top_advisors = sorted(
        [{"advisor": k, "revenue": round(v, 2),
          "pct": round(v / max(current_revenue, 1) * 100, 1)}
         for k, v in by_agent.items()],
        key=lambda x: -x["revenue"]
    )[:4]

    # Taux de conversion approx.
    conversion_rate = round(nb_tx / max(nb_tx + 5, 1) * 100, 1)

    features = {
        "nb_transactions":  nb_tx,
        "avg_ticket":       avg_ticket,
        "hourly_trend":     hourly_trend,
        "top_categories":   top_categories,
        "top_products":     top_products,
        "top_advisors":     top_advisors,
        "conversion_rate":  conversion_rate,
        "ca_live":          float(pos_data.get("ca_live", 0) or 0),
        "ca_historique":    float(pos_data.get("ca_historique", current_revenue) or 0),
        "business_date":    pos_data.get("business_date"),
        "sim_date":         pos_data.get("sim_date"),
        "source":           pos_data.get("source", "unknown"),
    }

    duration = (time.time() - t0) * 1000
    output   = {**state, "analysis_features": features}

    log.node_done("feature_engineering", log_id, output, duration, features)
    metrics = _update_metrics(state, "analyste_feature_ms", round(duration))

    logger.info(
        f"[ANALYST] Node feature_engineering — TX={nb_tx} | panier={avg_ticket} TND"
    )
    return {**output, "metrics": metrics}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 5 — compute_gap  (inchangé)
# ══════════════════════════════════════════════════════════════════════════════

async def node_compute_gap(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("compute_gap", state)
    t0     = time.time()

    pos_data = state.get("pos_data") or {}

    current_revenue = float(pos_data.get("current_revenue", 0) or 0)
    daily_target    = float(pos_data.get("daily_target",    0) or 0)

    if daily_target <= 0:
        daily_target = 1007.0

    gap_amount     = max(0.0, daily_target - current_revenue)
    gap_percentage = round(gap_amount / daily_target * 100, 2) if daily_target > 0 else 0.0
    attainment_pct = round(current_revenue / daily_target * 100, 1) if daily_target > 0 else 0.0

    duration = (time.time() - t0) * 1000

    output = {
        **state,
        "gap_objectif": gap_percentage,
        "gap_amount":   gap_amount,
        "attainment":   attainment_pct,
    }

    log.node_done("compute_gap", log_id, output, duration,
                  {"gap_pct": gap_percentage, "gap_amount": gap_amount})
    metrics = _update_metrics(state, "analyste_gap_ms", round(duration))

    logger.info(
        f"[ANALYST] Node compute_gap — Gap={gap_percentage:.1f}% "
        f"({gap_amount:.0f} TND) | CA={current_revenue:.0f}/{daily_target:.0f}"
    )
    return {**output, "metrics": metrics}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 6 — call_timesfm  (PostgreSQL uniquement)
# ══════════════════════════════════════════════════════════════════════════════

async def node_call_timesfm(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("call_timesfm", state)
    t0     = time.time()

    pos_data   = state.get("pos_data") or {}
    gap_amount = float(state.get("gap_amount", 0) or 0)

    prediction  = None
    source_used = "unknown"

    # ── PostgreSQL uniquement (vw_ca_par_boutique multi-sources) ──────────────
    try:
        provider    = get_data_provider()
        prediction  = await provider.fetch_timesfm_prediction(sid)
        source_used = "postgresql_forecast"
    except Exception as e_pg:
        prediction  = {
            "forecast_end_of_day":     0,
            "forecast_end_of_day_tnd": 0,
            "confidence_interval":     {"low": 0, "high": 0},
            "mape":                    99.0,
            "source":                  "fallback",
        }
        source_used = "fallback"
        logger.warning(f"[ANALYST] forecast fallback — {e_pg}")

    forecast_eod = float(prediction.get("forecast_end_of_day", 0) or 0)
    mape         = float(prediction.get("mape", 14.3)            or 14.3)

    # Couverture du gap
    current_revenue = float(pos_data.get("current_revenue", 0) or 0)
    if gap_amount > 0:
        coverage = round(min(100.0, ((forecast_eod - current_revenue) / gap_amount) * 100), 1)
    elif forecast_eod >= float(pos_data.get("daily_target", 1007) or 1007):
        coverage = 100.0
    else:
        coverage = 0.0

    duration = (time.time() - t0) * 1000

    output = {
        **state,
        "timesfm_prediction": prediction,
        "forecast_eod":       forecast_eod,
        "coverage":           coverage,
    }

    log.node_done("call_timesfm", log_id, output, duration,
                  {"forecast_eod": forecast_eod, "mape": mape,
                   "coverage": coverage, "source": source_used})
    metrics = _update_metrics(state, "analyste_timesfm_ms", round(duration))

    logger.info(
        f"[ANALYST] Node call_timesfm — EOD={forecast_eod:.0f} TND | "
        f"MAPE={mape:.1f}% | couverture={coverage:.1f}% | source={source_used}"
    )
    return {**output, "metrics": metrics}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 7 — compare_with_memory  (inchangé)
# ══════════════════════════════════════════════════════════════════════════════

async def node_compare_with_memory(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("compare_with_memory", state)
    t0     = time.time()

    pos_data  = state.get("pos_data")       or {}
    memory    = state.get("analyst_memory") or {}
    features  = state.get("analysis_features") or {}

    current = {
        "gap_pct":         state.get("gap_objectif", 0),
        "urgency_level":   state.get("urgency_level", ""),
        "current_revenue": pos_data.get("current_revenue", 0),
        "avg_ticket":      features.get("avg_ticket", 0),
    }

    insights  = compare_with_memory(current, memory)
    duration  = (time.time() - t0) * 1000
    output    = {**state, "memory_insights": insights}

    log.node_done("compare_with_memory", log_id, output, duration, insights)
    metrics = _update_metrics(state, "analyste_memory_compare_ms", round(duration))

    logger.info(
        f"[ANALYST MEMORY] gap_trend={insights.get('gap_trend','unknown')} "
        f"revenue_trend={insights.get('revenue_trend','unknown')}"
    )
    return {**output, "metrics": metrics}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 8 — detect_urgency  (règles métier renforcées)
# ══════════════════════════════════════════════════════════════════════════════

async def node_detect_urgency(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("detect_urgency", state)
    t0     = time.time()

    pos_data  = state.get("pos_data")          or {}
    features  = state.get("analysis_features") or {}

    gap_pct         = float(state.get("gap_objectif", 0) or 0)
    coverage        = float(state.get("coverage",      0) or 0)
    forecast_eod    = float(state.get("forecast_eod",  0) or 0)
    current_revenue = float(pos_data.get("current_revenue", 0) or 0)
    daily_target    = float(pos_data.get("daily_target",    1007) or 1007)
    nb_tx           = int(  features.get("nb_transactions", 0) or 0)
    avg_ticket      = float(features.get("avg_ticket",      0) or 0)

    current_hour  = datetime.now().hour
    hours_remaining = max(0, 20 - current_hour)

    # ── Score d'urgence ───────────────────────────────────────────────────────
    time_pressure    = min(1.0, max(0.0, (current_hour - 8) / 12))
    gap_score        = min(1.0, gap_pct / 60.0)
    coverage_penalty = max(0.0, (100 - coverage) / 100) * 0.25

    activity_risk = 0.0
    if current_hour >= 10 and nb_tx <= 2:   activity_risk = 0.15
    elif current_hour >= 12 and nb_tx <= 5: activity_risk = 0.10

    ticket_risk = 0.10 if (avg_ticket > 0 and avg_ticket < 30 and gap_pct > 10) else 0.0

    urgency_score = min(1.0,
        gap_score * 0.40 + time_pressure * 0.25
        + coverage_penalty + activity_risk + ticket_risk
    )

    # ── Niveau d'urgence ──────────────────────────────────────────────────────
    if pos_data.get("data_status") == "unavailable":
        urgency_level = "CRITICAL"
        urgency_score = 1.0
    elif gap_pct > 45 and coverage < 70:
        urgency_level = "CRITICAL"
    elif gap_pct > 30 and coverage < 85:
        urgency_level = "HIGH"
    elif gap_pct > 15 or hours_remaining < 3:
        urgency_level = "MEDIUM"
    else:
        urgency_level = "LOW"

    if hours_remaining < 2 and gap_pct > 10:
        urgency_level = "HIGH" if urgency_level != "CRITICAL" else "CRITICAL"
        urgency_score = max(urgency_score, 0.85)

    urgency_score = round(urgency_score, 3)

    duration = (time.time() - t0) * 1000
    output   = {
        **state,
        "urgency_level": urgency_level,
        "urgency_score": urgency_score,
    }

    log.node_done("detect_urgency", log_id, output, duration,
                  {"level": urgency_level, "score": urgency_score})
    metrics = _update_metrics(state, "analyste_urgency_ms", round(duration))

    logger.info(
        f"[ANALYST] Node detect_urgency — Urgence={urgency_level} | "
        f"Score={urgency_score:.3f} | Gap={gap_pct:.1f}%"
    )
    return {**output, "metrics": metrics}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 9 — llm_summary  (few-shot, Ollama)
# ══════════════════════════════════════════════════════════════════════════════

async def node_llm_summary(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("llm_summary", state)
    t0     = time.time()

    pos_data        = state.get("pos_data")          or {}
    features        = state.get("analysis_features") or {}
    memory_insights = state.get("memory_insights")   or {}
    prediction      = state.get("timesfm_prediction") or {}

    urgency_level   = state.get("urgency_level", "LOW")
    gap_pct         = float(state.get("gap_objectif", 0) or 0)
    gap_amount      = float(state.get("gap_amount",   0) or 0)
    forecast_eod    = float(state.get("forecast_eod", 0) or 0)
    coverage        = float(state.get("coverage",     0) or 0)
    current_revenue = float(pos_data.get("current_revenue", 0) or 0)
    daily_target    = float(pos_data.get("daily_target",    1007) or 1007)
    nb_tx           = int(  features.get("nb_transactions", 0) or 0)
    avg_ticket      = float(features.get("avg_ticket",      0) or 0)
    current_hour    = datetime.now().hour

    llm_ok          = False
    analyst_summary = ""

    # ── Résumé historique (pour le prompt) ────────────────────────────────────
    pos_history_summary = {
        "total_transactions": nb_tx,
        "total_revenue":      current_revenue,
        "avg_ticket":         avg_ticket,
        "top_categories":     features.get("top_categories", [])[:3],
        "hourly_trend":       features.get("hourly_trend",   [])[-4:],
        "ca_live":            features.get("ca_live",        0),
        "ca_historique":      features.get("ca_historique",  0),
        "business_date":      pos_data.get("business_date"),
        "sim_date":           pos_data.get("sim_date"),
        "source":             pos_data.get("source", "unknown"),
    }

    # ── Appel LLM (Ollama) ────────────────────────────────────────────────────
    try:
        logger.info(f"[ANALYST] Node llm_summary — Appel LLM ({OLLAMA_MODEL})")
        llm = get_llm()

        user_prompt = ANALYST_USER_PROMPT.format(
            pos_data=json.dumps({
                "current_revenue":       current_revenue,
                "daily_target":          daily_target,
                "nb_transactions":       nb_tx,
                "avg_ticket":            avg_ticket,
                "current_hour":          current_hour,
                "hours_remaining":       max(0, 20 - current_hour),
                "data_status":           pos_data.get("data_status", "available"),
                "source":                pos_data.get("source", "unknown"),
                "business_date":         pos_data.get("business_date"),
            }, ensure_ascii=False, indent=2),
            pos_history_summary=json.dumps(pos_history_summary, ensure_ascii=False, indent=2),
            timesfm_prediction=json.dumps({
                "forecast_end_of_day":   forecast_eod,
                "mape":                  prediction.get("mape", 14.3),
                "nb_sources":            prediction.get("nb_sources", 1),
                "confidence_interval":   prediction.get("confidence_interval", {}),
                "source":                prediction.get("source", "unknown"),
            }, ensure_ascii=False, indent=2),
            current_time=f"{current_hour:02d}:00",
            daily_target=f"{daily_target:.0f}",
        )

        messages = [
            SystemMessage(content=ANALYST_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]

        response = await llm.ainvoke(messages)
        raw      = response.content.strip() if response and response.content else ""

        # Parser la réponse JSON
        if raw.startswith("{"):
            try:
                parsed = json.loads(raw)
                analyst_summary = parsed.get("analyst_summary", "")
                # Override urgence/gap si le LLM les a calculés
                if parsed.get("urgency_level"):
                    state = {**state, "urgency_level": parsed["urgency_level"]}
                if parsed.get("urgency_score") is not None:
                    state = {**state, "urgency_score": parsed["urgency_score"]}
                llm_ok = True
            except json.JSONDecodeError:
                import re
                m = re.search(r'"analyst_summary"\s*:\s*"([^"]+)"', raw)
                analyst_summary = m.group(1).strip() if m else raw[:300]
                llm_ok = bool(analyst_summary)
        else:
            analyst_summary = raw[:300]
            llm_ok = bool(analyst_summary)

    except Exception as e:
        logger.warning(f"[ANALYST] LLM failed: {e} — fallback summary")

    # ── Fallback si LLM échoue ────────────────────────────────────────────────
    if not analyst_summary:
        analyst_summary = _make_fallback_summary(
            gap_pct, urgency_level, current_revenue,
            daily_target, forecast_eod, memory_insights,
        )

    duration = (time.time() - t0) * 1000
    output   = {**state, "analyst_summary": analyst_summary, "route_to": "strategie"}

    log.node_done("llm_summary", log_id, output, duration,
                  {"llm_ok": llm_ok, "summary_len": len(analyst_summary),
                   "urgency": urgency_level})

    metrics = dict(state.get("metrics") or {})
    metrics["analyste_llm_ms"]   = round(duration)
    metrics["nodes_executed"]    = int(metrics.get("nodes_executed", 0)) + 1
    metrics["llm_calls"]         = int(metrics.get("llm_calls", 0)) + (1 if llm_ok else 0)

    logger.info(
        f"[ANALYST] Node llm_summary — Appel LLM ({OLLAMA_MODEL})"
    )
    return {**output, "metrics": metrics}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 10 — build_strategy_query  (inchangé)
# ══════════════════════════════════════════════════════════════════════════════

async def node_build_strategy_query(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("build_strategy_query", state)
    t0     = time.time()

    urgency      = state.get("urgency_level", "LOW")
    gap_pct      = float(state.get("gap_objectif", 0)  or 0)
    gap_amount   = float(state.get("gap_amount",   0)  or 0)
    forecast_eod = float(state.get("forecast_eod", 0)  or 0)

    pos_data         = state.get("pos_data")           or {}
    features         = state.get("analysis_features")  or {}
    memory_insights  = state.get("memory_insights")    or {}

    top_categories   = features.get("top_categories", [])
    top_products     = features.get("top_products",   [])
    daily_target     = float(pos_data.get("daily_target", 0) or 0)

    category_text = " ".join([c["category"] for c in top_categories[:3]])
    product_text  = " ".join([p["product"]  for p in top_products[:3]])

    if pos_data.get("data_status") == "unavailable":
        strategy_intent = "vérifier source données POS avant toute recommandation"
        strategy_query  = (
            "données POS indisponibles vérifier source éviter recommandation commerciale "
            "basée sur données vides alerte monitoring"
        )
    else:
        if   gap_pct < 20 and forecast_eod >= daily_target:
            strategy_intent = "optimiser panier moyen et upsell accessoires sans alerte critique"
        elif gap_pct >= 20:
            strategy_intent = "accélérer ventes pour combler retard objectif"
        else:
            strategy_intent = "maintenir performance et convertir trafic"

        if   urgency in ["HIGH", "CRITICAL"]:
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
            f"catégories {category_text} produits {product_text}"
        ).strip()

    duration = (time.time() - t0) * 1000

    analyst_output = {
        "agent":           "analyst",
        "store_id":        sid,
        "cycle_id":        cid,
        "status":          "success" if not state.get("errors") else "partial_success",
        "business_date":   pos_data.get("business_date"),
        "sim_date":        pos_data.get("sim_date"),
        "current_revenue": pos_data.get("current_revenue", 0),
        "daily_target":    pos_data.get("daily_target",    0),
        "gap_amount":      gap_amount,
        "gap_pct":         gap_pct,
        "forecast_eod":    forecast_eod,
        "coverage":        state.get("coverage",      0),
        "urgency_level":   urgency,
        "urgency_score":   state.get("urgency_score", 0),
        "avg_ticket":      features.get("avg_ticket",  0),
        "nb_transactions": features.get("nb_transactions", 0),
        "top_categories":  top_categories,
        "top_products":    top_products,
        "top_advisors":    features.get("top_advisors", []),
        "data_quality":    state.get("data_quality",   {}),
        "memory_insights": memory_insights,
        "strategy_intent": strategy_intent,
        "rag_query":       strategy_query,
        "strategy_query":  strategy_query,
        "summary":         state.get("analyst_summary", ""),
        "logs_saved":      True,
        "next_agent":      "stratege",
        "source":          pos_data.get("source", "unknown"),
    }

    output = {
        **state,
        "rag_query":       strategy_query,
        "strategy_intent": strategy_intent,
        "route_to":        "strategie",
        "analyst_output":  analyst_output,
    }

    log.node_done("build_strategy_query", log_id, output, duration,
                  {"rag_query": strategy_query[:200], "route_to": "strategie"})
    metrics = _update_metrics(state, "analyste_strategy_query_ms", round(duration))

    logger.info(
        f"[ANALYST] Node build_strategy_query — query='{strategy_query[:80]}...'"
    )
    return {**output, "metrics": metrics}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 11 — save_memory  (inchangé)
# ══════════════════════════════════════════════════════════════════════════════

async def node_save_memory(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("save_memory", state)
    t0     = time.time()

    try:
        memory_payload = build_analyst_memory_payload(state)
        saved = await save_analyst_memory(
            store_id=sid, cycle_id=cid,
            memory_data=memory_payload, memory_type="cycle_summary",
        )

        duration = (time.time() - t0) * 1000
        output   = {**state, "analyst_memory_saved": saved}

        log.node_done("save_memory", log_id, output, duration,
                      {"memory_saved": saved,
                       "gap_pct":      memory_payload.get("gap_pct"),
                       "urgency":      memory_payload.get("urgency_level")})
        metrics = _update_metrics(state, "analyste_memory_save_ms", round(duration))

        logger.info(f"[ANALYST MEMORY] saved={saved} cycle={cid}")
        return {**output, "metrics": metrics}

    except Exception as e:
        duration = (time.time() - t0) * 1000
        log.node_error("save_memory", log_id, e, state)
        warnings = list(state.get("warnings") or [])
        warnings.append(f"memory save failed: {e}")
        return {**state, "warnings": warnings, "analyst_memory_saved": False}


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _make_fallback_summary(
    gap_pct: float, urgency: str,
    ca_today: float, daily_target: float, forecast_eod: float,
    memory_insights: dict | None = None,
) -> str:
    memory_insights = memory_insights or {}
    trend = memory_insights.get("gap_trend")
    trend_text = ""
    if trend == "improving": trend_text = " La tendance s'améliore."
    elif trend == "worsening": trend_text = " La tendance se dégrade."

    if urgency in ["HIGH", "CRITICAL"]:
        return (
            f"Gap critique {gap_pct:.1f}% ({daily_target - ca_today:.0f} TND restants). "
            f"CA {ca_today:.0f}/{daily_target:.0f} TND. "
            f"Forecast EOD {forecast_eod:.0f} TND — action immédiate requise.{trend_text}"
        )
    if urgency == "MEDIUM":
        return (
            f"Gap {gap_pct:.1f}% à surveiller. "
            f"CA {ca_today:.0f}/{daily_target:.0f} TND. "
            f"Forecast EOD : {forecast_eod:.0f} TND. Stratégie recommandée.{trend_text}"
        )
    return (
        f"Performance correcte — gap {gap_pct:.1f}%. "
        f"CA {ca_today:.0f}/{daily_target:.0f} TND. "
        f"Forecast EOD : {forecast_eod:.0f} TND. Objectif atteignable.{trend_text}"
    )


def route_after_analysis(state: SalesAgentState) -> str:
    return "agent_stratege"