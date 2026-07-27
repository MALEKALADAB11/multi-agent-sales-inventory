"""
Scraper Ooredoo Tunisie — Playwright (JS rendu).
Récupère les offres et promotions en temps réel.
Cache 1h pour éviter les requêtes répétées.
"""
import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_FILE = Path(__file__).parent.parent.parent.parent / "data" / "cache" / "ooredoo_events.json"
CACHE_TTL  = 3600  # 1 heure

# ooredoo.tn est injoignable depuis le réseau de déploiement : les trois pages
# partaient systématiquement en timeout (~23 s au total) pour finir sur le
# fallback, et comme seul un scraping réussi était mis en cache, ce coût était
# repayé à chaque cycle. Deux garde-fous :
#   - budget global : au-delà, on abandonne et on sert le fallback ;
#   - cache négatif : un échec est mémorisé (TTL court) pour ne pas relancer
#     Playwright à chaque appel tant que le site ne répond pas.
SCRAPER_BUDGET_S    = float(os.getenv("OOREDOO_SCRAPER_BUDGET_S", "6"))
NEGATIVE_CACHE_TTL  = int(os.getenv("OOREDOO_SCRAPER_FAIL_TTL_S", "900"))
NEGATIVE_CACHE_MAX  = int(os.getenv("OOREDOO_SCRAPER_FAIL_TTL_MAX_S", str(6 * 3600)))
SCRAPER_ENABLED     = os.getenv("OOREDOO_SCRAPER_ENABLED", "1") not in ("0", "false", "False")


# ─────────────────────────────────────────────────────────────────
# CACHE
# ─────────────────────────────────────────────────────────────────

def _load_cache() -> dict | None:
    """
    Cache disque. Un fallback est mémorisé avec un TTL court (cache négatif)
    plutôt qu'un TTL d'une heure : le site peut redevenir joignable, mais on ne
    veut pas relancer Playwright à chaque cycle en attendant.
    """
    try:
        if not CACHE_FILE.exists():
            return None
        data       = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(data.get("fetched_at", "2000-01-01"))
        age        = (datetime.now() - fetched_at).total_seconds()
        ttl        = _negative_ttl(int(data.get("fail_streak", 1))) \
                     if data.get("degraded") else CACHE_TTL
        if age < ttl:
            logger.info("[SCRAPER] Cache %s valide (%.0fs < %ds)",
                        "dégradé" if data.get("degraded") else "", age, ttl)
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

def _negative_ttl(fail_streak: int) -> int:
    """
    Backoff exponentiel sur les échecs consécutifs, plafonné.

    Un TTL négatif fixe de 15 min faisait repayer le budget Playwright quatre
    fois par heure alors que le site est indisponible de façon durable. En
    doublant à chaque échec, on converge vers une seule tentative toutes les
    quelques heures — assez pour détecter un retour du site sans peser sur les
    cycles.
    """
    return min(NEGATIVE_CACHE_TTL * (2 ** max(0, fail_streak - 1)), NEGATIVE_CACHE_MAX)


def _degraded_fallback(reason: str) -> dict:
    """Fallback marqué comme dégradé et mis en cache pour éviter de le repayer."""
    previous = 0
    try:
        if CACHE_FILE.exists():
            old = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            previous = int(old.get("fail_streak", 0)) if old.get("degraded") else 0
    except Exception:
        previous = 0

    result = _fallback_events()
    result["degraded"]        = True
    result["degraded_reason"] = reason
    result["fail_streak"]     = previous + 1
    result["source"]          = f"fallback ({reason})"
    result["fetched_at"]      = datetime.now().isoformat()
    _save_cache(result)
    logger.info("[SCRAPER] échec #%d — prochaine tentative dans %d min",
                result["fail_streak"], _negative_ttl(result["fail_streak"]) // 60)
    return result


async def scrape_ooredoo_events() -> dict:
    cached = _load_cache()
    if cached:
        return cached

    if not SCRAPER_ENABLED:
        logger.info("[SCRAPER] désactivé (OOREDOO_SCRAPER_ENABLED=0)")
        return _degraded_fallback("désactivé")

    logger.info("[SCRAPER] Lancement Playwright pour Ooredoo (budget %.0fs)...",
                SCRAPER_BUDGET_S)

    try:
        # ── Fix Windows asyncio + FastAPI ─────────────────
        import sys
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        # ── Lancer dans un thread séparé, sous budget de temps ──────────────
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _scrape_sync),
            timeout=SCRAPER_BUDGET_S,
        )
        return result

    except asyncio.TimeoutError:
        logger.warning("[SCRAPER] budget %.0fs dépassé → fallback dégradé (cache %ds)",
                       SCRAPER_BUDGET_S, NEGATIVE_CACHE_TTL)
        return _degraded_fallback("timeout")
    except Exception as e:
        logger.warning(f"[SCRAPER] Playwright error: {str(e)[:50]} → fallback dégradé")
        return _degraded_fallback("erreur")


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
        return _degraded_fallback("aucune offre extraite")

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
    """
    Promotions de repli — lues dans PostgreSQL, jamais inventées.

    Cette fonction renvoyait six offres écrites à la main (« iPhone 16 Pro à
    partir de 3 299 DT », « Samsung Galaxy A55 5G — Bundle Exclusif »…). Comme
    ooredoo.tn est injoignable depuis ce réseau, c'était en pratique la seule
    source d'« offres Ooredoo actives » : ces produits fictifs remontaient dans
    le prompt du Stratège et jusque dans la modal du dashboard.

    inventory.promotions porte les promotions réellement en cours, avec leur
    remise et leur date de fin. En cas d'échec DB, on renvoie une structure
    vide : mieux vaut aucune offre qu'une offre imaginaire.
    """
    events = []
    try:
        from app.inventory.repositories.inventory_repo import SyncInventoryRepo

        rows = SyncInventoryRepo.fetch_active_promotions(limit=8) or []
        for r in rows:
            remise = r.get("discount_pct")
            events.append({
                "title":     str(r.get("promo_name") or ""),
                "type":      "promotion",
                "category":  str(r.get("product_name") or ""),
                "price":     f"-{float(remise):.0f}%" if remise else "",
                "details":   (f"Valable jusqu'au {r.get('end_date')}"
                              if r.get("end_date") else ""),
                "is_active": True,
                "scraped":   False,
                "date":      datetime.now().isoformat(),
            })
        logger.info("[SCRAPER] Fallback PostgreSQL — %d promotion(s) active(s)", len(events))
    except Exception as e:
        logger.warning("[SCRAPER] Fallback PostgreSQL indisponible: %s", str(e)[:100])

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
        "source":     "postgresql_promotions",
        "fetched_at": datetime.now().isoformat(),
    }
