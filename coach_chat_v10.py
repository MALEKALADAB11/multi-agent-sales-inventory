"""
coach_chat_v10.py — Coach Chat "Claude-Like" v10.0
====================================================
Philosophie : un coach qui PENSE, pas qui suit des règles.

ARCHITECTURE :
  1. PERSONA-FIRST    — identité forte, 3 few-shots dans le prompt système
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

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/coach", tags=["coach"])

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

OPENROUTER_KEY   = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")
OPENROUTER_URL   = "https://openrouter.ai/api/v1/chat/completions"

OLLAMA_URL  = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MILVUS_URI  = os.getenv("MILVUS_URI", "http://localhost:19530")
COLLECTION  = "coaching_scripts"
EMBED_DIM   = 768

DB_CFG = {
    "host":     os.getenv("POSTGRES_HOST", os.getenv("DB_HOST", "localhost")),
    "port":     int(os.getenv("POSTGRES_PORT", os.getenv("DB_PORT", "5432"))),
    "dbname":   os.getenv("POSTGRES_DB", os.getenv("DB_NAME", "ooredoo_sales")),
    "user":     os.getenv("POSTGRES_USER", os.getenv("DB_USER", "postgres")),
    "password": os.getenv("POSTGRES_PASSWORD", os.getenv("DB_PASSWORD", "admin")),
}

_STORE_MAP = {
    "store-lac2": "I63", "oor_lac_01": "I63", "lac2": "I63", "i63": "I63",
    "store-menzah": "M01", "oor_menzah_02": "M01", "menzah": "M01",
    "store-sfax": "S01", "oor_sfax_03": "S01", "sfax": "S01",
}

def _normalize_store(s: str) -> str:
    return _STORE_MAP.get((s or "").lower(), s or "I63")

# ══════════════════════════════════════════════════════════════════════════════
# PERSONA + CATALOGUE (système stable — ~320 tokens)
# ══════════════════════════════════════════════════════════════════════════════

_SYSTEM_PERSONA = """Tu es Coach, l'IA de vente d'Ooredoo Tunisie.
Tu penses comme un directeur commercial senior qui fait du coaching terrain. Tu connais chaque produit par cœur, tu lis une situation de vente en un coup d'œil, tu donnes UN seul conseil actionnable. Pas de liste à rallonge, pas de langage corporatif.

TON STYLE :
• Tutoiement chaleureux — tu es le collègue de confiance du conseiller
• Décisif — 1 action concrète, pas 4 options
• Court — 80 à 140 mots, sauf script complet explicitement demandé
• Contextuel — tu t'appuies sur les vrais chiffres de la boutique
• Termine toujours par "Vas-y !", "À toi !", "Allez !" ou "Maintenant !"

JAMAIS :
• Inventer un prix, une offre ou un SKU hors catalogue ci-dessous
• Répondre hors vente / stock / Ooredoo Tunisie
• Écrire "je n'ai pas accès" ou "consulte le tableau de bord"
• Produire du JSON, du code, des balises HTML
• Répéter deux fois la même information dans la même réponse

CATALOGUE OFFICIEL OOREDOO (seuls prix autorisés) :
Terminaux  : iPhone 16 Pro 1 299 TND · Samsung A55 5G 899 TND · Galaxy S25 Ultra 1 599 TND · INFINIX NOTE 40 349 TND
Forfaits   : 5G Max 100Go 49 TND/mois · Flexi 25Go 29 TND/mois · Unlimited 69 TND/mois · Famille 5G 120 TND/mois
Services   : Assurance Premium 9 TND/mois · Cloud Backup 15 TND/mois · TV Streaming 12 TND/mois
Accessoires: AirPods Pro 3 279 TND · Apple Watch S10 449 TND
Bundle max : iPhone 16 Pro + 5G Max + Assurance ≈ 1 357 TND (54 TND/mois sur 24 mois avance postpayé)

EXEMPLES DE RÉPONSES PARFAITES :
[salutation] → «Salut {prenom} ! {perf}% de l'objectif à {heure}h — {observation en 5 mots}. Tu veux qu'on attaque le gap ou tu as une situation client ?»
[script iPhone] → «Script iPhone 16 Pro : 1. "Tu l'utilises pour quoi surtout ?" 2. Démo live puce A18 Pro — vitesse photo incomparable. 3. "1 299 TND ou 54 TND/mois sur 24 mois." 4. Bundle Assurance 9 TND/mois — écran remplacé en 48h. 5. Close : "Noir titane ou blanc naturel ?" Vas-y !»
[objection trop cher] → «Réponds : "1 299 TND sur 24 mois = 54 TND/mois — moins qu'un café par jour. Et dans 2 ans valeur revente 400+ TND." Close : "Noir titane ou blanc naturel ?" À toi !»
[bilan du jour] → «CA {ca} / {target} TND ({perf}%). Top : {produit} ({qty} vendus/7j). Dernière vente {heure}h. {conseil Stratège en 1 ligne}. Allez !»
[rupture stock] → «{produit} en rupture = {nb} ventes perdues en risque. Met le {alternatif} (349 TND) en avant + appelle le manager pour la commande. Maintenant !»
[closing] → «Choix forcé : "Lequel vous correspond le mieux, noir ou blanc ?" Si résistance : "Il nous reste 2 unités — l'offre 0% expire ce soir." Silence 3 secondes. À toi !»"""

# ══════════════════════════════════════════════════════════════════════════════
# DEDUP CACHE (évite doublons sur envoi rapide)
# ══════════════════════════════════════════════════════════════════════════════

_cache: dict[str, tuple[str, float]] = {}
_DEDUP_TTL = 20.0  # secondes

def _cache_get(advisor: str, store: str, msg: str) -> Optional[str]:
    key = f"{advisor}:{store}:{msg.lower().strip()[:120]}"
    if key in _cache:
        reply, ts = _cache[key]
        if time.time() - ts < _DEDUP_TTL:
            return reply
    return None

def _cache_set(advisor: str, store: str, msg: str, reply: str) -> None:
    key = f"{advisor}:{store}:{msg.lower().strip()[:120]}"
    _cache[key] = (reply, time.time())
    # Nettoyage si trop grand
    if len(_cache) > 300:
        cutoff = time.time() - _DEDUP_TTL * 2
        for k in [k for k, (_, ts) in _cache.items() if ts < cutoff]:
            _cache.pop(k, None)

# ══════════════════════════════════════════════════════════════════════════════
# 1. INTENT CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════

_GREETINGS_SET = {
    "bonjour", "bonsoir", "salut", "hello", "hey", "coucou", "bjr", "bj", "slt",
    "hi", "yo", "hola", "bonjour coach", "salut coach", "bonsoir coach", "hey coach",
    "bonne journee", "bonne soiree",
}

_COACHING_KW: dict[str, list[str]] = {
    "script":    ["script", "comment vendre", "que dire", "pitch", "argumentaire",
                  "comment presenter", "discours de vente", "donne moi un script"],
    "objection": ["objection", "trop cher", "il refuse", "elle refuse", "pas besoin",
                  "concurrent", "reticent", "comment repondre", "il dit que", "elle dit que",
                  "client dit", "client dit non"],
    "closing":   ["closing", "comment closer", "finaliser", "il hesite", "elle hesite",
                  "indecis", "comment convaincre", "fermer la vente", "signer"],
    "upsell":    ["upsell", "cross-sell", "ajouter", "bundle", "vient d acheter",
                  "apres la vente", "complementaire", "accessoire"],
    "forfait":   ["convertir", "migration forfait", "recharge vers forfait",
                  "passer en 5g", "changer de forfait", "basculer", "bascule"],
    "objectif":  ["atteindre objectif", "combler le gap", "rattraper", "plan d action",
                  "comment faire pour atteindre", "boost ca", "augmenter les ventes"],
    "meteo":     ["strategie meteo", "il pleut", "adapter la meteo", "temps couvert",
                  "profiter du beau temps", "beau temps", "mauvais temps"],
}

_INV_KW = [
    "stock", "rupture", "inventaire", "quantite", "disponible", "epuise",
    "combien reste", "en stock", "reapprovisionner", "commande", "livraison",
    "rotation", "dormant", "eoq", "point de commande", "alerte stock",
    "critique", "velocity",
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
    "resultat", "performance du jour", "comment ca va les ventes",
    "rapport du jour", "synthese", "comment on est",
]

_OFF_KW = [
    "cybersecurite", "crypto", "bitcoin", "politique", "recette", "cuisine",
    "football", "match", "film", "serie", "jeu video", "gaming", "musique",
    "philosophie", "mathematique", "physique", "chimie", "histoire generale",
    "religion", "horoscope", "code python", "javascript", "programmation",
    "machine learning", "deep learning", "chatgpt", "bourse", "trading",
    "medecine", "sante", "geographie", "tourisme", "voyage",
]

_TELECOM_KW = [
    "ooredoo", "vente", "client", "boutique", "forfait", "stock",
    "objectif", "conseiller", "terminal", "iphone", "samsung", "5g",
    "assurance", "bundle", "coach", "produit", "recharge",
]


def _norm(t: str) -> str:
    for a, b in [("é","e"),("è","e"),("ê","e"),("à","a"),("ô","o"),("î","i"),("ç","c"),("ù","u"),("â","a"),("û","u"),("ï","i"),("œ","oe"),("'","'")]:
        t = t.replace(a, b)
    return t


def _is_greeting(msg: str) -> bool:
    n = _norm(msg.strip().lower().rstrip("!?,. "))
    if n in _GREETINGS_SET:
        return True
    words = n.split()
    return len(words) <= 3 and words[0] in _GREETINGS_SET


def classify_intent(message: str) -> dict:
    msg = _norm(message.lower())

    # Salutation
    if _is_greeting(msg):
        return {"mode": "greeting", "domain": "sales", "type": "greeting", "confidence": 0.99}

    # Hors sujet
    off_hits  = sum(1 for k in _OFF_KW    if k in msg)
    tel_hits  = sum(1 for k in _TELECOM_KW if k in msg)
    if off_hits >= 1 and tel_hits == 0:
        return {"mode": "off_topic", "domain": "none", "type": "off_topic", "confidence": 0.96}

    # Bilan / recap
    recap_hits = sum(1 for k in _RECAP_KW if k in msg)
    if recap_hits >= 1:
        return {"mode": "conversation", "domain": "sales", "type": "recap",
                "confidence": min(0.93, 0.72 + recap_hits * 0.08)}

    # Cross-domain : stock + vente ensemble
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

    # Coaching
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
# 2. CONTEXT LOADERS (lean — timeout courts)
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
        logger.warning("[v10 POS] %.60s", str(e))
    return {"ca": 0.0, "nb_tx": 0}


def _load_sales_detail_sync(store_id: str) -> dict:
    """Top 5 vendeurs 7j + 3 dernières transactions."""
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
        logger.warning("[v10 SALES] %.60s", str(e))
    return r


def _load_stock_sync(store_id: str) -> dict:
    """Stats stock + top 4 alertes + top 3 vendeurs + agent recos."""
    import psycopg2, psycopg2.extras
    r = {"stats": {}, "alerts": [], "top_sellers": [], "agent_recos": []}
    try:
        conn = psycopg2.connect(**DB_CFG, connect_timeout=8)
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            # Stats globales
            cur.execute("""
                SELECT COUNT(*) AS total,
                  SUM(CASE WHEN COALESCE(quantity_available,quantity,0) <= 0 THEN 1 ELSE 0 END) AS ruptures,
                  SUM(CASE WHEN COALESCE(quantity_available,quantity,0) BETWEEN 1 AND 5 THEN 1 ELSE 0 END) AS critiques,
                  SUM(CASE WHEN COALESCE(quantity_available,quantity,0) > 15 THEN 1 ELSE 0 END) AS ok_count
                FROM inventory.stock_levels WHERE store_id = %s
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
                            WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 5 THEN 'critical'
                            ELSE 'warning' END AS level,
                       COALESCE(rp.demande_moy_jour, 0.5) AS vel
                FROM inventory.stock_levels sl
                LEFT JOIN sales.produits p ON p.sku = sl.sku
                LEFT JOIN supply.reorder_params rp ON rp.sku=sl.sku AND rp.store_id=sl.store_id
                WHERE sl.store_id=%s
                  AND COALESCE(sl.quantity_available, sl.quantity, 0) <= 15
                ORDER BY COALESCE(sl.quantity_available, sl.quantity, 0) ASC LIMIT 5
            """, (store_id,))
            for row in cur.fetchall():
                stock = int(row["qty"] or 0)
                vel   = float(row["vel"] or 0.5)
                jours = round(stock / vel, 0) if vel > 0 and stock > 0 else 0
                r["alerts"].append({"nom": row["nom"], "qty": stock, "level": row["level"], "jours": jours})

            # Top vendeurs avec vélocité
            cur.execute("""
                SELECT COALESCE(p.nom, t.sku::text) AS nom,
                       SUM(t.quantity) AS sold,
                       COALESCE(sl.quantity_available, sl.quantity, 0) AS stock,
                       COALESCE(rp.demande_moy_jour, SUM(t.quantity)::NUMERIC/7) AS vel
                FROM   sales.transactions t
                LEFT JOIN sales.produits p ON p.sku = t.sku
                LEFT JOIN inventory.stock_levels sl ON sl.sku=t.sku AND sl.store_id=t.store_id
                LEFT JOIN supply.reorder_params rp ON rp.sku=t.sku AND rp.store_id=t.store_id
                WHERE  t.store_id=%s
                  AND  t.date_only>=(
                         SELECT MAX(date_only) FROM sales.transactions WHERE store_id=%s
                       ) - INTERVAL '7 days'
                GROUP BY p.nom, t.sku, sl.quantity_available, sl.quantity, rp.demande_moy_jour
                ORDER BY sold DESC LIMIT 4
            """, (store_id, store_id))
            for row in cur.fetchall():
                vel   = float(row["vel"] or 0)
                stock = int(row["stock"] or 0)
                jours = round(stock / vel, 0) if vel > 0 and stock > 0 else 999
                r["top_sellers"].append({"nom": row["nom"], "sold": int(row["sold"] or 0),
                                         "stock": stock, "jours": jours})

            # Recommandations agent inventaire
            cur.execute("""
                SELECT COALESCE(p.nom, r.sku::text) AS nom,
                       r.recommendation_type AS type,
                       r.suggested_quantity  AS qty
                FROM inventory.recommendations r
                LEFT JOIN sales.produits p ON p.sku = r.sku
                WHERE r.store_id=%s
                  AND r.status IN ('pending','approved')
                  AND r.created_at >= CURRENT_TIMESTAMP - INTERVAL '24 hours'
                ORDER BY r.created_at DESC LIMIT 3
            """, (store_id,))
            r["agent_recos"] = [
                {"nom": row["nom"], "type": row["type"], "qty": int(row["qty"] or 0)}
                for row in cur.fetchall()
            ]
        finally:
            cur.close(); conn.close()
    except Exception as e:
        logger.warning("[v10 STOCK] %.80s", str(e))
    return r


def _load_history_sync(advisor: str, store_id: str, limit: int = 4) -> list:
    """Historique des échanges du jour (messages + réponses)."""
    import psycopg2
    try:
        conn = psycopg2.connect(**DB_CFG, connect_timeout=5)
        cur  = conn.cursor()
        try:
            cur.execute("""
                SELECT message, response FROM public.coach_interactions
                WHERE store_id=%s AND advisor_name=%s AND created_at >= CURRENT_DATE
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
        logger.debug("[v10 HIST] %.60s", str(e))
    return []

# ══════════════════════════════════════════════════════════════════════════════
# 3. RAG (coaching seulement — Milvus + Ollama embeddings)
# ══════════════════════════════════════════════════════════════════════════════

def _rag_search(query: str, hour: int, top_k: int = 2, min_score: float = 0.32) -> tuple[list, bool]:
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
            collection_name=COLLECTION,
            data=[emb],
            limit=top_k + 2,
            output_fields=["categorie","situation","action","produit","argument","impact","heure_min","heure_max"],
        )
        scripts = []
        for hit in results[0]:
            e     = hit["entity"]
            score = float(hit["distance"])
            if int(e.get("heure_min", 0)) <= hour <= int(e.get("heure_max", 24)):
                score += 0.08
            scripts.append({
                "score":     round(score, 3),
                "categorie": e.get("categorie", ""),
                "action":    e.get("action", "")[:100],
                "argument":  e.get("argument", "")[:100],
                "produit":   e.get("produit", ""),
            })
        scripts = sorted(scripts, key=lambda x: x["score"], reverse=True)[:top_k]
        relevant = bool(scripts) and scripts[0]["score"] >= min_score
        return scripts, relevant
    except Exception as e:
        logger.debug("[v10 RAG] %.50s", str(e))
        return [], False

# ══════════════════════════════════════════════════════════════════════════════
# 4. SITUATION BLOCK (contexte sélectif par intent — ~120-200 tokens)
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
    stock_data: dict,
) -> str:
    """Bloc de situation — adapté à l'intent, compact et actionnable."""

    # En-tête universel (~40 tokens)
    lines = [
        f"SITUATION {advisor_name} | {store_id} | {hour}h | {weather} | Urgence : {urgency}",
        f"Performance : {ca:,.0f}/{target:,.0f} TND ({perf:.0f}%) | Gap : {gap:,.0f} TND | {hours_left}h restantes",
    ]
    if cause:
        lines.append(f"Cause gap : {cause}")

    # Contexte sélectif par intent
    if mode == "coaching":
        # Stratège : meilleure action
        if actions:
            a = actions[0]
            lines.append(
                f"Action Stratège : {a.get('action','')} → {a.get('produit_cible','')} "
                f"| Argument : {a.get('argument_vente','')[:80]}"
            )
        # RAG : meilleur script terrain
        if rag_scripts:
            s = rag_scripts[0]
            lines.append(
                f"Script terrain [{s['categorie']}] : {s['action'][:90]} "
                f"| {s.get('argument','')[:80]}"
            )

    elif mode in ("inventory", "cross_domain"):
        alerts   = stock_data.get("alerts", [])
        top_inv  = stock_data.get("top_sellers", [])
        stats    = stock_data.get("stats", {})
        recos    = stock_data.get("agent_recos", [])

        lines.append(
            f"Stock : {stats.get('total',0)} SKUs | "
            f"Ruptures : {stats.get('ruptures',0)} | "
            f"Critiques : {stats.get('critiques',0)} | "
            f"OK : {stats.get('ok_count',0)}"
        )
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
        stats = stock_data.get("stats", {})
        if stats.get("ruptures", 0) > 0:
            lines.append(f"Alerte : {stats['ruptures']} rupture(s) en cours")

    else:  # conversation générale
        if actions:
            a = actions[0]
            lines.append(f"Action du jour : {a.get('action','')} → {a.get('produit_cible','')}")

    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════════════
# 5. LLM CALL CHAIN
# ══════════════════════════════════════════════════════════════════════════════

def _is_valid_reply(reply: str) -> bool:
    """Vérifie que la réponse LLM est utilisable."""
    if not reply or len(reply.strip()) < 25:
        return False
    r = reply.strip()
    # Fuite en anglais
    if r[:6] in ("I'm ", "I can", "I wil", "The c", "To he", "Based", "Here ", "Sure,"):
        return False
    # JSON leaked
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
    try:
        import httpx
        messages = [{"role": "system", "content": system}]
        if history:
            messages.extend(history[-8:])  # max 4 derniers échanges
        messages.append({"role": "user", "content": user_msg})

        body = json.dumps({
            "model":       OPENROUTER_MODEL,
            "max_tokens":  max_tokens,
            "temperature": temperature,
            "messages":    messages,
        }, ensure_ascii=False).encode("utf-8")

        headers = {
            "Authorization": f"Bearer {OPENROUTER_KEY}",
            "Content-Type":  "application/json; charset=utf-8",
            "HTTP-Referer":  "https://github.com/MALEKALADAB11/multi-agent-sales-inventory",
            "X-Title":       "AI Sales Coach Ooredoo v10",
        }

        async with httpx.AsyncClient(timeout=28.0) as client:
            resp = await client.post(OPENROUTER_URL, headers=headers, content=body)

        data = resp.json()
        if "error" in data:
            logger.warning("[v10 OR] error: %.80s", data["error"].get("message",""))
            return "", (time.time() - t0) * 1000

        raw = data["choices"][0]["message"]["content"].strip()

        # Nettoyage préfixes indésirables
        for pfx in ["Reponse:", "Coach:", "CoachIA:", "Réponse :", "RÉPONSE :",
                    "Here is", "Based on", "Voici ma", "D'accord,"]:
            if raw.lower().startswith(pfx.lower()):
                raw = raw[len(pfx):].strip()

        # JSON wrappé
        if raw.startswith("{"):
            m = re.search(r'"(?:reply|response|conseil|text|message)"\s*:\s*"([^"]+)"', raw)
            raw = m.group(1).strip() if m else ""

        ms = (time.time() - t0) * 1000
        logger.info("[v10 OR] %dc | %.0fms | %s", len(raw), ms, OPENROUTER_MODEL)
        return raw, ms

    except Exception as e:
        logger.warning("[v10 OR] %.100s", str(e))
        return "", (time.time() - t0) * 1000


async def _call_ollama_chat(system: str, user_msg: str, max_tokens: int) -> str:
    """Fallback Ollama — utilise le premier modèle de chat disponible."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            tags = await client.get(f"{OLLAMA_URL}/api/tags")
        models = [m["name"] for m in tags.json().get("models", [])]

        chat_model = next(
            (m for m in models if any(p in m for p in
             ["llama3", "llama2", "mistral", "mixtral", "phi", "gemma", "qwen"])),
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
        logger.debug("[v10 OLLAMA] %.50s", str(e))
        return ""

# ══════════════════════════════════════════════════════════════════════════════
# 6. INTENT FALLBACK (last resort — jamais générique pour toutes les situations)
# ══════════════════════════════════════════════════════════════════════════════

def _intent_fallback(
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
    stock_alerts: list,
) -> str:
    name = advisor_name.split()[0] if advisor_name else "toi"

    if mode == "greeting":
        if perf >= 95:
            return (f"Salut {name} ! Objectif presque atteint — excellent départ. "
                    "Sur quoi je peux t'aider pour finir en beauté ?")
        if perf >= 70:
            return (f"Salut {name} ! {perf:.0f}% de l'objectif avec {hours_left}h devant toi — "
                    "bien parti. On attaque le gap ?")
        return (f"Salut {name} ! {perf:.0f}% de l'objectif — {hours_left}h, c'est large. "
                "Qu'est-ce qu'on attaque ?")

    if qtype == "recap":
        top = f"Top : {top_sellers[0]['nom']} ({top_sellers[0]['qty']} vendus)." if top_sellers else ""
        a_txt = f"Action : {actions[0].get('action','')}." if actions else ""
        return (f"CA {ca:,.0f} / {target:,.0f} TND ({perf:.0f}%). {top} "
                f"Gap : {gap:,.0f} TND avec {hours_left}h. {a_txt} Allez !")

    if mode == "inventory":
        if stock_alerts:
            names = ", ".join(a["nom"] for a in stock_alerts[:2])
            return (f"{names} en rupture/critique. "
                    "Contacte le manager pour réappro urgente. "
                    "En attendant, pousse les alternatives disponibles. Maintenant !")
        return f"Stock global OK. {gap:,.0f} TND de gap avec {hours_left}h. À toi !"

    if qtype == "objection":
        return ("Réponds : «Sur 24 mois via avance postpayé Ooredoo, "
                "c'est 54 TND/mois — moins qu'un café par jour. "
                "Et valeur revente 400+ TND dans 2 ans.» "
                "Close : «Noir titane ou blanc naturel ?» À toi !")

    if qtype in ("closing",):
        return ("Choix forcé : «Lequel vous correspond le mieux, noir ou blanc ?» "
                "Si résistance : «Il nous reste 2 unités — l'offre expire ce soir.» "
                "Silence 3 secondes. À toi !")

    if qtype in ("script", "upsell", "forfait", "objectif"):
        if actions:
            a = actions[0]
            return (f"Action prioritaire : {a.get('action','Bundle terminal + forfait')} "
                    f"→ {a.get('produit_cible','iPhone 16 Pro + Forfait 5G Max')}. "
                    f"Argument : {a.get('argument_vente','Financement 0 TND aujourd’hui')}. Vas-y !")
        return ("Script : 1. Accroche besoins 2. Démo live 3. Prix + financement "
                "4. Assurance 9 TND/mois 5. Close coloris. Vas-y !")

    # General / cross_domain
    if actions:
        a = actions[0]
        return (f"{perf:.0f}% de l'objectif. {a.get('action','')} → "
                f"{a.get('produit_cible','')}. "
                f"{gap:,.0f} TND de gap, {hours_left}h devant toi. Allez !")
    return (f"{perf:.0f}% de l'objectif. Assurance Premium 9 TND/mois sur chaque terminal — "
            f"marge 80%, {gap:,.0f} TND de gap. À toi !")

# ══════════════════════════════════════════════════════════════════════════════
# 7. MAIN ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/chat")
async def coach_chat(request: dict):
    t0 = time.time()

    message      = (request.get("message") or "").strip()
    advisor_name = (request.get("advisor_name") or "Conseiller").strip()
    store_id     = _normalize_store(request.get("store_id") or "I63")
    ctx          = request.get("context") or {}

    if not message:
        return JSONResponse({"reply": "", "mode": "empty"})

    # ── Dedup cache ──────────────────────────────────────────────────────────
    cached = _cache_get(advisor_name, store_id, message)
    if cached:
        logger.info("[v10] DEDUP hit '%s'", message[:40])
        return JSONResponse({
            "reply": cached, "mode": "cached", "domain": "sales",
            "question_type": "dedup", "source": "cache", "model": "cache",
            "confidence": 1.0, "rag_used": False, "nb_rag_scripts": 0,
            "latency_ms": round((time.time()-t0)*1000),
            "sources": [], "rag_scripts": [], "inventory_alerts": [], "agent_recos": [],
            "context_used": {"advisor": advisor_name, "store_id": store_id},
        })

    # ── Intent ───────────────────────────────────────────────────────────────
    intent = classify_intent(message)
    mode   = intent["mode"]
    domain = intent["domain"]
    qtype  = intent["type"]
    logger.info("[v10] mode=%s type=%s conf=%.2f msg='%s'",
                mode, qtype, intent["confidence"], message[:50])

    # ── Off-topic ────────────────────────────────────────────────────────────
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
    target_ctx  = float(ctx.get("daily_target")    or ctx.get("ca_target") or 1007)
    urgency_ctx = str(ctx.get("urgency") or ctx.get("urgency_level") or "MEDIUM").upper()
    weather_ctx = str(ctx.get("weather") or "Tunis")
    actions_ctx = ctx.get("strategie_actions") or []
    cause_ctx   = str(ctx.get("cause_racine") or "")

    now        = datetime.now()
    hour       = now.hour
    hours_left = max(1, 20 - hour)

    # ── Greeting fast path (< 5ms, sans LLM) ────────────────────────────────
    if mode == "greeting":
        perf = round((ca_ctx / target_ctx * 100), 1) if ca_ctx and target_ctx else 0.0
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
            "sources": [{"id":"fast","label":"Fast path","active":True}],
            "rag_scripts": [], "inventory_alerts": [], "agent_recos": [],
            "context_used": {"advisor": advisor_name, "store_id": store_id,
                             "hour": hour, "performance": perf},
        })

    # ── Chargement contexte en parallèle ────────────────────────────────────
    need_inv = mode in ("inventory", "cross_domain") or qtype == "recap"

    loop = asyncio.get_event_loop()
    if need_inv:
        pos_data, sales_detail, stock_data, day_history = await asyncio.gather(
            loop.run_in_executor(None, _load_pos_sync, store_id),
            loop.run_in_executor(None, _load_sales_detail_sync, store_id),
            loop.run_in_executor(None, _load_stock_sync, store_id),
            loop.run_in_executor(None, _load_history_sync, advisor_name, store_id),
        )
    else:
        pos_data, sales_detail, day_history = await asyncio.gather(
            loop.run_in_executor(None, _load_pos_sync, store_id),
            loop.run_in_executor(None, _load_sales_detail_sync, store_id),
            loop.run_in_executor(None, _load_history_sync, advisor_name, store_id),
        )
        stock_data = {"stats": {}, "alerts": [], "top_sellers": [], "agent_recos": []}

    ca     = ca_ctx or pos_data.get("ca", 0.0)
    target = target_ctx
    perf   = round((ca / target * 100), 1) if target > 0 else 0.0
    gap    = max(0.0, target - ca)
    nb_tx  = pos_data.get("nb_tx", 0) or len(sales_detail.get("recent_tx", []))

    top_sellers = sales_detail.get("top_sellers", [])
    recent_tx   = sales_detail.get("recent_tx", [])

    # ── RAG (coaching seulement) ─────────────────────────────────────────────
    rag_scripts, rag_relevant = [], False
    if mode == "coaching" or qtype in ("script","objection","closing","upsell","forfait","objectif"):
        rag_query = f"{message} {qtype} vente telecom Ooredoo"
        if gap > target * 0.4:
            rag_query += " gap critique urgent"
        rag_scripts, rag_relevant = _rag_search(rag_query, hour, top_k=2)

    # ── Prompt ──────────────────────────────────────────────────────────────
    situation_block = _build_situation(
        advisor_name=advisor_name, store_id=store_id, hour=hour,
        ca=ca, target=target, perf=perf, gap=gap, hours_left=hours_left,
        urgency=urgency_ctx, weather=weather_ctx, cause=cause_ctx,
        mode=mode, qtype=qtype,
        actions=actions_ctx, rag_scripts=rag_scripts,
        top_sellers=top_sellers, recent_tx=recent_tx,
        stock_data=stock_data,
    )
    user_message = f"{situation_block}\n\nQUESTION DU CONSEILLER : {message}"

    # Paramètres LLM selon intent
    if qtype in ("script", "objectif"):
        max_tokens, temp = 450, 0.17
    elif mode in ("inventory",):
        max_tokens, temp = 380, 0.20
    elif urgency_ctx in ("HIGH", "CRITICAL"):
        max_tokens, temp = 280, 0.17
    else:
        max_tokens, temp = 350, 0.22

    # ── Tentative 1 : OpenRouter full ───────────────────────────────────────
    reply, llm_ms = await _call_openrouter(
        _SYSTEM_PERSONA, user_message, max_tokens, temp, day_history
    )
    model_used = OPENROUTER_MODEL if _is_valid_reply(reply) else ""

    # ── Tentative 2 : OpenRouter stripped (contexte minimal) ────────────────
    if not _is_valid_reply(reply):
        logger.warning("[v10] Attempt 1 failed (%.0fms) — retry stripped", llm_ms)
        await asyncio.sleep(0.6 + random.uniform(0, 0.8))  # backoff aléatoire

        min_user = (
            f"SITUATION {advisor_name} | {store_id} | {hour}h | {urgency_ctx}\n"
            f"CA : {ca:,.0f}/{target:,.0f} TND ({perf:.0f}%) | Gap : {gap:,.0f} TND | {hours_left}h\n"
            f"QUESTION : {message}"
        )
        reply, llm_ms2 = await _call_openrouter(
            _SYSTEM_PERSONA, min_user, min(max_tokens, 260), temp, []
        )
        if _is_valid_reply(reply):
            model_used = f"{OPENROUTER_MODEL}+stripped"
        else:
            # ── Tentative 3 : Ollama local ───────────────────────────────────
            logger.warning("[v10] Attempt 2 failed — trying Ollama")
            reply = await _call_ollama_chat(_SYSTEM_PERSONA, min_user, min(max_tokens, 220))
            model_used = "ollama" if _is_valid_reply(reply) else ""

    # ── Fallback intent (last resort) ────────────────────────────────────────
    if not _is_valid_reply(reply):
        reply = _intent_fallback(
            mode=mode, qtype=qtype,
            advisor_name=advisor_name,
            ca=ca, target=target, perf=perf, gap=gap,
            hours_left=hours_left, urgency=urgency_ctx,
            actions=actions_ctx, top_sellers=top_sellers,
            stock_alerts=stock_data.get("alerts", []),
        )
        model_used = "intent_fallback"

    # ── Cache + confidence ───────────────────────────────────────────────────
    _cache_set(advisor_name, store_id, message, reply)

    n_active = sum([
        ca > 0,
        bool(top_sellers),
        bool(actions_ctx),
        stock_data.get("stats", {}).get("total", 0) > 0,
        rag_relevant,
        bool(day_history),
    ])
    confidence = min(0.95, 0.70 + n_active * 0.04)
    if model_used == "intent_fallback": confidence = min(confidence, 0.55)
    elif model_used == "ollama":        confidence = min(confidence, 0.68)

    total_ms = (time.time() - t0) * 1000

    # ── Traces async (non-bloquant) ──────────────────────────────────────────
    async def _persist():
        try:
            from langfuse_observer import trace_coach_chat
            trace_coach_chat(
                store_id=store_id, advisor_name=advisor_name,
                message=message, response=reply,
                question_type=f"{domain}/{mode}/{qtype}",
                rag_used=rag_relevant, nb_rag_scripts=len(rag_scripts),
                confidence=confidence, latency_ms=total_ms,
                gap_pct=round(gap/target*100,1) if target else 0,
                urgency=urgency_ctx,
            )
        except Exception:
            pass
        try:
            from sales_module.modules.coaching.agents.coach.tools import save_interaction
            save_interaction(
                advisor_name=advisor_name, store_id=store_id,
                message=message, response=reply,
                gap_pct=round(gap/target*100, 1) if target else 0,
                urgency=urgency_ctx, rag_used=rag_relevant,
                nb_rag_scripts=len(rag_scripts),
                conseil_type=f"{domain}/{qtype}", confidence=confidence,
            )
        except Exception:
            pass

    asyncio.create_task(_persist())

    logger.info(
        "[v10] %s/%s | %s | model=%s | RAG=%s | conf=%.2f | %.0fms",
        mode, qtype, advisor_name, model_used,
        "Y" if rag_relevant else "N", confidence, total_ms,
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
            {"id": "stock",    "label": "Agent Stock",  "active": stock_data.get("stats",{}).get("total",0) > 0},
            {"id": "rag",      "label": "RAG",          "active": rag_relevant},
            {"id": "history",  "label": "Historique",   "active": bool(day_history)},
        ],
        "context_used": {
            "advisor":     advisor_name,
            "store_id":    store_id,
            "hour":        hour,
            "domain":      domain,
            "mode":        mode,
            "ca_today":    ca,
            "target":      target,
            "performance": perf,
            "gap_tnd":     gap,
            "nb_tx":       nb_tx,
            "urgency":     urgency_ctx,
            "ruptures":    stock_data.get("stats", {}).get("ruptures", 0),
        },
        "rag_scripts": [
            {"categorie": s["categorie"], "action": s["action"], "score": s["score"]}
            for s in rag_scripts[:2]
        ],
        "inventory_alerts": [
            {"product": a["nom"], "qty": a["qty"], "level": a["level"], "jours": a["jours"]}
            for a in stock_data.get("alerts", [])[:3]
        ],
        "agent_recos": stock_data.get("agent_recos", [])[:3],
    })


# ══════════════════════════════════════════════════════════════════════════════
# 8. HEALTH
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/health")
async def coach_health():
    return JSONResponse({
        "status":        "ok",
        "version":       "10.0.0",
        "architecture":  "claude-like persona-first lean-prompts",
        "features": [
            "persona_first_prompting",       # identité > règles
            "few_shot_in_system_prompt",     # 6 exemples de réponses parfaites
            "lean_context_selection",        # ~550 tokens total vs 2000+ avant
            "intent_selective_context",      # seules les données utiles à CETTE question
            "response_dedup_cache_20s",      # évite doublons sur double envoi
            "greeting_fast_path_no_llm",     # < 5ms pour les salutations
            "off_topic_guard",               # garde hors vente/stock
            "retry_chain_3_levels",          # OR full → OR stripped → Ollama → fallback
            "ollama_chat_fallback",          # llama3/mistral/gemma local
            "intent_aware_fallback",         # jamais d'alerte stock pour "bonjour"
            "response_validator",            # detect anglais / trop court / JSON
            "async_persist_nonblocking",     # asyncio.create_task pour traces
            "parallel_context_loading",      # asyncio.gather
            "selective_stock_loading",       # stock chargé seulement si pertinent
        ],
        "llm_primary":   OPENROUTER_MODEL,
        "llm_fallback":  "ollama_local_chat_model",
        "llm_available": bool(OPENROUTER_KEY),
        "prompt_tokens_approx": {
            "system_persona": 320,
            "situation_block": "100-200 (intent-selective)",
            "total_per_call":  "420-520",
        },
    })
