"""
Tools de l'Agent Stratège.
Sources externes : Météo, Jours fériés, Événements Ooredoo.
"""
import asyncio
import logging
from datetime import datetime, date
from typing import Optional
from unittest import result
from unittest import result

import httpx

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 1 — MÉTÉO TUNIS — Open-Meteo (gratuit, sans clé)
# ─────────────────────────────────────────────────────────────────────────────

# Coordonnées des villes Ooredoo Tunisie
STORE_COORDS = {
    "OOR_LAC_01":    {"lat": 36.8065, "lon": 10.1815, "city": "Tunis Lac"},
    "OOR_MENZAH_02": {"lat": 36.8448, "lon": 10.1942, "city": "Menzah"},
    "OOR_SFAX_03":   {"lat": 34.7406, "lon": 10.7603, "city": "Sfax"},
}

WEATHER_CODES = {
    0:  {"label": "Ciel dégagé",    "icon": "☀️",  "impact": "positive", "traffic_effect": +0.10},
    1:  {"label": "Peu nuageux",    "icon": "🌤️",  "impact": "neutre",   "traffic_effect": +0.05},
    2:  {"label": "Partiellement nuageux", "icon": "⛅", "impact": "neutre", "traffic_effect": 0.0},
    3:  {"label": "Couvert",        "icon": "☁️",  "impact": "négatif",  "traffic_effect": -0.05},
    45: {"label": "Brouillard",     "icon": "🌫️",  "impact": "négatif",  "traffic_effect": -0.10},
    51: {"label": "Bruine légère",  "icon": "🌦️",  "impact": "négatif",  "traffic_effect": -0.15},
    61: {"label": "Pluie légère",   "icon": "🌧️",  "impact": "négatif",  "traffic_effect": -0.20},
    63: {"label": "Pluie modérée",  "icon": "🌧️",  "impact": "négatif",  "traffic_effect": -0.25},
    65: {"label": "Pluie forte",    "icon": "⛈️",  "impact": "très négatif", "traffic_effect": -0.35},
    80: {"label": "Averses",        "icon": "🌦️",  "impact": "négatif",  "traffic_effect": -0.20},
    95: {"label": "Orage",          "icon": "⛈️",  "impact": "très négatif", "traffic_effect": -0.40},
}


async def fetch_weather(store_id: str) -> dict:
    """
    Récupère la météo actuelle et les prévisions horaires
    depuis Open-Meteo (gratuit, sans clé API).
    """
    coords = STORE_COORDS.get(store_id, STORE_COORDS["OOR_LAC_01"])
    lat, lon = coords["lat"], coords["lon"]

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude":           lat,
        "longitude":          lon,
        "current":            "temperature_2m,precipitation,weathercode,windspeed_10m,relativehumidity_2m",
        "hourly":             "temperature_2m,precipitation_probability,weathercode",
        "forecast_days":      1,
        "timezone":           "Africa/Tunis",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        current     = data.get("current", {})
        hourly      = data.get("hourly", {})
        wcode       = current.get("weathercode", 0)
        weather_info = WEATHER_CODES.get(wcode, WEATHER_CODES[0])

        # Prévisions horaires 9h-20h
        hours       = hourly.get("time", [])
        temps       = hourly.get("temperature_2m", [])
        precip_prob = hourly.get("precipitation_probability", [])
        wcodes_h    = hourly.get("weathercode", [])

        hourly_forecast = []
        for i, h in enumerate(hours):
            hour_dt = datetime.fromisoformat(h)
            if 9 <= hour_dt.hour <= 20:
                wc = wcodes_h[i] if i < len(wcodes_h) else 0
                wi = WEATHER_CODES.get(wc, WEATHER_CODES[0])
                hourly_forecast.append({
                    "hour":       hour_dt.strftime("%H:%M"),
                    "temp":       temps[i] if i < len(temps) else 0,
                    "precip_prob": precip_prob[i] if i < len(precip_prob) else 0,
                    "weathercode": wc,
                    "label":      wi["label"],
                    "icon":       wi["icon"],
                    "impact":     wi["impact"],
                    "traffic_effect": wi["traffic_effect"],
                })

        # Impact global sur le trafic boutique
        avg_effect = sum(h["traffic_effect"] for h in hourly_forecast) / max(len(hourly_forecast), 1)

        result = {
            "store_id":    store_id,
            "city":        coords["city"],
            "current": {
                "temperature":    current.get("temperature_2m", 0),
                "precipitation":  current.get("precipitation", 0),
                "humidity":       current.get("relativehumidity_2m", 0),
                "windspeed":      current.get("windspeed_10m", 0),
                "weathercode":    wcode,
                "label":          weather_info["label"],
                "icon":           weather_info["icon"],
                "impact":         weather_info["impact"],
                "traffic_effect": weather_info["traffic_effect"],
            },
            "hourly_forecast":    hourly_forecast,
            "avg_traffic_effect": round(avg_effect, 3),
            "rain_hours":         [h for h in hourly_forecast if h["precip_prob"] > 50],
            "best_hours":         [h for h in hourly_forecast if h["traffic_effect"] >= 0],
            "risk_hours":         [h for h in hourly_forecast if h["traffic_effect"] < -0.15],
            "source":             "open-meteo.com",
            "fetched_at":         datetime.now().isoformat(),
        }

        logger.info(
            f"[STRATEGE] Météo {coords['city']}: "
            f"{weather_info['icon']} {weather_info['label']} "
            f"| Effet trafic: {weather_info['traffic_effect']:+.0%}"
        )
        return result

    except Exception as e:
        logger.warning(f"[STRATEGE] Météo fallback: {e}")
        return _weather_fallback(store_id)


def _weather_fallback(store_id: str) -> dict:
    """Météo par défaut si API indisponible."""
    coords = STORE_COORDS.get(store_id, STORE_COORDS["OOR_LAC_01"])
    return {
        "store_id":    store_id,
        "city":        coords["city"],
        "current": {
            "temperature":    22.0,
            "precipitation":  0.0,
            "humidity":       60,
            "windspeed":      10.0,
            "weathercode":    1,
            "label":          "Peu nuageux",
            "icon":           "🌤️",
            "impact":         "neutre",
            "traffic_effect": 0.05,
        },
        "hourly_forecast":    [],
        "avg_traffic_effect": 0.0,
        "rain_hours":         [],
        "best_hours":         [],
        "risk_hours":         [],
        "source":             "fallback",
        "fetched_at":         datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 2 — JOURS FÉRIÉS TUNISIE — Nager.Date (gratuit)
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_public_holidays(year: Optional[int] = None) -> dict:
    """
    Récupère les jours fériés tunisiens depuis l'API Nager.Date.
    """
    if year is None:
        year = datetime.now().year

    url = f"https://date.nager.at/api/v3/PublicHolidays/{year}/TN"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            holidays = resp.json()

        today = date.today()
        today_str = today.isoformat()

        formatted = []
        for h in holidays:
            h_date = date.fromisoformat(h["date"])
            days_until = (h_date - today).days
            formatted.append({
                "date":       h["date"],
                "name":       h["localName"] or h["name"],
                "name_fr":    h["name"],
                "is_today":   h["date"] == today_str,
                "days_until": days_until,
                "is_upcoming": 0 <= days_until <= 7,
                "impact":     "fort" if days_until in [0, 1, -1] else
                              "moyen" if 0 <= days_until <= 3 else "faible",
            })

        today_holiday   = next((h for h in formatted if h["is_today"]), None)
        upcoming        = [h for h in formatted if h["is_upcoming"] and not h["is_today"]]
        next_holiday    = min(
            (h for h in formatted if h["days_until"] > 0),
            key=lambda x: x["days_until"],
            default=None
        )

        result = {
            "year":           year,
            "total":          len(formatted),
            "all_holidays":   formatted,
            "today_holiday":  today_holiday,
            "upcoming_week":  upcoming,
            "next_holiday":   next_holiday,
            "is_holiday_today": today_holiday is not None,
            "source":         "date.nager.at",
            "fetched_at":     datetime.now().isoformat(),
        }

        if today_holiday:
            logger.info(f"[STRATEGE] 🎉 Jour férié aujourd'hui: {today_holiday['name']}")
        elif next_holiday:
            logger.info(
                f"[STRATEGE] Prochain férié: {next_holiday['name']} "
                f"dans {next_holiday['days_until']} jour(s)"
            )

        return result

    except Exception as e:
        logger.warning(f"[STRATEGE] Jours fériés fallback: {e}")
        return _holidays_fallback()


def _holidays_fallback() -> dict:
    """Jours fériés hardcodés si API indisponible."""
    today = date.today()
    tunisian_holidays = [
        {"date": f"{today.year}-01-01", "name": "Jour de l'An"},
        {"date": f"{today.year}-03-20", "name": "Fête de l'Indépendance"},
        {"date": f"{today.year}-04-09", "name": "Journée des Martyrs"},
        {"date": f"{today.year}-05-01", "name": "Fête du Travail"},
        {"date": f"{today.year}-07-25", "name": "Fête de la République"},
        {"date": f"{today.year}-08-13", "name": "Fête de la Femme"},
        {"date": f"{today.year}-10-15", "name": "Fête de l'Évacuation"},
    ]

    formatted = []
    for h in tunisian_holidays:
        h_date      = date.fromisoformat(h["date"])
        days_until  = (h_date - today).days
        formatted.append({
            "date":        h["date"],
            "name":        h["name"],
            "is_today":    h["date"] == today.isoformat(),
            "days_until":  days_until,
            "is_upcoming": 0 <= days_until <= 7,
            "impact":      "fort" if days_until in [0, 1, -1] else "faible",
        })

    return {
        "year":           today.year,
        "total":          len(formatted),
        "all_holidays":   formatted,
        "today_holiday":  next((h for h in formatted if h["is_today"]), None),
        "upcoming_week":  [h for h in formatted if h["is_upcoming"]],
        "next_holiday":   min(
            (h for h in formatted if h["days_until"] > 0),
            key=lambda x: x["days_until"],
            default=None
        ),
        "is_holiday_today": any(h["is_today"] for h in formatted),
        "source":         "fallback_hardcoded",
        "fetched_at":     datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 3 — ÉVÉNEMENTS OOREDOO TUNISIE — Scraping
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_ooredoo_events() -> dict:

    """
    Récupère les événements Ooredoo via Playwright.
    Cache 1h pour éviter scraping répété.
    """
    from modules.coaching.agents.stratege.scraper import scrape_ooredoo_events
    return await scrape_ooredoo_events()


def _parse_ooredoo_html(html: str, url: str) -> list:
    """Parse le HTML Ooredoo pour extraire les offres."""
    events = []
    try:
        # Recherche patterns communs dans le HTML Ooredoo
        import re

        # Titres d'offres (h2, h3, .offer-title, .promo-title)
        title_patterns = [
            r'<h[23][^>]*class="[^"]*(?:offer|promo|title)[^"]*"[^>]*>(.*?)</h[23]>',
            r'<div[^>]*class="[^"]*(?:offer-title|promo-title|card-title)[^"]*"[^>]*>(.*?)</div>',
            r'<h[23][^>]*>(.*?(?:forfait|offre|promo|4G|5G|fibre)[^<]*)</h[23]>',
        ]

        found_titles = []
        for pattern in title_patterns:
            matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
            for m in matches:
                # Nettoyer le HTML
                clean = re.sub(r'<[^>]+>', '', m).strip()
                if clean and 3 < len(clean) < 100:
                    found_titles.append(clean)

        # Dédupliquer
        seen = set()
        for title in found_titles:
            if title not in seen:
                seen.add(title)
                events.append({
                    "title":     title,
                    "type":      _classify_event(title),
                    "source_url": url,
                    "is_active": True,
                    "scraped":   True,
                    "date":      datetime.now().isoformat(),
                })

    except Exception as e:
        logger.warning(f"[STRATEGE] Parse HTML error: {e}")

    return events[:10]  # Max 10 événements


def _classify_event(title: str) -> str:
    """Classifie le type d'événement Ooredoo."""
    title_lower = title.lower()
    if any(k in title_lower for k in ["promo", "offre spéciale", "réduction", "gratuit"]):
        return "promotion"
    if any(k in title_lower for k in ["nouveau", "new", "lance", "découvrez"]):
        return "new_offer"
    if any(k in title_lower for k in ["forfait", "4g", "5g", "fibre", "data"]):
        return "tarif"
    return "general"


def _ooredoo_known_offers() -> list:
    """Offres Ooredoo connues comme fallback."""
    return [
        {
            "title":     "Forfait Max 5G — 100Go à 49 DT/mois",
            "type":      "tarif",
            "category":  "Mobile",
            "price":     "49 DT/mois",
            "is_active": True,
            "scraped":   False,
            "highlight": "5G disponible dans les grandes villes",
            "date":      datetime.now().isoformat(),
        },
        {
            "title":     "Box Fibre Ooredoo — 200 Mbps à 59 DT/mois",
            "type":      "tarif",
            "category":  "Internet",
            "price":     "59 DT/mois",
            "is_active": True,
            "scraped":   False,
            "highlight": "Installation gratuite ce mois",
            "date":      datetime.now().isoformat(),
        },
        {
            "title":     "iPhone 16 Pro — Disponible en boutique",
            "type":      "new_offer",
            "category":  "Smartphone",
            "price":     "À partir de 3,299 DT",
            "is_active": True,
            "scraped":   False,
            "highlight": "Stock limité",
            "date":      datetime.now().isoformat(),
        },
        {
            "title":     "Promo Recharge Double — Weekend",
            "type":      "promotion",
            "category":  "Prépayé",
            "price":     "Bonus 100%",
            "is_active": True,
            "scraped":   False,
            "highlight": "Valable samedi et dimanche",
            "date":      datetime.now().isoformat(),
        },
        {
            "title":     "Samsung Galaxy A55 5G — Offre bundle",
            "type":      "promotion",
            "category":  "Smartphone",
            "price":     "1,299 DT + forfait offert 3 mois",
            "is_active": True,
            "scraped":   False,
            "highlight": "Bundle exclusif boutique",
            "date":      datetime.now().isoformat(),
        },
    ]


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 4 — CONTEXTE GLOBAL — Agrégation de toutes les sources
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_full_context(store_id: str) -> dict:
    """
    Récupère et agrège toutes les données contextuelles externes
    en parallèle : météo + jours fériés + événements Ooredoo.
    """
    logger.info(f"[STRATEGE] Fetch contexte complet pour {store_id}...")

    # Appels parallèles
    weather, holidays, events = await asyncio.gather(
        fetch_weather(store_id),
        fetch_public_holidays(),
        fetch_ooredoo_events(),
        return_exceptions=True
    )

    # Gestion erreurs
    if isinstance(weather,  Exception): weather  = _weather_fallback(store_id)
    if isinstance(holidays, Exception): holidays = _holidays_fallback()
    if isinstance(events,   Exception): events   = {"events": [], "active": []}

    # ── Calcul impact global ──────────────────────────────────────────────────
    weather_effect   = weather.get("current", {}).get("traffic_effect", 0)
    is_holiday       = holidays.get("is_holiday_today", False)
    holiday_effect   = 0.15 if is_holiday else 0.0
    promo_effect     = 0.10 if events.get("promotions") else 0.0

    total_effect     = weather_effect + holiday_effect + promo_effect
    context_risk     = _compute_context_risk(weather, holidays, events)

    # ── Heatmap data pour le frontend ────────────────────────────────────────
    heatmap = _build_heatmap(weather, holidays, events)

    result = {
        "store_id":   store_id,
        "weather":    weather,
        "holidays":   holidays,
        "events":     events,
        "summary": {
            "weather_label":   weather.get("current", {}).get("label", ""),
            "weather_icon":    weather.get("current", {}).get("icon", ""),
            "weather_effect":  weather_effect,
            "is_holiday":      is_holiday,
            "holiday_name":    holidays.get("today_holiday", {}).get("name", "") if is_holiday else "",
            "active_promos":   len(events.get("promotions", [])),
            "total_effect":    round(total_effect, 3),
            "context_risk":    context_risk,
            "rain_hours":      [h["hour"] for h in weather.get("rain_hours", [])],
            "best_hours":      [h["hour"] for h in weather.get("best_hours", [])],
        },
        "heatmap":    heatmap,
        "fetched_at": datetime.now().isoformat(),
    }

    logger.info(
        f"[STRATEGE] Contexte {store_id} — "
        f"Météo: {weather.get('current', {}).get('icon', '')} | "
        f"Férié: {'oui' if is_holiday else 'non'} | "
        f"Promos: {len(events.get('promotions', []))} | "
        f"Effet total: {total_effect:+.0%}"
    )

    return result


def _compute_context_risk(weather: dict, holidays: dict, events: dict) -> str:
    """Calcule le niveau de risque contextuel global."""
    risk_score = 0

    # Météo
    effect = weather.get("current", {}).get("traffic_effect", 0)
    if effect <= -0.30: risk_score += 3
    elif effect <= -0.15: risk_score += 2
    elif effect < 0: risk_score += 1

    # Jour férié
    if holidays.get("is_holiday_today"): risk_score -= 2  # positif

    # Promo active
    if events.get("promotions"): risk_score -= 1  # positif

    if risk_score >= 3: return "HIGH"
    if risk_score >= 1: return "MEDIUM"
    return "LOW"


def _build_heatmap(weather: dict, holidays: dict, events: dict) -> dict:
    """
    Construit les données heatmap pour le frontend.
    Format compatible avec context_heatmap du dashboard.
    """
    hours = ["11AM","12PM","1PM","2PM","3PM","4PM","5PM","6PM"]

    # Mapping heure → index
    hour_map = {
        "11:00": 0, "12:00": 1, "13:00": 2,
        "14:00": 3, "15:00": 4, "16:00": 5,
        "17:00": 6, "18:00": 7,
    }

    # Météo par heure
    weather_row = [1] * 8
    traffic_row = [2] * 8
    hourly      = weather.get("hourly_forecast", [])

    for h in hourly:
        h_time = h.get("hour", "")
        idx    = hour_map.get(h_time)
        if idx is None:
            continue
        effect = h.get("traffic_effect", 0)
        precip = h.get("precip_prob", 0)

        # Météo
        if precip > 70:    weather_row[idx] = 4  # crit
        elif precip > 50:  weather_row[idx] = 3  # high
        elif precip > 30:  weather_row[idx] = 2  # med
        else:              weather_row[idx] = 1  # low

        # Traffic
        if effect <= -0.30:   traffic_row[idx] = 4
        elif effect <= -0.15: traffic_row[idx] = 3
        elif effect < 0:      traffic_row[idx] = 2
        else:                 traffic_row[idx] = 1

    # Stock — statique pour l'instant (sera enrichi par l'Inventaire)
    stock_row = [1, 1, 2, 3, 3, 4, 4, 3]

    # Événements
    event_row = [1] * 8
    if events.get("promotions"):
        # Promos → impact PM
        for i in range(4, 8):
            event_row[i] = 3

    if holidays.get("is_holiday_today"):
        event_row = [4] * 8  # Jour férié → tout rouge

    # Risk global
    risk_row = [
        max(weather_row[i], traffic_row[i], event_row[i])
        for i in range(8)
    ]

    # Convertir en labels
    level_map = {1: "low", 2: "med", 3: "high", 4: "crit"}
    to_labels = lambda arr: [level_map.get(v, "low") for v in arr]

    return {
        "hours":   hours,
        "traffic": to_labels(traffic_row),
        "weather": to_labels(weather_row),
        "stock":   to_labels(stock_row),
        "event":   to_labels(event_row),
        "risk":    to_labels(risk_row),
    }