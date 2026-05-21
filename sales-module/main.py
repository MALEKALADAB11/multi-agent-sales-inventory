import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
<<<<<<< HEAD
from data.postgres_provider import get_data_provider
from core.config import get_settings
=======

from core.config import get_config
>>>>>>> ff55f5bd3860ff2c2677f14edf1b4cbb95a2003c
from data.json_service import JsonDataService
from data.realtime_simulator import RealtimeSimulator
from mcp_servers.timefm.tools import TimesFMTools
from orchestration.graph import CycleOrchestrator
from orchestration.trigger import CronTrigger
from api.routers import stores, forecast, cycle as cycle_router
logging.basicConfig(level=logging.INFO)
logger   = logging.getLogger(__name__)
settings = get_config()

# ── Services globaux ──────────────────────────────────
json_svc      = JsonDataService()
timefm        = TimesFMTools(model_path="./models/timefm")
simulator     = RealtimeSimulator(json_svc, interval_seconds=15)
orchestrator  = CycleOrchestrator(json_svc, timefm)
cron_trigger  = CronTrigger(orchestrator, interval_minutes=15)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting AI Sales Coach backend...")

    # Charger TimesFM
    await timefm.load_model()

    # Injecter dans les routers
    stores.set_json_svc(json_svc)
    forecast.set_json_svc(json_svc)
    cycle_router.set_orchestrator(orchestrator, cron_trigger)

    # Démarrer le simulateur POS
    simulator.start()

    # Démarrer le cron — premier cycle immédiat puis toutes les 15min
    cron_trigger.start()

    logger.info("All systems started")
    yield

    simulator.stop()
    cron_trigger.stop()
    logger.info("Shutting down...")


app = FastAPI(title="AI Sales Coach", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["http://localhost:4200", "http://localhost:3000"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"]
)

app.include_router(stores.router)
app.include_router(forecast.router)
app.include_router(cycle_router.router)


@app.get("/health")
async def health():
    last = cron_trigger.last_result
    return {
        "status":    "ok",
        "version":   "1.0.0",
        "mode":      "agent",
        "simulator": "running",
        "last_cycle": {
            "cycle_id":       last.get("cycle_id") if last else None,
            "niveau_urgence": last.get("niveau_urgence") if last else None,
            "ecart_objectif": last.get("ecart_objectif") if last else None,
            "forecast_eod":   last.get("forecast_eod") if last else None,
            "completed_at":   last["metrics"].get("completed_at") if last else None
        } if last else None
    }


# ── WebSocket ──────────────────────────────────────────
@app.websocket("/ws/store/{store_id}")
async def ws_store(websocket: WebSocket, store_id: str):
    await websocket.accept()
    try:
        while True:
            metrics  = json_svc.get_store_metrics()
            advisors = json_svc.get_advisors_performance()
            last     = cron_trigger.last_result

            await websocket.send_json({
                "type":       "metrics_update",
                "store_id":   store_id,
                "ca_today":   metrics["ca_today"],
                "ca_target":  metrics["ca_target"],
                "attainment": metrics["attainment_pct"],
                "visitors_h": metrics["visitors_h"],

                # ── Données APP02 — vraie IA ──────────────
                "niveau_urgence":  last.get("niveau_urgence")  if last else "LOW",
                "ecart_objectif":  last.get("ecart_objectif")  if last else 0,
                "forecast_eod":    last.get("forecast_eod")    if last else 0,
                "forecast_ci_low": last.get("forecast_ci_low") if last else 0,
                "forecast_mape":   last.get("forecast_mape")   if last else 0,
                "last_cycle_id":   last.get("cycle_id")        if last else None,

                "advisors": [
                    {
                        "advisor_id": a["id"],
                        "ca_today":   a["ca_realized"],
                        "performance":a["performance"]
                    }
                    for a in advisors
                ]
            })
            await asyncio.sleep(5)
    except Exception:
        pass


@app.websocket("/ws/advisor/{advisor_id}")
async def ws_advisor(websocket: WebSocket, advisor_id: str):
    await websocket.accept()
    from datetime import datetime as dt
    try:
        while True:
            advisors = json_svc.get_advisors_performance()
            adv      = next(
                (a for a in advisors if a["id"] == advisor_id), None
            )
            last = cron_trigger.last_result
            await websocket.send_json({
                "type":            "coach_update",
                "advisor_id":      advisor_id,
                "ca_realized":     adv["ca_realized"]  if adv else 0,
                "performance":     adv["performance"]  if adv else 0,
                "status":          adv["status"]       if adv else "ok",
                "niveau_urgence":  last.get("niveau_urgence") if last else "LOW",
                "timestamp":       dt.utcnow().isoformat()
            })
            await asyncio.sleep(10)
    except Exception:
        pass