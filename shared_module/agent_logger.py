"""
shared_module/agent_logger.py — Logger centralisé pour tous les agents.
Écrit dans le schéma `monitoring` de la DB partagée.
Utilisé par : sales-module, inventory-module, monitoring-module.
"""

import json
import logging
import traceback
from datetime import datetime
from typing import Any, Optional

from .config import config
from .db import get_conn, safe_json

logger = logging.getLogger(__name__)

_SCHEMA = config.SCHEMA_MONITORING
_SKIP_KEYS = frozenset({
    "pos_history", "rag_context", "feedback_history",
    "coach_context", "rag_scripts", "history",
})


def _clean(state: dict) -> dict:
    return {
        k: v for k, v in (state or {}).items()
        if k not in _SKIP_KEYS and not isinstance(v, (bytes, bytearray))
    }


def _error_type(error: Exception) -> str:
    msg  = str(error).lower()
    name = type(error).__name__
    if "timeout"  in msg or "cancelled" in name.lower(): return "timeout"
    if "ollama"   in msg or "llm"       in msg:          return "llm_error"
    if "milvus"   in msg or "rag"       in msg:          return "rag_error"
    if "psycopg"  in msg or "postgres"  in msg:          return "pg_error"
    if "embedding" in msg:                               return "embedding_error"
    return name[:30]


# ══════════════════════════════════════════════════════════════════════════════
# Classe AgentLogger — interface fluide
# ══════════════════════════════════════════════════════════════════════════════

class AgentLogger:
    """
    Logger fluide à utiliser dans chaque node LangGraph.

    Usage :
        log    = AgentLogger("analyste", cycle_id, "I63")
        log_id = log.node_start("compute_gap", state)
        ...
        log.node_done("compute_gap", log_id, output, duration_ms,
                      {"gap_pct": 45.2})
        log.node_error("compute_gap", log_id, exception, state)
        log.rag_log(query, scripts)
        log.fallback("compute_gap", log_id, reason, duration_ms)
    """

    def __init__(self, agent_name: str, cycle_id: str, store_id: str = "I63"):
        self.agent_name = agent_name
        self.cycle_id   = cycle_id
        self.store_id   = store_id

    def node_start(self, node_name: str, state: dict) -> int:
        return log_node_start(
            cycle_id    = self.cycle_id,
            agent_name  = self.agent_name,
            node_name   = node_name,
            input_state = state,
            store_id    = self.store_id,
        )

    def node_done(
        self,
        node_name:   str,
        log_id:      int,
        output:      dict,
        duration_ms: float,
        metadata:    Optional[dict] = None,
        status:      str = "completed",
    ):
        log_node_complete(
            log_id       = log_id,
            output_state = output,
            duration_ms  = duration_ms,
            metadata     = metadata,
            status       = status,
        )

    def node_error(self, node_name: str, log_id: int,
                   error: Exception, context: dict):
        log_node_error(
            log_id     = log_id,
            cycle_id   = self.cycle_id,
            agent_name = self.agent_name,
            node_name  = node_name,
            error      = error,
            context    = context,
            store_id   = self.store_id,
        )

    def rag_log(self, query: str, scripts: list,
                action_used: str = "", context: Optional[dict] = None):
        log_rag_feedback(
            cycle_id    = self.cycle_id,
            query       = query,
            scripts     = scripts,
            action_used = action_used,
            store_id    = self.store_id,
            agent_name  = self.agent_name,
            context     = context,
        )

    def fallback(self, node_name: str, log_id: int,
                 reason: str, duration_ms: float):
        log_node_complete(
            log_id       = log_id,
            output_state = {"fallback_reason": reason},
            duration_ms  = duration_ms,
            metadata     = {"fallback": True, "reason": reason},
            status       = "fallback",
        )
        logger.info(
            f"[{self.agent_name.upper()}] ⚡ {node_name} "
            f"fallback: {reason[:60]}"
        )

    def inventory_log(self, sku: str, action: str,
                      details: dict = None, duration_ms: float = 0):
        log_inventory_action(
            cycle_id    = self.cycle_id,
            store_id    = self.store_id,
            sku         = sku,
            action      = action,
            details     = details or {},
            duration_ms = duration_ms,
        )


# ══════════════════════════════════════════════════════════════════════════════
# Fonctions bas niveau
# ══════════════════════════════════════════════════════════════════════════════

def log_node_start(
    cycle_id: str, agent_name: str, node_name: str,
    input_state: dict, store_id: str = "I63",
) -> int:
    try:
        with get_conn(_SCHEMA) as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    INSERT INTO {_SCHEMA}.agent_logs
                        (cycle_id, store_id, agent_name, node_name,
                         status, input_state, created_at)
                    VALUES (%s,%s,%s,%s,'started',%s::jsonb,NOW())
                    RETURNING id
                """, (
                    cycle_id, store_id, agent_name, node_name,
                    safe_json(_clean(input_state)),
                ))
                return cur.fetchone()[0]
    except Exception as e:
        logger.warning(f"[LOGGER] log_node_start: {e}")
        return -1


def log_node_complete(
    log_id: int, output_state: dict, duration_ms: float,
    metadata: Optional[dict] = None, status: str = "completed",
):
    if log_id < 0:
        return
    try:
        with get_conn(_SCHEMA) as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    UPDATE {_SCHEMA}.agent_logs
                    SET status=%s, output_state=%s::jsonb,
                        duration_ms=%s, metadata=%s::jsonb
                    WHERE id=%s
                """, (
                    status,
                    safe_json(_clean(output_state)),
                    round(duration_ms, 2),
                    safe_json(metadata or {}),
                    log_id,
                ))
    except Exception as e:
        logger.warning(f"[LOGGER] log_node_complete: {e}")


def log_node_error(
    log_id: int, cycle_id: str, agent_name: str, node_name: str,
    error: Exception, context: Optional[dict] = None, store_id: str = "I63",
):
    error_msg  = str(error)[:500]
    error_type = _error_type(error)
    tb         = traceback.format_exc()[:2000]
    try:
        with get_conn(_SCHEMA) as conn:
            with conn.cursor() as cur:
                if log_id >= 0:
                    cur.execute(
                        f"UPDATE {_SCHEMA}.agent_logs "
                        f"SET status='error', error_msg=%s WHERE id=%s",
                        (error_msg, log_id)
                    )
                cur.execute(f"""
                    INSERT INTO {_SCHEMA}.agent_errors
                        (cycle_id, store_id, agent_name, node_name,
                         error_type, error_msg, traceback_txt, context)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                """, (
                    cycle_id, store_id, agent_name, node_name,
                    error_type, error_msg, tb,
                    safe_json(_clean(context or {})),
                ))
        logger.warning(
            f"[LOGGER] ❌ [{agent_name}] {node_name} "
            f"— {error_type}: {error_msg[:60]}"
        )
    except Exception as e2:
        logger.warning(f"[LOGGER] log_node_error failed: {e2}")


def log_cycle(
    cycle_id: str, state: dict, total_ms: float,
    triggered_by: str = "cron", store_id: str = "I63",
    nodes_executed: int = 0, errors_count: int = 0,
    rag_used: bool = False, nb_rag_scripts: int = 0,
):
    try:
        pos_data = state.get("pos_data")         or {}
        ext_ctx  = state.get("external_context") or {}
        weather  = ext_ctx.get("summary")         or {}
        actions  = state.get("strategie_actions") or []

        with get_conn(_SCHEMA) as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    INSERT INTO {_SCHEMA}.agent_cycles (
                        cycle_id, store_id, triggered_by,
                        urgency_level, urgency_score,
                        gap_pct, gap_amount, ca_today, ca_target, forecast_eod,
                        analyst_summary, strategie, nb_actions, cause_racine,
                        rag_used, nb_rag_scripts, weather_label, weather_effect,
                        total_ms, nodes_executed, errors_count, status
                    ) VALUES (
                        %s,%s,%s, %s,%s, %s,%s,%s,%s,%s,
                        %s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s
                    )
                    ON CONFLICT (cycle_id) DO UPDATE SET
                        urgency_level   = EXCLUDED.urgency_level,
                        gap_pct         = EXCLUDED.gap_pct,
                        nb_actions      = EXCLUDED.nb_actions,
                        analyst_summary = EXCLUDED.analyst_summary,
                        rag_used        = EXCLUDED.rag_used,
                        nb_rag_scripts  = EXCLUDED.nb_rag_scripts,
                        total_ms        = EXCLUDED.total_ms,
                        errors_count    = EXCLUDED.errors_count,
                        status          = EXCLUDED.status
                """, (
                    cycle_id, store_id, triggered_by,
                    state.get("urgency_level", "LOW"),
                    float(state.get("urgency_score", 0)),
                    float(state.get("gap_objectif",  0)),
                    float(state.get("gap_amount",    0)),
                    float(pos_data.get("current_revenue", 0)),
                    float(pos_data.get("daily_target",    1007)),
                    float(state.get("forecast_eod",       0)),
                    (state.get("analyst_summary") or "")[:500],
                    (state.get("strategie")       or "")[:500],
                    len(actions),
                    (state.get("cause_racine")    or "")[:300],
                    rag_used, nb_rag_scripts,
                    weather.get("weather_label",  ""),
                    float(weather.get("weather_effect", 0)),
                    round(total_ms, 2),
                    nodes_executed, errors_count,
                    "error" if errors_count > 0 else "completed",
                ))

        logger.info(
            f"[LOGGER] ✅ Cycle {cycle_id} | "
            f"{total_ms:.0f}ms | urgence={state.get('urgency_level','?')} | "
            f"gap={state.get('gap_objectif',0):.1f}% | "
            f"RAG={'✓' if rag_used else '✗'}({nb_rag_scripts}) | "
            f"actions={len(actions)} | errors={errors_count}"
        )
    except Exception as e:
        logger.warning(f"[LOGGER] log_cycle: {e}")


def log_rag_feedback(
    cycle_id: str, query: str, scripts: list,
    action_used: str = "", store_id: str = "I63",
    agent_name: str = "stratege", context: Optional[dict] = None,
):
    try:
        with get_conn(_SCHEMA) as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    INSERT INTO {_SCHEMA}.rag_feedback
                        (cycle_id, store_id, agent_name, query, nb_results,
                         top_category, top_score, action_used, context)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                """, (
                    cycle_id, store_id, agent_name,
                    query[:200], len(scripts),
                    scripts[0].get("categorie", "")   if scripts else "",
                    float(scripts[0].get("score", 0)) if scripts else 0.0,
                    action_used[:200],
                    safe_json(context or {}),
                ))
    except Exception as e:
        logger.warning(f"[LOGGER] log_rag_feedback: {e}")


def log_inventory_action(
    cycle_id: str, store_id: str, sku: str,
    action: str, details: dict = None, duration_ms: float = 0,
):
    try:
        with get_conn(_SCHEMA) as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    INSERT INTO {_SCHEMA}.inventory_logs
                        (cycle_id, store_id, sku, action, details, duration_ms)
                    VALUES (%s,%s,%s,%s,%s::jsonb,%s)
                """, (
                    cycle_id, store_id, sku, action,
                    safe_json(details or {}), round(duration_ms, 2),
                ))
    except Exception as e:
        logger.warning(f"[LOGGER] log_inventory_action: {e}")


# ── API queries pour le monitoring frontend ───────────────────────────────────

def get_recent_cycles(limit: int = 20, store_id: str = "I63") -> list:
    try:
        with get_conn(_SCHEMA) as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT cycle_id, triggered_by, urgency_level, urgency_score,
                           gap_pct, ca_today, ca_target, forecast_eod,
                           nb_actions, rag_used, nb_rag_scripts,
                           weather_label, total_ms, nodes_executed,
                           errors_count, status, created_at
                    FROM {_SCHEMA}.agent_cycles
                    WHERE store_id=%s
                    ORDER BY created_at DESC LIMIT %s
                """, (store_id, limit))
                return [dict(zip([d[0] for d in cur.description], r))
                        for r in cur.fetchall()]
    except Exception as e:
        logger.warning(f"[LOGGER] get_recent_cycles: {e}")
        return []


def get_recent_errors(limit: int = 50, store_id: str = "I63") -> list:
    try:
        with get_conn(_SCHEMA) as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT id, cycle_id, agent_name, node_name,
                           error_type, error_msg, resolved, created_at
                    FROM {_SCHEMA}.agent_errors
                    WHERE store_id=%s AND resolved=FALSE
                    ORDER BY created_at DESC LIMIT %s
                """, (store_id, limit))
                return [dict(zip([d[0] for d in cur.description], r))
                        for r in cur.fetchall()]
    except Exception as e:
        logger.warning(f"[LOGGER] get_recent_errors: {e}")
        return []


def get_agent_stats(store_id: str = "I63", hours: int = 24) -> dict:
    try:
        with get_conn(_SCHEMA) as conn:
            with conn.cursor() as cur:
                # Cycles stats
                cur.execute(f"""
                    SELECT COUNT(*) AS total_cycles,
                           AVG(total_ms) AS avg_ms,
                           AVG(gap_pct) AS avg_gap,
                           SUM(CASE WHEN rag_used THEN 1 ELSE 0 END) AS rag_cycles,
                           SUM(errors_count) AS total_errors,
                           MAX(created_at) AS last_cycle_at,
                           SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                           SUM(CASE WHEN status='error'     THEN 1 ELSE 0 END) AS failed
                    FROM {_SCHEMA}.agent_cycles
                    WHERE store_id=%s
                      AND created_at >= NOW() - INTERVAL '{hours} hours'
                """, (store_id,))
                row = cur.fetchone()
                stats = dict(zip([d[0] for d in cur.description], row)) if row else {}

                # Stats par agent
                cur.execute(f"""
                    SELECT agent_name,
                           COUNT(*) AS nb_logs,
                           AVG(duration_ms) AS avg_ms,
                           SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                           SUM(CASE WHEN status='error'     THEN 1 ELSE 0 END) AS errors,
                           SUM(CASE WHEN status='fallback'  THEN 1 ELSE 0 END) AS fallbacks
                    FROM {_SCHEMA}.agent_logs
                    WHERE store_id=%s
                      AND created_at >= NOW() - INTERVAL '{hours} hours'
                    GROUP BY agent_name ORDER BY nb_logs DESC
                """, (store_id,))
                stats["agents"] = [
                    dict(zip([d[0] for d in cur.description], r))
                    for r in cur.fetchall()
                ]

                # RAG stats
                cur.execute(f"""
                    SELECT agent_name,
                           COUNT(*) AS total_queries,
                           AVG(top_score) AS avg_score,
                           MAX(top_score) AS best_score
                    FROM {_SCHEMA}.rag_feedback
                    WHERE store_id=%s
                      AND created_at >= NOW() - INTERVAL '{hours} hours'
                    GROUP BY agent_name
                """, (store_id,))
                stats["rag"] = [
                    dict(zip([d[0] for d in cur.description], r))
                    for r in cur.fetchall()
                ]

                return stats
    except Exception as e:
        logger.warning(f"[LOGGER] get_agent_stats: {e}")
        return {}


def resolve_error(error_id: int) -> bool:
    try:
        with get_conn(_SCHEMA) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {_SCHEMA}.agent_errors "
                    f"SET resolved=TRUE WHERE id=%s",
                    (error_id,)
                )
        return True
    except Exception as e:
        logger.warning(f"[LOGGER] resolve_error: {e}")
        return False
