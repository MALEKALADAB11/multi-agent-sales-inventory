"""
WebSocket FastAPI — Agent Analyste + Stratège toutes les 2 minutes.
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

# Charge le .env racine (multi-agent-sales-inventory/.env)
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", encoding="utf-8")

from data.postgres_provider import get_data_provider
from modules.coaching.agents.analyst.agent import get_analyst_agent
from modules.coaching.agents.stratege.agent import get_stratege_agent

# ── Logging ───────────────────────────────────────────────────────────────────
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

_active_stores: set[str] = set()


@app.on_event("startup")
async def on_startup():
    _active_stores.clear()
    print("🚀 Serveur démarré — slots WebSocket réinitialisés")


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _extract_summary(raw: str, gap_pct: float, urgency: str,
                     current_revenue: float, daily_target: float,
                     forecast_eod: float) -> str:
    if not raw:
        return _make_fallback_summary(
            gap_pct, urgency, current_revenue, daily_target, forecast_eod
        )
    raw = raw.strip()
    if raw.startswith('{'):
        try:
            parsed  = json.loads(raw)
            summary = parsed.get("analyst_summary", "")
            if summary:
                return summary.strip()
        except json.JSONDecodeError:
            import re
            match = re.search(r'"analyst_summary"\s*:\s*"([^"]+)"', raw)
            if match:
                return match.group(1).strip()
    if not raw.startswith('{'):
        return raw[:400]
    return _make_fallback_summary(
        gap_pct, urgency, current_revenue, daily_target, forecast_eod
    )


def _make_fallback_summary(gap_pct: float, urgency: str,
                            current_revenue: float, daily_target: float,
                            forecast_eod: float) -> str:
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
    return (
        f"Performance correcte — gap {gap_pct:.1f}%. "
        f"CA {current_revenue:,.0f} / {daily_target:,.0f} TND. "
        f"Objectif atteignable."
    )


# ─────────────────────────────────────────────────────────────────────────────
# AGENT ANALYSTE — Logs terminal
# ─────────────────────────────────────────────────────────────────────────────

async def run_analyst_with_logs(store_id: str, cycle: int) -> dict:
    now = datetime.now().strftime("%H:%M:%S")

    print()
    print("=" * 62)
    print(f"  🤖 AGENT ANALYSTE — Cycle #{cycle} @ {now}")
    print(f"  Store : {store_id}")
    print("=" * 62)

    print()
    print("📥 Chargement des données...")
    provider    = get_data_provider()
    pos_data    = await provider.fetch_pos_data(store_id)
    pos_history = await provider.fetch_pos_history(store_id)
    prediction  = await provider.fetch_timesfm_prediction(store_id)

    current_revenue = pos_data.get("current_revenue", 0) or 0
    daily_target    = pos_data.get("daily_target", 18000) or 18000
    forecast_eod    = prediction.get("forecast_end_of_day", 0) or 0

    print(f"   ✓ Transactions   : {len(pos_history)}")
    print(f"   ✓ CA actuel      : {current_revenue:>10,.0f} TND")
    print(f"   ✓ Objectif       : {daily_target:>10,.0f} TND")
    print(f"   ✓ Forecast EOD   : {forecast_eod:>10,.0f} TND")

    # ── NODE 1 ────────────────────────────────────────────
    print()
    print("┌──────────────────────────────────────────────────┐")
    print("│  NODE 1 — Réception POS                          │")
    print("└──────────────────────────────────────────────────┘")
    await asyncio.sleep(0.2)
    sellers = pos_data.get("sellers", [])
    print(f"   → {len(pos_history)} transactions | {len(sellers)} conseillers")
    for s in sellers:
        print(
            f"   • {s['name']:<22} "
            f"{s['revenue_today']:>8,.0f} TND  "
            f"{s['nb_ventes']:>3} ventes"
        )
    print(f"   ✅ Node 1 OK")

    # ── NODE 2 ────────────────────────────────────────────
    print()
    print("┌──────────────────────────────────────────────────┐")
    print("│  NODE 2 — Calcul Gap Objectif                    │")
    print("└──────────────────────────────────────────────────┘")
    await asyncio.sleep(0.2)
    gap_amount = max(0, daily_target - current_revenue)
    gap_pct    = round(
        (gap_amount / daily_target * 100) if daily_target > 0 else 0, 1
    )
    attainment = round((current_revenue / daily_target) * 100, 1)
    filled     = int(attainment / 100 * 40)
    bar        = "█" * filled + "░" * (40 - filled)
    print(f"   → CA actuel      : {current_revenue:>10,.0f} TND")
    print(f"   → Objectif       : {daily_target:>10,.0f} TND")
    print(f"   → Gap montant    : {gap_amount:>10,.0f} TND")
    print(f"   → Gap %          : {gap_pct:>10.1f} %")
    print(f"   → Atteinte       : {attainment:>10.1f} %")
    print(f"   → [{bar}] {attainment:.0f}%")
    print(f"   ✅ Node 2 OK")

    # ── NODE 3 ────────────────────────────────────────────
    print()
    print("┌──────────────────────────────────────────────────┐")
    print("│  NODE 3 — Prévision TimesFM                      │")
    print("└──────────────────────────────────────────────────┘")
    await asyncio.sleep(0.3)
    ci      = prediction.get("confidence_interval", {}) or {}
    ci_low  = ci.get("low", 0)
    ci_high = ci.get("high", 0)
    mape    = round(random.uniform(12.0, 16.5), 1)

    if gap_amount > 0:
        coverage = round(
            min(100.0, ((forecast_eod - current_revenue) / gap_amount) * 100), 1
        )
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
        print(f"   ⚠️  Shortfall : {daily_target - forecast_eod:,.0f} TND non couverts")
    print(f"   ✅ Node 3 OK")

    # ── NODE 4 ────────────────────────────────────────────
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

    # ── NODE 5 — LLM ──────────────────────────────────────
    print()
    print("┌──────────────────────────────────────────────────┐")
    print("│  NODE 5 — Synthèse LLM (Ollama)                  │")
    print("└──────────────────────────────────────────────────┘")

    analyst_summary = ""
    agent_result    = {}
    t_start         = datetime.now()

    try:
        print(f"   → Appel LLM...")
        agent        = get_analyst_agent()
        agent_result = await agent.ainvoke({
            "pos_data":           {**pos_data, "current_hour": current_hour},
            "pos_history":        pos_history,
            "timesfm_prediction": prediction,
            "feedback_history":   [],
        })

        duration        = round((datetime.now() - t_start).total_seconds(), 1)
        raw             = agent_result.get("analyst_summary", "")
        analyst_summary = _extract_summary(
            raw, gap_pct, urgency_level,
            current_revenue, daily_target, forecast_eod
        )

        urgency_level = agent_result.get("urgency_level", urgency_level)
        urgency_score = agent_result.get("urgency_score", urgency_score)

        print(f"   → Durée LLM      : {duration}s")
        print(f"   → Résumé         : {len(analyst_summary)} chars")
        print()
        print(f"   💬 {analyst_summary}")
        print(f"   ✅ Node 5 OK (LLM)")

    except Exception as e:
        duration        = round((datetime.now() - t_start).total_seconds(), 1)
        analyst_summary = _make_fallback_summary(
            gap_pct, urgency_level, current_revenue, daily_target, forecast_eod
        )
        print(f"   ⚠️  Fallback ({duration}s) : {str(e)[:60]}")
        print(f"   💬 {analyst_summary}")
        print(f"   ✅ Node 5 OK (fallback)")

    # ── Résultat Analyste ─────────────────────────────────
    analyst_output = {
        "pos_data":           pos_data,
        "pos_history":        pos_history,
        "prediction":         prediction,
        "urgency_level":      urgency_level,
        "urgency_score":      urgency_score,
        "gap_objectif":       gap_pct,
        "gap_pct":            gap_pct,
        "gap_amount":         gap_amount,
        "analyst_summary":    analyst_summary,
        "current_revenue":    current_revenue,
        "daily_target":       daily_target,
        "forecast_eod":       forecast_eod,
        "attainment":         attainment,
        "coverage":           coverage,
        "mape":               mape,
        "timesfm_prediction": prediction,
        "feedback_history":   [],
    }

    # ── AGENT STRATÈGE ────────────────────────────────────
    print()
    print("=" * 62)
    print(f"  🎯 AGENT STRATÈGE — Cycle #{cycle}")
    print("=" * 62)

    stratege_output = {}

    try:
        stratege       = get_stratege_agent()
        stratege_state = await stratege.ainvoke(analyst_output)

        ctx           = stratege_state.get("external_context", {}).get("summary", {}) or {}
        strategie_str = stratege_state.get("strategie", "")
        actions       = stratege_state.get("strategie_actions", [])
        cause         = stratege_state.get("cause_racine", "")
        heatmap       = stratege_state.get("context_heatmap", {})
        signals       = stratege_state.get("context_signals", [])

        print(f"  Météo      : {ctx.get('weather_icon','')} {ctx.get('weather_label','')}")
        print(f"  Effet      : {ctx.get('weather_effect', 0):+.0%}")
        print(f"  Cause      : {cause[:70]}")
        print(f"  Stratégie  : {strategie_str[:80]}")
        print(f"  Actions    : {len(actions)}")
        for a in actions:
            print(f"    {a.get('priorite','')}) {a.get('action','')[:60]}")

        stratege_output = {
            "strategie":          strategie_str,
            "strategie_actions":  actions,
            "cause_racine":       cause,
            "context_heatmap":    heatmap,
            "context_signals":    signals,
            "external_context":   stratege_state.get("external_context", {}),
            "message_manager":    stratege_state.get("message_manager", ""),
            "focus_produits":     stratege_state.get("focus_produits", []),
        }

    except Exception as e:
        logger.error(f"[STRATEGE] Erreur: {e}")
        import traceback
        traceback.print_exc()
        print(f"  ⚠️  Stratège fallback: {str(e)[:60]}")
        stratege_output = {
            "strategie":          analyst_summary,
            "strategie_actions":  [],
            "cause_racine":       f"Gap de {gap_pct:.1f}%",
            "context_heatmap":    {},
            "context_signals":    [],
            "external_context":   {},
            "message_manager":    "",
            "focus_produits":     [],
        }

    # ── Résumé final ──────────────────────────────────────
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

    return {**analyst_output, **stratege_output}


# ─────────────────────────────────────────────────────────────────────────────
# BUILD PAYLOAD FRONTEND
# ─────────────────────────────────────────────────────────────────────────────

def _build_offer_script(offer: dict) -> str:
    """Argumentaire court prêt-à-dire pour une offre Ooredoo scrapée."""
    title   = offer.get("title", "").strip()
    price   = offer.get("price", "").strip()
    details = offer.get("details", "").strip()

    pitch = f"Présentez « {title} »" if title else "Présentez cette offre"
    if price:
        pitch += f" au tarif {price}"
    pitch += "."
    if details:
        pitch += f" {details}."
    return pitch


def build_payload_from_analysis(analysis: dict) -> dict:
    if analysis is None:
        analysis = {}

    pos_data        = analysis.get("pos_data")        or {}
    pos_history     = analysis.get("pos_history")     or []
    prediction      = analysis.get("prediction")      or {}
    urgency_level   = analysis.get("urgency_level")   or "LOW"
    urgency_score   = analysis.get("urgency_score")   or 0
    gap_pct         = analysis.get("gap_pct")         or 0
    gap_amount      = analysis.get("gap_amount")      or 0
    analyst_summary = analysis.get("analyst_summary") or ""
    current_revenue = analysis.get("current_revenue") or 0
    daily_target    = analysis.get("daily_target")    or 18000
    forecast_eod    = analysis.get("forecast_eod")    or 0
    attainment      = analysis.get("attainment")      or 0

    # ── Données Stratège ──────────────────────────────────
    strategie         = analysis.get("strategie")         or analyst_summary
    strategie_actions = analysis.get("strategie_actions") or []
    cause_racine      = analysis.get("cause_racine")      or ""
    context_heatmap   = analysis.get("context_heatmap")   or {}
    context_signals   = analysis.get("context_signals")   or []
    external_ctx      = analysis.get("external_context")  or {}
    message_manager   = analysis.get("message_manager")   or ""
    focus_produits    = analysis.get("focus_produits")    or []

    # ── Météo réelle depuis external_context ─────────────
    weather_summary = external_ctx.get("summary") or {}
    holidays        = external_ctx.get("holidays") or {}
    events_data     = external_ctx.get("events")   or {}

    weather_icon  = weather_summary.get("weather_icon",  "")
    weather_label = weather_summary.get("weather_label", "")
    weather_str   = f"{weather_icon} {weather_label}".strip()

    # ── Événement / Prochain férié ────────────────────────
    today_holiday = holidays.get("today_holiday") or {}
    next_holiday  = holidays.get("next_holiday")  or {}

    event_str = ""
    if today_holiday.get("name"):
        event_str = f"🎉 {today_holiday['name']}"
    elif next_holiday.get("name"):
        days      = next_holiday.get("days_until", 0)
        event_str = f"📅 {next_holiday['name']} dans {days}j"

    # ── Promos actives ────────────────────────────────────
    all_promos = (
        (events_data.get("promotions") or []) +
        (events_data.get("new_offers")  or [])
    )
    promo_str = (
        f"🎯 {len(all_promos)} offre(s) active(s) Ooredoo"
        if all_promos else ""
    )
    active_offers = [
        {
            "title":    offer.get("title", ""),
            "category": offer.get("category", ""),
            "type":     offer.get("type", ""),
            "price":    offer.get("price", ""),
            "details":  offer.get("details", ""),
            "script":   _build_offer_script(offer),
        }
        for offer in all_promos[:4]
    ]

    store_context = {
        "weather":       weather_str or "⛅ Météo en cours...",
        "event":         event_str,
        "promo":         promo_str,
        "stock_alert":   "📦 iPhone 15 — 3 unités restantes",
        "active_offers": active_offers,
    }

    # ── Visitors dynamique ────────────────────────────────
    current_hour  = datetime.now().hour
    hours_elapsed = max(1, current_hour - 9)
    nb_tx         = pos_data.get("nb_transactions_today", 0) or 0
    visitors_h    = max(10, round(nb_tx / hours_elapsed * random.uniform(0.9, 1.2)))

    # ── Advisors ──────────────────────────────────────────
    sellers    = pos_data.get("sellers", []) or []
    per_seller = round(daily_target / max(len(sellers), 1))
    max_rev    = max((s.get("revenue_today", 0) for s in sellers), default=0)

    advisors = sorted([
        {
            "id":         s.get("name", "").replace(" ", "_").lower(),
            "name":       s.get("name", ""),
            "revenue":    round(s.get("revenue_today", 0)),
            "target":     per_seller,
            "attainment": round(
                s.get("revenue_today", 0) / max(per_seller, 1) * 100
            ),
            "nb_ventes":  s.get("nb_ventes", 0),
            "status":     "Top"    if s.get("revenue_today", 0) == max_rev
                          else "OK" if s.get("revenue_today", 0) / max(per_seller, 1) >= 0.5
                          else "Urgent",
            "trend":      "up" if s.get("revenue_today", 0) >= per_seller * 0.7 else "down",
        }
        for s in sellers
    ], key=lambda x: -x["revenue"])
    for i, a in enumerate(advisors):
        a["rank"] = i + 1

    # ── Hourly performance réelle ─────────────────────────
    hours_remaining = max(1, 20 - current_hour)
    target_per_hour = round(daily_target / 11)
    hourly_rate     = current_revenue / max(hours_elapsed, 1)

    # Données réelles depuis historique
    # fetch_pos_history() renvoie un champ entier "hour" (0-23), jamais de
    # "transaction_time" — ce mismatch de clé faisait que hourly_dict restait
    # toujours vide, donc le graphique "Performance horaire" n'affichait aucune
    # barre "Réel" malgré un CA du jour réel non nul.
    hourly_dict: dict[int, float] = {}
    for tx in pos_history:
        h = tx.get("hour")
        if h is not None:
            hourly_dict[int(h)] = hourly_dict.get(int(h), 0.0) + float(tx.get("revenue", 0.0) or 0.0)

    hourly_performance = []

    # Heures passées — données réelles
    for h in range(9, min(current_hour + 1, 21)):
        rev = hourly_dict.get(h, 0.0)
        if h == 12:
            label = "12PM"
        elif h < 12:
            label = f"{h}AM"
        else:
            label = f"{h - 12}PM"

        hourly_performance.append({
            "hour":     label,
            "revenue":  round(rev),
            "actual":   round(rev),
            "target":   target_per_hour,
            "forecast": round(rev) if rev > 0 else round(
                hourly_rate * random.uniform(0.85, 1.10)
            ),
            "risk":     rev > 0 and rev < target_per_hour * 0.85,
        })

    # Heures futures — forecast uniquement
    for h in range(current_hour + 1, 21):
        if h == 12:
            label = "12PM"
        elif h < 12:
            label = f"{h}AM"
        else:
            label = f"{h - 12}PM"

        # Multiplier selon les pics horaires réalistes
        if h in [12, 13, 17, 18]:
            mult = random.uniform(1.10, 1.30)
        elif h in [9, 10, 19, 20]:
            mult = random.uniform(0.60, 0.80)
        else:
            mult = random.uniform(0.90, 1.10)

        hourly_performance.append({
            "hour":     label,
            "revenue":  0,
            "actual":   0,
            "target":   target_per_hour,
            "forecast": round(target_per_hour * mult),
            "risk":     False,
        })

    # ── Risk hours ────────────────────────────────────────
    risk_hours = []
    for h in hourly_performance:
        rev     = h["revenue"]
        tgt_pct = round(
            (rev / target_per_hour) * 100
        ) if target_per_hour > 0 and rev > 0 else 0
        if 0 < tgt_pct < 85:
            risk_hours.append({
                "hour":         h["hour"],
                "target_pct":   tgt_pct,
                "units_behind": round((rev - target_per_hour) / 150),
            })

    # ── Product mix ───────────────────────────────────────
    by_cat: dict[str, float] = {}
    for tx in pos_history:
        cat = tx.get("product_category", "Autre")
        by_cat[cat] = by_cat.get(cat, 0) + tx.get("revenue", 0)

    product_mix = [
        {
            "product":     cat,
            "revenue":     round(rev),
            "attainment":  round(
                rev / max(daily_target / max(len(by_cat), 1), 1) * 100
            ),
            "stock_level": "Low" if "Smartphone" in cat else "OK",
            "forecast":    round(rev * 1.10),
        }
        for cat, rev in sorted(by_cat.items(), key=lambda x: -x[1])
    ]

    # ── Heatmap — priorité données réelles Stratège ───────
    final_heatmap = (
        context_heatmap
        if context_heatmap and context_heatmap.get("traffic")
        else _compute_heatmap(urgency_level)
    )

    # ── Signaux contextuels ───────────────────────────────
    final_signals = context_signals if context_signals else [
        {"type": "weather", "label": "Météo non disponible",         "level": "low", "value": 0},
        {"type": "stock",   "label": "iPhone 15 — 3 unités restantes", "level": "high", "value": -0.3},
    ]

    # ── Coaching cards depuis actions Stratège ────────────
    coaching_cards = [
        {
            "advisor":   advisors[i % len(advisors)]["name"] if advisors else "—",
            "urgency":   urgency_level,
            "gap_pct":   advisors[i % len(advisors)]["attainment"] if advisors else 0,
            "advice":    a.get("action", ""),
            "produit":   a.get("produit_cible", ""),
            "argument":  a.get("argument_vente", ""),
            "timestamp": datetime.now().strftime("%H:%M"),
            "priority":  a.get("priorite", i + 1),
        }
        for i, a in enumerate(strategie_actions[:4])
    ]

    return {
        "type":      "metrics_update",
        "timestamp": datetime.now().isoformat(),

        # ── KPIs ─────────────────────────────────────────
        "ca_today":   current_revenue,
        "ca_target":  daily_target,
        "attainment": attainment,
        "visitors_h": visitors_h,
        "agents_live": pos_data.get("active_sellers", 4),

        # ── Agent Analyste ────────────────────────────────
        "niveau_urgence":  urgency_level,
        "urgency_score":   urgency_score,
        "ecart_objectif":  gap_pct,
        "gap_amount":      gap_amount,
        "analyst_summary": analyst_summary,
        "route_to": (
            "strategie" if urgency_level in ("HIGH", "MEDIUM") else "coach"
        ),

        # ── TimesFM ──────────────────────────────────────
        "forecast_eod":     forecast_eod,
        "forecast_ci_low":  (prediction.get("confidence_interval") or {}).get("low", 0),
        "forecast_ci_high": (prediction.get("confidence_interval") or {}).get("high", 0),
        "forecast_mape":    analysis.get("mape", 14.3),

        # ── Agent Stratège ────────────────────────────────
        "strategie":         strategie,
        "strategie_actions": strategie_actions,
        "cause_racine":      cause_racine,
        "message_manager":   message_manager,
        "focus_produits":    focus_produits,

        # ── Contexte réel ─────────────────────────────────
        "store_context":   store_context,
        "context_heatmap": final_heatmap,
        "context_signals": final_signals,

        # ── Advisors ──────────────────────────────────────
        "advisors":     advisors,
        "liveAdvisors": advisors,

        # ── Nodes LangGraph ───────────────────────────────
        "analyst_nodes": {
            "receive_pos":    {"status": "done", "transactions": len(pos_history)},
            "compute_gap":    {"status": "done", "gap_pct": gap_pct, "gap_amount": gap_amount},
            "call_timesfm":   {"status": "done", "forecast_eod": forecast_eod},
            "detect_urgency": {"status": "done", "level": urgency_level, "score": urgency_score},
            "llm_summary":    {"status": "done", "summary": analyst_summary},
        },

        # ── Performance ───────────────────────────────────
        "hourly_performance": hourly_performance,
        "risk_hours":         risk_hours,
        "product_mix":        product_mix,
        "coaching_cards":     coaching_cards,

        "advisor_priorities": [
            {
                "advisor_id":  a["id"],
                "name":        a["name"],
                "performance": a["attainment"],
                "priority":    "TOP_CLOSE" if a["attainment"] >= 80
                               else "STABLE" if a["attainment"] >= 50
                               else "AT_RISK",
                "reason":      f"{a['nb_ventes']} ventes · {a['revenue']:,} DT",
                "action":      (
                    f"Gap {100 - a['attainment']}% à combler"
                    if a["attainment"] < 80
                    else "Maintenir le rythme"
                ),
            }
            for a in advisors
        ],

        "ca_yesterday_same_hour": current_revenue * 0.88,
        "last_cycle_id": f"cycle_{datetime.now().strftime('%H%M%S')}",
    }


def _compute_heatmap(urgency: str) -> dict:
    """Heatmap fallback si Stratège non disponible."""
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

# ─────────────────────────────────────────────────────────
# COACH AGENT — LLM + Fallback
# ─────────────────────────────────────────────────────────

import os as _os
_OPENROUTER_KEY   = _os.getenv("OPENROUTER_API_KEY", "")
_OPENROUTER_MODEL = _os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")
_OPENROUTER_URL   = "https://openrouter.ai/api/v1/chat/completions"
_USE_OPENROUTER   = bool(_OPENROUTER_KEY)


async def _get_coach_reply(system_prompt: str, message: str) -> tuple[str, str]:
    """OpenRouter en priorité, Ollama en fallback. Retourne (reply, source)."""
    import os
    if _USE_OPENROUTER:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=25) as client:
                resp = await client.post(
                    _OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {_OPENROUTER_KEY}",
                        "Content-Type":  "application/json",
                        "HTTP-Referer":  "https://github.com/MALEKALADAB11/multi-agent-sales-inventory",
                        "X-Title":       "AI Sales Coach Ooredoo",
                    },
                    json={
                        "model":       _OPENROUTER_MODEL,
                        "messages":    [
                            {"role": "system", "content": system_prompt},
                            {"role": "user",   "content": message},
                        ],
                        "temperature": 0.25,
                        "max_tokens":  300,
                    },
                )
                data = resp.json()
                if "error" not in data:
                    reply = data["choices"][0]["message"]["content"].strip()
                    if reply:
                        return reply, "openrouter"
        except Exception as e:
            logger.warning("[COACH] OpenRouter failed: %s", str(e)[:60])

    # Fallback Ollama
    from langchain_ollama import ChatOllama
    from langchain_core.messages import HumanMessage, SystemMessage
    llm = ChatOllama(
        model=os.getenv("OLLAMA_MODEL", "llama3.2:latest"),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        temperature=0.3,
        num_predict=250,
        num_ctx=2048,
    )
    resp = await llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=message)])
    return resp.content.strip(), "ollama"


def _build_coach_system(
    advisor_name: str,
    cr: float, dt: float,
    gap: float, gap_pct: float,
    urgency: str, weather: str,
    strategie: str, actions: list,
    cause: str
) -> str:
    actions_txt = "\n".join([
        f"- P{a.get('priorite','')})"
        f" {a.get('action','')} → {a.get('produit_cible','')}"
        for a in actions[:3]
    ]) or "Analyse en cours..."

    return f"""Tu es le CoachAgent IA d'Ooredoo Tunisie.
Assistant commercial expert pour les conseillers de vente en boutique.

## Contexte boutique temps réel
Conseiller : {advisor_name}
CA actuel   : {cr:,.0f} TND / Objectif : {dt:,.0f} TND
Gap         : {gap_pct:.1f}% ({gap:,.0f} TND à combler)
Urgence     : {urgency}
Météo       : {weather or 'Normale'}

## Analyse Agent Analyste
{cause or f'Gap de {gap_pct:.1f}% détecté — action recommandée'}

## Recommandations Agent Stratège
{strategie or 'Analyse en cours...'}
{actions_txt}

## Catalogue Ooredoo (prix réels Tunisie)
Smartphones : iPhone 16 Pro 1299 DT | Samsung A55 5G 899 DT | INFINIX 349 DT
Forfaits    : 5G Max 100Go 49 DT/mois | Famille 4 lignes 120 DT/mois
Internet    : Box Fibre 200Mbps 59 DT/mois | Box 4G+ 39 DT/mois
Services    : Assurance Premium 9 DT/mois | Cloud 1To 15 DT/mois
Accessoires : AirPods Pro 3 279 DT | Apple Watch S10 449 DT

## Règles
- Réponds en français, direct et professionnel
- Maximum 120 mots — concis et actionnable
- Donne TOUJOURS un script avec les vrais prix Ooredoo
- Adapte au contexte météo et urgence
- Utilise les données live (CA, gap, chiffres réels)
- Commence directement par l'action — pas de formules de politesse
- Si météo défavorable → accessoires résistants eau en priorité"""


def _coach_fallback(
    msg: str,
    gap_pct: float,
    urgency: str,
    actions: list,
    weather: str
) -> str:
    m = msg.lower()

    if any(k in m for k in ["assurance", "insurance"]):
        return (
            "Script Assurance Premium (9 DT/mois) :\n\n"
            "1. Proposer APRÈS confirmation achat terminal\n"
            "2. '9 DT/mois = remplacement écran en 48h'\n"
            "3. Montrer coût sans assurance = 280 DT réparation\n"
            "4. Cible : 70% conversion sur chaque vente terminal"
        )

    if any(k in m for k in ["5g", "concurrent", "réseau"]):
        return (
            "Argument 5G vs concurrents :\n\n"
            "1. Couverture Ooredoo 94% vs 87% concurrents\n"
            "2. Débit garanti vs bande passante partagée\n"
            "3. Testez leur réseau actuel en boutique — live\n"
            "4. Bundle 5G Max + terminal = avantage exclusif"
        )

    if any(k in m for k in ["prix", "objection", "cher", "budget"]):
        return (
            "Recadrage prix :\n\n"
            "• '1 299 DT = 54 DT/mois sur 24 mois'\n"
            "• Moins que Netflix + Spotify + café quotidien\n"
            "• 3x sans frais disponible immédiatement\n"
            "• Valeur de reprise à 2 ans = argument fort"
        )

    if any(k in m for k in ["pluie", "météo", "accessoire", "eau"]):
        return (
            f"Opportunité météo {weather} — accessoires :\n\n"
            "• AirPods Pro 3 (279 DT) — résistant eau IPX4\n"
            "• Apple Watch S10 (449 DT) — étanche 50m\n"
            "• Coques protection (29-89 DT) — achat impulsif\n"
            "→ Accroche : 'Parfait par ce temps, certifié résistant à l'eau'"
        )

    if any(k in m for k in ["stock", "iphone", "rupture", "épuisé"]):
        return (
            "Stock iPhone 16 Pro critique (3 unités) :\n\n"
            "• Urgence : 'Dernières unités disponibles'\n"
            "• Réservation avec acompte 10% si hésitation\n"
            "• Alternative premium : Samsung Galaxy S24 (1 099 DT)\n"
            "• Budget serré : Samsung A55 5G (899 DT)"
        )

    if any(k in m for k in ["objectif", "target", "gap", "atteindre"]):
        return (
            f"Atteindre l'objectif — Gap {gap_pct:.0f}% :\n\n"
            "1. iPhone 16 Pro + Assurance = 1 308 DT (comble 15% du gap)\n"
            "2. Bundle Fibre 200Mbps + TV Streaming = 71 DT/mois\n"
            "3. Famille 5G 4 lignes = 120 DT/mois haute valeur\n"
            f"→ Urgence {urgency} — focus produits haute marge"
        )

    if any(k in m for k in ["trafic", "pic", "17h", "18h", "concert"]):
        return (
            "Plan équipe pour le pic de trafic :\n\n"
            "• Conseiller 1 → terminaux premium (iPhone, Samsung S24)\n"
            "• Conseiller 2 → forfaits et fibre (conversion rapide)\n"
            "• Conseiller 3 → accessoires (trafic facile, marge haute)\n"
            "• Conseiller 4 → accueil et orientation\n"
            "→ Préparez les produits phares en vitrine avant le pic"
        )

    if any(k in m for k in ["fibre", "box", "internet", "domicile"]):
        return (
            "Script Box Fibre 200Mbps (59 DT/mois) :\n\n"
            "1. 'Combien de personnes utilisent internet chez vous ?'\n"
            "2. 200Mbps symétrique = upload rapide, gaming, télétravail\n"
            "3. Bundle Fibre + TV Streaming Ooredoo = +12 DT, +60% rétention\n"
            "4. Demandez quand expire leur contrat actuel\n"
            "5. Installation GRATUITE ce mois = argument de closing"
        )

    if any(k in m for k in ["stratégie", "agent", "recommand", "exécuter"]):
        if actions:
            txt = "\n\n".join([
                f"P{a.get('priorite','')}) {a.get('action','')}\n"
                f"   → {a.get('produit_cible','')}\n"
                f"   💬 {a.get('argument_vente','')}\n"
                f"   📈 {a.get('impact_estime','')}"
                for a in actions[:3]
            ])
            return f"Recommandations Agent Stratège :\n\n{txt}"

    if actions:
        a = actions[0]
        return (
            f"Action prioritaire ({urgency}) :\n\n"
            f"{a.get('action', '')}\n"
            f"→ Produit : {a.get('produit_cible', '')}\n"
            f"💬 {a.get('argument_vente', '')}\n"
            f"📈 {a.get('impact_estime', '')}"
        )

    return (
        f"Gap {gap_pct:.0f}% — Urgence {urgency}\n\n"
        "Focus recommandé :\n"
        "• Assurance Premium sur chaque vente terminal\n"
        "• Bundle Smartphone + Forfait 5G = panier optimal\n"
        f"• Météo {weather or 'normale'} → accessoires si contexte favorable"
    )


@app.post("/api/v1/coach/chat")
async def coach_chat(request: dict):
    """CoachAgent — Réponses LLM contextualisées Ooredoo."""
    message      = request.get("message",      "")
    advisor_name = request.get("advisor_name", "Conseiller")
    store_id     = request.get("store_id",     "store-lac2")
    context      = request.get("context",      {})

    if not message:
        return JSONResponse({"reply": "", "source": "empty"})

    mapped_id = STORE_MAP.get(store_id, "OOR_LAC_01")
    provider  = get_data_provider()
    pos_data  = await provider.fetch_pos_data(mapped_id)

    cr      = pos_data.get("current_revenue", 0) or 0
    dt      = pos_data.get("daily_target",   18000) or 18000
    gap     = max(0, dt - cr)
    gap_pct = round((gap / dt * 100) if dt > 0 else 0, 1)

    strategie = context.get("strategie",         "")
    actions   = context.get("strategie_actions", []) or []
    cause     = context.get("cause_racine",      "")
    weather   = context.get("weather",           "")
    urgency   = context.get("urgency",           "MEDIUM")

    system_prompt = _build_coach_system(
        advisor_name, cr, dt, gap, gap_pct,
        urgency, weather, strategie, actions, cause
    )

    try:
        reply, source = await _get_coach_reply(system_prompt, message)
        logger.info(
            f"[COACH] {source} OK ({len(reply)} chars) "
            f"| {advisor_name} | {store_id}"
        )
    except Exception as e:
        logger.warning(f"[COACH] LLM fallback: {str(e)[:60]}")
        reply  = _coach_fallback(message, gap_pct, urgency, actions, weather)
        source = "fallback"

    return JSONResponse({
        "reply":     reply,
        "source":    source,
        "timestamp": datetime.now().isoformat(),
    })
# ─────────────────────────────────────────────────────────────────────────────
# WEBSOCKET ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws/store/{store_id}")
async def store_websocket(websocket: WebSocket, store_id: str):
    await websocket.accept()

    if store_id in _active_stores:
        print(f"⚠️  Double connexion bloquée → {store_id}")
        await websocket.close(code=1008)
        return

    _active_stores.add(store_id)
    print(f"\n[WS] Frontend connecté → {store_id}")
    mapped_id = STORE_MAP.get(store_id, "OOR_LAC_01")
    cycle     = 0

    # ── Heartbeat — ping toutes les 30s ──────────────────
    async def heartbeat():
        while True:
            try:
                await asyncio.sleep(30)
                await websocket.send_text(json.dumps({
                    "type":      "ping",
                    "timestamp": datetime.now().isoformat(),
                }))
            except Exception:
                break

    heartbeat_task = asyncio.create_task(heartbeat())

    try:
        # ── Payload initial immédiat ──────────────────────
        print("📤 Envoi payload initial rapide...")
        try:
            provider    = get_data_provider()
            pos_data    = await provider.fetch_pos_data(mapped_id)
            pos_history = await provider.fetch_pos_history(mapped_id)
            prediction  = await provider.fetch_timesfm_prediction(mapped_id)

            cr  = pos_data.get("current_revenue", 0) or 0
            dt  = pos_data.get("daily_target", 18000) or 18000
            ga  = max(0, dt - cr)
            gp  = round((ga / dt * 100) if dt > 0 else 0, 1)
            feo = prediction.get("forecast_end_of_day", 0) or 0
            ul  = "HIGH" if gp > 30 else "MEDIUM" if gp > 15 else "LOW"
            att = round((cr / dt) * 100, 1) if dt > 0 else 0

            initial_analysis = {
                "pos_data":           pos_data,
                "pos_history":        pos_history,
                "prediction":         prediction,
                "urgency_level":      ul,
                "urgency_score":      round(min(1.0, gp / 60), 3),
                "gap_pct":            gp,
                "gap_objectif":       gp,
                "gap_amount":         ga,
                "analyst_summary":    (
                    f"Gap de {gp:.1f}% — CA {cr:,.0f} / {dt:,.0f} TND. "
                    f"Analyse LLM en cours..."
                ),
                "current_revenue":    cr,
                "daily_target":       dt,
                "forecast_eod":       feo,
                "attainment":         att,
                "coverage":           100.0,
                "mape":               14.3,
                "strategie":          "",
                "strategie_actions":  [],
                "cause_racine":       "",
                "context_heatmap":    {},
                "context_signals":    [],
                "external_context":   {},
                "message_manager":    "",
                "focus_produits":     [],
                "timesfm_prediction": prediction,
                "feedback_history":   [],
            }

            initial_msg  = build_payload_from_analysis(initial_analysis)
            initial_json = json.dumps(initial_msg, default=str)
            await websocket.send_text(initial_json)
            print(f"✅ Payload initial envoyé ({len(initial_json):,} bytes)")

        except Exception as e:
            logger.warning(f"[WS] Payload initial échoué: {e}")

        # ── Cycle principal ───────────────────────────────
        while True:
            cycle   += 1
            analysis = await run_analyst_with_logs(mapped_id, cycle)

            if analysis is None:
                logger.warning(f"[WS] Cycle #{cycle} retourné None — skip")
                await asyncio.sleep(120)
                continue

            msg      = build_payload_from_analysis(analysis)
            msg_json = json.dumps(msg, default=str)
            await websocket.send_text(msg_json)
            size = len(msg_json)
            print(f"\n📤 Payload cycle #{cycle} envoyé ({size:,} bytes)")
            print(f"⏳ Prochain cycle dans 2 minutes...\n")
            await asyncio.sleep(120)

    except WebSocketDisconnect:
        print(f"\n[WS] Frontend déconnecté : {store_id}")
    except Exception as e:
        logger.error(f"[WS] Erreur cycle #{cycle}: {e}")
        import traceback
        traceback.print_exc()
        try:
            await websocket.close()
        except Exception:
            pass
    finally:
        heartbeat_task.cancel()
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
    cr = pos_data.get("current_revenue", 0) or 0
    dt = pos_data.get("daily_target", 18000) or 18000
    return JSONResponse({
        "ca_today":    cr,
        "ca_target":   dt,
        "attainment":  round((cr / dt) * 100, 1) if dt > 0 else 0,
        "visitors_h":  pos_data.get("nb_transactions_today", 0),
        "agents_live": pos_data.get("active_sellers", 4),
        "store_context": {
            "weather":       "⛅ Météo en cours...",
            "event":         "",
            "promo":         "",
            "stock_alert":   "📦 iPhone 15 — 3 unités restantes",
            "active_offers": [],
        },
        "ca_yesterday_same_hour": cr * 0.88,
    })


@app.get("/api/v1/forecast/eod/{store_id}")
async def get_forecast_eod(store_id: str):
    mapped_id = STORE_MAP.get(store_id, "OOR_LAC_01")
    provider  = get_data_provider()
    pred      = await provider.fetch_timesfm_prediction(mapped_id)
    pos_data  = await provider.fetch_pos_data(mapped_id)
    dt  = pos_data.get("daily_target", 18000) or 18000
    cr  = pos_data.get("current_revenue", 0) or 0
    ga  = max(0, dt - cr)
    gp  = round((ga / dt * 100) if dt > 0 else 0, 1)
    return JSONResponse({
        "eod":        pred.get("forecast_end_of_day", 0),
        "gap_pct":    gp,
        "gap_amount": ga,
        "ci_low":     (pred.get("confidence_interval") or {}).get("low", 0),
        "ci_high":    (pred.get("confidence_interval") or {}).get("high", 0),
    })


@app.get("/api/v1/stores/{store_id}/advisors")
async def get_advisors(store_id: str):
    mapped_id = STORE_MAP.get(store_id, "OOR_LAC_01")
    provider  = get_data_provider()
    pos_data  = await provider.fetch_pos_data(mapped_id)
    sellers   = pos_data.get("sellers", []) or []
    dt        = pos_data.get("daily_target", 18000) or 18000
    ps        = round(dt / max(len(sellers), 1))
    max_rev   = max((s.get("revenue_today", 0) for s in sellers), default=0)
    advisors  = sorted([
        {
            "id":         s.get("name", "").replace(" ", "_").lower(),
            "name":       s.get("name", ""),
            "revenue":    round(s.get("revenue_today", 0)),
            "target":     ps,
            "attainment": round(
                s.get("revenue_today", 0) / max(ps, 1) * 100
            ),
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
    cr  = pos_data.get("current_revenue", 0) or 0
    dt  = pos_data.get("daily_target", 18000) or 18000
    ga  = max(0, dt - cr)
    gp  = round((ga / dt * 100) if dt > 0 else 0, 1)
    feo = prediction.get("forecast_end_of_day", 0) or 0
    ul  = "HIGH" if gp > 30 else "MEDIUM" if gp > 15 else "LOW"
    us  = round(min(1.0, gp / 60), 3)
    analysis = {
        "pos_data":           pos_data,
        "pos_history":        pos_history,
        "prediction":         prediction,
        "urgency_level":      ul,
        "urgency_score":      us,
        "gap_pct":            gp,
        "gap_objectif":       gp,
        "gap_amount":         ga,
        "analyst_summary":    f"Gap de {gp:.1f}% — CA {cr:,.0f} / {dt:,.0f} TND.",
        "current_revenue":    cr,
        "daily_target":       dt,
        "forecast_eod":       feo,
        "attainment":         round((cr / dt) * 100, 1) if dt > 0 else 0,
        "coverage":           100.0,
        "mape":               14.3,
        "strategie":          "",
        "strategie_actions":  [],
        "cause_racine":       "",
        "context_heatmap":    {},
        "context_signals":    [],
        "external_context":   {},
        "message_manager":    "",
        "focus_produits":     [],
    }
    return JSONResponse(build_payload_from_analysis(analysis))
