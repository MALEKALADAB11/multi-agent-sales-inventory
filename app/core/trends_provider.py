"""
trends_provider.py — Signal "tendances marché" partagé Sales × Inventory.
==========================================================================
Le Stratège sales scrape ooredoo.tn (promotions/internet/mobile) et met le
résultat en cache JSON (scraper.py). Ce module expose ce cache aux agents
INVENTORY (ContextAgent) sans re-scraper : lecture directe du fichier avec
tolérance au cache "expiré" (une offre d'hier reste un signal de demande
valable pour un horizon stock de 7 jours).

Aucun appel réseau ici — le rafraîchissement appartient au cycle Stratège.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Une offre scrapée reste un signal exploitable 7 jours pour l'inventory
MAX_STALENESS_DAYS = 7


def _load_scraper_cache() -> Optional[Dict[str, Any]]:
    try:
        from app.sales.coaching.agents.stratege.scraper import CACHE_FILE
        if not CACHE_FILE.exists():
            return None
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(data.get("fetched_at", "2000-01-01"))
        age_days = (datetime.now() - fetched_at).total_seconds() / 86400
        if age_days > MAX_STALENESS_DAYS:
            logger.info("[Trends] cache scraper trop ancien (%.1f j) — ignoré", age_days)
            return None
        return data
    except Exception as e:
        logger.warning("[Trends] lecture cache scraper: %s", e)
        return None


def get_market_offers(category: Optional[str] = None, max_items: int = 8) -> List[Dict[str, Any]]:
    """
    Offres/promotions Ooredoo actives (scrapées par le Stratège).

    Args:
        category: filtre souple sur la catégorie produit inventory
                  (ex: 'smartphone' matche les offres 'promotion'/'mobile').
        max_items: nombre max d'offres retournées.

    Returns:
        Liste d'offres normalisées : title, type, category, price, details, date.
    """
    data = _load_scraper_cache()
    if not data:
        return []

    offers = list(data.get("active") or data.get("events") or [])
    if category and category != "unknown":
        cat_l = str(category).lower()
        # Matching souple : une promo 'mobile' concerne les smartphones, etc.
        related = {
            "smartphone": {"promotion", "mobile", "new_offer"},
            "accessoire": {"promotion", "new_offer"},
            "internet":   {"internet", "promotion"},
            "modem":      {"internet", "promotion"},
        }
        wanted = None
        for key, cats in related.items():
            if key in cat_l:
                wanted = cats
                break
        if wanted:
            filtered = [o for o in offers
                        if o.get("category") in wanted or o.get("type") in wanted]
            offers = filtered or offers   # jamais vide à cause d'un filtre trop strict

    normalized = [
        {
            "title":    str(o.get("title", ""))[:80],
            "type":     o.get("type", "offer"),
            "category": o.get("category", ""),
            "price":    o.get("price", ""),
            "details":  str(o.get("details", ""))[:160],
            "date":     o.get("date", ""),
        }
        for o in offers[:max_items]
    ]
    return normalized
