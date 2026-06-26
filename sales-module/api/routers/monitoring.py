"""
api/routers/monitoring.py — Agent Monitoring API
=================================================
7 endpoints consommés par la page Monitoring Angular.
Toutes les données viennent de :
  - PostgreSQL agent_logs / agent_cycles / agent_errors  (via agent_logger)
  - cron_trigger.last_result  (état LangGraph du dernier cycle)
  - Connexions directes (PostgreSQL, Milvus, Ollama) pour /dependencies

Aucune donnée mockée.
"""

import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

# ── agent_logger est à la racine du mono-repo ─────────────────────────────────
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from agent_logger import get_agent_stats, get_recent_cycles, get_recent_errors
    _LOGGER_OK = True
except ImportError:
    _LOGGER_OK = False

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])


def _clean(obj):
    """Convertit Decimal, date, datetime en types JSON-sérialisables."""
    from decimal import Decimal
    from datetime import datetime, date
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj

# Référence partagée vers le CronTrigger (injectée depuis main.py au démarrage)
_cron_trigger = None


def set_cron_trigger(ct):
    global _cron_trigger
    _cron_trigger = ct


def _last() -> dict:
    if _cron_trigger is None:
        return {}
    return _cron_trigger.last_result or {}


# ── Mapping nom interne → ID frontend ─────────────────────────────────────────
AGENT_MAP = {
    "analyste":  "APP02",
    "stratege":  "APP05",
    "coach":     "APP07",
    "rag":       "APP10",
    "memory":    "APP11",
    "watcher":   "APP08",
    "orchestrateur": "APP06",
}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. KPIs globaux
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/kpis")
async def get_kpis():
    """
    KPI bar supérieure : healthy, running, failed, avg_latency, cost_today_tnd.
    Source principale : agent_logs / agent_cycles.
    Enrichi avec le last_result LangGraph pour le statut temps réel.
    """
    last = _last()

    stats: Dict[str, Any] = {}
    if _LOGGER_OK:
        try:
            stats = _clean(get_agent_stats(store_id=last.get("store_id", "I63"), hours=24) or {})
        except Exception as e:
            logger.warning("[MONITORING/kpis] agent_logger error: %s", e)

    agents_db = stats.get("agents", [])
    total_errors = int(stats.get("total_errors") or 0)

    # ── Agents sains : avg_ms < 10 000 et taux erreur < 5 % ──────────────────
    healthy = sum(
        1 for a in agents_db
        if (a.get("avg_ms") or 0) < 10_000
        and (a.get("errors") or 0) / max(a.get("nb_logs", 1), 1) < 0.05
    )

    # ── Agents en cours d'exécution depuis le state ───────────────────────────
    agents_status = {
        "analyste": "LIVE" if last.get("analyst_summary")   else "IDLE",
        "stratege": "LIVE" if last.get("strategie_actions") else "IDLE",
        "coach":    "LIVE" if last.get("conseil_final")     else "IDLE",
        "rag":      "DONE" if last.get("rag_used")          else "IDLE",
    }
    running = sum(1 for s in agents_status.values() if s == "LIVE")

    # ── Latence moyenne ───────────────────────────────────────────────────────
    latencies = [float(a.get("avg_ms") or 0) for a in agents_db if a.get("avg_ms")]
    avg_latency_s = round(sum(latencies) / len(latencies) / 1000, 2) if latencies else 0.0

    # ── Coût journalier : ~0.0002 TND/cycle (tokens GPT-4o-mini × 3.1 USD→TND) ──
    total_cycles = int(stats.get("total_cycles") or 0)
    cost_tnd = round(total_cycles * 0.15, 2)  # ~0.15 TND/cycle estimé

    # ── Résumé cycle courant ──────────────────────────────────────────────────
    metrics = last.get("metrics") or {}

    return JSONResponse({
        # Attendu par monitoring.service.ts
        "healthy":          max(healthy, 0),
        "running":          running,
        "failed":           total_errors,
        "avg_latency":      avg_latency_s,
        "cost_today_tnd":   cost_tnd,
        # Extra : cycle courant
        "cycle_id":         last.get("cycle_id",       ""),
        "store_id":         last.get("store_id",       ""),
        "urgency_level":    last.get("urgency_level",  "LOW"),
        "urgency_score":    last.get("urgency_score",  0),
        "gap_pct":          last.get("gap_objectif",   0),
        "gap_amount":       last.get("gap_amount",     0),
        "forecast_eod":     last.get("forecast_eod",   0),
        "rag_used":         last.get("rag_used",       False),
        "nb_rag_scripts":   last.get("nb_rag_scripts", 0),
        "nb_actions":       len(last.get("strategie_actions") or []),
        "total_cycles_24h": total_cycles,
        "agents_status":    agents_status,
        "nodes_executed":   metrics.get("nodes_executed", 0),
        "llm_calls":        metrics.get("llm_calls",      0),
        "total_ms":         metrics.get("total_ms",       0),
        "completed_at":     metrics.get("completed_at",   ""),
        "updated_at":       datetime.utcnow().isoformat(),
        "source":           "postgresql+langgraph",
    })


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Performance par agent
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/performance")
async def get_performance():
    """
    Métriques de performance par agent sur les 24 dernières heures.
    Source : agent_logs (PostgreSQL).
    """
    last  = _last()
    stats = {}
    if _LOGGER_OK:
        try:
            stats = _clean(get_agent_stats(store_id=last.get("store_id", "I63"), hours=24) or {})
        except Exception as e:
            logger.warning("[MONITORING/performance] %s", e)

    agents_raw = stats.get("agents", [])

    agents = []
    for a in agents_raw:
        name   = str(a.get("agent_name") or "")
        nb     = int(a.get("nb_logs") or 0)
        errors = int(a.get("errors") or 0)
        avg_ms = float(a.get("avg_ms") or 0)
        agents.append({
            "agent_id":     AGENT_MAP.get(name, name.upper()),
            "agent_name":   name,
            "avg_latency_ms": round(avg_ms, 1),
            "avg_latency_s":  round(avg_ms / 1000, 2),
            "total_runs":   nb,
            "error_count":  errors,
            "success_rate": round(1 - errors / max(nb, 1), 3),
            "sla_ok":       avg_ms < 10_000,
        })

    # Trier par latence décroissante
    agents.sort(key=lambda x: -x["avg_latency_ms"])

    # Ajouter les 3 agents core depuis le state si non présents dans les logs
    names_in = {a["agent_name"] for a in agents}
    metrics  = last.get("metrics") or {}
    for name, ms_key in [("analyste", "analyste_ms"), ("stratege", "stratege_ms"), ("rag", "rag_ms")]:
        if name not in names_in and metrics.get(ms_key):
            agents.append({
                "agent_id":      AGENT_MAP.get(name, name.upper()),
                "agent_name":    name,
                "avg_latency_ms": float(metrics[ms_key]),
                "avg_latency_s":  round(float(metrics[ms_key]) / 1000, 2),
                "total_runs":    1,
                "error_count":   0,
                "success_rate":  1.0,
                "sla_ok":        float(metrics[ms_key]) < 10_000,
            })

    return JSONResponse({
        "agents":     agents,
        "total_agents": len(agents),
        "sla_threshold_ms": 10_000,
        "period_hours":   24,
        "updated_at":     datetime.utcnow().isoformat(),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Coûts
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/costs")
async def get_costs():
    """
    Estimation des coûts journaliers par agent.
    LLM : $0.15/1M tokens input, $0.60/1M output (GPT-4o-mini) × 3.1 USD→TND.
    Source : agent_logs.metadata (llm_calls) + agent_cycles.
    """
    last  = _last()
    stats = {}
    cycles: list = []
    if _LOGGER_OK:
        try:
            stats  = _clean(get_agent_stats(store_id=last.get("store_id", "I63"), hours=24) or {})
            cycles = _clean(get_recent_cycles(limit=50, store_id=last.get("store_id", "I63")))
        except Exception as e:
            logger.warning("[MONITORING/costs] %s", e)

    total_cycles    = int(stats.get("total_cycles") or 0)
    avg_llm_calls   = float((last.get("metrics") or {}).get("llm_calls", 0))

    # Estimation coût LLM : ~600 tokens/call × $0.15/1M × 3.1
    cost_per_llm_call = 600 * 0.15 / 1_000_000 * 3.1  # TND
    api_cost_tnd      = round(total_cycles * avg_llm_calls * cost_per_llm_call, 4)

    # Latence compute → coût infra
    avg_total_ms   = float(stats.get("avg_duration_ms") or 0)
    compute_cost   = round(total_cycles * avg_total_ms / 1000 * 0.00001 * 3.1, 4)

    # Stockage PostgreSQL + Milvus : fixe journalier ~0.5 TND
    storage_cost = 0.5

    total_tnd = round(api_cost_tnd + compute_cost + storage_cost, 3)

    by_agent: Dict[str, float] = {}
    for a in stats.get("agents", []):
        name = str(a.get("agent_name") or "")
        nb   = int(a.get("nb_logs") or 0)
        by_agent[name] = round(nb * cost_per_llm_call, 4)

    return JSONResponse({
        "api_cost_tnd":     api_cost_tnd,
        "compute_cost_tnd": compute_cost,
        "storage_cost_tnd": storage_cost,
        "total_cost_tnd":   total_tnd,
        "total_cycles":     total_cycles,
        "by_agent":         by_agent,
        "period_hours":     24,
        "currency":         "TND",
        "updated_at":       datetime.utcnow().isoformat(),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Santé par agent
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/health/{agent_id}")
async def get_agent_health(agent_id: str):
    """
    État de santé d'un agent spécifique.
    Source : agent_logs + agent_errors + last_result.
    """
    last   = _last()
    errors = []
    stats  = {}

    if _LOGGER_OK:
        try:
            errors = _clean(get_recent_errors(limit=10, store_id=last.get("store_id", "I63")))
            errors = [e for e in errors if agent_id.lower() in str(e.get("agent_name", "")).lower()
                      or agent_id.upper() == AGENT_MAP.get(str(e.get("agent_name", "")), "")]
            stats  = _clean(get_agent_stats(store_id=last.get("store_id", "I63"), hours=1) or {})
        except Exception as e:
            logger.warning("[MONITORING/health/%s] %s", agent_id, e)

    # Trouver le nom interne depuis l'ID frontend (APP02 → analyste)
    inv_map = {v: k for k, v in AGENT_MAP.items()}
    internal_name = inv_map.get(agent_id.upper(), agent_id.lower())

    agent_db = next(
        (a for a in stats.get("agents", []) if a.get("agent_name") == internal_name),
        {}
    )
    nb     = int(agent_db.get("nb_logs") or 0)
    errs   = int(agent_db.get("errors")  or 0)
    avg_ms = float(agent_db.get("avg_ms") or 0)

    # Statut depuis le dernier cycle LangGraph
    agents_live = {
        "analyste": bool(last.get("analyst_summary")),
        "stratege": bool(last.get("strategie_actions")),
        "coach":    bool(last.get("conseil_final")),
        "rag":      bool(last.get("rag_used")),
    }
    is_live = agents_live.get(internal_name, False)

    status = "LIVE" if is_live else ("ERROR" if errs > 0 else ("IDLE" if nb == 0 else "DONE"))

    return JSONResponse({
        "agent_id":       agent_id,
        "agent_name":     internal_name,
        "status":         status,
        "avg_latency_ms": round(avg_ms, 1),
        "total_runs_1h":  nb,
        "error_count_1h": errs,
        "success_rate":   round(1 - errs / max(nb, 1), 3),
        "sla_ok":         avg_ms < 10_000,
        "recent_errors":  [
            {
                "cycle_id":   e.get("cycle_id", ""),
                "error_type": e.get("error_type", ""),
                "error_msg":  str(e.get("error_msg", ""))[:120],
                "created_at": str(e.get("created_at", "")),
            }
            for e in errors[:5]
        ],
        "last_cycle_id":  last.get("cycle_id", ""),
        "updated_at":     datetime.utcnow().isoformat(),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Timeline d'exécution (Gantt)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/timeline")
async def get_timeline():
    """
    Timeline du dernier cycle : durée de chaque node LangGraph.
    Source : agent_logs + metrics du last_result.
    """
    last    = _last()
    metrics = last.get("metrics") or {}
    cycles  = []

    if _LOGGER_OK:
        try:
            cycles = _clean(get_recent_cycles(limit=1, store_id=last.get("store_id", "I63")))
        except Exception as e:
            logger.warning("[MONITORING/timeline] %s", e)

    # Durées connues dans les metrics
    known_ms = {
        "analyste":  float(metrics.get("analyste_ms",  0)),
        "stratege":  float(metrics.get("stratege_ms",  0)),
        "rag":       float(metrics.get("rag_ms",       0)),
        "total":     float(metrics.get("total_ms",     0)),
    }

    # Reconstruire une timeline approximative depuis les durées cumulées
    events = []
    t = 0.0
    for name in ("analyste", "rag", "stratege", "coach"):
        dur = known_ms.get(name, 0)
        if dur > 0:
            events.append({
                "agent":       name,
                "agent_id":    AGENT_MAP.get(name, name.upper()),
                "start_ms":    round(t),
                "end_ms":      round(t + dur),
                "duration_ms": round(dur),
                "status":      "success",
            })
            t += dur

    # Coach : total_ms − sum des autres
    coach_ms = max(0, known_ms["total"] - t)
    if coach_ms > 0:
        events.append({
            "agent": "coach", "agent_id": "APP07",
            "start_ms": round(t), "end_ms": round(t + coach_ms),
            "duration_ms": round(coach_ms), "status": "success",
        })

    last_cycle = cycles[0] if cycles else {}

    return JSONResponse({
        "cycle_id":    last.get("cycle_id", last_cycle.get("cycle_id", "")),
        "total_ms":    known_ms["total"],
        "events":      events,
        "nb_events":   len(events),
        "completed_at": metrics.get("completed_at", last_cycle.get("created_at", "")),
        "updated_at":  datetime.utcnow().isoformat(),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Prédiction de pannes (Prophet-style simple)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/predict")
async def get_failure_prediction():
    """
    Prédiction simple de défaillance basée sur le taux d'erreurs récent.
    Source : agent_cycles (50 derniers cycles).
    """
    last   = _last()
    cycles = []

    if _LOGGER_OK:
        try:
            cycles = _clean(get_recent_cycles(limit=50, store_id=last.get("store_id", "I63")))
        except Exception as e:
            logger.warning("[MONITORING/predict] %s", e)

    if not cycles:
        return JSONResponse({
            "risk_score": 0.0, "trend": "stable", "source": "no_data",
            "historical": [], "forecast": [], "upper": [], "lower": [],
        })

    # Score d'erreur par cycle (0 = ok, 1 = erreur)
    error_flags = [int(c.get("errors_count", 0) > 0) for c in cycles][::-1]  # chronologique
    n = len(error_flags)

    # Fenêtre glissante 5 cycles → taux d'erreur local
    window = 5
    rolling = []
    for i in range(n):
        start = max(0, i - window + 1)
        rolling.append(sum(error_flags[start:i+1]) / (i - start + 1))

    # Prédiction naïve : tendance linéaire simple des 10 derniers points
    recent = rolling[-10:] if len(rolling) >= 10 else rolling
    trend_slope = (recent[-1] - recent[0]) / max(len(recent) - 1, 1) if len(recent) > 1 else 0
    last_val    = recent[-1] if recent else 0.0

    # Forecast 12 points futurs
    forecast = [max(0.0, min(1.0, last_val + trend_slope * i)) for i in range(1, 13)]
    margin   = 0.10
    upper    = [min(1.0, f + margin) for f in forecast]
    lower    = [max(0.0, f - margin) for f in forecast]

    # Tendance globale
    trend_label = "rising" if trend_slope > 0.01 else ("falling" if trend_slope < -0.01 else "stable")
    risk_score  = round(last_val, 3)

    return JSONResponse({
        "risk_score":    risk_score,
        "trend":         trend_label,
        "trend_slope":   round(trend_slope, 4),
        "historical":    [round(v, 3) for v in rolling],
        "forecast":      [round(v, 3) for v in forecast],
        "upper":         [round(v, 3) for v in upper],
        "lower":         [round(v, 3) for v in lower],
        "nb_cycles":     n,
        "updated_at":    datetime.utcnow().isoformat(),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Dépendances système
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/dependencies")
async def get_dependencies():
    """
    Vérifie PostgreSQL, Milvus (RAG), Ollama (LLM) en temps réel.
    Connexions directes — aucune donnée mockée.
    """
    deps: Dict[str, Any] = {}

    # ── PostgreSQL ─────────────────────────────────────────────────────────────
    try:
        import psycopg2
        t0 = time.time()
        conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", 5432)),
            dbname=os.getenv("POSTGRES_DB", "ooredoo_sales"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", "admin"),
            connect_timeout=3,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent_cycles")
            nb_cycles = cur.fetchone()[0]
        conn.close()
        deps["postgresql"] = {
            "status": "ok",
            "latency_ms": round((time.time() - t0) * 1000),
            "detail": f"{nb_cycles} agent_cycles enregistrés",
        }
    except Exception as e:
        deps["postgresql"] = {"status": "error", "detail": str(e)[:120]}

    # ── Milvus (RAG) ───────────────────────────────────────────────────────────
    try:
        from pymilvus import MilvusClient
        t0 = time.time()
        client  = MilvusClient(uri=os.getenv("MILVUS_URI", "http://localhost:19530"))
        has_col = client.has_collection("coaching_scripts")
        nb_docs = 0
        if has_col:
            nb_docs = client.get_collection_stats("coaching_scripts").get("row_count", 0)
        deps["milvus"] = {
            "status":     "ok" if has_col else "degraded",
            "latency_ms": round((time.time() - t0) * 1000),
            "detail":     f"coaching_scripts: {nb_docs} vectors",
        }
    except Exception as e:
        deps["milvus"] = {"status": "error", "detail": str(e)[:120]}

    # ── Ollama (LLM local) ─────────────────────────────────────────────────────
    try:
        import httpx
        t0 = time.time()
        r = httpx.get(
            os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/tags",
            timeout=3,
        )
        models = [m["name"] for m in r.json().get("models", [])]
        deps["ollama"] = {
            "status":     "ok" if r.status_code == 200 else "degraded",
            "latency_ms": round((time.time() - t0) * 1000),
            "detail":     f"models: {', '.join(models[:3]) or 'none'}",
        }
    except Exception as e:
        deps["ollama"] = {"status": "error", "detail": str(e)[:120]}

    # ── Redis (Pub/Sub alerts) ─────────────────────────────────────────────────
    try:
        import redis
        t0 = time.time()
        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            socket_connect_timeout=2,
        )
        r.ping()
        info = r.info("keyspace")
        deps["redis"] = {
            "status":     "ok",
            "latency_ms": round((time.time() - t0) * 1000),
            "detail":     f"keyspace: {info}",
        }
    except Exception as e:
        deps["redis"] = {"status": "error", "detail": str(e)[:120]}

    all_ok = all(d.get("status") == "ok" for d in deps.values())
    degraded = any(d.get("status") == "degraded" for d in deps.values())

    return JSONResponse({
        "status":       "ok" if all_ok else ("degraded" if degraded else "partial"),
        "dependencies": deps,
        "nb_ok":        sum(1 for d in deps.values() if d.get("status") == "ok"),
        "nb_total":     len(deps),
        "updated_at":   datetime.utcnow().isoformat(),
    })
