"""
Scraper Ooredoo Tunisie — Playwright (JS rendu).
Récupère les offres et promotions en temps réel.
Cache 1h pour éviter les requêtes répétées.
"""
import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_FILE = Path(__file__).parent.parent.parent.parent / "data" / "cache" / "ooredoo_events.json"
CACHE_TTL  = 3600  # 1 heure


# ─────────────────────────────────────────────────────────────────
# CACHE
# ─────────────────────────────────────────────────────────────────

def _load_cache() -> dict | None:
    try:
        if not CACHE_FILE.exists():
            return None
        data       = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(data.get("fetched_at", "2000-01-01"))
        age        = (datetime.now() - fetched_at).total_seconds()
        if age < CACHE_TTL:
            logger.info(f"[SCRAPER] Cache valide ({age:.0f}s < {CACHE_TTL}s)")
            return data
        logger.info(f"[SCRAPER] Cache expiré ({age:.0f}s)")
        return None
    except Exception as e:
        logger.warning(f"[SCRAPER] Cache load error: {e}")
        return None


def _save_cache(data: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        logger.info(f"[SCRAPER] Cache sauvegardé → {CACHE_FILE}")
    except Exception as e:
        logger.warning(f"[SCRAPER] Cache save error: {e}")


# ─────────────────────────────────────────────────────────────────
# SCRAPER PRINCIPAL
# ─────────────────────────────────────────────────────────────────

async def scrape_ooredoo_events() -> dict:
    cached = _load_cache()
    if cached:
        return cached

    logger.info("[SCRAPER] Lancement Playwright pour Ooredoo...")

    try:
        from playwright.async_api import async_playwright

        # ── Fix Windows asyncio + FastAPI ─────────────────
        import asyncio
        import sys
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        # ── Lancer dans un thread séparé ──────────────────
        import concurrent.futures
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            _scrape_sync
        )
        return result

    except Exception as e:
        logger.warning(f"[SCRAPER] Playwright error: {str(e)[:50]} → fallback")
        return _fallback_events()


def _scrape_sync() -> dict:
    """Scraping synchrone dans un thread séparé."""
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_scrape_async())
    finally:
        loop.close()


async def _scrape_async() -> dict:
    """Scraping asynchrone réel."""
    from playwright.async_api import async_playwright

    all_events = []

    pages_config = [
        {"url": "https://www.ooredoo.tn/particuliers/promotions/", "category": "promotion", "label": "promotions", "timeout": 15000},
        {"url": "https://www.ooredoo.tn/particuliers/internet/",   "category": "internet",  "label": "internet",   "timeout": 15000},
        {"url": "https://www.ooredoo.tn/particuliers/mobile/",     "category": "mobile",    "label": "mobile",     "timeout": 15000},
    ]

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="fr-FR",
        )
        page = await context.new_page()
        page.set_default_timeout(15000)

        for cfg in pages_config:
            try:
                logger.info(f"[SCRAPER] Scraping {cfg['label']}...")
                await page.goto(cfg["url"], wait_until="domcontentloaded", timeout=cfg["timeout"])
                await page.wait_for_timeout(2000)
                offers = await _extract_offers_from_page(page, cfg["category"])
                if offers:
                    all_events.extend(offers)
                    logger.info(f"[SCRAPER] {cfg['label'].capitalize()}: {len(offers)} offres")
            except Exception as e:
                logger.warning(f"[SCRAPER] {cfg['label']} error: {str(e)[:60]}")
                continue

        await browser.close()

    # Dédupliquer
    seen, unique = set(), []
    for e in all_events:
        key = e.get("title", "").lower().strip()[:50]
        if key and len(key) > 4 and key not in seen:
            seen.add(key)
            unique.append(e)
    events = unique[:20]

    if not events:
        return _fallback_events()

    promotions = [e for e in events if e.get("type") == "promotion"]
    new_offers = [e for e in events if e.get("type") == "new_offer"]
    tarifs     = [e for e in events if e.get("type") == "tarif"]

    if not promotions:
        for e in tarifs:
            if any(k in e.get("title", "").lower() for k in ["promo","offre","bonus","gratuit"]):
                e["type"] = "promotion"
                promotions.append(e)

    result = {
        "total":      len(events),
        "events":     events,
        "active":     events,
        "promotions": promotions,
        "new_offers": new_offers,
        "tarifs":     tarifs,
        "source":     "ooredoo.tn (playwright)",
        "fetched_at": datetime.now().isoformat(),
    }

    _save_cache(result)
    logger.info(f"[SCRAPER] ✓ {len(events)} événements | Promos: {len(promotions)} | Offres: {len(new_offers)}")
    return result


# ─────────────────────────────────────────────────────────────────
# EXTRACTION PAR PAGE
# ─────────────────────────────────────────────────────────────────

async def _extract_offers_from_page(page: any, category: str) -> list:
    """
    Extrait les offres depuis une page Ooredoo.
    Essaie d'abord les sélecteurs CSS, puis le texte brut.
    """
    offers = []

    # ── Méthode 1 : Sélecteurs CSS ────────────────────────
    css_selectors = [
        ".offer-card",
        ".card-offer",
        ".product-card",
        ".promo-card",
        ".offer-item",
        ".package-card",
        ".oo-card",
        ".oo-offer",
        ".oo-package",
        "[class*='offer']",
        "[class*='promo']",
        "[class*='package']",
        "[class*='card']",
        "article",
        ".item",
    ]

    for selector in css_selectors:
        try:
            elements = await page.query_selector_all(selector)
            if not elements or len(elements) < 2:
                continue

            for el in elements[:10]:
                try:
                    text = await el.inner_text()
                    text = text.strip()

                    if len(text) < 8 or len(text) > 600:
                        continue

                    # Ignorer les éléments nav/footer
                    if any(k in text.lower() for k in [
                        "accueil", "connexion", "mon compte",
                        "contact", "footer", "copyright"
                    ]):
                        continue

                    lines = [
                        l.strip() for l in text.split("\n")
                        if l.strip() and len(l.strip()) > 3
                    ]
                    if not lines:
                        continue

                    title = lines[0][:100]
                    price = _extract_price(text)

                    offers.append({
                        "title":     title,
                        "type":      _classify_offer(title, text, category),
                        "category":  category,
                        "price":     price,
                        "details":   " | ".join(lines[1:3]) if len(lines) > 1 else "",
                        "is_active": True,
                        "scraped":   True,
                        "date":      datetime.now().isoformat(),
                    })

                except Exception:
                    continue

            if len(offers) >= 3:
                break

        except Exception:
            continue

    # ── Méthode 2 : Texte brut si peu de résultats ────────
    if len(offers) < 3:
        try:
            body_text = await page.inner_text("body")
            raw_offers = _extract_from_raw_text(body_text, category)
            offers.extend(raw_offers)
        except Exception:
            pass

    # ── Méthode 3 : Meta tags et titres ───────────────────
    if len(offers) < 2:
        try:
            titles = await page.query_selector_all("h1, h2, h3")
            for t in titles[:10]:
                try:
                    text = await t.inner_text()
                    text = text.strip()
                    if len(text) < 5 or len(text) > 150:
                        continue

                    # Filtrer les titres pertinents
                    text_lower = text.lower()
                    if any(k in text_lower for k in [
                        "forfait", "offre", "promo", "5g", "4g",
                        "fibre", "internet", "data", "dt", "tnd",
                        "mobile", "box", "recharge"
                    ]):
                        price = _extract_price(text)
                        offers.append({
                            "title":     text[:100],
                            "type":      _classify_offer(text, "", category),
                            "category":  category,
                            "price":     price,
                            "details":   "",
                            "is_active": True,
                            "scraped":   True,
                            "date":      datetime.now().isoformat(),
                        })
                except Exception:
                    continue
        except Exception:
            pass

    return offers


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def _extract_price(text: str) -> str:
    """Extrait le prix depuis le texte."""
    patterns = [
        r"(\d+(?:[.,]\d+)?)\s*DT\s*/?\s*mois",
        r"(\d+(?:[.,]\d+)?)\s*TND\s*/?\s*mois",
        r"(\d+(?:[.,]\d+)?)\s*DT",
        r"(\d+(?:[.,]\d+)?)\s*TND",
        r"(\d+(?:[.,]\d+)?)\s*dt",
        r"(\d+)\s*dinars?",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            full_match = m.group(0).strip()
            return full_match if len(full_match) <= 20 else f"{m.group(1)} DT"
    return ""


def _classify_offer(title: str, text: str, category: str) -> str:
    """Classifie le type d'offre Ooredoo."""
    combined = (title + " " + text).lower()

    # ── Promotions ────────────────────────────────────────
    if any(k in combined for k in [
        "promo", "offre spéciale", "réduction", "gratuit",
        "bonus", "double", "cadeau", "remise", "économisez",
        "-50%", "-30%", "-20%", "50%", "30%", "offert",
        "promotion", "special", "deal", "solde"
    ]):
        return "promotion"

    # ── Nouvelles offres ──────────────────────────────────
    if any(k in combined for k in [
        "nouveau", "new", "lancé", "découvrez", "exclusif",
        "disponible", "arrivage", "maintenant", "déjà",
        "vient de", "tout nouveau"
    ]):
        return "new_offer"

    # ── Catégorie promotion si page promos ────────────────
    if category == "promotion":
        return "promotion"

    # ── Tarifs ────────────────────────────────────────────
    if any(k in combined for k in [
        "forfait", "4g", "5g", "fibre", "data", "go",
        "appels", "sms", "internet", "box", "dt/mois",
        "tnd/mois", "abonnement", "mensuel"
    ]):
        return "tarif"

    return "general"


def _extract_from_raw_text(text: str, category: str) -> list:
    """Extraction depuis le texte brut de la page."""
    offers   = []
    lines    = [l.strip() for l in text.split("\n") if l.strip()]
    keywords = [
        "forfait", "offre", "promo", "5g", "4g", "fibre",
        "internet", "data", "go", "dt", "tnd", "box",
        "recharge", "mobile", "appels"
    ]

    seen_titles = set()

    for line in lines:
        if len(line) < 8 or len(line) > 120:
            continue

        # Ignorer navigation et footer
        if any(k in line.lower() for k in [
            "accueil", "connexion", "contact", "©", "copyright",
            "politique", "mentions", "plan du site"
        ]):
            continue

        line_lower = line.lower()
        if any(k in line_lower for k in keywords):
            key = line.lower()[:40]
            if key in seen_titles:
                continue
            seen_titles.add(key)

            price = _extract_price(line)
            offers.append({
                "title":     line[:100],
                "type":      _classify_offer(line, "", category),
                "category":  category,
                "price":     price,
                "details":   "",
                "is_active": True,
                "scraped":   True,
                "date":      datetime.now().isoformat(),
            })

        if len(offers) >= 6:
            break

    return offers


# ─────────────────────────────────────────────────────────────────
# FALLBACK
# ─────────────────────────────────────────────────────────────────

def _fallback_events() -> dict:
    """Événements Ooredoo connus comme fallback."""
    events = [
        {
            "title":     "Forfait Max 5G — 100Go à 49 DT/mois",
            "type":      "tarif",
            "category":  "Mobile",
            "price":     "49 DT/mois",
            "details":   "5G disponible dans les grandes villes",
            "is_active": True,
            "scraped":   False,
            "date":      datetime.now().isoformat(),
        },
        {
            "title":     "Box Fibre Ooredoo — 200 Mbps",
            "type":      "tarif",
            "category":  "Internet",
            "price":     "59 DT/mois",
            "details":   "Installation gratuite ce mois",
            "is_active": True,
            "scraped":   False,
            "date":      datetime.now().isoformat(),
        },
        {
            "title":     "iPhone 16 Pro — Disponible en boutique",
            "type":      "new_offer",
            "category":  "Smartphone",
            "price":     "À partir de 3,299 DT",
            "details":   "Stock limité",
            "is_active": True,
            "scraped":   False,
            "date":      datetime.now().isoformat(),
        },
        {
            "title":     "Promo Recharge Double — Weekend",
            "type":      "promotion",
            "category":  "Prépayé",
            "price":     "Bonus 100%",
            "details":   "Valable samedi et dimanche",
            "is_active": True,
            "scraped":   False,
            "date":      datetime.now().isoformat(),
        },
        {
            "title":     "Samsung Galaxy A55 5G — Bundle Exclusif",
            "type":      "promotion",
            "category":  "Smartphone",
            "price":     "1,299 DT + forfait offert 3 mois",
            "details":   "Bundle exclusif boutique",
            "is_active": True,
            "scraped":   False,
            "date":      datetime.now().isoformat(),
        },
        {
            "title":     "Forfait Famille 5G — 4 lignes",
            "type":      "promotion",
            "category":  "Mobile",
            "price":     "120 DT/mois",
            "details":   "4 lignes 5G incluses — économie 40%",
            "is_active": True,
            "scraped":   False,
            "date":      datetime.now().isoformat(),
        },
    ]

    promotions = [e for e in events if e["type"] == "promotion"]
    new_offers = [e for e in events if e["type"] == "new_offer"]
    tarifs     = [e for e in events if e["type"] == "tarif"]

    return {
        "total":      len(events),
        "events":     events,
        "active":     events,
        "promotions": promotions,
        "new_offers": new_offers,
        "tarifs":     tarifs,
        "source":     "fallback_hardcoded",
        "fetched_at": datetime.now().isoformat(),
    }