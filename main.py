"""
Unified API Server - Inventory + Sales + Agents IA
===================================================
Run with:
    uvicorn main:app --reload --port 8000
"""

import sys
import os
import asyncio
import logging
from dotenv import load_dotenv
from monitoring import router as monitoring_router

# ── Fix module paths ──────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "inventory-module"))
sys.path.insert(0, os.path.join(BASE_DIR, "sales-module"))

# ── Load .env ─────────────────────────────────────────────────
load_dotenv(os.path.join(BASE_DIR, "inventory-module", ".env"), override=True)
load_dotenv(os.path.join(BASE_DIR, "sales-module", ".env"), override=True)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import json
import random
from datetime import datetime

# ── Inventory Module ──────────────────────────────────────────
from src.api.routes import router as inventory_router, invalidate_store
from src.tools.internal.stock_tools import _DataCache as InventoryDataCache

# ── Sales Module Routers (coéquipier) ─────────────────────────
from api.routers.cycle    import router as cycle_router,    set_orchestrator
from api.routers.forecast import router as forecast_router, set_json_svc as set_forecast_json
from api.routers.stores   import router as stores_router,   set_json_svc as set_stores_json

# ── Sales Dependencies ────────────────────────────────────────
from data.json_service           import JsonDataService
from data.realtime_simulator     import RealtimeSimulator
from mcp_servers.timefm.tools    import TimesFMTools
from orchestration.graph         import CycleOrchestrator
from orchestration.trigger       import CronTrigger

# ── Vos Agents IA ─────────────────────────────────────────────
from data.mock_provider import get_data_provider
from modules.coaching.agents.analyst.agent         import get_analyst_agent
from modules.coaching.agents.stratege.agent        import get_stratege_agent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Verrou anti-double connexion ──────────────────────────────
_active_stores: set[str] = set()

STORE_MAP = {
    "store-lac2":    "OOR_LAC_01",
    "store-menzah":  "OOR_MENZAH_02",
    "store-sfax":    "OOR_SFAX_03",
    "OOR_LAC_01":    "OOR_LAC_01",
    "OOR_MENZAH_02": "OOR_MENZAH_02",
    "OOR_SFAX_03":   "OOR_SFAX_03",
}

# ══════════════════════════════════════════════════════════════
# App
# ══════════════════════════════════════════════════════════════

app = FastAPI(
    title="Unified Retail AI API",
    description="Inventory + Sales + Agents IA ",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ══════════════════════════════════════════════════════════════
# Routers
# ══════════════════════════════════════════════════════════════

app.include_router(inventory_router, prefix="/api/inventory")
app.include_router(cycle_router)
app.include_router(forecast_router)
app.include_router(stores_router)
app.include_router(monitoring_router)

# ══════════════════════════════════════════════════════════════
# Startup / Shutdown
# ══════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup_event():
    _active_stores.clear()
    load_dotenv(os.path.join(BASE_DIR, "inventory-module", ".env"), override=True)
    load_dotenv(os.path.join(BASE_DIR, "sales-module", ".env"), override=True)
    logger.info("✅ Environment variables loaded")

    json_svc = JsonDataService()
    set_forecast_json(json_svc)
    set_stores_json(json_svc)
    app.state.json_svc = json_svc

    timefm = TimesFMTools(model_path="./models/timefm")
    await timefm.load_model()
    app.state.timefm = timefm
    logger.info("✅ TimesFM model loaded")

    simulator = RealtimeSimulator(json_svc, interval_seconds=15)
    simulator.start()
    app.state.simulator = simulator

    def _on_sale(store_id: str, sku: str, units: int) -> None:
        InventoryDataCache.record_sale('STORE-001', sku, float(units))
        # Pass sku + new live stock so invalidate_store can fire an instant
        # stock_delta broadcast before the full pipeline re-runs (~10s later).
        new_stock = InventoryDataCache.get_current_stock(sku, 'STORE-001')
        invalidate_store('STORE-001', sku=sku, new_stock=new_stock)

    simulator.on_sale = _on_sale
    logger.info("✅ Inventory ↔ Sales sync wired")

    orchestrator = CycleOrchestrator(json_svc=json_svc, timefm=timefm)
    trigger = CronTrigger(
        orchestrator=orchestrator,
        store_id="store-lac2",
        interval_minutes=15
    )
    trigger.start()
    set_orchestrator(orchestrator, trigger)
    app.state.trigger      = trigger
    app.state.orchestrator = orchestrator
    logger.info("✅ Orchestration cycle started")
    logger.info("🚀 Slots WebSocket réinitialisés")
    logger.info("✨ All systems started!")


@app.on_event("shutdown")
async def shutdown_event():
    simulator = getattr(app.state, "simulator", None)
    if simulator: simulator.stop()
    trigger = getattr(app.state, "trigger", None)
    if trigger: trigger.stop()
    logger.info("Shutting down cleanly.")


# ══════════════════════════════════════════════════════════════
# Helpers Agents IA (de votre websocket_endpoint.py)
# ══════════════════════════════════════════════════════════════

def _extract_summary(raw, gap_pct, urgency, cr, dt, feo):
    if not raw: return _make_fallback_summary(gap_pct, urgency, cr, dt, feo)
    raw = raw.strip()
    if raw.startswith('{'):
        try:
            parsed = json.loads(raw)
            s = parsed.get("analyst_summary", "")
            if s: return s.strip()
        except:
            import re
            m = re.search(r'"analyst_summary"\s*:\s*"([^"]+)"', raw)
            if m: return m.group(1).strip()
    if not raw.startswith('{'): return raw[:400]
    return _make_fallback_summary(gap_pct, urgency, cr, dt, feo)


def _make_fallback_summary(gap_pct, urgency, cr, dt, feo):
    if urgency == "HIGH":
        return f"Gap critique de {gap_pct:.1f}%. CA {cr:,.0f} / {dt:,.0f} TND. Forecast {feo:,.0f} TND — action immédiate."
    elif urgency == "MEDIUM":
        return f"Gap de {gap_pct:.1f}% à surveiller. CA {cr:,.0f} / {dt:,.0f} TND. Forecast EOD : {feo:,.0f} TND."
    return f"Performance correcte — gap {gap_pct:.1f}%. CA {cr:,.0f} / {dt:,.0f} TND."


def _compute_heatmap(urgency):
    if urgency == "HIGH":
        traffic = ["med","med","high","high","crit","crit","high","med"]
        risk    = ["low","med","med","high","high","crit","crit","high"]
    elif urgency == "MEDIUM":
        traffic = ["low","med","med","high","high","med","med","low"]
        risk    = ["low","low","med","med","high","med","med","low"]
    else:
        traffic = ["low","low","med","med","med","low","low","low"]
        risk    = ["low","low","low","med","med","low","low","low"]
    return {
        "hours":   ["11AM","12PM","1PM","2PM","3PM","4PM","5PM","6PM"],
        "traffic": traffic,
        "weather": ["low","low","low","med","med","high","high","high"],
        "stock":   ["low","low","med","high","high","crit","crit","crit"],
        "event":   ["low","low","low","low","low","med","high","high"],
        "risk":    risk,
    }


async def _run_agents(store_id: str, cycle: int) -> dict:
    """Lance Agent Analyste + Agent Stratège."""
    now = datetime.now().strftime("%H:%M:%S")
    print(f"\n{'='*60}")
    print(f"  🤖 AGENT ANALYSTE — Cycle #{cycle} @ {now} | {store_id}")
    print(f"{'='*60}")

    provider    = get_data_provider()
    pos_data    = await provider.fetch_pos_data(store_id)
    pos_history = await provider.fetch_pos_history(store_id)
    prediction  = await provider.fetch_timesfm_prediction(store_id)

    cr      = pos_data.get("current_revenue", 0) or 0
    dt_val  = pos_data.get("daily_target", 18000) or 18000
    feo     = prediction.get("forecast_end_of_day", 0) or 0
    gap_amt = max(0, dt_val - cr)
    gap_pct = round((gap_amt / dt_val * 100) if dt_val > 0 else 0, 1)
    att     = round((cr / dt_val) * 100, 1) if dt_val > 0 else 0

    current_hour    = datetime.now().hour
    hours_remaining = max(0, 20 - current_hour)
    time_pressure   = min(1.0, max(0.0, (current_hour - 8) / 10))
    gap_score       = min(1.0, gap_pct / 50.0)
    cov             = 100.0
    if gap_amt > 0:
        cov = round(min(100.0, ((feo - cr) / gap_amt) * 100), 1)
    cov_penalty     = max(0.0, (100 - cov) / 100) * 0.3
    urgency_score   = round(min(1.0, (gap_score*0.5) + (time_pressure*0.3) + cov_penalty), 3)

    if gap_pct > 30 and cov < 80:   urgency_level = "HIGH"
    elif gap_pct > 15:               urgency_level = "MEDIUM"
    else:                            urgency_level = "LOW"
    if hours_remaining < 2 and gap_pct > 10:
        urgency_level = "HIGH"
        urgency_score = max(urgency_score, 0.85)

    # LLM Analyste
    analyst_summary = ""
    try:
        agent        = get_analyst_agent()
        agent_result = await agent.ainvoke({
            "pos_data":           {**pos_data, "current_hour": current_hour},
            "pos_history":        pos_history,
            "timesfm_prediction": prediction,
            "feedback_history":   [],
        })
        raw             = agent_result.get("analyst_summary", "")
        analyst_summary = _extract_summary(raw, gap_pct, urgency_level, cr, dt_val, feo)
        urgency_level   = agent_result.get("urgency_level", urgency_level)
        urgency_score   = agent_result.get("urgency_score", urgency_score)
        print(f"  💬 {analyst_summary}")
    except Exception as e:
        analyst_summary = _make_fallback_summary(gap_pct, urgency_level, cr, dt_val, feo)
        logger.warning(f"[ANALYST] Fallback: {str(e)[:60]}")

    analyst_output = {
        "pos_data": pos_data, "pos_history": pos_history,
        "prediction": prediction,
        "urgency_level": urgency_level, "urgency_score": urgency_score,
        "gap_objectif": gap_pct, "gap_pct": gap_pct, "gap_amount": gap_amt,
        "analyst_summary": analyst_summary,
        "current_revenue": cr, "daily_target": dt_val,
        "forecast_eod": feo, "attainment": att, "coverage": cov, "mape": 14.3,
        "timesfm_prediction": prediction, "feedback_history": [],
    }

    # Agent Stratège
    print(f"\n  🎯 AGENT STRATÈGE — Cycle #{cycle}")
    stratege_output = {}
    try:
        stratege       = get_stratege_agent()
        stratege_state = await stratege.ainvoke(analyst_output)
        ctx            = stratege_state.get("external_context", {}).get("summary", {}) or {}
        print(f"  Météo   : {ctx.get('weather_icon','')} {ctx.get('weather_label','')}")
        print(f"  Cause   : {stratege_state.get('cause_racine','')[:60]}")
        print(f"  Actions : {len(stratege_state.get('strategie_actions', []))}")
        stratege_output = {
            "strategie":         stratege_state.get("strategie", ""),
            "strategie_actions": stratege_state.get("strategie_actions", []),
            "cause_racine":      stratege_state.get("cause_racine", ""),
            "context_heatmap":   stratege_state.get("context_heatmap", {}),
            "context_signals":   stratege_state.get("context_signals", []),
            "external_context":  stratege_state.get("external_context", {}),
            "message_manager":   stratege_state.get("message_manager", ""),
            "focus_produits":    stratege_state.get("focus_produits", []),
        }
    except Exception as e:
        logger.error(f"[STRATEGE] Erreur: {e}")
        stratege_output = {
            "strategie": analyst_summary, "strategie_actions": [],
            "cause_racine": f"Gap {gap_pct:.1f}%", "context_heatmap": {},
            "context_signals": [], "external_context": {},
            "message_manager": "", "focus_produits": [],
        }

    return {**analyst_output, **stratege_output}


def _build_payload(analysis: dict) -> dict:
    """Construit le payload WebSocket pour le frontend."""
    pos_data        = analysis.get("pos_data")        or {}
    pos_history     = analysis.get("pos_history")     or []
    prediction      = analysis.get("prediction")      or {}
    urgency_level   = analysis.get("urgency_level")   or "LOW"
    urgency_score   = analysis.get("urgency_score")   or 0
    gap_pct         = analysis.get("gap_pct")         or 0
    gap_amount      = analysis.get("gap_amount")      or 0
    analyst_summary = analysis.get("analyst_summary") or ""
    cr              = analysis.get("current_revenue") or 0
    dt_val          = analysis.get("daily_target")    or 18000
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

    # Météo
    weather_summary = external_ctx.get("summary") or {}
    holidays        = external_ctx.get("holidays") or {}
    events_data     = external_ctx.get("events")   or {}

    weather_str = f"{weather_summary.get('weather_icon','')} {weather_summary.get('weather_label','')}".strip()
    next_holiday = holidays.get("next_holiday") or {}
    event_str = ""
    if holidays.get("is_holiday_today"):
        event_str = f"🎉 {(holidays.get('today_holiday') or {}).get('name','')}"
    elif next_holiday.get("name"):
        event_str = f"📅 {next_holiday['name']} dans {next_holiday.get('days_until',0)}j"

    all_promos = ((events_data.get("promotions") or []) + (events_data.get("new_offers") or []))
    promo_str  = f"🎯 {len(all_promos)} offre(s) active(s) Ooredoo" if all_promos else ""

    store_context = {
        "weather": weather_str or "⛅ Météo en cours...",
        "event":   event_str,
        "promo":   promo_str,
        "stock_alert": "📦 iPhone 15 — 3 unités restantes",
    }

    # Advisors
    current_hour  = datetime.now().hour
    hours_elapsed = max(1, current_hour - 9)
    sellers       = pos_data.get("sellers", []) or []
    per_seller    = round(dt_val / max(len(sellers), 1))
    max_rev       = max((s.get("revenue_today", 0) for s in sellers), default=0)
    nb_tx         = pos_data.get("nb_transactions_today", 0) or 0
    visitors_h    = max(10, round(nb_tx / hours_elapsed * random.uniform(0.9, 1.2)))

    advisors = sorted([
        {
            "id":         s.get("name","").replace(" ","_").lower(),
            "name":       s.get("name",""),
            "revenue":    round(s.get("revenue_today", 0)),
            "target":     per_seller,
            "attainment": round(s.get("revenue_today",0) / max(per_seller,1) * 100),
            "nb_ventes":  s.get("nb_ventes", 0),
            "status":     "Top" if s.get("revenue_today",0) == max_rev
                          else "OK" if s.get("revenue_today",0) / max(per_seller,1) >= 0.5
                          else "Urgent",
            "trend": "up" if s.get("revenue_today",0) >= per_seller * 0.7 else "down",
        }
        for s in sellers
    ], key=lambda x: -x["revenue"])
    for i, a in enumerate(advisors): a["rank"] = i + 1

    # Hourly performance
    target_per_hour = round(dt_val / 11)
    hourly_rate     = cr / hours_elapsed
    hourly_dict: dict[int, float] = {}
    for tx in pos_history:
        tx_time = tx.get("transaction_time")
        if tx_time and hasattr(tx_time, "hour"):
            h = tx_time.hour
            hourly_dict[h] = hourly_dict.get(h, 0.0) + tx.get("revenue", 0.0)

    hourly_performance = []
    for h in range(9, min(current_hour + 1, 21)):
        rev = max(0, hourly_dict.get(h, 0.0))
        label = "12PM" if h==12 else f"{h}AM" if h<12 else f"{h-12}PM"
        hourly_performance.append({
            "hour": label, "revenue": round(rev), "actual": round(rev),
            "target": target_per_hour,
            "forecast": round(rev) if rev > 0 else round(max(0,hourly_rate)*random.uniform(0.85,1.10)),
            "risk": rev > 0 and rev < target_per_hour * 0.85,
        })
    for h in range(current_hour+1, 21):
        label = "12PM" if h==12 else f"{h}AM" if h<12 else f"{h-12}PM"
        mult = random.uniform(1.10,1.30) if h in [12,13,17,18] else \
               random.uniform(0.60,0.80) if h in [9,10,19,20] else random.uniform(0.90,1.10)
        hourly_performance.append({
            "hour": label, "revenue": 0, "actual": 0,
            "target": target_per_hour, "forecast": round(target_per_hour*mult), "risk": False,
        })

    # Risk hours
    risk_hours = []
    for h in hourly_performance:
        rev = h["revenue"]
        tgt_pct = round((rev/target_per_hour)*100) if target_per_hour > 0 and rev > 0 else 0
        if 0 < tgt_pct < 85:
            risk_hours.append({"hour": h["hour"], "target_pct": tgt_pct,
                                "units_behind": round((rev-target_per_hour)/150)})

    # Product mix
    by_cat: dict[str, float] = {}
    for tx in pos_history:
        cat = tx.get("product_category", "Autre")
        by_cat[cat] = by_cat.get(cat, 0) + tx.get("revenue", 0)
    product_mix = [
        {"product": cat, "revenue": round(rev),
         "attainment": round(rev/max(dt_val/max(len(by_cat),1),1)*100),
         "stock_level": "Low" if "Smartphone" in cat else "OK",
         "forecast": round(rev*1.10)}
        for cat, rev in sorted(by_cat.items(), key=lambda x: -x[1])
    ]

    final_heatmap = context_heatmap if (context_heatmap and context_heatmap.get("traffic")) \
                    else _compute_heatmap(urgency_level)
    final_signals = context_signals if context_signals else [
        {"type":"weather","label":"Météo non disponible","level":"low","value":0},
        {"type":"stock","label":"iPhone 15 — 3 unités","level":"high","value":-0.3},
    ]

    return {
        "type": "metrics_update", "timestamp": datetime.now().isoformat(),
        "ca_today": cr, "ca_target": dt_val, "attainment": att,
        "visitors_h": visitors_h, "agents_live": 4,
        "niveau_urgence": urgency_level, "urgency_score": urgency_score,
        "ecart_objectif": gap_pct, "gap_amount": gap_amount,
        "analyst_summary": analyst_summary,
        "route_to": "strategie" if urgency_level in ("HIGH","MEDIUM") else "coach",
        "forecast_eod": feo,
        "forecast_ci_low":  (prediction.get("confidence_interval") or {}).get("low", 0),
        "forecast_ci_high": (prediction.get("confidence_interval") or {}).get("high", 0),
        "forecast_mape": analysis.get("mape", 14.3),
        "strategie": strategie, "strategie_actions": strategie_actions,
        "cause_racine": cause_racine, "message_manager": message_manager,
        "focus_produits": focus_produits,
        "store_context": store_context,
        "context_heatmap": final_heatmap, "context_signals": final_signals,
        "advisors": advisors, "liveAdvisors": advisors,
        "analyst_nodes": {
            "receive_pos":    {"status":"done","transactions":len(pos_history)},
            "compute_gap":    {"status":"done","gap_pct":gap_pct,"gap_amount":gap_amount},
            "call_timesfm":   {"status":"done","forecast_eod":feo},
            "detect_urgency": {"status":"done","level":urgency_level,"score":urgency_score},
            "llm_summary":    {"status":"done","summary":analyst_summary},
        },
        "hourly_performance": hourly_performance,
        "risk_hours": risk_hours, "product_mix": product_mix,
        "advisor_priorities": [
            {"advisor_id":a["id"],"name":a["name"],"performance":a["attainment"],
             "priority":"TOP_CLOSE" if a["attainment"]>=80 else "STABLE" if a["attainment"]>=50 else "AT_RISK",
             "reason":f"{a['nb_ventes']} ventes · {a['revenue']:,} DT",
             "action":f"Gap {100-a['attainment']}% à combler" if a["attainment"]<80 else "Maintenir le rythme"}
            for a in advisors
        ],
        "ca_yesterday_same_hour": cr * 0.88,
        "last_cycle_id": f"cycle_{datetime.now().strftime('%H%M%S')}",
    }


# ══════════════════════════════════════════════════════════════
# WebSocket — Store (Agents IA)
# ══════════════════════════════════════════════════════════════

@app.websocket("/ws/store/{store_id}")
async def ws_store(websocket: WebSocket, store_id: str):
    await websocket.accept()

    if store_id in _active_stores:
        await asyncio.sleep(2)          # give the slot time to clear if it's mid-cleanup
        if store_id in _active_stores:  # re-check after waiting
            print(f"⚠️  Double connexion bloquée → {store_id}")
            await websocket.close(code=1008)
            return

    _active_stores.add(store_id)
    print(f"\n🔌 Frontend connecté → {store_id}")
    mapped_id = STORE_MAP.get(store_id, "OOR_LAC_01")
    cycle     = 0

    # Heartbeat 30s
    async def heartbeat():
        while True:
            try:
                await asyncio.sleep(30)
                await websocket.send_text(json.dumps({
                    "type": "ping", "timestamp": datetime.now().isoformat()
                }))
            except: break

    heartbeat_task = asyncio.create_task(heartbeat())

    try:
        # Payload initial immédiat
        try:
            provider    = get_data_provider()
            pos_data    = await provider.fetch_pos_data(mapped_id)
            pos_history = await provider.fetch_pos_history(mapped_id)
            prediction  = await provider.fetch_timesfm_prediction(mapped_id)
            cr  = pos_data.get("current_revenue", 0) or 0
            dt_ = pos_data.get("daily_target", 18000) or 18000
            ga  = max(0, dt_ - cr)
            gp  = round((ga/dt_*100) if dt_ > 0 else 0, 1)
            feo = prediction.get("forecast_end_of_day", 0) or 0
            ul  = "HIGH" if gp>30 else "MEDIUM" if gp>15 else "LOW"
            initial = {
                "pos_data": pos_data, "pos_history": pos_history, "prediction": prediction,
                "urgency_level": ul, "urgency_score": round(min(1.0,gp/60),3),
                "gap_pct": gp, "gap_amount": ga,
                "analyst_summary": f"Gap {gp:.1f}% — CA {cr:,.0f}/{dt_:,.0f} TND. Analyse en cours...",
                "current_revenue": cr, "daily_target": dt_, "forecast_eod": feo,
                "attainment": round((cr/dt_)*100,1) if dt_>0 else 0,
                "coverage": 100.0, "mape": 14.3,
                "strategie":"","strategie_actions":[],"cause_racine":"",
                "context_heatmap":{},"context_signals":[],"external_context":{},
                "message_manager":"","focus_produits":[],
                "timesfm_prediction":prediction,"feedback_history":[],
            }
            await websocket.send_text(json.dumps(_build_payload(initial), default=str))
            print(f"✅ Payload initial envoyé")
        except Exception as e:
            logger.warning(f"[WS] Payload initial échoué: {e}")

        # Cycle principal
        while True:
            cycle      += 1
            agent_task  = asyncio.create_task(_run_agents(mapped_id, cycle))
            while not agent_task.done():
                await asyncio.sleep(30)
                if not agent_task.done():
                    try:
                        await websocket.send_text(json.dumps({
                            "type":      "processing",
                            "message":   "Analyse en cours...",
                            "timestamp": datetime.now().isoformat(),
                        }))
                    except Exception:
                        agent_task.cancel()
                        break
            analysis = await agent_task
            msg      = _build_payload(analysis)
            await websocket.send_text(json.dumps(msg, default=str))
            print(f"\n📤 Payload cycle #{cycle} ({len(json.dumps(msg,default=str)):,} bytes)")
            print(f"⏳ Prochain cycle dans 2 minutes...\n")
            await asyncio.sleep(120)

    except WebSocketDisconnect:
        print(f"\n🔌 Frontend déconnecté : {store_id}")
    except Exception as e:
        logger.error(f"[WS] Erreur cycle #{cycle}: {e}")
        try: await websocket.close()
        except: pass
    finally:
        heartbeat_task.cancel()
        _active_stores.discard(store_id)
        print(f"🔓 Slot libéré → {store_id}\n")


# ══════════════════════════════════════════════════════════════
# WebSocket — Advisor
# ══════════════════════════════════════════════════════════════

@app.websocket("/ws/advisor/{advisor_id}")
async def ws_advisor(websocket: WebSocket, advisor_id: str):
    await websocket.accept()
    try:
        while True:
            await websocket.send_text(json.dumps({
                "type": "coach_update", "advisor_id": advisor_id,
                "timestamp": datetime.now().isoformat(), "status": "active",
            }))
            await asyncio.sleep(30)
    except: pass


# ══════════════════════════════════════════════════════════════
# REST Endpoints
# ══════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    trigger = getattr(app.state, "trigger", None)
    last    = trigger.last_result if trigger else None
    return {
        "status": "ok", "version": "2.0.0",
        "modules": ["inventory", "sales", "agents-ia"],
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
    cr = pos_data.get("current_revenue", 0) or 0
    dt = pos_data.get("daily_target", 18000) or 18000
    return JSONResponse({
        "ca_today": cr, "ca_target": dt,
        "attainment": round((cr/dt)*100,1) if dt>0 else 0,
        "visitors_h": pos_data.get("nb_transactions_today", 0),
        "agents_live": 4,
        "store_context": {"weather":"⛅ Météo en cours...","event":"","promo":"","stock_alert":"📦 iPhone 15 — 3 unités"},
        "ca_yesterday_same_hour": cr * 0.88,
    })


@app.get("/api/v1/forecast/eod/{store_id}")
async def get_forecast_eod(store_id: str):
    mapped_id = STORE_MAP.get(store_id, "OOR_LAC_01")
    provider  = get_data_provider()
    pred      = await provider.fetch_timesfm_prediction(mapped_id)
    pos_data  = await provider.fetch_pos_data(mapped_id)
    dt = pos_data.get("daily_target", 18000) or 18000
    cr = pos_data.get("current_revenue", 0)   or 0
    ga = max(0, dt - cr)
    gp = round((ga/dt*100) if dt>0 else 0, 1)
    return JSONResponse({
        "eod": pred.get("forecast_end_of_day", 0), "gap_pct": gp, "gap_amount": ga,
        "ci_low":  (pred.get("confidence_interval") or {}).get("low", 0),
        "ci_high": (pred.get("confidence_interval") or {}).get("high", 0),
    })


@app.get("/api/v1/stores/{store_id}/advisors")
async def get_advisors(store_id: str):
    mapped_id = STORE_MAP.get(store_id, "OOR_LAC_01")
    provider  = get_data_provider()
    pos_data  = await provider.fetch_pos_data(mapped_id)
    sellers   = pos_data.get("sellers", []) or []
    dt        = pos_data.get("daily_target", 18000) or 18000
    ps        = round(dt / max(len(sellers), 1))
    max_rev   = max((s.get("revenue_today",0) for s in sellers), default=0)
    advisors  = sorted([
        {"id": s.get("name","").replace(" ","_").lower(), "name": s.get("name",""),
         "revenue": round(s.get("revenue_today",0)), "target": ps,
         "attainment": round(s.get("revenue_today",0)/max(ps,1)*100),
         "nb_ventes": s.get("nb_ventes",0),
         "status": "Top" if s.get("revenue_today",0)==max_rev else "OK"}
        for s in sellers
    ], key=lambda x: -x["revenue"])
    return JSONResponse({"advisors": advisors})


@app.get("/api/v1/stores/{store_id}/live-analysis")
async def get_live_analysis(store_id: str):
    mapped_id   = STORE_MAP.get(store_id, "OOR_LAC_01")
    provider    = get_data_provider()
    pos_data    = await provider.fetch_pos_data(mapped_id)
    pos_history = await provider.fetch_pos_history(mapped_id)
    prediction  = await provider.fetch_timesfm_prediction(mapped_id)
    cr  = pos_data.get("current_revenue", 0) or 0
    dt  = pos_data.get("daily_target", 18000) or 18000
    ga  = max(0, dt - cr)
    gp  = round((ga/dt*100) if dt>0 else 0, 1)
    feo = prediction.get("forecast_end_of_day", 0) or 0
    ul  = "HIGH" if gp>30 else "MEDIUM" if gp>15 else "LOW"
    analysis = {
        "pos_data": pos_data, "pos_history": pos_history, "prediction": prediction,
        "urgency_level": ul, "urgency_score": round(min(1.0,gp/60),3),
        "gap_pct": gp, "gap_amount": ga,
        "analyst_summary": f"Gap {gp:.1f}% — CA {cr:,.0f}/{dt:,.0f} TND.",
        "current_revenue": cr, "daily_target": dt, "forecast_eod": feo,
        "attainment": round((cr/dt)*100,1) if dt>0 else 0,
        "coverage": 100.0, "mape": 14.3,
        "strategie":"","strategie_actions":[],"cause_racine":"",
        "context_heatmap":{},"context_signals":[],"external_context":{},
        "message_manager":"","focus_produits":[],
    }
    return JSONResponse(_build_payload(analysis))


@app.post("/api/v1/coach/chat")
async def coach_chat(request: dict):
    """CoachAgent — Réponses LLM contextualisées Ooredoo."""
    message      = request.get("message", "")
    advisor_name = request.get("advisor_name", "Conseiller")
    store_id     = request.get("store_id", "store-lac2")
    context      = request.get("context", {})

    if not message:
        return JSONResponse({"reply": "", "source": "empty"})

    mapped_id = STORE_MAP.get(store_id, "OOR_LAC_01")
    provider  = get_data_provider()
    pos_data  = await provider.fetch_pos_data(mapped_id)

    cr      = pos_data.get("current_revenue", 0) or 0
    dt      = pos_data.get("daily_target", 18000) or 18000
    gap     = max(0, dt - cr)
    gap_pct = round((gap/dt*100) if dt>0 else 0, 1)

    strategie = context.get("strategie", "")
    actions   = context.get("strategie_actions", []) or []
    cause     = context.get("cause_racine", "")
    weather   = context.get("weather", "")
    urgency   = context.get("urgency", "MEDIUM")

    actions_txt = "\n".join([
        f"- P{a.get('priorite','')}) {a.get('action','')} → {a.get('produit_cible','')}"
        for a in actions[:3]
    ]) or "Analyse en cours..."

    system_prompt = f"""Tu es le CoachAgent IA d'Ooredoo Tunisie.
Conseiller : {advisor_name} | CA : {cr:,.0f}/{dt:,.0f} TND | Gap : {gap_pct:.1f}% | Urgence : {urgency}
Météo : {weather or 'Normale'}
Cause racine : {cause or f'Gap {gap_pct:.1f}%'}
Actions Stratège : {actions_txt}
Catalogue : iPhone 16 Pro 1299 DT | Samsung A55 5G 899 DT | Forfait 5G Max 49 DT/mois | Box Fibre 59 DT/mois | Assurance Premium 9 DT/mois | AirPods Pro 3 279 DT | Apple Watch S10 449 DT
Règles : français direct, max 120 mots, prix réels Ooredoo, commence par l'action."""

    try:
        from langchain_ollama import ChatOllama
        from langchain_core.messages import HumanMessage, SystemMessage
        llm = ChatOllama(
            model=os.getenv("OLLAMA_MODEL","llama3.2:latest"),
            base_url=os.getenv("OLLAMA_BASE_URL","http://localhost:11434"),
            temperature=0.3, num_predict=250, num_ctx=2048,
        )
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=message),
        ])
        return JSONResponse({"reply": response.content.strip(), "source": "llm",
                             "timestamp": datetime.now().isoformat()})
    except Exception as e:
        logger.warning(f"[COACH] Fallback: {str(e)[:60]}")
        return JSONResponse({"reply": f"Gap {gap_pct:.0f}% — Urgence {urgency}. Focus : Assurance Premium sur chaque vente terminal. Bundle Smartphone + Forfait 5G = panier optimal.",
                             "source": "fallback", "timestamp": datetime.now().isoformat()})