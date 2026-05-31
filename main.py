"""
Unified API Server — Inventory + Sales + Agents IA
===================================================
    uvicorn main:app --port 8000
"""

import sys
import os
import asyncio
import logging
from typing import Dict
import json, random, time
from datetime import datetime
from dotenv import load_dotenv

# ── Auth (avant les imports du module sales pour éviter les conflits) ─────────
from auth_router import router as auth_router, setup_auth_tables

# ── Chemins ───────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "inventory-module"))
sys.path.insert(0, os.path.join(BASE_DIR, "sales-module"))

# ── .env ──────────────────────────────────────────────────────────────────────
for env_dir in ("inventory-module", "sales-module"):
    load_dotenv(os.path.join(BASE_DIR, env_dir, ".env"), override=True)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# ── Inventory ─────────────────────────────────────────────────────────────────
from src.api.routes import router as inventory_router, invalidate_store
from src.tools.internal.stock_tools import _DataCache as InventoryDataCache
from db.repositories.inventory_repo import InventoryRepo
from db.stock_simulator import StockSimulator

# ── Sales routers ─────────────────────────────────────────────────────────────
from api.routers.cycle    import router as cycle_router,    set_orchestrator
from api.routers.forecast import router as forecast_router, set_json_svc as set_forecast_json
from api.routers.stores   import router as stores_router,   set_json_svc as set_stores_json

# ── Sales deps ────────────────────────────────────────────────────────────────
from data.json_service        import JsonDataService
from data.realtime_simulator  import RealtimeSimulator
from mcp_servers.timefm.tools import TimesFMTools
from orchestration.graph      import CycleOrchestrator
from orchestration.trigger    import CronTrigger

# ── Agents ────────────────────────────────────────────────────────────────────
from data.mock_provider import get_data_provider
from modules.coaching.agents.analyst.agent  import get_analyst_agent
from modules.coaching.agents.stratege.agent import get_stratege_agent

# ── Monitoring ────────────────────────────────────────────────────────────────
from monitoring_router import router as monitoring_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Anti-double connexion WebSocket ───────────────────────────────────────────
_active_stores: set[str] = set()

STORE_MAP = {
    "store-lac2":    "OOR_LAC_01",
    "store-menzah":  "OOR_MENZAH_02",
    "store-sfax":    "OOR_SFAX_03",
    "OOR_LAC_01":    "OOR_LAC_01",
    "OOR_MENZAH_02": "OOR_MENZAH_02",
    "OOR_SFAX_03":   "OOR_SFAX_03",
}

# ── Cache météo 5 min ─────────────────────────────────────────────────────────
_weather_cache:      dict  = {}
_weather_cache_time: float = 0.0
_WEATHER_CACHE_TTL         = 300


# ══════════════════════════════════════════════════════════════════════════════
# FastAPI App
# ══════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title       = "Unified Retail AI API",
    description = "Inventory + Sales + Agents IA (Analyste · Stratège · Coach)",
    version     = "3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(inventory_router, prefix="/api/inventory")
app.include_router(cycle_router)
app.include_router(forecast_router)
app.include_router(stores_router)
app.include_router(monitoring_router)

# Coach Chat RAG (Agent Coach LangGraph)
try:
    from modules.coaching.agents.coach.coach_chat import router as coach_rag_router
    app.include_router(coach_rag_router)
    logger.info("✅ Coach Chat RAG (LangGraph) chargé")
except ImportError as e:
    logger.warning(f"⚠️  coach_chat_rag non trouvé: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Startup / Shutdown
# ══════════════════════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup_event():
    _active_stores.clear()

    # Auth tables PostgreSQL
    try:
        setup_auth_tables()
        logger.info("✅ Auth tables prêtes")
    except Exception as e:
        logger.warning(f"⚠️  Auth tables: {e}")

    # Monitoring tables
    try:
        from agent_logger import setup_monitoring_tables
        setup_monitoring_tables()
        logger.info("✅ Monitoring tables prêtes")
    except Exception as e:
        logger.warning(f"⚠️  Monitoring tables: {e}")

    # Coach interactions table
    try:
        from modules.coaching.agents.coach.tools import ensure_interactions_table
        ensure_interactions_table()
        logger.info("✅ Coach interactions table prête")
    except Exception as e:
        logger.warning(f"⚠️  Coach interactions table: {e}")

    # Services
    json_svc = JsonDataService()
    set_forecast_json(json_svc)
    set_stores_json(json_svc)
    app.state.json_svc = json_svc

    timefm = TimesFMTools(model_path="./models/timefm")
    await timefm.load_model()
    app.state.timefm = timefm
    logger.info("✅ TimesFM chargé")

    # ── DB pool + StockSimulator ──────────────────────────────
    try:
        db_repo = InventoryRepo()
        await db_repo.connect()
        stock_sim = StockSimulator(db_repo)
        app.state.db_repo   = db_repo
        app.state.stock_sim = stock_sim
        logger.info("✅ DB pool connected — StockSimulator ready")
    except Exception as e:
        logger.warning("⚠️  DB pool failed — stock updates will be in-memory only: %s", e)
        stock_sim = None
        app.state.db_repo   = None
        app.state.stock_sim = None

    simulator = RealtimeSimulator(json_svc, interval_seconds=15, store_id="I63")

    # ✅ In-memory stock tracker (fast path for WebSocket broadcasts)
    _live_stock: Dict[str, float] = {}

    def _init_stock_from_cache():
        """Load initial stock levels from stock_history CSV"""
        try:
            stock_df = InventoryDataCache.stock()
            store_stock = stock_df[stock_df["store_id"] == "I63"]
            if not store_stock.empty:
                latest = store_stock.sort_values('date').groupby('sku').last()
                for sku, row in latest.iterrows():
                    _live_stock[str(sku)] = float(row.get('stock_level', 0))
                logger.info(f"✅ Loaded {len(_live_stock)} SKU stock levels for I63")
        except Exception as e:
            logger.warning(f"Could not load initial stock: {e}")

    _init_stock_from_cache()

    def _on_sale(store_id: str, sku: str, units: int) -> None:
        """
        Called by RealtimeSimulator every time a sale fires.
        1. Updates in-memory _live_stock (fast — for WebSocket broadcast)
        2. Updates _DataCache._stock_overrides (so pipeline sees live stock)
        3. Persists to inv.stock_levels via StockSimulator (DB source of truth)

        Guards:
          - Skips if (sku, store_id) has no stock_levels row in DB.
            get_stock_level is a single query that implicitly validates both
            the sku FK (inv.products) and store FK (inv.stores): if either is
            missing the row won't exist.
          - This prevents FK violations that would occur if StockSimulator
            tried to INSERT a new row for an unseeded (sku, store_id) pair.
        """
        sku_str = str(sku)

        # ── Guard: validate (sku, store_id) exists in inv.stock_levels ────────
        # One query covers both FK checks (products + stores).
        # If there's no row, StockSimulator would have to INSERT — which causes
        # FK violations. Skip the sale instead and log it.
        try:
            from db.repositories.inventory_repo import SyncInventoryRepo
            db_row = SyncInventoryRepo.get_stock_level(sku_str, store_id)
            if db_row is None:
                logger.debug(
                    "Skipping sale for %s@%s — no stock_levels row. "
                    "Run init_stock_levels.py to seed this pair.",
                    sku_str, store_id,
                )
                return
        except Exception:
            # DB unavailable — allow through so in-memory path still works
            db_row = None

        # ── 1. In-memory fast path ─────────────────────────────────────────────
        # Seed _live_stock from DB on first sight so the broadcast delta
        # reflects real stock rather than defaulting to 0.
        if sku_str not in _live_stock:
            if db_row is not None:
                _live_stock[sku_str] = float(db_row["stock_current"])
            else:
                # DB unavailable — try mem override, else 0
                override = InventoryDataCache._stock_overrides.get((sku_str, store_id))
                _live_stock[sku_str] = float(override) if override is not None else 0.0

        current   = _live_stock[sku_str]
        new_stock = max(0, current - units)
        _live_stock[sku_str] = new_stock

        # ── 2. Keep analysis pipeline in sync ─────────────────────────────────
        InventoryDataCache.record_sale(sku_str, store_id, units)

        logger.info("📉 Sale: %s | %.0f → %.0f units (-%d)", sku_str, current, new_stock, units)

        # ── 3 & 4. Persist to DB then broadcast (in order) ────────────────────
        # DB write must complete before broadcast triggers pipeline re-run,
        # otherwise the pipeline reads stale stock from DB.
        async def _sale_and_broadcast():
            if stock_sim is not None:
                await stock_sim.record_sale(sku_str, store_id, units)
            invalidate_store(store_id, sku=sku_str, new_stock=new_stock)

        if stock_sim is not None:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(_sale_and_broadcast())

    # ✅ Wire on_sale callback BEFORE starting
    simulator.on_sale = _on_sale

    # ✅ Now start
    simulator.start()
    app.state.simulator = simulator
    logger.info("✅ Inventory ↔ Sales sync wired")

    orchestrator = CycleOrchestrator(json_svc=json_svc, timefm=timefm)
    trigger      = CronTrigger(
        orchestrator     = orchestrator,
        store_id         = "store-lac2",
        interval_minutes = 15,
    )
    trigger.start()
    set_orchestrator(orchestrator, trigger)
    app.state.trigger      = trigger
    app.state.orchestrator = orchestrator

    logger.info("✅ Orchestration démarrée")

    # ── Pre-warm inventory cache at startup ───────────────────────────────────
    # The two-phase pipeline (fast all SKUs + LLM on critical/high only) takes
    # ~5-30s depending on how many SKUs are flagged.  Running it at startup
    # means the first page load hits a warm cache and responds instantly.
    async def _prewarm_inventory():
        await asyncio.sleep(3)   # brief delay so all services finish initialising
        try:
            logger.info("🔥 Pre-warming inventory cache for I63 (balanced)...")
            loop = asyncio.get_event_loop()
            # Import here to avoid circular import at module load time
            from src.api.routes import analyze_store as _analyze_store
            await loop.run_in_executor(
                None,
                lambda: _analyze_store(
                    "I63",
                    business_objective="balanced",
                    force_refresh=False,   # skip if already warm (e.g. hot-reload)
                    fast=False,
                    page=1,
                    page_size=0,
                ),
            )
            logger.info("✅ Inventory cache pre-warmed for I63")
        except Exception as exc:
            logger.warning("⚠️  Inventory pre-warm failed (non-fatal): %s", exc)

    asyncio.create_task(_prewarm_inventory())
    logger.info("🚀 All systems started — v3.0.0")


@app.on_event("shutdown")
async def shutdown_event():
    simulator = getattr(app.state, "simulator", None)
    if simulator: simulator.stop()
    trigger = getattr(app.state, "trigger", None)
    if trigger: trigger.stop()
    db_repo = getattr(app.state, "db_repo", None)
    if db_repo:
        try:
            await db_repo.close()
            logger.info("✅ DB pool closed")
        except Exception:
            pass
    logger.info("Shutting down cleanly.")


# ══════════════════════════════════════════════════════════════════════════════
# Météo helper
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_weather_fallback() -> dict:
    global _weather_cache, _weather_cache_time
    now = time.time()
    if _weather_cache and (now - _weather_cache_time) < _WEATHER_CACHE_TTL:
        return _weather_cache
    try:
        import httpx
        r    = httpx.get(
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=36.8065&longitude=10.1815"
            "&current=weathercode,temperature_2m,precipitation"
            "&timezone=Africa/Tunis",
            timeout=4,
        )
        d    = r.json().get("current", {})
        code = d.get("weathercode", 1)
        temp = d.get("temperature_2m", 22)
        ICONS  = {0:"☀️",1:"🌤️",2:"⛅",3:"☁️",45:"🌫️",61:"🌧️",80:"🌦️",95:"⛈️"}
        LABELS = {0:"Ciel dégagé",1:"Peu nuageux",2:"Partiellement nuageux",
                  3:"Couvert",45:"Brouillard",61:"Pluie légère",80:"Averses",95:"Orage"}
        effect = -0.15 if code >= 61 else -0.05 if code >= 3 else +0.10 if code == 0 else +0.05
        result = {
            "weather_icon":   ICONS.get(code, "⛅"),
            "weather_label":  LABELS.get(code, "Variable"),
            "weather_effect": effect,
            "temperature":    temp,
            "is_rainy":       code >= 61,
        }
        _weather_cache, _weather_cache_time = result, now
        return result
    except Exception as e:
        logger.warning(f"[WEATHER] {e}")
        return {"weather_icon":"🌤️","weather_label":"Tunis Lac","weather_effect":0.0,"temperature":22,"is_rainy":False}


# ══════════════════════════════════════════════════════════════════════════════
# Helpers Agents
# ══════════════════════════════════════════════════════════════════════════════

def _extract_summary(raw, gap_pct, urgency, cr, dt, feo):
    if not raw:
        return _make_fallback_summary(gap_pct, urgency, cr, dt, feo)
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            s = json.loads(raw).get("analyst_summary", "")
            if s:
                return s.strip()
        except Exception:
            import re
            m = re.search(r'"analyst_summary"\s*:\s*"([^"]+)"', raw)
            if m:
                return m.group(1).strip()
    return raw[:400] if not raw.startswith("{") else _make_fallback_summary(gap_pct, urgency, cr, dt, feo)


def _make_fallback_summary(gap_pct, urgency, cr, dt, feo):
    if urgency == "HIGH":
        return f"Gap critique {gap_pct:.1f}%. CA {cr:,.0f}/{dt:,.0f} TND. Forecast {feo:,.0f} TND — action immédiate."
    if urgency == "MEDIUM":
        return f"Gap {gap_pct:.1f}% à surveiller. CA {cr:,.0f}/{dt:,.0f} TND. Forecast EOD: {feo:,.0f} TND."
    return f"Performance correcte — gap {gap_pct:.1f}%. CA {cr:,.0f}/{dt:,.0f} TND."


def _compute_heatmap(urgency):
    HIGH   = (["med","med","high","high","crit","crit","high","med"],
              ["low","med","med","high","high","crit","crit","high"])
    MEDIUM = (["low","med","med","high","high","med","med","low"],
              ["low","low","med","med","high","med","med","low"])
    LOW    = (["low","low","med","med","med","low","low","low"],
              ["low","low","low","med","med","low","low","low"])
    traffic, risk = {"HIGH": HIGH, "MEDIUM": MEDIUM}.get(urgency, LOW)
    return {
        "hours":   ["11AM","12PM","1PM","2PM","3PM","4PM","5PM","6PM"],
        "traffic": traffic,
        "weather": ["low","low","low","med","med","high","high","high"],
        "stock":   ["low","low","med","high","high","crit","crit","crit"],
        "event":   ["low","low","low","low","low","med","high","high"],
        "risk":    risk,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Cycle Agents (Analyste + Stratège)
# ══════════════════════════════════════════════════════════════════════════════

async def _run_agents(store_id: str, cycle: int) -> dict:
    """Lance Agent Analyste + Agent Stratège et log dans le monitoring."""
    import uuid
    cycle_id = f"cycle-{uuid.uuid4().hex[:8]}"
    started  = datetime.utcnow()

    print(f"\n{'='*60}")
    print(f"  🤖 AGENT ANALYSTE — Cycle #{cycle} @ {datetime.now().strftime('%H:%M:%S')} | {store_id}")
    print(f"{'='*60}")

    provider    = get_data_provider()
    pos_data    = await provider.fetch_pos_data(store_id)
    pos_history = await provider.fetch_pos_history(store_id)
    prediction  = await provider.fetch_timesfm_prediction(store_id)

    cr     = pos_data.get("current_revenue", 0) or 0
    dt_val = pos_data.get("daily_target",    1007) or 1007
    feo    = prediction.get("forecast_end_of_day", 0) or 0
    gap_amt = max(0, dt_val - cr)
    gap_pct = round((gap_amt / dt_val * 100) if dt_val > 0 else 0, 1)
    att     = round((cr / dt_val) * 100, 1) if dt_val > 0 else 0
    hour    = datetime.now().hour
    hrs_rem = max(0, 20 - hour)

    # Urgence
    cov         = 100.0
    if gap_amt > 0:
        cov = round(min(100.0, ((feo - cr) / gap_amt) * 100), 1)
    gap_score   = min(1.0, gap_pct / 50.0)
    time_press  = min(1.0, max(0.0, (hour - 8) / 10))
    cov_penalty = max(0.0, (100 - cov) / 100) * 0.3
    urg_score   = round(min(1.0, gap_score * 0.5 + time_press * 0.3 + cov_penalty), 3)
    urg_level   = "HIGH" if (gap_pct > 30 and cov < 80) else "MEDIUM" if gap_pct > 15 else "LOW"
    if hrs_rem < 2 and gap_pct > 10:
        urg_level = "HIGH"
        urg_score = max(urg_score, 0.85)

    # ── Agent Analyste ────────────────────────────────────────────────────
    analyst_summary = ""
    try:
        agent       = get_analyst_agent()
        result      = await agent.ainvoke({
            "pos_data":           {**pos_data, "current_hour": hour},
            "pos_history":        pos_history,
            "timesfm_prediction": prediction,
            "feedback_history":   [],
        })
        raw             = result.get("analyst_summary", "")
        analyst_summary = _extract_summary(raw, gap_pct, urg_level, cr, dt_val, feo)
        urg_level       = result.get("urgency_level", urg_level)
        urg_score       = result.get("urgency_score", urg_score)
        print(f"  💬 {analyst_summary[:100]}")
    except (Exception, asyncio.CancelledError) as e:
        analyst_summary = _make_fallback_summary(gap_pct, urg_level, cr, dt_val, feo)
        logger.warning(f"[ANALYST] Fallback: {str(e)[:60]}")

    analyst_output = {
        "pos_data":           pos_data,
        "pos_history":        pos_history,
        "prediction":         prediction,
        "urgency_level":      urg_level,
        "urgency_score":      urg_score,
        "gap_objectif":       gap_pct,
        "gap_pct":            gap_pct,
        "gap_amount":         gap_amt,
        "analyst_summary":    analyst_summary,
        "current_revenue":    cr,
        "daily_target":       dt_val,
        "forecast_eod":       feo,
        "attainment":         att,
        "coverage":           cov,
        "mape":               14.3,
        "timesfm_prediction": prediction,
        "feedback_history":   [],
        "metrics":            {"cycle_id": cycle_id, "store_id": "I63"},
    }

    # ── Agent Stratège ────────────────────────────────────────────────────
    print(f"\n  🎯 AGENT STRATÈGE — Cycle #{cycle}")
    stratege_output: dict = {}
    try:
        stratege       = get_stratege_agent()
        strat_state    = await stratege.ainvoke(analyst_output)
        ctx            = (strat_state.get("external_context") or {}).get("summary") or {}
        nb_actions     = len(strat_state.get("strategie_actions") or [])
        print(f"  Météo   : {ctx.get('weather_icon','')} {ctx.get('weather_label','')}")
        print(f"  Cause   : {str(strat_state.get('cause_racine',''))[:60]}")
        print(f"  Actions : {nb_actions}")
        stratege_output = {
            "strategie":         strat_state.get("strategie",         ""),
            "strategie_actions": strat_state.get("strategie_actions", []),
            "cause_racine":      strat_state.get("cause_racine",      ""),
            "context_heatmap":   strat_state.get("context_heatmap",   {}),
            "context_signals":   strat_state.get("context_signals",   []),
            "external_context":  strat_state.get("external_context",  {}),
            "message_manager":   strat_state.get("message_manager",   ""),
            "focus_produits":    strat_state.get("focus_produits",    []),
            "rag_used":          strat_state.get("rag_used",          False),
            "nb_rag_scripts":    strat_state.get("nb_rag_scripts",    0),
        }
    except (Exception, asyncio.CancelledError) as e:
        logger.warning(f"[STRATEGE] Fallback: {str(e)[:60]}")
        stratege_output = {
            "strategie":"","strategie_actions":[],"cause_racine":f"Gap {gap_pct:.1f}%",
            "context_heatmap":{},"context_signals":[],"external_context":{},
            "message_manager":"","focus_produits":[],"rag_used":False,"nb_rag_scripts":0,
        }

    # ── Log monitoring ─────────────────────────────────────────────────────
    try:
        from agent_logger import log_cycle
        total_ms = (datetime.utcnow() - started).total_seconds() * 1000
        log_cycle(
            cycle_id       = cycle_id,
            state          = {**analyst_output, **stratege_output},
            total_ms       = total_ms,
            triggered_by   = "websocket",
            store_id       = "I63",
            nodes_executed = 2,
            errors_count   = 0,
            rag_used       = stratege_output.get("rag_used", False),
            nb_rag_scripts = stratege_output.get("nb_rag_scripts", 0),
        )
    except Exception:
        pass

    return {**analyst_output, **stratege_output}


# ══════════════════════════════════════════════════════════════════════════════
# Payload Builder
# ══════════════════════════════════════════════════════════════════════════════

def _build_payload(analysis: dict) -> dict:
    pos_data        = analysis.get("pos_data")        or {}
    pos_history     = analysis.get("pos_history")     or []
    prediction      = analysis.get("prediction")      or {}
    urgency_level   = analysis.get("urgency_level")   or "LOW"
    urgency_score   = analysis.get("urgency_score")   or 0
    gap_pct         = analysis.get("gap_pct")         or 0
    gap_amount      = analysis.get("gap_amount")      or 0
    analyst_summary = analysis.get("analyst_summary") or ""
    cr              = analysis.get("current_revenue") or 0
    dt_val          = analysis.get("daily_target")    or 1007
    feo             = analysis.get("forecast_eod")    or 0
    att             = analysis.get("attainment")      or 0
    strategie         = analysis.get("strategie")         or ""
    strategie_actions = analysis.get("strategie_actions") or []
    cause_racine      = analysis.get("cause_racine")      or ""
    context_heatmap   = analysis.get("context_heatmap")   or {}
    context_signals   = analysis.get("context_signals")   or []
    external_ctx      = analysis.get("external_context")  or {}
    message_manager   = analysis.get("message_manager")   or ""
    focus_produits    = analysis.get("focus_produits")    or []

    # ── Météo ──────────────────────────────────────────────────────────────
    weather_sum = external_ctx.get("summary") or {}
    holidays    = external_ctx.get("holidays") or {}
    events_data = external_ctx.get("events")   or {}

    if not weather_sum or not weather_sum.get("weather_icon"):
        weather_sum = _fetch_weather_fallback()

    weather_str  = f"{weather_sum.get('weather_icon','🌤️')} {weather_sum.get('weather_label','Tunis')}".strip()
    next_holiday = holidays.get("next_holiday") or {}
    event_str    = ""
    if holidays.get("is_holiday_today"):
        event_str = f"🎉 {(holidays.get('today_holiday') or {}).get('name','Jour férié')}"
    elif next_holiday.get("name"):
        event_str = f"📅 {next_holiday['name']} dans {next_holiday.get('days_until',0)}j"

    all_promos = (events_data.get("promotions") or []) + (events_data.get("new_offers") or [])
    promo_str  = f"🎯 {len(all_promos)} offre(s) Ooredoo" if all_promos else ""

    store_context = {
        "weather":     weather_str,
        "event":       event_str,
        "promo":       promo_str,
        "stock_alert": "📦 iPhone 15 — 3 unités restantes",
        "temperature": f"{weather_sum.get('temperature', 22)}°C",
    }

    # ── Advisors depuis PostgreSQL ─────────────────────────────────────────
    hour          = datetime.now().hour
    hours_elapsed = max(1, hour - 9)
    nb_tx         = pos_data.get("nb_transactions_today", 0) or 0
    visitors_h    = max(10, round(nb_tx / hours_elapsed * random.uniform(0.9, 1.2)))

    try:
        from data.json_service import _query
        pg_sellers = _query("""
            SELECT a.agent_id,
                   a.agent_name || ' ' || a.agent_surname AS full_name,
                   COALESCE(SUM(CASE WHEN t.date_only = CURRENT_DATE THEN t.lig_ttc ELSE 0 END), 0) AS revenue_today,
                   COALESCE(SUM(t.lig_ttc), 0) AS revenue_total,
                   COUNT(CASE WHEN t.date_only = CURRENT_DATE THEN 1 END) AS nb_ventes
            FROM agents a
            LEFT JOIN transactions t ON t.agent_id=a.agent_id
                AND t.store_id='I63' AND t.lig_ttc > 0
            WHERE a.store_id='I63' AND a.actif=true
            GROUP BY a.agent_id, a.agent_name, a.agent_surname
            ORDER BY revenue_today DESC, revenue_total DESC
        """)
        total_hist    = sum(float(s["revenue_total"]) for s in pg_sellers) or 1
        sellers_built = []
        for s in pg_sellers:
            rev_today = float(s["revenue_today"])
            if rev_today == 0 and cr > 0:
                rev_today = round(cr * float(s["revenue_total"]) / total_hist, 2)
            nb_v = int(s["nb_ventes"]) or max(1, round(rev_today / 80))
            sellers_built.append({
                "name":          s["full_name"].title(),
                "revenue_today": rev_today,
                "nb_ventes":     nb_v,
                "agent_id":      s["agent_id"],
            })
        sellers = sellers_built
    except Exception as e:
        logger.warning(f"[PAYLOAD] sellers fallback: {e}")
        sellers = pos_data.get("sellers", []) or []

    per_seller = round(dt_val / max(len(sellers), 1))
    max_rev    = max((s.get("revenue_today", 0) for s in sellers), default=0)

    advisors = sorted([
        {
            "id":         s.get("name","").replace(" ","_").lower(),
            "name":       s.get("name",""),
            "revenue":    round(s.get("revenue_today",0)),
            "target":     per_seller,
            "attainment": round(s.get("revenue_today",0) / max(per_seller,1) * 100),
            "nb_ventes":  s.get("nb_ventes",0),
            "status":     "Top"    if s.get("revenue_today",0) == max_rev
                          else "OK"     if s.get("revenue_today",0) / max(per_seller,1) >= 0.5
                          else "Urgent",
            "trend":      "up" if s.get("revenue_today",0) >= per_seller * 0.7 else "down",
        }
        for s in sellers
    ], key=lambda x: -x["revenue"])
    for i, a in enumerate(advisors):
        a["rank"] = i + 1

    # ── Coaching Cards ─────────────────────────────────────────────────────
    coaching_cards = [
        {
            "id":       f"card-{i}",
            "advisor":  a["name"],
            "initials": "".join(p[0].upper() for p in a["name"].split() if p)[:2],
            "gap":      max(0, 100 - a["attainment"]),
            "urgency":  "HIGH" if a["attainment"] < 50 else "MEDIUM" if a["attainment"] < 80 else "LOW",
            "context":  f"{a['nb_ventes']} ventes · {a['revenue']:,} DT",
            "advice":   analyst_summary[:120] or f"Gap {max(0,100-a['attainment'])}% — focus produits premium",
            "action":   strategie_actions[0].get("action","Focus bundle terminal + forfait") if strategie_actions else "Focus bundle terminal + forfait",
            "produit":  strategie_actions[0].get("produit_cible","Forfait Flexi 25Go") if strategie_actions else "Forfait Flexi 25Go",
            "status":   "pending",
            "priority": i + 1,
        }
        for i, a in enumerate(advisors) if a["attainment"] < 80
    ]

    # ── Hourly performance ─────────────────────────────────────────────────
    tph         = round(dt_val / 11)
    hourly_rate = cr / hours_elapsed
    hd: dict[int, float] = {}
    for tx in pos_history:
        t = tx.get("transaction_time")
        if t and hasattr(t, "hour"):
            hd[t.hour] = hd.get(t.hour, 0.0) + tx.get("revenue", 0.0)

    hourly_performance = []
    for h in range(9, min(hour + 1, 21)):
        rev   = max(0, hd.get(h, 0.0))
        label = "12PM" if h == 12 else f"{h}AM" if h < 12 else f"{h-12}PM"
        hourly_performance.append({
            "hour":     label, "revenue": round(rev), "actual": round(rev),
            "target":   tph,
            "forecast": round(rev) if rev > 0 else round(max(0, hourly_rate) * random.uniform(0.85, 1.10)),
            "risk":     rev > 0 and rev < tph * 0.85,
        })
    for h in range(hour + 1, 21):
        label = "12PM" if h == 12 else f"{h}AM" if h < 12 else f"{h-12}PM"
        mult  = (random.uniform(1.10,1.30) if h in [12,13,17,18]
                 else random.uniform(0.60,0.80) if h in [9,10,19,20]
                 else random.uniform(0.90,1.10))
        hourly_performance.append({"hour":label,"revenue":0,"actual":0,"target":tph,"forecast":round(tph*mult),"risk":False})

    # ── Risk hours ─────────────────────────────────────────────────────────
    risk_hours = [
        {"hour":h["hour"],"target_pct":round((h["revenue"]/tph)*100),"units_behind":round((h["revenue"]-tph)/150)}
        for h in hourly_performance
        if 0 < tph > 0 and h["revenue"] > 0 and (h["revenue"]/tph)*100 < 85
    ]

    # ── Product mix ────────────────────────────────────────────────────────
    by_cat: dict[str, float] = {}
    for tx in pos_history:
        cat = tx.get("product_category","Autre")
        by_cat[cat] = by_cat.get(cat,0) + tx.get("revenue",0)
    product_mix = [
        {"product":cat,"revenue":round(rev),
         "attainment":round(rev/max(dt_val/max(len(by_cat),1),1)*100),
         "stock_level":"Low" if "Smartphone" in cat else "OK",
         "forecast":round(rev*1.10)}
        for cat, rev in sorted(by_cat.items(), key=lambda x: -x[1])
    ]

    # ── Heatmap et signaux ─────────────────────────────────────────────────
    final_heatmap = context_heatmap if (context_heatmap and context_heatmap.get("traffic")) else _compute_heatmap(urgency_level)
    w_eff   = weather_sum.get("weather_effect", 0)
    w_level = "high" if w_eff <= -0.15 else "med" if w_eff < 0 else "low"
    final_signals = context_signals or [
        {"type":"weather","label":f"{weather_sum.get('weather_icon','⛅')} {weather_sum.get('weather_label','Tunis')} — {weather_sum.get('temperature',22)}°C","level":w_level,"value":w_eff},
        {"type":"stock","label":"📦 iPhone 15 — 3 unités restantes","level":"high","value":-0.3},
    ]
    if event_str and not context_signals:
        final_signals.append({"type":"holiday","label":event_str,"level":"med","value":0.5})
    if promo_str and not context_signals:
        final_signals.append({"type":"event","label":promo_str,"level":"low","value":0.2})

    return {
        "type":               "metrics_update",
        "timestamp":          datetime.now().isoformat(),
        "ca_today":           cr,
        "ca_target":          dt_val,
        "attainment":         att,
        "visitors_h":         visitors_h,
        "agents_live":        4,
        "niveau_urgence":     urgency_level,
        "urgency_score":      urgency_score,
        "ecart_objectif":     gap_pct,
        "gap_amount":         gap_amount,
        "analyst_summary":    analyst_summary,
        "route_to":           "strategie" if urgency_level in ("HIGH","MEDIUM") else "coach",
        "forecast_eod":       feo,
        "forecast_ci_low":    (prediction.get("confidence_interval") or {}).get("low", 0),
        "forecast_ci_high":   (prediction.get("confidence_interval") or {}).get("high", 0),
        "forecast_mape":      analysis.get("mape", 14.3),
        "strategie":          strategie,
        "strategie_actions":  strategie_actions,
        "cause_racine":       cause_racine,
        "message_manager":    message_manager,
        "focus_produits":     focus_produits,
        "store_context":      store_context,
        "context_heatmap":    final_heatmap,
        "context_signals":    final_signals,
        "coaching_cards":     coaching_cards,
        "advisors":           advisors,
        "liveAdvisors":       advisors,
        "analyst_nodes": {
            "receive_pos":    {"status":"done","transactions":len(pos_history)},
            "compute_gap":    {"status":"done","gap_pct":gap_pct,"gap_amount":gap_amount},
            "call_timesfm":   {"status":"done","forecast_eod":feo},
            "detect_urgency": {"status":"done","level":urgency_level,"score":urgency_score},
            "llm_summary":    {"status":"done","summary":analyst_summary},
        },
        "hourly_performance":     hourly_performance,
        "risk_hours":             risk_hours,
        "product_mix":            product_mix,
        "advisor_priorities": [
            {
                "advisor_id": a["id"], "name": a["name"],
                "performance": a["attainment"],
                "priority":   "TOP_CLOSE" if a["attainment"] >= 80 else "STABLE" if a["attainment"] >= 50 else "AT_RISK",
                "reason":     f"{a['nb_ventes']} ventes · {a['revenue']:,} DT",
                "action":     f"Gap {100-a['attainment']}% à combler" if a["attainment"] < 80 else "Maintenir le rythme",
            }
            for a in advisors
        ],
        "rag_used":               analysis.get("rag_used", False),
        "nb_rag_scripts":         analysis.get("nb_rag_scripts", 0),
        "ca_yesterday_same_hour": cr * 0.88,
        "last_cycle_id":          f"cycle_{datetime.now().strftime('%H%M%S')}",
    }


# ══════════════════════════════════════════════════════════════════════════════
# WebSocket — Store
# ══════════════════════════════════════════════════════════════════════════════

@app.websocket("/ws/store/{store_id}")
async def ws_store(websocket: WebSocket, store_id: str):
    await websocket.accept()

    if store_id in _active_stores:
        await asyncio.sleep(2)
        if store_id in _active_stores:
            await websocket.close(code=1008)
            return

    _active_stores.add(store_id)
    print(f"\n🔌 Frontend connecté → {store_id}")
    mapped_id = STORE_MAP.get(store_id, "OOR_LAC_01")
    cycle     = 0

    async def heartbeat():
        while True:
            try:
                await asyncio.sleep(30)
                await websocket.send_text(json.dumps({"type":"ping","timestamp":datetime.now().isoformat()}))
            except Exception:
                break

    hb_task = asyncio.create_task(heartbeat())

    try:
        # ── Payload initial immédiat ───────────────────────────────────────
        try:
            provider    = get_data_provider()
            pos_data    = await provider.fetch_pos_data(mapped_id)
            pos_history = await provider.fetch_pos_history(mapped_id)
            prediction  = await provider.fetch_timesfm_prediction(mapped_id)
            cr   = pos_data.get("current_revenue", 0) or 0
            dt_  = pos_data.get("daily_target",    1007) or 1007
            ga   = max(0, dt_ - cr)
            gp   = round((ga / dt_ * 100) if dt_ > 0 else 0, 1)
            feo  = prediction.get("forecast_end_of_day", 0) or 0
            ul   = "HIGH" if gp > 30 else "MEDIUM" if gp > 15 else "LOW"
            initial = {
                "pos_data": pos_data, "pos_history": pos_history,
                "prediction": prediction, "urgency_level": ul,
                "urgency_score": round(min(1.0, gp/60), 3),
                "gap_pct": gp, "gap_amount": ga,
                "analyst_summary": f"Gap {gp:.1f}% — CA {cr:,.0f}/{dt_:,.0f} TND. Analyse en cours...",
                "current_revenue": cr, "daily_target": dt_, "forecast_eod": feo,
                "attainment": round((cr/dt_)*100,1) if dt_ > 0 else 0,
                "coverage": 100.0, "mape": 14.3,
                "strategie":"","strategie_actions":[],"cause_racine":"",
                "context_heatmap":{},"context_signals":[],"external_context":{},
                "message_manager":"","focus_produits":[],"timesfm_prediction":prediction,"feedback_history":[],
            }
            await websocket.send_text(json.dumps(_build_payload(initial), default=str))
            print("✅ Payload initial envoyé")
        except Exception as e:
            logger.warning(f"[WS] Payload initial: {e}")

        # ── Cycle principal ────────────────────────────────────────────────
        while True:
            cycle += 1
            task   = asyncio.create_task(_run_agents(mapped_id, cycle))

            while not task.done():
                await asyncio.sleep(30)
                if not task.done():
                    try:
                        await websocket.send_text(json.dumps({
                            "type":"processing","message":"Analyse en cours...","timestamp":datetime.now().isoformat()
                        }))
                    except Exception:
                        task.cancel()
                        break

            analysis = await task
            msg      = _build_payload(analysis)
            await websocket.send_text(json.dumps(msg, default=str))
            print(f"\n📤 Payload #{cycle} ({len(json.dumps(msg,default=str)):,} bytes) — prochain dans 2min")
            await asyncio.sleep(120)

    except WebSocketDisconnect:
        print(f"\n🔌 Déconnecté : {store_id}")
    except Exception as e:
        logger.error(f"[WS] Erreur cycle #{cycle}: {e}")
        try:
            await websocket.close()
        except Exception:
            pass
    finally:
        hb_task.cancel()
        _active_stores.discard(store_id)
        print(f"🔓 Slot libéré → {store_id}")


@app.websocket("/ws/advisor/{advisor_id}")
async def ws_advisor(websocket: WebSocket, advisor_id: str):
    await websocket.accept()
    try:
        while True:
            await websocket.send_text(json.dumps({
                "type":"coach_update","advisor_id":advisor_id,
                "timestamp":datetime.now().isoformat(),"status":"active",
            }))
            await asyncio.sleep(30)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# REST Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    trigger = getattr(app.state, "trigger", None)
    last    = trigger.last_result if trigger else None
    return {
        "status": "ok", "version": "3.0.0",
        "modules": ["inventory","sales","agents-ia","rag","coach","monitoring"],
        "last_cycle": {
            "cycle_id":       last.get("cycle_id")       if last else None,
            "niveau_urgence": last.get("niveau_urgence") if last else None,
            "ecart_objectif": last.get("ecart_objectif") if last else None,
            "forecast_eod":   last.get("forecast_eod")   if last else None,
        } if last else None,
    }


@app.get("/api/v1/stores/{store_id}/metrics")
async def get_store_metrics(store_id: str):
    mapped_id = STORE_MAP.get(store_id, "OOR_LAC_01")
    provider  = get_data_provider()
    pos_data  = await provider.fetch_pos_data(mapped_id)
    weather   = _fetch_weather_fallback()
    cr = pos_data.get("current_revenue", 0) or 0
    dt = pos_data.get("daily_target",    1007) or 1007
    return JSONResponse({
        "ca_today":    cr, "ca_target": dt,
        "attainment":  round((cr/dt)*100,1) if dt > 0 else 0,
        "visitors_h":  pos_data.get("nb_transactions_today", 0),
        "agents_live": 4,
        "store_context": {
            "weather":     f"{weather['weather_icon']} {weather['weather_label']}",
            "event":"","promo":"",
            "stock_alert": "📦 iPhone 15 — 3 unités restantes",
            "temperature": f"{weather['temperature']}°C",
        },
        "ca_yesterday_same_hour": cr * 0.88,
        "source": "postgresql",
    })


@app.get("/api/v1/forecast/eod/{store_id}")
async def get_forecast_eod(store_id: str):
    mapped_id = STORE_MAP.get(store_id, "OOR_LAC_01")
    provider  = get_data_provider()
    pred      = await provider.fetch_timesfm_prediction(mapped_id)
    pos_data  = await provider.fetch_pos_data(mapped_id)
    dt = pos_data.get("daily_target", 1007) or 1007
    cr = pos_data.get("current_revenue", 0) or 0
    ga = max(0, dt - cr)
    gp = round((ga/dt*100) if dt > 0 else 0, 1)
    return JSONResponse({
        "eod":pred.get("forecast_end_of_day",0),"gap_pct":gp,"gap_amount":ga,
        "ci_low":(pred.get("confidence_interval") or {}).get("low",0),
        "ci_high":(pred.get("confidence_interval") or {}).get("high",0),
        "source":"prophet+ratio",
    })


@app.get("/api/v1/stores/{store_id}/advisors")
async def get_advisors(store_id: str):
    mapped_id = STORE_MAP.get(store_id, "OOR_LAC_01")
    provider  = get_data_provider()
    pos_data  = await provider.fetch_pos_data(mapped_id)
    dt        = pos_data.get("daily_target", 1007) or 1007
    cr        = pos_data.get("current_revenue", 0) or 0
    try:
        from data.json_service import _query
        pg = _query("""
            SELECT a.agent_id, a.agent_name||' '||a.agent_surname AS full_name,
                   COALESCE(SUM(CASE WHEN t.date_only=CURRENT_DATE THEN t.lig_ttc ELSE 0 END),0) AS revenue_today,
                   COALESCE(SUM(t.lig_ttc),0) AS revenue_total,
                   COUNT(CASE WHEN t.date_only=CURRENT_DATE THEN 1 END) AS nb_ventes
            FROM agents a
            LEFT JOIN transactions t ON t.agent_id=a.agent_id AND t.store_id='I63' AND t.lig_ttc>0
            WHERE a.store_id='I63' AND a.actif=true
            GROUP BY a.agent_id, a.agent_name, a.agent_surname
            ORDER BY revenue_today DESC, revenue_total DESC
        """)
        th = sum(float(s["revenue_total"]) for s in pg) or 1
        ps = round(dt / max(len(pg), 1))
        sellers = []
        for s in pg:
            rt = float(s["revenue_today"])
            if rt == 0 and cr > 0:
                rt = round(cr * float(s["revenue_total"]) / th, 2)
            nb = int(s["nb_ventes"]) or max(1, round(rt/80))
            sellers.append({"name":s["full_name"].title(),"revenue_today":rt,"nb_ventes":nb})
    except Exception as e:
        logger.warning(f"[ADVISORS] PG fallback: {e}")
        sellers = pos_data.get("sellers",[]) or []
        ps = round(dt / max(len(sellers), 1))
    mr = max((s.get("revenue_today",0) for s in sellers), default=0)
    advisors = sorted([
        {"id":s.get("name","").replace(" ","_").lower(),"name":s.get("name",""),
         "revenue":round(s.get("revenue_today",0)),"target":ps,
         "attainment":round(s.get("revenue_today",0)/max(ps,1)*100),
         "nb_ventes":s.get("nb_ventes",0),
         "status":"Top" if s.get("revenue_today",0)==mr else "OK"}
        for s in sellers
    ], key=lambda x: -x["revenue"])
    return JSONResponse({"advisors":advisors,"source":"postgresql"})


@app.get("/api/v1/stores/{store_id}/live-analysis")
async def get_live_analysis(store_id: str):
    mapped_id   = STORE_MAP.get(store_id, "OOR_LAC_01")
    provider    = get_data_provider()
    pos_data    = await provider.fetch_pos_data(mapped_id)
    pos_history = await provider.fetch_pos_history(mapped_id)
    prediction  = await provider.fetch_timesfm_prediction(mapped_id)
    cr  = pos_data.get("current_revenue",0) or 0
    dt  = pos_data.get("daily_target",1007) or 1007
    ga  = max(0, dt - cr)
    gp  = round((ga/dt*100) if dt > 0 else 0, 1)
    feo = prediction.get("forecast_end_of_day",0) or 0
    ul  = "HIGH" if gp > 30 else "MEDIUM" if gp > 15 else "LOW"
    return JSONResponse(_build_payload({
        "pos_data":pos_data,"pos_history":pos_history,"prediction":prediction,
        "urgency_level":ul,"urgency_score":round(min(1.0,gp/60),3),
        "gap_pct":gp,"gap_amount":ga,
        "analyst_summary":f"Gap {gp:.1f}% — CA {cr:,.0f}/{dt:,.0f} TND.",
        "current_revenue":cr,"daily_target":dt,"forecast_eod":feo,
        "attainment":round((cr/dt)*100,1) if dt > 0 else 0,
        "coverage":100.0,"mape":14.3,
        "strategie":"","strategie_actions":[],"cause_racine":"",
        "context_heatmap":{},"context_signals":[],"external_context":{},
        "message_manager":"","focus_produits":[],
    }))