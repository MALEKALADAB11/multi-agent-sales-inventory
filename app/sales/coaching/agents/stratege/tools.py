"""
Tools Agent Stratège — 100% PostgreSQL.
Toutes les données viennent de la base ooredoo_sales (market.*, agent_kpi_daily).
Météo : Open-Meteo (gratuit, sans clé). Fériés : Nager.Date API.
Aucune donnée hardcodée, aucun CSV.
"""
import asyncio
import logging
import os
from datetime import datetime, date
from typing import Optional

import asyncpg
import httpx

from app.core.db import acquire

logger = logging.getLogger(__name__)

_DB_CONFIG = {
    "host":     os.getenv("POSTGRES_HOST", "localhost"),
    "port":     int(os.getenv("POSTGRES_PORT", "5432")),
    "database": os.getenv("POSTGRES_DB", "ooredoo_sales"),
    "user":     os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", "root"),
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

_weather_cache: dict = {}
_weather_cache_time: dict = {}
_WEATHER_TTL = 300

# Budget par source du contexte externe. Le contexte est un enrichissement :
# aucune de ces sources ne justifie de retarder le cycle commercial, et la
# somme des budgets doit rester très en deçà du timeout de l'orchestrateur.
WEATHER_BUDGET_S  = float(os.getenv("STRATEGE_WEATHER_BUDGET_S",  "6"))
HOLIDAYS_BUDGET_S = float(os.getenv("STRATEGE_HOLIDAYS_BUDGET_S", "5"))
MARKET_BUDGET_S   = float(os.getenv("STRATEGE_MARKET_BUDGET_S",   "8"))
EVENTS_BUDGET_S   = float(os.getenv("STRATEGE_EVENTS_BUDGET_S",   "9"))


# ── Coordonnées boutique depuis PostgreSQL ────────────────────────────────────

async def _fetch_store_coords(store_id: str) -> dict:
    """Lit latitude/longitude depuis sales.boutiques. Fallback Tunis centre."""
    try:
        async with acquire(connect_timeout=5) as conn:
            row = await conn.fetchrow(
                "SELECT latitude, longitude, ville FROM sales.boutiques WHERE store_id = $1",
                store_id
            )
            if row and row["latitude"] and row["longitude"]:
                return {"lat": float(row["latitude"]), "lon": float(row["longitude"]),
                        "city": row["ville"] or store_id}
    except Exception:
        pass
    return {"lat": 36.8065, "lon": 10.1815, "city": "Tunis"}


# ── Météo Open-Meteo ──────────────────────────────────────────────────────────

async def fetch_weather(store_id: str) -> dict:
    import time
    global _weather_cache, _weather_cache_time
    now = time.time()
    cached_at = _weather_cache_time.get(store_id, 0.0)
    if store_id in _weather_cache and (now - cached_at) < _WEATHER_TTL:
        return _weather_cache[store_id]

    coords = await _fetch_store_coords(store_id)
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
            code    = current.get("weathercode", 1)
            temp    = current.get("temperature_2m", 22)
            rain    = current.get("precipitation", 0)
            wind    = current.get("windspeed_10m", 0)
            w       = WEATHER_CODES.get(code, {"label": "Variable", "icon": "⛅", "effect": 0.0})
            hour    = datetime.now().hour
            rain_probs = hourly.get("precipitation_probability", [])
            rain_hours = [
                (hour + i) % 24
                for i, prob in enumerate(rain_probs[:24])
                if prob and prob > 60
            ]
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
                "city":           coords["city"],
            }
            result = {"current": dict(current), "hourly": hourly, "summary": summary}
            _weather_cache[store_id] = result
            _weather_cache_time[store_id] = now
            logger.info(f"[STRATEGE] Météo {coords['city']}: {w['icon']} | effet={w['effect']:+.0%}")
            return result
    except Exception as e:
        logger.warning(f"[STRATEGE] Météo fallback: {e}")
        return {
            "current": {"weathercode": 1, "temperature": 22, "precipitation": 0, "windspeed": 0},
            "hourly":  {},
            "summary": {
                "weather_label": "Tunis", "weather_icon": "🌤️",
                "weather_effect": 0.05, "temperature": 22,
                "is_rainy": False, "is_sunny": True,
                "rain_hours": [], "best_hours": [16, 17, 19],
                "is_holiday": False, "city": coords.get("city", "Tunis"),
            },
        }


# ── Jours fériés Nager.Date ────────────────────────────────────────────────────

_holidays_cache: dict = {}
_holidays_cache_time: dict = {}
_HOLIDAYS_TTL = 6 * 3600  # holiday lists for a given year practically never change intraday


async def fetch_holidays(year: int = None) -> dict:
    import time
    global _holidays_cache, _holidays_cache_time
    if year is None:
        year = datetime.now().year

    now = time.time()
    cached_at = _holidays_cache_time.get(year, 0.0)
    if year in _holidays_cache and (now - cached_at) < _HOLIDAYS_TTL:
        return _holidays_cache[year]

    today = date.today()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp  = await client.get(f"https://date.nager.at/api/v3/PublicHolidays/{year}/TN")
            holidays = resp.json()
        is_today, today_hol, next_hol, min_days = False, None, None, 9999
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
        result = {"is_holiday_today": is_today, "today_holiday": today_hol, "next_holiday": next_hol}
        _holidays_cache[year] = result
        _holidays_cache_time[year] = now
        return result
    except Exception as e:
        logger.warning(f"[STRATEGE] Holidays API failed: {e}")
        # Don't cache the fallback — retry on the next call instead of being
        # stuck with "no holidays" for 6h if this was just a transient blip.
        return {"is_holiday_today": False, "today_holiday": None, "next_holiday": None}


# ── Market Intelligence PostgreSQL ────────────────────────────────────────────

async def fetch_market_intelligence_pg(store_id: str = None) -> dict:
    """
    Charge depuis market.* et agent_kpi_daily :
    - Événements actifs / proches (30j)
    - Saisonnalité mois courant par catégorie
    - Concurrents et prix (dernier relevé 90j)
    - MNP flows nets 3 derniers mois
    - Tendance KPI store 7j
    """
    result = {
        "events_actifs":     [],
        "events_en_cours":   [],
        "events_a_venir":    [],
        "seasonal_factors":  {},
        "competitors":       [],
        "competitor_pricing": [],
        "mnp_net":           {},
        "agent_kpi_trend":   {},
        "source":            "postgresql_market",
    }
    try:
        async with acquire(connect_timeout=8) as conn:
            # Déduplication à la lecture. Le scraper d'offres réinsérait chaque
            # jour les mêmes lignes avec une start_date différente (clé
            # d'idempotence = nom + start_date) : 35 des 42 événements « en
            # cours » étaient des doublons de texte de navigation ooredoo.tn, et
            # ils saturaient le LIMIT au détriment des vrais événements
            # (festivals, fêtes nationales, rentrée). On ne garde qu'une
            # occurrence par nom normalisé, la plus récemment commencée.
            ev_rows = await conn.fetch("""
                SELECT DISTINCT ON (norm_name)
                       event_name, event_type, sous_type,
                       start_date, end_date, intensite, scope,
                       uplift_terminal, uplift_forfait, uplift_sim,
                       uplift_recharge, uplift_accessoire, note_strategie
                FROM (
                    SELECT *,
                           -- translate() plutôt que unaccent : l'extension n'est
                           -- pas garantie présente, et « Soldes Été 2026 » /
                           -- « Soldes Ete 2026 » doivent se réduire à la même clé.
                           lower(regexp_replace(
                               translate(event_name,
                                         'áàâäãéèêëíìîïóòôöõúùûüçÁÀÂÄÃÉÈÊËÍÌÎÏÓÒÔÖÕÚÙÛÜÇ',
                                         'aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC'),
                               '[^a-zA-Z0-9]', '', 'g')) AS norm_name
                    FROM market.events
                    WHERE start_date <= CURRENT_DATE + INTERVAL '30 days'
                      AND end_date   >= CURRENT_DATE
                      -- Bruit de scraping : fragments de navigation capturés
                      -- comme « offres » (menus, liens, barres de recherche).
                      AND event_name !~* ('(eshop|recherche de produits|acces rapide|'
                                       || 'accès rapide|telechargez|téléchargez|'
                                       || 'notre selection|notre sélection|https?://)')
                ) e
                ORDER BY norm_name, start_date DESC
            """)
            today = date.today()

            def _uplift(x) -> float:
                # Les seeds historiques stockent des pourcentages (25.0),
                # les récents des fractions (0.25) — normaliser en fraction.
                v = float(x or 0)
                return round(v / 100, 4) if v > 1 else v

            result["events_actifs"] = [
                {
                    "nom":       r["event_name"],
                    "type":      r["event_type"],
                    "sous_type": r["sous_type"],
                    "debut":     str(r["start_date"]),
                    "fin":       str(r["end_date"]),
                    "intensite": r["intensite"],
                    "scope":     r["scope"],
                    "en_cours":  r["start_date"] <= today <= r["end_date"],
                    "jours_avant": max(0, (r["start_date"] - today).days),
                    "uplift": {
                        "terminal":   _uplift(r["uplift_terminal"]),
                        "forfait":    _uplift(r["uplift_forfait"]),
                        "sim":        _uplift(r["uplift_sim"]),
                        "recharge":   _uplift(r["uplift_recharge"]),
                        "accessoire": _uplift(r["uplift_accessoire"]),
                    },
                    "strategie": r["note_strategie"] or "",
                }
                for r in ev_rows
            ]
            # DISTINCT ON impose son propre ORDER BY : le classement métier se
            # fait donc ici, et il diffère selon l'horizon. Un événement en cours
            # agit sur le trafic d'aujourd'hui — l'intensité prime. Un événement à
            # venir sert à préparer stock et équipe — l'imminence prime, sinon un
            # gros festival dans un mois masque une fête nationale dans six jours.
            _INTENSITE = {"EXTREME": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}
            en_cours = [e for e in result["events_actifs"] if e["en_cours"]]
            a_venir  = [e for e in result["events_actifs"] if not e["en_cours"]]
            en_cours.sort(key=lambda e: _INTENSITE.get(e["intensite"], 0), reverse=True)
            a_venir.sort(key=lambda e: (e["jours_avant"],
                                        -_INTENSITE.get(e["intensite"], 0)))

            result["events_en_cours"] = en_cours[:10]
            result["events_a_venir"]  = a_venir[:10]
            result["events_actifs"]   = result["events_en_cours"] + result["events_a_venir"]

            sp_rows = await conn.fetch("""
                SELECT categorie, mois, jour_semaine,
                       facteur_demande, confidence, notes
                FROM market.seasonal_patterns
                WHERE mois = EXTRACT(MONTH FROM CURRENT_DATE)::INTEGER
                ORDER BY categorie, facteur_demande DESC
            """)
            for r in sp_rows:
                cat = r["categorie"]
                fd  = float(r["facteur_demande"])
                if cat not in result["seasonal_factors"]:
                    result["seasonal_factors"][cat] = {
                        "mois":        r["mois"],
                        "facteur_max": fd,
                        "confidence":  r["confidence"],
                        "notes":       r["notes"] or "",
                        "par_jour":    {},
                    }
                else:
                    result["seasonal_factors"][cat]["facteur_max"] = max(
                        result["seasonal_factors"][cat]["facteur_max"], fd
                    )
                if r["jour_semaine"] is not None:
                    result["seasonal_factors"][cat]["par_jour"][str(r["jour_semaine"])] = fd

            comp_rows = await conn.fetch("""
                SELECT concurrent_id, nom, part_marche_pct, positionnement, points_forts
                FROM market.competitors WHERE actif = TRUE
                ORDER BY part_marche_pct DESC NULLS LAST
            """)
            result["competitors"] = [
                {
                    "id":       r["concurrent_id"],
                    "nom":      r["nom"],
                    "pdm_pct":  float(r["part_marche_pct"] or 0),
                    "position": r["positionnement"],
                    "avantages": r["points_forts"] if isinstance(r["points_forts"], list)
                                 else [],
                }
                for r in comp_rows
            ]

            pricing_rows = await conn.fetch("""
                SELECT concurrent_id, categorie, produit_type, donnees_go,
                       prix_ttc, engagement_mois, date_releve
                FROM market.competitor_pricing
                WHERE date_releve >= CURRENT_DATE - INTERVAL '90 days' AND actif = TRUE
                ORDER BY concurrent_id, categorie, date_releve DESC
                LIMIT 30
            """)
            result["competitor_pricing"] = [
                {
                    "concurrent": r["concurrent_id"],
                    "categorie":  r["categorie"],
                    "produit":    r["produit_type"],
                    "data_go":    float(r["donnees_go"] or 0),
                    "prix_ttc":   float(r["prix_ttc"] or 0),
                    "engagement": int(r["engagement_mois"] or 0),
                    "releve":     str(r["date_releve"]),
                }
                for r in pricing_rows
            ]

            mnp_rows = await conn.fetch("""
                SELECT direction, SUM(volume) AS vol
                FROM market.mnp_flows
                WHERE mois >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '3 months')
                GROUP BY direction
            """)
            for r in mnp_rows:
                result["mnp_net"][r["direction"]] = int(r["vol"] or 0)
            port_in  = result["mnp_net"].get("PORT_IN", 0)
            port_out = result["mnp_net"].get("PORT_OUT", 0)
            result["mnp_net"]["bilan"]    = port_in - port_out
            result["mnp_net"]["tendance"] = "favorable" if port_in > port_out else "defavorable"

            if store_id:
                kpi_rows = await conn.fetch("""
                    SELECT kpi_date, SUM(ca_realise) AS ca,
                           SUM(nb_forfaits)  AS forfaits,
                           SUM(nb_terminaux) AS terminaux,
                           SUM(nb_postpaye)  AS postpaye,
                           COUNT(DISTINCT agent_id) AS nb_agents
                    FROM agent_kpi_daily
                    WHERE store_id = $1 AND kpi_date >= CURRENT_DATE - INTERVAL '7 days'
                    GROUP BY kpi_date ORDER BY kpi_date DESC
                """, store_id)
                if kpi_rows:
                    ca_list = [float(r["ca"] or 0) for r in kpi_rows]
                    result["agent_kpi_trend"] = {
                        "ca_7j_avg":    round(sum(ca_list) / len(ca_list), 2),
                        "ca_7j_max":    round(max(ca_list), 2),
                        "ca_7j_min":    round(min(ca_list), 2),
                        "forfaits_7j":  int(sum(r["forfaits"] or 0 for r in kpi_rows)),
                        "terminaux_7j": int(sum(r["terminaux"] or 0 for r in kpi_rows)),
                        "postpaye_7j":  int(sum(r["postpaye"] or 0 for r in kpi_rows)),
                        "nb_jours":     len(kpi_rows),
                    }

            logger.info(
                f"[STRATEGE-PG] {len(result['events_actifs'])} events, "
                f"{len(result['competitors'])} concurrents, "
                f"MNP bilan={result['mnp_net'].get('bilan', 0)}"
            )
    except Exception as e:
        logger.warning(f"[STRATEGE-PG] market_intelligence_pg failed: {e}")
        result["source"] = "fallback"

    return result


# ── Heatmap horaire ───────────────────────────────────────────────────────────

def build_heatmap(urgency: str, weather_effect: float) -> dict:
    is_rainy = weather_effect <= -0.10
    if urgency == "HIGH":
        traffic = ["med", "high", "high", "crit", "crit", "high", "med", "low"]
        risk    = ["low", "med", "high", "high", "crit", "crit", "high", "med"]
    elif urgency == "MEDIUM":
        traffic = ["low", "med", "med", "high", "high", "med", "med", "low"]
        risk    = ["low", "low", "med", "med", "high", "med", "med", "low"]
    else:
        traffic = ["low", "low", "med", "med", "med", "low", "low", "low"]
        risk    = ["low", "low", "low", "med", "med", "low", "low", "low"]
    return {
        "hours":   ["11AM", "12PM", "1PM", "2PM", "3PM", "4PM", "5PM", "6PM"],
        "traffic": traffic,
        "weather": (
            ["high"] * 8 if is_rainy else ["low"] * 8
        ),
        "stock":   ["low", "low", "med", "high", "high", "crit", "crit", "high"],
        "event":   ["low", "low", "low", "low", "med", "med", "high", "high"],
        "risk":    risk,
    }


# ── Point d'entrée principal ──────────────────────────────────────────────────

async def fetch_full_context(store_id: str) -> dict:
    """
    Contexte complet Agent Stratège :
    - Météo Open-Meteo
    - Fériés Nager.Date
    - Market intelligence 100% PostgreSQL (events, seasonalité, concurrents, MNP, KPIs)
    """
    logger.info(f"[STRATEGE] fetch_full_context pour {store_id}...")

    from .scraper import scrape_ooredoo_events

    # Chaque source a son propre budget : une source lente ne doit jamais
    # dicter la latence du cycle. Auparavant gather(return_exceptions=False)
    # faisait remonter la première exception, et le except relançait la météo
    # en séquentiel — on payait deux fois la source la plus lente.
    async def _budget(coro, seconds: float, label: str, default):
        try:
            return await asyncio.wait_for(coro, timeout=seconds)
        except asyncio.TimeoutError:
            logger.warning("[STRATEGE] %s : budget %.0fs dépassé — ignoré", label, seconds)
            return default
        except Exception as e:
            logger.warning("[STRATEGE] %s : %s", label, str(e)[:100])
            return default

    weather, holidays, market, ooredoo_events = await asyncio.gather(
        _budget(fetch_weather(store_id), WEATHER_BUDGET_S, "météo",
                {"current": {}, "hourly": {}, "summary": {
                    "weather_label": "", "weather_icon": "🌤️", "weather_effect": 0.0,
                    "temperature": 22, "is_rainy": False, "is_sunny": False,
                    "rain_hours": [], "best_hours": [], "is_holiday": False,
                    "city": store_id}}),
        _budget(fetch_holidays(), HOLIDAYS_BUDGET_S, "fériés",
                {"is_holiday_today": False, "today_holiday": None, "next_holiday": None}),
        _budget(fetch_market_intelligence_pg(store_id), MARKET_BUDGET_S, "market PG", {}),
        _budget(scrape_ooredoo_events(), EVENTS_BUDGET_S, "offres Ooredoo",
                {"promotions": [], "new_offers": [], "tarifs": [], "events": []}),
    )

    summary = weather.get("summary", {})
    if holidays.get("is_holiday_today"):
        summary["is_holiday"]   = True
        summary["holiday_name"] = (holidays.get("today_holiday") or {}).get("name", "")
    else:
        summary["is_holiday"]   = False
        summary["holiday_name"] = ""

    events_actifs   = market.get("events_actifs", [])
    events_en_cours = market.get("events_en_cours", [])
    events_a_venir  = market.get("events_a_venir", [])
    summary["active_promos"]    = len(events_actifs)
    summary["nb_events_en_cours"] = len(events_en_cours)
    summary["nb_events_a_venir"]  = len(events_a_venir)
    if events_en_cours:
        top_ev = max(events_en_cours,
                     key=lambda e: {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "EXTREME": 3}.get(e.get("intensite"), 0))
        summary["event_en_cours"] = top_ev["nom"]

    total_effect = summary.get("weather_effect", 0)
    if holidays.get("is_holiday_today"):
        total_effect += 0.30
    # Seuls les événements EN COURS impactent le trafic du jour ; un événement
    # à venir prépare le stock mais ne booste pas les ventes d'aujourd'hui.
    if events_en_cours:
        intensite = max((e.get("intensite", "LOW") for e in events_en_cours),
                        key=lambda i: {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "EXTREME": 3}.get(i, 0))
        total_effect += {"LOW": 0.05, "MEDIUM": 0.15, "HIGH": 0.30, "EXTREME": 0.50}.get(intensite, 0)
    summary["total_effect"] = round(total_effect, 2)

    heatmap = build_heatmap("MEDIUM", summary.get("weather_effect", 0))

    return {
        "summary":            summary,
        "weather":            weather.get("current", {}),
        "hourly":             weather.get("hourly", {}),
        "holidays":           holidays,
        "market":             market,
        "events":             ooredoo_events,
        "events_actifs":      events_actifs,
        "events_en_cours":    events_en_cours,
        "events_a_venir":     events_a_venir,
        "seasonal_factors":   market.get("seasonal_factors", {}),
        "competitors":        market.get("competitors", []),
        "competitor_pricing": market.get("competitor_pricing", []),
        "mnp_net":            market.get("mnp_net", {}),
        "agent_kpi_trend":    market.get("agent_kpi_trend", {}),
        "heatmap":            heatmap,
    }