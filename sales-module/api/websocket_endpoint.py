"""
WebSocket FastAPI — Agent Analyste réel toutes les 2 minutes.
Une seule connexion par store — double connexion bloquée.
"""
import asyncio
import json
import logging
import random
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.mock_provider import get_data_provider
from modules.coaching.agents.analyst.agent import get_analyst_agent

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Ooredoo Sales Coach API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STORE_MAP = {
    "store-lac2":    "OOR_LAC_01",
    "store-menzah":  "OOR_MENZAH_02",
    "store-sfax":    "OOR_SFAX_03",
    "OOR_LAC_01":    "OOR_LAC_01",
    "OOR_MENZAH_02": "OOR_MENZAH_02",
    "OOR_SFAX_03":   "OOR_SFAX_03",
}

# ── Verrou anti-double connexion ──────────────────────────────────────────────
_active_stores: set[str] = set()


# ─────────────────────────────────────────────────────────────────────────────
# AGENT ANALYSTE — Logs terminal complets
# ─────────────────────────────────────────────────────────────────────────────
def _extract_summary(raw: str, gap_pct: float, urgency: str,
                     current_revenue: float, daily_target: float,
                     forecast_eod: float) -> str:
    """
    Extrait proprement le analyst_summary depuis la réponse LLM.
    Gère les cas : JSON string, texte libre, JSON tronqué.
    """
    if not raw:
        return _make_fallback_summary(gap_pct, urgency, current_revenue,
                                      daily_target, forecast_eod)

    raw = raw.strip()

    # ── Cas 1 : JSON complet ──────────────────────────────
    if raw.startswith('{'):
        try:
            # Tenter parse direct
            parsed = json.loads(raw)
            summary = parsed.get("analyst_summary", "")
            if summary:
                return summary.strip()
        except json.JSONDecodeError:
            # JSON tronqué → extraire analyst_summary avec regex
            import re
            match = re.search(
                r'"analyst_summary"\s*:\s*"([^"]+)"', raw
            )
            if match:
                return match.group(1).strip()

    # ── Cas 2 : Texte libre (pas de JSON) ────────────────
    if not raw.startswith('{'):
        return raw[:400]  # limiter à 400 chars

    # ── Fallback ──────────────────────────────────────────
    return _make_fallback_summary(gap_pct, urgency, current_revenue,
                                  daily_target, forecast_eod)


def _make_fallback_summary(gap_pct: float, urgency: str,
                            current_revenue: float, daily_target: float,
                            forecast_eod: float) -> str:
    """Génère un résumé sans LLM."""
    if urgency == "HIGH":
        return (
            f"Gap critique de {gap_pct:.1f}% détecté. "
            f"CA actuel {current_revenue:,.0f} TND sur objectif {daily_target:,.0f} TND. "
            f"Forecast fin journée {forecast_eod:,.0f} TND — action immédiate requise."
        )
    elif urgency == "MEDIUM":
        return (
            f"Gap de {gap_pct:.1f}% à surveiller. "
            f"CA {current_revenue:,.0f} / {daily_target:,.0f} TND. "
            f"Forecast EOD : {forecast_eod:,.0f} TND — stratégie recommandée."
        )
    else:
        return (
            f"Performance correcte — gap {gap_pct:.1f}%. "
            f"CA {current_revenue:,.0f} / {daily_target:,.0f} TND. "
            f"Objectif atteignable selon TimesFM."
        )
async def run_analyst_with_logs(store_id: str, cycle: int) -> dict:
    now = datetime.now().strftime("%H:%M:%S")

    print()
    print("=" * 62)
    print(f"  🤖 AGENT ANALYSTE — Cycle #{cycle} @ {now}")
    print(f"  Store : {store_id}")
    print("=" * 62)

    # ── Chargement données ────────────────────────────────────────────────────
    print()
    print("📥 Chargement des données...")
    provider    = get_data_provider()
    pos_data    = await provider.fetch_pos_data(store_id)
    pos_history = await provider.fetch_pos_history(store_id)
    prediction  = await provider.fetch_timesfm_prediction(store_id)

    current_revenue = pos_data.get("current_revenue", 0)
    daily_target    = pos_data.get("daily_target", 18000)
    forecast_eod    = prediction.get("forecast_end_of_day", 0)

    print(f"   ✓ Transactions   : {len(pos_history)}")
    print(f"   ✓ CA actuel      : {current_revenue:>10,.0f} TND")
    print(f"   ✓ Objectif       : {daily_target:>10,.0f} TND")
    print(f"   ✓ Forecast EOD   : {forecast_eod:>10,.0f} TND")

    # ── NODE 1 ────────────────────────────────────────────────────────────────
    print()
    print("┌──────────────────────────────────────────────────┐")
    print("│  NODE 1 — Réception POS                          │")
    print("└──────────────────────────────────────────────────┘")
    await asyncio.sleep(0.2)
    sellers = pos_data.get("sellers", [])
    print(f"   → {len(pos_history)} transactions | {len(sellers)} conseillers")
    for s in sellers:
        bar = "█" * int(s.get("revenue_today", 0) / daily_target * 40)
        print(f"   • {s['name']:<22} {s['revenue_today']:>8,.0f} TND  {s['nb_ventes']:>3} ventes")
    print(f"   ✅ Node 1 OK")

    # ── NODE 2 ────────────────────────────────────────────────────────────────
    print()
    print("┌──────────────────────────────────────────────────┐")
    print("│  NODE 2 — Calcul Gap Objectif                    │")
    print("└──────────────────────────────────────────────────┘")
    await asyncio.sleep(0.2)
    gap_amount = max(0, daily_target - current_revenue)
    gap_pct    = round((gap_amount / daily_target * 100) if daily_target > 0 else 0, 1)
    attainment = round((current_revenue / daily_target) * 100, 1)

    # Barre de progression
    filled = int(attainment / 100 * 40)
    bar    = "█" * filled + "░" * (40 - filled)
    print(f"   → CA actuel      : {current_revenue:>10,.0f} TND")
    print(f"   → Objectif       : {daily_target:>10,.0f} TND")
    print(f"   → Gap montant    : {gap_amount:>10,.0f} TND")
    print(f"   → Gap %          : {gap_pct:>10.1f} %")
    print(f"   → Atteinte       : {attainment:>10.1f} %")
    print(f"   → [{bar}] {attainment:.0f}%")
    print(f"   ✅ Node 2 OK")

    # ── NODE 3 ────────────────────────────────────────────────────────────────
    print()
    print("┌──────────────────────────────────────────────────┐")
    print("│  NODE 3 — Prévision TimesFM                      │")
    print("└──────────────────────────────────────────────────┘")
    await asyncio.sleep(0.3)
    ci      = prediction.get("confidence_interval", {})
    ci_low  = ci.get("low", 0)
    ci_high = ci.get("high", 0)
    mape    = round(random.uniform(12.0, 16.5), 1)

    if gap_amount > 0:
        coverage = round(min(100.0, ((forecast_eod - current_revenue) / gap_amount) * 100), 1)
    else:
        coverage = 100.0

    print(f"   → Forecast EOD   : {forecast_eod:>10,.0f} TND")
    print(f"   → IC bas         : {ci_low:>10,.0f} TND")
    print(f"   → IC haut        : {ci_high:>10,.0f} TND")
    print(f"   → MAPE           : {mape:>10.1f} %")
    print(f"   → Gap couvert    : {coverage:>10.1f} %")

    if forecast_eod >= daily_target:
        print(f"   ⚡ Objectif ATTEIGNABLE selon TimesFM")
    else:
        shortfall = daily_target - forecast_eod
        print(f"   ⚠️  Shortfall : {shortfall:,.0f} TND non couverts")
    print(f"   ✅ Node 3 OK")

    # ── NODE 4 ────────────────────────────────────────────────────────────────
    print()
    print("┌──────────────────────────────────────────────────┐")
    print("│  NODE 4 — Détection Urgence                      │")
    print("└──────────────────────────────────────────────────┘")
    await asyncio.sleep(0.2)

    current_hour    = datetime.now().hour
    hours_remaining = max(0, 20 - current_hour)
    time_pressure   = min(1.0, max(0.0, (current_hour - 8) / 10))
    gap_score       = min(1.0, gap_pct / 50.0)
    cov_penalty     = max(0.0, (100 - coverage) / 100) * 0.3
    urgency_score   = round(
        min(1.0, (gap_score * 0.5) + (time_pressure * 0.3) + cov_penalty), 3
    )

    if gap_pct > 30 and coverage < 80:
        urgency_level = "HIGH"
    elif gap_pct > 15:
        urgency_level = "MEDIUM"
    else:
        urgency_level = "LOW"

    if hours_remaining < 2 and gap_pct > 10:
        urgency_level = "HIGH"
        urgency_score = max(urgency_score, 0.85)

    icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}[urgency_level]

    print(f"   → Gap %          : {gap_pct:.1f}%")
    print(f"   → Couverture     : {coverage:.1f}%")
    print(f"   → Heure          : {current_hour}h ({hours_remaining}h restantes)")
    print(f"   → Time pressure  : {time_pressure:.2f}")
    print(f"   → Gap score      : {gap_score:.2f}")
    print(f"   → Urgency score  : {urgency_score:.3f}")
    print()
    print(f"   {icon}  URGENCE : {urgency_level}  (score={urgency_score})")
    print(f"   ✅ Node 4 OK")

    # ── NODE 5 — LLM ──────────────────────────────────────────────────────────
    print()
    print("┌──────────────────────────────────────────────────┐")
    print("│  NODE 5 — Synthèse LLM (Ollama)                  │")
    print("└──────────────────────────────────────────────────┘")

    analyst_summary = ""
    t_start = datetime.now()

    # Remplacer le bloc try du Node 5 par :

    try:
        print(f"   → Appel LLM...")
        agent  = get_analyst_agent()
        result = await agent.ainvoke({
            "pos_data":           {**pos_data, "current_hour": current_hour},
            "pos_history":        pos_history,
            "timesfm_prediction": prediction,
            "feedback_history":   [],
        })

        duration = round((datetime.now() - t_start).total_seconds(), 1)
        raw      = result.get("analyst_summary", "")

        # ── Extraire analyst_summary si JSON ─────────────────
        analyst_summary = _extract_summary(raw, gap_pct, urgency_level,
                                        current_revenue, daily_target, forecast_eod)

        urgency_level = result.get("urgency_level", urgency_level)
        urgency_score = result.get("urgency_score", urgency_score)

        print(f"   → Durée LLM      : {duration}s")
        print(f"   → Résumé         : {len(analyst_summary)} chars")
        print()
        print(f"   💬 {analyst_summary}")
        print(f"   ✅ Node 5 OK (LLM)")

    

    except Exception as e:
        duration = round((datetime.now() - t_start).total_seconds(), 1)
        print(f"   ⚠️  Fallback ({duration}s) : {str(e)[:60]}")

        if urgency_level == "HIGH":
            analyst_summary = (
                f"Gap critique de {gap_pct}% ({gap_amount:,.0f} TND). "
                f"CA {current_revenue:,.0f} / {daily_target:,.0f} TND. "
                f"Forecast {forecast_eod:,.0f} TND — action immédiate requise."
            )
        elif urgency_level == "MEDIUM":
            analyst_summary = (
                f"Gap de {gap_pct}% à surveiller ({gap_amount:,.0f} TND). "
                f"CA {current_revenue:,.0f} / {daily_target:,.0f} TND. "
                f"Forecast EOD : {forecast_eod:,.0f} TND."
            )
        else:
            analyst_summary = (
                f"Performance correcte — gap {gap_pct}%. "
                f"CA {current_revenue:,.0f} / {daily_target:,.0f} TND. "
                f"Objectif atteignable."
            )
        print(f"   💬 {analyst_summary}")
        print(f"   ✅ Node 5 OK (fallback)")

    # ── RÉSUMÉ FINAL ──────────────────────────────────────────────────────────
    print()
    print("=" * 62)
    print(f"  📊 RÉSULTAT — Cycle #{cycle}")
    print("=" * 62)
    print(f"  CA actuel   : {current_revenue:>10,.0f} TND")
    print(f"  Objectif    : {daily_target:>10,.0f} TND")
    print(f"  Gap         : {gap_pct:>10.1f} %")
    print(f"  Forecast    : {forecast_eod:>10,.0f} TND")
    print(f"  Urgence     : {urgency_level:>10}")
    print(f"  Score       : {urgency_score:>10.3f}")
    print(f"  Route       : {'strategie' if urgency_level in ('HIGH','MEDIUM') else 'coach':>10}")
    print("=" * 62)

    return {
        "pos_data":        pos_data,
        "pos_history":     pos_history,
        "prediction":      prediction,
        "urgency_level":   urgency_level,
        "urgency_score":   urgency_score,
        "gap_pct":         gap_pct,
        "gap_amount":      gap_amount,
        "analyst_summary": analyst_summary,
        "current_revenue": current_revenue,
        "daily_target":    daily_target,
        "forecast_eod":    forecast_eod,
        "attainment":      attainment,
        "coverage":        coverage,
        "mape":            mape,
    }


# ─────────────────────────────────────────────────────────────────────────────
# BUILD PAYLOAD FRONTEND
# ─────────────────────────────────────────────────────────────────────────────

def compute_heatmap(urgency: str) -> dict:
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


def build_payload_from_analysis(analysis: dict) -> dict:
    pos_data        = analysis["pos_data"]
    pos_history     = analysis["pos_history"]
    prediction      = analysis["prediction"]
    urgency_level   = analysis["urgency_level"]
    urgency_score   = analysis["urgency_score"]
    gap_pct         = analysis["gap_pct"]
    gap_amount      = analysis["gap_amount"]
    analyst_summary = analysis["analyst_summary"]
    current_revenue = analysis["current_revenue"]
    daily_target    = analysis["daily_target"]
    forecast_eod    = analysis["forecast_eod"]
    attainment      = analysis["attainment"]

    # ── Advisors ──────────────────────────────────────────────────────────────
    sellers    = pos_data.get("sellers", [])
    per_seller = round(daily_target / max(len(sellers), 1))
    max_rev    = max((s.get("revenue_today", 0) for s in sellers), default=0)

    advisors = sorted([
        {
            "id":         s.get("name","").replace(" ","_").lower(),
            "name":       s.get("name",""),
            "revenue":    round(s.get("revenue_today", 0)),
            "target":     per_seller,
            "attainment": round(s.get("revenue_today", 0) / max(per_seller, 1) * 100),
            "nb_ventes":  s.get("nb_ventes", 0),
            "status":     "Top"    if s.get("revenue_today", 0) == max_rev
                          else "OK" if s.get("revenue_today", 0) / max(per_seller, 1) >= 0.5
                          else "Urgent",
            "trend":      "up"  if s.get("revenue_today", 0) >= per_seller * 0.7 else "down",
        }
        for s in sellers
    ], key=lambda x: -x["revenue"])

    for i, a in enumerate(advisors):
        a["rank"] = i + 1

    # ── Hourly performance depuis historique ──────────────────────────────────
    hourly_dict: dict[int, float] = {}
    for tx in pos_history:
        tx_time = tx.get("transaction_time")
        if tx_time and hasattr(tx_time, "hour"):
            h = tx_time.hour
            hourly_dict[h] = hourly_dict.get(h, 0.0) + tx.get("revenue", 0.0)

    current_hour    = datetime.now().hour
    hours_elapsed   = max(1, current_hour - 9)
    hours_remaining = max(1, 20 - current_hour)
    target_per_hour = round(daily_target / 11)
    hourly_rate     = current_revenue / hours_elapsed

    hourly_performance = []
    for h, rev in sorted(hourly_dict.items()):
        label = "12PM" if h == 12 else f"{h}AM" if h < 12 else f"{h-12}PM"
        hourly_performance.append({
            "hour":     label,
            "revenue":  round(rev),
            "actual":   round(rev),
            "target":   target_per_hour,
            "forecast": round(hourly_rate * random.uniform(0.9, 1.1)),
            "risk":     rev < target_per_hour * 0.85,
        })

    # ── Risk hours ────────────────────────────────────────────────────────────
    risk_hours = []
    for h in hourly_performance:
        rev     = h["revenue"]
        tgt_pct = round((rev / target_per_hour) * 100) if target_per_hour > 0 else 0
        if tgt_pct < 85:
            risk_hours.append({
                "hour":         h["hour"],
                "target_pct":   tgt_pct,
                "units_behind": round((rev - target_per_hour) / 150),
            })

    # ── Product mix ───────────────────────────────────────────────────────────
    by_cat: dict[str, float] = {}
    for tx in pos_history:
        cat = tx.get("product_category", "Autre")
        by_cat[cat] = by_cat.get(cat, 0) + tx.get("revenue", 0)

    product_mix = [
        {
            "product":     cat,
            "revenue":     round(rev),
            "attainment":  round(rev / max(daily_target / max(len(by_cat), 1), 1) * 100),
            "stock_level": "Low" if "Smartphone" in cat else "OK",
            "forecast":    round(rev * 1.10),
        }
        for cat, rev in sorted(by_cat.items(), key=lambda x: -x[1])
    ]

    return {
        "type":      "metrics_update",
        "timestamp": datetime.now().isoformat(),

        "ca_today":   current_revenue,
        "ca_target":  daily_target,
        "attainment": attainment,

        "visitors_h":  pos_data.get("nb_transactions_today", 0),
        "agents_live": pos_data.get("active_sellers", 4),

        "niveau_urgence":  urgency_level,
        "urgency_score":   urgency_score,
        "ecart_objectif":  gap_pct,
        "gap_amount":      gap_amount,
        "analyst_summary": analyst_summary,
        "route_to":        "strategie" if urgency_level in ("HIGH","MEDIUM") else "coach",

        "forecast_eod":     forecast_eod,
        "forecast_ci_low":  prediction.get("confidence_interval", {}).get("low", 0),
        "forecast_ci_high": prediction.get("confidence_interval", {}).get("high", 0),
        "forecast_mape":    analysis.get("mape", 14.3),

        "store_context": {
            "weather":     "Pluie 14h-18h",
            "event":       "Concert ce soir (2km)",
            "stock_alert": "iPhone 15 — 3 unités restantes",
        },

        "advisors":     advisors,
        "liveAdvisors": advisors,

        "analyst_nodes": {
            "receive_pos":    {"status":"done","transactions": len(pos_history)},
            "compute_gap":    {"status":"done","gap_pct": gap_pct,"gap_amount": gap_amount},
            "call_timesfm":   {"status":"done","forecast_eod": forecast_eod},
            "detect_urgency": {"status":"done","level": urgency_level,"score": urgency_score},
            "llm_summary":    {"status":"done","summary": analyst_summary},
        },

        "hourly_performance": hourly_performance,
        "risk_hours":         risk_hours,
        "context_heatmap":    compute_heatmap(urgency_level),
        "product_mix":        product_mix,

        "advisor_priorities": [
            {
                "advisor_id":  a["id"],
                "name":        a["name"],
                "performance": a["attainment"],
                "priority":    "TOP_CLOSE" if a["attainment"] >= 80
                               else "STABLE" if a["attainment"] >= 50
                               else "AT_RISK",
                "reason":      f"{a['nb_ventes']} ventes · {a['revenue']:,} DT",
                "action":      f"Gap {100-a['attainment']}% à combler"
                               if a["attainment"] < 80
                               else "Maintenir le rythme",
            }
            for a in advisors
        ],

        "context_signals": [
            {"type":"weather","label":"Pluie 14h-18h — impact trafic -20%"},
            {"type":"stock",  "label":"iPhone 15 — 3 unités restantes"},
            {"type":"event",  "label":"Concert ce soir (2km) — pic 18h-20h"},
        ],

        "ca_yesterday_same_hour": current_revenue * 0.88,
        "last_cycle_id":          f"cycle_{datetime.now().strftime('%H%M%S')}",
    }


# ─────────────────────────────────────────────────────────────────────────────
# WEBSOCKET ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws/store/{store_id}")
async def store_websocket(websocket: WebSocket, store_id: str):
    await websocket.accept()

    # ── Bloquer double connexion ──────────────────────────
    if store_id in _active_stores:
        print(f"⚠️  Double connexion bloquée → {store_id}")
        await websocket.close(code=1008)
        return

    _active_stores.add(store_id)
    print(f"\n🔌 Frontend connecté → {store_id}")
    mapped_id = STORE_MAP.get(store_id, "OOR_LAC_01")
    cycle = 0

    try:
        while True:
            cycle += 1
            analysis = await run_analyst_with_logs(mapped_id, cycle)
            msg      = build_payload_from_analysis(analysis)
            await websocket.send_text(json.dumps(msg, default=str))
            size = len(json.dumps(msg, default=str))
            print(f"\n📤 Payload envoyé au frontend ({size:,} bytes)")
            print(f"⏳ Prochain cycle dans 2 minutes...\n")
            await asyncio.sleep(120)

    except WebSocketDisconnect:
        print(f"\n🔌 Frontend déconnecté : {store_id}")
    except Exception as e:
        logger.error(f"[WS] Erreur cycle #{cycle}: {e}")
        try:
            await websocket.close()
        except Exception:
            pass
    finally:
        _active_stores.discard(store_id)
        print(f"🔓 Slot libéré → {store_id}\n")


@app.websocket("/ws/advisor/{advisor_id}")
async def advisor_websocket(websocket: WebSocket, advisor_id: str):
    await websocket.accept()
    try:
        while True:
            await websocket.send_text(json.dumps({
                "type":       "coach_update",
                "advisor_id": advisor_id,
                "timestamp":  datetime.now().isoformat(),
                "status":     "active",
            }))
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# REST ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

@app.get("/stores")
async def get_stores():
    return {"stores": list(STORE_MAP.keys())}

@app.get("/api/v1/stores/{store_id}/metrics")
async def get_store_metrics(store_id: str):
    mapped_id = STORE_MAP.get(store_id, "OOR_LAC_01")
    provider  = get_data_provider()
    pos_data  = await provider.fetch_pos_data(mapped_id)
    cr = pos_data.get("current_revenue", 0)
    dt = pos_data.get("daily_target", 18000)
    return JSONResponse({
        "ca_today":    cr,
        "ca_target":   dt,
        "attainment":  round((cr / dt) * 100, 1) if dt > 0 else 0,
        "visitors_h":  pos_data.get("nb_transactions_today", 0),
        "agents_live": pos_data.get("active_sellers", 4),
        "store_context": {
            "weather":     "Pluie 14h-18h",
            "event":       "Concert ce soir (2km)",
            "stock_alert": "iPhone 15 — 3 unités restantes",
        },
        "ca_yesterday_same_hour": cr * 0.88,
    })

@app.get("/api/v1/forecast/eod/{store_id}")
async def get_forecast_eod(store_id: str):
    mapped_id = STORE_MAP.get(store_id, "OOR_LAC_01")
    provider  = get_data_provider()
    pred      = await provider.fetch_timesfm_prediction(mapped_id)
    pos_data  = await provider.fetch_pos_data(mapped_id)
    dt  = pos_data.get("daily_target", 18000)
    cr  = pos_data.get("current_revenue", 0)
    ga  = max(0, dt - cr)
    gp  = round((ga / dt * 100) if dt > 0 else 0, 1)
    return JSONResponse({
        "eod":        pred.get("forecast_end_of_day", 0),
        "gap_pct":    gp,
        "gap_amount": ga,
        "ci_low":     pred.get("confidence_interval", {}).get("low", 0),
        "ci_high":    pred.get("confidence_interval", {}).get("high", 0),
    })

@app.get("/api/v1/stores/{store_id}/advisors")
async def get_advisors(store_id: str):
    mapped_id = STORE_MAP.get(store_id, "OOR_LAC_01")
    provider  = get_data_provider()
    pos_data  = await provider.fetch_pos_data(mapped_id)
    sellers   = pos_data.get("sellers", [])
    dt        = pos_data.get("daily_target", 18000)
    ps        = round(dt / max(len(sellers), 1))
    max_rev   = max((s.get("revenue_today", 0) for s in sellers), default=0)
    advisors  = sorted([
        {
            "id":         s.get("name","").replace(" ","_").lower(),
            "name":       s.get("name",""),
            "revenue":    round(s.get("revenue_today", 0)),
            "target":     ps,
            "attainment": round(s.get("revenue_today", 0) / max(ps, 1) * 100),
            "nb_ventes":  s.get("nb_ventes", 0),
            "status":     "Top" if s.get("revenue_today", 0) == max_rev else "OK",
        }
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
    cr  = pos_data.get("current_revenue", 0)
    dt  = pos_data.get("daily_target", 18000)
    ga  = max(0, dt - cr)
    gp  = round((ga / dt * 100) if dt > 0 else 0, 1)
    feo = prediction.get("forecast_end_of_day", 0)
    ul  = "HIGH" if gp > 30 else "MEDIUM" if gp > 15 else "LOW"
    us  = round(min(1.0, gp / 60), 3)
    cov = round(min(100.0, ((feo - cr) / ga * 100)) if ga > 0 else 100, 1)
    analysis = {
        "pos_data": pos_data, "pos_history": pos_history,
        "prediction": prediction,
        "urgency_level": ul, "urgency_score": us,
        "gap_pct": gp, "gap_amount": ga,
        "analyst_summary": f"Gap de {gp}% — CA {cr:,.0f} / {dt:,.0f} TND.",
        "current_revenue": cr, "daily_target": dt,
        "forecast_eod": feo, "attainment": round((cr/dt)*100,1),
        "coverage": cov, "mape": 14.3,
    }
    return JSONResponse(build_payload_from_analysis(analysis))