"""
Context Agent Tools — 100% PostgreSQL.
Toutes les données lues depuis la base ooredoo_sales.
Aucun CSV, aucune donnée hardcodée.

Sources :
  - market.seasonal_patterns  → uplift par catégorie/mois/jour
  - market.events             → événements actifs et à venir
  - inventory.promotions      → promotions actives
  - inventory.sales_history   → historique ventes pour patterns
  - supply.suppliers          → informations fournisseurs
"""

import logging
import os
import socket
import requests
import urllib3.util.connection as _urllib3_conn
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)


def _requests_get_ipv4_only(url: str, **kwargs) -> requests.Response:
    """
    requests.get() with IPv6 addresses excluded from DNS resolution for the
    duration of this call only.

    Some Windows networks resolve a working IPv4 address AND an IPv6 address
    for a host, but have no real IPv6 route — urllib3 then tries the IPv6
    address first and fails with WSAENETUNREACH instead of falling back,
    even though the site is reachable over IPv4. Forcing AF_INET here avoids
    that failure mode for hosts affected by it (seen on date.nager.at).
    """
    original_family = _urllib3_conn.allowed_gai_family
    _urllib3_conn.allowed_gai_family = lambda: socket.AF_INET
    try:
        return requests.get(url, **kwargs)
    finally:
        _urllib3_conn.allowed_gai_family = original_family

_DB_CONFIG = {
    "host":     os.getenv("POSTGRES_HOST", "localhost"),
    "port":     int(os.getenv("POSTGRES_PORT", "5432")),
    "dbname":   os.getenv("POSTGRES_DB", "ooredoo_sales"),
    "user":     os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", "admin"),
}

_WMO_LABELS: Dict[int, str] = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
    80: "Rain showers", 95: "Thunderstorm",
}

_weather_cache: Dict[str, Dict[str, Any]] = {}
_holiday_cache: Dict[str, List[dict]] = {}

# The batch pipeline fans out to several worker threads (see Orchestrator
# workers=8) that each call get_upcoming_holidays() for the same store/year.
# Without a lock they all miss the cache at once and fire the same request
# to date.nager.at in parallel, multiplying the odds of a timeout. One lock
# per cache key makes the other threads wait for the first fetch instead of
# each doing their own network call.
import threading as _threading
_holiday_cache_locks: Dict[str, _threading.Lock] = {}
_holiday_cache_locks_guard = _threading.Lock()


def _get_holiday_cache_lock(key: str) -> _threading.Lock:
    with _holiday_cache_locks_guard:
        if key not in _holiday_cache_locks:
            _holiday_cache_locks[key] = _threading.Lock()
        return _holiday_cache_locks[key]


def _get_conn():
    return psycopg2.connect(**_DB_CONFIG, connect_timeout=8)


def _wmo_label(code: int) -> str:
    return _WMO_LABELS.get(code, f"Weather code {code}")


def _store_location(store_id: str) -> Dict[str, Any]:
    """Lit latitude/longitude depuis sales.boutiques."""
    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT latitude, longitude, ville FROM sales.boutiques WHERE store_id = %s",
                (str(store_id),)
            )
            row = cur.fetchone()
        conn.close()
        if row and row["latitude"] and row["longitude"]:
            return {
                "lat":      float(row["latitude"]),
                "lon":      float(row["longitude"]),
                "country":  "TN",
                "timezone": "Africa/Tunis",
                "city":     row["ville"] or str(store_id),
            }
    except Exception as e:
        logger.warning("_store_location DB failed for %s: %s", store_id, e)
    return {"lat": 36.8065, "lon": 10.1815, "country": "TN",
            "timezone": "Africa/Tunis", "city": "Tunis"}


# ═══════════════════════════════════════════════════════════════════════════
# Historical Demand Patterns — depuis market.seasonal_patterns + sales_history
# ═══════════════════════════════════════════════════════════════════════════

_CAT_CODE_TO_NAME: Dict[str, str] = {
    "50": "TERMINAL", "70": "AUTRE",
    "20": "SIM",      "40": "SIM",
    "88": "FORFAIT",  "80": "FORFAIT",
    "30": "RECHARGE", "32": "RECHARGE",
    "90": "AUTRE",    "99": "AUTRE",
    "10": "AUTRE",    "TELECOM": "AUTRE",
}


def get_historical_patterns(category: str, store_id: str) -> Dict[str, Any]:
    """
    Uplift patterns lus depuis market.seasonal_patterns (table PostgreSQL).
    Complété par inventory.sales_history pour les uplifts event/promo réels.
    """
    today = date.today()
    # sales_history.category stores text names (FORFAIT, SIM, TERMINAL…)
    # but sales.produits.categorie stores numeric codes (88, 50, 20…).
    # Normalise before querying so category='88' → 'FORFAIT' gets a hit.
    category = _CAT_CODE_TO_NAME.get(str(category), str(category))
    result: Dict[str, Any] = {
        "baseline_avg_qty": 0.0,
        "category":         category,
        "sample_size":      0,
        "by_event_type":    {},
        "by_promo":         {},
        "by_season":        {},
        "seasonal_current_month": {},
    }

    try:
        conn = _get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # ── Baseline : ventes moyennes hors events/promos (30j) ────────
                cur.execute("""
                    SELECT AVG(quantity_sold) AS baseline, COUNT(*) AS n
                    FROM inventory.sales_history
                    WHERE category = %s AND store_id = %s
                      AND is_promo = FALSE
                      AND (event_name IS NULL OR event_name = '')
                      AND record_date >= CURRENT_DATE - INTERVAL '90 days'
                """, (category, str(store_id)))
                row = cur.fetchone()
                if row and row["n"] and int(row["n"]) >= 5:
                    result["baseline_avg_qty"] = round(float(row["baseline"] or 0), 2)
                    result["sample_size"]       = int(row["n"])
                else:
                    cur.execute("""
                        SELECT AVG(quantity_sold) AS baseline, COUNT(*) AS n
                        FROM inventory.sales_history
                        WHERE category = %s AND is_promo = FALSE
                          AND (event_name IS NULL OR event_name = '')
                          AND record_date >= CURRENT_DATE - INTERVAL '90 days'
                    """, (category,))
                    row = cur.fetchone()
                    result["baseline_avg_qty"] = round(float(row["baseline"] or 0), 2) if row else 0.0
                    result["sample_size"]       = int(row["n"] or 0) if row else 0

                baseline = result["baseline_avg_qty"] or 1.0

                # ── By event_type depuis sales_history ─────────────────────────
                cur.execute("""
                    SELECT event_type, AVG(quantity_sold) AS avg_qty, COUNT(*) AS n
                    FROM inventory.sales_history
                    WHERE category = %s
                      AND event_type IS NOT NULL AND event_type != ''
                      AND record_date >= CURRENT_DATE - INTERVAL '365 days'
                    GROUP BY event_type
                    HAVING COUNT(*) >= 3
                """, (category,))
                for r in cur.fetchall():
                    avg = float(r["avg_qty"] or 0)
                    result["by_event_type"][str(r["event_type"])] = {
                        "avg_qty":       round(avg, 2),
                        "baseline_qty":  round(baseline, 2),
                        "uplift_pct":    round((avg - baseline) / baseline * 100, 1) if baseline else 0.0,
                        "sample_count":  int(r["n"]),
                    }

                # ── By promo ───────────────────────────────────────────────────
                cur.execute("""
                    SELECT COALESCE(promo_type, 'promotion') AS promo_type,
                           AVG(quantity_sold) AS avg_qty, COUNT(*) AS n
                    FROM inventory.sales_history
                    WHERE category = %s AND is_promo = TRUE
                      AND record_date >= CURRENT_DATE - INTERVAL '365 days'
                    GROUP BY COALESCE(promo_type, 'promotion')
                    HAVING COUNT(*) >= 3
                """, (category,))
                for r in cur.fetchall():
                    avg = float(r["avg_qty"] or 0)
                    result["by_promo"][str(r["promo_type"])] = {
                        "avg_qty":       round(avg, 2),
                        "baseline_qty":  round(baseline, 2),
                        "uplift_pct":    round((avg - baseline) / baseline * 100, 1) if baseline else 0.0,
                        "sample_count":  int(r["n"]),
                    }

                # ── By season ──────────────────────────────────────────────────
                cur.execute("""
                    SELECT season, AVG(quantity_sold) AS avg_qty, COUNT(*) AS n
                    FROM inventory.sales_history
                    WHERE category = %s
                      AND season IS NOT NULL AND season != ''
                      AND record_date >= CURRENT_DATE - INTERVAL '365 days'
                    GROUP BY season
                    HAVING COUNT(*) >= 5
                """, (category,))
                for r in cur.fetchall():
                    avg = float(r["avg_qty"] or 0)
                    result["by_season"][str(r["season"])] = {
                        "avg_qty":       round(avg, 2),
                        "baseline_qty":  round(baseline, 2),
                        "uplift_pct":    round((avg - baseline) / baseline * 100, 1) if baseline else 0.0,
                        "sample_count":  int(r["n"]),
                    }

                # ── Saisonnalité mois courant depuis market.seasonal_patterns ──
                cur.execute("""
                    SELECT mois, jour_semaine, facteur_demande, confidence, notes
                    FROM market.seasonal_patterns
                    WHERE categorie = %s
                      AND mois = EXTRACT(MONTH FROM CURRENT_DATE)::INTEGER
                    ORDER BY facteur_demande DESC
                """, (category.upper(),))
                sp_rows = cur.fetchall()
                if sp_rows:
                    best = sp_rows[0]
                    result["seasonal_current_month"] = {
                        "facteur_max": float(best["facteur_demande"]),
                        "confidence":  best["confidence"],
                        "notes":       best["notes"] or "",
                        "par_jour":    {
                            str(r["jour_semaine"]): float(r["facteur_demande"])
                            for r in sp_rows if r["jour_semaine"] is not None
                        },
                    }

        finally:
            conn.close()

    except Exception as e:
        logger.warning("get_historical_patterns DB failed category=%s: %s", category, e)

    return result


# ═══════════════════════════════════════════════════════════════════════════
# Active / Upcoming Promotions — inventory.promotions
# ═══════════════════════════════════════════════════════════════════════════

def get_active_promotions(sku: str, category: str, store_id: str) -> List[dict]:
    """Promotions actives ou démarrant dans les 7 prochains jours depuis inventory.promotions."""
    today   = date.today()
    horizon = today + timedelta(days=7)
    try:
        conn = _get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT promo_id, promo_name, promo_type, discount_pct,
                           scope, category, sku, start_date, end_date
                    FROM inventory.promotions
                    WHERE start_date <= %s AND end_date >= %s
                      AND (sku = %s OR category = %s OR LOWER(scope) IN ('store','all_stores','all'))
                    ORDER BY discount_pct DESC
                """, (horizon, today, str(sku), str(category)))
                rows = cur.fetchall()
        finally:
            conn.close()
        results = []
        for row in rows:
            start = row["start_date"]
            end   = row["end_date"]
            if isinstance(start, str):
                start = date.fromisoformat(start)
            if isinstance(end, str):
                end = date.fromisoformat(end)
            results.append({
                "promo_id":    str(row.get("promo_id", "")),
                "promo_name":  str(row.get("promo_name", "")),
                "promo_type":  str(row.get("promo_type", "")),
                "discount_pct": float(row.get("discount_pct") or 0.0),
                "scope":       str(row.get("scope", "")),
                "category":    str(row.get("category", "")),
                "start_date":  start.isoformat(),
                "end_date":    end.isoformat(),
                "days_active": max(0, (end - today).days + 1),
            })
        return results
    except Exception as e:
        logger.warning("get_active_promotions DB failed sku=%s: %s", sku, e)
        return []


# ═══════════════════════════════════════════════════════════════════════════
# Weather Forecast — Open-Meteo
# ═══════════════════════════════════════════════════════════════════════════

def get_weather(store_id: str) -> Dict[str, Any]:
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
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
                "timezone":      loc["timezone"],
                "forecast_days": 7,
            },
            timeout=8,
        )
        resp.raise_for_status()
        raw = resp.json().get("daily", {})
        dates    = raw.get("time", [])
        temp_max = raw.get("temperature_2m_max", [])
        temp_min = raw.get("temperature_2m_min", [])
        precip   = raw.get("precipitation_sum", [])
        codes    = raw.get("weathercode", [])
        days = []
        for i, d in enumerate(dates):
            t_max = float(temp_max[i]) if i < len(temp_max) and temp_max[i] is not None else None
            t_min = float(temp_min[i]) if i < len(temp_min) and temp_min[i] is not None else None
            rain  = float(precip[i])   if i < len(precip)   and precip[i]   is not None else 0.0
            code  = int(codes[i])      if i < len(codes)     and codes[i]    is not None else 0
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
        t_str = f"{avg_temp_max}°C" if avg_temp_max else "N/A"
        summary = (
            f"Mostly wet — {bad_days}/7 days heavy rain" if bad_days >= 4 else
            f"Mixed — {bad_days} days heavy rain" if bad_days >= 2 else
            f"Very hot — avg max {t_str}" if avg_temp_max and avg_temp_max > 35 else
            f"Normal — avg max {t_str}, {total_precip}mm"
        )
        result = {
            "summary": summary,
            "avg_temp_max": avg_temp_max,
            "total_precip_mm": total_precip,
            "bad_weather_days": bad_days,
            "days": days,
            "city": loc.get("city", "Tunis"),
            "source": "open-meteo",
        }
    except Exception as e:
        logger.warning("Weather API failed store=%s: %s", store_id, e)
        result = {
            "summary": "Weather data unavailable", "avg_temp_max": None,
            "total_precip_mm": None, "bad_weather_days": 0,
            "days": [], "city": loc.get("city", "Tunis"), "source": "unavailable",
        }
    _weather_cache[store_id] = result
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Upcoming Holidays — Nager.Date API
# ═══════════════════════════════════════════════════════════════════════════

def get_upcoming_holidays(country: str = "TN", days_ahead: int = 7) -> List[dict]:
    global _holiday_cache
    today   = date.today()
    horizon = today + timedelta(days=days_ahead)
    year    = today.year
    key     = f"{country}-{year}"
    if key not in _holiday_cache:
        with _get_holiday_cache_lock(key):
            # Re-check inside the lock — another thread may have already
            # populated the cache while this one was waiting.
            if key not in _holiday_cache:
                try:
                    resp = _requests_get_ipv4_only(
                        f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country}", timeout=8
                    )
                    resp.raise_for_status()
                    _holiday_cache[key] = resp.json()
                except Exception as e:
                    logger.warning("Holiday API failed %s/%d: %s", country, year, e)
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
# Upcoming Events — market.events (PostgreSQL)
# ═══════════════════════════════════════════════════════════════════════════

def get_upcoming_events(category: str, days_ahead: int = 7) -> List[dict]:
    """
    Événements depuis market.events avec uplift par catégorie.
    Retourne aussi les events nationaux sans filtre catégorie.
    """
    today   = date.today()
    horizon = today + timedelta(days=days_ahead)
    try:
        conn = _get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT event_name, event_type, sous_type, start_date, end_date,
                           scope, intensite,
                           uplift_terminal, uplift_forfait, uplift_sim,
                           uplift_recharge, uplift_accessoire, note_strategie
                    FROM market.events
                    WHERE start_date <= %s AND end_date >= %s
                    ORDER BY start_date ASC
                """, (horizon, today))
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("get_upcoming_events DB failed category=%s: %s", category, e)
        return []

    cat_upper = category.upper()
    uplift_col = {
        "TERMINAL":   "uplift_terminal",
        "FORFAIT":    "uplift_forfait",
        "SIM":        "uplift_sim",
        "RECHARGE":   "uplift_recharge",
        "ACCESSOIRE": "uplift_accessoire",
    }.get(cat_upper, None)

    events = []
    for row in rows:
        start = row["start_date"]
        uplift_pct = None
        if uplift_col:
            uplift_pct = float(row[uplift_col] or 0)
        events.append({
            "event_name":           row.get("event_name", ""),
            "event_type":           row.get("event_type", ""),
            "sous_type":            row.get("sous_type", ""),
            "start_date":           str(start),
            "end_date":             str(row.get("end_date", "")),
            "intensite":            row.get("intensite", "MEDIUM"),
            "estimated_uplift_pct": uplift_pct,
            "scope":                str(row.get("scope", "national")).lower(),
            "note_strategie":       row.get("note_strategie", ""),
            "days_away":            max(0, (start - today).days) if isinstance(start, date) else 0,
        })
    return events


# ═══════════════════════════════════════════════════════════════════════════
# Product Category Lookup — sales.produits (PostgreSQL)
# ═══════════════════════════════════════════════════════════════════════════

def get_product_category(sku: str) -> str:
    """Catégorie produit depuis sales.produits."""
    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT categorie FROM sales.produits WHERE sku = %s", (int(sku),))
                row = cur.fetchone()
        finally:
            conn.close()
        if row and row[0]:
            return str(row[0])
    except Exception as e:
        logger.warning("get_product_category DB failed sku=%s: %s", sku, e)
    return "unknown"
