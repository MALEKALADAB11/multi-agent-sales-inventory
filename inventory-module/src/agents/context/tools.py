"""
Context Agent Tools
====================
Data fetching and pattern extraction for the context agent.

Responsibilities:
  - Historical demand uplift patterns from sales_history.csv (category-level)
  - Active/upcoming promotions from inv.promotions DB (DB is source of truth)
  - 7-day weather forecast via Open-Meteo (module-level cached)
  - Upcoming public holidays via Nager.Date (module-level cached)
  - Upcoming events from inv.events (DB)
  - Product category lookup (DB first, CSV fallback via get_product)

No LLM calls. No metric computation.
"""

import json
import logging
import requests
import pandas as pd
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

import sys
sys.path.append(str(Path(__file__).resolve().parents[4]))

from config.settings import SALES_HISTORY_PATH, DEFAULT_STORE
from src.tools.internal.stock_tools import _DataCache, get_product

try:
    from db.repositories.inventory_repo import SyncInventoryRepo
    _DB_AVAILABLE = True
except Exception:
    _DB_AVAILABLE = False
    logger.warning("SyncInventoryRepo not importable — DB reads disabled.")


# ═══════════════════════════════════════════════════════════════════════════
# Store Locations
# ═══════════════════════════════════════════════════════════════════════════

STORE_LOCATIONS: Dict[str, Dict[str, Any]] = {
    # Add entries as stores are onboarded:
    # "S02": {"lat": 36.4561, "lon": 10.7382, "country": "TN",
    #         "timezone": "Africa/Tunis", "city": "Sousse"},
    "_default": {
        "lat": 36.8065,
        "lon": 10.1815,
        "country": "TN",
        "timezone": "Africa/Tunis",
        "city": "Tunis",
    },
}


def _store_location(store_id: str) -> Dict[str, Any]:
    return STORE_LOCATIONS.get(str(store_id), STORE_LOCATIONS["_default"])


# ═══════════════════════════════════════════════════════════════════════════
# WMO Weather Code → Human Label
# ═══════════════════════════════════════════════════════════════════════════

_WMO_LABELS: Dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Icy fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Rain showers", 81: "Heavy rain showers", 82: "Violent showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail", 99: "Thunderstorm, heavy hail",
}


def _wmo_label(code: int) -> str:
    return _WMO_LABELS.get(code, f"Weather code {code}")


# ═══════════════════════════════════════════════════════════════════════════
# Module-Level Caches (one fetch per process per key)
# ═══════════════════════════════════════════════════════════════════════════

_weather_cache: Dict[str, Dict[str, Any]] = {}     # keyed by store_id
_holiday_cache: Dict[str, List[dict]] = {}          # keyed by "COUNTRY-YEAR"


# ═══════════════════════════════════════════════════════════════════════════
# Historical Demand Patterns
# ═══════════════════════════════════════════════════════════════════════════

def get_historical_patterns(category: str, store_id: str) -> Dict[str, Any]:
    """
    Compute demand uplift patterns for a product category from sales_history.csv.

    Uses category-level data (not SKU-level) because individual SKU event
    history is often too thin to be statistically meaningful.

    Baseline = rows where is_promo=False AND event_name is null/empty.
    Uplift = (avg_during_period - baseline) / baseline * 100.

    Falls back to all-store data if the specific store has thin history.

    Returns:
        {
            "baseline_avg_qty": float,
            "category": str,
            "sample_size": int,
            "by_event_type": {event_type: {avg_qty, baseline_qty, uplift_pct, sample_count}},
            "by_promo":      {promo_type:  {avg_qty, baseline_qty, uplift_pct, sample_count}},
            "by_season":     {season:      {avg_qty, baseline_qty, uplift_pct, sample_count}},
        }
    """
    df = _DataCache.sales().copy()

    # Category + store filter first, fall back to all stores if thin
    cat_store = df[(df["category"] == category) & (df["store_id"] == str(store_id))]
    cat_df = cat_store.copy() if len(cat_store) >= 30 else df[df["category"] == category].copy()

    if len(cat_df) < len(cat_store):
        logger.debug(
            "Category=%s: only %d rows at store=%s, using all-store data (%d rows)",
            category, len(cat_store), store_id, len(cat_df)
        )

    if cat_df.empty:
        return _empty_patterns(category)

    # ── Baseline ───────────────────────────────────────────────────────────
    has_promo = "is_promo" in cat_df.columns
    has_event_name = "event_name" in cat_df.columns

    if has_promo and has_event_name:
        normal_mask = (
            (cat_df["is_promo"] == False) &
            (cat_df["event_name"].isna() | (cat_df["event_name"].str.strip() == ""))
        )
    elif has_promo:
        normal_mask = cat_df["is_promo"] == False
    else:
        normal_mask = pd.Series(True, index=cat_df.index)

    normal_rows = cat_df[normal_mask]
    baseline_qty = (
        float(normal_rows["quantity_sold"].mean())
        if not normal_rows.empty
        else float(cat_df["quantity_sold"].mean())
    )
    if baseline_qty == 0:
        baseline_qty = float(cat_df["quantity_sold"].mean())

    def _uplift(avg: float) -> float:
        return round((avg - baseline_qty) / baseline_qty * 100, 1) if baseline_qty > 0 else 0.0

    # ── By event_type ──────────────────────────────────────────────────────
    by_event_type: Dict[str, Any] = {}
    if "event_type" in cat_df.columns:
        event_rows = cat_df[cat_df["event_type"].notna() & (cat_df["event_type"].str.strip() != "")]
        for evt, grp in event_rows.groupby("event_type"):
            avg = float(grp["quantity_sold"].mean())
            by_event_type[str(evt)] = {
                "avg_qty": round(avg, 2),
                "baseline_qty": round(baseline_qty, 2),
                "uplift_pct": _uplift(avg),
                "sample_count": len(grp),
            }

    # ── By promo type ──────────────────────────────────────────────────────
    by_promo: Dict[str, Any] = {}
    if has_promo:
        promo_rows = cat_df[cat_df["is_promo"] == True]
        group_col = "promo_type" if "promo_type" in cat_df.columns else None
        if group_col and not promo_rows.empty:
            for ptype, grp in promo_rows.groupby(group_col):
                if str(ptype).strip():
                    avg = float(grp["quantity_sold"].mean())
                    by_promo[str(ptype)] = {
                        "avg_qty": round(avg, 2),
                        "baseline_qty": round(baseline_qty, 2),
                        "uplift_pct": _uplift(avg),
                        "sample_count": len(grp),
                    }
        elif not promo_rows.empty:
            avg = float(promo_rows["quantity_sold"].mean())
            by_promo["promotion"] = {
                "avg_qty": round(avg, 2),
                "baseline_qty": round(baseline_qty, 2),
                "uplift_pct": _uplift(avg),
                "sample_count": len(promo_rows),
            }

    # ── By season ──────────────────────────────────────────────────────────
    by_season: Dict[str, Any] = {}
    if "season" in cat_df.columns:
        for season, grp in cat_df.groupby("season"):
            s = str(season).strip()
            if s and s not in ("nan", "None", ""):
                avg = float(grp["quantity_sold"].mean())
                by_season[s] = {
                    "avg_qty": round(avg, 2),
                    "baseline_qty": round(baseline_qty, 2),
                    "uplift_pct": _uplift(avg),
                    "sample_count": len(grp),
                }

    return {
        "baseline_avg_qty": round(baseline_qty, 2),
        "category": category,
        "sample_size": len(cat_df),
        "by_event_type": by_event_type,
        "by_promo": by_promo,
        "by_season": by_season,
    }


def _empty_patterns(category: str) -> Dict[str, Any]:
    return {
        "baseline_avg_qty": 0.0,
        "category": category,
        "sample_size": 0,
        "by_event_type": {},
        "by_promo": {},
        "by_season": {},
    }


# ═══════════════════════════════════════════════════════════════════════════
# Active / Upcoming Promotions  (DB-first — DB is source of truth for current state)
# ═══════════════════════════════════════════════════════════════════════════

def _sync_get_promotions(sku: str, category: str) -> List[dict]:
    """
    Sync DB query mirroring the async get_active_promotions / get_promotions_for_sku
    methods in the repo. Returns rows active across the 7-day horizon.

    Matches: sku, OR category, OR scope = store/all_stores/all.
    Window:  start_date ≤ today+7  AND  end_date ≥ today.
    """
    today   = date.today()
    horizon = today + timedelta(days=7)

    try:
        import psycopg2.extras
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return []

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT promo_id, promo_name, promo_type, discount_pct,
                       scope, category, sku, start_date, end_date
                FROM inv.promotions
                WHERE start_date <= %s
                  AND end_date   >= %s
                  AND (
                      sku      = %s
                   OR category = %s
                   OR LOWER(scope) IN ('store', 'all_stores', 'all')
                  )
                ORDER BY discount_pct DESC
            """, (horizon, today, str(sku), str(category)))
            rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    except Exception as e:
        logger.warning("_sync_get_promotions DB query failed: %s", e)
        return []


def get_active_promotions(sku: str, category: str, store_id: str) -> List[dict]:
    """
    Return promotions active or starting within the next 7 days relevant to this SKU.

    DB is source of truth for current/future promotions — a manager may have
    entered or modified a promo today and the CSV snapshot won't reflect it.
    CSV (promotions.csv) is historical only — not used here.

    Falls back to empty list if DB unavailable (context agent continues
    without promotions signal, confidence degrades to medium/low).
    """
    if not _DB_AVAILABLE:
        logger.debug("DB unavailable — promotions signal skipped for %s", sku)
        return []

    today = date.today()
    rows  = _sync_get_promotions(sku, category)

    results = []
    for row in rows:
        start = row["start_date"]
        end   = row["end_date"]
        # Normalise: psycopg2 returns date objects, handle both date and str
        if isinstance(start, str):
            start = date.fromisoformat(start)
        if isinstance(end, str):
            end = date.fromisoformat(end)

        results.append({
            "promo_id":    str(row.get("promo_id", "")),
            "promo_name":  str(row.get("promo_name", "")),
            "promo_type":  str(row.get("promo_type", "")),
            "discount_pct": float(row.get("discount_pct", 0.0)),
            "scope":       str(row.get("scope", "")),
            "category":    str(row.get("category", "")),
            "start_date":  start.isoformat(),
            "end_date":    end.isoformat(),
            "days_active": max(0, (end - today).days + 1),
        })

    return results


# ═══════════════════════════════════════════════════════════════════════════
# Weather Forecast  (Open-Meteo, module-level cached)
# ═══════════════════════════════════════════════════════════════════════════

def get_weather(store_id: str) -> Dict[str, Any]:
    """
    Fetch 7-day weather forecast for the store's location via Open-Meteo.
    Cached at module level — one API call per store per process.
    Falls back gracefully to an empty-signal dict on any failure.
    """
    global _weather_cache
    store_id = str(store_id)

    if store_id in _weather_cache:
        return _weather_cache[store_id]

    loc = _store_location(store_id)

    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":  loc["lat"],
                "longitude": loc["lon"],
                "daily": ",".join([
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_sum",
                    "weathercode",
                ]),
                "timezone":     loc["timezone"],
                "forecast_days": 7,
            },
            timeout=8,
        )
        resp.raise_for_status()
        raw = resp.json().get("daily", {})

        dates     = raw.get("time", [])
        temp_max  = raw.get("temperature_2m_max", [])
        temp_min  = raw.get("temperature_2m_min", [])
        precip    = raw.get("precipitation_sum", [])
        codes     = raw.get("weathercode", [])

        days = []
        for i, d in enumerate(dates):
            t_max  = float(temp_max[i])  if i < len(temp_max)  and temp_max[i]  is not None else None
            t_min  = float(temp_min[i])  if i < len(temp_min)  and temp_min[i]  is not None else None
            rain   = float(precip[i])    if i < len(precip)    and precip[i]    is not None else 0.0
            code   = int(codes[i])       if i < len(codes)     and codes[i]     is not None else 0
            days.append({
                "date":             d,
                "temp_max":         t_max,
                "temp_min":         t_min,
                "precipitation_mm": round(rain, 1),
                "weather_label":    _wmo_label(code),
                "is_bad_weather":   rain > 5.0,
            })

        valid_temps  = [d["temp_max"] for d in days if d["temp_max"] is not None]
        avg_temp_max = round(sum(valid_temps) / len(valid_temps), 1) if valid_temps else None
        total_precip = round(sum(d["precipitation_mm"] for d in days), 1)
        bad_days     = sum(1 for d in days if d["is_bad_weather"])

        if bad_days >= 4:
            summary = f"Mostly wet — {bad_days}/7 days with heavy precipitation"
        elif bad_days >= 2:
            summary = f"Mixed — {bad_days} days heavy rain expected"
        elif avg_temp_max and avg_temp_max > 35:
            summary = f"Very hot — avg max {avg_temp_max}°C, minimal rain"
        elif avg_temp_max and avg_temp_max < 12:
            summary = f"Cold — avg max {avg_temp_max}°C"
        else:
            t_str = f"{avg_temp_max}°C" if avg_temp_max else "N/A"
            summary = f"Normal — avg max {t_str}, {total_precip}mm total rain"

        result = {
            "summary":          summary,
            "avg_temp_max":     avg_temp_max,
            "total_precip_mm":  total_precip,
            "bad_weather_days": bad_days,
            "days":             days,
            "city":             loc.get("city", "Unknown"),
            "source":           "open-meteo",
        }

    except Exception as e:
        logger.warning("Weather API failed for store=%s: %s", store_id, e)
        result = {
            "summary":          "Weather data unavailable",
            "avg_temp_max":     None,
            "total_precip_mm":  None,
            "bad_weather_days": 0,
            "days":             [],
            "city":             loc.get("city", "Unknown"),
            "source":           "unavailable",
        }

    _weather_cache[store_id] = result
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Public Holidays  (Nager.Date, module-level cached)
# ═══════════════════════════════════════════════════════════════════════════

def get_upcoming_holidays(country: str = "TN", days_ahead: int = 7) -> List[dict]:
    """
    Fetch public holidays for the country in the current year, returning
    only those within the next `days_ahead` days.
    Cached at module level — one API call per country-year per process.
    Falls back gracefully to empty list.
    """
    global _holiday_cache
    today   = date.today()
    horizon = today + timedelta(days=days_ahead)
    year    = today.year
    key     = f"{country}-{year}"

    if key not in _holiday_cache:
        try:
            resp = requests.get(
                f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country}",
                timeout=8,
            )
            resp.raise_for_status()
            _holiday_cache[key] = resp.json()
        except Exception as e:
            logger.warning("Holiday API failed for %s/%d: %s", country, year, e)
            _holiday_cache[key] = []

    upcoming = []
    for h in _holiday_cache[key]:
        try:
            h_date = date.fromisoformat(h["date"])
        except (KeyError, ValueError):
            continue
        if today <= h_date <= horizon:
            upcoming.append({
                "date":        h["date"],
                "local_name":  h.get("localName", ""),
                "name":        h.get("name", ""),
                "is_national": h.get("global", True),
                "days_away":   (h_date - today).days,
            })

    upcoming.sort(key=lambda x: x["days_away"])
    return upcoming


# ═══════════════════════════════════════════════════════════════════════════
# Upcoming Events from DB
# ═══════════════════════════════════════════════════════════════════════════

def get_upcoming_events(category: str, days_ahead: int = 7) -> List[dict]:
    """
    Fetch current and near-future events from inv.events that affect
    this product category or are national/all-scope.

    Falls back gracefully to empty list if DB is unavailable.
    """
    if not _DB_AVAILABLE:
        return []

    today   = date.today()
    horizon = today + timedelta(days=days_ahead)

    try:
        import psycopg2.extras
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return []

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT event_name, event_type, start_date, end_date,
                       affected_categories, estimated_uplift_pct, scope
                FROM inv.events
                WHERE start_date <= %s
                  AND end_date   >= %s
                ORDER BY start_date ASC
            """, (horizon, today))
            rows = cur.fetchall()
        conn.close()

    except Exception as e:
        logger.warning("get_upcoming_events DB query failed: %s", e)
        return []

    events = []
    for row in rows:
        r = dict(row)

        # Deserialise affected_categories (stored as JSONB → already a list from psycopg2,
        # but may arrive as a JSON string if column is TEXT)
        affected = r.get("affected_categories") or []
        if isinstance(affected, str):
            try:
                affected = json.loads(affected)
            except Exception:
                affected = []

        scope = str(r.get("scope", "national")).lower()

        # Include if: national/all scope, no category filter, or category matched
        if scope in ("national", "all") or not affected or category in affected:
            start = r["start_date"]
            events.append({
                "event_name":          r.get("event_name", ""),
                "event_type":          r.get("event_type", ""),
                "start_date":          str(start),
                "end_date":            str(r.get("end_date", "")),
                "estimated_uplift_pct": r.get("estimated_uplift_pct"),
                "scope":               scope,
                "days_away":           max(0, (start - today).days) if isinstance(start, date) else 0,
            })

    return events


# ═══════════════════════════════════════════════════════════════════════════
# Product Category Lookup
# ═══════════════════════════════════════════════════════════════════════════

def get_product_category(sku: str) -> str:
    """
    Get product category for a SKU — DB first, CSV fallback via get_product().
    Returns "unknown" if not found.
    """
    product = get_product(str(sku))
    if product and product.get("category"):
        return str(product["category"])
    return "unknown"