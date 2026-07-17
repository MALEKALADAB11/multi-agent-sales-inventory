"""

coach_chat.py — Coach Chat "Claude-Like" v10.0 (Production)
=============================================================
Philosophie : un coach qui PENSE, pas qui suit des règles.

ARCHITECTURE :
  1. PERSONA-FIRST    — identité forte + 6 few-shots dans le prompt système
  2. LEAN PROMPTS     — ~350 tokens système + ~200 tokens contexte = ~550 total
                        (gpt-oss-120b:free digère mieux des prompts courts)
  3. CONTEXT SELECTIF — seules les données utiles à CETTE question sont injectées
  4. DEDUP CACHE 20s  — évite les réponses identiques sur double envoi rapide
  5. RETRY 3 NIVEAUX  — OpenRouter full → OpenRouter stripped → Ollama → fallback intent
  6. FALLBACK COHERENT— jamais d'alertes stock pour "bonjour"

LLM primaire : OpenRouter gpt-oss-120b:free
LLM fallback  : Ollama (premier modèle de chat disponible)
"""

import asyncio
import json
import logging
import os
import random
import re
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from app.core.config import DEFAULT_STORE_ID

try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    _limiter = Limiter(key_func=get_remote_address)
    _RATE_LIMIT_AVAILABLE = True
except ImportError:
    _limiter = None
    _RATE_LIMIT_AVAILABLE = False

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/coach", tags=["coach"])

def _rate_limit(limit_str: str):
    """Decorator that applies slowapi rate limit if available, otherwise no-op."""
    def decorator(func):
        if _RATE_LIMIT_AVAILABLE and _limiter:
            return _limiter.limit(limit_str)(func)
        return func
    return decorator

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

OLLAMA_URL       = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OPENROUTER_KEY   = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
OPENROUTER_URL   = "https://openrouter.ai/api/v1/chat/completions"
_OR_MODEL_ROTATION = [
    os.getenv("OPENROUTER_MODEL",          "nvidia/nemotron-3-super-120b-a12b:free"),
    os.getenv("OPENROUTER_MODEL_FALLBACK", "openai/gpt-oss-120b:free"),
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
]
MILVUS_URI       = "http://localhost:19530"
COLLECTION       = "coaching_scripts"
EMBED_DIM        = 768

DB_CFG = {
    "host":     os.getenv("POSTGRES_HOST", os.getenv("DB_HOST", "localhost")),
    "port":     int(os.getenv("POSTGRES_PORT", os.getenv("DB_PORT", "5432"))),
    "dbname":   os.getenv("POSTGRES_DB", os.getenv("DB_NAME", "ooredoo_sales")),
    "user":     os.getenv("POSTGRES_USER", os.getenv("DB_USER", "postgres")),
    "password": os.getenv("POSTGRES_PASSWORD", os.getenv("DB_PASSWORD", "root")),
}

_STORE_MAP = {
    "store-lac2": DEFAULT_STORE_ID, "OOR_LAC_01": DEFAULT_STORE_ID, "lac2": DEFAULT_STORE_ID,
    "store-menzah": "M01", "OOR_MENZAH_02": "M01", "menzah": "M01",
    "store-sfax": "S01", "OOR_SFAX_03": "S01", "sfax": "S01",
}

def _normalize_store(store_id: str) -> str:
    return _STORE_MAP.get(store_id, store_id) if store_id else DEFAULT_STORE_ID

# ── Catalogue dynamique (DB, TTL 10min) ──────────────────────────────────────

_catalog_cache: str = ""
_catalog_cache_ts: float = 0.0
_CATALOG_TTL = 600.0

_CATALOG_FALLBACK = (
    "Terminaux  : iPhone 16 Pro 1 299 TND · Samsung A55 5G 899 TND · "
    "Galaxy S25 Ultra 1 599 TND · INFINIX NOTE 40 349 TND\n"
    "Forfaits   : 5G Max 100Go 49 TND/mois · Flexi 25Go 29 TND/mois · "
    "Unlimited 69 TND/mois · Famille 5G 120 TND/mois\n"
    "Services   : Assurance Premium 9 TND/mois · Cloud Backup 15 TND/mois · TV Streaming 12 TND/mois\n"
    "Accessoires: AirPods Pro 3 279 TND · Apple Watch S10 449 TND\n"
    "Bundle max : iPhone 16 Pro + 5G Max + Assurance ≈ 1 357 TND (54 TND/mois × 24 mois)"
)


def _load_catalog(store_id: str) -> str:
    import psycopg2, psycopg2.extras, time as _t
    global _catalog_cache, _catalog_cache_ts
    if _catalog_cache and (_t.time() - _catalog_cache_ts) < _CATALOG_TTL:
        return _catalog_cache
    try:
        conn = psycopg2.connect(**DB_CFG, connect_timeout=6)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.execute("""
                SELECT p.nom, p.prix_ttc,
                       COALESCE(sl.quantity_available, sl.quantity, 0) AS qty
                FROM sales.produits p
                JOIN inventory.stock_levels sl ON sl.sku=p.sku AND sl.store_id=%s
                WHERE p.flag_terminal=TRUE AND p.prix_ttc>0
                  AND COALESCE(sl.quantity_available, sl.quantity, 0) > 0
                ORDER BY p.prix_ttc DESC LIMIT 8
            """, (store_id,))
            terminals = cur.fetchall()
            cur.execute("""
                SELECT p.nom, p.prix_ttc FROM sales.produits p
                WHERE p.flag_forfait=TRUE AND p.prix_ttc>0
                  AND (p.date_eol IS NULL OR p.date_eol>=CURRENT_DATE)
                ORDER BY p.prix_ttc ASC LIMIT 6
            """)
            forfaits = cur.fetchall()
            cur.execute("""
                SELECT p.nom, p.prix_ttc FROM sales.produits p
                JOIN inventory.stock_levels sl ON sl.sku=p.sku AND sl.store_id=%s
                WHERE p.categorie='70' AND p.prix_ttc>0
                  AND COALESCE(sl.quantity_available, sl.quantity, 0) > 0
                ORDER BY p.prix_ttc DESC LIMIT 4
            """, (store_id,))
            accessoires = cur.fetchall()
        finally:
            cur.close(); conn.close()

        lines = []
        if terminals:
            lines.append("Terminaux  : " + " · ".join(
                f"{r['nom']} {r['prix_ttc']:.0f} TND (stock {r['qty']})" for r in terminals))
        if forfaits:
            lines.append("Forfaits   : " + " · ".join(
                f"{r['nom']} {r['prix_ttc']:.0f} TND/mois" for r in forfaits))
        if accessoires:
            lines.append("Accessoires: " + " · ".join(
                f"{r['nom']} {r['prix_ttc']:.0f} TND" for r in accessoires))
        lines.append("Bundle max : iPhone 16 Pro + 5G Max + Assurance ≈ 1 357 TND (54 TND/mois × 24 mois)")

        result = "\n".join(lines) if lines else _CATALOG_FALLBACK
        _catalog_cache, _catalog_cache_ts = result, _t.time()
        return result
    except Exception as e:
        logger.warning("[COACH CATALOG] %.80s", str(e))
        return _CATALOG_FALLBACK

# ══════════════════════════════════════════════════════════════════════════════
# PERSONA SYSTÈME (stable — ~320 tokens)
# ══════════════════════════════════════════════════════════════════════════════

def _build_system_prompt(catalog: str) -> str:
    return f"""Tu es Coach, l'IA de vente d'Ooredoo Tunisie.
Tu penses comme un directeur commercial senior qui fait du coaching terrain. Tu connais chaque produit par cœur, tu lis une situation de vente en un coup d'œil, tu donnes UN seul conseil actionnable. Pas de liste à rallonge, pas de langage corporatif.

TON STYLE :
• Tutoiement chaleureux — tu es le collègue de confiance du conseiller
• Décisif — 1 action concrète, pas 4 options
• Court — 80 à 140 mots, sauf script complet explicitement demandé
• Contextuel — tu t'appuies sur les vrais chiffres de la boutique
• Termine toujours par "Vas-y !", "À toi !", "Allez !" ou "Maintenant !"

JAMAIS :
• Inventer un prix, une offre ou un SKU hors catalogue ci-dessous
• Inventer un CLIENT, une SITUATION DE VENTE ou un SCÉNARIO qui n'est pas donné dans le bloc SITUATION ci-dessous — si la question du conseiller ne décrit aucun client réel, tu n'en inventes pas un
• Répondre hors vente / stock / Ooredoo Tunisie
• Écrire "je n'ai pas accès" ou "consulte le tableau de bord"
• Produire du JSON, du code, des balises HTML
• Répéter deux fois la même information dans la même réponse

SI TU NE COMPRENDS PAS LA QUESTION, ou si elle ne correspond à aucune situation de vente/stock réelle (message ambigu, hors-sujet non filtré, une question sans rapport) : NE PAS halluciner un client ou un script. Demande une clarification courte en 1 phrase, du style [clarification] ci-dessous.

CATALOGUE OFFICIEL OOREDOO (seuls prix autorisés) :
{catalog}

EXEMPLES DE RÉPONSES PARFAITES :
[salutation] → «Salut {{prenom}} ! {{perf}}% de l'objectif à {{heure}}h — {{observation en 5 mots}}. Tu veux qu'on attaque le gap ou tu as une situation client ?»
[script iPhone] → «Script iPhone 16 Pro : 1. "Tu l'utilises pour quoi surtout ?" 2. Démo live puce A18 Pro — vitesse photo incomparable. 3. "1 299 TND ou 54 TND/mois sur 24 mois." 4. Bundle Assurance 9 TND/mois — écran remplacé en 48h. 5. Close : "Noir titane ou blanc naturel ?" Vas-y !»
[objection trop cher] → «Réponds : "1 299 TND sur 24 mois = 54 TND/mois — moins qu'un café par jour. Et dans 2 ans valeur revente 400+ TND." Close : "Noir titane ou blanc naturel ?" À toi !»
[bilan du jour] → «CA {{ca}} / {{target}} TND ({{perf}}%). Top : {{produit}} ({{qty}} vendus/7j). Dernière vente {{heure}}h. {{conseil Stratège en 1 ligne}}. Allez !»
[rupture stock] → «{{produit}} en rupture = {{nb}} ventes perdues en risque. Met le {{alternatif}} (349 TND) en avant + appelle le manager pour la commande. Maintenant !»
[closing] → «Choix forcé : "Lequel vous correspond le mieux, noir ou blanc ?" Si résistance : "Il nous reste 2 unités — l'offre 0% expire ce soir." Silence 3 secondes. À toi !»
[clarification — question incomprehensible ou sans client réel décrit] → «Je ne suis pas sûr de te suivre — tu veux un script de vente, un point sur le stock, ou un coup de main pour ton objectif du jour ? Dis-m'en un peu plus !»"""

# ══════════════════════════════════════════════════════════════════════════════
# DEDUP CACHE (évite doublons sur envoi rapide — 20 secondes)
# ══════════════════════════════════════════════════════════════════════════════

_resp_cache: dict[str, tuple[str, float]] = {}
_DEDUP_TTL = 20.0

def _cache_get(advisor: str, store: str, msg: str) -> Optional[str]:
    key = f"{advisor}:{store}:{msg.lower().strip()[:120]}"
    if key in _resp_cache:
        reply, ts = _resp_cache[key]
        if time.time() - ts < _DEDUP_TTL:
            return reply
    return None

def _cache_set(advisor: str, store: str, msg: str, reply: str) -> None:
    key = f"{advisor}:{store}:{msg.lower().strip()[:120]}"
    _resp_cache[key] = (reply, time.time())
    if len(_resp_cache) > 300:
        cutoff = time.time() - _DEDUP_TTL * 2
        for k in [k for k, (_, ts) in list(_resp_cache.items()) if ts < cutoff]:
            _resp_cache.pop(k, None)

# ══════════════════════════════════════════════════════════════════════════════
# 1. INTENT CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════

_COACHING_KW: dict[str, list[str]] = {
    "script":    ["script", "comment vendre", "que dire", "pitch", "argumentaire",
                  "donne-moi un script", "comment presenter", "discours de vente"],
    "objection": ["objection", "trop cher", "il refuse", "elle refuse", "pas besoin",
                  "concurrent", "reticent", "comment repondre", "il dit que", "elle dit que",
                  "client dit", "client dit non"],
    "closing":   ["closing", "comment closer", "finaliser", "il hesite", "elle hesite",
                  "indecis", "comment convaincre", "fermer la vente", "signer"],
    "upsell":    ["upsell", "cross-sell", "ajouter", "bundle", "vient d'acheter",
                  "apres la vente", "complementaire", "accessoire"],
    "forfait":   ["convertir", "migration forfait", "recharge vers forfait",
                  "passer en 5g", "changer de forfait", "basculer"],
    "objectif":  ["atteindre objectif", "combler le gap", "rattraper", "plan d'action",
                  "comment faire pour atteindre", "boost ca"],
    "meteo":     ["strategie meteo", "il pleut", "adapter meteo", "beau temps", "mauvais temps"],
}

_INV_KW = [
    "stock", "rupture", "inventaire", "quantite", "disponible", "epuise",
    "combien reste", "en stock", "reapprovisionner", "commande", "livraison",
    "rotation", "dormant", "eoq", "point de commande", "alerte stock", "critique",
]

_SALES_KW = [
    "vente", "objectif", "gap", "ca ", " ca", "chiffre", "performance",
    "client", "forfait", "terminal", "script", "objection", "closing",
    "iphone", "samsung", "assurance",
]

_RECAP_KW = [
    "parle moi", "resume", "bilan", "comment vont", "ventes d aujourd",
    "ventes du jour", "ca du jour", "ou en sont", "etat des ventes",
    "montre moi", "qu est ce qui se vend", "comment se passe", "bilan du jour",
    "resultat", "performance du jour", "rapport du jour", "synthese", "comment on est",
    "comment ca va les ventes", "derniere vente", "dernier vente", "dernieres ventes",
    "dernier achat", "derniere transaction", "vente recente", "achat recent",
    "qui a achete", "dernier client", "quel produit vendu", "top vendeur",
    "top produit", "meilleur produit", "meilleures ventes",
]

# Détection floue : toute question "dernier/dernière X" où X référence une
# vente/transaction/client — capte les formulations non prévues dans la liste ci-dessus
_RECAP_FUZZY_ANCHORS = ["dernier", "derniere", "recent", "recente"]
_RECAP_FUZZY_NOUNS   = ["vente", "achat", "transaction", "client", "produit vendu"]

_OFF_KW = [
    "cybersecurite", "crypto", "bitcoin", "politique", "recette", "cuisine",
    "football", "match", "film", "serie", "jeu video", "gaming", "musique",
    "philosophie", "mathematique", "physique", "chimie", "histoire generale",
    "religion", "horoscope", "code python", "javascript", "programmation",
    "machine learning", "deep learning", "chatgpt", "bourse", "trading",
]

_TELECOM_KW = [
    "ooredoo", "vente", "client", "boutique", "forfait", "stock",
    "objectif", "conseiller", "terminal", "iphone", "samsung", "5g",
    "assurance", "bundle", "coach", "produit", "recharge",
]

_GREETINGS_SET = {
    "bonjour", "bonsoir", "salut", "hello", "hey", "coucou", "bjr", "bj", "slt",
    "hi", "yo", "hola", "bonjour coach", "salut coach", "bonsoir coach", "hey coach",
    "bonne journee", "bonne soiree",
}


def _norm(t: str) -> str:
    for a, b in [("é","e"),("è","e"),("ê","e"),("à","a"),("ô","o"),("î","i"),("ç","c"),("ù","u"),
                 ("â","a"),("û","u"),("ï","i"),("œ","oe"),("'","'"),("-"," ")]:
        t = t.replace(a, b)
    return t


def _is_pure_greeting(msg: str) -> bool:
    n = _norm(msg.strip().lower().rstrip("!?,. "))
    if n in _GREETINGS_SET:
        return True
    words = n.split()
    if not words:          # message vide → pas une salutation (IndexError avant)
        return False
    return len(words) <= 4 and words[0].strip('!?,.') in _GREETINGS_SET


def _classify_intent(message: str) -> dict:
    msg = _norm(message.lower())

    if _is_pure_greeting(msg):
        return {"mode": "greeting", "domain": "sales", "type": "greeting", "confidence": 0.99}

    off_hits = sum(1 for k in _OFF_KW    if k in msg)
    tel_hits = sum(1 for k in _TELECOM_KW if k in msg)
    if off_hits >= 1 and tel_hits == 0:
        return {"mode": "off_topic", "domain": "none", "type": "off_topic", "confidence": 0.96}

    recap_hits = sum(1 for k in _RECAP_KW if k in msg)
    recap_fuzzy = (
        any(a in msg for a in _RECAP_FUZZY_ANCHORS)
        and any(n in msg for n in _RECAP_FUZZY_NOUNS)
    )
    if recap_hits >= 1 or recap_fuzzy:
        return {"mode": "conversation", "domain": "sales", "type": "recap",
                "confidence": min(0.93, 0.72 + recap_hits * 0.08)}

    inv_hits   = sum(1 for k in _INV_KW   if k in msg)
    sales_hits = sum(1 for k in _SALES_KW if k in msg)

    if inv_hits >= 1 and sales_hits >= 2:
        return {"mode": "cross_domain", "domain": "both", "type": "cross_domain", "confidence": 0.88}

    if inv_hits >= 1:
        subtype = "alerte"
        if any(k in msg for k in ["reappro","commande","livraison","eoq"]): subtype = "reorder"
        elif any(k in msg for k in ["rotation","dormant","top ventes"]):    subtype = "rotation"
        elif any(k in msg for k in ["combien","quantite","disponible"]):    subtype = "query"
        return {"mode": "inventory", "domain": "inventory", "type": subtype,
                "confidence": min(0.95, 0.62 + inv_hits * 0.12)}

    best_type, best_score = "general", 0
    for qtype, kws in _COACHING_KW.items():
        hits = sum(1 for k in kws if k in msg)
        if hits > best_score:
            best_score, best_type = hits, qtype

    if best_score >= 2:
        return {"mode": "coaching", "domain": "sales", "type": best_type,
                "confidence": min(0.95, 0.62 + best_score * 0.13)}
    if best_score == 1:
        return {"mode": "coaching", "domain": "sales", "type": best_type, "confidence": 0.72}

    return {"mode": "conversation", "domain": "sales", "type": "general", "confidence": 0.78}

# ══════════════════════════════════════════════════════════════════════════════
# 2. CONTEXT LOADERS (lean — timeout courts, parallèle)
# ══════════════════════════════════════════════════════════════════════════════

def _load_pos_sync(store_id: str) -> dict:
    import psycopg2
    try:
        conn = psycopg2.connect(**DB_CFG, connect_timeout=5)
        cur  = conn.cursor()
        try:
            cur.execute(
                "SELECT ca_total, nb_transactions FROM sales.vw_ca_par_boutique "
                "WHERE store_id=%s ORDER BY date_only DESC LIMIT 1",
                (store_id,),
            )
            row = cur.fetchone()
            if row:
                return {"ca": float(row[0] or 0), "nb_tx": int(row[1] or 0)}
        finally:
            cur.close(); conn.close()
    except Exception as e:
        logger.warning("[COACH POS] %.60s", str(e))
    return {"ca": 0.0, "nb_tx": 0}


def _load_sales_detail_sync(store_id: str) -> dict:
    import psycopg2, psycopg2.extras
    r = {"top_sellers": [], "recent_tx": []}
    try:
        conn = psycopg2.connect(**DB_CFG, connect_timeout=6)
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.execute("""
                SELECT COALESCE(p.nom, t.sku::text) AS nom,
                       SUM(t.quantity) AS qty, SUM(t.lig_ttc) AS ca
                FROM   sales.transactions t
                LEFT JOIN sales.produits p ON p.sku = t.sku
                WHERE  t.store_id = %s
                  AND  t.date_only >= (
                         SELECT MAX(date_only) FROM sales.transactions WHERE store_id=%s
                       ) - INTERVAL '7 days'
                GROUP BY p.nom, t.sku
                ORDER BY ca DESC LIMIT 5
            """, (store_id, store_id))
            r["top_sellers"] = [
                {"nom": x["nom"], "qty": int(x["qty"] or 0), "ca": float(x["ca"] or 0)}
                for x in cur.fetchall()
            ]
            cur.execute("""
                SELECT COALESCE(p.nom, t.sku::text) AS nom,
                       t.quantity, t.lig_ttc, t.heure
                FROM   sales.transactions t
                LEFT JOIN sales.produits p ON p.sku = t.sku
                WHERE  t.store_id = %s
                ORDER BY t.date_only DESC, t.heure DESC LIMIT 3
            """, (store_id,))
            r["recent_tx"] = [
                {"nom": x["nom"], "qty": int(x["quantity"] or 1),
                 "ttc": float(x["lig_ttc"] or 0), "heure": int(x["heure"] or 0)}
                for x in cur.fetchall()
            ]
        finally:
            cur.close(); conn.close()
    except Exception as e:
        logger.warning("[COACH SALES] %.60s", str(e))
    return r


def _load_inventory_context_sync(store_id: str) -> dict:
    """Stock complet : stats + alertes + top vendeurs + recos agent + KPI."""
    import psycopg2, psycopg2.extras
    r = {"stats": {}, "alerts": [], "top_sellers": [], "agent_recos": [],
         "kpi_terminaux": 0, "kpi_forfaits": 0, "ca_moy_agent": 0}
    try:
        conn = psycopg2.connect(**DB_CFG, connect_timeout=10)
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            # Stats globales
            cur.execute("""
                SELECT COUNT(*) AS total,
                  SUM(CASE WHEN COALESCE(quantity_available, quantity, 0) <= 0 THEN 1 ELSE 0 END) AS ruptures,
                  SUM(CASE WHEN COALESCE(quantity_available, quantity, 0) BETWEEN 1 AND 5 THEN 1 ELSE 0 END) AS critiques,
                  SUM(CASE WHEN COALESCE(quantity_available, quantity, 0) > 15 THEN 1 ELSE 0 END) AS ok_count
                FROM inventory.stock_levels WHERE store_id=%s
            """, (store_id,))
            row = cur.fetchone()
            if row:
                r["stats"] = {k: int(row[k] or 0) for k in ["total","ruptures","critiques","ok_count"]}

            # Alertes critiques
            cur.execute("""
                SELECT sl.sku,
                       COALESCE(p.nom, sl.sku::text) AS nom,
                       COALESCE(sl.quantity_available, sl.quantity, 0) AS qty,
                       CASE WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 0 THEN 'rupture'
                            WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 5  THEN 'critical'
                            ELSE 'warning' END AS level,
                       COALESCE(rp.demande_moy_jour, 0.5) AS vel,
                       COALESCE(rp.eoq, 0) AS eoq
                FROM inventory.stock_levels sl
                LEFT JOIN sales.produits p ON p.sku = sl.sku
                LEFT JOIN supply.reorder_params rp ON rp.sku = sl.sku AND rp.store_id = sl.store_id
                WHERE sl.store_id=%s AND COALESCE(sl.quantity_available, sl.quantity, 0) <= 15
                ORDER BY COALESCE(sl.quantity_available, sl.quantity, 0) ASC LIMIT 6
            """, (store_id,))
            for row in cur.fetchall():
                stock = int(row["qty"] or 0)
                vel   = float(row["vel"] or 0.5)
                jours = round(stock / vel, 0) if vel > 0 and stock > 0 else 0
                r["alerts"].append({
                    "nom": row["nom"], "qty": stock, "level": row["level"],
                    "jours": jours, "eoq": int(row["eoq"] or 0),
                })

            # Top vendeurs avec vélocité supply chain
            cur.execute("""
                SELECT COALESCE(p.nom, t.sku::text) AS nom,
                       SUM(t.quantity) AS sold,
                       COALESCE(sl.quantity_available, sl.quantity, 0) AS stock,
                       COALESCE(rp.demande_moy_jour, SUM(t.quantity)::NUMERIC/7) AS vel
                FROM   sales.transactions t
                LEFT JOIN sales.produits p ON p.sku=t.sku
                LEFT JOIN inventory.stock_levels sl ON sl.sku=t.sku AND sl.store_id=t.store_id
                LEFT JOIN supply.reorder_params rp ON rp.sku=t.sku AND rp.store_id=t.store_id
                WHERE  t.store_id=%s
                  AND  t.date_only>=(
                         SELECT MAX(date_only) FROM sales.transactions WHERE store_id=%s
                       ) - INTERVAL '7 days'
                GROUP BY p.nom, t.sku, sl.quantity_available, sl.quantity, rp.demande_moy_jour
                ORDER BY sold DESC LIMIT 5
            """, (store_id, store_id))
            for row in cur.fetchall():
                vel   = float(row["vel"] or 0)
                stock = int(row["stock"] or 0)
                jours = round(stock / vel, 0) if vel > 0 and stock > 0 else 999
                r["top_sellers"].append({
                    "nom": row["nom"], "sold": int(row["sold"] or 0),
                    "stock": stock, "jours": jours,
                })

            # Recommandations agent inventaire
            cur.execute("""
                SELECT COALESCE(p.nom, rec.sku::text) AS nom,
                       rec.recommendation_type AS type, rec.suggested_quantity AS qty
                FROM inventory.recommendations rec
                LEFT JOIN sales.produits p ON p.sku = rec.sku
                WHERE rec.store_id=%s AND rec.status IN ('pending','approved')
                  AND rec.created_at>=CURRENT_TIMESTAMP-INTERVAL '24 hours'
                ORDER BY rec.created_at DESC LIMIT 3
            """, (store_id,))
            r["agent_recos"] = [
                {"nom": row["nom"], "type": row["type"], "qty": int(row["qty"] or 0)}
                for row in cur.fetchall()
            ]

            # KPI terminaux et forfaits du jour
            cur.execute("""
                SELECT
                    COALESCE(SUM(CASE WHEN p.flag_terminal=TRUE THEN t.quantity ELSE 0 END), 0) AS kpi_terminaux,
                    COALESCE(SUM(CASE WHEN p.flag_forfait=TRUE THEN t.quantity ELSE 0 END), 0)  AS kpi_forfaits
                FROM sales.transactions t JOIN sales.produits p ON p.sku=t.sku
                WHERE t.store_id=%s AND t.date_only=(SELECT MAX(date_only) FROM sales.transactions WHERE store_id=%s)
            """, (store_id, store_id))
            row = cur.fetchone()
            if row:
                r["kpi_terminaux"] = int(row["kpi_terminaux"] or 0)
                r["kpi_forfaits"]  = int(row["kpi_forfaits"]  or 0)

            # CA moyen agent depuis advisor_profile
            cur.execute("""
                SELECT COALESCE(avg_daily_ca, 0) AS ca_moy
                FROM monitoring.advisor_profile WHERE store_id=%s LIMIT 1
            """, (store_id,))
            row = cur.fetchone()
            if row:
                r["ca_moy_agent"] = float(row["ca_moy"] or 0)

        finally:
            cur.close(); conn.close()
    except Exception as e:
        logger.warning("[COACH INV] %.80s", str(e))
    return r


def _load_day_history_sync(advisor: str, store_id: str, limit: int = 4) -> list:
    import psycopg2
    try:
        conn = psycopg2.connect(**DB_CFG, connect_timeout=5)
        cur  = conn.cursor()
        try:
            cur.execute("""
                SELECT message, response FROM public.coach_interactions
                WHERE store_id=%s AND advisor_name=%s AND created_at>=CURRENT_DATE
                ORDER BY created_at ASC LIMIT %s
            """, (store_id, advisor, limit))
            history = []
            for msg, resp in cur.fetchall():
                if msg:  history.append({"role": "user",      "content": msg[:600]})
                if resp: history.append({"role": "assistant", "content": resp[:700]})
            return history
        finally:
            cur.close(); conn.close()
    except Exception as e:
        logger.debug("[COACH HIST] %.60s", str(e))
    return []

# ── Advisor profile (S3.3) ────────────────────────────────────────────────────

_advisor_profile_cache: dict[str, tuple[dict, float]] = {}
_ADVISOR_PROFILE_TTL = 300.0  # 5 min

def _load_advisor_profile_sync(advisor_name: str, store_id: str) -> dict:
    """Load per-advisor KPIs: top products, 7-day sales count, seniority."""
    key = f"{store_id}:{advisor_name.lower()}"
    if key in _advisor_profile_cache:
        data, ts = _advisor_profile_cache[key]
        if time.time() - ts < _ADVISOR_PROFILE_TTL:
            return data

    profile: dict = {
        "top_products_7d": [],
        "nb_ventes_7d":    0,
        "avg_ticket_7d":   0.0,
        "seniority_days":  None,
        "best_category":   "",
    }
    import psycopg2, psycopg2.extras
    try:
        conn = psycopg2.connect(**DB_CFG, connect_timeout=5)
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            # Top products sold by this advisor last 7 days (via historical transactions)
            cur.execute("""
                SELECT
                    COALESCE(p.nom, t.sku::text) AS nom,
                    SUM(t.quantity)              AS qty,
                    SUM(t.lig_ttc)               AS ca
                FROM sales.transactions t
                LEFT JOIN sales.produits p ON p.sku = t.sku
                LEFT JOIN sales.agents a ON a.agent_id = t.agent_id
                WHERE t.store_id = %s
                  AND a.agent_name ILIKE %s
                  AND t.date_only >= CURRENT_DATE - INTERVAL '7 days'
                GROUP BY p.nom, t.sku
                ORDER BY qty DESC
                LIMIT 4
            """, (store_id, f"%{advisor_name.split()[0]}%"))
            rows = cur.fetchall()
            profile["top_products_7d"] = [
                {"nom": r["nom"], "qty": int(r["qty"] or 0), "ca": float(r["ca"] or 0)}
                for r in rows
            ]
            profile["nb_ventes_7d"]  = sum(r["qty"] for r in profile["top_products_7d"])
            profile["avg_ticket_7d"] = round(
                sum(r["ca"] for r in profile["top_products_7d"])
                / max(profile["nb_ventes_7d"], 1), 1
            )
            if profile["top_products_7d"]:
                # Derive best category heuristically from product name
                names = " ".join(r["nom"] for r in profile["top_products_7d"]).lower()
                if any(k in names for k in ["iphone","samsung","galaxy","infinix","phone"]):
                    profile["best_category"] = "Terminaux"
                elif any(k in names for k in ["5g","flexi","unlimited","forfait"]):
                    profile["best_category"] = "Forfaits"
                elif any(k in names for k in ["airpods","watch","coque","chargeur"]):
                    profile["best_category"] = "Accessoires"

            # Seniority from advisor_profile table if available
            cur.execute("""
                SELECT
                    COALESCE(days_since_onboarding,
                        EXTRACT(DAY FROM NOW() - hire_date)::int, NULL) AS seniority
                FROM monitoring.advisor_profile
                WHERE store_id = %s AND advisor_name ILIKE %s
                LIMIT 1
            """, (store_id, f"%{advisor_name.split()[0]}%"))
            row = cur.fetchone()
            if row and row["seniority"] is not None:
                profile["seniority_days"] = int(row["seniority"])

        finally:
            cur.close(); conn.close()
    except Exception as e:
        logger.debug("[COACH PROFILE] %.60s", str(e))

    _advisor_profile_cache[key] = (profile, time.time())
    return profile


# ══════════════════════════════════════════════════════════════════════════════
# 3. RAG (Milvus + Ollama embeddings — coaching seulement)
# ══════════════════════════════════════════════════════════════════════════════

def _search_rag_sync(query: str, hour: int, top_k: int = 2, min_score: float = 0.32) -> tuple[list, bool]:
    try:
        import requests as _req
        resp = _req.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": "nomic-embed-text", "prompt": query[:400]},
            timeout=8,
        )
        emb = resp.json().get("embedding", [])
        if not emb:
            return [], False
        if len(emb) < EMBED_DIM: emb += [0.0] * (EMBED_DIM - len(emb))
        emb = emb[:EMBED_DIM]

        from pymilvus import MilvusClient
        client  = MilvusClient(uri=MILVUS_URI)
        results = client.search(
            collection_name=COLLECTION, data=[emb], limit=top_k + 2,
            output_fields=["categorie","situation","action","produit","argument","impact","heure_min","heure_max"],
        )
        scripts = []
        for hit in results[0]:
            e     = hit["entity"]
            score = float(hit["distance"])
            if int(e.get("heure_min", 0)) <= hour <= int(e.get("heure_max", 24)):
                score += 0.10
            scripts.append({
                "score":     round(score, 3),
                "categorie": e.get("categorie", ""),
                "situation": e.get("situation", ""),
                "action":    e.get("action", "")[:100],
                "argument":  e.get("argument", "")[:100],
                "produit":   e.get("produit", ""),
                "impact":    e.get("impact", ""),
            })
        scripts = sorted(scripts, key=lambda x: x["score"], reverse=True)[:top_k]
        relevant = bool(scripts) and scripts[0]["score"] >= min_score
        return scripts, relevant
    except Exception as e:
        logger.debug("[COACH RAG] %.50s", str(e))
        return [], False

# ══════════════════════════════════════════════════════════════════════════════
# 4. SITUATION BLOCK (contexte sélectif par intent — ~100-220 tokens)
# ══════════════════════════════════════════════════════════════════════════════

def _build_situation(
    *,
    advisor_name: str,
    store_id: str,
    hour: int,
    ca: float,
    target: float,
    perf: float,
    gap: float,
    hours_left: int,
    urgency: str,
    weather: str,
    cause: str,
    mode: str,
    qtype: str,
    actions: list,
    rag_scripts: list,
    top_sellers: list,
    recent_tx: list,
    inv_ctx: dict,
    advisor_profile: dict | None = None,
) -> str:
    """Bloc de situation — adapté à l'intent, compact et actionnable."""

    lines = [
        f"SITUATION {advisor_name} | {store_id} | {hour}h | {weather} | Urgence : {urgency}",
        f"Performance : {ca:,.0f}/{target:,.0f} TND ({perf:.0f}%) | Gap : {gap:,.0f} TND | {hours_left}h restantes",
    ]

    # Advisor profile enrichment (S3.3)
    if advisor_profile:
        prof_parts = []
        if advisor_profile.get("seniority_days") is not None:
            seniority = advisor_profile["seniority_days"]
            level = "Senior" if seniority > 365 else ("Confirmé" if seniority > 90 else "Junior")
            prof_parts.append(f"Profil: {level} ({seniority}j)")
        if advisor_profile.get("best_category"):
            prof_parts.append(f"Spécialité: {advisor_profile['best_category']}")
        if advisor_profile.get("nb_ventes_7d"):
            prof_parts.append(f"Ventes 7j: {advisor_profile['nb_ventes_7d']}")
        if advisor_profile.get("avg_ticket_7d"):
            prof_parts.append(f"Panier moy: {advisor_profile['avg_ticket_7d']:.0f} TND")
        if prof_parts:
            lines.append(" | ".join(prof_parts))
        if advisor_profile.get("top_products_7d"):
            top = advisor_profile["top_products_7d"][:2]
            top_str = " · ".join(f"{p['nom']} ({p['qty']})" for p in top)
            lines.append(f"Top produits 7j: {top_str}")

    if cause:
        lines.append(f"Cause gap : {cause}")

    if mode == "coaching":
        if actions:
            a = actions[0]
            lines.append(
                f"Action Stratège : {a.get('action','')} → {a.get('produit_cible','')} "
                f"| Argument : {a.get('argument_vente','')[:80]}"
            )
        if rag_scripts:
            s = rag_scripts[0]
            lines.append(
                f"Script terrain [{s['categorie']}] : {s['action'][:90]} "
                f"| {s.get('argument','')[:80]}"
            )

    elif mode in ("inventory", "cross_domain"):
        alerts   = inv_ctx.get("alerts", [])
        top_inv  = inv_ctx.get("top_sellers", [])
        stats    = inv_ctx.get("stats", {})
        recos    = inv_ctx.get("agent_recos", [])
        kpi_t    = inv_ctx.get("kpi_terminaux", 0)
        kpi_f    = inv_ctx.get("kpi_forfaits", 0)

        lines.append(
            f"Stock : {stats.get('total',0)} SKUs | "
            f"Ruptures : {stats.get('ruptures',0)} | "
            f"Critiques : {stats.get('critiques',0)} | "
            f"OK : {stats.get('ok_count',0)}"
        )
        if kpi_t or kpi_f:
            lines.append(f"KPI jour : {kpi_t} terminaux vendus · {kpi_f} forfaits vendus")
        if alerts:
            lines.append("Alertes :")
            for a in alerts[:4]:
                icon = "X" if a["level"] == "rupture" else "!"
                lines.append(f"  {icon} {a['nom']} — {a['qty']} unité(s) | rupture J+{a['jours']:.0f}")
        if top_inv:
            lines.append("Top vendeurs (vélocité) :")
            for t in top_inv[:3]:
                lines.append(f"  {t['nom']} — {t['sold']}/7j | stock {t['stock']} | J+{t['jours']:.0f}")
        if recos:
            lines.append("Agent Décision : " + " · ".join(
                f"[{r['type']}] {r['nom']} x{r['qty']}" for r in recos[:2]
            ))
        if mode == "cross_domain" and actions:
            a = actions[0]
            lines.append(f"Stratège : {a.get('action','')} → {a.get('produit_cible','')}")

    elif qtype == "recap":
        if top_sellers:
            t3 = " · ".join(f"{t['nom']} ({t['qty']} vendus)" for t in top_sellers[:3])
            lines.append(f"Top produits (7j) : {t3}")
        if recent_tx:
            r0 = recent_tx[0]
            lines.append(f"Dernière vente : {r0['heure']}h — {r0['nom']} x{r0['qty']} — {r0['ttc']:.0f} TND")
        if actions:
            a = actions[0]
            lines.append(f"Conseil Stratège : {a.get('action','')} → {a.get('produit_cible','')}")
        ruptures = inv_ctx.get("stats", {}).get("ruptures", 0)
        if ruptures > 0:
            lines.append(f"Alerte : {ruptures} rupture(s) en cours")

    else:  # conversation générale — aucun mot-clé métier détecté dans le message
        # Filet de sécurité : même mal classée, la question peut porter sur des
        # données réelles déjà chargées (dernière vente, top produits...). On les
        # expose systématiquement pour éviter que le coach ignore la DB/les agents.
        if recent_tx:
            r0 = recent_tx[0]
            lines.append(f"Dernière vente : {r0['heure']}h — {r0['nom']} x{r0['qty']} — {r0['ttc']:.0f} TND")
        if top_sellers:
            t3 = " · ".join(f"{t['nom']} ({t['qty']} vendus)" for t in top_sellers[:3])
            lines.append(f"Top produits (7j) : {t3}")
        if actions:
            a = actions[0]
            lines.append(f"Action du jour : {a.get('action','')} → {a.get('produit_cible','')}")
        lines.append(
            "Note : si des données ci-dessus répondent à la question, utilise-les. "
            "Sinon, si la question est incomprehensible ou hors-sujet, demande une "
            "clarification en 1 phrase — n'invente aucun client ni situation."
        )

    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════════════
# 5. LLM CALL CHAIN (OpenRouter → Ollama → intent fallback)
# ══════════════════════════════════════════════════════════════════════════════

def _is_valid_reply(reply: str) -> bool:
    if not reply or len(reply.strip()) < 25:
        return False
    r = reply.strip()
    if r[:6] in ("I'm ", "I can", "I wil", "The c", "To he", "Based", "Here ", "Sure,"):
        return False
    if r.startswith(("{", "[", "```")):
        return False
    return True


async def _call_openrouter(
    system: str,
    user_msg: str,
    max_tokens: int,
    temperature: float,
    history: list,
) -> tuple[str, float]:
    t0 = time.time()
    if not OPENROUTER_KEY:
        return "", 0.0
    import httpx
    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history[-8:])
    messages.append({"role": "user", "content": user_msg})
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type":  "application/json; charset=utf-8",
        "HTTP-Referer":  "https://github.com/MALEKALADAB11/multi-agent-sales-inventory",
        "X-Title":       "AI Sales Coach Ooredoo v10",
    }
    for model in _OR_MODEL_ROTATION:
        try:
            body = json.dumps({
                "model": model, "max_tokens": max_tokens,
                "temperature": temperature, "messages": messages,
            }, ensure_ascii=False).encode("utf-8")
            async with httpx.AsyncClient(timeout=28.0) as client:
                resp = await client.post(OPENROUTER_URL, headers=headers, content=body)
            data = resp.json()
            if "error" in data:
                code = data["error"].get("code", 0)
                logger.warning("[COACH LLM] %s → %s: %.60s", model, code, data["error"].get("message",""))
                if code in (429, 503):
                    continue
                return "", (time.time() - t0) * 1000
            raw = data["choices"][0]["message"]["content"].strip()
            for pfx in ["Reponse:", "Coach:", "CoachIA:", "Réponse :", "RÉPONSE :",
                        "Here is", "Based on", "Voici ma", "D'accord,"]:
                if raw.lower().startswith(pfx.lower()):
                    raw = raw[len(pfx):].strip()
            if raw.startswith("{"):
                m = re.search(r'"(?:reply|response|conseil|text|message)"\s*:\s*"([^"]+)"', raw)
                raw = m.group(1).strip() if m else ""
            ms = (time.time() - t0) * 1000
            logger.info("[COACH LLM] %dc | %.0fms | %s", len(raw), ms, model)
            return raw, ms
        except Exception as e:
            logger.warning("[COACH LLM] %s exception: %.60s", model, str(e))
            continue
    return "", (time.time() - t0) * 1000


# ── Mistral (API directe La Plateforme — quota indépendant d'OpenRouter) ─────
MISTRAL_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
_MISTRAL_MODEL_ROTATION = [
    os.getenv("MISTRAL_MODEL_SMART", "mistral-large-latest"),
    os.getenv("MISTRAL_MODEL",       "mistral-small-latest"),
    os.getenv("MISTRAL_MODEL_FAST",  "open-mistral-nemo"),
]


async def _call_mistral(
    system: str,
    user_msg: str,
    max_tokens: int,
    temperature: float,
    history: list,
) -> tuple[str, float]:
    t0 = time.time()
    if not MISTRAL_KEY:
        return "", 0.0
    import httpx
    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history[-8:])
    messages.append({"role": "user", "content": user_msg})
    headers = {
        "Authorization": f"Bearer {MISTRAL_KEY}",
        "Content-Type":  "application/json; charset=utf-8",
    }
    for model in _MISTRAL_MODEL_ROTATION:
        try:
            body = json.dumps({
                "model": model, "max_tokens": max_tokens,
                "temperature": temperature, "messages": messages,
            }, ensure_ascii=False).encode("utf-8")
            async with httpx.AsyncClient(timeout=28.0) as client:
                resp = await client.post(MISTRAL_URL, headers=headers, content=body)
            if resp.status_code == 429:
                logger.warning("[COACH MISTRAL] %s → 429 rate limit", model)
                continue
            if resp.status_code >= 500:
                logger.warning("[COACH MISTRAL] %s → %s server error", model, resp.status_code)
                continue
            data = resp.json()
            if resp.status_code >= 400:
                err = data.get("error")
                msg = err.get("message") if isinstance(err, dict) else data.get("message", "?")
                logger.warning("[COACH MISTRAL] %s → %s: %.60s", model, resp.status_code, str(msg))
                return "", (time.time() - t0) * 1000
            raw = data["choices"][0]["message"]["content"].strip()
            for pfx in ["Reponse:", "Coach:", "CoachIA:", "Réponse :", "RÉPONSE :",
                        "Here is", "Based on", "Voici ma", "D'accord,"]:
                if raw.lower().startswith(pfx.lower()):
                    raw = raw[len(pfx):].strip()
            if raw.startswith("{"):
                m = re.search(r'"(?:reply|response|conseil|text|message)"\s*:\s*"([^"]+)"', raw)
                raw = m.group(1).strip() if m else ""
            ms = (time.time() - t0) * 1000
            logger.info("[COACH MISTRAL] %dc | %.0fms | %s", len(raw), ms, model)
            return raw, ms
        except Exception as e:
            logger.warning("[COACH MISTRAL] %s exception: %.60s", model, str(e))
            continue
    return "", (time.time() - t0) * 1000


async def _call_ollama_fallback(system: str, user_msg: str, max_tokens: int) -> str:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            tags = await client.get(f"{OLLAMA_URL}/api/tags")
        models = [m["name"] for m in tags.json().get("models", [])]
        chat_model = next(
            (m for m in models if any(p in m for p in
             ["llama3","llama2","mistral","mixtral","phi","gemma","qwen"])),
            None,
        )
        if not chat_model:
            return ""
        async with httpx.AsyncClient(timeout=22.0) as client:
            resp = await client.post(f"{OLLAMA_URL}/api/generate", json={
                "model":   chat_model,
                "prompt":  f"{system}\n\n{user_msg}",
                "stream":  False,
                "options": {"num_predict": max_tokens, "temperature": 0.3},
            })
        return resp.json().get("response", "").strip()
    except Exception as e:
        logger.debug("[COACH OLLAMA] %.50s", str(e))
        return ""

# ══════════════════════════════════════════════════════════════════════════════
# 6. INTENT FALLBACK (last resort — cohérent par intent)
# ══════════════════════════════════════════════════════════════════════════════

def _build_intent_fallback(
    *,
    mode: str,
    qtype: str,
    advisor_name: str,
    ca: float,
    target: float,
    perf: float,
    gap: float,
    hours_left: int,
    urgency: str,
    actions: list,
    top_sellers: list,
    inv_ctx: dict,
) -> str:
    name = advisor_name.split()[0] if advisor_name else "toi"
    alerts = inv_ctx.get("alerts", [])

    if mode == "greeting":
        if perf >= 95:
            return (f"Salut {name} ! Objectif presque atteint — excellent départ. "
                    "Sur quoi je peux t'aider pour finir en beauté ?")
        if perf >= 70:
            return (f"Salut {name} ! {perf:.0f}% de l'objectif avec {hours_left}h devant toi — "
                    "bien parti. On attaque le gap ?")
        return (f"Salut {name} ! {perf:.0f}% de l'objectif, {hours_left}h, c'est large. "
                "Qu'est-ce qu'on attaque ?")

    if mode == "conversation" and qtype == "general":
        return ("Je ne suis pas sûr de te suivre — tu veux un script de vente, "
                "un point sur le stock, ou un coup de main pour ton objectif du jour ? "
                "Dis-m'en un peu plus !")

    if qtype == "recap":
        top = f"Top : {top_sellers[0]['nom']} ({top_sellers[0]['qty']} vendus)." if top_sellers else ""
        a_txt = f"Action : {actions[0].get('action','')}." if actions else ""
        return (f"CA {ca:,.0f} / {target:,.0f} TND ({perf:.0f}%). {top} "
                f"Gap : {gap:,.0f} TND avec {hours_left}h. {a_txt} Allez !")

    if mode == "inventory":
        if alerts:
            names = ", ".join(a["nom"] for a in alerts[:2])
            return (f"{names} en rupture/critique. "
                    "Contacte le manager pour réappro urgente. "
                    "Pousse les alternatives en attendant. Maintenant !")
        stats = inv_ctx.get("stats", {})
        return (f"Stock : {stats.get('total',0)} SKUs — "
                f"{stats.get('ruptures',0)} rupture(s), {stats.get('critiques',0)} critique(s). "
                f"Gap {gap:,.0f} TND avec {hours_left}h. À toi !")

    if qtype == "objection":
        return ("Réponds : «Sur 24 mois via avance postpayé Ooredoo, "
                "c'est 54 TND/mois — moins qu'un café par jour. "
                "Et valeur revente 400+ TND dans 2 ans.» "
                "Close : «Noir titane ou blanc naturel ?» À toi !")

    if qtype == "closing":
        return ("Choix forcé : «Lequel vous correspond le mieux, noir ou blanc ?» "
                "Si résistance : «Il nous reste 2 unités — l'offre expire ce soir.» "
                "Silence 3 secondes. À toi !")

    if qtype in ("script", "upsell", "forfait", "objectif", "meteo"):
        if actions:
            a = actions[0]
            arg_vente = a.get('argument_vente') or "Financement 0 TND aujourd'hui"
            return (f"Action prioritaire : {a.get('action','Bundle terminal + forfait')} "
                    f"→ {a.get('produit_cible','iPhone 16 Pro + Forfait 5G Max')}. "
                    f"Argument : {arg_vente}. Vas-y !")
        return ("Script : 1. Accroche besoins 2. Démo live 3. Prix + financement "
                "4. Assurance 9 TND/mois 5. Close coloris. Vas-y !")

    if actions:
        a = actions[0]
        return (f"{perf:.0f}% de l'objectif. {a.get('action','')} → "
                f"{a.get('produit_cible','')}. "
                f"{gap:,.0f} TND de gap, {hours_left}h devant toi. Allez !")
    return (f"{perf:.0f}% de l'objectif. Assurance Premium 9 TND/mois sur chaque terminal — "
            f"marge 80%, {gap:,.0f} TND de gap. À toi !")

# ══════════════════════════════════════════════════════════════════════════════
# 7. ENDPOINT PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/chat")
@_rate_limit("30/minute")
async def coach_chat(request: Request, body: dict):
    t0 = time.time()

    message      = (body.get("message") or "").strip()
    advisor_name = (body.get("advisor_name") or "Conseiller").strip()
    store_id     = _normalize_store(body.get("store_id") or DEFAULT_STORE_ID)
    ctx          = body.get("context") or {}

    if not message:
        return JSONResponse({"reply": "", "mode": "empty"})

    # ── RBAC store-level (S6.3) ──────────────────────────────────────────────
    try:
        from app.api.auth import validate_store_access as _vsa
        _bearer = request.headers.get("Authorization", "")
        _token  = _bearer[7:] if _bearer.startswith("Bearer ") else None
        await _vsa(_token, store_id)
    except Exception as _rbac_err:
        if getattr(_rbac_err, "status_code", 0) in (401, 403):
            raise

    # ── Dedup cache ──────────────────────────────────────────────────────────
    cached = _cache_get(advisor_name, store_id, message)
    if cached:
        logger.info("[COACH] DEDUP hit '%s'", message[:40])
        return JSONResponse({
            "reply": cached, "mode": "cached", "domain": "sales",
            "question_type": "dedup", "source": "cache", "model": "cache",
            "confidence": 1.0, "rag_used": False, "nb_rag_scripts": 0,
            "latency_ms": round((time.time()-t0)*1000),
            "sources": [], "rag_scripts": [], "inventory_alerts": [], "agent_recos": [],
            "context_used": {"advisor": advisor_name, "store_id": store_id},
        })

    # ── Intent ───────────────────────────────────────────────────────────────
    intent = _classify_intent(message)
    mode   = intent["mode"]
    domain = intent["domain"]
    qtype  = intent["type"]
    logger.info("[COACH] mode=%s type=%s conf=%.2f msg='%s'",
                mode, qtype, intent["confidence"], message[:50])

    # ── Off-topic guard ──────────────────────────────────────────────────────
    if mode == "off_topic":
        reply = ("Je suis ton coach Ooredoo — spécialisé en vente telecom, "
                 "stock et objectifs commerciaux. Sur quoi je peux t'aider ?")
        _cache_set(advisor_name, store_id, message, reply)
        return JSONResponse({
            "reply": reply, "mode": "off_topic", "domain": "none",
            "question_type": "off_topic", "source": "guardrail", "model": "none",
            "confidence": 1.0, "rag_used": False, "nb_rag_scripts": 0,
            "latency_ms": round((time.time()-t0)*1000),
            "sources": [], "rag_scripts": [], "inventory_alerts": [], "agent_recos": [],
            "context_used": {"advisor": advisor_name},
        })

    # ── Données contexte frontend ────────────────────────────────────────────
    ca_ctx      = float(ctx.get("current_revenue") or ctx.get("ca_today") or 0)
    target_ctx  = float(ctx.get("daily_target") or ctx.get("ca_target") or 1007)
    urgency     = str(ctx.get("urgency") or ctx.get("urgency_level") or "MEDIUM").upper()
    weather     = str(ctx.get("weather") or "Tunis")
    actions_ctx = ctx.get("strategie_actions") or []
    cause_ctx   = str(ctx.get("cause_racine") or "")

    now        = datetime.now()
    hour       = now.hour
    hours_left = max(1, 20 - hour)

    # ── Greeting fast path (< 5ms, sans LLM) ────────────────────────────────
    if mode == "greeting":
        perf  = round((ca_ctx / target_ctx * 100), 1) if ca_ctx and target_ctx else 0.0
        first = advisor_name.split()[0]
        if perf >= 95:
            reply = (f"Salut {first} ! Objectif presque atteint — excellent départ. "
                     "Sur quoi je peux t'aider pour finir en beauté ?")
        elif perf > 0:
            reply = (f"Salut {first} ! {perf:.0f}% de l'objectif avec {hours_left}h devant toi. "
                     "On attaque le gap ? Comment je peux t'aider ?")
        else:
            reply = (f"Salut {first} ! Je suis ton coach Ooredoo — "
                     "demande-moi un script, une stratégie ou l'état du stock.")
        _cache_set(advisor_name, store_id, message, reply)
        return JSONResponse({
            "reply": reply, "mode": "greeting", "domain": "sales",
            "question_type": "greeting", "source": "fast_path", "model": "none",
            "confidence": 0.99, "rag_used": False, "nb_rag_scripts": 0,
            "latency_ms": round((time.time()-t0)*1000),
            "sources": [{"id": "fast", "label": "Fast path", "active": True}],
            "rag_scripts": [], "inventory_alerts": [], "agent_recos": [],
            "context_used": {"advisor": advisor_name, "store_id": store_id,
                             "hour": hour, "performance": perf},
        })

    # ── Chargement contexte en parallèle ────────────────────────────────────
    need_inv = mode in ("inventory", "cross_domain") or qtype == "recap"
    loop = asyncio.get_event_loop()

    if need_inv:
        pos_data, sales_detail, inv_ctx, day_history, catalog, adv_profile = await asyncio.gather(
            loop.run_in_executor(None, _load_pos_sync, store_id),
            loop.run_in_executor(None, _load_sales_detail_sync, store_id),
            loop.run_in_executor(None, _load_inventory_context_sync, store_id),
            loop.run_in_executor(None, _load_day_history_sync, advisor_name, store_id),
            loop.run_in_executor(None, _load_catalog, store_id),
            loop.run_in_executor(None, _load_advisor_profile_sync, advisor_name, store_id),
        )
    else:
        pos_data, sales_detail, day_history, catalog, adv_profile = await asyncio.gather(
            loop.run_in_executor(None, _load_pos_sync, store_id),
            loop.run_in_executor(None, _load_sales_detail_sync, store_id),
            loop.run_in_executor(None, _load_day_history_sync, advisor_name, store_id),
            loop.run_in_executor(None, _load_catalog, store_id),
            loop.run_in_executor(None, _load_advisor_profile_sync, advisor_name, store_id),
        )
        inv_ctx = {"stats": {}, "alerts": [], "top_sellers": [], "agent_recos": [],
                   "kpi_terminaux": 0, "kpi_forfaits": 0, "ca_moy_agent": 0}

    ca     = ca_ctx or pos_data.get("ca", 0.0)
    target = target_ctx
    perf   = round((ca / target * 100), 1) if target > 0 else 0.0
    gap    = max(0.0, target - ca)
    nb_tx  = pos_data.get("nb_tx", 0) or len(sales_detail.get("recent_tx", []))

    top_sellers = sales_detail.get("top_sellers", [])
    recent_tx   = sales_detail.get("recent_tx", [])

    # ── RAG (coaching seulement) ─────────────────────────────────────────────
    rag_scripts, rag_relevant, rag_query, rag_ms = [], False, "", 0.0
    if mode == "coaching" or qtype in ("script","objection","closing","upsell","forfait","objectif"):
        rag_query = f"{message} {qtype} vente telecom Ooredoo"
        if gap > target * 0.4:
            rag_query += " gap critique urgent"
        _rag_t0 = time.time()
        rag_scripts, rag_relevant = await loop.run_in_executor(
            None, _search_rag_sync, rag_query, hour, 2
        )
        rag_ms = (time.time() - _rag_t0) * 1000

    # ── Prompt (système lean + situation sélective) ─────────────────────────
    system_prompt = _build_system_prompt(catalog)

    situation_block = _build_situation(
        advisor_name=advisor_name, store_id=store_id, hour=hour,
        ca=ca, target=target, perf=perf, gap=gap, hours_left=hours_left,
        urgency=urgency, weather=weather, cause=cause_ctx,
        mode=mode, qtype=qtype,
        actions=actions_ctx, rag_scripts=rag_scripts,
        top_sellers=top_sellers, recent_tx=recent_tx,
        inv_ctx=inv_ctx, advisor_profile=adv_profile,
    )
    user_message = f"{situation_block}\n\nQUESTION DU CONSEILLER : {message}"

    # Paramètres LLM selon intent
    if qtype in ("script", "objectif"):
        max_tokens, temp = 450, 0.17
    elif mode in ("inventory",):
        max_tokens, temp = 380, 0.20
    elif urgency in ("HIGH", "CRITICAL"):
        max_tokens, temp = 280, 0.17
    else:
        max_tokens, temp = 350, 0.22

    # ── Tentative 1 : Mistral direct (quota indépendant d'OpenRouter) ───────
    reply, llm_ms = await _call_mistral(system_prompt, user_message, max_tokens, temp, day_history)
    model_used = "mistral" if _is_valid_reply(reply) else ""

    # ── Tentative 2 : OpenRouter full ───────────────────────────────────────
    if not _is_valid_reply(reply):
        if MISTRAL_KEY:
            logger.warning("[COACH] Mistral failed (%.0fms) — retry OpenRouter", llm_ms)
        reply, llm_ms = await _call_openrouter(system_prompt, user_message, max_tokens, temp, day_history)
        model_used = OPENROUTER_MODEL if _is_valid_reply(reply) else ""

    # ── Tentative 3 : OpenRouter stripped ───────────────────────────────────
    if not _is_valid_reply(reply):
        logger.warning("[COACH] Attempt 2 failed (%.0fms) — retry stripped", llm_ms)
        await asyncio.sleep(0.6 + random.uniform(0, 0.8))

        min_user = (
            f"SITUATION {advisor_name} | {store_id} | {hour}h | {urgency}\n"
            f"CA : {ca:,.0f}/{target:,.0f} TND ({perf:.0f}%) | Gap : {gap:,.0f} TND | {hours_left}h\n"
            f"QUESTION : {message}"
        )
        reply, _ = await _call_openrouter(system_prompt, min_user, min(max_tokens, 260), temp, [])
        if _is_valid_reply(reply):
            model_used = f"{OPENROUTER_MODEL}+stripped"
        else:
            # ── Tentative 4 : Ollama local ───────────────────────────────────
            logger.warning("[COACH] Attempt 3 failed — trying Ollama")
            reply = await _call_ollama_fallback(system_prompt, min_user, min(max_tokens, 220))
            model_used = "ollama" if _is_valid_reply(reply) else ""

    # ── Fallback intent (last resort) ────────────────────────────────────────
    if not _is_valid_reply(reply):
        reply = _build_intent_fallback(
            mode=mode, qtype=qtype, advisor_name=advisor_name,
            ca=ca, target=target, perf=perf, gap=gap,
            hours_left=hours_left, urgency=urgency,
            actions=actions_ctx, top_sellers=top_sellers, inv_ctx=inv_ctx,
        )
        model_used = "intent_fallback"

    # ── Cache + confidence ───────────────────────────────────────────────────
    _cache_set(advisor_name, store_id, message, reply)

    n_active = sum([
        ca > 0,
        bool(top_sellers),
        bool(actions_ctx),
        inv_ctx.get("stats", {}).get("total", 0) > 0,
        rag_relevant,
        bool(day_history),
    ])
    confidence = min(0.95, 0.70 + n_active * 0.04)
    if model_used == "intent_fallback": confidence = min(confidence, 0.55)
    elif model_used == "ollama":        confidence = min(confidence, 0.68)

    total_ms = (time.time() - t0) * 1000

    # ── Guardrail validation (S6.1) ─────────────────────────────────────────
    _grd: dict = {
        "status": "APPROVE", "issues": [], "feedback": "",
        "requires_human_validation": False, "safe_fallback": "",
    }
    try:
        from app.sales.coaching.agents.guardrail.guardrail_agent import evaluate_guardrails
        _guard_snap = {
            "skus": {
                a["nom"]: {
                    "product_name": a["nom"],
                    "stock_qty":    max(0, int(a.get("qty", 1))),
                    "days_remaining": float(a.get("jours", 999)),
                }
                for a in inv_ctx.get("alerts", [])
            }
        }
        _focus = inv_ctx.get("focus_produits") or []
        _grd = evaluate_guardrails(
            recommendation={
                "product_to_push":     _focus[0] if _focus else None,
                "message_for_advisor": reply,
                "strategy_actions":    actions_ctx,
            },
            store_id           = store_id,
            inventory_snapshot = _guard_snap,
            rag_used           = rag_relevant,
            nb_scripts         = len(rag_scripts),
            confidence         = confidence,
        )
        if _grd["status"] == "BLOCK":
            reply = _grd["safe_fallback"] or (
                "Je vous recommande de consulter le catalogue disponible "
                "et de contacter le manager pour valider l'action commerciale."
            )
            confidence = 0.0
            model_used = "guardrail_block"
        if _grd["status"] in ("BLOCK", "ESCALATE"):
            async def _broadcast_guardrail():
                try:
                    import json as _json
                    from main import _broadcast
                    await _broadcast(store_id, _json.dumps({
                        "type":           "guardrail_event",
                        "status":         _grd["status"],
                        "store_id":       store_id,
                        "advisor":        advisor_name,
                        "issues":         _grd.get("issues", []),
                        "urgency":        urgency,
                        "timestamp":      __import__("datetime").datetime.utcnow().isoformat(),
                    }))
                except Exception:
                    pass
            asyncio.create_task(_broadcast_guardrail())

        if _grd.get("requires_human_validation") and urgency in ("HIGH", "CRITICAL"):
            async def _fire_hitl():
                try:
                    from app.api.hitl import submit_hitl_review
                    await submit_hitl_review(
                        store_id          = store_id,
                        cycle_id          = f"chat-{int(time.time())}",
                        urgency_level     = urgency,
                        gap_pct           = round(gap / target * 100, 1) if target else 0.0,
                        critique_score    = confidence,
                        critique_feedback = _grd.get("feedback", ""),
                        strategie_summary = reply[:500],
                        actions           = actions_ctx[:2],
                        source            = "guardrail_chat",
                    )
                except Exception:
                    pass
            asyncio.create_task(_fire_hitl())
    except Exception as _ge:
        logger.debug("[COACH] Guardrail skipped: %s", _ge)

    # ── Scored products (S6.2) — léger, sans appel DB ───────────────────────
    _scored_products: list = []
    try:
        _sales_ctx = {
            "gap_amount":       gap,
            "gap_pct":          round(gap / target * 100, 1) if target else 0.0,
            "remaining_hours":  hours_left,
        }
        _advisor_hist = {
            "strong_categories": [],
            "weak_categories":   [],
            "advisor_tier":      "standard",
        }
        # Convert already-loaded agent_recos to rank_products input format
        _candidate_products = []
        for r in inv_ctx.get("agent_recos", [])[:6]:
            _candidate_products.append({
                "sku":              r.get("nom", ""),
                "name":             r.get("nom", ""),
                "price":            float(r.get("prix", 0)),
                "stock_qty":        int(r.get("qty", 10)),
                "margin_pct":       float(r.get("marge", 20)),
                "is_top_seller":    r.get("type") == "terminal",
                "risk_level":       "ok",
                "days_to_stockout": 30.0,
            })
        if _candidate_products:
            from app.sales.coaching.agents.coach.cross_domain_tools import rank_products
            _scored_products = rank_products(
                products        = _candidate_products,
                sales_context   = _sales_ctx,
                advisor_history = _advisor_hist,
                top_n           = 3,
            )
    except Exception as _se:
        logger.debug("[COACH] Scored products skipped: %s", _se)

    # ── Traces async (non-bloquant) ──────────────────────────────────────────
    async def _persist():
        try:
            from app.core.langfuse import trace_coach_chat
            trace_coach_chat(
                store_id=store_id, advisor_name=advisor_name,
                message=message, response=reply,
                question_type=f"{domain}/{mode}/{qtype}",
                rag_used=rag_relevant, nb_rag_scripts=len(rag_scripts),
                confidence=confidence, latency_ms=total_ms,
                gap_pct=round(gap/target*100, 1) if target else 0,
                urgency=urgency,
            )
        except Exception:
            pass
        try:
            from app.sales.coaching.agents.coach.tools import save_interaction
            save_interaction(
                advisor_name=advisor_name, store_id=store_id,
                message=message, response=reply,
                gap_pct=round(gap/target*100, 1) if target else 0,
                urgency=urgency, rag_used=rag_relevant,
                nb_rag_scripts=len(rag_scripts),
                conseil_type=f"{domain}/{qtype}", confidence=confidence,
            )
        except Exception:
            pass
        # ── Télémétrie /api/monitoring/agents (agent_logs) — coach/rag/guardrail
        # réels, visibles dynamiquement sur la page Monitoring (pas de mock) ──
        try:
            from app.core.agent_logger import log_node_start, log_node_complete
            _cycle_id = f"chat-{advisor_name[:20]}-{int(t0*1000)}"

            _lid = log_node_start(
                cycle_id=_cycle_id, agent_name="coach", node_name="chat_reply",
                input_state={"message": message[:200], "advisor": advisor_name,
                             "mode": mode, "qtype": qtype, "urgency": urgency},
                store_id=store_id,
            )
            log_node_complete(
                _lid,
                output_state={"reply_preview": reply[:200], "model": model_used,
                              "confidence": confidence, "rag_used": rag_relevant,
                              "nb_rag_scripts": len(rag_scripts)},
                duration_ms=total_ms,
                metadata={"guardrail_status": _grd.get("status", "APPROVE")},
                status="error" if model_used in ("", "guardrail_block") else "completed",
            )

            if rag_query:
                _rid = log_node_start(
                    cycle_id=_cycle_id, agent_name="rag", node_name="search",
                    input_state={"query": rag_query[:150], "hour": hour},
                    store_id=store_id,
                )
                log_node_complete(
                    _rid,
                    output_state={"nb_scripts": len(rag_scripts), "relevant": rag_relevant,
                                  "top_score": rag_scripts[0]["score"] if rag_scripts else 0},
                    duration_ms=rag_ms,
                    status="completed" if rag_relevant else "fallback",
                )

            _grd_status = _grd.get("status", "APPROVE")
            _gid = log_node_start(
                cycle_id=_cycle_id, agent_name="guardrail", node_name="evaluate_guardrails",
                input_state={"advisor": advisor_name, "confidence": confidence,
                             "rag_used": rag_relevant, "urgency": urgency},
                store_id=store_id,
            )
            log_node_complete(
                _gid,
                output_state={"status": _grd_status, "issues": _grd.get("issues", [])[:3],
                              "requires_hitl": _grd.get("requires_human_validation", False)},
                duration_ms=0.0,
                status={"APPROVE": "completed", "REWRITE": "completed",
                        "ESCALATE": "fallback", "BLOCK": "error"}.get(_grd_status, "completed"),
            )
        except Exception as _tel_err:
            logger.debug("[COACH] Monitoring telemetry skipped: %s", _tel_err)

    asyncio.create_task(_persist())

    logger.info(
        "[COACH] %s/%s | %s | model=%s | RAG=%s(%d) | conf=%.2f | llm=%.0fms | total=%.0fms",
        mode, qtype, advisor_name, model_used,
        "Y" if rag_relevant else "N", len(rag_scripts),
        confidence, llm_ms, total_ms,
    )

    return JSONResponse({
        "reply":           reply,
        "mode":            mode,
        "domain":          domain,
        "question_type":   qtype,
        "source":          f"v10_{model_used}",
        "model":           model_used,
        "confidence":      round(confidence, 3),
        "rag_used":        rag_relevant,
        "nb_rag_scripts":  len(rag_scripts),
        "latency_ms":      round(total_ms),
        "sources": [
            {"id": "pos",      "label": "POS",          "active": ca > 0},
            {"id": "analyste", "label": "Top vendeurs", "active": bool(top_sellers)},
            {"id": "stratege", "label": "Stratège",     "active": bool(actions_ctx)},
            {"id": "stock",    "label": "Agent Stock",  "active": inv_ctx.get("stats",{}).get("total",0) > 0},
            {"id": "rag",      "label": "RAG",          "active": rag_relevant},
            {"id": "history",  "label": "Historique",   "active": bool(day_history)},
        ],
        "context_used": {
            "advisor":       advisor_name,
            "store_id":      store_id,
            "hour":          hour,
            "domain":        domain,
            "mode":          mode,
            "ca_today":      ca,
            "target":        target,
            "performance":   perf,
            "gap_tnd":       gap,
            "nb_tx":         nb_tx,
            "urgency":       urgency,
            "kpi_terminaux": inv_ctx.get("kpi_terminaux", 0),
            "kpi_forfaits":  inv_ctx.get("kpi_forfaits", 0),
            "ca_moy_agent":  inv_ctx.get("ca_moy_agent", 0),
            "ruptures":      inv_ctx.get("stats", {}).get("ruptures", 0),
        },
        "rag_scripts": [
            {"categorie": s["categorie"], "action": s["action"],
             "produit": s.get("produit",""), "score": s["score"]}
            for s in rag_scripts[:2]
        ],
        "inventory_alerts": [
            {"product": a["nom"], "qty": a["qty"], "level": a["level"],
             "jours": a["jours"], "eoq": a.get("eoq", 0)}
            for a in inv_ctx.get("alerts", [])[:3]
        ],
        "agent_recos":      inv_ctx.get("agent_recos", [])[:3],
        "scored_products":  _scored_products,
        "guardrail_status": _grd.get("status", "APPROVE"),
        "guardrail_issues": _grd.get("issues", []),
        "requires_hitl":    _grd.get("requires_human_validation", False),
    })


# ══════════════════════════════════════════════════════════════════════════════
# 8. ENDPOINT SSE STREAMING
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/stream")
@_rate_limit("10/minute")
async def coach_chat_stream(request: Request, body: dict):
    """SSE streaming variant — same logic as /chat but yields tokens progressively."""
    from fastapi.responses import StreamingResponse as _SR

    t0 = time.time()
    message      = (body.get("message") or "").strip()
    advisor_name = (body.get("advisor_name") or "Conseiller").strip()
    store_id     = _normalize_store(body.get("store_id") or DEFAULT_STORE_ID)
    ctx          = body.get("context") or {}

    async def _words(text: str, delay: float = 0.018):
        words = text.split(" ")
        for i, w in enumerate(words):
            chunk = w + (" " if i < len(words) - 1 else "")
            yield f'data: {json.dumps({"token": chunk, "done": False})}\n\n'
            await asyncio.sleep(delay)

    # ── RBAC store-level (même logique que /chat) ────────────────────────────
    try:
        from app.api.auth import validate_store_access as _vsa
        _bearer = request.headers.get("Authorization", "")
        _token  = _bearer.replace("Bearer ", "").strip() or None
        await _vsa(_token, store_id)
    except Exception as _rbac_err:
        from fastapi import HTTPException as _HE
        if isinstance(_rbac_err, _HE) and _rbac_err.status_code in (401, 403):
            async def _denied():
                yield f'data: {json.dumps({"done": True, "reply": "Accès refusé.", "error": str(_rbac_err.detail)})}\n\n'
            return _SR(_denied(), media_type="text/event-stream")

    if not message:
        async def _empty():
            yield 'data: {"done": true, "reply": ""}\n\n'
        return _SR(_empty(), media_type="text/event-stream")

    # Dedup cache → word-by-word replay
    cached = _cache_get(advisor_name, store_id, message)
    if cached:
        async def _cached_gen():
            async for evt in _words(cached, 0.020):
                yield evt
            yield f'data: {json.dumps({"done": True, "rag_used": False, "source": "cache", "reply": cached})}\n\n'
        return _SR(_cached_gen(), media_type="text/event-stream",
                   headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    intent = _classify_intent(message)
    mode, domain, qtype = intent["mode"], intent["domain"], intent["type"]

    if mode == "off_topic":
        reply = ("Je suis ton coach Ooredoo — spécialisé en vente telecom, "
                 "stock et objectifs commerciaux. Sur quoi je peux t'aider ?")
        _cache_set(advisor_name, store_id, message, reply)
        async def _offtopic_gen():
            async for evt in _words(reply):
                yield evt
            yield f'data: {json.dumps({"done": True, "rag_used": False, "source": "guardrail", "reply": reply})}\n\n'
        return _SR(_offtopic_gen(), media_type="text/event-stream",
                   headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    ca_ctx      = float(ctx.get("current_revenue") or ctx.get("ca_today") or 0)
    target_ctx  = float(ctx.get("daily_target") or ctx.get("ca_target") or 1007)
    urgency     = str(ctx.get("urgency") or ctx.get("urgency_level") or "MEDIUM").upper()
    weather     = str(ctx.get("weather") or "Tunis")
    actions_ctx = ctx.get("strategie_actions") or []
    cause_ctx   = str(ctx.get("cause_racine") or "")

    now        = datetime.now()
    hour       = now.hour
    hours_left = max(1, 20 - hour)

    if mode == "greeting":
        perf  = round((ca_ctx / target_ctx * 100), 1) if ca_ctx and target_ctx else 0.0
        first = advisor_name.split()[0]
        if perf >= 95:
            reply = (f"Salut {first} ! Objectif presque atteint — excellent départ. "
                     "Sur quoi je peux t'aider pour finir en beauté ?")
        elif perf > 0:
            reply = (f"Salut {first} ! {perf:.0f}% de l'objectif avec {hours_left}h devant toi. "
                     "On attaque le gap ? Comment je peux t'aider ?")
        else:
            reply = (f"Salut {first} ! Je suis ton coach Ooredoo — "
                     "demande-moi un script, une stratégie ou l'état du stock.")
        _cache_set(advisor_name, store_id, message, reply)
        async def _greeting_gen():
            async for evt in _words(reply, 0.015):
                yield evt
            yield f'data: {json.dumps({"done": True, "rag_used": False, "source": "fast_path", "reply": reply})}\n\n'
        return _SR(_greeting_gen(), media_type="text/event-stream",
                   headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    need_inv = mode in ("inventory", "cross_domain") or qtype == "recap"
    loop     = asyncio.get_event_loop()

    if need_inv:
        pos_data, sales_detail, inv_ctx_data, day_history, catalog, adv_profile = await asyncio.gather(
            loop.run_in_executor(None, _load_pos_sync, store_id),
            loop.run_in_executor(None, _load_sales_detail_sync, store_id),
            loop.run_in_executor(None, _load_inventory_context_sync, store_id),
            loop.run_in_executor(None, _load_day_history_sync, advisor_name, store_id),
            loop.run_in_executor(None, _load_catalog, store_id),
            loop.run_in_executor(None, _load_advisor_profile_sync, advisor_name, store_id),
        )
    else:
        pos_data, sales_detail, day_history, catalog, adv_profile = await asyncio.gather(
            loop.run_in_executor(None, _load_pos_sync, store_id),
            loop.run_in_executor(None, _load_sales_detail_sync, store_id),
            loop.run_in_executor(None, _load_day_history_sync, advisor_name, store_id),
            loop.run_in_executor(None, _load_catalog, store_id),
            loop.run_in_executor(None, _load_advisor_profile_sync, advisor_name, store_id),
        )
        inv_ctx_data = {"stats": {}, "alerts": [], "top_sellers": [], "agent_recos": [],
                        "kpi_terminaux": 0, "kpi_forfaits": 0, "ca_moy_agent": 0}

    ca     = ca_ctx or pos_data.get("ca", 0.0)
    target = target_ctx
    perf   = round((ca / target * 100), 1) if target > 0 else 0.0
    gap    = max(0.0, target - ca)

    top_sellers = sales_detail.get("top_sellers", [])
    recent_tx   = sales_detail.get("recent_tx", [])

    rag_scripts, rag_relevant, rag_query, rag_ms = [], False, "", 0.0
    if mode == "coaching" or qtype in ("script", "objection", "closing", "upsell", "forfait", "objectif"):
        rag_query = f"{message} {qtype} vente telecom Ooredoo"
        if gap > target * 0.4:
            rag_query += " gap critique urgent"
        _rag_t0 = time.time()
        rag_scripts, rag_relevant = await loop.run_in_executor(
            None, _search_rag_sync, rag_query, hour, 2
        )
        rag_ms = (time.time() - _rag_t0) * 1000

    system_prompt   = _build_system_prompt(catalog)
    situation_block = _build_situation(
        advisor_name=advisor_name, store_id=store_id, hour=hour,
        ca=ca, target=target, perf=perf, gap=gap, hours_left=hours_left,
        urgency=urgency, weather=weather, cause=cause_ctx,
        mode=mode, qtype=qtype,
        actions=actions_ctx, rag_scripts=rag_scripts,
        top_sellers=top_sellers, recent_tx=recent_tx,
        inv_ctx=inv_ctx_data, advisor_profile=adv_profile,
    )
    user_message = f"{situation_block}\n\nQUESTION DU CONSEILLER : {message}"

    if qtype in ("script", "objectif"):    max_tokens, temp = 450, 0.17
    elif mode == "inventory":              max_tokens, temp = 380, 0.20
    elif urgency in ("HIGH", "CRITICAL"): max_tokens, temp = 280, 0.17
    else:                                  max_tokens, temp = 350, 0.22

    async def _sse_gen():
        import httpx
        full_reply: list[str] = []
        model_used = ""
        streamed_ok = False

        if OPENROUTER_KEY:
            try:
                messages = [{"role": "system", "content": system_prompt}]
                if day_history:
                    messages.extend(day_history[-8:])
                messages.append({"role": "user", "content": user_message})

                body = json.dumps({
                    "model": OPENROUTER_MODEL, "max_tokens": max_tokens,
                    "temperature": temp, "messages": messages, "stream": True,
                }, ensure_ascii=False).encode("utf-8")

                headers = {
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type":  "application/json; charset=utf-8",
                    "HTTP-Referer":  "https://github.com/MALEKALADAB11/multi-agent-sales-inventory",
                    "X-Title":       "AI Sales Coach Ooredoo v10 stream",
                }

                async with httpx.AsyncClient(timeout=30.0) as client:
                    async with client.stream("POST", OPENROUTER_URL, headers=headers, content=body) as resp:
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                delta = json.loads(data_str)["choices"][0]["delta"].get("content", "")
                                if delta:
                                    full_reply.append(delta)
                                    yield f'data: {json.dumps({"token": delta, "done": False})}\n\n'
                                    streamed_ok = True
                            except Exception:
                                continue
                model_used = OPENROUTER_MODEL
            except Exception as e:
                logger.warning("[COACH STREAM] OR error: %.80s", str(e))

        # Fallback: non-streaming chain → word-by-word replay
        if not streamed_ok:
            reply_fb, _ = await _call_openrouter(system_prompt, user_message, max_tokens, temp, day_history)
            if not _is_valid_reply(reply_fb):
                reply_fb = await _call_ollama_fallback(system_prompt, user_message, max_tokens)
                model_used = "ollama"
            if not _is_valid_reply(reply_fb):
                reply_fb = _build_intent_fallback(
                    mode=mode, qtype=qtype, advisor_name=advisor_name,
                    ca=ca, target=target, perf=perf, gap=gap,
                    hours_left=hours_left, urgency=urgency,
                    actions=actions_ctx, top_sellers=top_sellers, inv_ctx=inv_ctx_data,
                )
                model_used = "intent_fallback"
            full_reply = []
            async for evt in _words(reply_fb, 0.018):
                full_reply.append(evt)
                yield evt

        final_reply = "".join(full_reply).strip() if streamed_ok else "".join(
            json.loads(e[6:])["token"] for e in full_reply if e.startswith("data:") and '"token"' in e
        )
        # Clean token artifacts when built from streaming
        if not streamed_ok:
            final_reply = "".join(
                json.loads(e[6:]).get("token", "")
                for e in full_reply if e.startswith("data:") and not json.loads(e[6:]).get("done")
            ) if full_reply and full_reply[0].startswith("data:") else final_reply

        # For streamed output re-join from list
        if streamed_ok:
            final_reply = "".join(full_reply).strip()
            for pfx in ["Reponse:", "Coach:", "CoachIA:", "Réponse :", "RÉPONSE :"]:
                if final_reply.lower().startswith(pfx.lower()):
                    final_reply = final_reply[len(pfx):].strip()

        _cache_set(advisor_name, store_id, message, final_reply)
        logger.info("[COACH STREAM] %s | %dc | RAG=%s | model=%s",
                    advisor_name, len(final_reply), rag_relevant, model_used)

        # ── Guardrail validation (S7.1) ─────────────────────────────────────
        _sgrd = {"status": "APPROVE", "issues": [], "requires_human_validation": False, "safe_fallback": ""}
        try:
            from app.sales.coaching.agents.guardrail.guardrail_agent import evaluate_guardrails as _eval_g
            _snap_s = {
                "skus": {
                    a["nom"]: {
                        "product_name":   a["nom"],
                        "stock_qty":      max(0, int(a.get("qty", 1))),
                        "days_remaining": float(a.get("jours", 999)),
                    }
                    for a in inv_ctx_data.get("alerts", [])
                }
            }
            _sgrd = _eval_g(
                recommendation={"product_to_push": None, "message_for_advisor": final_reply, "strategy_actions": actions_ctx},
                store_id=store_id, inventory_snapshot=_snap_s,
                rag_used=rag_relevant, nb_scripts=len(rag_scripts), confidence=0.80,
            )
            if _sgrd["status"] == "BLOCK" and _sgrd.get("safe_fallback"):
                final_reply = _sgrd["safe_fallback"]
        except Exception:
            pass

        total_ms = (time.time() - t0) * 1000

        # ── Persistance + télémétrie monitoring (non-bloquant) ──────────────
        # Le endpoint /stream est celui réellement utilisé par le CoachAgent
        # frontend (chat.ts) — sans ceci, ni coach_interactions ni agent_logs
        # ne voyaient jamais ces échanges, d'où "NO TELEMETRY" sur la page
        # Monitoring pour Coach/RAG/Guardrail malgré une activité réelle.
        async def _persist_stream():
            try:
                from app.sales.coaching.agents.coach.tools import save_interaction
                save_interaction(
                    advisor_name=advisor_name, store_id=store_id,
                    message=message, response=final_reply,
                    gap_pct=round(gap/target*100, 1) if target else 0,
                    urgency=urgency, rag_used=rag_relevant,
                    nb_rag_scripts=len(rag_scripts),
                    conseil_type=f"{domain}/{qtype}", confidence=0.80,
                )
            except Exception:
                pass
            try:
                from app.core.agent_logger import log_node_start, log_node_complete
                _cycle_id = f"stream-{advisor_name[:20]}-{int(t0*1000)}"

                _lid = log_node_start(
                    cycle_id=_cycle_id, agent_name="coach", node_name="chat_reply",
                    input_state={"message": message[:200], "advisor": advisor_name,
                                 "mode": mode, "qtype": qtype, "urgency": urgency},
                    store_id=store_id,
                )
                log_node_complete(
                    _lid,
                    output_state={"reply_preview": final_reply[:200], "model": model_used,
                                  "rag_used": rag_relevant, "nb_rag_scripts": len(rag_scripts)},
                    duration_ms=total_ms,
                    metadata={"guardrail_status": _sgrd.get("status", "APPROVE")},
                    status="error" if not model_used else "completed",
                )

                if rag_query:
                    _rid = log_node_start(
                        cycle_id=_cycle_id, agent_name="rag", node_name="search",
                        input_state={"query": rag_query[:150], "hour": hour},
                        store_id=store_id,
                    )
                    log_node_complete(
                        _rid,
                        output_state={"nb_scripts": len(rag_scripts), "relevant": rag_relevant,
                                      "top_score": rag_scripts[0]["score"] if rag_scripts else 0},
                        duration_ms=rag_ms,
                        status="completed" if rag_relevant else "fallback",
                    )

                _sgrd_status = _sgrd.get("status", "APPROVE")
                _gid = log_node_start(
                    cycle_id=_cycle_id, agent_name="guardrail", node_name="evaluate_guardrails",
                    input_state={"advisor": advisor_name, "rag_used": rag_relevant,
                                 "urgency": urgency},
                    store_id=store_id,
                )
                log_node_complete(
                    _gid,
                    output_state={"status": _sgrd_status, "issues": _sgrd.get("issues", [])[:3],
                                  "requires_hitl": _sgrd.get("requires_human_validation", False)},
                    duration_ms=0.0,
                    status={"APPROVE": "completed", "REWRITE": "completed",
                            "ESCALATE": "fallback", "BLOCK": "error"}.get(_sgrd_status, "completed"),
                )
            except Exception as _tel_err:
                logger.debug("[COACH STREAM] Monitoring telemetry skipped: %s", _tel_err)

            # ── Push WS temps réel — le panneau "Guardrail Events" du Monitoring
            # écoutait déjà ce message mais /stream ne l'émettait jamais (seul
            # /chat le faisait), donc aucun incident réel n'apparaissait en live.
            if _sgrd.get("status") in ("BLOCK", "ESCALATE"):
                try:
                    from main import _broadcast
                    await _broadcast(store_id, json.dumps({
                        "type":      "guardrail_event",
                        "status":    _sgrd["status"],
                        "store_id":  store_id,
                        "advisor":   advisor_name,
                        "issues":    _sgrd.get("issues", []),
                        "urgency":   urgency,
                        "timestamp": datetime.utcnow().isoformat(),
                    }))
                except Exception:
                    pass

        asyncio.create_task(_persist_stream())

        yield f'data: {json.dumps({"done": True, "reply": final_reply, "rag_used": rag_relevant, "source": f"v10_{model_used}", "model": model_used, "guardrail_status": _sgrd.get("status", "APPROVE"), "guardrail_issues": _sgrd.get("issues", []), "requires_hitl": _sgrd.get("requires_human_validation", False)})}\n\n'

    return _SR(
        _sse_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ══════════════════════════════════════════════════════════════════════════════
# 9. HEALTH
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/health")
async def coach_health():
    return JSONResponse({
        "status":       "ok",
        "version":      "10.0.0",
        "architecture": "claude-like persona-first lean-prompts",
        "features": [
            "persona_first_prompting",
            "few_shot_in_system_prompt",
            "lean_context_selection",
            "intent_selective_context",
            "response_dedup_cache_20s",
            "greeting_fast_path_no_llm",
            "off_topic_guard",
            "retry_chain_3_levels",
            "ollama_chat_fallback",
            "intent_aware_fallback",
            "response_validator",
            "async_persist_nonblocking",
            "parallel_context_loading",
            "selective_inventory_loading",
            "dynamic_catalog_db_10min_ttl",
        ],
        "llm_primary":  OPENROUTER_MODEL,
        "llm_fallback": "ollama_local_chat_model",
        "llm_available": bool(OPENROUTER_KEY),
        "prompt_tokens_approx": {
            "system_persona": "~320",
            "catalog":        "~80 (dynamique DB)",
            "situation_block": "100-220 (intent-sélectif)",
            "total_per_call":  "~500-620",
        },
    })
