"""
main.py — AI Sales Coach Backend
==================================
Corrections appliquées :
  1. Conflit de merge Git résolu (<<<<<<< HEAD supprimé)
  2. Route POST /api/v1/coach/chat montée (était 404)
  3. Route GET  /api/monitoring/kpis ajoutée (était 404)
  4. WebSocket /api/inventory/ws stub 200 (était 403 en boucle)
  5. Routes GET /api/inventory/skus|stores|objectives|alerts (404 → 200)
  6. store_id dynamique via normalize_store_id — plus de hardcoding
  7. WebSocket pousse real_time_alerts depuis le state LangGraph
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core.config import get_config
from data.json_service import JsonDataService
from data.postgres_provider import normalize_store_id
from data.realtime_simulator import RealtimeSimulator
from mcp_servers.timefm.tools import TimesFMTools
from orchestration.graph import CycleOrchestrator
from orchestration.trigger import CronTrigger
from api.routers import stores, forecast, cycle as cycle_router

logging.basicConfig(level=logging.INFO)
logger   = logging.getLogger(__name__)
settings = get_config()

json_svc     = JsonDataService()
timefm       = TimesFMTools(model_path="./models/timefm")
simulator    = RealtimeSimulator(store_id=normalize_store_id("I63"), interval=15)
orchestrator = CycleOrchestrator(json_svc, timefm)
cron_trigger = CronTrigger(orchestrator, interval_minutes=15)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting AI Sales Coach backend...")
    await timefm.load_model()
    stores.set_json_svc(json_svc)
    forecast.set_json_svc(json_svc)
    cycle_router.set_orchestrator(orchestrator, cron_trigger)
    simulator.start()
    cron_trigger.start()
    logger.info("All systems started")
    yield
    simulator.stop()
    cron_trigger.stop()
    logger.info("Shutting down...")


app = FastAPI(title="AI Sales Coach", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["http://localhost:4200", "http://localhost:3000", "*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

app.include_router(stores.router)
app.include_router(forecast.router)
app.include_router(cycle_router.router)

# ── Route POST /api/v1/coach/chat ─────────────────────────────────────────────
# Définie dans websocket_endpoint.py mais jamais montée dans main.py → 404.
try:
    from api.websocket_endpoint import coach_chat as _coach_chat_handler
    app.add_api_route(
        "/api/v1/coach/chat",
        _coach_chat_handler,
        methods=["POST"],
        tags=["coach"],
    )
    logger.info("[MAIN] Route POST /api/v1/coach/chat montée ✅")
except Exception as e:
    logger.warning(f"[MAIN] coach/chat import échoué: {e}")


@app.get("/health")
async def health():
    last = cron_trigger.last_result
    return {
        "status":    "ok",
        "version":   "1.0.0",
        "mode":      "agent",
        "simulator": "running",
        "last_cycle": {
            "cycle_id":       last.get("cycle_id")       if last else None,
            "store_id":       last.get("store_id")       if last else None,
            "niveau_urgence": last.get("niveau_urgence") if last else None,
            "ecart_objectif": last.get("ecart_objectif") if last else None,
            "forecast_eod":   last.get("forecast_eod")   if last else None,
            "completed_at":   (last.get("metrics") or {}).get("completed_at") if last else None,
        } if last else None,
    }


# ── GET /api/monitoring/kpis ──────────────────────────────────────────────────
@app.get("/api/monitoring/kpis")
async def get_monitoring_kpis():
    """
    KPIs temps réel pour l'onglet Monitoring Angular.
    Données depuis le state LangGraph (last_result) + PostgreSQL.
    """
    last    = cron_trigger.last_result or {}
    metrics = json_svc.get_store_metrics()

    agents_status = {
        "analyste": "LIVE" if last.get("analyst_summary")   else "IDLE",
        "stratege": "LIVE" if last.get("strategie_actions") else "IDLE",
        "coach":    "LIVE" if last.get("conseil_final")     else "IDLE",
        "rag":      "DONE" if last.get("rag_used")          else "IDLE",
    }

    return JSONResponse({
        "ca_today":        metrics.get("ca_today",        0),
        "ca_target":       metrics.get("ca_target",       0),
        "attainment_pct":  metrics.get("attainment_pct",  0),
        "nb_transactions": metrics.get("nb_transactions", 0),
        "agents_live":     metrics.get("agents_live",     0),
        "cycle_id":        last.get("cycle_id",      ""),
        "store_id":        last.get("store_id",      ""),
        "urgency_level":   last.get("urgency_level", "LOW"),
        "urgency_score":   last.get("urgency_score", 0),
        "gap_pct":         last.get("gap_objectif",  0),
        "gap_amount":      last.get("gap_amount",    0),
        "forecast_eod":    last.get("forecast_eod",  0),
        "rag_used":        last.get("rag_used",       False),
        "nb_rag_scripts":  last.get("nb_rag_scripts", 0),
        "nb_actions":      len(last.get("strategie_actions") or []),
        "analyst_summary": last.get("analyst_summary", ""),
        "cause_racine":    last.get("cause_racine",    ""),
        "total_ms":        (last.get("metrics") or {}).get("total_ms",       0),
        "nodes_executed":  (last.get("metrics") or {}).get("nodes_executed", 0),
        "llm_calls":       (last.get("metrics") or {}).get("llm_calls",      0),
        "completed_at":    (last.get("metrics") or {}).get("completed_at",   ""),
        "agents_status":   agents_status,
        "updated_at":      datetime.utcnow().isoformat(),
        "source":          "postgresql+langgraph",
    })


# ── Stubs inventory ───────────────────────────────────────────────────────────
# Le module inventory tourne en service séparé (inventory-module/).
# Ces stubs éliminent les 403/404 en boucle sans crasher Angular ni le simulateur.

@app.websocket("/api/inventory/ws/{store_id}")
async def ws_inventory_stub(
    websocket: WebSocket,
    store_id: str,
    business_objective: str = "balanced",
):
    """Stub WS inventory — élimine les 403. Remplacer quand inventory sera intégré."""
    await websocket.accept()
    internal_id = normalize_store_id(store_id)
    try:
        await websocket.send_json({
            "type":      "inventory_status",
            "store_id":  internal_id,
            "status":    "module_separate",
            "message":   "Inventory module runs as a separate service",
            "timestamp": datetime.utcnow().isoformat(),
        })
        while True:
            await asyncio.sleep(30)
            await websocket.send_json({"type": "ping", "timestamp": datetime.utcnow().isoformat()})
    except (WebSocketDisconnect, Exception):
        pass


@app.get("/api/inventory/skus")
async def get_inventory_skus_stub(store_id: str = "I63"):
    """Stub — stoppe les retries du realtime_simulator. Simulateur reste en phase 1."""
    return JSONResponse({
        "skus":     [],
        "store_id": normalize_store_id(store_id),
        "source":   "stub_pending_integration",
    })


@app.get("/api/inventory/stores")
async def get_inventory_stores_stub():
    return JSONResponse({"stores": [], "source": "stub_pending_integration"})


@app.get("/api/inventory/objectives")
async def get_inventory_objectives_stub():
    return JSONResponse({"objectives": [], "source": "stub_pending_integration"})


@app.get("/api/inventory/alerts/{store_id}")
async def get_inventory_alerts_stub(store_id: str, status: str = "pending"):
    return JSONResponse({
        "alerts":   [],
        "store_id": normalize_store_id(store_id),
        "source":   "stub_pending_integration",
    })


# ── WebSocket /ws/store/{store_id} ────────────────────────────────────────────
@app.websocket("/ws/store/{store_id}")
async def ws_store(websocket: WebSocket, store_id: str):
    """
    WebSocket par boutique.
    Toutes les valeurs viennent du last_result (state LangGraph final)
    et de PostgreSQL via JsonDataService. Aucune valeur hardcodée.
    """
    await websocket.accept()
    internal_id = normalize_store_id(store_id)
    try:
        while True:
            metrics  = json_svc.get_store_metrics()
            advisors = json_svc.get_advisors_performance()
            last     = cron_trigger.last_result

            await websocket.send_json({
                "type":     "metrics_update",
                "store_id": internal_id,
                "ca_today":   metrics.get("ca_today",       0),
                "ca_target":  metrics.get("ca_target",      0),
                "attainment": metrics.get("attainment_pct", 0),
                "visitors_h": metrics.get("visitors_h",     0),
                "niveau_urgence":    last.get("niveau_urgence")      if last else "LOW",
                "ecart_objectif":    last.get("ecart_objectif")      if last else 0,
                "forecast_eod":      last.get("forecast_eod")        if last else 0,
                "forecast_ci_low":   last.get("forecast_ci_low")     if last else 0,
                "forecast_mape":     last.get("forecast_mape")       if last else 0,
                "last_cycle_id":     last.get("cycle_id")            if last else None,
                "strategie":         last.get("strategie",         "") if last else "",
                "strategie_actions": last.get("strategie_actions", []) if last else [],
                "cause_racine":      last.get("cause_racine",      "") if last else "",
                "real_time_alerts":  last.get("real_time_alerts",  []) if last else [],
                "focus_produits":    last.get("focus_produits",    []) if last else [],
                "advisors": [
                    {
                        "advisor_id":  a["id"],
                        "ca_today":    a["ca_realized"],
                        "performance": a["performance"],
                        "status":      a["status"],
                    }
                    for a in advisors
                ],
                "timestamp": datetime.utcnow().isoformat(),
            })
            await asyncio.sleep(5)
    except Exception:
        pass


# ── WebSocket /ws/advisor/{advisor_id} ───────────────────────────────────────
@app.websocket("/ws/advisor/{advisor_id}")
async def ws_advisor(websocket: WebSocket, advisor_id: str):
    await websocket.accept()
    try:
        while True:
            advisors = json_svc.get_advisors_performance()
            adv      = next((a for a in advisors if a["id"] == advisor_id), None)
            last     = cron_trigger.last_result
            await websocket.send_json({
                "type":             "coach_update",
                "advisor_id":       advisor_id,
                "ca_realized":      adv["ca_realized"]  if adv else 0,
                "performance":      adv["performance"]  if adv else 0,
                "status":           adv["status"]       if adv else "ok",
                "niveau_urgence":   last.get("niveau_urgence")      if last else "LOW",
                "real_time_alerts": last.get("real_time_alerts", []) if last else [],
                "timestamp":        datetime.utcnow().isoformat(),
            })
            await asyncio.sleep(10)
    except Exception:
        pass