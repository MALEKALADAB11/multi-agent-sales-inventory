"""
Context Agent Nodes
====================
fetch_signals  → gather all external context signals (pure Python, no LLM)
interpret      → synthesise signals into a demand adjustment (LLM or rule-based fallback)
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from decimal import Decimal
from threading import Lock
from typing import Any, Dict

from app.core.shutdown import is_shutting_down, is_shutdown_error

try:
    from app.inventory.config.inventory_settings import SIGNAL_WEIGHTS
except ImportError:
    SIGNAL_WEIGHTS = {
        "promo_factor": 0.5, "holiday_uplift_pct": 10.0,
        "bad_weather_impact": -3.0, "confidence_min_sample": 50,
    }

logger = logging.getLogger(__name__)

# ── Cache TTL in-memory pour signaux store-level (météo/holidays/events) ───
# Ces données ne changent pas entre SKUs — même store/date → même valeur.
# Évite 330 appels identiques par cycle de 143 SKUs.
_SIGNAL_CACHE: Dict[str, Any] = {}
_SIGNAL_CACHE_LOCK = Lock()
_SIGNAL_TTL_SECONDS = 3600  # 1 heure


def _cache_get(key: str):
    """Retourne la valeur si non expirée, sinon None."""
    entry = _SIGNAL_CACHE.get(key)
    if entry and time.monotonic() - entry["ts"] < _SIGNAL_TTL_SECONDS:
        return entry["val"]
    return None


def _cache_set(key: str, val: Any) -> None:
    with _SIGNAL_CACHE_LOCK:
        _SIGNAL_CACHE[key] = {"val": val, "ts": time.monotonic()}


class _SafeEncoder(json.JSONEncoder):
    """Handles Decimal, date, and other DB types that json.dumps chokes on."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, date):
            return obj.isoformat()
        return super().default(obj)


def _dumps(obj) -> str:
    return json.dumps(obj, indent=2, cls=_SafeEncoder)

from app.inventory.agents.context.tools import (
    get_historical_patterns,
    get_active_promotions,
    get_weather,
    get_upcoming_holidays,
    get_upcoming_events,
    get_product_category,
    _CAT_CODE_TO_NAME,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fetch Signals Node — pure Python
# ═══════════════════════════════════════════════════════════════════════════

def fetch_signals_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Gather all external context signals for a SKU/store.

    Optimizations vs v1:
      - Parallel fetch via ThreadPoolExecutor(max_workers=4)
      - Store-level signals (weather/holidays/events) cached 1h to avoid
        N×330 identical calls per inventory cycle
      - Failure in any signal does NOT block the rest
    """
    sku      = state["sku"]
    store_id = state["store_id"]

    # ── 1. Product category (SKU-specific — no cache) ──────────────────────
    try:
        raw_category = get_product_category(sku)
    except Exception as e:
        logger.warning("[Context/fetch] category lookup failed for %s: %s", sku, e)
        raw_category = "unknown"

    # Normalize immediately: raw codes ("70") never matched uplift columns or
    # in-memory category names downstream — this was why events/promotions
    # for accessoires were silently dropped. Everything below uses the
    # normalized name; tools.py functions re-normalize internally too, so
    # this is safe to pass through even if already normalized.
    category = _CAT_CODE_TO_NAME.get(raw_category, raw_category)

    today_str = str(date.today())

    # ── 2-6. Parallel fetch avec cache pour signaux store-level ───────────
    def _fetch_historical():
        key = f"hist:{category}:{store_id}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        try:
            result = get_historical_patterns(category, store_id)
            _cache_set(key, result)
            return result
        except Exception as e:
            logger.warning("[Context/fetch] historical_patterns failed: %s", e)
            return {
                "baseline_avg_qty": 0.0, "category": category,
                "sample_size": 0, "by_event_type": {}, "by_promo": {}, "by_season": {},
            }

    def _fetch_promotions():
        # Promos sont SKU-spécifiques → pas de cache global
        try:
            return get_active_promotions(sku, category, store_id)
        except Exception as e:
            logger.warning("[Context/fetch] promotions failed: %s", e)
            return []

    def _fetch_weather():
        key = f"weather:{store_id}:{today_str}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        try:
            result = get_weather(store_id)
            _cache_set(key, result)
            return result
        except Exception as e:
            logger.warning("[Context/fetch] weather failed: %s", e)
            return {
                "summary": "unavailable", "avg_temp_max": None,
                "total_precip_mm": None, "bad_weather_days": 0,
                "days": [], "source": "error",
            }

    def _fetch_holidays():
        key = f"holidays:{today_str}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        try:
            result = get_upcoming_holidays()
            _cache_set(key, result)
            return result
        except Exception as e:
            logger.warning("[Context/fetch] holidays failed: %s", e)
            return []

    def _fetch_events():
        # sku/store_id-specific now (inventory.events is per-SKU/boutique) —
        # cache key must include them, this signal is no longer store-level
        # only like weather/holidays.
        key = f"events:{category}:{sku}:{store_id}:{today_str}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        try:
            result = get_upcoming_events(category, sku=sku, store_id=store_id)
            _cache_set(key, result)
            return result
        except Exception as e:
            logger.warning("[Context/fetch] db_events failed: %s", e)
            return []

    def _fetch_market_offers():
        # Tendances marché : offres Ooredoo scrapées par le Stratège sales
        # (lecture cache uniquement — aucun scraping dans le cycle inventory)
        key = f"trends:{category}:{today_str}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        try:
            from app.core.trends_provider import get_market_offers
            result = get_market_offers(category)
            _cache_set(key, result)
            return result
        except Exception as e:
            logger.warning("[Context/fetch] market_offers failed: %s", e)
            return []

    # Lancer les 6 fetches en parallèle
    tasks = {
        "historical":    _fetch_historical,
        "promotions":    _fetch_promotions,
        "weather":       _fetch_weather,
        "holidays":      _fetch_holidays,
        "events":        _fetch_events,
        "market_offers": _fetch_market_offers,
    }
    results: Dict[str, Any] = {}

    def _fallback(name: str):
        return [] if name in ("promotions", "holidays", "events", "market_offers") else {}

    # Pendant l'arrêt du processus, submit() lève RuntimeError ("cannot schedule
    # new futures after interpreter shutdown") : on récupère les signaux en
    # séquentiel plutôt que de perdre tout le contexte du SKU.
    pending: Dict[str, Any] = dict(tasks)
    if not is_shutting_down():
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {}
            for name, fn in tasks.items():
                try:
                    futures[pool.submit(fn)] = name
                except RuntimeError as e:
                    if not is_shutdown_error(e):
                        raise
                    break
            for future in as_completed(futures):
                name = futures[future]
                pending.pop(name, None)
                try:
                    results[name] = future.result()
                except Exception as e:
                    logger.warning("[Context/fetch] parallel task '%s' failed: %s", name, e)
                    results[name] = _fallback(name)

    for name, fn in pending.items():
        try:
            results[name] = fn()
        except Exception as e:
            logger.warning("[Context/fetch] task '%s' failed: %s", name, e)
            results[name] = _fallback(name)

    historical_patterns = results.get("historical", {})
    promotions          = results.get("promotions", [])
    weather             = results.get("weather", {})
    holidays            = results.get("holidays", [])
    events              = results.get("events", [])
    market_offers       = results.get("market_offers", [])

    signals = {
        "category":           category,
        "historical_patterns": historical_patterns,
        "promotions":          promotions,
        "weather":             weather,
        "holidays":            holidays,
        "events":              events,
        "market_offers":       market_offers,
        "today":               today_str,
        "horizon":             str(date.today() + timedelta(days=7)),
    }

    logger.info(
        "[Context/fetch] SKU=%s store=%s | category=%s promos=%d "
        "holidays=%d events=%d offers=%d bad_weather=%d history_rows=%d",
        sku, store_id, category,
        len(promotions), len(holidays), len(events), len(market_offers),
        weather.get("bad_weather_days", 0),
        historical_patterns.get("sample_size", 0),
    )

    return {**state, "signals": signals}


def _build_events_summary(signals: Dict[str, Any], max_items: int = 4) -> str:
    """
    Résumé français court des événements/promotions/offres actifs pour ce
    SKU/store, destiné à être cité tel quel par le Decision Agent dans
    recommendation_text (ex: "la demande est soutenue par Rentrée scolaire
    2026"). Sans ceci, les events existent dans `signals` mais ne sont
    jamais mentionnés par leur nom dans la décision finale — seulement
    noyés dans un pourcentage d'uplift anonyme.

    Combine les 3 sources de signal :
      - events        (market.events scrapé + inventory.events en base)
      - promotions     (inventory.promotions)
      - market_offers  (offres Ooredoo scrapées, ooredoo.tn)
    Trié par impact décroissant, tronqué à max_items pour rester lisible.
    """
    items: list = []  # (abs_impact, phrase)

    for ev in signals.get("events", []) or []:
        name   = ev.get("event_name") or "Événement"
        uplift = ev.get("estimated_uplift_pct")
        if uplift:
            items.append((abs(float(uplift)), f"{name} ({float(uplift):+.0f}%)"))
        else:
            items.append((0.5, name))

    for p in signals.get("promotions", []) or []:
        name = p.get("promo_name") or "Promotion"
        disc = float(p.get("discount_pct") or 0)
        phrase = f"{name} (-{disc:.0f}%)" if disc else name
        items.append((disc or 1.0, phrase))

    for o in signals.get("market_offers", []) or []:
        title = str(o.get("title", ""))[:40] or "Offre Ooredoo"
        items.append((1.0, f"Offre Ooredoo — {title}"))

    if not items:
        return "Aucun"

    items.sort(key=lambda t: t[0], reverse=True)
    return "; ".join(phrase for _, phrase in items[:max_items])


# ═══════════════════════════════════════════════════════════════════════════
# Rule-Based Fallback
# ═══════════════════════════════════════════════════════════════════════════

def _rule_based_interpret(
    signals: Dict[str, Any],
    sku: str,
    store_id: str,
) -> Dict[str, Any]:
    """
    Simple additive model used when LLM is off or errored.

    Rules:
      - Each active promotion:  discount_pct × 0.5
      - Each national holiday:  +10%
      - Each DB event with known uplift: add estimated_uplift_pct
      - Each heavy-rain day:    -3%

    Dominant signal = whichever contributed the largest absolute uplift.
    Confidence = medium if historical sample > 50, else low.
    """
    uplift     = 0.0
    dominant   = "none"
    notes: list[str] = []

    # Promotions
    promo_uplift = 0.0
    promo_factor = SIGNAL_WEIGHTS["promo_factor"]
    for p in signals.get("promotions", []):
        disc = float(p.get("discount_pct", 0))
        contrib = disc * promo_factor
        promo_uplift += contrib
        notes.append(
            f"{p.get('promo_name', 'promo')} ({disc:.0f}% off) → +{contrib:.1f}%"
        )
    if promo_uplift > 0:
        uplift  += promo_uplift
        dominant = "promotions"

    # Holidays
    holiday_uplift_pct = SIGNAL_WEIGHTS["holiday_uplift_pct"]
    holiday_uplift = 0.0
    for h in signals.get("holidays", []):
        if h.get("is_national", True):
            holiday_uplift += holiday_uplift_pct
            notes.append(
                f"{h.get('name', 'holiday')} in {h.get('days_away', '?')} days → +{holiday_uplift_pct:.0f}%"
            )
    if holiday_uplift > 0:
        if holiday_uplift > promo_uplift:
            dominant = "holidays"
        uplift += holiday_uplift

    # Events with known estimated uplift
    event_uplift = 0.0
    for ev in signals.get("events", []):
        est = ev.get("estimated_uplift_pct")
        if est is not None:
            event_uplift += float(est)
            notes.append(
                f"Event '{ev.get('event_name', '')}' → {float(est):+.1f}%"
            )
    if event_uplift != 0:
        if abs(event_uplift) > abs(promo_uplift) and abs(event_uplift) > holiday_uplift:
            dominant = "events"
        uplift += event_uplift

    # Bad weather
    bad_days       = signals.get("weather", {}).get("bad_weather_days", 0)
    weather_factor = SIGNAL_WEIGHTS["bad_weather_impact"]
    weather_uplift = bad_days * weather_factor
    if bad_days > 0:
        notes.append(f"{bad_days} days heavy rain → {weather_uplift:.1f}%")
        if abs(weather_uplift) > abs(promo_uplift) and abs(weather_uplift) > holiday_uplift:
            dominant = "weather"
        uplift += weather_uplift

    # Market offers (offres Ooredoo scrapées — tendances marché)
    # Une offre active sur la catégorie tire la demande : +3% par offre, max +9%
    offers = signals.get("market_offers", [])
    offer_uplift_pct = SIGNAL_WEIGHTS.get("market_offer_uplift_pct", 3.0)
    market_uplift = min(len(offers), 3) * offer_uplift_pct
    if market_uplift > 0:
        titles = ", ".join(str(o.get("title", ""))[:30] for o in offers[:3])
        notes.append(f"{len(offers)} offre(s) Ooredoo actives ({titles}) → +{market_uplift:.1f}%")
        if market_uplift > abs(promo_uplift) and market_uplift > holiday_uplift \
                and market_uplift > abs(event_uplift) and market_uplift > abs(weather_uplift):
            dominant = "market_offers"
        uplift += market_uplift

    # Confidence
    sample_size  = signals.get("historical_patterns", {}).get("sample_size", 0)
    conf_min     = SIGNAL_WEIGHTS["confidence_min_sample"]
    confidence   = "medium" if sample_size >= conf_min else "low"

    # ── Contextual Intelligence fields (v2 schema) ──────────────────────────
    # No LLM reasoning available here, so these are conservative heuristics,
    # not judgment — they exist so the rule-based path returns the same
    # shape as the LLM path and never leaves the decision agent guessing.

    # impact_window_days: use the shortest *known* duration among active
    # signals when one is knowable (bad weather days), otherwise default 7 —
    # we have no per-event/promo duration data at this layer to do better.
    bad_days = signals.get("weather", {}).get("bad_weather_days", 0)
    if dominant == "weather" and bad_days:
        impact_window_days = min(max(int(bad_days), 1), 14)
    else:
        impact_window_days = 7

    # context_volatility: more active signal *types* stacking → more
    # uncertain combined read. No historical sample → automatically HIGH.
    active_signal_types = sum([
        promo_uplift != 0.0,
        holiday_uplift != 0.0,
        event_uplift != 0.0,
        weather_uplift != 0.0,
        market_uplift != 0.0,
    ])
    if sample_size == 0 or active_signal_types >= 3:
        context_volatility = "HIGH"
    elif active_signal_types >= 2:
        context_volatility = "MEDIUM"
    else:
        context_volatility = "LOW"

    buffer_recommendation_pct = {
        "HIGH": 15.0, "MEDIUM": 5.0, "LOW": 0.0,
    }[context_volatility]

    operational_directives = {
        "urgency": "immediate" if abs(uplift) >= 20.0 else (
            "this_week" if abs(uplift) >= 5.0 else "none"
        ),
        "delivery_timing": "N/A — mode règles, pas d'estimation de timing.",
        "risk_mitigation": "N/A — mode règles, pas d'estimation de risque.",
    }

    signal_summary = "; ".join(notes) if notes else "no active signals"
    interpretation = (
        f"Rule-based fallback estimate (LLM unavailable). "
        f"Active signals: {signal_summary}. "
        f"Combined demand adjustment: {uplift:+.1f}% over {impact_window_days}d."
    )

    return {
        "demand_uplift_pct":          round(uplift, 1),
        "impact_window_days":         impact_window_days,
        "context_volatility":         context_volatility,
        "buffer_recommendation_pct":  buffer_recommendation_pct,
        "store_context_impact":       "Non déterminable — mode règles, aucune analyse qualitative de boutique.",
        "operational_directives":     operational_directives,
        "interpretation":             interpretation,
        "confidence":                 confidence,
        "dominant_signal":            dominant,
        "reasoning_source":           "rule_based_fallback",
    }


# ═══════════════════════════════════════════════════════════════════════════
# Interpret Node Factory
# ═══════════════════════════════════════════════════════════════════════════

def create_interpret_node(llm, use_llm: bool):
    """
    Returns the interpret node function bound to the given LLM.
    If use_llm=False or llm is None, the node always runs the rule-based fallback.
    LLM errors at runtime also fall through to the rule-based fallback.
    """

    def interpret_node(state: Dict[str, Any]) -> Dict[str, Any]:
        sku      = state["sku"]
        store_id = state["store_id"]
        signals  = state["signals"]

        if not use_llm or llm is None:
            report = _rule_based_interpret(signals, sku, store_id)
            report["signals"] = signals
            report["events_summary"] = _build_events_summary(signals)
            return {**state, "context_report": report}

        # ── LLM path ──────────────────────────────────────────────────────
        from langchain_core.messages import HumanMessage, SystemMessage
        from app.inventory.agents.context.prompts import INTERPRET_SYSTEM, INTERPRET_USER

        patterns = signals.get("historical_patterns", {})
        weather  = signals.get("weather", {})

        user_prompt = INTERPRET_USER.format(
            sku=sku,
            store_id=store_id,
            category=signals.get("category", "unknown"),
            today=signals.get("today", str(date.today())),
            horizon=signals.get("horizon", str(date.today() + timedelta(days=7))),
            baseline_avg_qty=patterns.get("baseline_avg_qty", 0),
            sample_size=patterns.get("sample_size", 0),
            by_event_type=_dumps(patterns.get("by_event_type", {})),
            by_promo=_dumps(patterns.get("by_promo", {})),
            by_season=_dumps(patterns.get("by_season", {})),
            promotions=_dumps(signals.get("promotions", [])),
            holidays=_dumps(signals.get("holidays", [])),
            events=_dumps(signals.get("events", [])),
            weather_summary=weather.get("summary", "unavailable"),
            avg_temp_max=weather.get("avg_temp_max", "N/A"),
            bad_weather_days=weather.get("bad_weather_days", 0),
            weather_days=_dumps(weather.get("days", [])),
        )

        # Tendances marché (offres Ooredoo scrapées) — signal additionnel
        market_offers = signals.get("market_offers", [])
        if market_offers:
            user_prompt += (
                "\n\n## MARKET TRENDS — offres Ooredoo actives (scraping ooredoo.tn)\n"
                "Une offre commerciale active sur cette catégorie tire la demande à la hausse.\n"
                + _dumps(market_offers)
            )

        try:
            response = llm.invoke([
                SystemMessage(content=INTERPRET_SYSTEM),
                HumanMessage(content=user_prompt),
            ])
            raw = response.content.strip()

            # Strip markdown fences if LLM wrapped the JSON anyway
            if raw.startswith("```"):
                parts = raw.split("```")
                raw   = parts[1] if len(parts) > 1 else parts[0]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            parsed = json.loads(raw)

            # impact_window_days: clamp to the documented 1-14 range — an
            # LLM occasionally returns 0 or an out-of-range value, and a
            # 0-day window would silently zero out the decision agent's
            # proration math.
            try:
                impact_window_days = int(parsed.get("impact_window_days", 7))
            except (TypeError, ValueError):
                impact_window_days = 7
            impact_window_days = min(max(impact_window_days, 1), 14)

            context_volatility = str(parsed.get("context_volatility", "LOW")).upper()
            if context_volatility not in ("LOW", "MEDIUM", "HIGH"):
                context_volatility = "LOW"

            try:
                buffer_recommendation_pct = max(0.0, float(parsed.get("buffer_recommendation_pct", 0.0)))
            except (TypeError, ValueError):
                buffer_recommendation_pct = 0.0

            operational_directives = parsed.get("operational_directives", {})
            if not isinstance(operational_directives, dict):
                operational_directives = {}
            operational_directives = {
                "urgency":         str(operational_directives.get("urgency", "none")),
                "delivery_timing": str(operational_directives.get("delivery_timing", "N/A")),
                "risk_mitigation": str(operational_directives.get("risk_mitigation", "N/A")),
            }

            report = {
                "demand_uplift_pct":          float(parsed.get("demand_uplift_pct", 0.0)),
                "impact_window_days":         impact_window_days,
                "context_volatility":         context_volatility,
                "buffer_recommendation_pct":  buffer_recommendation_pct,
                "store_context_impact":       str(parsed.get("store_context_impact", "Non déterminable")),
                "operational_directives":     operational_directives,
                "interpretation":             str(parsed.get("interpretation", "")),
                "confidence":                 str(parsed.get("confidence", "low")),
                "dominant_signal":            str(parsed.get("dominant_signal", "none")),
                "reasoning_source":           "llm",
            }

            logger.info(
                "[Context/interpret] SKU=%s uplift=%.1f%% window=%dd volatility=%s "
                "confidence=%s dominant=%s",
                sku,
                report["demand_uplift_pct"],
                report["impact_window_days"],
                report["context_volatility"],
                report["confidence"],
                report["dominant_signal"],
            )

        except Exception as e:
            logger.warning(
                "[Context/interpret] LLM failed for SKU=%s: %s — falling back to rules",
                sku, e,
            )
            report = _rule_based_interpret(signals, sku, store_id)

        report["signals"] = signals
        report["events_summary"] = _build_events_summary(signals)
        return {**state, "context_report": report}

    return interpret_node