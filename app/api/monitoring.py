"""
monitoring_router.py — Endpoints FastAPI pour l'agent monitoring.
Expose tous les logs, cycles, erreurs et stats des agents IA.

Ajouter dans main.py :
    from app.api.monitoring import router as monitoring_router
    app.include_router(monitoring_router)
"""
import os
import time
from decimal import Decimal
from datetime import datetime, date

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from app.core.config import DEFAULT_STORE_ID

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])

AGENT_MAP = {
    "analyste": "APP02", "stratege": "APP05",
    "coach": "APP07",    "rag": "APP10", "guardrail": "APP08",
    "analysis_agent": "INV-A", "context_agent": "INV-C", "decision_agent": "INV-D",
}

# Les 3 seuls noms d'agent inventaire valides (contrainte CHECK sur
# inventory.agent_runs — voir db/migrations/versions/001_inv_initial.py)
INVENTORY_AGENT_NAMES = ("analysis_agent", "context_agent", "decision_agent")


def _clean(obj):
    """Convertit Decimal, datetime, date en types JSON natifs."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


def _last(request: Request) -> dict:
    trigger = getattr(request.app.state, "trigger", None)
    if trigger is None:
        return {}
    return trigger.last_result or {}


# ── Helpers page Monitoring (statut / niveau de log / I-O) ─────────────────────

def _log_level(status: str) -> str:
    """Mappe le status d'un node vers un niveau de log frontend."""
    return {
        "completed": "success",
        "error":     "error",
        "fallback":  "warn",
        "started":   "info",
    }.get((status or "").lower(), "info")


def _io_keys(state) -> list:
    """Retourne les clés d'un state JSONB (input/output) sans les valeurs lourdes."""
    if not isinstance(state, dict):
        return []
    return [k for k in state.keys() if not str(k).startswith("_")]


def _io_preview(state, max_len: int = 80) -> dict:
    """Aperçu compact clé→valeur d'un state (valeurs tronquées, listes/dicts résumés)."""
    out = {}
    if not isinstance(state, dict):
        return out
    for k, v in state.items():
        if str(k).startswith("_"):
            continue
        if isinstance(v, (dict,)):
            out[k] = f"{{{len(v)} champs}}"
        elif isinstance(v, (list, tuple)):
            out[k] = f"[{len(v)} éléments]"
        elif isinstance(v, bool):
            out[k] = v
        elif isinstance(v, (int, float)):
            out[k] = v
        else:
            s = str(v)
            out[k] = s if len(s) <= max_len else s[:max_len] + "…"
    return out


@router.get("/cycles")
async def get_cycles(limit: int = 20, store_id: str = DEFAULT_STORE_ID):
    """Derniers cycles d'orchestration — pour l'agent monitoring."""
    from app.core.agent_logger import get_recent_cycles
    from fastapi.encoders import jsonable_encoder
    cycles = jsonable_encoder(get_recent_cycles(limit=limit, store_id=store_id))
    return JSONResponse({
        "cycles":    cycles,
        "total":     len(cycles),
        "store_id":  store_id,
        "timestamp": datetime.utcnow().isoformat(),
    })


@router.get("/errors")
async def get_errors(limit: int = 50, store_id: str = DEFAULT_STORE_ID):
    """Erreurs récentes non résolues — pour alerting monitoring."""
    from app.core.agent_logger import get_recent_errors
    from fastapi.encoders import jsonable_encoder
    errors = jsonable_encoder(get_recent_errors(limit=limit, store_id=store_id))
    return JSONResponse({
        "errors":    errors,
        "total":     len(errors),
        "store_id":  store_id,
        "timestamp": datetime.utcnow().isoformat(),
    })


@router.get("/stats")
async def get_stats(store_id: str = DEFAULT_STORE_ID, hours: int = 24):
    """Statistiques complètes des agents sur les X dernières heures."""
    from app.core.agent_logger import get_agent_stats
    from fastapi.encoders import jsonable_encoder
    stats = jsonable_encoder(get_agent_stats(store_id=store_id, hours=hours))
    return JSONResponse({
        "stats":     stats,
        "store_id":  store_id,
        "hours":     hours,
        "timestamp": datetime.utcnow().isoformat(),
    })


@router.get("/logs")
def get_logs(limit: int = 100, store_id: str = DEFAULT_STORE_ID,
                   agent: str = None, status: str = None):
    """Logs détaillés des nodes LangGraph."""
    from app.core.agent_logger import _get_conn
    from psycopg2.extras import RealDictCursor
    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            where = ["store_id = %s"]
            params = [store_id]
            if agent:
                where.append("agent_name = %s")
                params.append(agent)
            if status:
                where.append("status = %s")
                params.append(status)
            params.append(limit)
            cur.execute(f"""
                SELECT id, cycle_id, agent_name, node_name, status,
                       duration_ms, error_msg, metadata,
                       input_state, output_state, created_at
                FROM agent_logs
                WHERE {' AND '.join(where)}
                ORDER BY created_at DESC
                LIMIT %s
            """, params)
            logs = [_clean(dict(r)) for r in cur.fetchall()]
        conn.close()
        return JSONResponse({"logs": logs, "total": len(logs)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _inventory_agents(conn, sid: str, hours: int, runs_per_agent: int) -> list:
    """
    Télémétrie réelle des 3 agents inventaire depuis inventory.agent_runs.
    Granularité "run" (pas de JSONB input/output par node comme agent_logs —
    seulement les colonnes réellement persistées : status, items/alerts/
    recommendations générés, erreur).
    """
    from psycopg2.extras import RealDictCursor
    out = []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT agent_name,
                       COUNT(*)                                              AS nb_runs,
                       AVG(EXTRACT(EPOCH FROM (completed_at - started_at)) * 1000)
                                                                              AS avg_ms,
                       SUM(CASE WHEN status='failed'  THEN 1 ELSE 0 END)     AS errors,
                       SUM(CASE WHEN status='running' THEN 1 ELSE 0 END)     AS running,
                       MAX(started_at)                                      AS last_run
                FROM inventory.agent_runs
                WHERE store_id = %s AND started_at >= NOW() - INTERVAL '%s hours'
                GROUP BY agent_name
            """, (sid, hours))
            aggregates = cur.fetchall()

            for a in aggregates:
                name = a["agent_name"]
                nb   = int(a["nb_runs"]  or 0)
                errs = int(a["errors"]   or 0)
                running = int(a["running"] or 0)
                ms   = float(a["avg_ms"] or 0)
                succ = round(1 - errs / max(nb, 1), 3)

                cur.execute("""
                    SELECT status, started_at, completed_at, error_message,
                           items_processed, alerts_generated, recommendations_generated
                    FROM inventory.agent_runs
                    WHERE store_id = %s AND agent_name = %s
                    ORDER BY started_at DESC
                    LIMIT %s
                """, (sid, name, runs_per_agent))
                rows = cur.fetchall()

                logs = []
                for r in rows:
                    ts = r["started_at"]
                    st = (r["status"] or "").lower()
                    if st == "failed":
                        msg = f"run failed — {str(r['error_message'] or 'erreur')[:120]}"
                        level = "error"
                    elif st == "running":
                        msg = "run en cours…"
                        level = "info"
                    else:
                        msg = (f"run terminé — {r['items_processed'] or 0} items, "
                               f"{r['alerts_generated'] or 0} alerts, "
                               f"{r['recommendations_generated'] or 0} recos")
                        level = "success"
                    logs.append({
                        "time": ts.strftime("%I:%M %p") if hasattr(ts, "strftime") else str(ts),
                        "level": level, "message": msg, "node": "run",
                    })

                last_row = rows[0] if rows else None
                last_output = {}
                if last_row:
                    last_output = {
                        "status": last_row["status"],
                        "items_processed": last_row["items_processed"],
                        "alerts_generated": last_row["alerts_generated"],
                        "recommendations_generated": last_row["recommendations_generated"],
                    }
                    if last_row["error_message"]:
                        last_output["error_message"] = last_row["error_message"][:200]

                if running > 0:
                    status = "LIVE"
                elif errs > 0 and errs / max(nb, 1) >= 0.5:
                    status = "ERROR"
                elif nb > 0:
                    status = "DONE"
                else:
                    status = "IDLE"

                last_run = a["last_run"]
                out.append({
                    "agent_id": AGENT_MAP.get(name, name.upper()), "agent_name": name,
                    "status": status,
                    "avg_latency_ms": round(ms, 1), "avg_latency_s": round(ms / 1000, 2),
                    "total_runs": nb, "error_count": errs, "success_rate": succ,
                    "last_run": last_run.strftime("%I:%M %p") if hasattr(last_run, "strftime") else str(last_run),
                    "inputs": ["store_id"], "outputs": ["status", "items_processed",
                              "alerts_generated", "recommendations_generated"],
                    "last_input": {"store_id": sid, "agent_name": name},
                    "last_output": last_output,
                    "logs": logs,
                    "granularity": "run",
                    "metrics": [
                        {"label": "Avg duration", "value": f"{ms/1000:.2f}s"},
                        {"label": "Runs", "value": str(nb)},
                        {"label": "Errors", "value": str(errs),
                         "color": "#E74C3C" if errs else "#00B894"},
                        {"label": "Success", "value": f"{succ*100:.0f}%",
                         "color": "#00B894" if succ >= 0.95 else "#F9A825"},
                    ],
                })
    except Exception as e:
        # Schema inventory.agent_runs indisponible (module inventaire éteint) — non bloquant
        pass
    return out


@router.get("/agents")
def get_agents(request: Request, store_id: str = None,
                     hours: int = 24, logs_per_agent: int = 6):
    """
    Vue consolidée par agent pour la page Monitoring : statut temps réel,
    latence, logs récents ET input/output réels.
    Source : agent_logs (agents sales — JSONB node I/O) +
             inventory.agent_runs (agents inventaire — granularité run).
    Aucune donnée mockée.
    """
    from app.core.agent_logger import _get_conn
    from psycopg2.extras import RealDictCursor

    last = _last(request)
    sid  = store_id or last.get("store_id", DEFAULT_STORE_ID)

    # Statut LangGraph du cycle courant (enrichit le statut LIVE)
    live_map = {
        "analyste": bool(last.get("analyst_summary")),
        "analyst":  bool(last.get("analyst_summary")),
        "stratege": bool(last.get("strategie_actions")),
        "coach":    bool(last.get("conseil_final")),
        "rag":      bool(last.get("rag_used")),
    }

    agents = []
    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Agrégat par agent sur la fenêtre
            cur.execute("""
                SELECT agent_name,
                       COUNT(*)                                   AS nb_logs,
                       AVG(duration_ms)                           AS avg_ms,
                       MAX(created_at)                            AS last_run,
                       SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors
                FROM agent_logs
                WHERE store_id = %s
                  AND created_at >= NOW() - INTERVAL '%s hours'
                GROUP BY agent_name
                ORDER BY MAX(created_at) DESC
            """, (sid, hours))
            aggregates = cur.fetchall()

            for a in aggregates:
                name = (a["agent_name"] or "unknown").strip()
                nb   = int(a["nb_logs"] or 0)
                errs = int(a["errors"]  or 0)
                ms   = float(a["avg_ms"] or 0)
                succ = round(1 - errs / max(nb, 1), 3)

                # Logs récents (avec input/output réels)
                cur.execute("""
                    SELECT node_name, status, duration_ms, error_msg,
                           input_state, output_state, metadata, created_at
                    FROM agent_logs
                    WHERE store_id = %s AND agent_name = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (sid, name, logs_per_agent))
                rows = cur.fetchall()

                logs = []
                last_input, last_output = {}, {}
                for r in rows:
                    ts = r["created_at"]
                    node = r["node_name"] or "node"
                    st   = (r["status"] or "").lower()
                    dur  = float(r["duration_ms"] or 0)
                    if st == "error":
                        msg = f"{node} — {str(r['error_msg'] or 'erreur')[:120]}"
                    elif st == "fallback":
                        msg = f"{node} — fallback ({dur:.0f}ms)"
                    elif st == "started":
                        msg = f"{node} démarré…"
                    else:
                        msg = f"{node} terminé ({dur:.0f}ms)"
                    logs.append({
                        "time":  ts.strftime("%I:%M %p") if hasattr(ts, "strftime") else str(ts),
                        "level": _log_level(st),
                        "message": msg,
                        "node":  node,
                    })
                    # Premier log complet trouvé → snapshot I/O réel
                    if not last_output and isinstance(r["output_state"], dict) and r["output_state"]:
                        last_output = _io_preview(r["output_state"])
                    if not last_input and isinstance(r["input_state"], dict) and r["input_state"]:
                        last_input = _io_preview(r["input_state"])

                # Clés I/O agrégées (union sur les logs récents)
                in_keys, out_keys = set(), set()
                for r in rows:
                    in_keys.update(_io_keys(r["input_state"]))
                    out_keys.update(_io_keys(r["output_state"]))

                # Statut
                if live_map.get(name):
                    status = "LIVE"
                elif errs > 0 and errs / max(nb, 1) >= 0.5:
                    status = "ERROR"
                elif nb > 0:
                    status = "DONE"
                else:
                    status = "IDLE"

                last_run = a["last_run"]
                agents.append({
                    "agent_id":     AGENT_MAP.get(name, name.upper()),
                    "agent_name":   name,
                    "status":       status,
                    "avg_latency_ms": round(ms, 1),
                    "avg_latency_s":  round(ms / 1000, 2),
                    "total_runs":   nb,
                    "error_count":  errs,
                    "success_rate": succ,
                    "last_run":     last_run.strftime("%I:%M %p") if hasattr(last_run, "strftime") else str(last_run),
                    "inputs":       sorted(in_keys),
                    "outputs":      sorted(out_keys),
                    "last_input":   last_input,
                    "last_output":  last_output,
                    "logs":         logs,
                    "granularity":  "node",
                    "metrics": [
                        {"label": "Avg latency", "value": f"{ms/1000:.2f}s"},
                        {"label": "Runs (24h)",  "value": str(nb)},
                        {"label": "Errors",      "value": str(errs),
                         "color": "#E74C3C" if errs else "#00B894"},
                        {"label": "Success",     "value": f"{succ*100:.0f}%",
                         "color": "#00B894" if succ >= 0.95 else "#F9A825"},
                    ],
                })

            # ── Agents inventaire réels (analysis_agent / context_agent / decision_agent) ──
            agents.extend(_inventory_agents(conn, sid, hours, logs_per_agent))
        conn.close()
    except Exception as e:
        return JSONResponse({"error": str(e), "agents": []}, status_code=500)

    return JSONResponse({
        "agents":    agents,
        "total":     len(agents),
        "store_id":  sid,
        "hours":     hours,
        "source":    "agent_logs+inventory.agent_runs",
        "timestamp": datetime.utcnow().isoformat(),
    })


@router.get("/guardrail-events")
def get_guardrail_events(limit: int = 20, store_id: str = DEFAULT_STORE_ID):
    """
    Historique des évaluations guardrail (agent_logs, agent_name='guardrail').
    Permet au panneau "Guardrail Events" de la page Monitoring d'afficher les
    incidents passés dès le chargement, sans attendre un nouveau push WebSocket
    dans la session courante.
    """
    from app.core.agent_logger import _get_conn
    from psycopg2.extras import RealDictCursor
    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT input_state, output_state, created_at
                FROM agent_logs
                WHERE store_id = %s AND agent_name = 'guardrail'
                ORDER BY created_at DESC
                LIMIT %s
            """, (store_id, limit))
            rows = [_clean(dict(r)) for r in cur.fetchall()]
        conn.close()

        events = []
        for r in rows:
            out = r.get("output_state") or {}
            inp = r.get("input_state") or {}
            status = out.get("status", "APPROVE")
            if status == "APPROVE":
                continue  # ne montrer que les incidents (REWRITE/ESCALATE/BLOCK)
            events.append({
                "status":    status,
                "advisor":   inp.get("advisor", ""),
                "issues":    out.get("issues", []),
                "urgency":   inp.get("urgency", ""),
                "timestamp": r.get("created_at", ""),
            })
        return JSONResponse({"events": events, "total": len(events)})
    except Exception as e:
        return JSONResponse({"error": str(e), "events": []}, status_code=500)


@router.get("/rag-stats")
def get_rag_stats(store_id: str = DEFAULT_STORE_ID, limit: int = 50):
    """Statistiques RAG — pertinence et utilisation."""
    from app.core.agent_logger import _get_conn
    from psycopg2.extras import RealDictCursor
    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT query, nb_results, top_category, top_score,
                       action_used, created_at
                FROM rag_feedback
                WHERE store_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (store_id, limit))
            rows = [dict(r) for r in cur.fetchall()]

            # Stats globales
            cur.execute("""
                SELECT
                    COUNT(*)          AS total_queries,
                    AVG(top_score)    AS avg_score,
                    AVG(nb_results)   AS avg_results,
                    MAX(top_score)    AS max_score,
                    MIN(top_score)    AS min_score
                FROM rag_feedback WHERE store_id = %s
            """, (store_id,))
            stats = dict(cur.fetchone() or {})

        conn.close()
        return JSONResponse({
            "feedback": rows,
            "stats":    stats,
            "total":    len(rows),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/health")
def monitoring_health():
    """Health check du système de monitoring."""
    from app.core.agent_logger import _get_conn
    status = {"postgres": False, "milvus": False, "ollama": False}

    # PostgreSQL
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent_cycles")
            status["postgres"] = True
            status["total_cycles"] = cur.fetchone()[0]
        conn.close()
    except Exception as e:
        status["postgres_error"] = str(e)

    # Milvus
    try:
        from pymilvus import MilvusClient
        c = MilvusClient(uri="http://localhost:19530")
        status["milvus"] = c.has_collection("coaching_scripts")
        if status["milvus"]:
            status["milvus_docs"] = c.get_collection_stats("coaching_scripts").get("row_count", 0)
    except Exception as e:
        status["milvus_error"] = str(e)

    # Ollama
    try:
        import httpx
        r = httpx.get("http://localhost:11434/api/tags", timeout=3)
        status["ollama"] = r.status_code == 200
        models = [m["name"] for m in r.json().get("models", [])]
        status["ollama_models"] = models
    except Exception as e:
        status["ollama_error"] = str(e)

    all_ok = status["postgres"] and status["milvus"] and status["ollama"]
    return JSONResponse({
        "status":    "ok" if all_ok else "degraded",
        "services":  status,
        "timestamp": datetime.utcnow().isoformat(),
    })


@router.post("/errors/{error_id}/resolve")
def resolve_error(error_id: int):
    """Marque une erreur comme résolue."""
    from app.core.agent_logger import _get_conn
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_errors SET resolved=TRUE WHERE id=%s",
                (error_id,)
            )
        conn.commit()
        conn.close()
        return JSONResponse({"status": "resolved", "id": error_id})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoints consommés par la page Monitoring Angular
# Source : agent_logger (PostgreSQL) + app.state.trigger.last_result (LangGraph)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/kpis")
async def get_kpis(request: Request):
    """KPI bar : healthy, running, failed, avg_latency, cost_today_tnd."""
    last = _last(request)
    stats = {}
    try:
        from app.core.agent_logger import get_agent_stats
        stats = _clean(get_agent_stats(store_id=last.get("store_id", DEFAULT_STORE_ID), hours=24) or {})
    except Exception:
        pass

    agents_db = stats.get("agents", [])
    healthy = sum(
        1 for a in agents_db
        if float(a.get("avg_ms") or 0) < 10_000
        and int(a.get("errors") or 0) / max(int(a.get("nb_logs") or 1), 1) < 0.05
    )
    agents_status = {
        "analyste": "LIVE" if last.get("analyst_summary")   else "IDLE",
        "stratege": "LIVE" if last.get("strategie_actions") else "IDLE",
        "coach":    "LIVE" if last.get("conseil_final")     else "IDLE",
        "rag":      "DONE" if last.get("rag_used")          else "IDLE",
    }
    running   = sum(1 for s in agents_status.values() if s == "LIVE")
    latencies = [float(a.get("avg_ms") or 0) for a in agents_db if a.get("avg_ms")]
    avg_lat_s = round(sum(latencies) / len(latencies) / 1000, 2) if latencies else 0.0
    total_cyc = int(stats.get("total_cycles") or 0)
    metrics   = last.get("metrics") or {}

    return JSONResponse({
        "healthy": max(healthy, 0), "running": running,
        "failed": int(stats.get("total_errors") or 0),
        "avg_latency": avg_lat_s, "cost_today_tnd": round(total_cyc * 0.15, 2),
        "cycle_id":       last.get("cycle_id",       ""),
        "store_id":       last.get("store_id",       ""),
        "urgency_level":  last.get("urgency_level",  "LOW"),
        "urgency_score":  last.get("urgency_score",  0),
        "gap_pct":        last.get("gap_objectif",   0),
        "gap_amount":     last.get("gap_amount",     0),
        "forecast_eod":   last.get("forecast_eod",   0),
        "rag_used":       last.get("rag_used",        False),
        "nb_rag_scripts": last.get("nb_rag_scripts",  0),
        "nb_actions":     len(last.get("strategie_actions") or []),
        "total_cycles_24h": total_cyc,
        "agents_status":  agents_status,
        "nodes_executed": metrics.get("nodes_executed", 0),
        "llm_calls":      metrics.get("llm_calls",      0),
        "total_ms":       metrics.get("total_ms",       0),
        "completed_at":   metrics.get("completed_at",   ""),
        "updated_at":     datetime.utcnow().isoformat(),
        "source":         "postgresql+langgraph",
    })


@router.get("/performance")
async def get_performance(request: Request):
    """Métriques de performance par agent (24h)."""
    last = _last(request)
    stats = {}
    try:
        from app.core.agent_logger import get_agent_stats
        stats = _clean(get_agent_stats(store_id=last.get("store_id", DEFAULT_STORE_ID), hours=24) or {})
    except Exception:
        pass

    agents = []
    for a in stats.get("agents", []):
        name = str(a.get("agent_name") or "")
        nb   = int(a.get("nb_logs") or 0)
        errs = int(a.get("errors") or 0)
        ms   = float(a.get("avg_ms") or 0)
        agents.append({
            "agent_id": AGENT_MAP.get(name, name.upper()), "agent_name": name,
            "avg_latency_ms": round(ms, 1), "avg_latency_s": round(ms / 1000, 2),
            "total_runs": nb, "error_count": errs,
            "success_rate": round(1 - errs / max(nb, 1), 3), "sla_ok": ms < 10_000,
        })
    agents.sort(key=lambda x: -x["avg_latency_ms"])
    return JSONResponse({
        "agents": agents, "total_agents": len(agents),
        "sla_threshold_ms": 10_000, "period_hours": 24,
        "updated_at": datetime.utcnow().isoformat(),
    })


@router.get("/costs")
async def get_costs(request: Request):
    """Estimation des coûts journaliers par agent."""
    last = _last(request)
    stats = {}
    try:
        from app.core.agent_logger import get_agent_stats
        stats = _clean(get_agent_stats(store_id=last.get("store_id", DEFAULT_STORE_ID), hours=24) or {})
    except Exception:
        pass

    total_cyc  = int(stats.get("total_cycles") or 0)
    avg_ms     = float(stats.get("avg_duration_ms") or 0)
    llm_calls  = float((last.get("metrics") or {}).get("llm_calls", 1))
    cost_per   = 600 * 0.15 / 1_000_000 * 3.1
    api_cost   = round(total_cyc * llm_calls * cost_per, 4)
    compute    = round(total_cyc * avg_ms / 1000 * 0.00001 * 3.1, 4)
    by_agent   = {str(a.get("agent_name","")): round(int(a.get("nb_logs",0)) * cost_per, 4)
                  for a in stats.get("agents", [])}
    return JSONResponse({
        "api_cost_tnd": api_cost, "compute_cost_tnd": compute,
        "storage_cost_tnd": 0.5,
        "total_cost_tnd": round(api_cost + compute + 0.5, 3),
        "total_cycles": total_cyc, "by_agent": by_agent,
        "period_hours": 24, "currency": "TND",
        "updated_at": datetime.utcnow().isoformat(),
    })


@router.get("/health/{agent_id}")
async def get_agent_health(agent_id: str, request: Request):
    """Santé d'un agent spécifique depuis agent_logs + agent_errors."""
    last = _last(request)
    errors, stats = [], {}
    try:
        from app.core.agent_logger import get_recent_errors, get_agent_stats
        all_errors = _clean(get_recent_errors(limit=10, store_id=last.get("store_id", DEFAULT_STORE_ID)))
        inv_map    = {v: k for k, v in AGENT_MAP.items()}
        iname      = inv_map.get(agent_id.upper(), agent_id.lower())
        errors     = [e for e in all_errors if iname in str(e.get("agent_name", ""))]
        stats      = _clean(get_agent_stats(store_id=last.get("store_id", DEFAULT_STORE_ID), hours=1) or {})
    except Exception:
        iname = agent_id.lower()

    inv_map = {v: k for k, v in AGENT_MAP.items()}
    iname   = inv_map.get(agent_id.upper(), agent_id.lower())
    adb     = next((a for a in stats.get("agents", []) if a.get("agent_name") == iname), {})
    nb      = int(adb.get("nb_logs") or 0)
    errs    = int(adb.get("errors")  or 0)
    ms      = float(adb.get("avg_ms") or 0)
    live    = {"analyste": bool(last.get("analyst_summary")),
               "stratege": bool(last.get("strategie_actions")),
               "coach":    bool(last.get("conseil_final")),
               "rag":      bool(last.get("rag_used"))}.get(iname, False)
    status  = "LIVE" if live else ("ERROR" if errs > 0 else ("IDLE" if nb == 0 else "DONE"))
    return JSONResponse({
        "agent_id": agent_id, "agent_name": iname, "status": status,
        "avg_latency_ms": round(ms, 1), "total_runs_1h": nb,
        "error_count_1h": errs, "success_rate": round(1 - errs / max(nb, 1), 3),
        "sla_ok": ms < 10_000,
        "recent_errors": [{"cycle_id": e.get("cycle_id",""), "error_type": e.get("error_type",""),
                           "error_msg": str(e.get("error_msg",""))[:120],
                           "created_at": str(e.get("created_at",""))} for e in errors[:5]],
        "updated_at": datetime.utcnow().isoformat(),
    })


@router.get("/timeline")
async def get_timeline(request: Request):
    """Timeline du dernier cycle LangGraph (Gantt)."""
    last    = _last(request)
    metrics = last.get("metrics") or {}
    cycles  = []
    try:
        from app.core.agent_logger import get_recent_cycles
        cycles = _clean(get_recent_cycles(limit=1, store_id=last.get("store_id", DEFAULT_STORE_ID)))
    except Exception:
        pass

    known = {
        "analyste": float(metrics.get("analyste_ms", 0)),
        "rag":      float(metrics.get("rag_ms",      0)),
        "stratege": float(metrics.get("stratege_ms", 0)),
        "total":    float(metrics.get("total_ms",    0)),
    }
    events, t = [], 0.0
    for name in ("analyste", "rag", "stratege"):
        d = known[name]
        if d > 0:
            events.append({"agent": name, "agent_id": AGENT_MAP.get(name, name.upper()),
                           "start_ms": round(t), "end_ms": round(t+d),
                           "duration_ms": round(d), "status": "success"})
            t += d
    coach_ms = max(0, known["total"] - t)
    if coach_ms > 0:
        events.append({"agent": "coach", "agent_id": "APP07",
                       "start_ms": round(t), "end_ms": round(t+coach_ms),
                       "duration_ms": round(coach_ms), "status": "success"})
    lc = cycles[0] if cycles else {}
    return JSONResponse({
        "cycle_id": last.get("cycle_id", lc.get("cycle_id", "")),
        "total_ms": known["total"], "events": events, "nb_events": len(events),
        "completed_at": metrics.get("completed_at", lc.get("created_at", "")),
        "updated_at": datetime.utcnow().isoformat(),
    })


@router.get("/predict")
async def get_failure_prediction(request: Request):
    """Prédiction de défaillance basée sur le taux d'erreurs (50 derniers cycles)."""
    last   = _last(request)
    cycles = []
    try:
        from app.core.agent_logger import get_recent_cycles
        cycles = _clean(get_recent_cycles(limit=50, store_id=last.get("store_id", DEFAULT_STORE_ID)))
    except Exception:
        pass

    if not cycles:
        return JSONResponse({"risk_score": 0.0, "trend": "stable", "source": "no_data",
                             "historical": [], "forecast": [], "upper": [], "lower": []})

    flags   = [int(c.get("errors_count", 0) > 0) for c in cycles][::-1]
    n, win  = len(flags), 5
    rolling = [sum(flags[max(0,i-win+1):i+1]) / (i - max(0,i-win+1) + 1) for i in range(n)]
    recent  = rolling[-10:] if len(rolling) >= 10 else rolling
    slope   = (recent[-1] - recent[0]) / max(len(recent)-1, 1) if len(recent) > 1 else 0
    last_v  = recent[-1] if recent else 0.0
    fc      = [max(0.0, min(1.0, last_v + slope * i)) for i in range(1, 13)]
    return JSONResponse({
        "risk_score": round(last_v, 3),
        "trend": "rising" if slope > 0.01 else ("falling" if slope < -0.01 else "stable"),
        "trend_slope": round(slope, 4),
        "historical": [round(v,3) for v in rolling],
        "forecast": [round(v,3) for v in fc],
        "upper": [min(1.0, round(v+0.10, 3)) for v in fc],
        "lower": [max(0.0, round(v-0.10, 3)) for v in fc],
        "nb_cycles": n, "updated_at": datetime.utcnow().isoformat(),
    })


@router.get("/dependencies")
def get_dependencies():
    """Vérifie PostgreSQL, Milvus, Ollama, Redis en temps réel."""
    from app.core.agent_logger import _get_conn
    deps = {}

    try:
        t0 = time.time()
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent_cycles")
            nb = cur.fetchone()[0]
        conn.close()
        deps["postgresql"] = {"status": "ok", "latency_ms": round((time.time()-t0)*1000), "detail": f"{nb} agent_cycles"}
    except Exception as e:
        deps["postgresql"] = {"status": "error", "detail": str(e)[:120]}

    try:
        from pymilvus import MilvusClient
        t0 = time.time()
        c = MilvusClient(uri=os.getenv("MILVUS_URI", "http://localhost:19530"))
        has = c.has_collection("coaching_scripts")
        nb  = c.get_collection_stats("coaching_scripts").get("row_count", 0) if has else 0
        deps["milvus"] = {"status": "ok" if has else "degraded",
                          "latency_ms": round((time.time()-t0)*1000), "detail": f"coaching_scripts: {nb}"}
    except Exception as e:
        deps["milvus"] = {"status": "error", "detail": str(e)[:120]}

    try:
        import httpx
        t0 = time.time()
        r  = httpx.get(os.getenv("OLLAMA_BASE_URL","http://localhost:11434")+"/api/tags", timeout=3)
        models = [m["name"] for m in r.json().get("models", [])]
        deps["ollama"] = {"status": "ok" if r.status_code==200 else "degraded",
                          "latency_ms": round((time.time()-t0)*1000), "detail": f"models: {', '.join(models[:3]) or 'none'}"}
    except Exception as e:
        deps["ollama"] = {"status": "error", "detail": str(e)[:120]}

    try:
        import redis
        t0 = time.time()
        r  = redis.Redis(host=os.getenv("REDIS_HOST","localhost"), port=int(os.getenv("REDIS_PORT",6379)),
                         socket_connect_timeout=2)
        r.ping()
        deps["redis"] = {"status": "ok", "latency_ms": round((time.time()-t0)*1000), "detail": "ping ok"}
    except Exception as e:
        deps["redis"] = {"status": "error", "detail": str(e)[:120]}

    all_ok   = all(d.get("status")=="ok" for d in deps.values())
    degraded = any(d.get("status")=="degraded" for d in deps.values())
    return JSONResponse({
        "status": "ok" if all_ok else ("degraded" if degraded else "partial"),
        "dependencies": deps, "nb_ok": sum(1 for d in deps.values() if d.get("status")=="ok"),
        "nb_total": len(deps), "updated_at": datetime.utcnow().isoformat(),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Page SUPERVISION MÉTIER — feedback humain + qualité IA (juge live + RAGAS)
# Source : feedback_service (agent_feedback/hitl_reviews/purchase_orders),
#          quality_service (public.recommendation_scores), evals/ (juge + RAGAS).
# Aucune donnée mockée.
# ═══════════════════════════════════════════════════════════════════════════════

# Cache process du résumé LLM : évite un appel modèle à chaque affichage.
_SUMMARY_CACHE: dict = {}


@router.get("/feedback/overview")
async def feedback_overview(store_id: str = None, window_days: int = 30,
                            baseline_days: int = 90):
    """Cartes KPI : acceptation 30j, rejet vs moyenne 3 mois, volumes, score qualité."""
    from app.core.feedback_service import get_feedback_overview
    from app.core.quality_service import get_judge_summary
    sid = store_id or None
    ov = get_feedback_overview(store_id=sid, window_days=window_days, baseline_days=baseline_days)
    judge = get_judge_summary(store_id=sid, days=window_days)
    ov["quality"] = {
        "overall_mean": judge.get("overall_mean"),
        "overall_pct": judge.get("overall_pct"),
        "n_scored": judge.get("n_scored"),
    }
    ov["updated_at"] = datetime.utcnow().isoformat()
    return JSONResponse(_clean(ov))


def _inventory_reco_group(store_id: str, days: int) -> list:
    """Approuvé/Rejeté des recommandations de réappro (inventory.recommendations.status).
    approved/executed = accepté ; rejected/cancelled = rejeté ; pending ignoré.
    Une seule barre : le type d'action (ORDER/EXPEDITE) et l'urgence ne sont pas
    persistés en colonne — seul le statut de décision l'est."""
    from app.core.agent_logger import _get_conn
    from psycopg2.extras import RealDictCursor
    accepted = rejected = 0
    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            store_clause = "AND store_id = %(sid)s" if store_id else ""
            cur.execute(f"""
                SELECT status, COUNT(*) AS cnt
                FROM inventory.recommendations
                WHERE created_at >= NOW() - (%(days)s || ' days')::interval
                  {store_clause}
                GROUP BY status
            """, {"sid": store_id, "days": days})
            for r in cur.fetchall():
                st = (r["status"] or "").lower()
                if st in ("approved", "executed"):
                    accepted += int(r["cnt"])
                elif st in ("rejected", "cancelled"):
                    rejected += int(r["cnt"])
        conn.close()
    except Exception:
        pass
    if accepted == 0 and rejected == 0:
        return []
    return [{"key": "reco_stock", "label": "Recommandations réappro", "domain": "stock",
             "accepted": accepted, "rejected": rejected}]


@router.get("/feedback/breakdown")
async def feedback_breakdown(store_id: str = None, days: int = 30):
    """Approuvé/Rejeté par boucle de feedback réelle, taggé par domaine
    (sales / stock) pour le filtre de la page Supervision."""
    from app.core.feedback_service import get_feedback_stats
    s = get_feedback_stats(store_id=store_id or None, days=days)
    groups = [
        {"key": "incitations", "label": "Incitations coach", "domain": "sales",
         "accepted": int(s["incitations"]["followed"]),
         "rejected": int(s["incitations"]["ignored"])},
        {"key": "hitl", "label": "Stratégies HITL", "domain": "sales",
         "accepted": int(s["hitl"]["approved"]),
         "rejected": int(s["hitl"]["rejected"])},
        {"key": "po", "label": "PO stock", "domain": "stock",
         "accepted": int(s["po"].get("accepted", 0)),
         "rejected": int(s["po"].get("cancelled", 0))},
    ]
    # Détail stock : décisions sur les recommandations de réappro
    groups.extend(_inventory_reco_group(store_id or None, days))
    return JSONResponse(_clean({
        "days": days, "groups": groups,
        "updated_at": datetime.utcnow().isoformat(),
    }))


def _summary_llm(overview: dict) -> dict:
    """Résumé narratif FR via LLM (evals.common). Repli learning_context si indispo."""
    ov = overview
    facts = (
        f"- Décidés sur {ov['window_days']}j : {ov['decided']} "
        f"({ov['accepted']} acceptés / {ov['rejected']} rejetés).\n"
        f"- Taux d'acceptation : {ov.get('accept_rate')}%.\n"
        f"- Taux de rejet : {ov.get('reject_rate')}% "
        f"(moyenne 3 mois : {ov.get('baseline_reject_rate')}%, "
        f"écart {ov.get('reject_delta')} pt).\n"
        f"- Par boucle : incitations {ov['by_loop']['incitations']}, "
        f"HITL {ov['by_loop']['hitl']}, PO {ov['by_loop']['po']}.\n"
        f"- Raisons de rejet récentes : "
        f"{' | '.join(ov.get('recent_rejections') or []) or 'aucune'}."
    )
    try:
        from evals.common import load_providers, chat
        provider = model = None
        for name in ("mistral", "groq", "openrouter"):
            p = load_providers().get(name)
            if p and p.available:
                provider, model = p, p.models[-1]
                break
        if provider is None:
            raise RuntimeError("aucun provider LLM disponible")
        messages = [
            {"role": "system", "content":
                "Tu es analyste supervision d'un système multi-agents retail Ooredoo Tunisie. "
                "On te donne des statistiques de feedback humain sur les recommandations des "
                "agents (ventes + stock). Rédige un résumé de supervision en français, 3 à 4 "
                "phrases, factuel et actionnable : ce qui va, ce qui inquiète (rejets vs moyenne, "
                "boucle la plus rejetée), et une piste d'amélioration. Pas de puces, un paragraphe."},
            {"role": "user", "content": f"STATISTIQUES :\n{facts}"},
        ]
        r = chat(provider, model, messages, temperature=0.4, max_tokens=300, max_retries=1)
        if r.ok:
            return {"text": r.text, "source": f"{provider.name}/{model}"}
    except Exception as e:
        pass
    # Repli : le bloc texte déjà construit pour les prompts agents.
    try:
        from app.core.feedback_service import get_learning_context_sync
        txt = get_learning_context_sync(store_id=None, days=overview["window_days"])
        return {"text": txt or "Pas encore assez de feedback pour un résumé.",
                "source": "fallback:learning_context"}
    except Exception:
        return {"text": "Résumé indisponible.", "source": "unavailable"}


@router.get("/feedback/summary")
async def feedback_summary(store_id: str = None, regenerate: int = 0, window_days: int = 30):
    """Résumé automatique (LLM) de la situation feedback. Caché ; regenerate=1 force."""
    from app.core.feedback_service import get_feedback_overview
    sid = store_id or None
    key = f"{sid}:{window_days}"
    if not regenerate and key in _SUMMARY_CACHE:
        return JSONResponse(_SUMMARY_CACHE[key])
    ov = get_feedback_overview(store_id=sid, window_days=window_days)
    result = _summary_llm(ov)
    result["generated_at"] = datetime.utcnow().isoformat()
    _SUMMARY_CACHE[key] = result
    return JSONResponse(result)


@router.get("/quality/judge")
async def quality_judge(store_id: str = None, days: int = 30):
    """Résumé des scores du juge LLM (inventaire + vente)."""
    from app.core.quality_service import get_judge_summary
    res = get_judge_summary(store_id=store_id or None, days=days)
    res["updated_at"] = datetime.utcnow().isoformat()
    return JSONResponse(_clean(res))


@router.post("/quality/judge/run")
async def quality_judge_run(background: BackgroundTasks, store_id: str = None, limit: int = 20):
    """Lance le scoring des recommandations non notées (tâche de fond, hors cycle)."""
    from app.core.quality_service import score_recent_recommendations
    background.add_task(score_recent_recommendations, store_id or None, limit)
    return JSONResponse({"status": "scheduled", "store_id": store_id, "limit": limit,
                         "note": "scoring juge lancé en tâche de fond — recharger /quality/judge dans quelques instants"})


def _read_ragas_result() -> dict:
    """Dernier résultat RAGAS sauvegardé par evals.run_ragas (results/ragas.json)."""
    try:
        import json as _json
        from evals.common import RESULTS_DIR
        path = RESULTS_DIR / "ragas.json"
        if not path.exists():
            return {"available": False, "reason": "aucun run RAGAS enregistré"}
        with open(path, encoding="utf-8") as f:
            data = _json.load(f)
        return {
            "available": not bool(data.get("error")),
            "error": data.get("error"),
            "means": data.get("means"),
            "metrics": data.get("metrics"),
            "scored_cases": data.get("scored_cases"),
            "n_cases": data.get("n_cases"),
            "context_coverage": data.get("context_coverage"),
            "judge_model": data.get("judge_model"),
            "embed_model": data.get("embed_model"),
            "run_at": data.get("run_at"),
        }
    except Exception as e:
        return {"available": False, "reason": str(e)[:160]}


def _run_ragas_task(store_id: str):
    try:
        from evals.run_ragas import run as run_ragas
        # max_workers=1 (défaut de run_ragas : 2). Déclenché depuis /monitoring,
        # le juge RAGAS partage le quota Mistral avec les agents qui servent les
        # requêtes en cours : à 2 workers, les jobs tombaient en 429/timeout
        # (cf. logs du 28/07, 8 jobs perdus sur 32) et les moyennes ne portaient
        # plus que sur les survivants. En tâche de fond, la durée importe moins
        # que la complétude des scores.
        run_ragas(store_id=store_id, max_workers=1)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("[Supervision] RAGAS run: %s", e)


@router.get("/quality/ragas")
async def quality_ragas():
    """Dernier résultat de l'évaluation RAGAS de la chaîne RAG (vente)."""
    res = _read_ragas_result()
    res["updated_at"] = datetime.utcnow().isoformat()
    return JSONResponse(res)


@router.post("/quality/ragas/run")
async def quality_ragas_run(background: BackgroundTasks, store_id: str = DEFAULT_STORE_ID):
    """Lance l'évaluation RAGAS en tâche de fond (lourd : Milvus + Ollama + LLM)."""
    background.add_task(_run_ragas_task, store_id)
    return JSONResponse({"status": "scheduled", "store_id": store_id,
                         "note": "RAGAS lancé en tâche de fond — recharger /quality/ragas dans 1-3 min"})
