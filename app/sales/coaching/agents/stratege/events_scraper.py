"""
Events Scraper — détection d'événements réels (avec dates) et stockage PG.
==========================================================================
Scrape les sources web accessibles et upsert dans market.events pour que
le Stratège (contexte ventes) et l'Inventory (contexte stock) les utilisent :

  - festivaldecarthage.tn : programme complet du FIC (concerts datés + prix)
    → 1 événement par soirée (sous_type=CONCERT) + mise à jour des bornes
      du festival agrégé (sous_type=FESTIVAL).
  - ooredoo.tn (si joignable — souvent bloqué depuis ce réseau) : offres/promos
    → sous_type=PROMO_OPERATEUR (type CONCURRENTIEL).

Idempotent : clé = nom normalisé (alphanumérique ASCII) + start_date.
Contrainte events_event_type_check : types autorisés RELIGIEUX, SCOLAIRE,
SPORTIF, COMMERCIAL, NATIONAL, CONCURRENTIEL, METEO, RESEAU.

Usage :
    python -m app.sales.coaching.agents.stratege.events_scraper   # one-shot
    await refresh_events()                                        # depuis l'app
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import date, datetime

import asyncpg
import httpx

logger = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:root@localhost:5432/ooredoo_sales")
UA     = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36")

REFRESH_INTERVAL_S = int(os.getenv("EVENTS_SCRAPER_INTERVAL_S", str(24 * 3600)))

_MONTHS_FR = {
    "jan": 1, "janv": 1, "janvier": 1,
    "fev": 2, "fév": 2, "février": 2, "fevrier": 2,
    "mar": 3, "mars": 3,
    "avr": 4, "avril": 4,
    "mai": 5,
    "juin": 6,
    "juil": 7, "juillet": 7,
    "aout": 8, "août": 8, "aou": 8, "aoû": 8,
    "sep": 9, "sept": 9, "septembre": 9,
    "oct": 10, "octobre": 10,
    "nov": 11, "novembre": 11,
    "dec": 12, "déc": 12, "décembre": 12, "decembre": 12,
}


def _strip_html(html: str) -> str:
    txt = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    txt = re.sub(r"<style[\s\S]*?</style>", " ", txt, flags=re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = txt.replace("&#8211;", "–").replace("&amp;", "&").replace("&#038;", "&").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", txt)


def _parse_month(word: str) -> int | None:
    return _MONTHS_FR.get(word.lower().strip("."))


async def _fetch_text(url: str, timeout: float = 20.0) -> str:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                 headers={"User-Agent": UA}) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return _strip_html(resp.text)


# ── Parser : Festival International de Carthage ───────────────────────────────

# Ex : « Juil 16 2026 صابر الرباعي Théâtre Romain de Carthage 100DT »
_FIC_RE = re.compile(
    r"(Jan|Fév|Fev|Mar|Avr|Mai|Juin|Juil|Août|Aout|Sep|Oct|Nov|Déc|Dec)\w*\.?\s+"
    r"(\d{1,2})\s+(\d{4})\s+(.{3,80}?)\s+Théâtre Romain de Carthage\s+(\d+)\s*DT",
    re.IGNORECASE,
)


async def scrape_carthage() -> list[dict]:
    """Programme du FIC : un événement par soirée de concert."""
    text = await _fetch_text("https://festivaldecarthage.tn/")
    events = []
    for m in _FIC_RE.finditer(text):
        month = _parse_month(m.group(1))
        if not month:
            continue
        try:
            d = date(int(m.group(3)), month, int(m.group(2)))
        except ValueError:
            continue
        artist = m.group(4).strip(" -–|")
        price  = int(m.group(5))
        # Prix élevé ≈ tête d'affiche ≈ affluence forte
        intensite = "HIGH" if price >= 80 else "MEDIUM" if price >= 40 else "LOW"
        events.append({
            "event_name": f"FIC 2026 — {artist[:60]}",
            "event_type": "NATIONAL",
            "sous_type":  "CONCERT",
            "start_date": d, "end_date": d,
            "intensite":  intensite, "scope": "REGIONAL",
            "uplift": {"terminal": 0.02, "forfait": 0.05, "sim": 0.10,
                       "recharge": 0.25, "accessoire": 0.20},
            "note_strategie": (
                f"Concert {artist} au Théâtre Romain de Carthage ({price} DT). "
                "Ooredoo partenaire du festival — affluence 19h-23h : pousser "
                "recharges data, batteries externes, écouteurs avant la soirée."
            ),
            "source": "scraper_festivaldecarthage.tn",
        })
    logger.info("[EVENTS-SCRAPER] Carthage : %d concert(s) daté(s)", len(events))
    return events


# ── Parser : offres Ooredoo (si le site répond) ───────────────────────────────

_OFFER_KEYWORDS = ("promo", "offre", "bonus", "gratuit", "réduction", "solde", "-50", "-30")

# Fragments de navigation que le découpage en phrases capture comme des offres :
# menus, barres de recherche, liens. Ils ont pollué market.events avec des lignes
# du type « Offre Ooredoo — Eshop Ooredoo Particulier Business Recherche… ».
_NAV_NOISE = re.compile(
    r"(eshop|recherche de produits|acc[eè]s rapide|t[ée]l[ée]chargez|"
    r"notre s[ée]lection|par ici|https?://|www\.)",
    re.IGNORECASE,
)


def _looks_like_offer(line: str) -> bool:
    """Une offre annonce un prix, une remise ou une durée — pas juste un mot-clé."""
    if _NAV_NOISE.search(line):
        return False
    return bool(re.search(r"(\d+\s*(dt|dinars?|go|%|mois)|-\s*\d+\s*%)", line, re.IGNORECASE))


async def scrape_ooredoo_offers() -> list[dict]:
    """Promos ooredoo.tn stockées comme événements PROMO_OPERATEUR (durée 30j)."""
    events = []
    try:
        text = await _fetch_text("https://www.ooredoo.tn/particuliers/promotions/", timeout=15.0)
    except Exception as e:
        logger.info("[EVENTS-SCRAPER] ooredoo.tn injoignable (%s) — offres ignorées",
                    str(e)[:60])
        return events

    today = date.today()
    seen: set[str] = set()
    for line in re.split(r"(?<=[.!?])\s+|\|", text):
        low = line.lower()
        if not any(k in low for k in _OFFER_KEYWORDS):
            continue
        if not _looks_like_offer(line):
            continue
        title = line.strip()[:90]
        key = re.sub(r"[^a-z0-9]", "", low)[:40]
        if len(title) < 10 or key in seen:
            continue
        seen.add(key)
        events.append({
            "event_name": f"Offre Ooredoo — {title}"[:150],
            "event_type": "CONCURRENTIEL",
            "sous_type":  "PROMO_OPERATEUR",
            "start_date": today, "end_date": today.replace(day=today.day),  # ajusté ci-dessous
            "intensite":  "MEDIUM", "scope": "NATIONAL",
            "uplift": {"terminal": 0.05, "forfait": 0.10, "sim": 0.05,
                       "recharge": 0.10, "accessoire": 0.05},
            "note_strategie": f"Offre détectée sur ooredoo.tn : {title}. Mentionner en boutique.",
            "source": "scraper_ooredoo.tn",
        })
        if len(events) >= 8:
            break
    # Validité par défaut 30 jours (les pages promo n'affichent pas toujours la fin)
    from datetime import timedelta
    for e in events:
        e["end_date"] = today + timedelta(days=30)
    logger.info("[EVENTS-SCRAPER] Ooredoo : %d offre(s) détectée(s)", len(events))
    return events


# ── Upsert PostgreSQL ─────────────────────────────────────────────────────────

def _norm_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


async def store_events(events: list[dict]) -> int:
    """
    Upsert idempotent. Retourne le nombre de lignes insérées.

    Clé d'idempotence : le nom normalisé SEUL pour les promotions opérateur,
    nom + start_date pour les événements datés (concerts, fêtes).

    Une promotion n'a pas de date de début connue — le scraper lui attribuait
    date.today(). La clé incluant start_date, chaque exécution quotidienne
    créait donc une nouvelle ligne pour la même offre : 5 promotions étaient
    devenues 35 lignes en une semaine, saturant le contexte du Stratège. Pour
    ces événements-là, on prolonge la fenêtre de validité de la ligne existante
    au lieu d'en insérer une nouvelle.
    """
    if not events:
        return 0
    conn = await asyncpg.connect(DB_URL)
    inserted = 0
    try:
        rows = await conn.fetch("SELECT event_name, start_date, sous_type FROM market.events")
        existing      = {(_norm_key(r["event_name"]), r["start_date"]) for r in rows}
        existing_names = {_norm_key(r["event_name"]) for r in rows}

        for ev in events:
            undated = ev.get("sous_type") == "PROMO_OPERATEUR"
            if undated and _norm_key(ev["event_name"]) in existing_names:
                # Offre déjà connue : on prolonge sa validité, sans doublon.
                await conn.execute("""
                    UPDATE market.events
                    SET end_date = GREATEST(end_date, $2)
                    WHERE lower(regexp_replace(event_name, '[^a-zA-Z0-9]', '', 'g')) = $1
                """, _norm_key(ev["event_name"]), ev["end_date"])
                continue
            if (_norm_key(ev["event_name"]), ev["start_date"]) in existing:
                continue
            up = ev.get("uplift", {})
            await conn.execute("""
                INSERT INTO market.events
                    (event_id, event_name, event_type, sous_type, start_date, end_date,
                     scope, uplift_terminal, uplift_forfait, uplift_sim,
                     uplift_recharge, uplift_accessoire, intensite,
                     source_donnee, note_strategie, created_at)
                VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6,
                        $7, $8, $9, $10, $11, $12, $13, $14, NOW())
            """,
                ev["event_name"][:150], ev["event_type"], ev["sous_type"],
                ev["start_date"], ev["end_date"], ev.get("scope", "NATIONAL"),
                up.get("terminal", 0), up.get("forfait", 0), up.get("sim", 0),
                up.get("recharge", 0), up.get("accessoire", 0),
                ev.get("intensite", "MEDIUM"), ev.get("source", "scraper"),
                (ev.get("note_strategie") or "")[:1000],
            )
            inserted += 1
            existing_names.add(_norm_key(ev["event_name"]))

        # Réaligne les bornes du festival agrégé sur les vraies dates de concerts
        concert_dates = [e["start_date"] for e in events if e.get("sous_type") == "CONCERT"]
        if concert_dates:
            await conn.execute("""
                UPDATE market.events
                SET start_date = LEAST(start_date, $1),
                    end_date   = GREATEST(end_date, $2)
                WHERE event_name ILIKE '%Festival International de Carthage%'
                  AND sous_type = 'FESTIVAL'
            """, min(concert_dates), max(concert_dates))
    finally:
        await conn.close()
    return inserted


# ── Point d'entrée ────────────────────────────────────────────────────────────

async def refresh_events() -> int:
    """Scrape toutes les sources et stocke — tolérant aux pannes par source."""
    all_events: list[dict] = []
    for scraper in (scrape_carthage, scrape_ooredoo_offers):
        try:
            all_events.extend(await scraper())
        except Exception as e:
            logger.warning("[EVENTS-SCRAPER] %s a échoué : %s", scraper.__name__, str(e)[:100])
    inserted = await store_events(all_events)
    logger.info("[EVENTS-SCRAPER] ✓ %d événement(s) détecté(s), %d nouveau(x) stocké(s)",
                len(all_events), inserted)
    return inserted


async def refresh_events_loop() -> None:
    """Boucle de rafraîchissement quotidienne (lancée au startup de l'app)."""
    while True:
        try:
            await refresh_events()
        except Exception as e:
            logger.warning("[EVENTS-SCRAPER] refresh loop: %s", str(e)[:100])
        await asyncio.sleep(REFRESH_INTERVAL_S)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(refresh_events())
