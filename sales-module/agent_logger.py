"""
agent_logger.py — Logging PostgreSQL complet pour tous les agents IA.
=====================================================================
Tables :
  - agent_logs         : log détaillé de chaque node LangGraph
  - agent_cycles       : résumé de chaque cycle complet
  - agent_errors       : erreurs pour alerting monitoring
  - rag_feedback       : pertinence RAG pour amélioration continue

Classe centrale :
  AgentLogger          : interface fluide à utiliser dans chaque node

Usage dans les nodes :
    from agent_logger import AgentLogger
    log    = AgentLogger("stratege", cycle_id, "I63")
    log_id = log.node_start("rag_search", state)
    ...
    log.node_done("rag_search", log_id, output, duration_ms, {"nb_scripts": 3})
    log.node_error("rag_search", log_id, exception, state)
    log.rag_log(query, scripts, action_used)
"""

import json
import logging
import os
from pathlib import Path
import traceback
from datetime import datetime
from typing import Any, Optional

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

load_dotenv(Path(__file__).resolve().parent.parent / ".env", encoding="utf-8")

logger = logging.getLogger(__name__)

_DB_CONFIG = {
    "host":     os.getenv("POSTGRES_HOST", "localhost"),
    "port":     int(os.getenv("POSTGRES_PORT", 5432)),
    "dbname":   os.getenv("POSTGRES_DB", "ooredoo_sales"),
    "user":     os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", "admin"),
}
# Clés lourdes à exclure du JSONB
_SKIP_KEYS = frozenset({
    "pos_history", "rag_context", "feedback_history",
    "coach_context", "rag_scripts", "history",
})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_conn():
    os.environ["PGCLIENTENCODING"] = "UTF8"
    conn = psycopg2.connect(**_DB_CONFIG)
    conn.set_client_encoding("UTF8")
    return conn


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return "{}"


def _clean_state(state: dict) -> dict:
    return {
        k: v for k, v in (state or {}).items()
        if k not in _SKIP_KEYS and not isinstance(v, (bytes, bytearray))
    }


def _detect_error_type(error: Exception) -> str:
    msg  = str(error).lower()
    name = type(error).__name__
    if "timeout" in msg or "cancelled" in name.lower(): return "timeout"
    if "ollama" in msg or "llm" in msg or "chat" in msg: return "llm_error"
    if "milvus" in msg or "rag" in msg:                  return "rag_error"
    if "psycopg" in msg or "postgres" in msg:            return "pg_error"
    if "embedding" in msg:                               return "embedding_error"
    return name[:30]


# Les tables de monitoring (agent_logs, agent_cycles, agent_errors,
# rag_feedback) sont creees par les migrations Alembic (db/migrations,
# baseline 0001). Plus aucun DDL au runtime.


# ══════════════════════════════════════════════════════════════════════════════
# Classe AgentLogger — interface fluide
# ══════════════════════════════════════════════════════════════════════════════

class AgentLogger:
    """
    Interface fluide pour logger tous les nodes LangGraph.

    Exemple dans un node :
        async def node_rag_search(state):
            log    = AgentLogger("stratege", state.get("metrics",{}).get("cycle_id","?"))
            log_id = log.node_start("rag_search", state)
            t0     = time.time()
            try:
                ... logique ...
                log.node_done("rag_search", log_id, output, (time.time()-t0)*1000,
                              {"nb_scripts": len(scripts), "rag_used": True})
            except Exception as e:
                log.node_error("rag_search", log_id, e, state)
    """

    def __init__(
        self,
        agent_name: str,
        cycle_id:   str,
        store_id:   str = "I63",
    ):
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
        logger.debug(
            f"[{self.agent_name.upper()}] ✓ {node_name} "
            f"({duration_ms:.0f}ms) {status}"
        )

    def node_error(
        self,
        node_name: str,
        log_id:    int,
        error:     Exception,
        context:   dict,
    ):
        log_node_error(
            log_id     = log_id,
            cycle_id   = self.cycle_id,
            agent_name = self.agent_name,
            node_name  = node_name,
            error      = error,
            context    = context,
            store_id   = self.store_id,
        )

    def rag_log(
        self,
        query:       str,
        scripts:     list,
        action_used: str = "",
        context:     Optional[dict] = None,
    ):
        log_rag_feedback(
            cycle_id    = self.cycle_id,
            query       = query,
            scripts     = scripts,
            action_used = action_used,
            store_id    = self.store_id,
            agent_name  = self.agent_name,
            context     = context,
        )

    def fallback(
        self,
        node_name:   str,
        log_id:      int,
        reason:      str,
        duration_ms: float,
    ):
        """Log un fallback (LLM indisponible, RAG vide...)."""
        log_node_complete(
            log_id       = log_id,
            output_state = {"fallback_reason": reason},
            duration_ms  = duration_ms,
            metadata     = {"fallback": True, "reason": reason},
            status       = "fallback",
        )
        logger.info(
            f"[{self.agent_name.upper()}] ⚡ {node_name} fallback: {reason[:60]}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Fonctions bas niveau
# ══════════════════════════════════════════════════════════════════════════════

def log_node_start(
    cycle_id:    str,
    agent_name:  str,
    node_name:   str,
    input_state: dict,
    store_id:    str = "I63",
) -> int:
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO agent_logs
                    (cycle_id, store_id, agent_name, node_name,
                     status, input_state, created_at)
                VALUES (%s,%s,%s,%s,'started',%s::jsonb,NOW())
                RETURNING id
            """, (
                cycle_id, store_id, agent_name, node_name,
                _safe_json(_clean_state(input_state)),
            ))
            log_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        return log_id
    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] log_node_start: {e}")
        return -1


def log_node_complete(
    log_id:       int,
    output_state: dict,
    duration_ms:  float,
    metadata:     Optional[dict] = None,
    status:       str = "completed",
):
    if log_id < 0:
        return
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE agent_logs
                SET status=%s, output_state=%s::jsonb,
                    duration_ms=%s, metadata=%s::jsonb
                WHERE id=%s
            """, (
                status,
                _safe_json(_clean_state(output_state)),
                round(duration_ms, 2),
                _safe_json(metadata or {}),
                log_id,
            ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] log_node_complete: {e}")


def log_node_error(
    log_id:     int,
    cycle_id:   str,
    agent_name: str,
    node_name:  str,
    error:      Exception,
    context:    Optional[dict] = None,
    store_id:   str = "I63",
):
    error_msg  = str(error)[:500]
    error_type = _detect_error_type(error)
    tb         = traceback.format_exc()[:2000]
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            if log_id >= 0:
                cur.execute(
                    "UPDATE agent_logs SET status='error', error_msg=%s WHERE id=%s",
                    (error_msg, log_id)
                )
            cur.execute("""
                INSERT INTO agent_errors
                    (cycle_id, store_id, agent_name, node_name,
                     error_type, error_msg, traceback_txt, context)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            """, (
                cycle_id, store_id, agent_name, node_name,
                error_type, error_msg, tb,
                _safe_json(_clean_state(context or {})),
            ))
        conn.commit()
        conn.close()
        logger.warning(
            f"[AGENT_LOGGER] ❌ [{agent_name}] {node_name} "
            f"— {error_type}: {error_msg[:60]}"
        )
    except Exception as e2:
        logger.warning(f"[AGENT_LOGGER] log_node_error failed: {e2}")


def log_cycle(
    cycle_id:       str,
    state:          dict,
    total_ms:       float,
    triggered_by:   str  = "cron",
    store_id:       str  = "I63",
    nodes_executed: int  = 0,
    errors_count:   int  = 0,
    rag_used:       bool = False,
    nb_rag_scripts: int  = 0,
):
    try:
        pos_data = state.get("pos_data")         or {}
        ext_ctx  = state.get("external_context") or {}
        weather  = ext_ctx.get("summary")         or {}
        actions  = state.get("strategie_actions") or []

        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO agent_cycles (
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
                    urgency_score   = EXCLUDED.urgency_score,
                    gap_pct         = EXCLUDED.gap_pct,
                    nb_actions      = EXCLUDED.nb_actions,
                    analyst_summary = EXCLUDED.analyst_summary,
                    strategie       = EXCLUDED.strategie,
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
        conn.commit()
        conn.close()
        logger.info(
            f"[AGENT_LOGGER] Cycle {cycle_id} sauvegardé ✅ "
            f"({total_ms:.0f}ms | urgence={state.get('urgency_level','?')} | "
            f"gap={state.get('gap_objectif',0):.0f}% | "
            f"RAG={'✓' if rag_used else '✗'}({nb_rag_scripts}) | "
            f"actions={len(actions)} | errors={errors_count})"
        )
    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] log_cycle error: {e}")


def log_rag_feedback(
    cycle_id:    str,
    query:       str,
    scripts:     list,
    action_used: str = "",
    store_id:    str = "I63",
    agent_name:  str = "stratege",
    context:     Optional[dict] = None,
):
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO rag_feedback
                    (cycle_id, store_id, agent_name, query, nb_results,
                     top_category, top_score, action_used, context)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            """, (
                cycle_id, store_id, agent_name,
                query[:200], len(scripts),
                scripts[0].get("categorie", "")   if scripts else "",
                float(scripts[0].get("score", 0)) if scripts else 0.0,
                action_used[:200],
                _safe_json(context or {}),
            ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] log_rag_feedback: {e}")


def enrich_rag_from_cycle(
    cycle_id: str,
    state:    dict,
    store_id: str = "I63",
):
    """Enrichit le RAG automatiquement après chaque cycle réussi."""
    actions = state.get("strategie_actions") or []
    if not actions:
        return
    ext_ctx = state.get("external_context") or {}
    weather = ext_ctx.get("summary") or {}
    gap     = state.get("gap_objectif", 0)
    urgency = state.get("urgency_level", "MEDIUM")

    try:
        import requests
        new_scripts = []
        for a in actions[:3]:
            situation = (
                f"Gap {gap:.0f}% urgence {urgency} "
                f"météo {weather.get('weather_label','Normal')} "
                f"effet {weather.get('weather_effect',0):+.0%}"
            )
            new_scripts.append({
                "categorie":      f"cycle_{urgency.lower()}",
                "situation":      situation[:250],
                "action":         (a.get("action",         "") or "")[:250],
                "produit_cible":  (a.get("produit_cible",  "") or "")[:100],
                "argument_vente": (a.get("argument_vente", "") or "")[:250],
                "impact_observe": (a.get("impact_estime",  "") or "")[:100],
                "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
                "store_id": store_id,
                "source": f"auto_{cycle_id[-8:]}",
            })

        conn  = _get_conn()
        pg_ids = []
        with conn.cursor() as cur:
            for s in new_scripts:
                cur.execute("""
                    INSERT INTO sales.coaching_scripts
                        (store_id,categorie,situation,action,produit_cible,
                         argument_vente,impact_observe,heure_min,heure_max,
                         jour_semaine,source)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                """, (
                    s["store_id"],s["categorie"],s["situation"],s["action"],
                    s["produit_cible"],s["argument_vente"],s["impact_observe"],
                    s["heure_min"],s["heure_max"],s["jour_semaine"],s["source"],
                ))
                pg_ids.append(cur.fetchone()[0])
        conn.commit()
        conn.close()

        try:
            from pymilvus import MilvusClient
            client = MilvusClient(uri="http://localhost:19530")
            data   = []
            for script, pg_id in zip(new_scripts, pg_ids):
                text = (
                    f"Situation: {script['situation']} "
                    f"Action: {script['action']} "
                    f"Produit: {script['produit_cible']} "
                    f"Argument: {script['argument_vente']}"
                )
                r   = requests.post(
                    "http://localhost:11434/api/embeddings",
                    json={"model":"nomic-embed-text","prompt":text},
                    timeout=15,
                )
                emb = r.json().get("embedding",[])
                if not emb: continue
                if len(emb) < 768: emb += [0.0]*(768-len(emb))
                data.append({
                    "vector":emb[:768],"pg_id":pg_id,
                    "categorie":script["categorie"][:200],
                    "situation":script["situation"][:1000],
                    "action":script["action"][:500],
                    "produit":script["produit_cible"][:300],
                    "argument":script["argument_vente"][:1000],
                    "impact":script["impact_observe"][:300],
                    "heure_min":9,"heure_max":20,"jour_semaine":-1,
                    "store_id":store_id[:50],
                })
            if data:
                client.insert(collection_name="coaching_scripts",data=data)
                conn2 = _get_conn()
                with conn2.cursor() as cur:
                    cur.execute(
                        "UPDATE coaching_scripts SET embedded=TRUE WHERE id=ANY(%s)",
                        (pg_ids,)
                    )
                conn2.commit()
                conn2.close()
                logger.info(
                    f"[AGENT_LOGGER] RAG enrichi: +{len(data)} scripts "
                    f"(cycle {cycle_id[-8:]})"
                )
        except Exception as e:
            logger.warning(f"[AGENT_LOGGER] Milvus enrichissement: {e}")
    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] enrich_rag_from_cycle: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# API queries pour le monitoring frontend
# ══════════════════════════════════════════════════════════════════════════════

def get_recent_cycles(limit: int = 20, store_id: str = "I63") -> list:
    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT cycle_id, triggered_by, urgency_level, urgency_score,
                       gap_pct, ca_today, ca_target, forecast_eod,
                       nb_actions, rag_used, nb_rag_scripts,
                       weather_label, total_ms, nodes_executed,
                       errors_count, status, created_at
                FROM agent_cycles
                WHERE store_id=%s
                ORDER BY created_at DESC LIMIT %s
            """, (store_id, limit))
            rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] get_recent_cycles: {e}")
        return []


def get_recent_errors(limit: int = 50, store_id: str = "I63") -> list:
    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, cycle_id, agent_name, node_name, error_type,
                       error_msg, resolved, created_at
                FROM agent_errors
                WHERE store_id=%s AND resolved=FALSE
                ORDER BY created_at DESC LIMIT %s
            """, (store_id, limit))
            rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] get_recent_errors: {e}")
        return []


def get_agent_stats(store_id: str = "I63", hours: int = 24) -> dict:
    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT COUNT(*)                                           AS total_cycles,
                       AVG(total_ms)                                      AS avg_duration_ms,
                       AVG(gap_pct)                                       AS avg_gap_pct,
                       AVG(nb_actions)                                    AS avg_nb_actions,
                       SUM(CASE WHEN rag_used  THEN 1 ELSE 0 END)         AS rag_cycles,
                       SUM(errors_count)                                   AS total_errors,
                       MAX(created_at)                                     AS last_cycle_at,
                       SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                       SUM(CASE WHEN status='error'     THEN 1 ELSE 0 END) AS failed
                FROM agent_cycles
                WHERE store_id=%s AND created_at >= NOW() - INTERVAL '%s hours'
            """, (store_id, hours))
            stats = dict(cur.fetchone() or {})

            cur.execute("""
                SELECT agent_name,
                       COUNT(*)                                            AS nb_logs,
                       AVG(duration_ms)                                    AS avg_ms,
                       SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                       SUM(CASE WHEN status='error'     THEN 1 ELSE 0 END) AS errors,
                       SUM(CASE WHEN status='fallback'  THEN 1 ELSE 0 END) AS fallbacks
                FROM agent_logs
                WHERE store_id=%s AND created_at >= NOW() - INTERVAL '%s hours'
                GROUP BY agent_name ORDER BY nb_logs DESC
            """, (store_id, hours))
            stats["agents"] = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT agent_name,
                       COUNT(*)        AS total_queries,
                       AVG(top_score)  AS avg_score,
                       AVG(nb_results) AS avg_results,
                       MAX(top_score)  AS best_score
                FROM rag_feedback
                WHERE store_id=%s AND created_at >= NOW() - INTERVAL '%s hours'
                GROUP BY agent_name
            """, (store_id, hours))
            stats["rag"] = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT top_category, COUNT(*) AS nb
                FROM rag_feedback
                WHERE store_id=%s AND created_at >= NOW() - INTERVAL '%s hours'
                  AND top_category != ''
                GROUP BY top_category ORDER BY nb DESC LIMIT 5
            """, (store_id, hours))
            stats["top_rag_categories"] = [dict(r) for r in cur.fetchall()]

            try:
                cur.execute("""
                    SELECT COUNT(*)                                    AS total,
                           AVG(confidence)                             AS avg_confidence,
                           SUM(CASE WHEN rag_used THEN 1 ELSE 0 END)  AS rag_used,
                           COUNT(DISTINCT advisor_name)                AS nb_advisors
                    FROM coach_interactions
                    WHERE created_at >= NOW() - INTERVAL '%s hours'
                """, (hours,))
                stats["coach"] = dict(cur.fetchone() or {})
            except Exception:
                stats["coach"] = {}

        conn.close()
        return stats
    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] get_agent_stats: {e}")
        return {}


def get_recent_logs(
    limit:      int = 100,
    store_id:   str = "I63",
    agent_name: str = "",
    status:     str = "",
) -> list:
    try:
        filters = ["store_id=%s"]
        params  = [store_id]
        if agent_name:
            filters.append("agent_name=%s")
            params.append(agent_name)
        if status:
            filters.append("status=%s")
            params.append(status)
        params.append(limit)

        conn = _get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"""
                SELECT id, cycle_id, agent_name, node_name, status,
                       duration_ms, error_msg, metadata, created_at
                FROM agent_logs
                WHERE {' AND '.join(filters)}
                ORDER BY created_at DESC LIMIT %s
            """, params)
            rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] get_recent_logs: {e}")
        return []


def get_rag_stats(store_id: str = "I63", limit: int = 50) -> list:
    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT cycle_id, agent_name, query, nb_results,
                       top_category, top_score, created_at
                FROM rag_feedback
                WHERE store_id=%s
                ORDER BY created_at DESC LIMIT %s
            """, (store_id, limit))
            rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] get_rag_stats: {e}")
        return []


def resolve_error(error_id: int) -> bool:
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_errors SET resolved=TRUE WHERE id=%s",
                (error_id,)
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] resolve_error: {e}")
        return False
