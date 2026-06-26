"""
coach_chat.py — Coach Chat Multi-Agent v8.0 (Production)
==========================================================
Vrai chatbot interactif avec :
  - Comprehension reelle de la question (pas de pitch automatique)
  - RAG conditionnel : utilise SEULEMENT si pertinent, transparent sur l'usage
  - Anti-hallucination : chiffres reels uniquement, refuse d'inventer
  - Mode conversationnel pour les questions generales
  - Mode coaching pour les demandes explicites (script, objection, closing...)
  - Routing auto Sales / Inventory

OpenRouter (gpt-oss-120b:free) — Ollama = embeddings RAG uniquement.
"""

import asyncio
import json
import logging
import os
import re
import time
import requests
from datetime import datetime
from typing import Optional  # noqa: F401

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/coach", tags=["coach"])

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

OLLAMA_URL       = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OPENROUTER_KEY   = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")
OPENROUTER_URL   = "https://openrouter.ai/api/v1/chat/completions"
MILVUS_URI       = "http://localhost:19530"
COLLECTION       = "coaching_scripts"
EMBED_DIM        = 768

DB_CFG = {
    "host":     os.getenv("POSTGRES_HOST", os.getenv("DB_HOST", "localhost")),
    "port":     int(os.getenv("POSTGRES_PORT", os.getenv("DB_PORT", "5432"))),
    "dbname":   os.getenv("POSTGRES_DB", os.getenv("DB_NAME", "ooredoo_sales")),
    "user":     os.getenv("POSTGRES_USER", os.getenv("DB_USER", "postgres")),
    "password": os.getenv("POSTGRES_PASSWORD", os.getenv("DB_PASSWORD", "admin")),
}

# Mapping des alias store_id envoyés par le frontend → store_id réel en DB
_STORE_MAP = {
    "store-lac2": "I63",
    "OOR_LAC_01": "I63",
    "lac2":       "I63",
    "store-menzah":  "M01",
    "OOR_MENZAH_02": "M01",
    "store-sfax":    "S01",
    "OOR_SFAX_03":   "S01",
}

def _normalize_store(store_id: str) -> str:
    return _STORE_MAP.get(store_id, store_id) if store_id else "I63"

_catalog_cache: str = ""
_catalog_cache_ts: float = 0.0
_CATALOG_TTL = 600.0  # 10 minutes


def _load_catalog_from_pg(store_id: str) -> str:
    """
    Charge le catalogue produits réels depuis sales.produits + inventory.stock_levels.
    Filtre : produits avec stock > 0 dans la boutique, prix_ttc > 0.
    Retourne une chaîne texte compacte utilisable dans le prompt.
    """
    import psycopg2, psycopg2.extras, time as _t
    global _catalog_cache, _catalog_cache_ts
    if _catalog_cache and (_t.time() - _catalog_cache_ts) < _CATALOG_TTL:
        return _catalog_cache
    try:
        conn = psycopg2.connect(
            host=DB_CFG["host"], port=DB_CFG["port"],
            dbname=DB_CFG["dbname"], user=DB_CFG["user"],
            password=DB_CFG["password"], connect_timeout=6,
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            # Terminaux en stock avec prix
            cur.execute("""
                SELECT p.nom, p.prix_ttc, p.marque,
                       COALESCE(sl.quantity_available, sl.quantity, 0) AS qty
                FROM sales.produits p
                JOIN inventory.stock_levels sl ON sl.sku = p.sku AND sl.store_id = %s
                WHERE p.flag_terminal = TRUE AND p.prix_ttc > 0
                  AND COALESCE(sl.quantity_available, sl.quantity, 0) > 0
                ORDER BY p.prix_ttc DESC
                LIMIT 8
            """, (store_id,))
            terminals = cur.fetchall()

            # Forfaits actifs
            cur.execute("""
                SELECT p.nom, p.prix_ttc
                FROM sales.produits p
                WHERE p.flag_forfait = TRUE AND p.prix_ttc > 0
                  AND (p.date_eol IS NULL OR p.date_eol >= CURRENT_DATE)
                ORDER BY p.prix_ttc ASC
                LIMIT 6
            """)
            forfaits = cur.fetchall()

            # Accessoires en stock
            cur.execute("""
                SELECT p.nom, p.prix_ttc
                FROM sales.produits p
                JOIN inventory.stock_levels sl ON sl.sku = p.sku AND sl.store_id = %s
                WHERE p.categorie = '70' AND p.prix_ttc > 0
                  AND COALESCE(sl.quantity_available, sl.quantity, 0) > 0
                ORDER BY p.prix_ttc DESC
                LIMIT 4
            """, (store_id,))
            accessoires = cur.fetchall()
        finally:
            cur.close(); conn.close()

        lines = []
        if terminals:
            lines.append("TERMINAUX EN STOCK :")
            for r in terminals:
                lines.append(f"  {r['nom']} — {r['prix_ttc']:.0f} TND (stock: {r['qty']})")
        if forfaits:
            lines.append("FORFAITS :")
            for r in forfaits:
                lines.append(f"  {r['nom']} — {r['prix_ttc']:.0f} TND/mois")
        if accessoires:
            lines.append("ACCESSOIRES :")
            for r in accessoires:
                lines.append(f"  {r['nom']} — {r['prix_ttc']:.0f} TND")

        result = "\n".join(lines) if lines else "Catalogue non disponible."
        _catalog_cache = result
        _catalog_cache_ts = _t.time()
        return result

    except Exception as e:
        logger.warning(f"[COACH CATALOG] {str(e)[:80]}")
        return "Catalogue temporairement indisponible — utilise les prix habituels Ooredoo."


# ══════════════════════════════════════════════════════════════════════════════
# 1. INTENT CLASSIFICATION — Comprendre VRAIMENT ce que veut l'utilisateur
# ══════════════════════════════════════════════════════════════════════════════

# Mode coaching = l'utilisateur demande EXPLICITEMENT un outil de vente
COACHING_INTENTS = {
    "script":    ["script", "comment vendre", "que dire", "pitch", "argumentaire",
                  "donne-moi un script", "comment presenter", "discours de vente"],
    "objection": ["objection", "trop cher", "il refuse", "pas besoin", "concurrent",
                  "reticent", "comment repondre", "il dit que", "elle dit que"],
    "closing":   ["closing", "comment closer", "finaliser", "il hesite", "elle hesite",
                  "indecis", "comment convaincre", "signature"],
    "upsell":    ["upsell", "cross-sell", "ajouter", "bundle", "vient d'acheter",
                  "apres la vente", "complementaire"],
    "forfait":   ["convertir", "migration forfait", "recharge vers forfait",
                  "passer en 5g", "changer de forfait"],
    "objectif":  ["atteindre objectif", "combler le gap", "rattraper", "plan d'action",
                  "comment faire pour atteindre"],
    "meteo":     ["strategie meteo", "il pleut que faire", "adapter a la meteo",
                  "profiter du temps"],
}

# Mode conversationnel = question ouverte, demande d'info, discussion
CONVERSATION_SIGNALS = [
    "comment", "c'est quoi", "explique", "qu'est-ce", "pourquoi",
    "je voulais savoir", "peux-tu", "dis-moi", "raconte",
    "deroulement", "processus", "etape", "procedure", "fonctionnement",
    "aide", "conseil", "avis", "quoi faire", "que penses",
    "bonjour", "bonsoir", "salut", "hello", "hey",
    "merci", "ok", "d'accord", "compris", "oui", "non",
]

# Mode inventory
INVENTORY_SIGNALS = [
    "stock", "rupture", "inventaire", "quantite", "disponible",
    "epuise", "combien reste", "en stock", "reapprovisionner",
    "commande", "livraison", "reorder", "rotation", "dormant",
    "best-seller", "top ventes",
]

# ── Salutations pures (fast path sans LLM) ──────────────────────────────────
_PURE_GREETINGS = {
    "bonjour", "bonsoir", "salut", "hello", "hey", "coucou",
    "bjr", "bj", "slt", "hi", "yo", "wesh",
    "bonjour coach", "salut coach", "bonsoir coach", "hey coach",
    "bonjour !", "salut !", "hello !", "coucou !", "bonsoir !",
}


def _is_pure_greeting(msg: str) -> bool:
    """Retourne True si le message est UNIQUEMENT une salutation (≤ 3 mots)."""
    norm = msg.strip().lower().rstrip("!?,.")
    for a, b in [("é","e"),("è","e"),("ê","e"),("à","a"),("ç","c"),("ù","u")]:
        norm = norm.replace(a, b)
    if norm in _PURE_GREETINGS:
        return True
    words = norm.split()
    return len(words) <= 2 and words[0] in _PURE_GREETINGS


def _classify_intent(message: str) -> dict:
    """Classifie le message en mode + domain + type avec score de confiance."""
    msg = message.lower()
    for a, b in [("e\u0301","e"),("e\u0300","e"),("\u00e9","e"),("\u00e8","e"),
                 ("\u00ea","e"),("\u00e0","a"),("\u00f4","o"),("\u00ee","i"),
                 ("\u00e7","c"),("\u00f9","u")]:
        msg = msg.replace(a, b)

    # 0a. Salutation pure \u2014 fast path sans LLM
    if _is_pure_greeting(msg):
        return {"mode": "greeting", "domain": "sales", "type": "greeting", "confidence": 0.99}

    # 0. Check OFF-TOPIC — sujets sans rapport avec telecom/vente/stock
    OFF_TOPIC_SIGNALS = [
        "cybersecurite", "crypto", "bitcoin", "blockchain", "politique",
        "recette", "cuisine", "football", "match", "film", "serie",
        "jeu video", "gaming", "musique", "chanson", "philosophie",
        "mathematique", "physique", "chimie", "histoire", "geographie",
        "medecine", "sante", "religion", "horoscope", "astrologie",
        "blague", "raconte", "poeme", "code python", "javascript",
        "programmation", "intelligence artificielle", "machine learning",
        "deep learning", "chatgpt", "openai", "google", "apple inc",
        "bourse", "trading", "investissement", "immobilier",
    ]
    # Mots-cles telecom qui NEUTRALISENT le off-topic
    TELECOM_ANCHORS = [
        "ooredoo", "vente", "client", "boutique", "forfait", "stock",
        "objectif", "conseiller", "terminal", "iphone", "samsung",
        "fibre", "5g", "assurance", "bundle", "coach", "strategie",
        "produit", "prix", "offre", "promotion", "recharge", "sim",
    ]
    off_hits = sum(1 for kw in OFF_TOPIC_SIGNALS if kw in msg)
    telecom_hits = sum(1 for kw in TELECOM_ANCHORS if kw in msg)
    if off_hits >= 1 and telecom_hits == 0:
        return {"mode": "off_topic", "domain": "none",
                "type": "off_topic", "confidence": 0.95}

    # 1. Check inventory
    inv_hits = sum(1 for kw in INVENTORY_SIGNALS if kw in msg)
    if inv_hits >= 1:
        inv_type = "stock"
        for kw in ["reappro", "commande", "livraison", "reorder"]:
            if kw in msg: inv_type = "reorder"; break
        for kw in ["rotation", "dormant", "best-seller", "top ventes"]:
            if kw in msg: inv_type = "rotation"; break
        for kw in ["rupture", "alerte", "critique", "danger"]:
            if kw in msg: inv_type = "alerte"; break
        return {"mode": "inventory", "domain": "inventory",
                "type": inv_type, "confidence": min(0.95, 0.6 + inv_hits * 0.15)}

    # 2. Check coaching (explicit sales tool request)
    best_coaching_type = None
    best_coaching_score = 0
    for qtype, keywords in COACHING_INTENTS.items():
        hits = sum(1 for kw in keywords if kw in msg)
        if hits > best_coaching_score:
            best_coaching_score = hits
            best_coaching_type = qtype

    # 3. Check recap/bilan — question informative sur la journée (PAS un script de vente)
    RECAP_SIGNALS = [
        "parle moi", "raconte", "resume", "bilan", "comment vont",
        "quelles sont les ventes", "ventes d aujourd", "ventes du jour",
        "ca du jour", "chiffre du jour", "ou en sont", "etat des ventes",
        "montre moi", "dis moi les ventes", "qu est ce qui se passe",
        "qu est ce qui se vend", "comment se passe", "comment ca se passe",
        "bilan du jour", "resultat", "performance du jour", "comment ca va",
    ]
    recap_hits = sum(1 for kw in RECAP_SIGNALS if kw in msg)
    if recap_hits >= 1 and best_coaching_score == 0:
        return {"mode": "conversation", "domain": "sales",
                "type": "recap", "confidence": min(0.92, 0.7 + recap_hits * 0.1)}

    # 4. Check conversation signals
    conv_hits = sum(1 for kw in CONVERSATION_SIGNALS if kw in msg)

    # 5. Decision
    if best_coaching_score >= 2:
        return {"mode": "coaching", "domain": "sales",
                "type": best_coaching_type, "confidence": min(0.95, 0.6 + best_coaching_score * 0.15)}
    elif best_coaching_score == 1 and conv_hits == 0:
        return {"mode": "coaching", "domain": "sales",
                "type": best_coaching_type, "confidence": 0.70}
    elif conv_hits >= 1 or best_coaching_score == 0:
        return {"mode": "conversation", "domain": "sales",
                "type": "general", "confidence": 0.80}
    else:
        return {"mode": "conversation", "domain": "sales",
                "type": "general", "confidence": 0.60}


# ══════════════════════════════════════════════════════════════════════════════
# 2. CONTEXT LOADERS
# ══════════════════════════════════════════════════════════════════════════════

def _load_sales_detail_sync(store_id: str) -> dict:
    """Charge top-vendeurs + dernières transactions depuis la DB (psycopg2)."""
    import psycopg2, psycopg2.extras
    result = {"top_sellers": [], "recent_transactions": []}
    try:
        conn = psycopg2.connect(
            host=DB_CFG["host"], port=DB_CFG["port"],
            dbname=DB_CFG["dbname"], user=DB_CFG["user"],
            password=DB_CFG["password"], connect_timeout=6,
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            # Top 6 produits sur les 7 derniers jours de données
            cur.execute("""
                SELECT t.sku,
                       COALESCE(p.nom, t.sku::text) AS nom,
                       SUM(t.quantity)               AS qty,
                       SUM(t.lig_ttc)                AS ca
                FROM sales.transactions t
                LEFT JOIN sales.produits p ON p.sku = t.sku
                WHERE t.store_id = %s
                  AND t.date_only >= (
                      SELECT MAX(date_only) FROM sales.transactions WHERE store_id = %s
                  ) - INTERVAL '7 days'
                GROUP BY t.sku, p.nom
                ORDER BY ca DESC
                LIMIT 15
            """, (store_id, store_id))
            result["top_sellers"] = [
                {"sku": str(r["sku"]), "nom": r["nom"],
                 "qty": int(r["qty"] or 0), "ca": float(r["ca"] or 0)}
                for r in cur.fetchall()
            ]

            # 8 dernières transactions (jour le plus récent)
            cur.execute("""
                SELECT COALESCE(p.nom, t.sku::text) AS nom,
                       t.quantity, t.lig_ttc, t.heure, t.date_only
                FROM sales.transactions t
                LEFT JOIN sales.produits p ON p.sku = t.sku
                WHERE t.store_id = %s
                ORDER BY t.date_only DESC, t.heure DESC
                LIMIT 15
            """, (store_id,))
            result["recent_transactions"] = [
                {"nom": r["nom"], "qty": int(r["quantity"] or 1),
                 "ttc": float(r["lig_ttc"] or 0), "heure": int(r["heure"] or 0),
                 "date": str(r["date_only"])}
                for r in cur.fetchall()
            ]
            logger.info(f"[COACH SALES] detail ok — top={len(result['top_sellers'])} recent={len(result['recent_transactions'])}")
        finally:
            cur.close(); conn.close()
    except Exception as e:
        logger.warning(f"[COACH SALES DETAIL] {str(e)[:80]}")
    return result


async def _load_sales_context(store_id: str, ctx: dict) -> dict:
    now = datetime.now()
    hour = now.hour
    hours_left = max(1, 20 - hour)

    ca_today = float(ctx.get("current_revenue") or ctx.get("ca_today") or 0)
    ca_target = float(ctx.get("daily_target") or ctx.get("ca_target") or 1007)
    gap_pct = float(ctx.get("gap_pct") or ctx.get("gap_objectif") or 0)
    urgency = str(ctx.get("urgency") or ctx.get("urgency_level") or "MEDIUM").upper()
    weather = str(ctx.get("weather") or "Tunis")
    nb_ventes = int(ctx.get("nb_ventes") or 0)

    if ca_today == 0:
        pg = await _query_pos_pg(store_id)
        ca_today = pg.get("ca_total", 0)
        nb_ventes = nb_ventes or pg.get("nb_transactions", 0)

    # Charger le détail produits en parallèle
    detail = await asyncio.get_event_loop().run_in_executor(
        None, _load_sales_detail_sync, store_id
    )

    gap_tnd = max(0.0, ca_target - ca_today)
    perf = round((ca_today / ca_target * 100), 1) if ca_target > 0 else 0.0
    if gap_pct == 0 and ca_target > 0:
        gap_pct = round((gap_tnd / ca_target) * 100, 1)

    # ── Réconciliation nb_ventes vs CA ────────────────────────────────────────
    # Si le contexte frontend dit nb_ventes=0 mais CA>0 (données historiques DB),
    # on recalcule nb_ventes depuis les transactions réelles pour éviter la contradiction.
    if nb_ventes == 0 and ca_today > 0 and detail.get("recent_transactions"):
        most_recent_date = detail["recent_transactions"][0].get("date", "")
        if most_recent_date:
            nb_ventes = sum(1 for t in detail["recent_transactions"]
                            if t.get("date") == most_recent_date)
        nb_ventes = nb_ventes or len(detail["recent_transactions"])

    # ── Outputs Agent Stratège ────────────────────────────────────────────────
    actions = ctx.get("strategie_actions") or []
    actions_txt = "\n".join(
        f"  {a.get('priorite', i+1)}. {a.get('action', '')} → {a.get('produit_cible', '')}"
        f" | Argument : {a.get('argument_vente', '')} | Impact : {a.get('impact_estime', '')}"
        for i, a in enumerate(actions[:4])
    ) if actions else ""

    # ── Outputs Agent Analyste (depuis ctx frontend) ───────────────────────────
    analyst_summary = str(ctx.get("analyst_summary") or "")
    forecast_eod    = float(ctx.get("forecast_eod") or 0)
    focus_produits  = ctx.get("focus_produits") or []

    # ── Output Context/Sentinel Agent (météo, events, promotions) ─────────────
    context_report = ctx.get("context_report") or {}

    return {
        "ca_today": ca_today, "ca_target": ca_target,
        "gap_pct": gap_pct, "gap_tnd": gap_tnd,
        "performance": perf, "nb_ventes": nb_ventes,
        "urgency": urgency, "weather": weather,
        "hours_left": hours_left, "hour": hour,
        "actions": actions, "actions_txt": actions_txt,
        "cause_racine": ctx.get("cause_racine", ""),
        "focus_produits": focus_produits,
        "analyst_summary": analyst_summary,
        "forecast_eod": forecast_eod,
        "context_report": context_report,
        "top_sellers": detail["top_sellers"],
        "recent_transactions": detail["recent_transactions"],
    }


def _load_inventory_context_sync(store_id: str) -> dict:
    """
    Charge le contexte stock complet depuis PostgreSQL :
    - inventory.stock_levels       → niveaux et alertes
    - inventory.recommendations    → décisions des agents stock (ORDER/EXPEDITE)
    - inventory.alerts             → alertes actives générées par les agents
    - supply.reorder_params        → EOQ, point commande, jours avant rupture
    - agent_kpi_daily              → KPI du jour et tendance 7j
    - telco_targets_monthly        → objectif mensuel boutique
    - sales.transactions           → top vendeurs 7j avec vélocité
    """
    import psycopg2, psycopg2.extras
    try:
        conn = psycopg2.connect(
            host=DB_CFG["host"], port=DB_CFG["port"],
            dbname=DB_CFG["dbname"], user=DB_CFG["user"],
            password=DB_CFG["password"], connect_timeout=8,
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            # ── 1. Stats globales stock ────────────────────────────────────
            cur.execute("""
                SELECT COUNT(*) AS total,
                    SUM(CASE WHEN COALESCE(quantity_available, quantity, 0) <= 0  THEN 1 ELSE 0 END) AS ruptures,
                    SUM(CASE WHEN COALESCE(quantity_available, quantity, 0) BETWEEN 1 AND 5 THEN 1 ELSE 0 END) AS critiques,
                    SUM(CASE WHEN COALESCE(quantity_available, quantity, 0) BETWEEN 6 AND 15 THEN 1 ELSE 0 END) AS warnings,
                    SUM(CASE WHEN COALESCE(quantity_available, quantity, 0) > 15  THEN 1 ELSE 0 END) AS ok_count,
                    AVG(COALESCE(quantity_available, quantity, 0))::NUMERIC(10,1) AS avg_stock
                FROM inventory.stock_levels
                WHERE store_id = %s
            """, (store_id,))
            stats = cur.fetchone()

            # ── 2. Produits en alerte avec données enrichies ───────────────
            cur.execute("""
                SELECT sl.sku, COALESCE(p.nom, sl.sku::text) AS product_name,
                       COALESCE(p.categorie, 'Autre') AS categorie,
                       COALESCE(p.prix_ttc, 0) AS prix_ttc,
                       COALESCE(sl.quantity_available, sl.quantity, 0) AS stock_qty,
                       CASE
                           WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 0  THEN 'rupture'
                           WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 5  THEN 'critical'
                           ELSE 'warning'
                       END AS risk_level,
                       rp.point_commande, rp.eoq, rp.stock_securite,
                       rp.demande_moy_jour
                FROM inventory.stock_levels sl
                LEFT JOIN sales.produits p ON p.sku = sl.sku
                LEFT JOIN supply.reorder_params rp ON rp.sku = sl.sku AND rp.store_id = sl.store_id
                WHERE sl.store_id = %s
                  AND COALESCE(sl.quantity_available, sl.quantity, 0) <= 15
                ORDER BY COALESCE(sl.quantity_available, sl.quantity, 0) ASC
                LIMIT 20
            """, (store_id,))
            alert_rows = cur.fetchall()

            # ── 3. Produits disponibles (stock > 15) ───────────────────────
            cur.execute("""
                SELECT sl.sku, COALESCE(p.nom, sl.sku::text) AS product_name,
                       COALESCE(p.categorie, 'Autre') AS categorie,
                       COALESCE(sl.quantity_available, sl.quantity, 0) AS stock_qty,
                       COALESCE(p.prix_ttc, 0) AS prix_ttc
                FROM inventory.stock_levels sl
                LEFT JOIN sales.produits p ON p.sku = sl.sku
                WHERE sl.store_id = %s
                  AND COALESCE(sl.quantity_available, sl.quantity, 0) > 15
                ORDER BY COALESCE(sl.quantity_available, sl.quantity, 0) DESC
                LIMIT 15
            """, (store_id,))
            ok_rows = cur.fetchall()

            # ── 4. Recommandations agents stock actives ────────────────────
            cur.execute("""
                SELECT r.sku, COALESCE(p.nom, r.sku::text) AS product_name,
                       r.recommendation_type, r.suggested_quantity AS recommended_qty,
                       r.recommendation_text, r.confidence,
                       r.created_at
                FROM inventory.recommendations r
                LEFT JOIN sales.produits p ON p.sku = r.sku
                WHERE r.store_id = %s
                  AND r.status IN ('pending', 'approved')
                  AND r.created_at >= CURRENT_TIMESTAMP - INTERVAL '48 hours'
                ORDER BY r.created_at DESC
                LIMIT 10
            """, (store_id,))
            reco_rows = cur.fetchall()

            # ── 5. Alertes actives agents ──────────────────────────────────
            cur.execute("""
                SELECT a.sku, COALESCE(p.nom, a.sku::text) AS product_name,
                       a.alert_type, a.severity, a.message,
                       a.recommended_action, a.created_at
                FROM inventory.alerts a
                LEFT JOIN sales.produits p ON p.sku = a.sku
                WHERE a.store_id = %s
                  AND a.status = 'active'
                  AND a.severity IN ('critical', 'high', 'medium')
                ORDER BY
                    CASE a.severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 ELSE 3 END,
                    a.created_at DESC
                LIMIT 10
            """, (store_id,))
            alert_agent_rows = cur.fetchall()

            # ── 6. Top vendeurs 7j avec vélocité et jours avant rupture ────
            cur.execute("""
                SELECT t.sku, COALESCE(p.nom, t.sku::text) AS nom,
                       SUM(t.quantity) AS total_sold,
                       COALESCE(sl.quantity_available, sl.quantity, 0) AS stock_restant,
                       COALESCE(p.prix_ttc, 0) AS prix_ttc,
                       COALESCE(rp.demande_moy_jour, SUM(t.quantity)::NUMERIC / 7) AS vel_jour,
                       rp.point_commande
                FROM sales.transactions t
                LEFT JOIN sales.produits p ON p.sku = t.sku
                LEFT JOIN inventory.stock_levels sl ON sl.sku = t.sku AND sl.store_id = t.store_id
                LEFT JOIN supply.reorder_params rp ON rp.sku = t.sku AND rp.store_id = t.store_id
                WHERE t.store_id = %s
                  AND t.date_only >= (
                      SELECT MAX(date_only) FROM sales.transactions WHERE store_id = %s
                  ) - INTERVAL '7 days'
                GROUP BY t.sku, p.nom, sl.quantity_available, sl.quantity,
                         p.prix_ttc, rp.demande_moy_jour, rp.point_commande
                ORDER BY total_sold DESC
                LIMIT 10
            """, (store_id, store_id))
            top_rows = cur.fetchall()

            # ── 7. KPI du jour + tendance 7j depuis agent_kpi_daily ────────
            cur.execute("""
                SELECT SUM(ca_realise) AS ca_jour, SUM(nb_transactions) AS nb_tx,
                       SUM(nb_forfaits) AS forfaits, SUM(nb_terminaux) AS terminaux,
                       SUM(nb_postpaye) AS postpaye, SUM(nb_recharges) AS recharges,
                       COUNT(DISTINCT agent_id) AS nb_agents,
                       AVG(ca_realise) AS ca_moy_agent
                FROM agent_kpi_daily
                WHERE store_id = %s AND kpi_date = (
                    SELECT MAX(kpi_date) FROM agent_kpi_daily WHERE store_id = %s
                )
            """, (store_id, store_id))
            kpi_today = cur.fetchone()

            cur.execute("""
                SELECT SUM(ca_realise) / NULLIF(COUNT(DISTINCT kpi_date), 0) AS ca_7j_avg,
                       SUM(nb_terminaux) AS term_7j, SUM(nb_forfaits) AS forc_7j,
                       SUM(nb_postpaye) AS postpaye_7j
                FROM agent_kpi_daily
                WHERE store_id = %s
                  AND kpi_date >= CURRENT_DATE - INTERVAL '7 days'
            """, (store_id,))
            kpi_7j = cur.fetchone()

            # ── 8. Objectif mensuel boutique ────────────────────────────────
            cur.execute("""
                SELECT ca_cible_mensuel, activations_totales,
                       ventes_terminaux, facteur_saisonnier
                FROM telco_targets_monthly
                WHERE store_id = %s
                  AND mois = EXTRACT(MONTH FROM CURRENT_DATE)::INTEGER
                  AND annee = EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER
                  AND niveau = 'BOUTIQUE'
                LIMIT 1
            """, (store_id,))
            target_row = cur.fetchone()

        finally:
            cur.close(); conn.close()

        # ── Construction résultats ─────────────────────────────────────────
        alert_items = []
        for r in alert_rows:
            stock = int(r["stock_qty"] or 0)
            vel = float(r["demande_moy_jour"] or 0.5)
            jours_rupture = round(stock / vel, 1) if vel > 0 and stock > 0 else 0
            alert_items.append({
                "sku":           str(r["sku"]),
                "product_name":  r["product_name"],
                "categorie":     r["categorie"],
                "prix_ttc":      float(r["prix_ttc"] or 0),
                "stock_qty":     stock,
                "risk_level":    r["risk_level"],
                "jours_rupture": jours_rupture,
                "point_commande": int(r["point_commande"] or 0),
                "eoq":           int(r["eoq"] or 0),
            })

        ok_items = [
            {"sku": str(r["sku"]), "product_name": r["product_name"],
             "categorie": r["categorie"], "stock_qty": int(r["stock_qty"] or 0),
             "prix_ttc": float(r["prix_ttc"] or 0)}
            for r in ok_rows
        ]

        recos = [
            {"sku": str(r["sku"]), "product_name": r["product_name"],
             "type": r["recommendation_type"],
             "qty": int(r["recommended_qty"] or 0),
             "texte": (r["recommendation_text"] or "")[:120],
             "confiance": float(r["confidence"] or 0)}
            for r in reco_rows
        ]

        agent_alerts = [
            {"sku": str(r["sku"]), "product_name": r["product_name"],
             "type": r["alert_type"], "severite": r["severity"],
             "message": (r["message"] or "")[:100],
             "action": (r["recommended_action"] or "")[:80]}
            for r in alert_agent_rows
        ]

        top_sellers = []
        for r in top_rows:
            vel = float(r["vel_jour"] or 0)
            stock = int(r["stock_restant"] or 0)
            jours = round(stock / vel, 1) if vel > 0 else 999
            top_sellers.append({
                "sku":          str(r["sku"]),
                "nom":          r["nom"],
                "total_sold":   int(r["total_sold"] or 0),
                "stock_restant": stock,
                "prix_ttc":     float(r["prix_ttc"] or 0),
                "vel_jour":     round(vel, 2),
                "jours_rupture": jours,
                "sous_point_commande": stock <= int(r["point_commande"] or 0),
            })

        kpi = {
            "ca_jour":    float(kpi_today["ca_jour"] or 0) if kpi_today else 0,
            "nb_tx":      int(kpi_today["nb_tx"] or 0) if kpi_today else 0,
            "forfaits":   int(kpi_today["forfaits"] or 0) if kpi_today else 0,
            "terminaux":  int(kpi_today["terminaux"] or 0) if kpi_today else 0,
            "postpaye":   int(kpi_today["postpaye"] or 0) if kpi_today else 0,
            "nb_agents":  int(kpi_today["nb_agents"] or 0) if kpi_today else 0,
            "ca_moy_agent": float(kpi_today["ca_moy_agent"] or 0) if kpi_today else 0,
            "ca_7j_avg":  float(kpi_7j["ca_7j_avg"] or 0) if kpi_7j else 0,
            "term_7j":    int(kpi_7j["term_7j"] or 0) if kpi_7j else 0,
            "forc_7j":    int(kpi_7j["forc_7j"] or 0) if kpi_7j else 0,
            "postpaye_7j": int(kpi_7j["postpaye_7j"] or 0) if kpi_7j else 0,
        }

        target = {
            "ca_cible_mensuel":  float(target_row["ca_cible_mensuel"] or 0) if target_row else 0,
            "activations_cible": int(target_row["activations_totales"] or 0) if target_row else 0,
            "terminaux_cible":   int(target_row["ventes_terminaux"] or 0) if target_row else 0,
            "facteur_saison":    float(target_row["facteur_saisonnier"] or 1) if target_row else 1,
        }

        logger.info(
            f"[COACH INV] store={store_id} skus={stats['total'] if stats else 0} "
            f"ruptures={stats['ruptures'] if stats else 0} "
            f"recos={len(recos)} agent_alerts={len(agent_alerts)}"
        )
        return {
            "total_skus":     int(stats["total"] or 0) if stats else 0,
            "ruptures":       int(stats["ruptures"] or 0) if stats else 0,
            "critiques":      int(stats["critiques"] or 0) if stats else 0,
            "warnings":       int(stats["warnings"] or 0) if stats else 0,
            "ok_count":       int(stats["ok_count"] or 0) if stats else 0,
            "avg_stock":      float(stats["avg_stock"] or 0) if stats else 0,
            "critical_items": [i for i in alert_items if i["risk_level"] in ("rupture", "critical")],
            "alert_items":    alert_items,
            "ok_items":       ok_items,
            "all_items":      alert_items,
            "top_sellers":    top_sellers,
            # Données agents stock (NOUVEAU)
            "agent_recos":    recos,
            "agent_alerts":   agent_alerts,
            # KPI boutique (NOUVEAU)
            "kpi":            kpi,
            "target":         target,
        }
    except Exception as e:
        logger.error(f"[COACH INV] psycopg2 error: {e}")
        return {
            "total_skus": 0, "ruptures": 0, "critiques": 0, "ok_count": 0,
            "avg_stock": 0, "critical_items": [], "all_items": [], "top_sellers": [],
            "agent_recos": [], "agent_alerts": [], "kpi": {}, "target": {},
        }


async def _load_inventory_context(store_id: str) -> dict:
    return await asyncio.get_event_loop().run_in_executor(None, _load_inventory_context_sync, store_id)


def _query_pos_pg_sync(store_id: str) -> dict:
    """Utilise MAX(date_only) au lieu de CURRENT_DATE pour fonctionner avec données simulées."""
    import psycopg2
    try:
        conn = psycopg2.connect(
            host=DB_CFG["host"], port=DB_CFG["port"],
            dbname=DB_CFG["dbname"], user=DB_CFG["user"],
            password=DB_CFG["password"], connect_timeout=5,
        )
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT ca_total, nb_transactions FROM sales.vw_ca_par_boutique "
                "WHERE store_id=%s ORDER BY date_only DESC LIMIT 1",
                (store_id,)
            )
            row = cur.fetchone()
            if row:
                return {"ca_total": float(row[0] or 0), "nb_transactions": int(row[1] or 0)}
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.warning(f"[COACH POS] {str(e)[:80]}")
    return {"ca_total": 0, "nb_transactions": 0}


async def _query_pos_pg(store_id: str) -> dict:
    return await asyncio.get_event_loop().run_in_executor(None, _query_pos_pg_sync, store_id)


# ══════════════════════════════════════════════════════════════════════════════
# 3. RAG SEARCH — Conditionnel et transparent
# ══════════════════════════════════════════════════════════════════════════════

def _ensure_coaching_collection(client) -> bool:
    try:
        if client.has_collection(COLLECTION):
            return True
        from pymilvus import DataType
        schema = client.create_schema(auto_id=True, enable_dynamic_field=True)
        schema.add_field("id",           DataType.INT64,        is_primary=True, auto_id=True)
        schema.add_field("vector",       DataType.FLOAT_VECTOR, dim=EMBED_DIM)
        schema.add_field("pg_id",        DataType.INT64)
        schema.add_field("categorie",    DataType.VARCHAR,       max_length=100)
        schema.add_field("situation",    DataType.VARCHAR,       max_length=200)
        schema.add_field("action",       DataType.VARCHAR,       max_length=200)
        schema.add_field("produit",      DataType.VARCHAR,       max_length=100)
        schema.add_field("argument",     DataType.VARCHAR,       max_length=200)
        schema.add_field("impact",       DataType.VARCHAR,       max_length=100)
        schema.add_field("heure_min",    DataType.INT64)
        schema.add_field("heure_max",    DataType.INT64)
        schema.add_field("jour_semaine", DataType.INT64)
        schema.add_field("store_id",     DataType.VARCHAR,       max_length=20)
        idx = client.prepare_index_params()
        idx.add_index("vector", index_type="FLAT", metric_type="COSINE")
        client.create_collection(COLLECTION, schema=schema, index_params=idx)
        logger.info(f"[COACH RAG] Collection '{COLLECTION}' créée")
        return True
    except Exception as e:
        logger.warning(f"[COACH RAG] ensure_collection: {e}")
        return False


def _search_rag(query: str, hour: int, top_k: int = 3,
                min_score: float = 0.35) -> tuple[list, str, bool]:
    """
    RAG conditionnel : retourne (scripts, rag_txt, is_relevant).
    is_relevant = True seulement si le meilleur score depasse min_score.
    """
    try:
        resp = requests.post(f"{OLLAMA_URL}/api/embeddings",
            json={"model": "nomic-embed-text", "prompt": query[:400]}, timeout=12)
        emb = resp.json().get("embedding", [])
        if not emb:
            return [], "", False
        if len(emb) < EMBED_DIM:
            emb += [0.0] * (EMBED_DIM - len(emb))
        emb = emb[:EMBED_DIM]

        from pymilvus import MilvusClient
        client = MilvusClient(uri=MILVUS_URI)
        _ensure_coaching_collection(client)
        results = client.search(
            collection_name=COLLECTION, data=[emb], limit=top_k + 2,
            output_fields=["categorie", "situation", "action", "produit",
                           "argument", "impact", "heure_min", "heure_max"])
        scripts = []
        for hit in results[0]:
            e = hit["entity"]
            score = float(hit["distance"])
            if int(e.get("heure_min", 0)) <= hour <= int(e.get("heure_max", 24)):
                score += 0.10
            scripts.append({
                "score": round(score, 3),
                "categorie": e.get("categorie", ""),
                "situation": e.get("situation", "")[:120],
                "action": e.get("action", "")[:120],
                "produit": e.get("produit", ""),
                "argument": e.get("argument", "")[:150],
                "impact": e.get("impact", ""),
            })
        scripts = sorted(scripts, key=lambda x: x["score"], reverse=True)[:top_k]

        if not scripts or scripts[0]["score"] < min_score:
            return scripts, "", False

        lines = []
        for i, s in enumerate(scripts, 1):
            lines.append(
                f"  #{i} [{s['categorie']}] {s['action']}\n"
                f"     Produit: {s['produit']} | Argument: {s['argument']}"
            )
        rag_txt = "SCRIPTS RAG PERTINENTS (situations similaires reussies) :\n" + "\n".join(lines)
        return scripts, rag_txt, True
    except Exception as e:
        logger.warning(f"[COACH RAG] {str(e)[:60]}")
        return [], "", False


# ══════════════════════════════════════════════════════════════════════════════
# 4. PROMPT BUILDERS — Differencies par MODE (pas juste par type)

def _format_sales_detail(sctx: dict) -> str:
    """Formate top-vendeurs + dernières transactions en texte pour le prompt."""
    lines = []

    recent = sctx.get("recent_transactions", [])
    if recent:
        r0 = recent[0]
        ttc0 = f"{r0['ttc']:,.2f} TND" if r0["ttc"] > 0 else "offert/service"
        lines.append(f"DERNIERE VENTE ENREGISTREE : {r0['heure']}h — {r0['nom']} x{r0['qty']} — {ttc0} (date: {r0['date']})")
        lines.append("TRANSACTIONS RECENTES (plus recente en premier) :")
        for r in recent[1:6]:
            ttc = f"{r['ttc']:,.2f} TND" if r["ttc"] > 0 else "offert/service"
            lines.append(f"  {r['heure']}h — {r['nom']} x{r['qty']} — {ttc}")

    top = sctx.get("top_sellers", [])
    if top:
        lines.append("TOP PRODUITS VENDUS (7 derniers jours) :")
        for i, t in enumerate(top[:6], 1):
            ca_str = f"{t['ca']:,.0f} TND" if t["ca"] > 0 else "prix nul"
            lines.append(f"  {i}. {t['nom']} — {t['qty']} vendus — {ca_str}")

    return "\n".join(lines) if lines else ""


# ══════════════════════════════════════════════════════════════════════════════
# MASTER PROMPT — Prompt unifié temps réel (LLM décide tout, zéro hardcode)
# ══════════════════════════════════════════════════════════════════════════════

def _build_master_prompt(
    advisor_name: str,
    store_id: str,
    sctx: dict,
    rag_scripts: list,
    inv_ctx: dict,
) -> str:
    """
    Prompt unique injectant TOUS les outputs agents temps réel (architecture multi-agents).
    Agents connectés : Analyste Sales, Stratège, InventoryAnalysis, InventoryDecision, RAG, Sentinel.
    Le LLM lit les données et décide seul du format et du contenu — zéro hardcode.
    """
    ca       = sctx.get("ca_today", 0)
    target   = sctx.get("ca_target", 0)
    perf     = sctx.get("performance", 0)
    gap      = sctx.get("gap_tnd", 0)
    nb_v     = sctx.get("nb_ventes", 0)
    urgency  = sctx.get("urgency", "MEDIUM")
    hl       = sctx.get("hours_left", 8)
    weather  = sctx.get("weather", "")
    cause    = sctx.get("cause_racine", "")
    actions  = sctx.get("actions", [])
    # Outputs Agent Analyste
    analyst_summary = sctx.get("analyst_summary", "")
    forecast_eod    = sctx.get("forecast_eod", 0)
    focus_produits  = sctx.get("focus_produits", [])
    # Output Sentinel/Context Agent
    ctx_report = sctx.get("context_report", {})

    # ── Verdict objectif ──────────────────────────────────────────────────────
    if perf >= 100:
        verdict = f"OBJECTIF ATTEINT ({perf:.0f}%) — excellent !"
    elif perf >= 75:
        verdict = f"{perf:.0f}% — en bonne voie"
    elif perf >= 40:
        verdict = f"{perf:.0f}% — à accélérer"
    else:
        verdict = f"{perf:.0f}% — URGENT, mobilisation totale"

    # ── Top produits vendus (Agent Analyste — source: sales.transactions) ─────
    top = sctx.get("top_sellers", [])
    top_lines = [
        f"  {i+1}. {t['nom']} — {t['qty']} vendus — {t['ca']:,.0f} TND"
        for i, t in enumerate(top[:6])
    ]
    top_txt = "\n".join(top_lines) if top_lines else "  (données en cours de chargement)"

    # ── Transactions récentes (POS temps réel) ────────────────────────────────
    recent = sctx.get("recent_transactions", [])
    recent_lines = [
        f"  {r['heure']}h — {r['nom']} x{r['qty']} — {r['ttc']:,.0f} TND  [{r['date']}]"
        for r in recent[:5]
    ]
    recent_txt = "\n".join(recent_lines) if recent_lines else "  (aucune transaction récente)"

    # ── Alertes stock critique (depuis inv_ctx.critical_items — Agent Stock) ──
    critical_items = inv_ctx.get("critical_items", []) if inv_ctx else []
    stock_alert_lines = []
    for it in critical_items[:8]:
        level = it.get("risk_level", "")
        if level == "rupture":
            stock_alert_lines.append(
                f"  ⛔ {it['product_name']} — 0 unité [RUPTURE] → ne pas promettre au client")
        else:
            jours = it.get("jours_rupture", 0)
            stock_alert_lines.append(
                f"  ⚠ {it['product_name']} — {it['stock_qty']} unité(s) [{level.upper()}]"
                f" | rupture J+{jours:.0f} → vendre en priorité")
    stock_alert_txt = "\n".join(stock_alert_lines) if stock_alert_lines else "  ✓ Aucune rupture critique détectée"

    # ── Output Agent Analyste ─────────────────────────────────────────────────
    analyste_section = ""
    if analyst_summary:
        analyste_section = f"\n  Synthèse Analyste : {analyst_summary[:200]}"
    if forecast_eod > 0:
        analyste_section += f"\n  Prévision fin de journée : {forecast_eod:,.0f} TND"
    if focus_produits:
        analyste_section += f"\n  Produits focus : {', '.join(str(p) for p in focus_produits[:5])}"

    # ── Output Context/Sentinel Agent ────────────────────────────────────────
    sentinel_section = ""
    if ctx_report:
        uplift = ctx_report.get("demand_uplift_pct", 0)
        interpretation = ctx_report.get("interpretation", "")
        dominant = ctx_report.get("dominant_signal", "")
        if interpretation:
            sentinel_section = (
                f"\nAGENT SENTINEL (contexte marché) :\n"
                f"  Signal dominant : {dominant} | Uplift demande : {uplift:+.0f}%\n"
                f"  {interpretation[:200]}"
            )

    # ── Actions Agent Stratège ────────────────────────────────────────────────
    stratege_lines = [
        f"  {a.get('priorite', i+1)}. {a.get('action', '')} → {a.get('produit_cible', '')} "
        f"| Argument : {a.get('argument_vente', '')} | Impact : {a.get('impact_estime', '')}"
        for i, a in enumerate(actions[:4])
    ]
    stratege_txt = "\n".join(stratege_lines) if stratege_lines else "  (en attente des recommandations du Stratège)"

    # ── RAG — Scripts terrain éprouvés ────────────────────────────────────────
    rag_lines = []
    for i, s in enumerate(rag_scripts[:3], 1):
        rag_lines.append(
            f"  #{i} [{s['categorie']}] Score {s['score']:.2f}\n"
            f"     Situation : {s.get('situation', '')[:100]}\n"
            f"     Action    : {s['action'][:120]}\n"
            f"     Argument  : {s.get('argument', '')[:120]}"
        )
    rag_txt = "\n".join(rag_lines) if rag_lines else ""
    rag_section = f"\nSCRIPTS TERRAIN ÉPROUVÉS (Agent RAG Milvus) :\n{rag_txt}\n" if rag_txt else ""

    # ── Inventaire complet (Agent Stock — si disponible) ──────────────────────
    inv_section = ""
    if inv_ctx:
        ruptures = inv_ctx.get("ruptures", 0)
        critiques = inv_ctx.get("critiques", 0)
        warnings = inv_ctx.get("warnings", 0)
        ok_count = inv_ctx.get("ok_count", 0)
        total_skus = inv_ctx.get("total_skus", 0)

        # Produits en alerte
        alert_lines = []
        for item in inv_ctx.get("alert_items", [])[:12]:
            icon = {"rupture": "⛔", "critical": "⚠", "warning": "~"}.get(item["risk_level"], "?")
            vel = item.get("jours_rupture", 0)
            alert_lines.append(
                f"  {icon} {item['product_name']} — {item['stock_qty']} unités"
                f" | J+{vel:.0f} rupture | EOQ recommandé : {item.get('eoq', '?')}"
            )

        # Produits OK
        ok_lines = [
            f"  ✓ {it['product_name']} — {it['stock_qty']} unités"
            for it in inv_ctx.get("ok_items", [])[:8]
        ]

        # Top vendeurs avec vélocité
        top_inv_lines = []
        for t in inv_ctx.get("top_sellers", [])[:6]:
            vel = round(t["total_sold"] / 7, 1)
            jours = round(t["stock_restant"] / vel, 1) if vel > 0 else 999
            flag = " ⚠ STOCK BAS" if t["sous_point_commande"] else ""
            top_inv_lines.append(
                f"  {t['nom']} — {t['total_sold']} vendus/7j | "
                f"vel {vel}/j | stock {t['stock_restant']}{flag} | rupture J+{jours:.0f}"
            )

        # Recommandations agents stock (Agent Décision)
        reco_lines = []
        for r in inv_ctx.get("agent_recos", [])[:4]:
            reco_lines.append(
                f"  [{r['type']}] {r['product_name']} x{r['qty']} — {r['texte'][:100]} "
                f"(confiance {r['confiance']:.0%})"
            )

        # Alertes agents (Agent Analyse)
        agent_alert_lines = []
        for a in inv_ctx.get("agent_alerts", [])[:4]:
            agent_alert_lines.append(
                f"  [{a['severite'].upper()}] {a['product_name']} — {a['message'][:80]}"
                f" → Action : {a['action'][:60]}"
            )

        # KPI boutique
        kpi = inv_ctx.get("kpi", {})
        kpi_txt = ""
        if kpi:
            kpi_txt = (
                f"\nKPI BOUTIQUE (Agent KPI) :\n"
                f"  CA jour : {kpi.get('ca_jour', 0):,.0f} TND | "
                f"Terminaux : {kpi.get('terminaux', 0)} | Forfaits : {kpi.get('forfaits', 0)} | "
                f"Agents actifs : {kpi.get('nb_agents', 0)} | "
                f"Moy/agent : {kpi.get('ca_moy_agent', 0):,.0f} TND"
            )

        inv_section = f"""
{'═'*58}
INVENTAIRE COMPLET (Agent Stock — temps réel) :
  Total SKUs : {total_skus} | ⛔ Ruptures : {ruptures} | ⚠ Critiques : {critiques} | ~ Warnings : {warnings} | ✓ OK : {ok_count}

Produits en alerte :
{chr(10).join(alert_lines) if alert_lines else "  Aucun produit en alerte"}

Produits disponibles (>15 unités) :
{chr(10).join(ok_lines) if ok_lines else "  Aucun produit en stock suffisant"}

Top vendeurs avec vélocité et risque rupture :
{chr(10).join(top_inv_lines) if top_inv_lines else "  Données non disponibles"}

Décisions Agent Stock (ORDER/EXPEDITE) :
{chr(10).join(reco_lines) if reco_lines else "  Aucune recommandation active"}

Alertes Agent Analyse :
{chr(10).join(agent_alert_lines) if agent_alert_lines else "  Aucune alerte active"}
{kpi_txt}"""

    return f"""Tu es CoachAgent — l'IA de coaching temps réel d'Ooredoo Tunisie, intégrée à la boutique {store_id}.
Tu lis en direct les outputs de 4 agents : Analyste Ventes, Stratège, Agent Stock, RAG Terrain.
Tu combines expertise vente telecom ET gestion stock. Tu réponds en français naturel.

{'═'*58}
SITUATION TEMPS RÉEL — {advisor_name} | {store_id}
{'═'*58}
Objectif du jour : {ca:,.0f} / {target:,.0f} TND → {verdict}
Transactions     : {nb_v} ventes enregistrées | Gap restant : {gap:,.0f} TND | {hl}h disponibles
Urgence          : {urgency} | Météo : {weather}{f" | Cause gap : {cause}" if cause else ""}

AGENT ANALYSTE — Ventes & performance :
  CA {ca:,.0f} / {target:,.0f} TND | {nb_v} ventes{f" | Prévision EOD : {forecast_eod:,.0f} TND" if forecast_eod else ""}{analyste_section}

  Top produits (7 jours) :
{top_txt}

POS TEMPS RÉEL — Dernières transactions :
{recent_txt}

AGENT STOCK (InventoryAnalysis + InventoryDecision) — Alertes :
{stock_alert_txt}

AGENT STRATÈGE — Plan d'action du jour :
{stratege_txt}
{sentinel_section}
{rag_section}{inv_section}
{'═'*58}
CATALOGUE OFFICIEL OOREDOO TUNISIE (seuls prix autorisés) :
  Terminaux  : iPhone 16 Pro 1 299 TND | Samsung A55 5G 899 TND | Galaxy S25 Ultra 1 599 TND | INFINIX NOTE 40 349 TND
  Forfaits   : 5G Max 100Go 49 TND/mois | Flexi 25Go 29 TND/mois | Unlimited 69 TND/mois | Famille 5G 120 TND/mois
  Box        : Fibre 1Go 59 TND/mois | Box 4G+ 39 TND/mois
  Services   : Assurance Premium 9 TND/mois | Cloud Backup 1To 15 TND/mois | TV Streaming 12 TND/mois
  Accessoires: AirPods Pro 3 279 TND | Apple Watch S10 449 TND
{'═'*58}
INTELLIGENCE COMPORTEMENTALE :

Salutation ("bonjour", "salut") →
  Réponds avec le prénom de l'advisor + résumé de sa situation en 1 phrase (basé sur données ci-dessus) + "Comment puis-je t'aider ?"

Bilan / recap ("parle moi des ventes", "comment ça va") →
  Résumé factuel structuré : CA réalisé vs objectif, nb ventes, top produit du jour, dernière transaction.
  Termine par 1 conseil actionnable basé sur les données Agent Stratège.
  RÈGLE COHÉRENCE : si CA > 0 TND, il y a eu des ventes — jamais "0 ventes" si CA > 0.

Script / technique de vente →
  Génère l'outil demandé avec les VRAIS produits du catalogue et les arguments du RAG si disponibles.
  Intègre les recommandations du Stratège si pertinentes.

Question stock / inventaire →
  Utilise les données Agent Stock ci-dessus. Lie toujours au CA : "X en rupture = Y TND de CA en risque."

Question cross-domaine (ventes + stock) →
  Réponds naturellement en liant les deux domaines. Exemple : "Flexi 25Go se vend bien (3ème) mais stock bas — pousse-le maintenant."

Hors-sujet (politique, sport, cuisine, etc.) →
  "Je suis ton coach Ooredoo — je me concentre sur la vente telecom et le stock. Dis-moi comment je peux t'aider sur ces sujets !"

RÈGLES ABSOLUES :
✓ Données ci-dessus UNIQUEMENT — jamais de chiffres inventés
✓ COHÉRENCE : CA={ca:,.0f} TND avec {nb_v} ventes — ces deux chiffres sont liés, ne les contredis jamais
✓ Prix CATALOGUE OFFICIEL ci-dessus — jamais de prix hors catalogue
✓ Français, tutoiement direct, ton de coach terrain — pas un robot corporate
✓ Concis et actionnable — 160 mots max, chaque phrase a de la valeur
✗ JAMAIS "je n'ai pas accès", "consulte le tableau de bord", "je ne peux pas voir"
✗ JAMAIS de JSON, de code, de balises HTML
✗ JAMAIS deux chiffres contradictoires dans la même réponse"""


# ══════════════════════════════════════════════════════════════════════════════
# 5. HISTORIQUE DU JOUR
# ══════════════════════════════════════════════════════════════════════════════

def _load_day_history_sync(advisor_name: str, store_id: str, limit: int = 8) -> list:
    """
    Charge les N dernières interactions du conseiller aujourd'hui.
    Retourne une liste de {role, content} pour l'API OpenRouter.
    """
    import psycopg2
    try:
        conn = psycopg2.connect(
            host=DB_CFG["host"], port=DB_CFG["port"],
            dbname=DB_CFG["dbname"], user=DB_CFG["user"],
            password=DB_CFG["password"], connect_timeout=5,
        )
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT message, response
                FROM public.coach_interactions
                WHERE store_id = %s
                  AND advisor_name = %s
                  AND created_at >= CURRENT_DATE
                ORDER BY created_at ASC
                LIMIT %s
            """, (store_id, advisor_name, limit))
            rows = cur.fetchall()
            history = []
            for msg, resp in rows:
                if msg:
                    history.append({"role": "user",      "content": msg[:600]})
                if resp:
                    history.append({"role": "assistant", "content": resp[:800]})
            logger.info(f"[COACH HIST] {len(rows)} echanges charges pour {advisor_name} ({store_id})")
            return history
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.warning(f"[COACH HIST] {str(e)[:80]}")
        return []


async def _load_day_history(advisor_name: str, store_id: str) -> list:
    return await asyncio.get_event_loop().run_in_executor(
        None, _load_day_history_sync, advisor_name, store_id
    )


# ══════════════════════════════════════════════════════════════════════════════
# 6. OPENROUTER — avec historique de conversation
# ══════════════════════════════════════════════════════════════════════════════

async def _call_llm(system: str, user_msg: str, max_tokens: int = 350,
                    temperature: float = 0.22,
                    history: list = None) -> tuple[str, float]:
    t0 = time.time()
    if not OPENROUTER_KEY:
        logger.error("[COACH] OPENROUTER_API_KEY manquante !")
        return "", 0.0
    try:
        import httpx
        # Construction des messages : system + historique du jour + message actuel
        messages = [{"role": "system", "content": system}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_msg})

        payload = {
            "model": OPENROUTER_MODEL,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": f"Bearer {OPENROUTER_KEY}",
                   "Content-Type": "application/json; charset=utf-8",
                   "HTTP-Referer": "https://github.com/MALEKALADAB11/multi-agent-sales-inventory",
                   "X-Title": "AI Sales Coach Ooredoo"}
        async with httpx.AsyncClient(timeout=35) as client:
            resp = await client.post(OPENROUTER_URL, headers=headers, content=body)
        data = resp.json()
        if "error" in data:
            logger.warning(f"[COACH LLM] {data['error'].get('message', '')[:100]}")
            return "", (time.time() - t0) * 1000
        reply = data["choices"][0]["message"]["content"].strip()
        for pfx in ["Reponse:", "Coach:", "CoachAgent:", "Here is",
                     "Based on", "REPONSE DU COACH :"]:
            if reply.lower().startswith(pfx.lower()):
                reply = reply[len(pfx):].strip()
        if reply.startswith("{"):
            m = re.search(r'"(?:reply|response|conseil|text)"\s*:\s*"([^"]+)"', reply)
            reply = m.group(1).strip() if m else ""
        ms = (time.time() - t0) * 1000
        nb_hist = len(history) // 2 if history else 0
        logger.info(f"[COACH LLM] {len(reply)} chars | {ms:.0f}ms | hist={nb_hist} | {OPENROUTER_MODEL}")
        return reply, ms
    except Exception as e:
        logger.warning(f"[COACH LLM] {str(e)[:120]}")
        return "", (time.time() - t0) * 1000


# ══════════════════════════════════════════════════════════════════════════════
# 7. MAIN ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/chat")
async def coach_chat(request: dict):
    t0 = time.time()

    message = (request.get("message") or "").strip()
    advisor_name = request.get("advisor_name") or "Conseiller"
    store_id = _normalize_store(request.get("store_id") or "I63")
    ctx = request.get("context") or {}
    logger.info(f"[COACH] store_id résolu: {request.get('store_id')!r} → {store_id!r}")

    if not message:
        return JSONResponse({"reply": "", "domain": "none", "mode": "empty",
                             "rag_used": False, "confidence": 0, "sources": []})

    # ── 1. Intent classification ───────────────────────────────────────────
    intent = _classify_intent(message)
    mode = intent["mode"]          # conversation | coaching | inventory
    domain = intent["domain"]      # sales | inventory
    qtype = intent["type"]
    intent_conf = intent["confidence"]

    logger.info(f"[COACH] {advisor_name} | mode={mode} domain={domain} type={qtype} "
                f"conf={intent_conf:.2f} | '{message[:60]}'")

    # ── Off-topic guard ────────────────────────────────────────────────
    if mode == "off_topic":
        logger.info(f"[COACH] OFF-TOPIC bloque : '{message[:60]}'")
        return JSONResponse({
            "reply": ("Je suis ton coach Ooredoo — specialise en vente telecom, "
                      "gestion de stock et objectifs commerciaux. "
                      "Pose-moi une question sur ces sujets et je t'aide tout de suite !"),
            "mode": "off_topic", "domain": "none", "question_type": "off_topic",
            "source": "guardrail", "model": "", "confidence": 1.0,
            "rag_used": False, "nb_rag_scripts": 0, "latency_ms": 0,
            "sources": [], "context_used": {"advisor": advisor_name},
            "rag_scripts": [], "inventory_alerts": [],
        })

    # ── 2. Chargement parallèle de TOUS les agents (architecture multi-agents) ─
    # Sales (Analyste + Stratège) + Inventory complet + Historique — en parallèle
    sctx, inv_ctx, day_history = await asyncio.gather(
        _load_sales_context(store_id, ctx),
        _load_inventory_context(store_id),
        _load_day_history(advisor_name, store_id),
    )
    urgency = sctx.get("urgency", "MEDIUM")

    # ── 3. RAG — Agent Knowledge (Milvus) ────────────────────────────────────
    # Actif pour coaching, scripts, objections et questions produits
    rag_scripts, rag_relevant = [], False
    should_rag = (
        mode in ("coaching", "greeting") or
        qtype in ("script", "objection", "closing", "upsell", "forfait", "objectif") or
        any(kw in message.lower() for kw in [
            "iphone", "samsung", "airpods", "watch", "forfait", "assurance",
            "bundle", "script", "objection", "closing", "comment vendre", "5g"])
    )
    if should_rag:
        rag_query = f"{message} {qtype} telecom vente Ooredoo"
        if sctx.get("gap_pct", 0) > 40:
            rag_query += " gap critique urgent"
        if "pluie" in sctx.get("weather", "").lower():
            rag_query += " pluie accessoires résistants"
        rag_scripts, _, rag_relevant = _search_rag(
            rag_query, sctx.get("hour", 12), top_k=3, min_score=0.30)

    # ── 4. Master prompt unifié — tous agents injectés ────────────────────────
    system_prompt = _build_master_prompt(
        advisor_name, store_id, sctx, rag_scripts, inv_ctx)

    # Tokens adaptés au type de question
    if mode == "greeting":
        max_tokens, temperature = 120, 0.30
    elif mode == "coaching" or qtype in ("script", "objectif", "objection"):
        max_tokens, temperature = 450, 0.18
    elif domain == "inventory":
        max_tokens, temperature = 400, 0.20
    else:
        max_tokens, temperature = 380, 0.22

    # ── 5. LLM Call ───────────────────────────────────────────────────────────
    reply, llm_ms = await _call_llm(
        system_prompt, message, max_tokens, temperature, history=day_history)

    # ── 6. Sources actives ────────────────────────────────────────────────────
    sources = [
        {"id": "pos",      "label": "POS temps réel",   "active": sctx.get("ca_today", 0) > 0},
        {"id": "analyste", "label": "Agent Analyste",   "active": bool(sctx.get("top_sellers"))},
        {"id": "stratege", "label": "Agent Stratège",   "active": bool(sctx.get("actions"))},
        {"id": "stock",    "label": "Agent Stock",      "active": inv_ctx.get("total_skus", 0) > 0},
        {"id": "decision", "label": "Agent Décision",   "active": bool(inv_ctx.get("agent_recos"))},
        {"id": "rag",      "label": "RAG Milvus",       "active": rag_relevant,
         "nb_scripts": len(rag_scripts)},
    ]
    if day_history:
        sources.append({"id": "history",
                        "label": f"Historique ({len(day_history)//2} échanges)",
                        "active": True})

    # ── 7. Confidence + fallback ──────────────────────────────────────────────
    if reply:
        model_used = OPENROUTER_MODEL
        active_agents = sum(1 for s in sources if s["active"])
        confidence = min(0.95, 0.70 + active_agents * 0.04)
        source_label = f"multi_agent+{'rag+' if rag_relevant else ''}llm"
    else:
        model_used = "fallback"
        confidence = 0.55
        source_label = "fallback"
        crit = inv_ctx.get("critical_items", [])
        perf = sctx.get("performance", 0)
        if crit:
            names = ", ".join(c["product_name"] for c in crit[:2])
            reply = (f"Alerte stock : {names} en rupture/critique. "
                     f"CA du jour : {sctx.get('ca_today', 0):,.0f} TND ({perf:.0f}%). "
                     "Focus bundle terminal + forfait + assurance. À toi !")
        elif mode == "greeting":
            reply = (f"Bonjour {advisor_name} ! {perf:.0f}% de l'objectif atteint. "
                     "Comment puis-je t'aider ?")
        else:
            reply = (f"{perf:.0f}% de l'objectif. "
                     "Assurance Premium 9 TND/mois sur chaque terminal — marge 80%. À toi !")

    total_ms = (time.time() - t0) * 1000

    # ── Traces ─────────────────────────────────────────────────────────────
    try:
        from langfuse_observer import trace_coach_chat
        trace_coach_chat(store_id=store_id, advisor_name=advisor_name,
            message=message, response=reply,
            question_type=f"{domain}/{mode}/{qtype}",
            rag_used=rag_relevant, nb_rag_scripts=len(rag_scripts),
            confidence=confidence, latency_ms=total_ms,
            gap_pct=sctx.get("gap_pct", 0), urgency=urgency)
    except Exception:
        pass

    try:
        from modules.coaching.agents.coach.tools import save_interaction
        save_interaction(advisor_name=advisor_name, store_id=store_id,
            message=message, response=reply,
            gap_pct=sctx.get("gap_pct", 0), urgency=urgency,
            rag_used=rag_relevant, nb_rag_scripts=len(rag_scripts),
            conseil_type=f"{domain}/{qtype}", confidence=confidence)
    except Exception:
        pass

    logger.info(f"[COACH] {mode}/{domain}/{qtype} | {advisor_name} | "
                f"RAG={'Y' if rag_relevant else 'N'}({len(rag_scripts)}) | "
                f"conf={confidence:.2f} | llm={llm_ms:.0f}ms | total={total_ms:.0f}ms")

    # ── 6. Response ────────────────────────────────────────────────────────
    return JSONResponse({
        "reply": reply,
        "mode": mode,
        "domain": domain,
        "question_type": qtype,
        "source": source_label,
        "model": model_used,
        "confidence": confidence,
        "rag_used": rag_relevant,
        "nb_rag_scripts": len(rag_scripts),
        "latency_ms": round(total_ms),
        "sources": [s for s in sources if s.get("active")],
        "context_used": {
            "advisor": advisor_name, "store_id": store_id,
            "domain": domain, "mode": mode,
            "hour": datetime.now().hour,
            **({"ca_today": sctx.get("ca_today", 0),
                "gap_pct": sctx.get("gap_pct", 0),
                "performance": sctx.get("performance", 0),
                "urgency": urgency} if domain == "sales" else {}),
            **({"total_skus": inv_ctx.get("total_skus", 0),
                "ruptures": inv_ctx.get("ruptures", 0)} if domain == "inventory" else {}),
        },
        "rag_scripts": [{"categorie": s["categorie"], "action": s["action"],
                         "score": s["score"]} for s in rag_scripts[:2]] if rag_scripts else [],
        "inventory_alerts": [{"sku": c["sku"], "product": c["product_name"],
                              "qty": c["stock_qty"], "level": c["risk_level"]}
                             for c in inv_ctx.get("critical_items", [])[:3]]
            if domain == "inventory" else [],
    })


@router.get("/health")
async def coach_health():
    return JSONResponse({
        "status": "ok", "version": "8.0.0",
        "architecture": "multi-agent-conversational",
        "modes": ["conversation", "coaching", "inventory"],
        "llm": OPENROUTER_MODEL, "llm_available": bool(OPENROUTER_KEY),
    })
