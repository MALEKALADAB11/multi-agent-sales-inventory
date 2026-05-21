"""
Tools de l'Agent Stratège — contexte externe.
Version sans scraper web (timeout réseau).
Météo : Open-Meteo (gratuit, fiable)
Fériés : Nager.Date API
Promos : cache statique Ooredoo (mise à jour manuelle)
"""
import asyncio
import logging
from datetime import datetime, date
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Coordonnées boutiques ─────────────────────────────────────────────────────
STORE_COORDS = {
    "OOR_LAC_01":    {"lat": 36.8065, "lon": 10.1815, "city": "Tunis Lac"},
    "OOR_MENZAH_02": {"lat": 36.8448, "lon": 10.1942, "city": "Menzah"},
    "OOR_SFAX_03":   {"lat": 34.7406, "lon": 10.7603, "city": "Sfax"},
    "I63":           {"lat": 36.8065, "lon": 10.1815, "city": "Tunis Lac"},
}

WEATHER_CODES = {
    0:  {"label": "Ciel dégagé",           "icon": "☀️",  "effect": +0.10},
    1:  {"label": "Peu nuageux",            "icon": "🌤️",  "effect": +0.05},
    2:  {"label": "Partiellement nuageux",  "icon": "⛅",  "effect":  0.00},
    3:  {"label": "Couvert",               "icon": "☁️",  "effect": -0.05},
    45: {"label": "Brouillard",            "icon": "🌫️",  "effect": -0.10},
    51: {"label": "Bruine légère",         "icon": "🌦️",  "effect": -0.15},
    61: {"label": "Pluie légère",          "icon": "🌧️",  "effect": -0.20},
    63: {"label": "Pluie modérée",         "icon": "🌧️",  "effect": -0.25},
    65: {"label": "Pluie forte",           "icon": "⛈️",  "effect": -0.35},
    80: {"label": "Averses",              "icon": "🌦️",  "effect": -0.20},
    95: {"label": "Orage",               "icon": "⛈️",  "effect": -0.40},
}

# ── Promotions Ooredoo statiques (mise à jour manuelle) ──────────────────────
STATIC_PROMOTIONS = [
    {"title": "Forfait 5G Max — Offre découverte", "price": "49 DT/mois",
     "type": "forfait", "discount": "10%"},
    {"title": "iPhone 16 Pro — Avance postpayé",   "price": "1299 DT",
     "type": "terminal", "discount": "0%"},
    {"title": "Box Fibre 1Go — Installation offerte", "price": "59 DT/mois",
     "type": "fibre", "discount": "installation"},
]

STATIC_NEW_OFFERS = [
    {"title": "Samsung Galaxy S25 Ultra disponible", "price": "1599 DT"},
    {"title": "AirPods Pro 3 en stock",              "price": "279 DT"},
]

# ── Cache météo global ────────────────────────────────────────────────────────
_weather_cache: dict = {}
_weather_cache_time: float = 0.0
_WEATHER_TTL = 300  # 5 minutes


async def fetch_weather(store_id: str) -> dict:
    """Météo depuis Open-Meteo (gratuit, sans clé)."""
    import time
    global _weather_cache, _weather_cache_time

    now = time.time()
    cache_key = store_id
    if (
        _weather_cache.get(cache_key)
        and (now - _weather_cache_time) < _WEATHER_TTL
    ):
        return _weather_cache[cache_key]

    coords = STORE_COORDS.get(store_id, STORE_COORDS["OOR_LAC_01"])
    lat, lon = coords["lat"], coords["lon"]

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude":   lat,
                    "longitude":  lon,
                    "current":    "temperature_2m,precipitation,weathercode,windspeed_10m",
                    "hourly":     "temperature_2m,precipitation_probability,weathercode",
                    "forecast_days": 1,
                    "timezone":   "Africa/Tunis",
                },
            )
            data    = resp.json()
            current = data.get("current", {})
            hourly  = data.get("hourly", {})

            code  = current.get("weathercode", 1)
            temp  = current.get("temperature_2m", 22)
            rain  = current.get("precipitation", 0)
            wind  = current.get("windspeed_10m", 0)

            w     = WEATHER_CODES.get(code, {"label":"Variable","icon":"⛅","effect":0.0})
            hour  = datetime.now().hour

            # Heures à risque (forte prob. pluie)
            rain_probs = hourly.get("precipitation_probability", [])
            rain_hours = []
            if rain_probs:
                for i, prob in enumerate(rain_probs[:24]):
                    if prob and prob > 60:
                        h = (hour + i) % 24
                        rain_hours.append(h)

            result = {
                "current": {
                    "weathercode":  code,
                    "temperature":  temp,
                    "precipitation": rain,
                    "windspeed":    wind,
                },
                "hourly": hourly,
            }

            summary = {
                "weather_label":  w["label"],
                "weather_icon":   w["icon"],
                "weather_effect": w["effect"],
                "temperature":    temp,
                "precipitation":  rain,
                "is_rainy":       code >= 61,
                "is_sunny":       code <= 1,
                "rain_hours":     rain_hours[:3],
                "best_hours":     [16, 17, 19] if code <= 2 else [11, 12],
                "is_holiday":     False,
                "active_promos":  len(STATIC_PROMOTIONS),
            }

            _weather_cache[cache_key] = {"current": result["current"],
                                          "hourly": hourly, "summary": summary}
            _weather_cache_time = now

            logger.info(
                f"[STRATEGE] Météo {coords['city']}: "
                f"{w['icon']} {w['label']} | "
                f"Effet trafic: {w['effect']:+.0%}"
            )
            return _weather_cache[cache_key]

    except Exception as e:
        logger.warning(f"[STRATEGE] Météo fallback: {e}")
        return {
            "current": {"weathercode":1,"temperature":22,"precipitation":0,"windspeed":0},
            "hourly":  {},
            "summary": {
                "weather_label":"Tunis Lac","weather_icon":"🌤️",
                "weather_effect":0.05,"temperature":22,
                "is_rainy":False,"is_sunny":True,
                "rain_hours":[],"best_hours":[16,17,19],
                "is_holiday":False,"active_promos":len(STATIC_PROMOTIONS),
            },
        }


async def fetch_holidays(year: int = None) -> dict:
    """Jours fériés Tunisie depuis Nager.Date API."""
    if year is None:
        year = datetime.now().year
    today = date.today()

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"https://date.nager.at/api/v3/PublicHolidays/{year}/TN"
            )
            holidays = resp.json()

        is_today    = False
        today_hol   = None
        next_hol    = None
        min_days    = 9999

        for h in holidays:
            try:
                hdate = date.fromisoformat(h.get("date", ""))
            except Exception:
                continue

            if hdate == today:
                is_today  = True
                today_hol = {"name": h.get("localName", h.get("name", "")), "date": str(hdate)}

            days_until = (hdate - today).days
            if 0 < days_until < min_days:
                min_days = days_until
                next_hol = {
                    "name":       h.get("localName", h.get("name", "")),
                    "date":       str(hdate),
                    "days_until": days_until,
                }

        return {
            "is_holiday_today": is_today,
            "today_holiday":    today_hol,
            "next_holiday":     next_hol,
        }

    except Exception as e:
        logger.warning(f"[STRATEGE] Holidays fallback: {e}")
        # Fériés Tunisie 2026 hardcodés
        HOLIDAYS_2026 = [
            (1,  1,  "Jour de l'An"),
            (3,  20, "Fête de l'Indépendance"),
            (4,  9,  "Journée des Martyrs"),
            (5,  1,  "Fête du Travail"),
            (7,  25, "Fête de la République"),
            (8,  13, "Fête de la Femme"),
            (10, 15, "Fête de l'Évacuation"),
            (11, 7,  "Anniversaire 7 Novembre"),
        ]
        next_hol = None
        for m, d, name in HOLIDAYS_2026:
            try:
                hdate = date(today.year, m, d)
                days  = (hdate - today).days
                if 0 < days < (next_hol or {}).get("days_until", 9999):
                    next_hol = {"name": name, "date": str(hdate), "days_until": days}
            except Exception:
                pass
        is_today = any(
            date(today.year, m, d) == today for m, d, _ in HOLIDAYS_2026
        )
        return {
            "is_holiday_today": is_today,
            "today_holiday":    None,
            "next_holiday":     next_hol,
        }


def get_static_events() -> dict:
    """Événements et promotions Ooredoo (statique, pas de scraping)."""
    return {
        "promotions": STATIC_PROMOTIONS,
        "new_offers": STATIC_NEW_OFFERS,
        "active":     STATIC_PROMOTIONS + STATIC_NEW_OFFERS,
    }


def build_heatmap(urgency: str, weather_effect: float) -> dict:
    """Construit la heatmap de risque horaire."""
    is_rainy = weather_effect <= -0.10
    if urgency == "HIGH":
        traffic = ["med","high","high","crit","crit","high","med","low"]
        risk    = ["low","med","high","high","crit","crit","high","med"]
    elif urgency == "MEDIUM":
        traffic = ["low","med","med","high","high","med","med","low"]
        risk    = ["low","low","med","med","high","med","med","low"]
    else:
        traffic = ["low","low","med","med","med","low","low","low"]
        risk    = ["low","low","low","med","med","low","low","low"]

    return {
        "hours":   ["11AM","12PM","1PM","2PM","3PM","4PM","5PM","6PM"],
        "traffic": traffic,
        "weather": (
            ["high","high","high","high","high","high","high","high"]
            if is_rainy else
            ["low","low","low","low","low","low","low","low"]
        ),
        "stock":   ["low","low","med","high","high","crit","crit","high"],
        "event":   ["low","low","low","low","med","med","high","high"],
        "risk":    risk,
    }


async def fetch_full_context(store_id: str) -> dict:
    """
    Récupère le contexte externe complet pour l'Agent Stratège.
    - Météo Open-Meteo (async, 5s timeout)
    - Fériés Nager.Date (async, 5s timeout)
    - Promotions statiques (instantané)
    Pas de scraping web → pas de timeout de 30s.
    """
    logger.info(f"[STRATEGE] Fetch contexte complet pour {store_id}...")

    # Lancer météo et fériés en parallèle
    weather_task  = asyncio.create_task(fetch_weather(store_id))
    holidays_task = asyncio.create_task(fetch_holidays())

    try:
        weather, holidays = await asyncio.gather(
            weather_task, holidays_task,
            return_exceptions=False,
        )
    except Exception as e:
        logger.warning(f"[STRATEGE] fetch_full_context gather: {e}")
        weather  = await fetch_weather(store_id)
        holidays = {"is_holiday_today": False, "today_holiday": None, "next_holiday": None}

    events  = get_static_events()
    summary = weather.get("summary", {})

    # Enrichir le summary avec les fériés
    if holidays.get("is_holiday_today"):
        summary["is_holiday"]   = True
        summary["holiday_name"] = (holidays.get("today_holiday") or {}).get("name", "")
    else:
        summary["is_holiday"]   = False
        summary["holiday_name"] = ""

    summary["active_promos"] = len(events.get("promotions", []))

    # Effet total : météo + ferié
    total_effect = summary.get("weather_effect", 0)
    if holidays.get("is_holiday_today"):
        total_effect += 0.30  # +30% trafic jour férié
    summary["total_effect"] = round(total_effect, 2)

    # Log
    next_hol = (holidays.get("next_holiday") or {})
    if next_hol:
        logger.info(
            f"[STRATEGE] Prochain férié: "
            f"{next_hol.get('name','')} dans {next_hol.get('days_until',0)} jour(s)"
        )

    logger.info(
        f"[STRATEGE] Contexte {store_id} — "
        f"Météo: {summary.get('weather_icon','')} | "
        f"Férié: {'oui' if summary['is_holiday'] else 'non'} | "
        f"Promos: {summary['active_promos']} | "
        f"Effet total: {total_effect:+.0%}"
    )

    urgency   = "MEDIUM"  # sera mis à jour depuis le state
    heatmap   = build_heatmap(urgency, summary.get("weather_effect", 0))

    return {
        "summary":  summary,
        "weather":  weather.get("current", {}),
        "hourly":   weather.get("hourly", {}),
        "holidays": holidays,
        "events":   events,
        "heatmap":  heatmap,
    }