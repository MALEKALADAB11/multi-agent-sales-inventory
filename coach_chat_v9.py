"""
coach_chat_v9.py — Coach Chat Multi-Agent v9.0 (Production)
=============================================================
Améliorations vs v8 :
  1.  Catalogue dynamique depuis DB (TTL 10 min, stock temps réel)
  2.  Store ID normalization (alias frontend → store_id DB)
  3.  Fast path salutation (réponse < 5ms sans LLM)
  4.  Nouveau mode "recap/bilan" — résumé factuel depuis agents
  5.  Nouveau mode "cross_domain" — questions sales+inventory liées
  6.  Contexte Sales enrichi : top_sellers, recent_transactions, forecast_eod
  7.  Contexte Inventory complet :
        - agent_recos (Agent Décision : ORDER / EXPEDITE)
        - agent_alerts (Agent Analyse stock)
        - supply.reorder_params (EOQ, point commande, jours rupture)
        - agent_kpi_daily (terminaux, forfaits, postpayé, CA moyen/agent)
        - telco_targets_monthly (objectif mensuel boutique)
  8.  Profil conseiller depuis monitoring.advisor_profile
  9.  Historique conversation du jour (multi-tours, max 6 échanges)
  10. Chargement parallèle via asyncio.gather (sctx + inv + history)
  11. Prompt maître unifié — tous agents injectés, LLM décide format
  12. Score confiance multi-agents (0.70 + 0.03 / agent actif)
  13. Routing cross-domaine amélioré + réponse liée sales ↔ stock
  14. Fallback enrichi avec vraies données (zéro invention)

Architecture : OpenRouter (gpt-oss-120b:free) · Ollama (embeddings RAG) · PostgreSQL · Milvus
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
MILVUS_URI       = os.getenv("MILVUS_URI", "http://localhost:19530")
COLLECTION       = "coaching_scripts"
EMBED_DIM        = 768

DB_CFG = {
    "host":     os.getenv("POSTGRES_HOST", os.getenv("DB_HOST", "localhost")),
    "port":     int(os.getenv("POSTGRES_PORT", os.getenv("DB_PORT", "5432"))),
    "dbname":   os.getenv("POSTGRES_DB", os.getenv("DB_NAME", "ooredoo_sales")),
    "user":     os.getenv("POSTGRES_USER", os.getenv("DB_USER", "postgres")),
    "password": os.getenv("POSTGRES_PASSWORD", os.getenv("DB_PASSWORD", "admin")),
}

# Mapping alias frontend → store_id réel en DB
_STORE_MAP: dict[str, str] = {
    "store-lac2":    "I63", "OOR_LAC_01": "I63", "lac2":        "I63",
    "store-menzah":  "M01", "OOR_MENZAH_02": "M01",
    "store-sfax":    "S01", "OOR_SFAX_03":   "S01",
    "store-sousse":  "SO1", "OOR_SOUSSE_04": "SO1",
}

_CATALOG_FALLBACK = """\
TERMINAUX  : iPhone 16 Pro 1 299 TND | Samsung A55 5G 899 TND | Galaxy S25 Ultra 1 599 TND | INFINIX NOTE 40 349 TND
FORFAITS   : 5G Max 100Go 49 TND/mois | Flexi 25Go 29 TND/mois | Unlimited 69 TND/mois | Famille 5G 120 TND/mois (4 lignes)
BOX        : Fibre 1Go 59 TND/mois | Fibre Pro 500Mbps 79 TND/mois | Box 4G+ 39 TND/mois
SERVICES   : Assurance Premium 9 TND/mois (marge 80%) | Cloud Backup 1To 15 TND/mois | TV Streaming 12 TND/mois
ACCESSOIRES: AirPods Pro 3 279 TND (IPX4) | Apple Watch S10 449 TND (50m étanche) | Coques & Protections 29-89 TND
BUNDLE MAX : iPhone 16 Pro + 5G Max + Assurance = ~1 357 TND (54 TND/mois × 24 mois avance postpayé Ooredoo)"""

_catalog_cache: str = ""
_catalog_cache_ts: float = 0.0
_CATALOG_TTL = 600.0  # 10 minutes


def _normalize_store(store_id: str) -> str:
    return _STORE_MAP.get(store_id, store_id) if store_id else "I63"


def _load_catalog(store_id: str) -> str:
    """Catalogue produits dynamique depuis DB avec TTL 10 min. Fallback statique."""
    import psycopg2, psycopg2.extras, time as _t
    global _catalog_cache, _catalog_cache_ts
    if _catalog_cache and (_t.time() - _catalog_cache_ts) < _CATALOG_TTL:
        return _catalog_cache
    try:
        conn = psycopg2.connect(**DB_CFG, connect_timeout=6)
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            # Terminaux en stock avec quantité
            cur.execute("""
                SELECT p.nom, p.prix_ttc, p.marque,
                       COALESCE(sl.quantity_available, sl.quantity, 0) AS qty
                FROM sales.produits p
                JOIN inventory.stock_levels sl ON sl.sku = p.sku AND sl.store_id = %s
                WHERE p.flag_terminal = TRUE AND p.prix_ttc > 0
                  AND COALESCE(sl.quantity_available, sl.quantity, 0) > 0
                ORDER BY p.prix_ttc DESC LIMIT 8
            """, (store_id,))
            terminals = cur.fetchall()

            # Forfaits actifs
            cur.execute("""
                SELECT p.nom, p.prix_ttc
                FROM sales.produits p
                WHERE p.flag_forfait = TRUE AND p.prix_ttc > 0
                  AND (p.date_eol IS NULL OR p.date_eol >= CURRENT_DATE)
                ORDER BY p.prix_ttc ASC LIMIT 6
            """)
            forfaits = cur.fetchall()

            # Accessoires en stock
            cur.execute("""
                SELECT p.nom, p.prix_ttc
                FROM sales.produits p
                JOIN inventory.stock_levels sl ON sl.sku = p.sku AND sl.store_id = %s
                WHERE p.categorie = '70' AND p.prix_ttc > 0
                  AND COALESCE(sl.quantity_available, sl.quantity, 0) > 0
                ORDER BY p.prix_ttc DESC LIMIT 4
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

        result = "\n".join(lines) if lines else _CATALOG_FALLBACK
        _catalog_cache    = result
        _catalog_cache_ts = _t.time()
        return result
    except Exception as e:
        logger.warning(f"[COACH CATALOG] {str(e)[:80]}")
        return _CATALOG_FALLBACK


# ══════════════════════════════════════════════════════════════════════════════
# 1. INTENT CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════

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

CONVERSATION_SIGNALS = [
    "comment", "c'est quoi", "explique", "qu'est-ce", "pourquoi",
    "je voulais savoir", "peux-tu", "dis-moi",
    "deroulement", "processus", "etape", "procedure", "fonctionnement",
    "aide", "conseil", "avis", "quoi faire", "que penses",
    "bonjour", "bonsoir", "salut", "hello", "hey",
    "merci", "ok", "d'accord", "compris", "oui", "non",
]

INVENTORY_SIGNALS = [
    "stock", "rupture", "inventaire", "quantite", "disponible",
    "epuise", "combien reste", "en stock", "reapprovisionner",
    "commande", "livraison", "reorder", "rotation", "dormant",
    "best-seller", "top ventes", "jours de stock", "eoq",
    "point de commande", "alerte stock", "critique stock",
]

SALES_SIGNALS = [
    "vente", "objectif", "gap", "ca", "chiffre", "performance",
    "client", "conseiller", "forfait", "terminal", "script",
    "objection", "closing", "upsell", "bundle", "coach",
]

RECAP_SIGNALS = [
    "parle moi", "resume", "bilan", "comment vont",
    "quelles sont les ventes", "ventes d aujourd", "ventes du jour",
    "ca du jour", "chiffre du jour", "ou en sont", "etat des ventes",
    "montre moi", "dis moi les ventes", "qu est ce qui se passe",
    "qu est ce qui se vend", "comment se passe", "bilan du jour",
    "resultat", "performance du jour", "comment ca va",
]

OFF_TOPIC_SIGNALS = [
    "cybersecurite", "crypto", "bitcoin", "blockchain", "politique",
    "recette", "cuisine", "football", "match", "film", "serie",
    "jeu video", "gaming", "musique", "chanson", "philosophie",
    "mathematique", "physique", "chimie", "histoire", "geographie",
    "medecine", "sante", "religion", "horoscope", "astrologie",
    "blague", "poeme", "code python", "javascript", "programmation",
    "intelligence artificielle", "machine learning", "deep learning",
    "chatgpt", "openai", "bourse", "trading", "investissement",
]

TELECOM_ANCHORS = [
    "ooredoo", "vente", "client", "boutique", "forfait", "stock",
    "objectif", "conseiller", "terminal", "iphone", "samsung",
    "fibre", "5g", "assurance", "bundle", "coach", "strategie",
    "produit", "prix", "offre", "recharge", "sim",
]

_PURE_GREETINGS = {
    "bonjour", "bonsoir", "salut", "hello", "hey", "coucou", "bjr", "bj",
    "slt", "hi", "yo", "wesh", "bonjour coach", "salut coach", "bonsoir coach",
    "hey coach", "bonjour !", "salut !", "hello !", "coucou !",
}


def _norm(text: str) -> str:
    for a, b in [("é","e"),("è","e"),("ê","e"),("à","a"),("ô","o"),
                 ("î","i"),("ç","c"),("ù","u"),("â","a"),("û","u")]:
        text = text.replace(a, b)
    return text


def _is_pure_greeting(msg: str) -> bool:
    norm = _norm(msg.strip().lower().rstrip("!?,. "))
    if norm in _PURE_GREETINGS:
        return True
    words = norm.split()
    return len(words) <= 2 and words[0] in _PURE_GREETINGS


def _classify_intent(message: str) -> dict:
    """Classifie le message : mode + domain + type + confidence."""
    msg = _norm(message.lower())

    # 0. Salutation pure — fast path
    if _is_pure_greeting(msg):
        return {"mode": "greeting", "domain": "sales", "type": "greeting", "confidence": 0.99}

    # 1. Off-topic
    off_hits     = sum(1 for kw in OFF_TOPIC_SIGNALS if kw in msg)
    telecom_hits = sum(1 for kw in TELECOM_ANCHORS   if kw in msg)
    if off_hits >= 1 and telecom_hits == 0:
        return {"mode": "off_topic", "domain": "none", "type": "off_topic", "confidence": 0.95}

    # 2. Recap/bilan — question informative sur la journée
    recap_hits = sum(1 for kw in RECAP_SIGNALS if kw in msg)
    if recap_hits >= 1:
        return {"mode": "conversation", "domain": "sales", "type": "recap",
                "confidence": min(0.93, 0.70 + recap_hits * 0.10)}

    # 3. Signals inventory + sales
    inv_hits   = sum(1 for kw in INVENTORY_SIGNALS if kw in msg)
    sales_hits = sum(1 for kw in SALES_SIGNALS     if kw in msg)

    # Cross-domain : questions qui mêlent ventes ET stock
    if inv_hits >= 1 and sales_hits >= 2:
        return {"mode": "cross_domain", "domain": "both", "type": "cross_domain",
                "confidence": min(0.91, 0.65 + (inv_hits + sales_hits) * 0.04)}

    # Inventory pur
    if inv_hits >= 1:
        inv_type = "stock"
        for kw in ["reappro", "commande", "livraison", "reorder", "eoq"]:
            if kw in msg: inv_type = "reorder"; break
        for kw in ["rotation", "dormant", "best-seller", "top ventes"]:
            if kw in msg: inv_type = "rotation"; break
        for kw in ["rupture", "alerte", "critique", "danger"]:
            if kw in msg: inv_type = "alerte";  break
        return {"mode": "inventory", "domain": "inventory", "type": inv_type,
                "confidence": min(0.95, 0.60 + inv_hits * 0.15)}

    # 4. Coaching explicite
    best_type, best_score = None, 0
    for qtype, keywords in COACHING_INTENTS.items():
        hits = sum(1 for kw in keywords if kw in msg)
        if hits > best_score:
            best_score, best_type = hits, qtype

    conv_hits = sum(1 for kw in CONVERSATION_SIGNALS if kw in msg)

    if best_score >= 2:
        return {"mode": "coaching", "domain": "sales", "type": best_type,
                "confidence": min(0.95, 0.60 + best_score * 0.15)}
    elif best_score == 1 and conv_hits == 0:
        return {"mode": "coaching", "domain": "sales", "type": best_type, "confidence": 0.70}
    else:
        return {"mode": "conversation", "domain": "sales", "type": "general", "confidence": 0.80}


# ══════════════════════════════════════════════════════════════════════════════
# 2. CONTEXT LOADERS
# ══════════════════════════════════════════════════════════════════════════════

def _query_pos_pg_sync(store_id: str) -> dict:
    """CA + nb_transactions depuis la vue POS (MAX(date_only) — compatible données simulées)."""
    import psycopg2
    try:
        conn = psycopg2.connect(**DB_CFG, connect_timeout=5)
        cur  = conn.cursor()
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
            cur.close(); conn.close()
    except Exception as e:
        logger.warning(f"[COACH POS] {str(e)[:80]}")
    return {"ca_total": 0, "nb_transactions": 0}


async def _query_pos_pg(store_id: str) -> dict:
    return await asyncio.get_event_loop().run_in_executor(None, _query_pos_pg_sync, store_id)


def _load_sales_detail_sync(store_id: str) -> dict:
    """Top-vendeurs 7j + dernières transactions depuis sales.transactions."""
    import psycopg2, psycopg2.extras
    result = {"top_sellers": [], "recent_transactions": []}
    try:
        conn = psycopg2.connect(**DB_CFG, connect_timeout=6)
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            # Top produits 7 derniers jours
            cur.execute("""
                SELECT t.sku, COALESCE(p.nom, t.sku::text) AS nom,
                       SUM(t.quantity) AS qty, SUM(t.lig_ttc) AS ca
                FROM sales.transactions t
                LEFT JOIN sales.produits p ON p.sku = t.sku
                WHERE t.store_id = %s
                  AND t.date_only >= (
                      SELECT MAX(date_only) FROM sales.transactions WHERE store_id = %s
                  ) - INTERVAL '7 days'
                GROUP BY t.sku, p.nom
                ORDER BY ca DESC LIMIT 15
            """, (store_id, store_id))
            result["top_sellers"] = [
                {"sku": str(r["sku"]), "nom": r["nom"],
                 "qty": int(r["qty"] or 0), "ca": float(r["ca"] or 0)}
                for r in cur.fetchall()
            ]

            # Dernières transactions
            cur.execute("""
                SELECT COALESCE(p.nom, t.sku::text) AS nom,
                       t.quantity, t.lig_ttc, t.heure, t.date_only
                FROM sales.transactions t
                LEFT JOIN sales.produits p ON p.sku = t.sku
                WHERE t.store_id = %s
                ORDER BY t.date_only DESC, t.heure DESC LIMIT 10
            """, (store_id,))
            result["recent_transactions"] = [
                {"nom": r["nom"], "qty": int(r["quantity"] or 1),
                 "ttc": float(r["lig_ttc"] or 0), "heure": int(r["heure"] or 0),
                 "date": str(r["date_only"])}
                for r in cur.fetchall()
            ]
            logger.debug(f"[COACH SALES] top={len(result['top_sellers'])} recent={len(result['recent_transactions'])}")
        finally:
            cur.close(); conn.close()
    except Exception as e:
        logger.warning(f"[COACH SALES DETAIL] {str(e)[:80]}")
    return result


async def _load_sales_context(store_id: str, ctx: dict) -> dict:
    now = datetime.now()
    hour       = now.hour
    hours_left = max(1, 20 - hour)

    ca_today  = float(ctx.get("current_revenue") or ctx.get("ca_today")  or 0)
    ca_target = float(ctx.get("daily_target")    or ctx.get("ca_target") or 1007)
    gap_pct   = float(ctx.get("gap_pct")         or ctx.get("gap_objectif") or 0)
    urgency   = str(ctx.get("urgency")           or ctx.get("urgency_level") or "MEDIUM").upper()
    weather   = str(ctx.get("weather")           or "Tunis")
    nb_ventes = int(ctx.get("nb_ventes")         or 0)

    if ca_today == 0:
        pg         = await _query_pos_pg(store_id)
        ca_today   = pg.get("ca_total", 0)
        nb_ventes  = nb_ventes or pg.get("nb_transactions", 0)

    detail = await asyncio.get_event_loop().run_in_executor(
        None, _load_sales_detail_sync, store_id
    )

    gap_tnd = max(0.0, ca_target - ca_today)
    perf    = round((ca_today / ca_target * 100), 1) if ca_target > 0 else 0.0
    if gap_pct == 0 and ca_target > 0:
        gap_pct = round((gap_tnd / ca_target) * 100, 1)

    # Réconciliation nb_ventes vs CA (évite contradiction "0 ventes mais CA > 0")
    if nb_ventes == 0 and ca_today > 0 and detail.get("recent_transactions"):
        most_recent = detail["recent_transactions"][0].get("date", "")
        if most_recent:
            nb_ventes = sum(1 for t in detail["recent_transactions"]
                            if t.get("date") == most_recent)
        nb_ventes = nb_ventes or len(detail["recent_transactions"])

    actions = ctx.get("strategie_actions") or []
    actions_txt = "\n".join(
        f"  {a.get('priorite', i+1)}. {a.get('action', '')} → {a.get('produit_cible', '')} "
        f"| Argument : {a.get('argument_vente', '')} | Impact : {a.get('impact_estime', '')}"
        for i, a in enumerate(actions[:4])
    ) if actions else ""

    return {
        "ca_today":     ca_today,   "ca_target":  ca_target,
        "gap_pct":      gap_pct,    "gap_tnd":    gap_tnd,
        "performance":  perf,       "nb_ventes":  nb_ventes,
        "urgency":      urgency,    "weather":    weather,
        "hours_left":   hours_left, "hour":       hour,
        "actions":      actions,    "actions_txt": actions_txt,
        "cause_racine": ctx.get("cause_racine", ""),
        "focus_produits": ctx.get("focus_produits", []),
        "analyst_summary": str(ctx.get("analyst_summary") or ""),
        "forecast_eod":    float(ctx.get("forecast_eod") or 0),
        "context_report":  ctx.get("context_report") or {},
        "top_sellers":       detail["top_sellers"],
        "recent_transactions": detail["recent_transactions"],
    }


def _load_inventory_context_sync(store_id: str) -> dict:
    """
    Contexte stock complet — 8 requêtes SQL :
      1. Stats globales (ruptures, critiques, warnings, ok)
      2. Produits en alerte (≤15) + supply.reorder_params (EOQ, point commande)
      3. Produits OK (>15)
      4. Recommandations Agent Décision (inventory.recommendations)
      5. Alertes Agent Analyse (inventory.alerts)
      6. Top vendeurs 7j avec vélocité + supply chain
      7. KPI boutique du jour (agent_kpi_daily)
      8. Objectif mensuel (telco_targets_monthly)
    """
    import psycopg2, psycopg2.extras
    _empty = {
        "total_skus": 0, "ruptures": 0, "critiques": 0, "warnings": 0,
        "ok_count": 0, "avg_stock": 0.0,
        "critical_items": [], "alert_items": [], "ok_items": [],
        "top_sellers": [], "agent_recos": [], "agent_alerts": [],
        "kpi": {}, "target": {},
    }
    try:
        conn = psycopg2.connect(**DB_CFG, connect_timeout=8)
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            # ── 1. Stats globales ──────────────────────────────────────────
            cur.execute("""
                SELECT COUNT(*) AS total,
                  SUM(CASE WHEN COALESCE(quantity_available,quantity,0) <= 0  THEN 1 ELSE 0 END) AS ruptures,
                  SUM(CASE WHEN COALESCE(quantity_available,quantity,0) BETWEEN 1  AND 5  THEN 1 ELSE 0 END) AS critiques,
                  SUM(CASE WHEN COALESCE(quantity_available,quantity,0) BETWEEN 6  AND 15 THEN 1 ELSE 0 END) AS warnings,
                  SUM(CASE WHEN COALESCE(quantity_available,quantity,0) > 15 THEN 1 ELSE 0 END) AS ok_count,
                  AVG(COALESCE(quantity_available,quantity,0))::NUMERIC(10,1) AS avg_stock
                FROM inventory.stock_levels WHERE store_id = %s
            """, (store_id,))
            stats = cur.fetchone()

            # ── 2. Produits en alerte (≤15) + supply chain ─────────────────
            cur.execute("""
                SELECT sl.sku, COALESCE(p.nom, sl.sku::text) AS product_name,
                       COALESCE(p.categorie,'Autre') AS categorie,
                       COALESCE(p.prix_ttc, 0) AS prix_ttc,
                       COALESCE(sl.quantity_available, sl.quantity, 0) AS stock_qty,
                       CASE
                         WHEN COALESCE(sl.quantity_available,sl.quantity,0) <= 0 THEN 'rupture'
                         WHEN COALESCE(sl.quantity_available,sl.quantity,0) <= 5 THEN 'critical'
                         ELSE 'warning'
                       END AS risk_level,
                       rp.point_commande, rp.eoq, rp.stock_securite, rp.demande_moy_jour
                FROM inventory.stock_levels sl
                LEFT JOIN sales.produits p ON p.sku = sl.sku
                LEFT JOIN supply.reorder_params rp ON rp.sku = sl.sku AND rp.store_id = sl.store_id
                WHERE sl.store_id = %s
                  AND COALESCE(sl.quantity_available, sl.quantity, 0) <= 15
                ORDER BY COALESCE(sl.quantity_available, sl.quantity, 0) ASC LIMIT 20
            """, (store_id,))
            alert_rows = cur.fetchall()

            # ── 3. Produits OK (>15) ───────────────────────────────────────
            cur.execute("""
                SELECT sl.sku, COALESCE(p.nom, sl.sku::text) AS product_name,
                       COALESCE(p.categorie,'Autre') AS categorie,
                       COALESCE(sl.quantity_available, sl.quantity, 0) AS stock_qty,
                       COALESCE(p.prix_ttc, 0) AS prix_ttc
                FROM inventory.stock_levels sl
                LEFT JOIN sales.produits p ON p.sku = sl.sku
                WHERE sl.store_id = %s
                  AND COALESCE(sl.quantity_available, sl.quantity, 0) > 15
                ORDER BY COALESCE(sl.quantity_available, sl.quantity, 0) DESC LIMIT 15
            """, (store_id,))
            ok_rows = cur.fetchall()

            # ── 4. Recommandations Agent Décision (ORDER / EXPEDITE) ────────
            cur.execute("""
                SELECT r.sku, COALESCE(p.nom, r.sku::text) AS product_name,
                       r.recommendation_type, r.suggested_quantity AS recommended_qty,
                       r.recommendation_text, r.confidence, r.created_at
                FROM inventory.recommendations r
                LEFT JOIN sales.produits p ON p.sku = r.sku
                WHERE r.store_id = %s
                  AND r.status IN ('pending','approved')
                  AND r.created_at >= CURRENT_TIMESTAMP - INTERVAL '48 hours'
                ORDER BY r.created_at DESC LIMIT 10
            """, (store_id,))
            reco_rows = cur.fetchall()

            # ── 5. Alertes Agent Analyse ────────────────────────────────────
            cur.execute("""
                SELECT a.sku, COALESCE(p.nom, a.sku::text) AS product_name,
                       a.alert_type, a.severity, a.message,
                       a.recommended_action, a.created_at
                FROM inventory.alerts a
                LEFT JOIN sales.produits p ON p.sku = a.sku
                WHERE a.store_id = %s
                  AND a.status = 'active'
                  AND a.severity IN ('critical','high','medium')
                ORDER BY
                  CASE a.severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 ELSE 3 END,
                  a.created_at DESC LIMIT 10
            """, (store_id,))
            alert_agent_rows = cur.fetchall()

            # ── 6. Top vendeurs 7j + vélocité + supply chain ────────────────
            cur.execute("""
                SELECT t.sku, COALESCE(p.nom, t.sku::text) AS nom,
                       SUM(t.quantity) AS total_sold,
                       COALESCE(sl.quantity_available, sl.quantity, 0) AS stock_restant,
                       COALESCE(p.prix_ttc, 0) AS prix_ttc,
                       COALESCE(rp.demande_moy_jour, SUM(t.quantity)::NUMERIC/7) AS vel_jour,
                       rp.point_commande
                FROM sales.transactions t
                LEFT JOIN sales.produits p     ON p.sku = t.sku
                LEFT JOIN inventory.stock_levels sl ON sl.sku = t.sku AND sl.store_id = t.store_id
                LEFT JOIN supply.reorder_params rp  ON rp.sku = t.sku AND rp.store_id = t.store_id
                WHERE t.store_id = %s
                  AND t.date_only >= (
                      SELECT MAX(date_only) FROM sales.transactions WHERE store_id = %s
                  ) - INTERVAL '7 days'
                GROUP BY t.sku, p.nom, sl.quantity_available, sl.quantity,
                         p.prix_ttc, rp.demande_moy_jour, rp.point_commande
                ORDER BY total_sold DESC LIMIT 10
            """, (store_id, store_id))
            top_rows = cur.fetchall()

            # ── 7. KPI boutique du jour + tendance 7j ──────────────────────
            cur.execute("""
                SELECT SUM(ca_realise) AS ca_jour, SUM(nb_transactions) AS nb_tx,
                       SUM(nb_forfaits) AS forfaits, SUM(nb_terminaux) AS terminaux,
                       SUM(nb_postpaye) AS postpaye, COUNT(DISTINCT agent_id) AS nb_agents,
                       AVG(ca_realise) AS ca_moy_agent
                FROM agent_kpi_daily
                WHERE store_id = %s AND kpi_date = (
                    SELECT MAX(kpi_date) FROM agent_kpi_daily WHERE store_id = %s
                )
            """, (store_id, store_id))
            kpi_today = cur.fetchone()

            cur.execute("""
                SELECT SUM(ca_realise)/NULLIF(COUNT(DISTINCT kpi_date),0) AS ca_7j_avg,
                       SUM(nb_terminaux) AS term_7j, SUM(nb_forfaits) AS forc_7j
                FROM agent_kpi_daily
                WHERE store_id = %s AND kpi_date >= CURRENT_DATE - INTERVAL '7 days'
            """, (store_id,))
            kpi_7j = cur.fetchone()

            # ── 8. Objectif mensuel boutique ────────────────────────────────
            cur.execute("""
                SELECT ca_cible_mensuel, activations_totales,
                       ventes_terminaux, facteur_saisonnier
                FROM telco_targets_monthly
                WHERE store_id = %s
                  AND mois  = EXTRACT(MONTH FROM CURRENT_DATE)::INTEGER
                  AND annee = EXTRACT(YEAR  FROM CURRENT_DATE)::INTEGER
                  AND niveau = 'BOUTIQUE'
                LIMIT 1
            """, (store_id,))
            target_row = cur.fetchone()

        finally:
            cur.close(); conn.close()

        # ── Construction des résultats ─────────────────────────────────────
        alert_items = []
        for r in alert_rows:
            stock = int(r["stock_qty"] or 0)
            vel   = float(r["demande_moy_jour"] or 0.5)
            jours = round(stock / vel, 1) if vel > 0 and stock > 0 else 0
            alert_items.append({
                "sku": str(r["sku"]), "product_name": r["product_name"],
                "categorie": r["categorie"], "prix_ttc": float(r["prix_ttc"] or 0),
                "stock_qty": stock, "risk_level": r["risk_level"],
                "jours_rupture": jours,
                "point_commande": int(r["point_commande"] or 0),
                "eoq": int(r["eoq"] or 0),
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
            vel   = float(r["vel_jour"] or 0)
            stock = int(r["stock_restant"] or 0)
            jours = round(stock / vel, 1) if vel > 0 else 999
            top_sellers.append({
                "sku": str(r["sku"]), "nom": r["nom"],
                "total_sold": int(r["total_sold"] or 0),
                "stock_restant": stock,
                "prix_ttc": float(r["prix_ttc"] or 0),
                "vel_jour": round(vel, 2),
                "jours_rupture": jours,
                "sous_point_commande": stock <= int(r["point_commande"] or 0),
            })

        def _i(obj, key, default=0): return int(obj[key] or default) if obj and obj.get(key) is not None else default
        def _f(obj, key, default=0.0): return float(obj[key] or default) if obj and obj.get(key) is not None else default

        kpi = {
            "ca_jour":      _f(kpi_today, "ca_jour"),
            "nb_tx":        _i(kpi_today, "nb_tx"),
            "forfaits":     _i(kpi_today, "forfaits"),
            "terminaux":    _i(kpi_today, "terminaux"),
            "postpaye":     _i(kpi_today, "postpaye"),
            "nb_agents":    _i(kpi_today, "nb_agents"),
            "ca_moy_agent": _f(kpi_today, "ca_moy_agent"),
            "ca_7j_avg":    _f(kpi_7j,    "ca_7j_avg"),
            "term_7j":      _i(kpi_7j,    "term_7j"),
            "forc_7j":      _i(kpi_7j,    "forc_7j"),
        }

        target = {
            "ca_cible_mensuel":  _f(target_row, "ca_cible_mensuel"),
            "activations_cible": _i(target_row, "activations_totales"),
            "terminaux_cible":   _i(target_row, "ventes_terminaux"),
            "facteur_saison":    _f(target_row, "facteur_saisonnier", 1.0),
        }

        logger.info(
            f"[COACH INV] store={store_id} total={_i(stats,'total')} "
            f"ruptures={_i(stats,'ruptures')} recos={len(recos)} alerts={len(agent_alerts)}"
        )
        return {
            "total_skus":     _i(stats, "total"),
            "ruptures":       _i(stats, "ruptures"),
            "critiques":      _i(stats, "critiques"),
            "warnings":       _i(stats, "warnings"),
            "ok_count":       _i(stats, "ok_count"),
            "avg_stock":      _f(stats, "avg_stock"),
            "critical_items": [i for i in alert_items if i["risk_level"] in ("rupture", "critical")],
            "alert_items":    alert_items,
            "ok_items":       ok_items,
            "top_sellers":    top_sellers,
            "agent_recos":    recos,
            "agent_alerts":   agent_alerts,
            "kpi":            kpi,
            "target":         target,
        }
    except Exception as e:
        logger.error(f"[COACH INV] DB error: {str(e)[:120]}")
        return _empty


async def _load_inventory_context(store_id: str) -> dict:
    return await asyncio.get_event_loop().run_in_executor(
        None, _load_inventory_context_sync, store_id
    )


def _load_advisor_profile_sync(advisor_name: str, store_id: str) -> dict:
    """Profil conseiller : catégories fortes/faibles + taux d'acceptation."""
    import psycopg2
    try:
        conn = psycopg2.connect(**DB_CFG, connect_timeout=3)
        cur  = conn.cursor()
        try:
            cur.execute("""
                SELECT strong_categories, weak_categories,
                       avg_response_acceptance, total_recos_received, total_recos_followed
                FROM monitoring.advisor_profile
                WHERE advisor_name = %s AND store_id = %s
            """, (advisor_name, store_id))
            row = cur.fetchone()
            if row:
                import json as _j
                return {
                    "strong":      _j.loads(row[0] or "{}"),
                    "weak":        _j.loads(row[1] or "{}"),
                    "acceptance":  float(row[2] or 0.5),
                    "total_recos": int(row[3] or 0),
                    "followed":    int(row[4] or 0),
                }
        finally:
            cur.close(); conn.close()
    except Exception:
        pass
    return {"strong": {}, "weak": {}, "acceptance": 0.5, "total_recos": 0, "followed": 0}


def _load_day_history_sync(advisor_name: str, store_id: str, limit: int = 6) -> list:
    """Historique conversation du jour — multi-tours pour le contexte LLM."""
    import psycopg2
    try:
        conn = psycopg2.connect(**DB_CFG, connect_timeout=5)
        cur  = conn.cursor()
        try:
            cur.execute("""
                SELECT message, response
                FROM public.coach_interactions
                WHERE store_id = %s AND advisor_name = %s
                  AND created_at >= CURRENT_DATE
                ORDER BY created_at ASC LIMIT %s
            """, (store_id, advisor_name, limit))
            history = []
            for msg, resp in cur.fetchall():
                if msg:  history.append({"role": "user",      "content": msg[:600]})
                if resp: history.append({"role": "assistant", "content": resp[:800]})
            logger.debug(f"[COACH HIST] {len(history)//2} échanges chargés pour {advisor_name}")
            return history
        finally:
            cur.close(); conn.close()
    except Exception as e:
        logger.warning(f"[COACH HIST] {str(e)[:80]}")
    return []


async def _load_day_history(advisor_name: str, store_id: str) -> list:
    return await asyncio.get_event_loop().run_in_executor(
        None, _load_day_history_sync, advisor_name, store_id
    )


# ══════════════════════════════════════════════════════════════════════════════
# 3. RAG SEARCH — conditionnel et transparent
# ══════════════════════════════════════════════════════════════════════════════

def _ensure_rag_collection(client) -> None:
    """Crée la collection Milvus si absente (bootstrap sécurisé)."""
    try:
        if client.has_collection(COLLECTION):
            return
        from pymilvus import DataType
        schema = client.create_schema(auto_id=True, enable_dynamic_field=True)
        schema.add_field("id",        DataType.INT64,        is_primary=True, auto_id=True)
        schema.add_field("vector",    DataType.FLOAT_VECTOR, dim=EMBED_DIM)
        for name, dtype, extra in [
            ("pg_id",     DataType.INT64,   {}),
            ("categorie", DataType.VARCHAR, {"max_length": 100}),
            ("situation", DataType.VARCHAR, {"max_length": 200}),
            ("action",    DataType.VARCHAR, {"max_length": 200}),
            ("produit",   DataType.VARCHAR, {"max_length": 100}),
            ("argument",  DataType.VARCHAR, {"max_length": 200}),
            ("impact",    DataType.VARCHAR, {"max_length": 100}),
            ("heure_min", DataType.INT64,   {}),
            ("heure_max", DataType.INT64,   {}),
        ]:
            schema.add_field(name, dtype, **extra)
        idx = client.prepare_index_params()
        idx.add_index("vector", index_type="FLAT", metric_type="COSINE")
        client.create_collection(COLLECTION, schema=schema, index_params=idx)
        logger.info(f"[COACH RAG] Collection '{COLLECTION}' créée")
    except Exception as e:
        logger.warning(f"[COACH RAG] ensure_collection: {e}")


def _search_rag(query: str, hour: int, top_k: int = 3,
                min_score: float = 0.30) -> tuple[list, bool]:
    """RAG conditionnel — retourne (scripts, is_relevant)."""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": "nomic-embed-text", "prompt": query[:400]},
            timeout=12,
        )
        emb = resp.json().get("embedding", [])
        if not emb: return [], False
        if len(emb) < EMBED_DIM: emb += [0.0] * (EMBED_DIM - len(emb))
        emb = emb[:EMBED_DIM]

        from pymilvus import MilvusClient
        client = MilvusClient(uri=MILVUS_URI)
        _ensure_rag_collection(client)

        results = client.search(
            collection_name=COLLECTION, data=[emb], limit=top_k + 2,
            output_fields=["categorie", "situation", "action", "produit",
                           "argument", "impact", "heure_min", "heure_max"],
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
                "situation": e.get("situation", "")[:120],
                "action":    e.get("action",    "")[:120],
                "produit":   e.get("produit",   ""),
                "argument":  e.get("argument",  "")[:150],
                "impact":    e.get("impact",    ""),
            })
        scripts      = sorted(scripts, key=lambda x: x["score"], reverse=True)[:top_k]
        is_relevant  = bool(scripts) and scripts[0]["score"] >= min_score
        return scripts, is_relevant
    except Exception as e:
        logger.warning(f"[COACH RAG] {str(e)[:60]}")
        return [], False


# ══════════════════════════════════════════════════════════════════════════════
# 4. PROMPT MAÎTRE UNIFIÉ — tous agents injectés, LLM décide le format
# ══════════════════════════════════════════════════════════════════════════════

def _build_master_prompt(
    advisor_name:    str,
    store_id:        str,
    sctx:            dict,
    rag_scripts:     list,
    inv_ctx:         dict,
    catalog:         str,
    advisor_profile: dict,
) -> str:
    """
    Injecte dans UN SEUL prompt l'output de 5 agents temps réel :
    Analyste Ventes · Stratège · Agent Stock · RAG Terrain · Sentinel (contexte marché).
    """
    ca      = sctx.get("ca_today", 0)
    target  = sctx.get("ca_target", 0)
    perf    = sctx.get("performance", 0)
    gap     = sctx.get("gap_tnd", 0)
    nb_v    = sctx.get("nb_ventes", 0)
    urgency = sctx.get("urgency", "MEDIUM")
    hl      = sctx.get("hours_left", 8)
    weather = sctx.get("weather", "")
    cause   = sctx.get("cause_racine", "")
    actions = sctx.get("actions", [])

    # Verdict objectif
    if perf >= 100: verdict = f"OBJECTIF ATTEINT ({perf:.0f}%) — excellent !"
    elif perf >= 75: verdict = f"{perf:.0f}% — en bonne voie"
    elif perf >= 40: verdict = f"{perf:.0f}% — à accélérer"
    else:            verdict = f"{perf:.0f}% — URGENT, mobilisation totale"

    # ── Agent Analyste : top produits ────────────────────────────────────────
    top = sctx.get("top_sellers", [])
    top_txt = "\n".join(
        f"  {i+1}. {t['nom']} — {t['qty']} vendus — {t['ca']:,.0f} TND"
        for i, t in enumerate(top[:6])
    ) or "  (données en chargement)"

    # ── POS temps réel ────────────────────────────────────────────────────────
    recent = sctx.get("recent_transactions", [])
    recent_txt = "\n".join(
        f"  {r['heure']}h — {r['nom']} x{r['qty']} — {r['ttc']:,.0f} TND  [{r['date']}]"
        for r in recent[:5]
    ) or "  (aucune transaction récente)"

    # ── Alertes stock critique (pour guardrail "ne pas promettre rupture") ────
    critical_items = inv_ctx.get("critical_items", []) if inv_ctx else []
    stock_alert_txt = "\n".join(
        f"  {'X' if it['risk_level']=='rupture' else '!'} {it['product_name']} "
        f"— {it['stock_qty']} unité(s) [{it['risk_level'].upper()}]"
        f" | rupture J+{it.get('jours_rupture',0):.0f}"
        f"{' → NE PAS PROMETTRE' if it['risk_level']=='rupture' else ' → vendre en priorité'}"
        for it in critical_items[:8]
    ) or "  OK — aucune rupture critique détectée"

    # ── Agent Analyste : résumé + forecast ───────────────────────────────────
    analyst_summary = sctx.get("analyst_summary", "")
    forecast_eod    = sctx.get("forecast_eod", 0)
    focus_produits  = sctx.get("focus_produits", [])
    analyste_section = ""
    if analyst_summary:
        analyste_section += f"\n  Synthèse Analyste : {analyst_summary[:200]}"
    if forecast_eod > 0:
        analyste_section += f"\n  Prévision fin de journée : {forecast_eod:,.0f} TND"
    if focus_produits:
        analyste_section += f"\n  Produits focus : {', '.join(str(p) for p in focus_produits[:5])}"

    # ── Sentinel / Context Agent ──────────────────────────────────────────────
    sentinel_section = ""
    ctx_report = sctx.get("context_report", {})
    if ctx_report:
        uplift   = ctx_report.get("demand_uplift_pct", 0)
        interp   = ctx_report.get("interpretation", "")
        dominant = ctx_report.get("dominant_signal", "")
        if interp:
            sentinel_section = (
                f"\nAGENT SENTINEL (contexte marché) :\n"
                f"  Signal dominant : {dominant} | Uplift demande : {uplift:+.0f}%\n"
                f"  {interp[:200]}"
            )

    # ── Stratège : actions prioritaires ──────────────────────────────────────
    stratege_txt = "\n".join(
        f"  {a.get('priorite', i+1)}. {a.get('action', '')} → {a.get('produit_cible', '')} "
        f"| {a.get('argument_vente', '')} | Impact : {a.get('impact_estime', '')}"
        for i, a in enumerate(actions[:4])
    ) or "  (en attente des recommandations du Stratège)"

    # ── RAG Milvus ────────────────────────────────────────────────────────────
    rag_lines = [
        f"  #{i} [{s['categorie']}] Score {s['score']:.2f}\n"
        f"     Situation : {s.get('situation','')[:100]}\n"
        f"     Action    : {s['action'][:120]}\n"
        f"     Argument  : {s.get('argument','')[:120]}"
        for i, s in enumerate(rag_scripts[:3], 1)
    ]
    rag_section = ("\nSCRIPTS TERRAIN ÉPROUVÉS (RAG Milvus — utilise ces arguments) :\n"
                   + "\n".join(rag_lines) + "\n") if rag_lines else ""

    # ── Profil conseiller ─────────────────────────────────────────────────────
    profile_section = ""
    if advisor_profile and (advisor_profile.get("strong") or advisor_profile.get("weak")):
        strong = ", ".join(advisor_profile["strong"].keys()) or "non défini"
        weak   = ", ".join(advisor_profile["weak"].keys())   or "non défini"
        acc    = advisor_profile.get("acceptance", 0.5)
        profile_section = (
            f"\nPROFIL {advisor_name} :\n"
            f"  Points forts : {strong} | À développer : {weak}\n"
            f"  Taux acceptation conseils : {acc:.0%} "
            f"({advisor_profile.get('followed',0)}/{advisor_profile.get('total_recos',0)} suivis)"
        )

    # ── Inventaire complet (si données disponibles) ────────────────────────────
    inv_section = ""
    if inv_ctx and inv_ctx.get("total_skus", 0) > 0:
        ruptures   = inv_ctx.get("ruptures", 0)
        critiques  = inv_ctx.get("critiques", 0)
        warnings   = inv_ctx.get("warnings", 0)
        ok_count   = inv_ctx.get("ok_count", 0)
        total_skus = inv_ctx.get("total_skus", 0)

        alert_lines = [
            f"  {'X' if it['risk_level']=='rupture' else '!'} {it['product_name']}"
            f" — {it['stock_qty']} u | J+{it.get('jours_rupture',0):.0f} rupture"
            f" | EOQ recommandé : {it.get('eoq','?')}"
            for it in inv_ctx.get("alert_items", [])[:12]
        ]
        ok_lines = [
            f"  OK {it['product_name']} — {it['stock_qty']} unités"
            for it in inv_ctx.get("ok_items", [])[:8]
        ]
        top_inv_lines = [
            f"  {t['nom']} — {t['total_sold']} vendus/7j | vel {t['vel_jour']}/j"
            f" | stock {t['stock_restant']}{' | STOCK BAS' if t['sous_point_commande'] else ''}"
            f" | rupture J+{t['jours_rupture']:.0f}"
            for t in inv_ctx.get("top_sellers", [])[:6]
        ]
        reco_lines = [
            f"  [{r['type']}] {r['product_name']} x{r['qty']}"
            f" — {r['texte'][:100]} ({r['confiance']:.0%})"
            for r in inv_ctx.get("agent_recos", [])[:4]
        ]
        aa_lines = [
            f"  [{a['severite'].upper()}] {a['product_name']}"
            f" — {a['message'][:80]} → {a['action'][:60]}"
            for a in inv_ctx.get("agent_alerts", [])[:4]
        ]

        kpi = inv_ctx.get("kpi", {})
        kpi_txt = ""
        if kpi and kpi.get("ca_jour", 0) > 0:
            kpi_txt = (
                f"\nKPI BOUTIQUE (agent_kpi_daily) :\n"
                f"  CA : {kpi['ca_jour']:,.0f} TND | Terminaux : {kpi['terminaux']}"
                f" | Forfaits : {kpi['forfaits']} | Postpayé : {kpi['postpaye']}"
                f" | {kpi['nb_agents']} agents | Moy/agent : {kpi['ca_moy_agent']:,.0f} TND\n"
                f"  Tendance 7j : moy {kpi['ca_7j_avg']:,.0f} TND/j"
                f" | {kpi['term_7j']} terminaux | {kpi['forc_7j']} forfaits"
            )

        tgt = inv_ctx.get("target", {})
        tgt_txt = ""
        if tgt and tgt.get("ca_cible_mensuel", 0) > 0:
            tgt_txt = (
                f"\nOBJECTIF MENSUEL (telco_targets_monthly) :\n"
                f"  CA cible : {tgt['ca_cible_mensuel']:,.0f} TND"
                f" | Terminaux : {tgt['terminaux_cible']}"
                f" | Activations : {tgt['activations_cible']}"
                f" | Facteur saison : {tgt['facteur_saison']:.2f}"
            )

        inv_section = f"""
{'='*60}
INVENTAIRE COMPLET (Agent Stock — temps réel) :
  Total : {total_skus} SKUs | X Ruptures : {ruptures} | ! Critiques : {critiques} | ~ Warnings : {warnings} | OK : {ok_count}

Produits en alerte :
{chr(10).join(alert_lines) if alert_lines else '  Aucun produit en alerte'}

Produits disponibles (>15 unités) :
{chr(10).join(ok_lines) if ok_lines else '  Stock suffisant partout'}

Top vendeurs avec vélocité et risque rupture :
{chr(10).join(top_inv_lines) if top_inv_lines else '  Données non disponibles'}

Décisions Agent Décision (ORDER / EXPEDITE) :
{chr(10).join(reco_lines) if reco_lines else '  Aucune recommandation active'}

Alertes Agent Analyse :
{chr(10).join(aa_lines) if aa_lines else '  Aucune alerte active'}
{kpi_txt}{tgt_txt}"""

    return f"""Tu es CoachAgent — l'IA de coaching temps réel d'Ooredoo Tunisie, boutique {store_id}.
Tu lis en direct les outputs de 5 agents : Analyste Ventes · Stratège · Agent Stock · RAG Terrain · Sentinel.
Tu combines expertise vente telecom ET gestion stock. Tu réponds en français naturel.

{'='*60}
SITUATION TEMPS RÉEL — {advisor_name} | {store_id}
{'='*60}
Objectif jour : {ca:,.0f} / {target:,.0f} TND → {verdict}
Transactions  : {nb_v} ventes | Gap restant : {gap:,.0f} TND | {hl}h disponibles
Urgence       : {urgency} | Météo : {weather}{f' | Cause gap : {cause}' if cause else ''}

AGENT ANALYSTE — Performance & Ventes :
  CA {ca:,.0f} / {target:,.0f} TND | {nb_v} ventes{f' | Prévision EOD : {forecast_eod:,.0f} TND' if (forecast_eod := sctx.get("forecast_eod",0)) else ''}{analyste_section}

  Top produits (7 jours) :
{top_txt}

POS TEMPS RÉEL — Dernières transactions :
{recent_txt}

AGENT STOCK — Alertes critiques :
{stock_alert_txt}

AGENT STRATÈGE — Plan d'action du jour :
{stratege_txt}
{sentinel_section}
{rag_section}{inv_section}{profile_section}
{'='*60}
CATALOGUE OFFICIEL OOREDOO TUNISIE (seuls prix autorisés — jamais d'invention) :
{catalog}
{'='*60}

INSTRUCTIONS DE RÉPONSE SELON LA QUESTION :

Salutation →
  Prénom + résumé situation en 1 phrase (basé sur données ci-dessus) + "Comment puis-je t'aider ?"

Bilan / recap →
  Résumé factuel structuré : CA réalisé vs objectif, nb ventes, top produit du jour, dernière tx + 1 conseil actionnable.
  RÈGLE COHÉRENCE ABSOLUE : si CA > 0, il y a eu des ventes — jamais "0 ventes" si CA = {ca:,.0f} TND.

Script / technique de vente →
  Génère l'outil demandé avec vrais produits catalogue + arguments RAG si disponibles + recommandations Stratège.

Question stock / inventaire →
  Utilise données Agent Stock ci-dessus. Lie toujours au CA : "X en rupture = Y TND de CA en risque."

Cross-domaine (ventes + stock) →
  Réponds en liant les deux domaines. Ex : "Flexi 25Go est 3ème en ventes mais stock bas — pousse-le maintenant avant rupture."

Hors-sujet →
  "Je suis ton coach Ooredoo — je me concentre sur la vente telecom et le stock. Comment puis-je t'aider ?"

RÈGLES ABSOLUES :
1. Données ci-dessus UNIQUEMENT — jamais de chiffres inventés ou hors catalogue
2. COHÉRENCE : CA={ca:,.0f} TND avec {nb_v} ventes — ces deux chiffres sont liés, ne les contredis jamais
3. Français, tutoiement direct, ton de coach terrain — pas un robot corporate
4. 160 mots max — chaque phrase doit mériter sa place
5. Termine TOUJOURS par "Vas-y !" / "Maintenant !" / "À toi !" / "Allez !"
6. JAMAIS "je n'ai pas accès", "consulte le tableau de bord", "je ne peux pas voir"
7. JAMAIS de JSON, de code, de balises HTML, de "Here is", de "Based on"
8. JAMAIS deux chiffres contradictoires dans la même réponse"""


# ══════════════════════════════════════════════════════════════════════════════
# 5. LLM CALL — avec historique conversation (multi-tours)
# ══════════════════════════════════════════════════════════════════════════════

async def _call_llm(
    system:      str,
    user_msg:    str,
    max_tokens:  int   = 400,
    temperature: float = 0.22,
    history:     list  = None,
) -> tuple[str, float]:
    t0 = time.time()
    if not OPENROUTER_KEY:
        logger.error("[COACH] OPENROUTER_API_KEY manquante !")
        return "", 0.0
    try:
        import httpx
        messages = [{"role": "system", "content": system}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_msg})

        body    = json.dumps({
            "model": OPENROUTER_MODEL, "max_tokens": max_tokens,
            "temperature": temperature, "messages": messages,
        }, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {OPENROUTER_KEY}",
            "Content-Type":  "application/json; charset=utf-8",
            "HTTP-Referer":  "https://github.com/MALEKALADAB11/multi-agent-sales-inventory",
            "X-Title":       "AI Sales Coach Ooredoo v9",
        }
        async with httpx.AsyncClient(timeout=35) as client:
            resp = await client.post(OPENROUTER_URL, headers=headers, content=body)
        data = resp.json()
        if "error" in data:
            logger.warning(f"[COACH LLM] {data['error'].get('message','')[:100]}")
            return "", (time.time() - t0) * 1000

        reply = data["choices"][0]["message"]["content"].strip()

        # Nettoyer préfixes LLM parasites
        for pfx in ["Reponse:", "Coach:", "CoachAgent:", "Here is",
                     "Based on", "REPONSE DU COACH :", "RESPONSE:"]:
            if reply.lower().startswith(pfx.lower()):
                reply = reply[len(pfx):].strip()
        # Nettoyer réponse JSON parasite
        if reply.startswith("{"):
            m = re.search(r'"(?:reply|response|conseil|text)"\s*:\s*"([^"]+)"', reply)
            reply = m.group(1).strip() if m else ""

        ms = (time.time() - t0) * 1000
        logger.info(f"[COACH LLM] {len(reply)}c | {ms:.0f}ms | hist={len(history or [])//2} | {OPENROUTER_MODEL}")
        return reply, ms
    except Exception as e:
        logger.warning(f"[COACH LLM] {str(e)[:120]}")
        return "", (time.time() - t0) * 1000


# ══════════════════════════════════════════════════════════════════════════════
# 6. MAIN ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/chat")
async def coach_chat(request: dict):
    t0 = time.time()

    message      = (request.get("message") or "").strip()
    advisor_name = request.get("advisor_name") or "Conseiller"
    store_id     = _normalize_store(request.get("store_id") or "I63")
    ctx          = request.get("context") or {}
    logger.info(f"[COACH v9] store={store_id!r} advisor={advisor_name!r} msg='{message[:60]}'")

    if not message:
        return JSONResponse({"reply": "", "domain": "none", "mode": "empty",
                             "rag_used": False, "confidence": 0, "sources": []})

    # ── 1. Intent classification ──────────────────────────────────────────────
    intent      = _classify_intent(message)
    mode        = intent["mode"]    # greeting|conversation|coaching|inventory|cross_domain|off_topic
    domain      = intent["domain"]  # sales|inventory|both|none
    qtype       = intent["type"]
    intent_conf = intent["confidence"]
    logger.info(f"[COACH v9] mode={mode} domain={domain} type={qtype} conf={intent_conf:.2f}")

    # ── Off-topic guard ───────────────────────────────────────────────────────
    if mode == "off_topic":
        logger.info(f"[COACH v9] OFF-TOPIC bloqué : '{message[:60]}'")
        return JSONResponse({
            "reply": ("Je suis ton coach Ooredoo — spécialisé en vente telecom, "
                      "gestion de stock et objectifs commerciaux. "
                      "Pose-moi une question sur ces sujets et je t'aide tout de suite !"),
            "mode": "off_topic", "domain": "none", "question_type": "off_topic",
            "source": "guardrail", "model": "", "confidence": 1.0,
            "rag_used": False, "nb_rag_scripts": 0, "latency_ms": 0,
            "sources": [], "context_used": {"advisor": advisor_name, "store_id": store_id},
            "rag_scripts": [], "inventory_alerts": [], "agent_recos": [],
        })

    # ── 2. Fast path salutation — sans LLM (< 5ms) ────────────────────────────
    if mode == "greeting":
        perf_ctx = float(ctx.get("performance") or ctx.get("gap_pct") or 0)
        if perf_ctx:
            gr = (f"Bonjour {advisor_name} ! "
                  f"Prêt à faire avancer l'objectif du jour ? Comment puis-je t'aider ?")
        else:
            gr = (f"Bonjour {advisor_name} ! Je suis ton coach Ooredoo — "
                  "demande-moi un script de vente, une stratégie ou l'état du stock. "
                  "Comment puis-je t'aider ?")
        return JSONResponse({
            "reply": gr, "mode": "greeting", "domain": "sales",
            "question_type": "greeting", "source": "fast_path", "model": "none",
            "confidence": 0.99, "rag_used": False, "nb_rag_scripts": 0,
            "latency_ms": round((time.time() - t0) * 1000),
            "sources": [], "context_used": {"advisor": advisor_name, "store_id": store_id},
            "rag_scripts": [], "inventory_alerts": [], "agent_recos": [],
        })

    # ── 3. Chargement parallèle de TOUS les agents ────────────────────────────
    sctx, inv_ctx, day_history = await asyncio.gather(
        _load_sales_context(store_id, ctx),
        _load_inventory_context(store_id),
        _load_day_history(advisor_name, store_id),
    )
    urgency = sctx.get("urgency", "MEDIUM")

    # Profil conseiller + catalogue en parallèle (non bloquants)
    advisor_profile, catalog = await asyncio.gather(
        asyncio.get_event_loop().run_in_executor(
            None, _load_advisor_profile_sync, advisor_name, store_id),
        asyncio.get_event_loop().run_in_executor(
            None, _load_catalog, store_id),
    )

    # ── 4. RAG — Agent Knowledge (Milvus) ─────────────────────────────────────
    rag_scripts, rag_relevant = [], False
    should_rag = (
        mode in ("coaching", "cross_domain") or
        qtype in ("script", "objection", "closing", "upsell", "forfait", "objectif") or
        any(kw in message.lower() for kw in [
            "iphone", "samsung", "airpods", "watch", "forfait", "assurance",
            "bundle", "script", "objection", "closing", "5g", "comment vendre",
        ])
    )
    if should_rag:
        rag_query = f"{message} {qtype} telecom vente Ooredoo"
        if sctx.get("gap_pct", 0) > 40:
            rag_query += " gap critique urgent"
        if "pluie" in sctx.get("weather", "").lower():
            rag_query += " pluie accessoires résistants eau"
        rag_scripts, rag_relevant = _search_rag(
            rag_query, sctx.get("hour", 12), top_k=3, min_score=0.30)

    # ── 5. Prompt maître unifié ───────────────────────────────────────────────
    system_prompt = _build_master_prompt(
        advisor_name, store_id, sctx, rag_scripts, inv_ctx, catalog, advisor_profile
    )

    # Tokens adaptatifs selon mode + urgence
    if qtype == "recap":
        max_tokens, temperature = 300, 0.22
    elif mode == "coaching" or qtype in ("script", "objectif", "objection", "closing"):
        max_tokens  = 350 if urgency in ("HIGH", "CRITICAL") else 450
        temperature = 0.18
    elif domain in ("inventory", "both"):
        max_tokens, temperature = 420, 0.20
    else:
        max_tokens, temperature = 380, 0.25

    # ── 6. LLM Call ───────────────────────────────────────────────────────────
    reply, llm_ms = await _call_llm(
        system_prompt, message, max_tokens, temperature, history=day_history
    )

    # ── 7. Sources actives ────────────────────────────────────────────────────
    sources = [
        {"id": "pos",      "label": "POS temps réel",   "active": sctx.get("ca_today", 0) > 0},
        {"id": "analyste", "label": "Agent Analyste",   "active": bool(sctx.get("top_sellers"))},
        {"id": "stratege", "label": "Agent Stratège",   "active": bool(sctx.get("actions"))},
        {"id": "stock",    "label": "Agent Stock",      "active": inv_ctx.get("total_skus", 0) > 0},
        {"id": "decision", "label": "Agent Décision",   "active": bool(inv_ctx.get("agent_recos"))},
        {"id": "kpi",      "label": "Agent KPI",        "active": bool(inv_ctx.get("kpi", {}).get("ca_jour", 0))},
        {"id": "supply",   "label": "Supply Chain",     "active": any(it.get("eoq", 0) > 0 for it in inv_ctx.get("alert_items", []))},
        {"id": "rag",      "label": "RAG Milvus",       "active": rag_relevant, "nb_scripts": len(rag_scripts)},
        {"id": "history",  "label": f"Hist. ({len(day_history)//2} échanges)", "active": bool(day_history)},
        {"id": "profile",  "label": "Profil conseiller","active": bool(advisor_profile.get("strong"))},
    ]

    # ── 8. Confidence + fallback ──────────────────────────────────────────────
    if reply:
        model_used    = OPENROUTER_MODEL
        active_agents = sum(1 for s in sources if s["active"])
        confidence    = min(0.96, 0.70 + active_agents * 0.03)
        source_label  = f"multi_agent+{'rag+' if rag_relevant else ''}llm_v9"
    else:
        model_used   = "fallback"
        confidence   = 0.55
        source_label = "fallback"
        perf         = sctx.get("performance", 0)
        crit         = inv_ctx.get("critical_items", [])

        if crit:
            names = ", ".join(c["product_name"] for c in crit[:2])
            reply = (f"Alerte stock : {names} en rupture/critique. "
                     f"CA : {sctx.get('ca_today', 0):,.0f} TND ({perf:.0f}%). "
                     "Focus bundle terminal + forfait + assurance. À toi !")
        elif rag_scripts:
            s = rag_scripts[0]
            reply = f"{s['action']}\n{s.get('argument','')}\nImpact : {s['impact']}\nVas-y !"
        else:
            actions = sctx.get("actions", [])
            if actions:
                a = actions[0]
                reply = (f"{perf:.0f}% de l'objectif. "
                         f"Action prioritaire : {a.get('action','')} → {a.get('produit_cible','')}. "
                         f"Argument : {a.get('argument_vente','')}. Vas-y !")
            else:
                reply = (f"{perf:.0f}% de l'objectif. "
                         "Assurance Premium 9 TND/mois sur chaque terminal — marge 80%. À toi !")

    total_ms = (time.time() - t0) * 1000

    # ── Traces Langfuse ───────────────────────────────────────────────────────
    try:
        from langfuse_observer import trace_coach_chat
        trace_coach_chat(
            store_id=store_id, advisor_name=advisor_name,
            message=message, response=reply,
            question_type=f"{domain}/{mode}/{qtype}",
            rag_used=rag_relevant, nb_rag_scripts=len(rag_scripts),
            confidence=confidence, latency_ms=total_ms,
            gap_pct=sctx.get("gap_pct", 0), urgency=urgency,
        )
    except Exception:
        pass

    # ── Persistance interaction ───────────────────────────────────────────────
    try:
        from modules.coaching.agents.coach.tools import save_interaction
        save_interaction(
            advisor_name=advisor_name, store_id=store_id,
            message=message, response=reply,
            gap_pct=sctx.get("gap_pct", 0), urgency=urgency,
            rag_used=rag_relevant, nb_rag_scripts=len(rag_scripts),
            conseil_type=f"{domain}/{qtype}", confidence=confidence,
        )
    except Exception:
        pass

    logger.info(
        f"[COACH v9] {mode}/{domain}/{qtype} | {advisor_name} | "
        f"RAG={'Y' if rag_relevant else 'N'}({len(rag_scripts)}) | "
        f"conf={confidence:.2f} | llm={llm_ms:.0f}ms | total={total_ms:.0f}ms"
    )

    # ── Response ──────────────────────────────────────────────────────────────
    return JSONResponse({
        "reply":          reply,
        "mode":           mode,
        "domain":         domain,
        "question_type":  qtype,
        "source":         source_label,
        "model":          model_used,
        "confidence":     confidence,
        "rag_used":       rag_relevant,
        "nb_rag_scripts": len(rag_scripts),
        "latency_ms":     round(total_ms),
        "sources": [s for s in sources if s.get("active")],
        "context_used": {
            "advisor":       advisor_name,
            "store_id":      store_id,
            "domain":        domain,
            "mode":          mode,
            "hour":          datetime.now().hour,
            "ca_today":      sctx.get("ca_today", 0),
            "gap_pct":       sctx.get("gap_pct", 0),
            "performance":   sctx.get("performance", 0),
            "urgency":       urgency,
            "total_skus":    inv_ctx.get("total_skus", 0),
            "ruptures":      inv_ctx.get("ruptures", 0),
            "kpi_terminaux": inv_ctx.get("kpi", {}).get("terminaux", 0),
            "kpi_forfaits":  inv_ctx.get("kpi", {}).get("forfaits", 0),
            "ca_moy_agent":  inv_ctx.get("kpi", {}).get("ca_moy_agent", 0),
        },
        "rag_scripts": [
            {"categorie": s["categorie"], "action": s["action"], "score": s["score"]}
            for s in rag_scripts[:2]
        ] if rag_scripts else [],
        "inventory_alerts": [
            {
                "sku":           c["sku"],
                "product":       c["product_name"],
                "qty":           c["stock_qty"],
                "level":         c["risk_level"],
                "jours_rupture": c.get("jours_rupture", 0),
                "eoq":           c.get("eoq", 0),
            }
            for c in inv_ctx.get("critical_items", [])[:5]
        ],
        "agent_recos": [
            {
                "type":       r["type"],
                "product":    r["product_name"],
                "qty":        r["qty"],
                "confidence": r["confiance"],
            }
            for r in inv_ctx.get("agent_recos", [])[:3]
        ],
    })


@router.get("/health")
async def coach_health():
    return JSONResponse({
        "status":       "ok",
        "version":      "9.0.0",
        "architecture": "multi-agent-unified-prompt",
        "modes": ["greeting", "conversation", "coaching", "inventory", "cross_domain"],
        "new_in_v9": [
            "dynamic_catalog_from_db_ttl10min",
            "store_id_normalization",
            "greeting_fast_path_no_llm",
            "recap_bilan_mode",
            "cross_domain_routing",
            "inventory_agent_recos_decision",
            "supply_chain_eoq_point_commande",
            "agent_kpi_daily_terminaux_forfaits",
            "telco_targets_monthly",
            "advisor_profile_strong_weak",
            "conversation_history_multi_turn",
            "parallel_context_loading_asyncio_gather",
            "unified_master_prompt_all_agents",
            "multi_agent_confidence_scoring",
            "enriched_fallback_real_data",
        ],
        "llm":           OPENROUTER_MODEL,
        "llm_available": bool(OPENROUTER_KEY),
        "rag_collection": COLLECTION,
        "embed_dim":     EMBED_DIM,
    })
