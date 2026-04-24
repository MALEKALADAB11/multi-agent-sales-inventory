"""
Unified API Server - Inventory + Sales
=======================================
Combines both modules into one FastAPI app.

Run with:
    uvicorn main:app --reload --port 8000
"""

import sys
import os
import asyncio
import logging
from dotenv import load_dotenv

# ── Fix module paths ──────────────────────────────────────────
# Must happen BEFORE any local imports
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "inventory-module"))  # → exposes src.*, config.*
sys.path.insert(0, os.path.join(BASE_DIR, "sales-module"))      # → exposes api.*, data.*, orchestration.*, mcp_servers.*, core.*, modules.*

# ── Load .env files before anything tries to read env vars ───
load_dotenv(os.path.join(BASE_DIR, "inventory-module", ".env"), override=True)
load_dotenv(os.path.join(BASE_DIR, "sales-module", ".env"), override=True)

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

# ── Inventory Module ──────────────────────────────────────────
from src.api.routes import router as inventory_router

# ── Sales Module Routers ──────────────────────────────────────
from api.routers.cycle    import router as cycle_router,    set_orchestrator
from api.routers.forecast import router as forecast_router, set_json_svc as set_forecast_json
from api.routers.stores   import router as stores_router,   set_json_svc as set_stores_json

# ── Sales Dependencies ────────────────────────────────────────
from data.json_service           import JsonDataService
from data.realtime_simulator     import RealtimeSimulator
from mcp_servers.timefm.tools    import TimesFMTools
from orchestration.graph         import CycleOrchestrator
from orchestration.trigger       import CronTrigger

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# App Initialization
# ══════════════════════════════════════════════════════════════

app = FastAPI(
    title="Unified Retail API",
    description="Inventory Analysis + Sales Forecasting & Orchestration",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4200",
        "http://localhost:4300",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════
# Include Routers
# ══════════════════════════════════════════════════════════════

app.include_router(inventory_router, prefix="/api/inventory")  # inventory-module
app.include_router(cycle_router)                               # /api/v1/cycle
app.include_router(forecast_router)                            # /api/v1/forecast
app.include_router(stores_router)                              # /api/v1/stores


# ══════════════════════════════════════════════════════════════
# Startup
# ══════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup_event():
    """Initialize all module dependencies."""

    # Reload .env inside startup as safety guard
    load_dotenv(os.path.join(BASE_DIR, "inventory-module", ".env"), override=True)
    load_dotenv(os.path.join(BASE_DIR, "sales-module", ".env"), override=True)
    logger.info("✅ Environment variables loaded")

    # JSON data service — shared by forecast & stores routers
    json_svc = JsonDataService()
    set_forecast_json(json_svc)
    set_stores_json(json_svc)
    app.state.json_svc = json_svc

    # TimesFM / Prophet forecasting engine
    timefm = TimesFMTools(model_path="./models/timefm")
    await timefm.load_model()
    app.state.timefm = timefm
    logger.info("✅ TimesFM model loaded")

    # POS Simulator
    simulator = RealtimeSimulator(json_svc, interval_seconds=15)
    simulator.start()
    app.state.simulator = simulator
    logger.info("✅ POS Simulator started")

    # Orchestrator + cron trigger
    orchestrator = CycleOrchestrator(json_svc=json_svc, timefm=timefm)
    trigger = CronTrigger(
        orchestrator=orchestrator,
        store_id="store-lac2",
        interval_minutes=15
    )
    trigger.start()
    set_orchestrator(orchestrator, trigger)
    app.state.trigger = trigger
    app.state.orchestrator = orchestrator
    logger.info("✅ Orchestration cycle started (every 15 min)")

    logger.info("📦 Inventory Module ready")
    logger.info("\n✨ All systems started!")

    logger.info("\n📋 Available Routes:")
    for route in app.routes:
        if hasattr(route, "methods"):
            logger.info("  %-25s %s", str(route.methods), route.path)


# ══════════════════════════════════════════════════════════════
# Shutdown
# ══════════════════════════════════════════════════════════════

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanly stop all background tasks."""
    simulator = getattr(app.state, "simulator", None)
    if simulator:
        simulator.stop()

    trigger = getattr(app.state, "trigger", None)
    if trigger:
        trigger.stop()

    logger.info("Shutting down cleanly.")


# ══════════════════════════════════════════════════════════════
# Health Check
# ══════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    trigger = getattr(app.state, "trigger", None)
    last = trigger.last_result if trigger else None
    return {
        "status":  "ok",
        "version": "1.0.0",
        "modules": ["inventory", "sales"],
        "endpoints": {
            "inventory": "/api/inventory/*",
            "cycle":     "/api/v1/cycle/*",
            "forecast":  "/api/v1/forecast/*",
            "stores":    "/api/v1/stores/*",
        },
        "last_cycle": {
            "cycle_id":       last.get("cycle_id")       if last else None,
            "niveau_urgence": last.get("niveau_urgence") if last else None,
            "ecart_objectif": last.get("ecart_objectif") if last else None,
            "forecast_eod":   last.get("forecast_eod")   if last else None,
            "completed_at":   last["metrics"].get("completed_at") if last else None,
        } if last else None,
    }


# ══════════════════════════════════════════════════════════════
# WebSocket — Store dashboard
# ══════════════════════════════════════════════════════════════

@app.websocket("/ws/store/{store_id}")
async def ws_store(websocket: WebSocket, store_id: str):
    await websocket.accept()
    json_svc = app.state.json_svc
    trigger  = app.state.trigger
    try:
        while True:
            metrics  = json_svc.get_store_metrics()
            advisors = json_svc.get_advisors_performance()
            last     = trigger.last_result

            await websocket.send_json({
                "type":            "metrics_update",
                "store_id":        store_id,
                "ca_today":        metrics["ca_today"],
                "ca_target":       metrics["ca_target"],
                "attainment":      metrics["attainment_pct"],
                "visitors_h":      metrics["visitors_h"],
                "niveau_urgence":  last.get("niveau_urgence")  if last else "LOW",
                "ecart_objectif":  last.get("ecart_objectif")  if last else 0,
                "forecast_eod":    last.get("forecast_eod")    if last else 0,
                "forecast_ci_low": last.get("forecast_ci_low") if last else 0,
                "forecast_mape":   last.get("forecast_mape")   if last else 0,
                "last_cycle_id":   last.get("cycle_id")        if last else None,
                "advisors": [
                    {
                        "advisor_id":  a["id"],
                        "ca_today":    a["ca_realized"],
                        "performance": a["performance"],
                    }
                    for a in advisors
                ],
            })
            await asyncio.sleep(5)
    except Exception as e:
        logger.error("WebSocket error (store): %s", e)


# ══════════════════════════════════════════════════════════════
# WebSocket — Advisor / KB
# ══════════════════════════════════════════════════════════════

@app.websocket("/ws/advisor/{advisor_id}")
async def ws_advisor(websocket: WebSocket, advisor_id: str):
    await websocket.accept()
    from datetime import datetime as dt
    json_svc = app.state.json_svc
    trigger  = app.state.trigger
    try:
        while True:
            advisors = json_svc.get_advisors_performance()
            adv      = next(
                (a for a in advisors if a["id"] == advisor_id), None
            )
            last = trigger.last_result
            await websocket.send_json({
                "type":           "coach_update",
                "advisor_id":     advisor_id,
                "ca_realized":    adv["ca_realized"]  if adv else 0,
                "performance":    adv["performance"]  if adv else 0,
                "status":         adv["status"]       if adv else "ok",
                "niveau_urgence": last.get("niveau_urgence") if last else "LOW",
                "timestamp":      dt.utcnow().isoformat(),
            })
            await asyncio.sleep(10)
    except Exception as e:
        logger.error("WebSocket error (advisor): %s", e)


# ══════════════════════════════════════════════════════════════
# WebSocket — KB (Knowledge Base) — alias for /ws/advisor/kb
# ══════════════════════════════════════════════════════════════

@app.websocket("/ws/advisor/kb")
async def ws_kb(websocket: WebSocket):
    await websocket.accept()
    from datetime import datetime as dt
    trigger = app.state.trigger
    try:
        while True:
            last = trigger.last_result
            await websocket.send_json({
                "type":           "kb_update",
                "niveau_urgence": last.get("niveau_urgence") if last else "LOW",
                "timestamp":      dt.utcnow().isoformat(),
            })
            await asyncio.sleep(10)
    except Exception as e:
        logger.error("WebSocket error (kb): %s", e)