"""
monitoring_router.py — Endpoints FastAPI pour l'agent monitoring.
Expose tous les logs, cycles, erreurs et stats des agents IA.

Ajouter dans main.py :
    from monitoring_router import router as monitoring_router
    app.include_router(monitoring_router)
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from datetime import datetime

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])


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