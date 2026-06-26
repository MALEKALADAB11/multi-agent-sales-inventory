"""
monitoring_router.py — Endpoints FastAPI pour l'agent monitoring.
Expose tous les logs, cycles, erreurs et stats des agents IA.

Ajouter dans main.py :
    from monitoring_router import router as monitoring_router
    app.include_router(monitoring_router)
"""
import os
import time
from decimal import Decimal
from datetime import datetime, date

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])

AGENT_MAP = {
    "analyste": "APP02", "stratege": "APP05",
    "coach": "APP07",    "rag": "APP10",
}


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


@router.get("/cycles")
async def get_cycles(limit: int = 20, store_id: str = "I63"):
    """Derniers cycles d'orchestration — pour l'agent monitoring."""
    from agent_logger import get_recent_cycles
    cycles = get_recent_cycles(limit=limit, store_id=store_id)
    return JSONResponse({
        "cycles":    cycles,
        "total":     len(cycles),
        "store_id":  store_id,
        "timestamp": datetime.utcnow().isoformat(),
    })


@router.get("/errors")
async def get_errors(limit: int = 50, store_id: str = "I63"):
    """Erreurs récentes non résolues — pour alerting monitoring."""
    from agent_logger import get_recent_errors
    errors = get_recent_errors(limit=limit, store_id=store_id)
    return JSONResponse({
        "errors":    errors,
        "total":     len(errors),
        "store_id":  store_id,
        "timestamp": datetime.utcnow().isoformat(),
    })


@router.get("/stats")
async def get_stats(store_id: str = "I63", hours: int = 24):
    """Statistiques complètes des agents sur les X dernières heures."""
    from agent_logger import get_agent_stats
    stats = get_agent_stats(store_id=store_id, hours=hours)
    return JSONResponse({
        "stats":     stats,
        "store_id":  store_id,
        "hours":     hours,
        "timestamp": datetime.utcnow().isoformat(),
    })


@router.get("/logs")
async def get_logs(limit: int = 100, store_id: str = "I63",
                   agent: str = None, status: str = None):
    """Logs détaillés des nodes LangGraph."""
    from agent_logger import _get_conn
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
                       duration_ms, error_msg, metadata, created_at
                FROM agent_logs
                WHERE {' AND '.join(where)}
                ORDER BY created_at DESC
                LIMIT %s
            """, params)
            logs = [dict(r) for r in cur.fetchall()]
        conn.close()
        return JSONResponse({"logs": logs, "total": len(logs)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/rag-stats")
async def get_rag_stats(store_id: str = "I63", limit: int = 50):
    """Statistiques RAG — pertinence et utilisation."""
    from agent_logger import _get_conn
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
async def monitoring_health():
    """Health check du système de monitoring."""
    from agent_logger import _get_conn
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
async def resolve_error(error_id: int):
    """Marque une erreur comme résolue."""
    from agent_logger import _get_conn
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
        from agent_logger import get_agent_stats
        stats = _clean(get_agent_stats(store_id=last.get("store_id", "I63"), hours=24) or {})
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
        from agent_logger import get_agent_stats
        stats = _clean(get_agent_stats(store_id=last.get("store_id", "I63"), hours=24) or {})
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
        from agent_logger import get_agent_stats
        stats = _clean(get_agent_stats(store_id=last.get("store_id", "I63"), hours=24) or {})
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
        from agent_logger import get_recent_errors, get_agent_stats
        all_errors = _clean(get_recent_errors(limit=10, store_id=last.get("store_id", "I63")))
        inv_map    = {v: k for k, v in AGENT_MAP.items()}
        iname      = inv_map.get(agent_id.upper(), agent_id.lower())
        errors     = [e for e in all_errors if iname in str(e.get("agent_name", ""))]
        stats      = _clean(get_agent_stats(store_id=last.get("store_id", "I63"), hours=1) or {})
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
        from agent_logger import get_recent_cycles
        cycles = _clean(get_recent_cycles(limit=1, store_id=last.get("store_id", "I63")))
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
        from agent_logger import get_recent_cycles
        cycles = _clean(get_recent_cycles(limit=50, store_id=last.get("store_id", "I63")))
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
async def get_dependencies():
    """Vérifie PostgreSQL, Milvus, Ollama, Redis en temps réel."""
    from agent_logger import _get_conn
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
