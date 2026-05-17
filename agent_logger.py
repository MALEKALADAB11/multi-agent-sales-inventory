"""
agent_logger.py — Sauvegarde PostgreSQL de tous les états/logs/erreurs agents.
Consommable par l'agent monitoring via la table agent_logs.

Tables créées :
  - agent_logs       : logs détaillés de chaque node LangGraph
  - agent_cycles     : résumé de chaque cycle complet
  - agent_errors     : erreurs isolées pour alerting
  - rag_feedback     : feedback sur la pertinence RAG (pour amélioration)
"""

import json
import logging
import os
import traceback
from datetime import datetime
from typing import Any, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

_DB_CONFIG = {
    "host":     "localhost",
    "port":     5432,
    "dbname":   "ooredoo_sales",
    "user":     "postgres",
    "password": "admin",
}


# ── Connexion ─────────────────────────────────────────────────────────────────

def _get_conn():
    os.environ['PGCLIENTENCODING'] = 'UTF8'
    conn = psycopg2.connect(**_DB_CONFIG)
    conn.set_client_encoding('UTF8')
    return conn


def _safe_json(obj: Any) -> str:
    """Sérialise en JSON en ignorant les objets non sérialisables."""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return "{}"


# ── Setup tables ──────────────────────────────────────────────────────────────

def setup_monitoring_tables():
    """Crée toutes les tables nécessaires pour le monitoring."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
            -- Table principale des logs agents
            CREATE TABLE IF NOT EXISTS agent_logs (
                id              SERIAL PRIMARY KEY,
                cycle_id        VARCHAR(50),
                store_id        VARCHAR(10) DEFAULT 'I63',
                agent_name      VARCHAR(30),    -- analyste | stratege | coach | rag
                node_name       VARCHAR(50),    -- nom du node LangGraph
                status          VARCHAR(20),    -- started | completed | error | fallback
                input_state     JSONB,          -- état en entrée du node
                output_state    JSONB,          -- état en sortie du node
                duration_ms     FLOAT,
                error_msg       TEXT,
                metadata        JSONB,          -- données extra (nb_actions, rag_used, etc.)
                created_at      TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_logs_cycle   ON agent_logs(cycle_id);
            CREATE INDEX IF NOT EXISTS idx_logs_agent   ON agent_logs(agent_name);
            CREATE INDEX IF NOT EXISTS idx_logs_status  ON agent_logs(status);
            CREATE INDEX IF NOT EXISTS idx_logs_created ON agent_logs(created_at DESC);

            -- Table résumé des cycles complets
            CREATE TABLE IF NOT EXISTS agent_cycles (
                id              SERIAL PRIMARY KEY,
                cycle_id        VARCHAR(50) UNIQUE,
                store_id        VARCHAR(10) DEFAULT 'I63',
                triggered_by    VARCHAR(20),    -- cron | startup | event | manual
                urgency_level   VARCHAR(10),
                urgency_score   FLOAT,
                gap_pct         FLOAT,
                gap_amount      FLOAT,
                ca_today        FLOAT,
                ca_target       FLOAT,
                forecast_eod    FLOAT,
                analyst_summary TEXT,
                strategie       TEXT,
                nb_actions      INT DEFAULT 0,
                cause_racine    TEXT,
                rag_used        BOOLEAN DEFAULT FALSE,
                nb_rag_scripts  INT DEFAULT 0,
                weather_label   VARCHAR(50),
                weather_effect  FLOAT,
                total_ms        FLOAT,
                nodes_executed  INT DEFAULT 0,
                errors_count    INT DEFAULT 0,
                status          VARCHAR(20) DEFAULT 'completed',
                created_at      TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_cycles_created  ON agent_cycles(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_cycles_store    ON agent_cycles(store_id);
            CREATE INDEX IF NOT EXISTS idx_cycles_urgency  ON agent_cycles(urgency_level);

            -- Table erreurs isolées pour alerting monitoring
            CREATE TABLE IF NOT EXISTS agent_errors (
                id              SERIAL PRIMARY KEY,
                cycle_id        VARCHAR(50),
                store_id        VARCHAR(10) DEFAULT 'I63',
                agent_name      VARCHAR(30),
                node_name       VARCHAR(50),
                error_type      VARCHAR(50),    -- timeout | llm_error | pg_error | rag_error
                error_msg       TEXT,
                traceback_txt   TEXT,
                context         JSONB,          -- état au moment de l'erreur
                resolved        BOOLEAN DEFAULT FALSE,
                created_at      TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_errors_cycle   ON agent_errors(cycle_id);
            CREATE INDEX IF NOT EXISTS idx_errors_type    ON agent_errors(error_type);
            CREATE INDEX IF NOT EXISTS idx_errors_created ON agent_errors(created_at DESC);

            -- Table feedback RAG pour amélioration continue
            CREATE TABLE IF NOT EXISTS rag_feedback (
                id              SERIAL PRIMARY KEY,
                cycle_id        VARCHAR(50),
                store_id        VARCHAR(10) DEFAULT 'I63',
                query           TEXT,
                nb_results      INT,
                top_category    VARCHAR(50),
                top_score       FLOAT,
                action_used     TEXT,           -- action générée depuis le RAG
                was_useful      BOOLEAN,        -- feedback conseiller (futur)
                context         JSONB,
                created_at      TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_rag_cycle   ON rag_feedback(cycle_id);
            CREATE INDEX IF NOT EXISTS idx_rag_created ON rag_feedback(created_at DESC);
            """)
        conn.commit()
        logger.info("[AGENT_LOGGER] Tables monitoring créées ✅")
    except Exception as e:
        logger.error(f"[AGENT_LOGGER] Erreur setup tables: {e}")
    finally:
        conn.close()


# ── Log node ──────────────────────────────────────────────────────────────────

def log_node_start(
    cycle_id: str,
    agent_name: str,
    node_name: str,
    input_state: dict,
    store_id: str = "I63",
) -> int:
    """Log le démarrage d'un node. Retourne l'ID du log."""
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            # Filtrer les clés lourdes pour éviter les gros JSONB
            safe_input = {
                k: v for k, v in input_state.items()
                if k not in ("pos_history", "rag_context", "feedback_history")
                and not isinstance(v, (bytes, bytearray))
            }
            cur.execute("""
                INSERT INTO agent_logs
                    (cycle_id, store_id, agent_name, node_name, status, input_state, created_at)
                VALUES (%s, %s, %s, %s, 'started', %s::jsonb, NOW())
                RETURNING id
            """, (cycle_id, store_id, agent_name, node_name, _safe_json(safe_input)))
            log_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        return log_id
    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] log_node_start error: {e}")
        return -1


def log_node_complete(
    log_id: int,
    output_state: dict,
    duration_ms: float,
    metadata: Optional[dict] = None,
    status: str = "completed",
):
    """Met à jour le log d'un node avec l'output et la durée."""
    if log_id < 0:
        return
    try:
        conn = _get_conn()
        safe_output = {
            k: v for k, v in output_state.items()
            if k not in ("pos_history", "rag_context", "feedback_history")
            and not isinstance(v, (bytes, bytearray))
        }
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE agent_logs
                SET status       = %s,
                    output_state = %s::jsonb,
                    duration_ms  = %s,
                    metadata     = %s::jsonb
                WHERE id = %s
            """, (
                status,
                _safe_json(safe_output),
                round(duration_ms, 2),
                _safe_json(metadata or {}),
                log_id,
            ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] log_node_complete error: {e}")


def log_node_error(
    log_id: int,
    cycle_id: str,
    agent_name: str,
    node_name: str,
    error: Exception,
    context: Optional[dict] = None,
    store_id: str = "I63",
):
    """Log une erreur sur un node."""
    error_msg = str(error)[:500]
    tb        = traceback.format_exc()[:2000]
    error_type = type(error).__name__

    # Détecter le type d'erreur
    if "timeout" in error_msg.lower() or "CancelledError" in error_type:
        error_type = "timeout"
    elif "llm" in error_msg.lower() or "ollama" in error_msg.lower():
        error_type = "llm_error"
    elif "milvus" in error_msg.lower() or "rag" in error_msg.lower():
        error_type = "rag_error"
    elif "postgres" in error_msg.lower() or "psycopg" in error_msg.lower():
        error_type = "pg_error"

    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            # Mettre à jour le log principal
            if log_id >= 0:
                cur.execute("""
                    UPDATE agent_logs
                    SET status    = 'error',
                        error_msg = %s
                    WHERE id = %s
                """, (error_msg, log_id))

            # Insérer dans la table erreurs
            safe_ctx = {
                k: v for k, v in (context or {}).items()
                if k not in ("pos_history", "feedback_history")
            }
            cur.execute("""
                INSERT INTO agent_errors
                    (cycle_id, store_id, agent_name, node_name,
                     error_type, error_msg, traceback_txt, context)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """, (
                cycle_id, store_id, agent_name, node_name,
                error_type, error_msg, tb, _safe_json(safe_ctx),
            ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] log_node_error failed: {e}")


# ── Log cycle complet ─────────────────────────────────────────────────────────

def log_cycle(
    cycle_id:       str,
    state:          dict,
    total_ms:       float,
    triggered_by:   str = "cron",
    store_id:       str = "I63",
    nodes_executed: int = 0,
    errors_count:   int = 0,
    rag_used:       bool = False,
    nb_rag_scripts: int = 0,
):
    """Sauvegarde le résumé d'un cycle complet."""
    try:
        pos_data   = state.get("pos_data")          or {}
        ext_ctx    = state.get("external_context")  or {}
        weather    = (ext_ctx.get("summary")        or {})
        actions    = state.get("strategie_actions") or []

        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO agent_cycles (
                    cycle_id, store_id, triggered_by,
                    urgency_level, urgency_score,
                    gap_pct, gap_amount, ca_today, ca_target, forecast_eod,
                    analyst_summary, strategie, nb_actions, cause_racine,
                    rag_used, nb_rag_scripts,
                    weather_label, weather_effect,
                    total_ms, nodes_executed, errors_count, status
                ) VALUES (
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s, %s
                )
                ON CONFLICT (cycle_id) DO UPDATE SET
                    urgency_level   = EXCLUDED.urgency_level,
                    urgency_score   = EXCLUDED.urgency_score,
                    gap_pct         = EXCLUDED.gap_pct,
                    analyst_summary = EXCLUDED.analyst_summary,
                    strategie       = EXCLUDED.strategie,
                    nb_actions      = EXCLUDED.nb_actions,
                    rag_used        = EXCLUDED.rag_used,
                    total_ms        = EXCLUDED.total_ms,
                    errors_count    = EXCLUDED.errors_count,
                    status          = EXCLUDED.status
            """, (
                cycle_id, store_id, triggered_by,
                state.get("urgency_level",  "LOW"),
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
                rag_used,
                nb_rag_scripts,
                weather.get("weather_label",  ""),
                float(weather.get("weather_effect", 0)),
                round(total_ms, 2),
                nodes_executed,
                errors_count,
                "error" if errors_count > 0 else "completed",
            ))
        conn.commit()
        conn.close()
        logger.info(f"[AGENT_LOGGER] Cycle {cycle_id} sauvegardé ✅ ({total_ms:.0f}ms)")
    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] log_cycle error: {e}")


# ── Log RAG feedback ──────────────────────────────────────────────────────────

def log_rag_feedback(
    cycle_id:    str,
    query:       str,
    scripts:     list,
    action_used: str = "",
    store_id:    str = "I63",
    context:     Optional[dict] = None,
):
    """Log les résultats RAG pour analyse de pertinence."""
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO rag_feedback
                    (cycle_id, store_id, query, nb_results,
                     top_category, top_score, action_used, context)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """, (
                cycle_id, store_id,
                query[:200],
                len(scripts),
                scripts[0].get("categorie", "") if scripts else "",
                float(scripts[0].get("score", 0)) if scripts else 0.0,
                action_used[:200],
                _safe_json(context or {}),
            ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] log_rag_feedback error: {e}")


# ── Enrichissement RAG depuis les actions générées ────────────────────────────

def enrich_rag_from_cycle(
    cycle_id:    str,
    state:       dict,
    store_id:    str = "I63",
):
    """
    Après chaque cycle réussi, enrichit la base RAG Milvus avec
    les nouvelles actions générées par l'agent stratège.
    Cela améliore le RAG au fil du temps.
    """
    actions  = state.get("strategie_actions") or []
    if not actions:
        return

    ext_ctx  = state.get("external_context") or {}
    weather  = (ext_ctx.get("summary") or {})
    gap      = state.get("gap_objectif", 0)
    urgency  = state.get("urgency_level", "MEDIUM")

    try:
        import requests

        # Construire les scripts RAG depuis les actions
        new_scripts = []
        for a in actions[:3]:  # Max 3 actions par cycle
            situation = (
                f"Gap {gap:.0f}% | Urgence {urgency} | "
                f"Météo: {weather.get('weather_label', 'Normal')} "
                f"({weather.get('weather_effect', 0):+.0%} trafic)"
            )
            script = {
                "categorie":      f"cycle_{urgency.lower()}",
                "situation":      situation[:250],
                "action":         (a.get("action", "")        or "")[:250],
                "produit_cible":  (a.get("produit_cible", "")  or "")[:100],
                "argument_vente": (a.get("argument_vente", "") or "")[:250],
                "impact_observe": (a.get("impact_estime", "")  or "")[:100],
                "heure_min":      9,
                "heure_max":      20,
                "jour_semaine":   -1,
                "source":         f"cycle_{cycle_id}",
                "store_id":       store_id,
            }
            new_scripts.append(script)

        if not new_scripts:
            return

        # Sauvegarder dans PostgreSQL
        conn = _get_conn()
        pg_ids = []
        with conn.cursor() as cur:
            for s in new_scripts:
                cur.execute("""
                    INSERT INTO coaching_scripts
                        (store_id, categorie, situation, action, produit_cible,
                         argument_vente, impact_observe, heure_min, heure_max,
                         jour_semaine, source)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                """, (
                    s["store_id"], s["categorie"], s["situation"],
                    s["action"], s["produit_cible"], s["argument_vente"],
                    s["impact_observe"], s["heure_min"], s["heure_max"],
                    s["jour_semaine"], s["source"],
                ))
                pg_ids.append(cur.fetchone()[0])
        conn.commit()
        conn.close()

        # Générer les embeddings et insérer dans Milvus
        try:
            from pymilvus import MilvusClient

            client = MilvusClient(uri="http://localhost:19530")

            data = []
            for script, pg_id in zip(new_scripts, pg_ids):
                text = (
                    f"Situation: {script['situation']} "
                    f"Action: {script['action']} "
                    f"Produit: {script['produit_cible']} "
                    f"Argument: {script['argument_vente']}"
                )
                resp = requests.post(
                    "http://localhost:11434/api/embeddings",
                    json={"model": "nomic-embed-text", "prompt": text},
                    timeout=15,
                )
                embedding = resp.json().get("embedding", [])
                if not embedding:
                    continue

                # Forcer dimension 768
                if len(embedding) < 768:
                    embedding = embedding + [0.0] * (768 - len(embedding))
                elif len(embedding) > 768:
                    embedding = embedding[:768]

                data.append({
                    "vector":       embedding,
                    "pg_id":        pg_id,
                    "categorie":    script["categorie"],
                    "situation":    script["situation"][:200],
                    "action":       script["action"][:200],
                    "produit":      script["produit_cible"][:100],
                    "argument":     script["argument_vente"][:200],
                    "impact":       script["impact_observe"][:100],
                    "heure_min":    script["heure_min"],
                    "heure_max":    script["heure_max"],
                    "jour_semaine": script["jour_semaine"],
                    "store_id":     script["store_id"],
                })

            if data:
                client.insert(collection_name="coaching_scripts", data=data)
                logger.info(f"[AGENT_LOGGER] RAG enrichi: +{len(data)} scripts (cycle {cycle_id})")

        except Exception as e:
            logger.warning(f"[AGENT_LOGGER] Milvus enrichissement: {e}")

    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] enrich_rag_from_cycle error: {e}")


# ── API monitoring — endpoints consommables ───────────────────────────────────

def get_recent_cycles(limit: int = 20, store_id: str = "I63") -> list:
    """Retourne les derniers cycles pour l'agent monitoring."""
    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    cycle_id, triggered_by, urgency_level, urgency_score,
                    gap_pct, ca_today, ca_target, forecast_eod,
                    nb_actions, rag_used, nb_rag_scripts,
                    weather_label, total_ms, nodes_executed,
                    errors_count, status, created_at
                FROM agent_cycles
                WHERE store_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (store_id, limit))
            rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] get_recent_cycles error: {e}")
        return []


def get_recent_errors(limit: int = 50, store_id: str = "I63") -> list:
    """Retourne les erreurs récentes pour l'agent monitoring."""
    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    cycle_id, agent_name, node_name, error_type,
                    error_msg, resolved, created_at
                FROM agent_errors
                WHERE store_id = %s AND resolved = FALSE
                ORDER BY created_at DESC
                LIMIT %s
            """, (store_id, limit))
            rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] get_recent_errors error: {e}")
        return []


def get_agent_stats(store_id: str = "I63", hours: int = 24) -> dict:
    """Statistiques des agents pour l'agent monitoring."""
    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    COUNT(*)                    AS total_cycles,
                    AVG(total_ms)               AS avg_duration_ms,
                    AVG(gap_pct)                AS avg_gap_pct,
                    AVG(nb_actions)             AS avg_nb_actions,
                    SUM(CASE WHEN rag_used THEN 1 ELSE 0 END) AS rag_cycles,
                    SUM(errors_count)           AS total_errors,
                    MAX(created_at)             AS last_cycle_at,
                    COUNT(DISTINCT urgency_level) AS urgency_variety
                FROM agent_cycles
                WHERE store_id = %s
                  AND created_at >= NOW() - INTERVAL '%s hours'
            """, (store_id, hours))
            stats = dict(cur.fetchone() or {})

            # Logs par agent
            cur.execute("""
                SELECT agent_name,
                       COUNT(*) AS nb_logs,
                       AVG(duration_ms) AS avg_ms,
                       SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors
                FROM agent_logs
                WHERE store_id = %s
                  AND created_at >= NOW() - INTERVAL '%s hours'
                GROUP BY agent_name
            """, (store_id, hours))
            stats["agents"] = [dict(r) for r in cur.fetchall()]

            # RAG stats
            cur.execute("""
                SELECT AVG(top_score) AS avg_rag_score,
                       COUNT(*) AS total_rag_queries,
                       AVG(nb_results) AS avg_results
                FROM rag_feedback
                WHERE store_id = %s
                  AND created_at >= NOW() - INTERVAL '%s hours'
            """, (store_id, hours))
            stats["rag"] = dict(cur.fetchone() or {})

        conn.close()
        return stats
    except Exception as e:
        logger.warning(f"[AGENT_LOGGER] get_agent_stats error: {e}")
        return {}