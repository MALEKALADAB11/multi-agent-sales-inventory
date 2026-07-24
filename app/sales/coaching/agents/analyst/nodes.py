"""
nodes.py — Agent Analyste LangGraph + Langfuse Observabilité
=============================================================
Source de données : 100% PostgreSQL.
Chaque node trace via Langfuse : input/output, latence ms, erreurs, scores qualité.

Ne contient plus que les 6 nodes d'ingestion/mémoire du graphe v4. Tout le calcul
analytique vit dans ts_engine.py, appelé par le node ts_analyst (ts_node.py) :
aucun LLM n'est instancié ici.
"""

import logging
import os
import time
from datetime import datetime

try:
    from app.core.agent_logger import AgentLogger
except ImportError:
    try:
        from app.core.agent_logger import AgentLogger
    except ImportError:
        class AgentLogger:
            def __init__(self, *a, **k): pass
            def node_start(self, *a, **k): return None
            def node_done(self, *a, **k): pass
            def node_error(self, *a, **k): pass
            def fallback(self, *a, **k): pass

from app.sales.core.config import get_config
from app.sales.core.state import SalesAgentState
from app.sales.data.postgres_provider import get_data_provider, normalize_store_id
from app.core.config import DEFAULT_STORE_ID
from .tools import (
    load_analyst_memory,
    save_analyst_memory,
    compare_with_memory,
    build_analyst_memory_payload,
)

logger = logging.getLogger(__name__)
config = get_config()


# ══════════════════════════════════════════════════════════════════════════════
# Langfuse helpers
# ══════════════════════════════════════════════════════════════════════════════

def _lf_span(trace, name: str, input_data: dict, output_data: dict,
             latency_ms: float, level: str = "DEFAULT", metadata: dict = None):
    try:
        if trace is None:
            return
        trace.span(
            name     = name,
            input    = input_data,
            output   = output_data,
            metadata = {"latency_ms": round(latency_ms), **(metadata or {})},
        )
    except Exception as e:
        logger.debug(f"[LANGFUSE] span {name}: {e}")


def _lf_generation(trace, name: str, model: str, system_prompt: str,
                   user_prompt: str, response: str, latency_ms: float,
                   metadata: dict = None):
    try:
        if trace is None:
            return
        trace.generation(
            name  = name,
            model = model,
            input = [
                {"role": "system", "content": system_prompt[:600]},
                {"role": "user",   "content": user_prompt[:600]},
            ],
            output   = response[:800],
            metadata = {"latency_ms": round(latency_ms), **(metadata or {})},
        )
    except Exception as e:
        logger.debug(f"[LANGFUSE] generation {name}: {e}")


def _get_or_create_trace(state: dict) -> object:
    try:
        from app.core.langfuse import get_langfuse
        lf = get_langfuse()
        if lf is None:
            return None
        cid = _cycle_id(state)
        sid = _store_id(state)
        trace = lf.trace(
            id         = cid,
            name       = "analyste-cycle",
            user_id    = sid,
            session_id = f"session-{sid}-{datetime.now().strftime('%Y%m%d')}",
            tags       = ["analyste", sid],
        )
        return trace
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _cycle_id(state: dict) -> str:
    return (state.get("metrics") or {}).get("cycle_id") or state.get("cycle_id", "unknown")

def _store_id(state: dict) -> str:
    sid = (state.get("pos_data") or {}).get("store_id") or state.get("store_id", DEFAULT_STORE_ID)
    return normalize_store_id(sid)

def _update_metrics(state: dict, key: str, value) -> dict:
    metrics = dict(state.get("metrics") or {})
    metrics[key] = value
    metrics["nodes_executed"] = int(metrics.get("nodes_executed", 0)) + 1
    return metrics

# ══════════════════════════════════════════════════════════════════════════════
# NODE 1 — receive_pos  (CSV en priorité, PostgreSQL en fallback)
# ══════════════════════════════════════════════════════════════════════════════

async def node_receive_pos(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("receive_pos", state)
    t0     = time.time()
    trace  = _get_or_create_trace(state)

    pos_data    = None
    pos_history = []
    source_used = "postgresql"

    # ── PostgreSQL (source unique) ────────────────────────────────────────────
    try:
        provider    = get_data_provider()
        pos_data    = await provider.fetch_pos_data(sid)
        pos_history = await provider.fetch_pos_history(sid)
        logger.info(
            f"[ANALYST PG] Node receive_pos — store={sid} | "
            f"DATE={pos_data.get('business_date','?')} | "
            f"CA={pos_data.get('current_revenue',0):.0f} TND | "
            f"TX={pos_data.get('nb_transactions_today',0)}"
        )
    except Exception as e_pg:
            duration = (time.time() - t0) * 1000
            log.node_error("receive_pos", log_id, e_pg, state)
            _lf_span(trace, "receive_pos", {"store_id": sid},
                     {"error": str(e_pg), "status": "fallback"}, duration, "ERROR")
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
            return {**state, "store_id": sid, "pos_data": pos_data,
                    "pos_history": [], "errors": errors}

    duration = (time.time() - t0) * 1000
    output   = {**state, "store_id": sid, "pos_data": pos_data, "pos_history": pos_history}

    _lf_span(trace, "receive_pos",
        input_data  = {"store_id": sid, "source": source_used},
        output_data = {
            "ca_today":      pos_data.get("current_revenue", 0),
            "daily_target":  pos_data.get("daily_target", 1007),
            "nb_tx":         pos_data.get("nb_transactions_today", 0),
            "business_date": pos_data.get("business_date"),
            "sim_date":      pos_data.get("sim_date"),
            "nb_history":    len(pos_history),
        },
        latency_ms = duration,
        metadata   = {"source": source_used},
    )

    log.node_done("receive_pos", log_id, output, duration,
                  {"store_id": sid, "source": source_used,
                   "nb_transactions": len(pos_history),
                   "ca_today": pos_data.get("current_revenue", 0),
                   "business_date": pos_data.get("business_date")})
    logger.info(
        f"[ANALYST] Node receive_pos — store={sid} | "
        f"TX={len(pos_history)} | CA={pos_data.get('current_revenue', 0):.0f} TND | "
        f"source={source_used}"
    )
    return {**output, "metrics": _update_metrics(state, "analyste_receive_ms", round(duration))}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 2 — validate_data
# ══════════════════════════════════════════════════════════════════════════════

async def node_validate_data(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("validate_data", state)
    t0     = time.time()
    trace  = _get_or_create_trace(state)

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
    negative_amounts = [tx for tx in pos_history
                        if float(tx.get("revenue", tx.get("revenue_tnd", 0)) or 0) < 0]
    unknown_products = [tx for tx in pos_history
                        if not tx.get("product_code") and not tx.get("cod_prod")]

    if duplicates        > 0: warnings.append(f"{duplicates} doublons détectés")
    if negative_amounts:      errors.append(f"{len(negative_amounts)} montants négatifs")
    if unknown_products:      warnings.append(f"{len(unknown_products)} produits inconnus")

    data_quality = {
        "is_valid":               len(errors) == 0,
        "warnings_count":         len(warnings),
        "errors_count":           len(errors),
        "transactions_count":     len(pos_history),
        "duplicates_count":       duplicates,
        "negative_amounts_count": len(negative_amounts),
        "unknown_products_count": len(unknown_products),
        "data_status":            pos_data.get("data_status", "available"),
    }

    duration = (time.time() - t0) * 1000
    output   = {**state, "data_quality": data_quality, "warnings": warnings, "errors": errors}

    _lf_span(trace, "validate_data",
        input_data  = {"nb_transactions": len(pos_history), "ca": current_revenue,
                       "target": daily_target},
        output_data = data_quality,
        latency_ms  = duration,
        level       = "WARNING" if errors else "DEFAULT",
        metadata    = {"warnings": warnings[:3], "errors": errors[:3]},
    )

    log.node_done("validate_data", log_id, output, duration, data_quality)
    logger.info(f"[ANALYST] Node validate_data — valid={data_quality['is_valid']} "
                f"| warnings={len(warnings)} | errors={len(errors)}")
    return {**output, "metrics": _update_metrics(state, "analyste_validate_ms", round(duration))}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 3 — load_memory
# ══════════════════════════════════════════════════════════════════════════════

async def node_load_memory(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("load_memory", state)
    t0     = time.time()
    trace  = _get_or_create_trace(state)

    try:
        memory   = await load_analyst_memory(sid, limit=5)
        duration = (time.time() - t0) * 1000
        output   = {**state, "analyst_memory": memory}

        _lf_span(trace, "load_memory",
            input_data  = {"store_id": sid, "limit": 5},
            output_data = {"memory_count": memory.get("count", 0),
                           "has_latest": bool(memory.get("latest")),
                           "gap_trend": (memory.get("latest") or {}).get("gap_pct")},
            latency_ms  = duration,
        )

        log.node_done("load_memory", log_id, output, duration,
                      {"memory_count": memory.get("count", 0),
                       "has_latest":   bool(memory.get("latest"))})
        metrics = _update_metrics(state, "analyste_memory_load_ms", round(duration))
        logger.info(f"[ANALYST MEMORY] Loaded {memory.get('count',0)} memories for {sid}")
        return {**output, "metrics": metrics}

    except Exception as e:
        duration = (time.time() - t0) * 1000
        _lf_span(trace, "load_memory", {"store_id": sid},
                 {"error": str(e)}, duration, "ERROR")
        log.node_error("load_memory", log_id, e, state)
        warnings = list(state.get("warnings") or [])
        warnings.append(f"memory load failed: {e}")
        return {**state, "analyst_memory": {"count": 0, "latest": {}, "history": []},
                "warnings": warnings}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 4 — compare_with_memory
# ══════════════════════════════════════════════════════════════════════════════

async def node_compare_with_memory(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("compare_with_memory", state)
    t0     = time.time()
    trace  = _get_or_create_trace(state)

    pos_data = state.get("pos_data")          or {}
    memory   = state.get("analyst_memory")    or {}
    features = state.get("analysis_features") or {}

    current = {
        "gap_pct":         state.get("gap_objectif", 0),
        "urgency_level":   state.get("urgency_level", ""),
        "current_revenue": pos_data.get("current_revenue", 0),
        "avg_ticket":      features.get("avg_ticket", 0),
    }

    insights = compare_with_memory(current, memory)
    duration = (time.time() - t0) * 1000
    output   = {**state, "memory_insights": insights}

    _lf_span(trace, "compare_with_memory",
        input_data  = current,
        output_data = insights,
        latency_ms  = duration,
    )

    log.node_done("compare_with_memory", log_id, output, duration, insights)
    logger.info(f"[ANALYST MEMORY] gap_trend={insights.get('gap_trend','unknown')} "
                f"revenue_trend={insights.get('revenue_trend','unknown')}")
    return {**output, "metrics": _update_metrics(state, "analyste_memory_compare_ms", round(duration))}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 5 — build_strategy_query
# ══════════════════════════════════════════════════════════════════════════════

async def node_build_strategy_query(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("build_strategy_query", state)
    t0     = time.time()
    trace  = _get_or_create_trace(state)

    urgency      = state.get("urgency_level", "LOW")
    gap_pct      = float(state.get("gap_objectif", 0)  or 0)
    gap_amount   = float(state.get("gap_amount",   0)  or 0)
    forecast_eod = float(state.get("forecast_eod", 0)  or 0)
    pos_data        = state.get("pos_data")           or {}
    features        = state.get("analysis_features")  or {}
    memory_insights = state.get("memory_insights")    or {}

    top_categories = features.get("top_categories", [])
    top_products   = features.get("top_products",   [])
    daily_target   = float(pos_data.get("daily_target", 0) or 0)
    category_text  = " ".join([c["category"] for c in top_categories[:3]])
    product_text   = " ".join([p["product"]  for p in top_products[:3]])

    if pos_data.get("data_status") == "unavailable":
        strategy_intent = "vérifier source données POS avant toute recommandation"
        strategy_query  = (
            "données POS indisponibles vérifier source éviter recommandation commerciale "
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

        # Signaux temps réel du moteur TS (heures en retard, tendance, faisabilité)
        ts        = state.get("ts_analysis") or {}
        trend     = state.get("trend_signal") or ts.get("trend_signal", "")
        feas      = state.get("feasibility")  or ts.get("feasibility", "")
        gap_hours = state.get("hourly_gaps")  or ts.get("hourly_gaps", [])
        ts_parts  = []
        if trend == "DECELERATING":
            ts_parts.append("rythme vente en décélération relance immédiate")
        elif trend == "ACCELERATING":
            ts_parts.append("rythme vente en accélération capitaliser momentum")
        if gap_hours:
            hrs = " ".join(f"{e['hour']}h" for e in gap_hours[:3])
            ts_parts.append(f"heures sous performance {hrs}")
        if feas == "VERY_HARD":
            ts_parts.append("rattrapage très difficile prioriser actions fort impact")
        next_h = (state.get("next_hours_forecast") or ts.get("next_hours") or [])
        if next_h:
            ts_parts.append(f"prochaine heure attendue {next_h[0].get('expected_ca', 0):.0f} TND")

        strategy_query = (
            f"{intent} {strategy_intent} "
            f"gap {gap_pct:.1f}% montant restant {gap_amount:.0f} TND "
            f"forecast fin journée {forecast_eod:.0f} TND "
            f"panier moyen {features.get('avg_ticket', 0)} TND "
            f"tendance gap {memory_insights.get('gap_trend', 'unknown')} "
            f"{' '.join(ts_parts)} "
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
        "forecast_mape":   state.get("forecast_mape", 0),
        "forecast_engine": (state.get("ts_analysis") or {}).get("model_engine", ""),
        "forecast_ci":     {"low":  (state.get("ts_analysis") or {}).get("eod_ci_low", 0),
                            "high": (state.get("ts_analysis") or {}).get("eod_ci_high", 0)},
        "tomorrow_forecast": (state.get("ts_analysis") or {}).get("tomorrow_forecast", 0),
        "trend_signal":    state.get("trend_signal", "UNKNOWN"),
        "feasibility":     state.get("feasibility", "UNKNOWN"),
        "hourly_ledger":   (state.get("ts_analysis") or {}).get("hourly_ledger", []),
        "hourly_gaps":     state.get("hourly_gaps", []),
        "next_hours":      state.get("next_hours_forecast", []),
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

    _lf_span(trace, "build_strategy_query",
        input_data  = {"urgency": urgency, "gap_pct": gap_pct},
        output_data = {"strategy_intent": strategy_intent,
                       "rag_query": strategy_query[:200], "route_to": "strategie"},
        latency_ms  = duration,
    )

    log.node_done("build_strategy_query", log_id, output, duration,
                  {"rag_query": strategy_query[:200], "route_to": "strategie"})
    logger.info(f"[ANALYST] Node build_strategy_query — query='{strategy_query[:80]}...'")
    return {**output, "metrics": _update_metrics(state, "analyste_strategy_query_ms", round(duration))}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 6 — save_memory
# ══════════════════════════════════════════════════════════════════════════════

async def node_save_memory(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("analyste", cid, sid)
    log_id = log.node_start("save_memory", state)
    t0     = time.time()
    trace  = _get_or_create_trace(state)

    try:
        memory_payload = build_analyst_memory_payload(state)
        saved = await save_analyst_memory(
            store_id=sid, cycle_id=cid,
            memory_data=memory_payload, memory_type="cycle_summary",
        )
        duration = (time.time() - t0) * 1000
        output   = {**state, "analyst_memory_saved": saved}

        _lf_span(trace, "save_memory",
            input_data  = {"store_id": sid, "gap_pct": memory_payload.get("gap_pct"),
                           "urgency": memory_payload.get("urgency_level")},
            output_data = {"saved": saved},
            latency_ms  = duration,
        )

        # Score final du cycle Analyste
        try:
            from app.core.langfuse import get_langfuse
            lf = get_langfuse()
            if lf:
                urgency_w = {"CRITICAL": 1.0, "HIGH": 0.8, "MEDIUM": 0.5, "LOW": 0.2}.get(
                    state.get("urgency_level", "LOW"), 0.3)
                metrics  = state.get("metrics") or {}
                total_ms = sum(metrics.get(k, 0) for k in
                    ["analyste_receive_ms", "analyste_validate_ms", "analyste_feature_ms",
                     "analyste_gap_ms", "analyste_timesfm_ms", "analyste_urgency_ms",
                     "analyste_llm_ms"])
                speed_w  = max(0.0, 1.0 - total_ms / 30000)
                llm_ok_w = 0.3 if metrics.get("llm_calls", 0) > 0 else 0.0
                score_val = round(urgency_w * 0.4 + speed_w * 0.3 + llm_ok_w, 3)
                lf.score(
                    trace_id = cid,
                    name     = "analyste-quality",
                    value    = score_val,
                    comment  = (
                        f"urgency={state.get('urgency_level','?')} "
                        f"gap={state.get('gap_objectif',0):.1f}% "
                        f"total={total_ms:.0f}ms"
                    ),
                )
                lf.flush()
        except Exception:
            pass

        log.node_done("save_memory", log_id, output, duration,
                      {"memory_saved": saved,
                       "gap_pct":      memory_payload.get("gap_pct"),
                       "urgency":      memory_payload.get("urgency_level")})
        metrics = _update_metrics(state, "analyste_memory_save_ms", round(duration))
        logger.info(f"[ANALYST MEMORY] saved={saved} cycle={cid}")
        return {**output, "metrics": metrics}

    except Exception as e:
        duration = (time.time() - t0) * 1000
        _lf_span(trace, "save_memory", {"store_id": sid},
                 {"error": str(e)}, duration, "ERROR")
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
    if trend == "improving":  trend_text = " La tendance s'améliore."
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
