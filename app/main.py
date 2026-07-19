"""
Unified API Server — Inventory + Sales + Agents IA
===================================================
    uvicorn main:app --port 8000
v4 : cache du dernier payload → renvoi immédiat à toute nouvelle connexion
     slot unique par store remplacé par multi-connexion avec broadcast
"""

import sys
import os
import asyncio
import logging
from typing import Dict, Set
import json, random, time
from datetime import datetime
from dotenv import load_dotenv

# Force UTF-8 on Windows (cp1252 choke on emojis/accents in logs)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Racine du repo (app/main.py → un niveau au-dessus de app/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

from app.api.auth import router as auth_router
from app.sales.data.postgres_provider import get_data_provider
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.inventory.api.routes import router as inventory_router, invalidate_store
from app.inventory.api.supply_routes import router as supply_router
from app.inventory.tools.internal.stock_tools import _DataCache as InventoryDataCache
from app.inventory.repositories.inventory_repo import InventoryRepo
from app.inventory.stock_simulator import StockSimulator

from app.api.cycle import router as cycle_router, set_orchestrator
from app.api.forecast import router as forecast_router, set_json_svc as set_forecast_json
from app.api.stores import router as stores_router, set_json_svc as set_stores_json

from app.sales.data.json_service import JsonDataService
from app.sales.data.realtime_simulator import RealtimeSimulator
from app.sales.mcp.timefm.tools import TimesFMTools
from app.sales.orchestration.graph import CycleOrchestrator
from app.sales.orchestration.trigger import CronTrigger

from app.sales.coaching.agents.analyst.agent import get_analyst_agent
from app.sales.coaching.agents.stratege.agent import get_stratege_agent

from app.api.monitoring import router as monitoring_router
from app.core.config import DEFAULT_STORE_ID

_log_handler = logging.StreamHandler(sys.stderr)
_log_handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))

# Toutes les erreurs (ERROR/CRITICAL, tracebacks inclus) de tous les modules
# sont extraites dans logs/errors.log — consultable sans filtrer la console.
os.makedirs("logs", exist_ok=True)
_err_handler = logging.FileHandler("logs/errors.log", encoding="utf-8")
_err_handler.setLevel(logging.ERROR)
_err_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(name)s | %(levelname)s | %(message)s"))

logging.basicConfig(level=logging.INFO, handlers=[_log_handler, _err_handler])
logger = logging.getLogger(__name__)

# Langfuse est de la télémétrie best-effort : quand le serveur local est KO
# (500), son SDK + backoff spamment la console avec retries et tracebacks
# sans aucun impact sur les agents. On ne garde que le silence.
logging.getLogger("langfuse").setLevel(logging.CRITICAL)
logging.getLogger("backoff").setLevel(logging.CRITICAL)

# ── Registry multi-connexions (remplace _active_stores slot unique) ───────────
# store_id → set of active WebSocket connections
_store_connections: Dict[str, Set[WebSocket]] = {}

# Cache du dernier payload connu par store → renvoi immédiat aux nouvelles connexions
_last_payload: Dict[str, str] = {}

# Flag : le cycle agent tourne-t-il déjà pour ce store ?
_agent_running: Dict[str, bool] = {}

_live_stock: Dict[str, int] = {}

# Alias boutique (ids auth/frontend → id POS canonique). Extensible via
# STORE_ALIASES_JSON dans .env ; toute boutique inconnue passe telle quelle
# (pass-through multi-boutiques, plus de repli forcé vers une boutique unique).
STORE_MAP = {
    "store-lac2": DEFAULT_STORE_ID,
    "lac2":       DEFAULT_STORE_ID,
    "OOR_LAC_01": DEFAULT_STORE_ID,
}
try:
    STORE_MAP.update(json.loads(os.getenv("STORE_ALIASES_JSON", "{}")))
except Exception as _e:
    logger.warning("STORE_ALIASES_JSON invalide: %s", _e)


def map_store_id(store_id: str) -> str:
    """Résout un alias boutique ; les ids inconnus passent tels quels."""
    return STORE_MAP.get(store_id, store_id or DEFAULT_STORE_ID)

_weather_cache: dict = {}
_weather_cache_time: float = 0.0
_WEATHER_CACHE_TTL = 300

app = FastAPI(
    title="Unified Retail AI API",
    description="Inventory + Sales + Agents IA (Analyste · Stratège · Coach)",
    version="4.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware
    _limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
    app.state.limiter = _limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    logger.info("✅ slowapi rate limiting activé")
except ImportError:
    logger.warning("⚠️ slowapi non installé — rate limiting désactivé (pip install slowapi)")
except Exception as _e:
    logger.warning("⚠️ slowapi init échoué (%s) — rate limiting désactivé", _e)

app.include_router(auth_router)
app.include_router(inventory_router, prefix="/api/inventory")
app.include_router(supply_router)
app.include_router(cycle_router)
app.include_router(forecast_router)
app.include_router(stores_router)
app.include_router(monitoring_router)

try:
    from app.api.feedback import router as feedback_router
    app.include_router(feedback_router)
    logger.info("✅ Feedback loop router chargé")
except ImportError as e:
    logger.warning(f"⚠️ feedback_router non trouvé: {e}")

try:
    from app.api.kpis import router as kpis_router
    app.include_router(kpis_router)
    logger.info("✅ KPIs router chargé")
except ImportError as e:
    logger.warning(f"⚠️ kpis_router non trouvé: {e}")

try:
    from app.sales.coaching.agents.coach.coach_chat import router as coach_rag_router
    app.include_router(coach_rag_router)
    logger.info("✅ Coach Chat RAG (LangGraph) chargé")
except ImportError as e:
    logger.warning(f"⚠️ coach_chat_rag non trouvé: {e}")

try:
    from app.api.product_requests import router as product_requests_router
    app.include_router(product_requests_router)
    logger.info("✅ Product requests router chargé (demandes conseillers)")
except ImportError as e:
    logger.warning(f"⚠️ product_requests_router non trouvé: {e}")

try:
    from app.api.hitl import router as hitl_router
    app.include_router(hitl_router)
    logger.info("✅ HITL router chargé")
except ImportError as e:
    logger.warning(f"⚠️ hitl_router non trouvé: {e}")

try:
    from app.api.supervisor import router as supervisor_router
    app.include_router(supervisor_router)
    logger.info("✅ SupervisorAgent router chargé (S8.1)")
except ImportError as e:
    logger.warning(f"⚠️ supervisor_router non trouvé: {e}")


# ── Broadcast vers toutes les connexions actives d'un store ──────────────────
async def _broadcast(store_id: str, payload: str) -> None:
    """Envoie payload à toutes les connexions WS actives du store."""
    _last_payload[store_id] = payload          # mise en cache systématique
    conns = list(_store_connections.get(store_id, set()))
    dead  = set()
    for ws in conns:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    # Nettoyer les connexions mortes
    if dead and store_id in _store_connections:
        _store_connections[store_id] -= dead


async def _safe_send(websocket: WebSocket, data: str) -> bool:
    try:
        await websocket.send_text(data)
        return True
    except Exception:
        return False


async def _warmup_sales_data(store_id: str = DEFAULT_STORE_ID) -> None:
    logger.info(f"⏳ Warm-up données sales pour {store_id}...")
    try:
        provider = get_data_provider()
        await provider.fetch_pos_data(store_id)
        await provider.fetch_pos_history(store_id)
        await provider.fetch_timesfm_prediction(store_id)
        logger.info(f"✅ Warm-up données sales terminé pour {store_id}")
    except Exception as e:
        logger.warning(f"⚠️ Warm-up sales data échoué: {e}")


@app.on_event("startup")
async def startup_event():
    _store_connections.clear()
    _last_payload.clear()
    _agent_running.clear()

    # Pool asyncpg partagé, ouvert sur la boucle uvicorn : les outils agents le
    # réutilisent au lieu d'ouvrir une connexion par appel.
    from app.core.db import get_async_pool
    if await get_async_pool() is None:
        logger.warning("⚠️ Pool asyncpg indisponible — connexions directes en repli")

    # Le schéma appartient aux migrations Alembic — l'app vérifie, ne crée pas.
    from app.core.schema_check import verify_schema
    verify_schema()

    try:
        from app.sales.coaching.orchestrator.bootstrap import initialize_coach_stratege_orchestrator
        await initialize_coach_stratege_orchestrator()
        logger.info("✅ Coach-Stratège orchestrator initialized")
    except Exception as e:
        logger.warning(f"⚠️ Coach-Stratège orchestrator: {e}")

    # Le trigger sync_stock_on_sale et la fonction associée vivent dans les
    # migrations (baseline 0001) — plus aucun DDL au démarrage.

    json_svc = JsonDataService()
    set_forecast_json(json_svc)
    set_stores_json(json_svc)
    app.state.json_svc = json_svc

    await _warmup_sales_data(DEFAULT_STORE_ID)

    timefm = TimesFMTools(model_path="./models/timefm")
    await timefm.load_model()
    app.state.timefm = timefm
    logger.info("✅ TimesFM chargé")

    stock_sim = None
    simulator = RealtimeSimulator(store_id=DEFAULT_STORE_ID, interval=15)

    _RT_DB_CFG = {
        "host":     os.getenv("POSTGRES_HOST", "localhost"),
        "port":     int(os.getenv("POSTGRES_PORT", "5432")),
        "database": os.getenv("POSTGRES_DB", "ooredoo_sales"),
        "user":     os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "admin"),
    }

    def _on_sale(store_id: str, sku: str, units: int, product_name: str = None, amount: float = None) -> None:
        """
        Callback temps réel : une vente vient d'être enregistrée dans transactions_rt.
        La persistance PG (décrement stock_levels + mouvement VENTE) est faite
        par le trigger trg_sync_stock_on_sale — ici uniquement :
        1. Met à jour le cache mémoire _live_stock
        2. Broadcast WebSocket immédiat avec le nouveau niveau de stock
        3. Déclenche une alerte si stock critique
        """
        sku_str = str(sku)

        # ── 1. Lire stock actuel depuis PG ou cache ────────────────────────
        try:
            from app.inventory.repositories.inventory_repo import SyncInventoryRepo
            db_row = SyncInventoryRepo.get_stock_level(sku_str, store_id)
        except Exception:
            db_row = None

        if sku_str not in _live_stock:
            if db_row is not None:
                _live_stock[sku_str] = float(
                    db_row.get("quantity_available") or db_row.get("stock_on_hand") or 0
                )
            else:
                override = InventoryDataCache._stock_overrides.get((sku_str, store_id))
                _live_stock[sku_str] = float(override) if override is not None else 50.0

        current   = _live_stock[sku_str]
        new_stock = max(0.0, current - units)
        _live_stock[sku_str] = new_stock
        InventoryDataCache.record_sale(sku_str, store_id, units)

        severity = (
            "rupture"  if new_stock == 0  else
            "critical" if new_stock <= 3  else
            "warning"  if new_stock <= 10 else
            "ok"
        )
        logger.info(
            "[RT_SYNC] Sale %s@%s | stock %.0f→%.0f (-%d) [%s]",
            sku_str, store_id, current, new_stock, units, severity
        )

        # ── 2. Broadcast (la persistance PG est faite par le trigger DB) ────
        # Le décrement de inventory.stock_levels ET l'écriture du mouvement
        # VENTE dans supply.stock_movements sont assurés par le trigger
        # trg_sync_stock_on_sale sur transactions_rt (migrations 0001+0009).
        # L'UPDATE applicatif qui vivait ici échouait de toute façon :
        # quantity_available est une colonne générée, non modifiable — et le
        # réparer aurait décrémenté le stock deux fois (trigger + app).
        async def _persist_and_broadcast():
            # Broadcast immédiat vers le frontend — event dédié temps réel
            label = product_name or f"SKU {sku_str}"
            amount_str = f" — {amount:.0f} DT" if amount is not None else ""
            rt_event = json.dumps({
                "type":         "realtime_stock_update",
                "sku":          sku_str,
                "store_id":     store_id,
                "product_name": product_name,
                "amount":       amount,
                "units_sold":   units,
                "stock_before": round(current),
                "stock_after":  round(new_stock),
                "severity":     severity,
                "timestamp":    datetime.utcnow().isoformat(),
                "message":    (
                    f"Vente : {label}{amount_str} — stock {int(current)}→{int(new_stock)} unités"
                    + (" ⚠️ RUPTURE" if severity == "rupture" else
                       " 🔴 CRITIQUE" if severity == "critical" else
                       " 🟡 BAS" if severity == "warning" else "")
                ),
            })
            await _broadcast(store_id, rt_event)

            # Invalidate inventory cache + alerte si seuil critique
            try:
                invalidate_store(store_id, sku=sku_str, new_stock=new_stock)
                if severity in ("rupture", "critical"):
                    from app.inventory.api.routes import _broadcast_to_store
                    await _broadcast_to_store(store_id, {
                        "type":     "stock_alert",
                        "sku":      sku_str,
                        "stock":    int(new_stock),
                        "severity": severity,
                        "message":  f"⚠️ Stock {severity} : SKU {sku_str} — {int(new_stock)} unités restantes",
                    })
                    # AlertBus Redis → AlertCycleTrigger déclenche un cycle
                    # de coaching immédiat sur ce magasin (données flux).
                    try:
                        from app.sales.core.alert_bus import get_alert_bus
                        get_alert_bus().publish_stock_alert(
                            store_id=store_id,
                            sku=sku_str,
                            product_name=product_name or f"SKU {sku_str}",
                            stock_qty=int(new_stock),
                            risk_level="CRITICAL" if severity == "rupture" else "HIGH",
                            days_to_stockout=0.0 if severity == "rupture" else 0.5,
                            revenue_at_risk=float(amount or 0) * 10,
                        )
                    except Exception as e:
                        logger.debug("[RT_SYNC] alert_bus publish: %s", e)
            except Exception as e:
                logger.debug("[RT_SYNC] broadcast: %s", e)

        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(_persist_and_broadcast())

    simulator.on_sale = _on_sale
    simulator.start()
    app.state.simulator = simulator
    logger.info("✅ Inventory ↔ Sales sync")

    orchestrator = CycleOrchestrator(json_svc=json_svc, timefm=timefm)

    # ── Magasins du cycle proactif (fini le mono-magasin codé en dur) ──
    # COACHING_STORE_IDS=I63,S01 pour une liste explicite ; sinon top-N par CA
    # des 7 derniers jours (COACHING_MAX_STORES, défaut 3). Les 196 boutiques
    # restent couvertes par AlertCycleTrigger (événementiel, pattern *).
    active_stores = [DEFAULT_STORE_ID]
    _env_stores = [s.strip() for s in os.getenv("COACHING_STORE_IDS", "").split(",") if s.strip()]
    if _env_stores:
        active_stores = _env_stores
    else:
        try:
            import asyncpg as _apg
            _max_stores = int(os.getenv("COACHING_MAX_STORES", "3"))
            _conn = await _apg.connect(**_RT_DB_CFG, timeout=5)
            _rows = await _conn.fetch("""
                SELECT b.store_id
                FROM sales.boutiques b
                JOIN (SELECT store_id, SUM(lig_ttc) AS ca
                      FROM sales.transactions
                      WHERE date_only >= CURRENT_DATE - 7
                      GROUP BY store_id) t ON t.store_id = b.store_id
                WHERE b.active
                ORDER BY t.ca DESC
                LIMIT $1
            """, _max_stores)
            await _conn.close()
            if _rows:
                active_stores = [r["store_id"] for r in _rows]
        except Exception as e:
            logger.warning("⚠️ Top magasins DB indisponible (%s) — fallback %s", e, DEFAULT_STORE_ID)

    trigger = CronTrigger(
        orchestrator=orchestrator,
        store_ids=active_stores,
        interval_minutes=15,
    )
    set_orchestrator(orchestrator, trigger)
    app.state.trigger = trigger
    app.state.orchestrator = orchestrator

    # ── Déclenchement événementiel : alerte stock critique → cycle immédiat ──
    from app.sales.orchestration.alert_trigger import AlertCycleTrigger
    alert_trigger = AlertCycleTrigger(orchestrator=orchestrator)
    app.state.alert_trigger = alert_trigger

    async def _delayed_trigger_start():
        logger.info("⏳ CronTrigger démarrera après stabilisation des données...")
        await asyncio.sleep(10)
        trigger.start()
        alert_trigger.start()
        logger.info("✅ CronTrigger (%d magasins) + AlertCycleTrigger démarrés", len(active_stores))

    asyncio.create_task(_delayed_trigger_start())
    logger.info("✅ Orchestration préparée")

    # ── TimesFM/Prophet preload (S5.5) — évite la latence au premier appel ─
    async def _preload_timesfm():
        try:
            from app.sales.mcp.timefm.tools import TimesFMTools
            _tfm = TimesFMTools()
            await _tfm.load_model()
            # Cache l'instance sur app.state pour réutilisation dans les routes
            app.state.timefm_tools = _tfm
            logger.info("✅ TimesFM/Prophet préchargé")
        except Exception as e:
            logger.warning(f"⚠️ TimesFM preload: {e}")

    asyncio.create_task(_preload_timesfm())

    # ── Scraper événements (FIC, offres Ooredoo) → market.events, quotidien ──
    try:
        from app.sales.coaching.agents.stratege.events_scraper import refresh_events_loop
        asyncio.create_task(refresh_events_loop())
        logger.info("✅ Events scraper programmé (rafraîchissement quotidien)")
    except Exception as e:
        logger.warning(f"⚠️ Events scraper non démarré: {e}")

    # ── Kanban : auto-confirmation des BC SOUMIS > 24h sans action humaine ──
    try:
        from app.inventory.services.po_auto_confirm import auto_confirm_loop
        asyncio.create_task(auto_confirm_loop())
        logger.info("✅ Auto-confirmation BC programmée (SOUMIS -> CONFIRME après 24h)")
    except Exception as e:
        logger.warning(f"⚠️ Auto-confirmation BC non démarrée: {e}")

    logger.info("🚀 All systems started — v5.0.0")


@app.on_event("shutdown")
async def shutdown_event():
    simulator = getattr(app.state, "simulator", None)
    if simulator: simulator.stop()
    trigger = getattr(app.state, "trigger", None)
    if trigger: trigger.stop()
    alert_trigger = getattr(app.state, "alert_trigger", None)
    if alert_trigger: alert_trigger.stop()

    from app.core.db import close_async_pool
    await close_async_pool()

    logger.info("Shutting down cleanly.")


def _fetch_weather_fallback() -> dict:
    global _weather_cache, _weather_cache_time
    now = time.time()
    if _weather_cache and (now - _weather_cache_time) < _WEATHER_CACHE_TTL:
        return _weather_cache
    try:
        import httpx
        r = httpx.get(
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
            "weather_icon": ICONS.get(code,"⛅"),
            "weather_label": LABELS.get(code,"Variable"),
            "weather_effect": effect,
            "temperature": temp,
            "is_rainy": code >= 61,
        }
        _weather_cache, _weather_cache_time = result, now
        return result
    except Exception as e:
        logger.warning(f"[WEATHER] {e}")
        return {"weather_icon":"🌤️","weather_label":"Tunis Lac",
                "weather_effect":0.0,"temperature":22,"is_rainy":False}


def _extract_summary(raw, gap_pct, urgency, cr, dt, feo):
    if not raw:
        return _make_fallback_summary(gap_pct, urgency, cr, dt, feo)
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            s = json.loads(raw).get("analyst_summary","")
            if s: return s.strip()
        except Exception:
            import re
            m = re.search(r'"analyst_summary"\s*:\s*"([^"]+)"', raw)
            if m: return m.group(1).strip()
    return raw[:400] if not raw.startswith("{") else _make_fallback_summary(gap_pct, urgency, cr, dt, feo)


def _make_fallback_summary(gap_pct, urgency, cr, dt, feo):
    if urgency in ("CRITICAL","HIGH"):
        return f"Gap critique {gap_pct:.1f}%. CA {cr:,.0f}/{dt:,.0f} TND. Forecast {feo:,.0f} TND — action immédiate."
    if urgency == "MEDIUM":
        return f"Gap {gap_pct:.1f}% à surveiller. CA {cr:,.0f}/{dt:,.0f} TND. Forecast EOD: {feo:,.0f} TND."
    return f"Performance correcte — gap {gap_pct:.1f}%. CA {cr:,.0f}/{dt:,.0f} TND."


def _compute_heatmap(urgency):
    HIGH   = (["med","med","high","high","crit","crit","high","med"],["low","med","med","high","high","crit","crit","high"])
    MEDIUM = (["low","med","med","high","high","med","med","low"],["low","low","med","med","high","med","med","low"])
    LOW    = (["low","low","med","med","med","low","low","low"],["low","low","low","med","med","low","low","low"])
    traffic, risk = {"CRITICAL":HIGH,"HIGH":HIGH,"MEDIUM":MEDIUM}.get(urgency, LOW)
    return {
        "hours":   ["11AM","12PM","1PM","2PM","3PM","4PM","5PM","6PM"],
        "traffic": traffic,
        "weather": ["low","low","low","med","med","high","high","high"],
        "stock":   ["low","low","med","high","high","crit","crit","crit"],
        "event":   ["low","low","low","low","low","med","high","high"],
        "risk":    risk,
    }


async def _get_advisors_from_pg(store_id: str, cr: float, dt: float) -> list:
    import asyncpg
    try:
        conn = await asyncpg.connect(
            host="localhost", port=5432,
            database="ooredoo_sales", user="postgres", password="admin",
            timeout=5,
        )
        try:
            rows = await conn.fetch("""
                SELECT
                    t.agent_id,
                    COALESCE(a.agent_name, t.agent_id::text) AS full_name,
                    SUM(t.lig_ttc)  AS revenue_today,
                    COUNT(*)        AS nb_ventes
                FROM sales.transactions t
                LEFT JOIN sales.agents a
                       ON a.agent_id = t.agent_id AND a.store_id = $1
                WHERE t.store_id = $1
                  AND t.date_only = (SELECT MAX(date_only) FROM sales.transactions WHERE store_id = $1)
                  AND t.lig_ttc > 0
                GROUP BY t.agent_id, full_name
                ORDER BY revenue_today DESC
            """, store_id)
        finally:
            await conn.close()

        sellers = [
            {
                "name":         str(r["full_name"]).title(),
                "revenue_today": float(r["revenue_today"] or 0),
                "nb_ventes":    int(r["nb_ventes"] or 0),
                "agent_id":     str(r["agent_id"]),
            }
            for r in rows
        ]

        if not sellers or all(s["revenue_today"] == 0 for s in sellers):
            conn2 = await asyncpg.connect(
                host="localhost", port=5432,
                database="ooredoo_sales", user="postgres", password="admin",
                timeout=5,
            )
            try:
                hist = await conn2.fetch("""
                    SELECT
                        t.agent_id,
                        COALESCE(a.agent_name, t.agent_id::text) AS full_name,
                        SUM(t.lig_ttc) AS revenue_total
                    FROM sales.transactions t
                    LEFT JOIN sales.agents a
                           ON a.agent_id = t.agent_id AND a.store_id = $1
                    WHERE t.store_id = $1 AND t.lig_ttc > 0
                    GROUP BY t.agent_id, full_name
                    ORDER BY revenue_total DESC
                    LIMIT 10
                """, store_id)
            finally:
                await conn2.close()

            total_hist = sum(float(r["revenue_total"]) for r in hist) or 1
            sellers = [
                {
                    "name":         str(r["full_name"]).title(),
                    "revenue_today": round(cr * float(r["revenue_total"]) / total_hist, 2) if cr > 0 else 0,
                    "nb_ventes":    max(1, round(cr * float(r["revenue_total"]) / total_hist / 80)) if cr > 0 else 0,
                    "agent_id":     str(r["agent_id"]),
                }
                for r in hist
            ]
        return sellers
    except Exception as e:
        logger.warning(f"[ADVISORS PG] {e}")
        return []


async def _run_agents(store_id: str, cycle: int) -> dict:
    import uuid
    cycle_id = f"cycle-{uuid.uuid4().hex[:8]}"
    started  = datetime.utcnow()

    logger.info("[CYCLE #%d] AGENT ANALYSTE @ %s | %s", cycle, datetime.now().strftime("%H:%M:%S"), store_id)

    provider = get_data_provider()
    try:
        pos_data    = await provider.fetch_pos_data(store_id)
        pos_history = await provider.fetch_pos_history(store_id)
        prediction  = await provider.fetch_timesfm_prediction(store_id)
        logger.info(
            f"[PG] CA={pos_data.get('current_revenue',0):.0f} TND | "
            f"TX={pos_data.get('nb_transactions_today',0)} | "
            f"EOD={prediction.get('forecast_end_of_day',0):.0f} TND | "
            f"DATE={pos_data.get('business_date','?')}"
        )
    except Exception as e:
        logger.warning(f"[PG] _run_agents fallback: {e}")
        pos_data    = {"store_id":store_id,"daily_target":1007,"current_revenue":0,
                       "nb_transactions_today":0,"data_status":"unavailable"}
        pos_history = []
        prediction  = {"forecast_end_of_day":0,"mape":99,"source":"fallback"}

    cr     = pos_data.get("current_revenue", 0) or 0
    dt_val = pos_data.get("daily_target", 1007) or 1007
    feo    = prediction.get("forecast_end_of_day", 0) or 0
    gap_amt = max(0, dt_val - cr)
    gap_pct = round((gap_amt / dt_val * 100) if dt_val > 0 else 0, 1)
    att     = round((cr / dt_val) * 100, 1) if dt_val > 0 else 0
    hour    = datetime.now().hour
    hrs_rem = max(0, 20 - hour)
    cov     = 0.0 if pos_data.get("data_status") == "unavailable" else 100.0

    if gap_amt > 0 and pos_data.get("data_status") != "unavailable":
        cov = round(min(100.0, ((feo - cr) / gap_amt) * 100), 1)

    if pos_data.get("data_status") == "unavailable":
        urg_level = "CRITICAL"; urg_score = 1.0
    else:
        gap_score   = min(1.0, gap_pct / 50.0)
        time_press  = min(1.0, max(0.0, (hour - 8) / 10))
        cov_penalty = max(0.0, (100 - cov) / 100) * 0.3
        urg_score   = round(min(1.0, gap_score * 0.5 + time_press * 0.3 + cov_penalty), 3)
        urg_level   = "HIGH" if (gap_pct > 30 and cov < 80) else "MEDIUM" if gap_pct > 15 else "LOW"
        if hrs_rem < 2 and gap_pct > 10:
            urg_level = "HIGH"; urg_score = max(urg_score, 0.85)

    analyst_summary = ""
    supervisor_result: dict = {}
    try:
        from app.sales.orchestration.supervisor_agent import get_supervisor_graph
        graph = get_supervisor_graph()
        supervisor_result = await asyncio.wait_for(
            graph.ainvoke({
                "cycle_id":       cycle_id,
                "store_id":       store_id,
                "trigger_type":   "dashboard_loop",
                "agents_invoked": [],
                "errors":         [],
                "metrics":        {"cycle_id": cycle_id, "store_id": store_id},
            }),
            # sales_branch (analyste + stratège) est séquentiel dans CycleOrchestrator
            # (le stratège consomme la requête RAG construite par l'analyste — vraie
            # dépendance de données, pas juste historique) et varie 43-108s selon la
            # latence RAG/OpenRouter observée. 150s laisse une marge réaliste ; en cas
            # de dépassement le fallback heuristique (PG seul) prend le relais proprement.
            timeout=150.0,
        ) or {}
        raw             = supervisor_result.get("analyst_summary", "")
        analyst_summary = (
            _extract_summary(raw, gap_pct, urg_level, cr, dt_val, feo) if raw
            else _make_fallback_summary(gap_pct, urg_level, cr, dt_val, feo)
        )
        urg_level = supervisor_result.get("urgency_level", urg_level)
        urg_score = supervisor_result.get("urgency_score", urg_score)
        gap_pct   = round(float(supervisor_result.get("gap_objectif", gap_pct) or gap_pct), 1)
        gap_amt   = supervisor_result.get("gap_amount", gap_amt)
        feo       = supervisor_result.get("forecast_eod", feo)
        cov       = supervisor_result.get("coverage", cov)
        att       = supervisor_result.get("attainment", att)
        logger.info(
            "[SUPERVISOR] agents=%s guardrail=%s summary: %s",
            supervisor_result.get("agents_invoked", []),
            supervisor_result.get("guardrail_status", "APPROVE"),
            analyst_summary[:100],
        )
    except (Exception, asyncio.CancelledError, asyncio.TimeoutError) as e:
        analyst_summary = _make_fallback_summary(gap_pct, urg_level, cr, dt_val, feo)
        logger.warning("[SUPERVISOR] Fallback: %s", str(e)[:120])

    analyst_output = {
        "pos_data": pos_data, "pos_history": pos_history, "prediction": prediction,
        "urgency_level": urg_level, "urgency_score": urg_score,
        "gap_objectif": gap_pct, "gap_pct": gap_pct, "gap_amount": gap_amt,
        "analyst_summary": analyst_summary, "current_revenue": cr,
        "daily_target": dt_val, "forecast_eod": feo, "attainment": att,
        "coverage": cov, "mape": prediction.get("mape", 14.3),
        "timesfm_prediction": prediction, "feedback_history": [],
        "metrics": {"cycle_id": cycle_id, "store_id": store_id},
    }

    logger.info("[CYCLE #%d] STRATEGE + INVENTORY + GUARDRAIL (SupervisorAgent)", cycle)
    stratege_output = {
        "strategie":          supervisor_result.get("strategie", ""),
        "strategie_actions":  supervisor_result.get("strategie_actions", []),
        "cause_racine":       supervisor_result.get("cause_racine", f"Gap {gap_pct:.1f}%"),
        "context_heatmap":    supervisor_result.get("context_heatmap", {}),
        "context_signals":    supervisor_result.get("context_signals", []),
        "external_context":   supervisor_result.get("external_context", {}),
        "message_manager":    supervisor_result.get("message_manager", ""),
        "focus_produits":     supervisor_result.get("focus_produits", []),
        "rag_used":           supervisor_result.get("rag_used", False),
        "nb_rag_scripts":     supervisor_result.get("nb_rag_scripts", 0),
        "real_time_alerts":   supervisor_result.get("real_time_alerts", []),
    }

    # Enrichissement dashboard — sorties du raisonnement unifié multi-domaine
    # (scoring cross-domaine, décisions stock, guardrail, HITL)
    supervisor_enrichment = {
        "guardrail_status":      supervisor_result.get("guardrail_status", "APPROVE"),
        "guardrail_issues":      supervisor_result.get("guardrail_issues", []),
        "scored_products":       supervisor_result.get("scored_products", [])[:5],
        "coach_recommendation":  supervisor_result.get("coach_recommendation", {}),
        "inventory_decisions":   supervisor_result.get("inventory_decisions", [])[:10],
        "critical_stock_alerts": supervisor_result.get("critical_stock_alerts", [])[:5],
        "hitl_required":         supervisor_result.get("hitl_required", False),
        "agents_invoked":        supervisor_result.get("agents_invoked", []),
    }

    try:
        from app.core.agent_logger import log_cycle
        total_ms = (datetime.utcnow() - started).total_seconds() * 1000
        log_cycle(
            cycle_id=cycle_id, state={**analyst_output, **stratege_output, **supervisor_enrichment},
            total_ms=total_ms, triggered_by="websocket", store_id=store_id,
            nodes_executed=len(supervisor_enrichment.get("agents_invoked", [])) or 2, errors_count=0,
            rag_used=stratege_output.get("rag_used",False),
            nb_rag_scripts=stratege_output.get("nb_rag_scripts",0),
        )
    except Exception:
        pass
    # ── Trace Langfuse ────────────────────────────────────────────────────────
    try:
        from app.core.langfuse import trace_full_cycle
        total_ms = (datetime.utcnow() - started).total_seconds() * 1000
        trace_full_cycle(
            cycle_id        = cycle_id,
            store_id        = store_id,
            urgency         = urg_level,
            gap_pct         = gap_pct,
            analyst_summary = analyst_summary,
            nb_actions      = len(stratege_output.get("strategie_actions", [])),
            rag_used        = stratege_output.get("rag_used", False),
            nb_rag_scripts  = stratege_output.get("nb_rag_scripts", 0),
            total_ms        = total_ms,
        )
    except Exception:
        pass
    return {**analyst_output, **stratege_output, **supervisor_enrichment}


def _build_payload(analysis: dict) -> dict:
    pos_data    = analysis.get("pos_data") or {}
    pos_history = analysis.get("pos_history") or []
    prediction  = analysis.get("prediction") or {}

    urgency_level   = analysis.get("urgency_level") or "LOW"
    urgency_score   = analysis.get("urgency_score") or 0
    gap_pct         = analysis.get("gap_pct") or 0
    gap_amount      = analysis.get("gap_amount") or 0
    analyst_summary = analysis.get("analyst_summary") or ""
    cr     = analysis.get("current_revenue") or 0
    dt_val = analysis.get("daily_target") or 1007
    feo    = analysis.get("forecast_eod") or 0
    att    = analysis.get("attainment") or 0

    strategie         = analysis.get("strategie") or ""
    strategie_actions = analysis.get("strategie_actions") or []
    cause_racine      = analysis.get("cause_racine") or ""
    context_heatmap   = analysis.get("context_heatmap") or {}
    context_signals   = analysis.get("context_signals") or []
    external_ctx      = analysis.get("external_context") or {}
    message_manager   = analysis.get("message_manager") or ""
    focus_produits    = analysis.get("focus_produits") or []

    weather_sum = external_ctx.get("summary") or {}
    holidays    = external_ctx.get("holidays") or {}
    events_data = external_ctx.get("events") or {}

    if not weather_sum or not weather_sum.get("weather_icon"):
        weather_sum = _fetch_weather_fallback()

    weather_str  = f"{weather_sum.get('weather_icon','🌤️')} {weather_sum.get('weather_label','Tunis')}".strip()
    next_holiday = holidays.get("next_holiday") or {}
    event_str    = ""

    # Priorité : événement marché EN COURS (festival partenaire Ooredoo…),
    # puis événement imminent, puis jour férié — c'est ce qui impacte les
    # ventes/le stock aujourd'hui.
    events_en_cours = external_ctx.get("events_en_cours") or []
    events_a_venir  = external_ctx.get("events_a_venir") or []
    if events_en_cours:
        event_str = f"🎪 {events_en_cours[0]['nom']} (en cours)"
    elif events_a_venir and events_a_venir[0].get("jours_avant", 99) <= 14:
        ev = events_a_venir[0]
        event_str = f"🎪 {ev['nom']} dans {ev['jours_avant']}j"
    elif holidays.get("is_holiday_today"):
        event_str = f"🎉 {(holidays.get('today_holiday') or {}).get('name','Jour férié')}"
    elif next_holiday.get("name"):
        event_str = f"📅 {next_holiday['name']} dans {next_holiday.get('days_until',0)}j"

    all_promos = (events_data.get("promotions") or []) + (events_data.get("new_offers") or [])
    promo_str  = f"🎯 {len(all_promos)} offre(s) Ooredoo" if all_promos else ""

    # La modal "Offres Ooredoo actives" du dashboard lit store_context.active_offers
    # (title/price/category/details/script) — n'envoyer que le compteur laissait
    # la liste vide alors que le badge annonçait N offres.
    active_offers = [
        {
            "title":    o.get("title", ""),
            "price":    o.get("price", ""),
            "category": o.get("category", ""),
            "details":  o.get("details", ""),
            "script":   (
                f"Mentionnez « {o.get('title','')} »"
                + (f" à {o['price']}" if o.get("price") else "")
                + " — vérifiez l'éligibilité du client et proposez le bundle associé."
            ),
        }
        for o in all_promos[:8] if o.get("title")
    ]

    store_context = {
        "weather":       weather_str,
        "event":         event_str,
        "promo":         promo_str,
        "active_offers": active_offers,
        "events_en_cours": [
            {"nom": e["nom"], "fin": e.get("fin",""), "intensite": e.get("intensite","")}
            for e in events_en_cours[:3]
        ],
        "events_a_venir": [
            {"nom": e["nom"], "debut": e.get("debut",""), "jours_avant": e.get("jours_avant",0)}
            for e in events_a_venir[:3]
        ],
        "stock_alert":   "📦 Stock critique — vérifier boutique",
        "temperature":   f"{weather_sum.get('temperature',22)}°C",
    }

    hour          = datetime.now().hour
    hours_elapsed = max(1, hour - 9)
    nb_tx         = pos_data.get("nb_transactions_today",0) or 0
    visitors_h    = max(10, round(nb_tx / hours_elapsed * random.uniform(0.9,1.2)))

    sellers    = analysis.get("_sellers_cache") or pos_data.get("sellers") or []
    per_seller = round(dt_val / max(len(sellers),1))
    max_rev    = max((s.get("revenue_today",0) for s in sellers), default=1)

    advisors = sorted([
        {
            "id":         s.get("name","").replace(" ","_").lower(),
            "name":       s.get("name",""),
            "revenue":    round(s.get("revenue_today",0)),
            "target":     per_seller,
            "attainment": round(s.get("revenue_today",0) / max(per_seller,1) * 100),
            "nb_ventes":  s.get("nb_ventes",0),
            "status": (
                "Top"    if s.get("revenue_today",0) == max_rev
                else "OK"     if s.get("revenue_today",0) / max(per_seller,1) >= 0.5
                else "Urgent"
            ),
            "trend": "up" if s.get("revenue_today",0) >= per_seller * 0.7 else "down",
        }
        for s in sellers
    ], key=lambda x: -x["revenue"])

    for i, a in enumerate(advisors):
        a["rank"] = i + 1

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
        for i, a in enumerate(advisors)
        if a["attainment"] < 80
    ]

    tph         = round(dt_val / 11)
    hourly_rate = cr / hours_elapsed
    hd: dict[int, float] = {}
    for tx in pos_history:
        t = tx.get("transaction_time")
        if t and hasattr(t, "hour"):
            hd[t.hour] = hd.get(t.hour,0.0) + tx.get("revenue",0.0)

    hourly_performance = []
    for h in range(9, min(hour+1, 21)):
        rev   = max(0, hd.get(h,0.0))
        label = "12PM" if h==12 else f"{h}AM" if h<12 else f"{h-12}PM"
        hourly_performance.append({
            "hour":label,"revenue":round(rev),"actual":round(rev),"target":tph,
            "forecast":round(rev) if rev>0 else round(max(0,hourly_rate)*random.uniform(0.85,1.10)),
            "risk":rev>0 and rev<tph*0.85,
        })
    for h in range(hour+1, 21):
        label = "12PM" if h==12 else f"{h}AM" if h<12 else f"{h-12}PM"
        mult  = random.uniform(1.10,1.30) if h in [12,13,17,18] else random.uniform(0.60,0.80) if h in [9,10,19,20] else random.uniform(0.90,1.10)
        hourly_performance.append({"hour":label,"revenue":0,"actual":0,"target":tph,"forecast":round(tph*mult),"risk":False})

    risk_hours = [
        {"hour":h["hour"],"target_pct":round((h["revenue"]/tph)*100),"units_behind":round((h["revenue"]-tph)/150)}
        for h in hourly_performance
        if tph>0 and h["revenue"]>0 and (h["revenue"]/tph)*100<85
    ]

    by_cat: dict[str,float] = {}
    for tx in pos_history:
        cat = tx.get("product_category","Autre")
        by_cat[cat] = by_cat.get(cat,0) + tx.get("revenue",0)

    product_mix = [
        {"product":cat,"revenue":round(rev),
         "attainment":round(rev/max(dt_val/max(len(by_cat),1),1)*100),
         "stock_level":"Low" if "Smartphone" in cat else "OK","forecast":round(rev*1.10)}
        for cat, rev in sorted(by_cat.items(), key=lambda x: -x[1])
    ]

    final_heatmap = context_heatmap if context_heatmap and context_heatmap.get("traffic") else _compute_heatmap(urgency_level)
    w_eff   = weather_sum.get("weather_effect",0)
    w_level = "high" if w_eff<=-0.15 else "med" if w_eff<0 else "low"

    final_signals = context_signals or [
        {"type":"weather","label":f"{weather_sum.get('weather_icon','⛅')} {weather_sum.get('weather_label','Tunis')} — {weather_sum.get('temperature',22)}°C","level":w_level,"value":w_eff},
    ]
    if event_str and not context_signals:
        final_signals.append({"type":"holiday","label":event_str,"level":"med","value":0.5})
    if promo_str and not context_signals:
        final_signals.append({"type":"event","label":promo_str,"level":"low","value":0.2})

    _stock_health = {"critical":0,"total":0,"optimal":0}
    try:
        from app.inventory.tools.internal.stock_tools import _DataCache
        _overrides = getattr(_DataCache,"_stock_overrides",{})
        _critical  = sum(1 for v in _overrides.values() if float(v or 0)<3)
        _total     = len(_overrides)
        _stock_health = {"critical":_critical,"total":_total,"optimal":_total-_critical}
    except Exception:
        pass

    return {
        "type":               "metrics_update",
        "timestamp":          datetime.now().isoformat(),
        "ca_today":           cr,
        "stock_health":       _stock_health,
        "ca_target":          dt_val,
        "attainment":         att,
        "visitors_h":         visitors_h,
        "agents_live":        4,
        "niveau_urgence":     urgency_level,
        "urgency_score":      urgency_score,
        "ecart_objectif":     gap_pct,
        "gap_amount":         gap_amount,
        "analyst_summary":    analyst_summary,
        "route_to":           "strategie" if urgency_level in ("CRITICAL","HIGH","MEDIUM") else "coach",
        "forecast_eod":       feo,
        "forecast_ci_low":    (prediction.get("confidence_interval") or {}).get("low",0),
        "forecast_ci_high":   (prediction.get("confidence_interval") or {}).get("high",0),
        "forecast_mape":      analysis.get("mape",14.3),
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
        "hourly_performance": hourly_performance,
        "risk_hours":         risk_hours,
        "product_mix":        product_mix,
        "advisor_priorities": [
            {
                "advisor_id": a["id"], "name": a["name"], "performance": a["attainment"],
                "priority":   "TOP_CLOSE" if a["attainment"]>=80 else "STABLE" if a["attainment"]>=50 else "AT_RISK",
                "reason":     f"{a['nb_ventes']} ventes · {a['revenue']:,} DT",
                "action":     f"Gap {100-a['attainment']}% à combler" if a["attainment"]<80 else "Maintenir le rythme",
            }
            for a in advisors
        ],
        "rag_used":               analysis.get("rag_used",False),
        "nb_rag_scripts":         analysis.get("nb_rag_scripts",0),
        "real_time_alerts":       analysis.get("real_time_alerts",[]),
        "ca_yesterday_same_hour": cr * 0.88,
        "last_cycle_id":          f"cycle_{datetime.now().strftime('%H%M%S')}",
        "guardrail_status":       analysis.get("guardrail_status","APPROVE"),
        "guardrail_issues":       analysis.get("guardrail_issues",[]),
        "scored_products":        analysis.get("scored_products",[]),
        "coach_recommendation":   analysis.get("coach_recommendation",{}),
        "inventory_decisions":    analysis.get("inventory_decisions",[]),
        "critical_stock_alerts":  analysis.get("critical_stock_alerts",[]),
        "hitl_required":          analysis.get("hitl_required",False),
        "agents_invoked":         analysis.get("agents_invoked",[]),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Agent loop — tourne en tâche de fond pour un store
# Séparé du handler WS → les reconnexions n'interrompent pas le cycle
# ══════════════════════════════════════════════════════════════════════════════

async def _broadcast_inventory_alerts(frontend_id: str, store_id: str) -> None:
    """
    S7.5 — Fetch critical stock items from inventory.stock_levels and
    broadcast an 'inventory_alerts' WS event to the frontend.
    Runs as fire-and-forget task after each agent cycle.
    """
    try:
        alerts = await asyncio.get_event_loop().run_in_executor(
            None, _fetch_critical_stock_sync, store_id
        )
        if not alerts:
            return
        nb_critical = sum(1 for a in alerts if a.get("severity") == "critical")
        await _broadcast(frontend_id, json.dumps({
            "type":        "inventory_alerts",
            "store_id":    store_id,
            "alerts":      alerts[:10],
            "nb_critical": nb_critical,
            "nb_warning":  len(alerts) - nb_critical,
            "timestamp":   datetime.utcnow().isoformat(),
        }, default=str))
        if nb_critical > 0:
            logger.info("[INV ALERTS] %s — %d critical alerts broadcasté", store_id, nb_critical)
    except Exception as e:
        logger.debug("[INV ALERTS] %s: %s", store_id, e)


def _fetch_critical_stock_sync(store_id: str) -> list:
    """Sync helper: query inventory.stock_levels for SKUs at risk."""
    try:
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(**{
            "host":     os.getenv("POSTGRES_HOST", "localhost"),
            "port":     int(os.getenv("POSTGRES_PORT", 5432)),
            "dbname":   os.getenv("POSTGRES_DB", "ooredoo_sales"),
            "user":     os.getenv("POSTGRES_USER", "postgres"),
            "password": os.getenv("POSTGRES_PASSWORD", "admin"),
            "connect_timeout": 5,
        })
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT sl.sku, p.nom AS product_name,
                       COALESCE(sl.quantity_available, sl.quantity, 0) AS stock_qty,
                       pm.lead_time_days,
                       CASE
                         WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 0  THEN 'critical'
                         WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 3  THEN 'critical'
                         WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 10 THEN 'warning'
                       END AS severity
                FROM inventory.stock_levels sl
                LEFT JOIN sales.produits p ON p.sku = sl.sku
                LEFT JOIN inventory.products pm ON pm.sku = sl.sku
                WHERE sl.store_id = %s
                  AND COALESCE(sl.quantity_available, sl.quantity, 0) <= 10
                ORDER BY COALESCE(sl.quantity_available, sl.quantity, 0) ASC
                LIMIT 20
            """, (store_id,))
            rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


async def _agent_loop(mapped_id: str, frontend_id: str) -> None:
    """
    Boucle agent indépendante des connexions WS.
    Démarre une seule fois par store et tourne tant qu'il y a des connexions.
    Résultats broadcastés à toutes les connexions actives via _broadcast().
    """
    cycle = 0
    logger.info(f"[AGENT LOOP] Démarré pour {mapped_id}")

    # ── Payload initial rapide depuis PG ──────────────────────────────────────
    try:
        provider    = get_data_provider()
        pos_data    = await provider.fetch_pos_data(mapped_id)
        pos_history = await provider.fetch_pos_history(mapped_id)
        prediction  = await provider.fetch_timesfm_prediction(mapped_id)
        sellers     = await _get_advisors_from_pg(
            mapped_id,
            pos_data.get("current_revenue",0),
            pos_data.get("daily_target",1007),
        )
        cr  = pos_data.get("current_revenue",0) or 0
        dt_ = pos_data.get("daily_target",1007) or 1007
        ga  = max(0, dt_ - cr)
        gp  = round((ga/dt_*100) if dt_>0 else 0, 1)
        feo = prediction.get("forecast_end_of_day",0) or 0
        ul  = "HIGH" if gp>30 else "MEDIUM" if gp>15 else "LOW"

        # ── Réutiliser le dernier cycle réel connu (CronTrigger) si dispo ────────
        # Évite d'écraser une "Prochaine meilleure action" déjà calculée par un
        # bootstrap vide à chaque (re)démarrage de _agent_loop — le frontend
        # affichait "vide" pendant tout le premier cycle (jusqu'à ~2min) alors
        # que des strategie_actions récentes existaient déjà côté serveur.
        _trigger = getattr(app.state, "trigger", None)
        _last    = (_trigger.last_result if _trigger else None) or {}
        _reuse   = _last.get("store_id") == mapped_id and bool(_last.get("strategie_actions"))

        initial_payload = _build_payload({
            "pos_data":pos_data,"pos_history":pos_history,"prediction":prediction,
            "urgency_level":ul,"urgency_score":round(min(1.0,gp/60),3),
            "gap_pct":gp,"gap_amount":ga,
            "analyst_summary": _last.get("analyst_summary") or
                f"Gap {gp:.1f}% — CA {cr:,.0f}/{dt_:,.0f} TND. Analyse en cours...",
            "current_revenue":cr,"daily_target":dt_,"forecast_eod":feo,
            "attainment":round((cr/dt_)*100,1) if dt_>0 else 0,
            "coverage":100.0,"mape":14.3,
            "strategie":         _last.get("strategie","")          if _reuse else "",
            "strategie_actions": _last.get("strategie_actions",[])  if _reuse else [],
            "cause_racine":      _last.get("cause_racine","")       if _reuse else "",
            "context_heatmap":   _last.get("context_heatmap",{})    if _reuse else {},
            "context_signals":   _last.get("context_signals",[])    if _reuse else [],
            "external_context":  _last.get("external_context",{})   if _reuse else {},
            "message_manager":   _last.get("message_manager","")    if _reuse else "",
            "focus_produits":    _last.get("focus_produits",[])     if _reuse else [],
            "timesfm_prediction":prediction,
            "feedback_history":[],"_sellers_cache":sellers,
        })
        await _broadcast(frontend_id, json.dumps(initial_payload, default=str))
        logger.info(f"[AGENT LOOP] Payload initial broadcasté pour {mapped_id} (reuse_last_cycle={_reuse})")
    except Exception as e:
        logger.warning(f"[AGENT LOOP] Payload initial échoué: {e}")

    # ── Boucle principale ─────────────────────────────────────────────────────
    while _store_connections.get(frontend_id):
        cycle += 1

        # Signaler que l'analyse est en cours
        await _broadcast(frontend_id, json.dumps({
            "type":"processing","message":"Analyse en cours...",
            "timestamp":datetime.now().isoformat(),
        }))

        try:
            analysis = await _run_agents(mapped_id, cycle)
            sellers  = await _get_advisors_from_pg(
                mapped_id,
                analysis.get("current_revenue",0),
                analysis.get("daily_target",1007),
            )
            analysis["_sellers_cache"] = sellers
            payload = _build_payload(analysis)
            await _broadcast(frontend_id, json.dumps(payload, default=str))
            nb_bytes = len(json.dumps(payload, default=str))
            logger.info("[CYCLE #%d] Payload %s bytes -> %s", cycle, f"{nb_bytes:,}", frontend_id)

            # S7.5 — Broadcast inventory alerts séparé si SKUs critiques ───────
            asyncio.create_task(_broadcast_inventory_alerts(frontend_id, mapped_id))
        except Exception as e:
            logger.error(f"[AGENT LOOP] Cycle #{cycle} erreur: {e}")

        # Attendre 2 minutes entre cycles
        for _ in range(24):   # 24 × 5s = 120s
            if not _store_connections.get(frontend_id):
                break
            await asyncio.sleep(5)

    _agent_running[frontend_id] = False
    logger.info(f"[AGENT LOOP] Arrêté pour {mapped_id} (plus de connexions)")


# ══════════════════════════════════════════════════════════════════════════════
# WebSocket handler — connexion légère, renvoie le cache puis attend
# ══════════════════════════════════════════════════════════════════════════════

@app.websocket("/ws/store/{store_id}")
async def ws_store(websocket: WebSocket, store_id: str):
    await websocket.accept()

    mapped_id = map_store_id(store_id)
    logger.info("[WS] Frontend connecte -> %s (mapped=%s)", store_id, mapped_id)

    # Enregistrer la connexion
    if store_id not in _store_connections:
        _store_connections[store_id] = set()
    _store_connections[store_id].add(websocket)

    try:
        # ── Renvoi immédiat du dernier payload connu ──────────────────────────
        # Le client voit les données instantanément sans attendre un nouveau cycle
        if store_id in _last_payload:
            await _safe_send(websocket, _last_payload[store_id])
            logger.info(f"[WS] Cache renvoyé immédiatement → {store_id}")
        else:
            # Première connexion : envoyer un payload minimal
            await _safe_send(websocket, json.dumps({
                "type":"processing","message":"Connexion établie — chargement des données...",
                "timestamp":datetime.now().isoformat(),
            }))

        # ── Démarrer la boucle agent si pas déjà active ───────────────────────
        if not _agent_running.get(store_id, False):
            _agent_running[store_id] = True
            asyncio.create_task(_agent_loop(mapped_id, store_id))
            logger.info(f"[WS] Agent loop démarrée pour {store_id}")
        else:
            logger.info(f"[WS] Agent loop déjà active pour {store_id} — connexion ajoutée")

        # ── Heartbeat ─────────────────────────────────────────────────────────
        while True:
            await asyncio.sleep(30)
            ok = await _safe_send(websocket, json.dumps({
                "type":"ping","timestamp":datetime.now().isoformat(),
            }))
            if not ok:
                break

    except WebSocketDisconnect:
        logger.info("[WS] Deconnecte : %s", store_id)
    except Exception as e:
        logger.error(f"[WS] Erreur: {e}")
    finally:
        # Retirer proprement la connexion
        if store_id in _store_connections:
            _store_connections[store_id].discard(websocket)
            nb = len(_store_connections[store_id])
            logger.info(f"[WS] {store_id} — {nb} connexion(s) restante(s)")
        logger.info("[WS] Connexion liberee -> %s", store_id)


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


@app.get("/health")
async def health():
    trigger = getattr(app.state,"trigger",None)
    last    = trigger.last_result if trigger else None
    return {
        "status":"ok","version":"4.0.0",
        "modules":["inventory","sales","agents-ia","rag","coach","monitoring"],
        "active_connections": {k: len(v) for k,v in _store_connections.items()},
        "last_cycle": {
            "cycle_id":       last.get("cycle_id") if last else None,
            "niveau_urgence": last.get("niveau_urgence") if last else None,
            "ecart_objectif": last.get("ecart_objectif") if last else None,
            "forecast_eod":   last.get("forecast_eod") if last else None,
        } if last else None,
    }


@app.get("/api/v1/stores/{store_id}/metrics")
async def get_store_metrics(store_id: str):
    mapped_id = map_store_id(store_id)
    provider  = get_data_provider()
    pos_data  = await provider.fetch_pos_data(mapped_id)
    weather   = _fetch_weather_fallback()
    cr = pos_data.get("current_revenue",0) or 0
    dt = pos_data.get("daily_target",1007) or 1007
    return JSONResponse({
        "ca_today":   cr, "ca_target": dt,
        "attainment": round((cr/dt)*100,1) if dt>0 else 0,
        "visitors_h": pos_data.get("nb_transactions_today",0),
        "agents_live": 4,
        "store_context": {
            "weather":     f"{weather['weather_icon']} {weather['weather_label']}",
            "event":"","promo":"",
            "stock_alert": "📦 Stock critique — vérifier boutique",
            "temperature": f"{weather['temperature']}°C",
        },
        "ca_yesterday_same_hour": cr*0.88,
        "source":"postgresql",
    })


@app.get("/api/v1/forecast/eod/{store_id}")
async def get_forecast_eod(store_id: str):
    mapped_id = map_store_id(store_id)
    provider  = get_data_provider()
    pred      = await provider.fetch_timesfm_prediction(mapped_id)
    pos_data  = await provider.fetch_pos_data(mapped_id)
    dt = pos_data.get("daily_target",1007) or 1007
    cr = pos_data.get("current_revenue",0) or 0
    ga = max(0, dt-cr)
    gp = round((ga/dt*100) if dt>0 else 0, 1)
    return JSONResponse({
        "eod":pred.get("forecast_end_of_day",0),"gap_pct":gp,"gap_amount":ga,
        "ci_low":(pred.get("confidence_interval") or {}).get("low",0),
        "ci_high":(pred.get("confidence_interval") or {}).get("high",0),
        "source":"prophet+ratio",
    })


@app.get("/api/v1/stores/{store_id}/advisors")
async def get_advisors(store_id: str):
    mapped_id = map_store_id(store_id)
    provider  = get_data_provider()
    pos_data  = await provider.fetch_pos_data(mapped_id)
    dt = pos_data.get("daily_target",1007) or 1007
    cr = pos_data.get("current_revenue",0) or 0
    sellers = await _get_advisors_from_pg(mapped_id, cr, dt)
    ps  = round(dt/max(len(sellers),1))
    mr  = max((s.get("revenue_today",0) for s in sellers), default=1)
    advisors = sorted([
        {
            "id":         s.get("name","").replace(" ","_").lower(),
            "name":       s.get("name",""),
            "revenue":    round(s.get("revenue_today",0)),
            "target":     ps,
            "attainment": round(s.get("revenue_today",0)/max(ps,1)*100),
            "nb_ventes":  s.get("nb_ventes",0),
            "status":     "Top" if s.get("revenue_today",0)==mr else "OK",
        }
        for s in sellers
    ], key=lambda x: -x["revenue"])
    return JSONResponse({"advisors":advisors,"source":"postgresql"})


@app.get("/api/v1/stores/{store_id}/live-analysis")
async def get_live_analysis(store_id: str):
    mapped_id   = map_store_id(store_id)
    provider    = get_data_provider()
    pos_data    = await provider.fetch_pos_data(mapped_id)
    pos_history = await provider.fetch_pos_history(mapped_id)
    prediction  = await provider.fetch_timesfm_prediction(mapped_id)
    sellers     = await _get_advisors_from_pg(
        mapped_id,
        pos_data.get("current_revenue",0),
        pos_data.get("daily_target",1007),
    )
    cr  = pos_data.get("current_revenue",0) or 0
    dt  = pos_data.get("daily_target",1007) or 1007
    ga  = max(0, dt-cr)
    gp  = round((ga/dt*100) if dt>0 else 0, 1)
    feo = prediction.get("forecast_end_of_day",0) or 0
    ul  = "HIGH" if gp>30 else "MEDIUM" if gp>15 else "LOW"
    return JSONResponse(_build_payload({
        "pos_data":pos_data,"pos_history":pos_history,"prediction":prediction,
        "urgency_level":ul,"urgency_score":round(min(1.0,gp/60),3),
        "gap_pct":gp,"gap_amount":ga,
        "analyst_summary":f"Gap {gp:.1f}% — CA {cr:,.0f}/{dt:,.0f} TND.",
        "current_revenue":cr,"daily_target":dt,"forecast_eod":feo,
        "attainment":round((cr/dt)*100,1) if dt>0 else 0,
        "coverage":100.0,"mape":14.3,
        "strategie":"","strategie_actions":[],"cause_racine":"",
        "context_heatmap":{},"context_signals":[],"external_context":{},
        "message_manager":"","focus_produits":[],"_sellers_cache":sellers,
    }))
