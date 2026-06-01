"""
Context Agent Nodes
====================
fetch_signals  → gather all external context signals (pure Python, no LLM)
interpret      → synthesise signals into a demand adjustment (LLM or rule-based fallback)
"""

import json
import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict

logger = logging.getLogger(__name__)


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

from src.agents.context.tools import (
    get_historical_patterns,
    get_active_promotions,
    get_weather,
    get_upcoming_holidays,
    get_upcoming_events,
    get_product_category,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fetch Signals Node — pure Python
# ═══════════════════════════════════════════════════════════════════════════

def fetch_signals_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Gather all external context signals for a SKU/store.

    Each signal is fetched independently — failure in one does NOT block
    the rest. The interpret node degrades gracefully with partial data.
    """
    sku      = state["sku"]
    store_id = state["store_id"]

    # ── 1. Product category ────────────────────────────────────────────────
    try:
        category = get_product_category(sku)
    except Exception as e:
        logger.warning("[Context/fetch] category lookup failed for %s: %s", sku, e)
        category = "unknown"

    logger.debug("[Context/fetch] SKU=%s store=%s category=%s", sku, store_id, category)

    # ── 2. Historical demand patterns (category-level) ─────────────────────
    try:
        historical_patterns = get_historical_patterns(category, store_id)
    except Exception as e:
        logger.warning("[Context/fetch] historical_patterns failed: %s", e)
        historical_patterns = {
            "baseline_avg_qty": 0.0,
            "category": category,
            "sample_size": 0,
            "by_event_type": {},
            "by_promo": {},
            "by_season": {},
        }

    # ── 3. Active / upcoming promotions ────────────────────────────────────
    try:
        promotions = get_active_promotions(sku, category, store_id)
    except Exception as e:
        logger.warning("[Context/fetch] promotions failed: %s", e)
        promotions = []

    # ── 4. 7-day weather forecast ──────────────────────────────────────────
    try:
        weather = get_weather(store_id)
    except Exception as e:
        logger.warning("[Context/fetch] weather failed: %s", e)
        weather = {
            "summary": "unavailable",
            "avg_temp_max": None,
            "total_precip_mm": None,
            "bad_weather_days": 0,
            "days": [],
            "source": "error",
        }

    # ── 5. Upcoming public holidays ────────────────────────────────────────
    try:
        holidays = get_upcoming_holidays()
    except Exception as e:
        logger.warning("[Context/fetch] holidays failed: %s", e)
        holidays = []

    # ── 6. Upcoming events from DB ─────────────────────────────────────────
    try:
        events = get_upcoming_events(category)
    except Exception as e:
        logger.warning("[Context/fetch] db_events failed: %s", e)
        events = []

    signals = {
        "category":           category,
        "historical_patterns": historical_patterns,
        "promotions":          promotions,
        "weather":             weather,
        "holidays":            holidays,
        "events":              events,
        "today":               str(date.today()),
        "horizon":             str(date.today() + timedelta(days=7)),
    }

    logger.info(
        "[Context/fetch] SKU=%s store=%s | category=%s promos=%d "
        "holidays=%d events=%d bad_weather=%d history_rows=%d",
        sku, store_id, category,
        len(promotions), len(holidays), len(events),
        weather.get("bad_weather_days", 0),
        historical_patterns.get("sample_size", 0),
    )

    return {**state, "signals": signals}


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
    for p in signals.get("promotions", []):
        disc = float(p.get("discount_pct", 0))
        contrib = disc * 0.5
        promo_uplift += contrib
        notes.append(
            f"{p.get('promo_name', 'promo')} ({disc:.0f}% off) → +{contrib:.1f}%"
        )
    if promo_uplift > 0:
        uplift  += promo_uplift
        dominant = "promotions"

    # Holidays
    holiday_uplift = 0.0
    for h in signals.get("holidays", []):
        if h.get("is_national", True):
            holiday_uplift += 10.0
            notes.append(
                f"{h.get('name', 'holiday')} in {h.get('days_away', '?')} days → +10%"
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
    bad_days     = signals.get("weather", {}).get("bad_weather_days", 0)
    weather_uplift = bad_days * (-3.0)
    if bad_days > 0:
        notes.append(f"{bad_days} days heavy rain → {weather_uplift:.1f}%")
        if abs(weather_uplift) > abs(promo_uplift) and abs(weather_uplift) > holiday_uplift:
            dominant = "weather"
        uplift += weather_uplift

    # Confidence
    sample_size = signals.get("historical_patterns", {}).get("sample_size", 0)
    confidence  = "medium" if sample_size >= 50 else "low"

    signal_summary = "; ".join(notes) if notes else "no active signals"
    interpretation = (
        f"Rule-based fallback estimate (LLM unavailable). "
        f"Active signals: {signal_summary}. "
        f"Combined demand adjustment: {uplift:+.1f}%."
    )

    return {
        "demand_uplift_pct": round(uplift, 1),
        "interpretation":    interpretation,
        "confidence":        confidence,
        "dominant_signal":   dominant,
        "reasoning_source":  "rule_based_fallback",
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
            return {**state, "context_report": report}

        # ── LLM path ──────────────────────────────────────────────────────
        from langchain_core.messages import HumanMessage, SystemMessage
        from src.agents.context.prompts import INTERPRET_SYSTEM, INTERPRET_USER

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

            report = {
                "demand_uplift_pct": float(parsed.get("demand_uplift_pct", 0.0)),
                "interpretation":    str(parsed.get("interpretation", "")),
                "confidence":        str(parsed.get("confidence", "low")),
                "dominant_signal":   str(parsed.get("dominant_signal", "none")),
                "reasoning_source":  "llm",
            }

            logger.info(
                "[Context/interpret] SKU=%s uplift=%.1f%% confidence=%s dominant=%s",
                sku,
                report["demand_uplift_pct"],
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
        return {**state, "context_report": report}

    return interpret_node