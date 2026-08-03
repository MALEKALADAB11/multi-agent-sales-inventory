"""

coach_chat.py — Coach Chat v11.0 (Production)
=============================================================
Philosophie : un coach qui PENSE, ancré dans les données réelles.

ARCHITECTURE :
  1. PERSONA-FIRST     — identité forte + méthode + few-shots dans le prompt système
  2. STRATÈGE CÂBLÉ    — agent Stratège (LangGraph : météo réelle, fériés, promos,
                         RAG, self-critique) invoqué serveur-side via l'orchestrateur
                         résilient (cache 30 min, timeout borné, warm en arrière-plan)
  3. RAG UNIFIÉ        — retriever partagé Milvus + fallback lexical corpus embarqué
                         (le RAG ne tombe jamais à vide si Milvus/Ollama est down)
  4. CONTEXT SELECTIF  — seules les données utiles à CETTE question sont injectées
  5. DEDUP CACHE 20s   — évite les réponses identiques sur double envoi rapide
  6. RETRY 4 NIVEAUX   — Mistral → OpenRouter full → OpenRouter stripped → Ollama
                         → fallback intent
  7. FALLBACK COHÉRENT — jamais d'alertes stock pour "bonjour"
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
from app.core.config import DEFAULT_STORE_ID, config
from app.core.http import get_http_client

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

OLLAMA_URL       = config.OLLAMA_BASE_URL

# ── Groq (rotation multi-clés — primaire si configuré) ──────────────────────
# GROQ_API_KEYS=clef1,clef2,... : quand une clé est épuisée (429/quota) ou
# invalide, la suivante prend le relais. GROQ_API_KEY (singulier) reste
# supporté et est fusionné en tête de liste.
GROQ_URL  = "https://api.groq.com/openai/v1/chat/completions"
GROQ_KEYS = list(dict.fromkeys(
    k.strip()
    for k in ([os.getenv("GROQ_API_KEY") or ""] + (os.getenv("GROQ_API_KEYS") or "").split(","))
    if k.strip()
))
_GROQ_MODEL_ROTATION = [
    os.getenv("GROQ_MODEL",          "openai/gpt-oss-120b"),
    os.getenv("GROQ_MODEL_FALLBACK", "llama-3.3-70b-versatile"),
]

OPENROUTER_KEY   = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-nano-30b-a3b:free")
OPENROUTER_URL   = "https://openrouter.ai/api/v1/chat/completions"

# nemotron-3-nano est un modèle de RAISONNEMENT : laissé libre, il consomme tout
# le budget de tokens en chaîne de pensée anglaise qui finit dans `content`
# (mesuré : 61 s et zéro réponse exploitable). Le coach n'a rien à raisonner —
# le contexte est déjà construit côté serveur — il n'a qu'à rédiger.
# Mesures sur la même requête : sans réglage 6,0 s, exclude 6,9 s, désactivé 2,3 s.
_NO_REASONING = {"reasoning": {"enabled": False}}

# nano-30b est le modèle du coach : 256k de contexte, latence basse, suffisant
# pour des réponses courtes ancrées dans un contexte déjà construit côté serveur.
# Les modèles suivants ne servent qu'en cas de 429/503 sur le quota gratuit.
_OR_MODEL_ROTATION = [
    os.getenv("OPENROUTER_MODEL",          "nvidia/nemotron-3-nano-30b-a3b:free"),
    os.getenv("OPENROUTER_MODEL_FALLBACK", "nvidia/nemotron-3-super-120b-a12b:free"),
    "openai/gpt-oss-120b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
]
# Attente max du Stratège dans le flux chat (cache 30 min → généralement <1ms ;
# cold start → le run continue en arrière-plan et chauffe le cache).
COACH_STRATEGE_TIMEOUT = float(os.getenv("COACH_STRATEGE_TIMEOUT", "10"))

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

# Aucun prix en dur ici. L'ancien fallback annonçait « iPhone 16 Pro 1 299 TND »
# (réel : 6 349 TND) sous l'intitulé « seuls prix autorisés » : une panne de base
# transformait le coach en menteur confiant. Mieux vaut qu'il dise qu'il ne sait pas.
_CATALOG_FALLBACK = (
    "Catalogue indisponible (base de données injoignable).\n"
    "N'annonce AUCUN prix ni SKU tant que tu n'as pas de fiche produit [P*] : "
    "dis au conseiller que tu ne peux pas confirmer les tarifs pour l'instant."
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
        # Pas de ligne « Bundle max » : elle était codée en dur à 1 357 TND alors
        # que l'iPhone 16 Pro coûte 6 349 TND en base. Sous un titre « seuls prix
        # autorisés », le coach la citait comme un fait — et en dérivait des
        # mensualités inventées (54 TND/mois).

        result = "\n".join(lines) if lines else _CATALOG_FALLBACK
        _catalog_cache, _catalog_cache_ts = result, _t.time()
        return result
    except Exception as e:
        logger.warning("[COACH CATALOG] %.80s", str(e))
        return _CATALOG_FALLBACK

# ══════════════════════════════════════════════════════════════════════════════
# PERSONA SYSTÈME v11 — identité + méthode + ancrage données + few-shots
# ══════════════════════════════════════════════════════════════════════════════

def _build_system_prompt(catalog: str) -> str:
    return f"""Tu es COACH, l'IA de coaching commercial des boutiques Ooredoo Tunisie.
Tu épaules le conseiller de vente en temps réel : scripts, objections, closing, upsell, conversion forfait, objectif du jour, stock. Tu penses comme un directeur commercial senior : tu lis la situation en un coup d'œil, tu choisis UNE action à fort impact, tu donnes l'argument exact, tu pousses au close.

MÉTHODE (raisonne dans cet ordre, sans jamais l'afficher) :
1. DIAGNOSTIC — que dit le bloc SITUATION ? (gap, heure, météo, stock, profil du conseiller)
2. PRIORITÉ — l'action unique au meilleur ratio impact/temps MAINTENANT. Les AGENTS et les DOCUMENTS RÉCUPÉRÉS priment sur toute idée générique.
3. PREUVE — chiffres réels de la SITUATION, des AGENTS et des fiches produit [P*], rien d'autre.
4. CLOSE — termine par une action immédiate et un encouragement.

HIÉRARCHIE DES SOURCES (en cas de contradiction, la plus haute gagne) :
1. CE QUE LES AGENTS DISENT MAINTENANT — état vivant, relu à chaque message
2. Les chiffres du bloc SITUATION (CA, gap, stock, commandes en cours)
3. Les fiches produit [P*] — prix et marges officiels du catalogue
4. Les scripts [S*], playbooks [I*] et décisions passées [D*] — méthode, pas données
Une alerte de l'AGENT DÉCISION sur un produit prime sur une fiche produit qui le dit en stock : le stock a pu bouger depuis l'indexation.

ANCRAGE DONNÉES (non négociable) :
• Tout chiffre (prix, stock, CA, %) vient de la SITUATION, des AGENTS ou d'une fiche [P*] — jamais de ta mémoire
• Quand tu t'appuies sur un document récupéré, cite sa référence entre crochets : « comme sur [S2] », « le playbook [I1] dit ». Une affirmation sans source vérifiable n'a rien à faire dans ta réponse
• Un SKU, un prix ou une référence produit ne s'écrit QUE s'il figure dans une fiche [P*] ci-dessous. Pas de fiche produit ? Nomme le produit sans SKU ni prix, ou dis que tu dois vérifier
• Ne jamais associer le nom d'un produit au SKU ou au prix d'un autre : chaque fiche [P*] forme un tout indissociable
• Aucun document récupéré, ou aucun qui réponde ? Dis-le en une demi-phrase et réponds avec la SITUATION seule. Ne comble jamais un trou par une invention
• Les ACTIONS STRATÈGE sont calculées sur la météo, le stock et les promos réels du jour — appuie-toi dessus en priorité
• Les scripts [S*] sont des ventes réellement réussies en boutique — reprends leurs arguments, adapte-les au contexte
• Ne recommande jamais un produit signalé en rupture par les AGENTS, même si un script le mentionne
• Aucun client, scénario ou situation inventé : si le message du conseiller ne décrit pas de client réel, tu n'en crées pas un

TON STYLE :
• Tutoiement chaleureux — tu es le collègue de confiance du conseiller
• Décisif — 1 action concrète (2 max si urgence CRITICAL), jamais un menu d'options
• Court — 80 à 140 mots ; script complet demandé = jusqu'à 180 mots, étapes numérotées
• Termine toujours par "Vas-y !", "À toi !", "Allez !" ou "Maintenant !"

JAMAIS :
• Inventer un prix, une offre ou un SKU hors catalogue ci-dessous
• Répondre hors vente / stock / Ooredoo Tunisie
• Écrire "je n'ai pas accès" ou "consulte le tableau de bord"
• Produire du JSON, du code, de l'anglais, des balises HTML
• Répéter deux fois la même information dans la même réponse

SI LE MESSAGE EST AMBIGU ou ne correspond à aucune situation de vente/stock réelle : NE PAS halluciner un client ou un script. Demande une clarification courte en 1 phrase, du style [clarification] ci-dessous.

CATALOGUE OFFICIEL OOREDOO (seuls prix autorisés) :
{catalog}

EXEMPLES DE RÉPONSES PARFAITES — les {{accolades}} sont des trous à remplir avec
les données réelles de la SITUATION, des AGENTS ou d'une fiche [P*]. Un chiffre
écrit en dur dans un exemple ci-dessous n'existe pas : ne le recopie JAMAIS.
[salutation] → «Salut {{prenom}} ! {{perf}}% de l'objectif à {{heure}}h — {{observation en 5 mots}}. Tu veux qu'on attaque le gap ou tu as une situation client ?»
[script produit] → «Script {{produit}} : 1. "Tu l'utilises pour quoi surtout ?" 2. Démo live {{atout technique de la fiche}}. 3. "{{prix}} TND, ou {{prix/24}} TND/mois sur 24 mois." 4. Bundle {{accessoire en stock}}. 5. Close : "{{couleur A}} ou {{couleur B}} ?" Vas-y !»
[objection trop cher] → «Réponds : "{{prix}} TND sur 24 mois = {{prix/24}} TND/mois — moins qu'un café par jour." [S{{n}}] Close : "{{choix binaire}}" À toi !»
[bilan du jour] → «CA {{ca}} / {{target}} TND ({{perf}}%). Top : {{produit}} ({{qty}} vendus/7j). Dernière vente {{heure}}h. {{conseil Stratège en 1 ligne}}. Allez !»
[question stratégie — actions Stratège fournies] → «Le Stratège a détecté {{cause en 5 mots}}. Priorité : {{action 1}} → {{produit}} ({{prix}} TND). Argument : "{{argument_vente}}". {{impact estimé}}. Vas-y !»
[rupture stock] → «{{produit}} en rupture = {{nb}} ventes perdues en risque. Met le {{substitut cité dans une fiche [P*]}} ({{son prix}} TND) en avant + appelle le manager pour la commande. Maintenant !»
[closing] → «Choix forcé : "Lequel vous correspond le mieux, {{option A}} ou {{option B}} ?" Si résistance : "Il nous reste {{stock}} unités." Silence 3 secondes. À toi !»
[donnée absente] → «Je n'ai pas le détail {{donnée}} sous la main, mais voilà ce que je vois : {{ce que la SITUATION donne}}. {{action}}. Allez !»
[question prix — fiche produit récupérée] → «{{produit}} : {{prix}} TND TTC, marge {{marge}}% [P1]. Il en reste {{stock}}. Argument qui marche [S1] : "{{argument}}". Vas-y !»
[rupture signalée par un agent] → «L'agent décision a levé une alerte rupture sur {{produit}}. Le playbook [I1] est clair : propose {{substitut}} ({{prix}} TND [P2]) en argumentant sur l'usage, pas la marque. Maintenant !»
[aucun document pertinent] → «Rien de précis en base sur ce point. Ce que je vois : {{données SITUATION}}. {{action}}. Allez !»
[clarification — question incompréhensible ou sans client réel décrit] → «Je ne suis pas sûr de te suivre — tu veux un script de vente, un point sur le stock, ou un coup de main pour ton objectif du jour ? Dis-m'en un peu plus !»"""

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
    """Stock complet : stats + alertes + top vendeurs + recos agent + KPI
    + commandes fournisseur en cours + décisions humaines récentes."""
    import psycopg2, psycopg2.extras
    r = {"stats": {}, "alerts": [], "top_sellers": [], "agent_recos": [],
         "kpi_terminaux": 0, "kpi_forfaits": 0, "ca_moy_agent": 0,
         "pos": [], "decisions": []}
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
            # NOTE (demand-sensing integration): rp.demande_moy_jour used to be the
            # velocity source here, but supply.reorder_params has no active
            # population script anymore (dead table). The velocity signal now
            # comes from inventory.demand_forecast, populated by the baseline
            # (run_baseline_batch.py) + xgboost correction (run_sensing_job.py)
            # pipeline. Same COALESCE(corrected_demand, baseline_demand, demand_24h)
            # priority used everywhere else this table is read (see
            # stock_tools.get_forecast_data / InventoryRepo.get_forecast_range).
            # demand_forecast has one row per (sku, store, forecast_date), so a
            # LATERAL join picks the nearest upcoming forecast per sku instead of
            # fanning out rows. rp is kept only for eoq, which the sensing
            # pipeline doesn't produce.
            cur.execute("""
                SELECT sl.sku,
                       COALESCE(p.nom, sl.sku::text) AS nom,
                       COALESCE(sl.quantity_available, sl.quantity, 0) AS qty,
                       CASE WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 0 THEN 'rupture'
                            WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 5  THEN 'critical'
                            ELSE 'warning' END AS level,
                       COALESCE(fc.demand, 0.5) AS vel,
                       COALESCE(rp.eoq, 0) AS eoq
                FROM inventory.stock_levels sl
                LEFT JOIN sales.produits p ON p.sku = sl.sku
                LEFT JOIN supply.reorder_params rp ON rp.sku = sl.sku AND rp.store_id = sl.store_id
                LEFT JOIN LATERAL (
                    SELECT COALESCE(df.corrected_demand, df.baseline_demand, df.demand_24h) AS demand
                    FROM inventory.demand_forecast df
                    WHERE df.sku = sl.sku AND df.store_id = sl.store_id
                      AND df.forecast_date >= CURRENT_DATE
                    ORDER BY df.forecast_date ASC
                    LIMIT 1
                ) fc ON TRUE
                WHERE sl.store_id=%s AND COALESCE(sl.quantity_available, sl.quantity, 0) <= 15
                ORDER BY COALESCE(sl.quantity_available, sl.quantity, 0) ASC LIMIT 6
            """, (store_id,))
            for row in cur.fetchall():
                stock = int(row["qty"] or 0)
                vel   = float(row["vel"] or 0.5)
                jours = round(stock / vel, 0) if vel > 0 and stock > 0 else 0
                r["alerts"].append({
                    "sku": row["sku"], "nom": row["nom"], "qty": stock,
                    "level": row["level"], "jours": jours, "eoq": int(row["eoq"] or 0),
                })

            # Top vendeurs avec vélocité demand-sensing (voir NOTE ci-dessus —
            # même remplacement de rp.demande_moy_jour par inventory.demand_forecast).
            # Fallback inchangé quand aucun forecast n'existe encore pour ce sku:
            # moyenne des ventes réelles sur 7 jours.
            cur.execute("""
                SELECT COALESCE(p.nom, t.sku::text) AS nom,
                       SUM(t.quantity) AS sold,
                       COALESCE(sl.quantity_available, sl.quantity, 0) AS stock,
                       COALESCE(fc.demand, SUM(t.quantity)::NUMERIC/7) AS vel
                FROM   sales.transactions t
                LEFT JOIN sales.produits p ON p.sku=t.sku
                LEFT JOIN inventory.stock_levels sl ON sl.sku=t.sku AND sl.store_id=t.store_id
                LEFT JOIN LATERAL (
                    SELECT COALESCE(df.corrected_demand, df.baseline_demand, df.demand_24h) AS demand
                    FROM inventory.demand_forecast df
                    WHERE df.sku = t.sku AND df.store_id = t.store_id
                      AND df.forecast_date >= CURRENT_DATE
                    ORDER BY df.forecast_date ASC
                    LIMIT 1
                ) fc ON TRUE
                WHERE  t.store_id=%s
                  AND  t.date_only>=(
                         SELECT MAX(date_only) FROM sales.transactions WHERE store_id=%s
                       ) - INTERVAL '7 days'
                GROUP BY p.nom, t.sku, sl.quantity_available, sl.quantity, fc.demand
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

            # Commandes fournisseur vivantes (Kanban) — le coach doit savoir
            # qu'une rupture a déjà sa commande en route avant de conseiller
            # d'en repasser une (anti-hallucination / anti-conseil périmé).
            cur.execute("""
                SELECT COALESCE(p.nom, po.sku::text) AS nom, po.statut,
                       po.quantite_commandee AS qty, po.source,
                       po.date_livraison_prevue AS eta
                FROM supply.purchase_orders po
                LEFT JOIN sales.produits p ON p.sku = po.sku
                WHERE po.store_id=%s AND po.statut NOT IN ('RECU','ANNULE')
                ORDER BY po.updated_at DESC LIMIT 5
            """, (store_id,))
            r["pos"] = [
                {"nom": row["nom"], "statut": row["statut"],
                 "qty": int(row["qty"] or 0), "source": row["source"],
                 "eta": str(row["eta"]) if row["eta"] else None}
                for row in cur.fetchall()
            ]

            # Décisions humaines récentes (boucle feedback 0008) — approbations
            # et rejets sur recommandations, PO et incitations des 7 derniers
            # jours, pour que le coach reflète les derniers arbitrages.
            cur.execute("""
                SELECT af.source, af.decision, af.action_type, af.reason,
                       COALESCE(p.nom, af.sku::text) AS nom
                FROM public.agent_feedback af
                LEFT JOIN sales.produits p ON p.sku = af.sku
                WHERE af.store_id=%s
                  AND af.created_at >= NOW() - INTERVAL '7 days'
                ORDER BY af.created_at DESC LIMIT 5
            """, (store_id,))
            r["decisions"] = [
                {"source": row["source"], "decision": row["decision"],
                 "action": row["action_type"], "nom": row["nom"],
                 "reason": (row["reason"] or "")[:80]}
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

            # CA moyen agent depuis advisor_profile — la vue a été supprimée
            # lors du nettoyage DB : to_regclass évite d'avorter la connexion
            # (une erreur ici mettait psycopg2 en état aborted et logguait un
            # warning à chaque message du coach).
            cur.execute("SELECT to_regclass('monitoring.advisor_profile') AS v")
            if (cur.fetchone() or {}).get("v"):
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
# 3. RAG (retriever partagé — Milvus sémantique + fallback lexical corpus)
# ══════════════════════════════════════════════════════════════════════════════

def _search_rag_sync(
    query: str,
    hour: int,
    store_id: str = "",
    top_k: int = 4,
    out_of_stock: frozenset[str] = frozenset(),
) -> tuple[list, bool, str]:
    """
    Retrieval cross-domaine : scripts de vente, playbooks stock, fiches produit
    (prix et marges réels) et mémoire des décisions, dans une seule recherche.

    Retourne (docs, pertinent, bloc_cité). Le bloc porte des références [S1] [I2]
    [P3] [D4] que le prompt système oblige le coach à citer.
    """
    try:
        from app.sales.data.rag import format_context_block, retrieve
        result = retrieve(query, store_id=store_id, hour=hour, top_k=top_k,
                          out_of_stock_skus=out_of_stock)
        if result.mode == "none":
            logger.warning("[COACH RAG] aucune branche de recherche disponible")
        return result.docs, result.relevant, format_context_block(result)
    except Exception as e:
        logger.warning("[COACH RAG] %.100s", str(e))
        return [], False, ""


def _out_of_stock(inv_ctx: dict) -> frozenset[str]:
    """SKU en rupture — le RAG ne doit jamais proposer un produit invendable."""
    return frozenset(
        str(a["sku"]) for a in inv_ctx.get("alerts", [])
        if a.get("level") == "rupture" and a.get("sku") is not None
    )


# Mots trop génériques pour identifier le produit que le conseiller a nommé.
_PRODUCT_STOPWORDS = {"portable", "pack", "option", "carte", "contrat", "gsm",
                      "dual", "sim", "noir", "blanc"}


def _names_product(message: str, title: str) -> bool:
    """
    Le conseiller a-t-il nommé CE produit précis ?

    On exige deux tokens distinctifs communs plutôt qu'un seuil de similarité :
    noyée dans une phrase entière, une fiche produit retombe à 0,50 de cosinus et
    « Galaxy A25 » devient indiscernable de « Galaxy S25 ». Le nom, lui, ne ment pas.
    """
    # _norm() retire les accents mais ne met pas en minuscules : les noms du
    # catalogue sont en capitales, d'où le .lower() explicite.
    msg  = set(re.findall(r"[a-z0-9]{3,}", _norm(message.lower())))
    name = set(re.findall(r"[a-z0-9]{2,}", _norm(title.lower()))) - _PRODUCT_STOPWORDS
    return len(msg & name) >= 2


# Candidats substituts : même gamme (`gamme_libelle` sépare TERMINAL de SIM_KIT,
# là où le code `famille` range les téléphones avec les kits SIM à 2 TND), même
# segment de prix, et stock réel > 0.
#
# Le segment n'est pas un seuil codé en dur : c'est le quartile du prix, calculé
# sur la distribution réelle des prix de cette gamme dans le catalogue. Si les
# prix changent, les bornes suivent, sans qu'on touche au code.
#
# Pourquoi filtrer plutôt qu'afficher l'écart et laisser juger : présenté avec la
# consigne « ne le propose pas si l'écart est absurde », le coach a recommandé un
# modem 4G à 99 TND pour remplacer un téléphone à 5 999 TND, tout en écrivant
# lui-même « moins de 2 % du S25 ». Un mauvais candidat visible finit proposé.
_SQL_SUBSTITUT_CANDIDATS = """
    WITH segments AS (
        SELECT sku, prix_ttc, gamme_libelle,
               NTILE(4) OVER (PARTITION BY gamme_libelle ORDER BY prix_ttc) AS quartile
          FROM sales.produits
         WHERE prix_ttc > 0 AND gamme_libelle IS NOT NULL
    ),
    cible AS (
        SELECT quartile FROM segments WHERE sku::text = %(sku)s
    )
    SELECT p.sku, p.nom, p.prix_ttc, p.marge_pct_calc,
           v.quantity_available AS qty
      FROM sales.produits p
      JOIN segments s ON s.sku = p.sku
      JOIN cible c    ON c.quartile = s.quartile
      JOIN inventory.vw_stock_enriched v
        ON v.sku = p.sku AND v.store_id = %(store)s
     WHERE p.gamme_libelle = %(gamme)s
       AND p.sku::text <> %(sku)s
       AND v.quantity_available > 0
     ORDER BY ABS(LN(p.prix_ttc) - LN(%(prix)s)) ASC
     LIMIT 12
"""


def _rank_with_agents(candidates: list[dict], gap: float) -> list[dict]:
    """
    Classe les substituts avec le score de fusion des agents plutôt qu'avec une
    heuristique locale : marge, santé du stock, promo active, alignement au gap.
    Si le module agent est indisponible, on garde l'ordre SQL (proximité de prix).
    """
    if not candidates:
        return []
    try:
        from app.sales.coaching.agents.coach.cross_domain_tools import rank_products
        products = [{
            "sku": str(c["sku"]), "name": c["nom"], "price": float(c["prix_ttc"]),
            "stock_current": int(c["qty"]), "stock_optimal": max(1.0, float(c["qty"])),
            "margin_pct": float(c["marge_pct_calc"] or 0),
            "days_to_stockout": 30.0, "category": "", "risk_level": "LOW",
            "is_top_seller": False, "active_promo": False,
        } for c in candidates]

        ranked = rank_products(
            products=products,
            sales_context={"gap_amount": gap, "gap_pct": 0.0},
            advisor_history={},
            top_n=3,
        )
        by_sku = {str(c["sku"]): c for c in candidates}
        return [{**by_sku[r["sku"]], "score": r["final_score"]}
                for r in ranked if r["sku"] in by_sku]
    except Exception as e:
        logger.debug("[COACH SUBST] scoring agent indisponible: %.60s", str(e))
        return candidates[:3]


def _load_substitutes_sync(store_id: str, message: str, gap: float = 0.0) -> str:
    """
    Disponibilité réelle des produits nommés par le conseiller, et substituts.

    Un substitut ne se trouve pas par similarité sémantique : « Galaxy A54 » et
    « Galaxy S25 » se ressemblent, mais seul le stock dit lequel est vendable.
    Sans ce bloc, le coach inventait le substitut — il a proposé un « Galaxy A54
    (SKU 5020160) à 399 TND » alors que ce SKU est un Samsung X160 à 120 TND.

    Tout vient de la donnée : le produit nommé sort du RAG, sa disponibilité de
    `vw_stock_enriched`, les candidats du catalogue en stock, et leur classement
    du score de fusion des agents. Aucun seuil de prix n'est codé en dur — l'écart
    est affiché, le coach tranche.
    """
    try:
        from app.sales.data.rag import DOMAIN_PRODUCT, retrieve
        found = retrieve(message, store_id=store_id, top_k=3, domains=[DOMAIN_PRODUCT])
        named = [d for d in found.docs if _names_product(message, d.produit)]
        if not named:
            return ""

        import psycopg2, psycopg2.extras
        blocks: list[str] = []
        conn = psycopg2.connect(**DB_CFG, connect_timeout=6)
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            for doc in named:
                payload = doc.payload or {}
                stock = payload.get("stock_dispo")
                gamme = payload.get("gamme")
                prix  = float(payload.get("prix_ttc") or 0)
                # stock None = aucune ligne de stock pour cette boutique : le
                # produit y est indisponible, au même titre qu'un stock à zéro.
                if stock or not gamme or prix <= 0:
                    continue

                cur.execute(_SQL_SUBSTITUT_CANDIDATS,
                            {"store": store_id, "gamme": gamme, "sku": doc.sku, "prix": prix})
                ranked = _rank_with_agents(cur.fetchall(), gap)

                if not ranked:
                    blocks.append(
                        f"  {doc.produit} ({prix:.0f} TND) : INDISPONIBLE en boutique, "
                        f"et AUCUN substitut comparable en stock (même gamme, même "
                        f"segment de prix). N'invente pas d'alternative : annonce la "
                        f"rupture, propose la réservation avec acompte."
                    )
                    continue

                alts = " · ".join(
                    f"{r['nom']} (SKU {r['sku']}, {r['prix_ttc']:.0f} TND, {r['qty']} en stock)"
                    for r in ranked
                )
                blocks.append(
                    f"  {doc.produit} ({prix:.0f} TND) : INDISPONIBLE en boutique. "
                    f"Substituts comparables réellement en stock : {alts}\n"
                    f"    Ne propose AUCUN autre produit en remplacement."
                )
            cur.close()
        finally:
            conn.close()

        if not blocks:
            return ""
        return ("DISPONIBILITÉ RÉELLE DES PRODUITS CITÉS (source : stock temps réel) :\n"
                + "\n".join(blocks))
    except Exception as e:
        logger.warning("[COACH SUBST] %.100s", str(e))
        return ""


def _agent_block_sync(store_id: str) -> str:
    """Sorties vivantes des agents sales + inventory (jamais indexées, toujours relues)."""
    try:
        from app.sales.coaching.agents.coach.agent_outputs import (
            format_agent_block,
            get_agent_outputs,
        )
        return format_agent_block(get_agent_outputs(store_id))
    except Exception as e:
        logger.warning("[COACH AGENTS] %.100s", str(e))
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# 3b. STRATÈGE (agent LangGraph : contexte réel météo/fériés/promos + RAG
#     + self-critique) — invoqué via l'orchestrateur résilient
# ══════════════════════════════════════════════════════════════════════════════

_EMPTY_STRAT: dict = {"actions": [], "source": "none", "extras": {}}


async def _get_stratege_for_chat(store_id: str, ca: float, target: float,
                                 urgency: str) -> dict:
    """
    Invoque l'agent Stratège serveur-side pour ancrer le coach dans la vraie
    stratégie du jour (météo Open-Meteo, fériés, promos actives, RAG, critique).

    Résilience :
      - Cache orchestrateur 30 min (clé store+gap) → la plupart des messages <1ms
      - Cold start : attente bornée à COACH_STRATEGE_TIMEOUT ; au-delà, le run
        continue en arrière-plan pour chauffer le cache — le chat n'est jamais bloqué
      - Toute erreur → dict vide, le coach répond avec le reste du contexte
    """
    try:
        from app.sales.coaching.orchestrator.bootstrap import get_orchestrator
        orchestrator = get_orchestrator()
    except Exception as e:
        logger.debug("[COACH STRAT] Orchestrateur indisponible: %.60s", str(e))
        return dict(_EMPTY_STRAT)

    gap_amount = max(0.0, target - ca)
    gap_pct    = round(gap_amount / target * 100, 1) if target > 0 else 0.0
    cycle_id   = f"chat-{store_id}-{int(time.time())}"
    state = {
        "cycle_id":          cycle_id,
        "pos_data":          {"store_id": store_id, "current_revenue": ca,
                              "daily_target": target},
        "gap_objectif":      gap_pct,
        "gap_amount":        gap_amount,
        "urgency_level":     urgency,
        "strategie_actions": [],
        "metrics":           {"cycle_id": cycle_id},
    }

    task = asyncio.create_task(orchestrator.invoke(state))
    try:
        result = await asyncio.wait_for(asyncio.shield(task),
                                        timeout=COACH_STRATEGE_TIMEOUT)
        logger.info("[COACH STRAT] %s | %d actions | %.0fms",
                    result.source, result.nb_actions, result.latency_ms)
        return {"actions": result.actions, "source": result.source,
                "extras": getattr(result, "extras", None) or {}}
    except asyncio.TimeoutError:
        logger.info("[COACH STRAT] > %.0fs — le Stratège continue en arrière-plan "
                    "(cache chaud pour le prochain message)", COACH_STRATEGE_TIMEOUT)
        task.add_done_callback(
            lambda t: t.exception() if not t.cancelled() else None)
        return {"actions": [], "source": "warming", "extras": {}}
    except Exception as e:
        logger.warning("[COACH STRAT] Erreur: %.80s", str(e))
        return dict(_EMPTY_STRAT)

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
    strat_extras: dict | None = None,
    rag_block: str = "",
    agent_block: str = "",
    substitutes_block: str = "",
) -> str:
    """
    Bloc de situation CROSS-DOMAINE : les sorties des agents SALES (analyste,
    stratège) ET INVENTORY (alertes stock, agent décision, KPIs) sont toujours
    visibles — le coach peut répondre vente, stock, ou mixer les deux.
    Le niveau de détail par domaine s'adapte à l'intent détecté.
    """
    strat_extras = strat_extras or {}

    # Météo réelle du Stratège (Open-Meteo) prioritaire sur le label frontend
    weather_line = weather
    if strat_extras.get("weather_label"):
        try:
            eff = float(strat_extras.get("weather_effect", 0) or 0)
            weather_line = f"{strat_extras['weather_label']} (trafic {eff:+.0%})"
        except (TypeError, ValueError):
            weather_line = strat_extras["weather_label"]

    lines = [
        f"SITUATION {advisor_name} | {store_id} | {hour}h | Météo : {weather_line} | Urgence : {urgency}",
        f"Performance : {ca:,.0f}/{target:,.0f} TND ({perf:.0f}%) | Gap : {gap:,.0f} TND | {hours_left}h restantes",
    ]
    if strat_extras.get("is_holiday") and strat_extras.get("holiday_name"):
        lines.append(f"Jour férié aujourd'hui : {strat_extras['holiday_name']}")

    # ── Profil conseiller (S3.3) ─────────────────────────────────────────────
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

    # ── STRATÉGIE DU JOUR (agent Stratège — tous modes) ──────────────────────
    strat_summary = strat_extras.get("strategie_summary", "")
    focus         = strat_extras.get("focus_produits") or []
    if strat_summary or actions:
        lines.append("STRATÉGIE DU JOUR (agent Stratège — météo/stock/promos réels) :")
        if strat_summary:
            lines.append(f"  Résumé : {strat_summary[:200]}")
        n_actions = 3 if mode in ("coaching", "cross_domain") else 1
        for a in actions[:n_actions]:
            lines.append(
                f"  {a.get('priorite', 1)}. {str(a.get('action', ''))[:120]} "
                f"→ {a.get('produit_cible', '')} "
                f"| «{str(a.get('argument_vente', ''))[:110]}» "
                f"| {str(a.get('impact_estime', ''))[:60]}"
            )
        if focus:
            lines.append(f"  Produits focus : {', '.join(str(f) for f in focus[:3])}")

    # ── VENTES (agent analyste — toujours visible, détaillé si recap) ────────
    if top_sellers:
        n = 3 if qtype == "recap" else 2
        t_str = " · ".join(f"{t['nom']} ({t['qty']} vendus)" for t in top_sellers[:n])
        lines.append(f"Ventes — top produits 7j : {t_str}")
    if recent_tx:
        r0 = recent_tx[0]
        lines.append(f"Dernière vente : {r0['heure']}h — {r0['nom']} x{r0['qty']} — {r0['ttc']:.0f} TND")

    # ── STOCK (agents inventory — toujours visible, détaillé si stock/mixte) ─
    stats  = inv_ctx.get("stats", {})
    alerts = inv_ctx.get("alerts", [])
    recos  = inv_ctx.get("agent_recos", [])
    if stats.get("total"):
        lines.append(
            f"Stock : {stats.get('total',0)} SKUs | "
            f"Ruptures : {stats.get('ruptures',0)} | "
            f"Critiques : {stats.get('critiques',0)} | "
            f"OK : {stats.get('ok_count',0)}"
        )
    inv_detail = mode in ("inventory", "cross_domain")
    if alerts:
        n_alerts = 4 if inv_detail else 2
        lines.append("Alertes stock :")
        for a in alerts[:n_alerts]:
            icon = "X" if a["level"] == "rupture" else "!"
            lines.append(f"  {icon} {a['nom']} — {a['qty']} unité(s) | rupture J+{a['jours']:.0f}")
    if inv_detail:
        kpi_t = inv_ctx.get("kpi_terminaux", 0)
        kpi_f = inv_ctx.get("kpi_forfaits", 0)
        if kpi_t or kpi_f:
            lines.append(f"KPI jour : {kpi_t} terminaux vendus · {kpi_f} forfaits vendus")
        top_inv = inv_ctx.get("top_sellers", [])
        if top_inv:
            lines.append("Rotation (vélocité) :")
            for t in top_inv[:3]:
                lines.append(f"  {t['nom']} — {t['sold']}/7j | stock {t['stock']} | J+{t['jours']:.0f}")
    if recos:
        n_recos = 3 if inv_detail else 1
        lines.append("Agent Décision (réappro) : " + " · ".join(
            f"[{r['type']}] {r['nom']} x{r['qty']}" for r in recos[:n_recos]
        ))

    # ── COMMANDES EN COURS + DÉCISIONS HUMAINES (état frais — le coach ne
    #    doit jamais conseiller de commander un produit dont la commande est
    #    déjà en route, ni contredire un arbitrage humain récent) ────────────
    pos = inv_ctx.get("pos", [])
    if pos:
        n_pos = 5 if inv_detail else 2
        _STATUT_FR = {"SUGGERE": "suggérée (en attente manager)", "BROUILLON": "brouillon",
                      "SOUMIS": "soumise", "CONFIRME": "confirmée",
                      "EXPEDIE": "expédiée", "RECU_PARTIEL": "reçue partiellement",
                      "LITIGE": "en litige"}
        lines.append("Commandes fournisseur en cours (Kanban) :")
        for po in pos[:n_pos]:
            eta = f" — livraison prévue {po['eta']}" if po.get("eta") else ""
            src = " [agent]" if po.get("source") == "AGENT" else ""
            lines.append(
                f"  {po['nom']} x{po['qty']} — {_STATUT_FR.get(po['statut'], po['statut'])}{src}{eta}"
            )
        lines.append(
            "  → Ces produits ont DÉJÀ une commande en route : ne pas conseiller "
            "d'en repasser une, annoncer plutôt l'arrivée."
        )
    decisions = inv_ctx.get("decisions", [])
    if decisions and inv_detail:
        _DEC_FR = {"approved": "approuvé", "rejected": "rejeté",
                   "followed": "suivi", "ignored": "ignoré"}
        lines.append("Décisions humaines récentes (7j) : " + " · ".join(
            f"{_DEC_FR.get(d['decision'], d['decision'])} — {d['nom'] or d['action']}"
            + (f" ({d['reason']})" if d.get("reason") else "")
            for d in decisions[:3]
        ))

    # ── SORTIES VIVANTES DES AGENTS (sales + inventory) ──────────────────────
    if agent_block:
        lines.append("── CE QUE LES AGENTS DISENT MAINTENANT ──\n" + agent_block)

    # ── SUBSTITUTS (stock réel, requêtés en base) ────────────────────────────
    if substitutes_block:
        lines.append(substitutes_block)

    # ── CONNAISSANCE RÉCUPÉRÉE (RAG cross-domaine, documents cités) ──────────
    if rag_block:
        lines.append("── DOCUMENTS RÉCUPÉRÉS (cite leurs références) ──\n" + rag_block)
    else:
        # Silence du RAG = danger. Sans cet avertissement, le coach comblait le
        # vide en inventant : Milvus et Ollama tombés, il a « proposé le Galaxy
        # A54 (SKU 5020160) à 399 TND » — un SKU qui est en réalité un Samsung
        # X160 à 120 TND. Une base muette doit rendre le coach prudent, pas
        # créatif.
        lines.append(
            "⚠ AUCUN DOCUMENT RÉCUPÉRÉ (base de connaissance indisponible ou sans "
            "réponse). Tu n'as donc AUCUNE fiche produit : n'écris ni prix, ni SKU, "
            "ni nom de modèle que tu ne lis pas explicitement ci-dessus. Appuie-toi "
            "uniquement sur les chiffres de la SITUATION et des AGENTS, et dis au "
            "conseiller que tu ne peux pas confirmer le détail catalogue."
        )

    # ── Consigne de lecture ──────────────────────────────────────────────────
    if mode == "cross_domain":
        lines.append(
            "Consigne : la question croise VENTE et STOCK — combine les deux "
            "(ex : produit à pousser = demande forte ET stock disponible ; "
            "rupture = proposer l'alternative en stock)."
        )
    elif qtype == "general":
        lines.append(
            "Note : si des données ci-dessus répondent à la question, utilise-les "
            "(vente, stock, ou les deux). Sinon, si la question est incompréhensible "
            "ou hors-sujet, demande une clarification en 1 phrase — n'invente aucun "
            "client ni situation."
        )

    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════════════
# 5. LLM CALL CHAIN (OpenRouter → Ollama → intent fallback)
# ══════════════════════════════════════════════════════════════════════════════

# Débuts de réponse qui trahissent soit un modèle qui répond en anglais, soit un
# modèle de raisonnement dont la chaîne de pensée a fui dans le contenu.
# (Comparer `r[:6]` à ces chaînes ne marchait pas : "I'm " fait 4 caractères.)
_INVALID_PREFIXES = (
    "I'm ", "I can", "I will", "The c", "To he", "Based", "Here ", "Sure,",
    "We need", "We should", "We must", "We have", "Let me", "First,",
    "Okay,", "Alright", "The user", "Thinking", "<think",
)


def _is_valid_reply(reply: str) -> bool:
    if not reply or len(reply.strip()) < 25:
        return False
    r = reply.strip()
    if r.startswith(_INVALID_PREFIXES):
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
                **_NO_REASONING,
            }, ensure_ascii=False).encode("utf-8")
            resp = await get_http_client().post(
                OPENROUTER_URL, headers=headers, content=body, timeout=28.0)
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


# ── Groq (rotation clés × modèles — quotas Groq indépendants par modèle) ─────
async def _call_groq(
    system: str,
    user_msg: str,
    max_tokens: int,
    temperature: float,
    history: list,
) -> tuple[str, float]:
    t0 = time.time()
    if not GROQ_KEYS:
        return "", 0.0
    import httpx
    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history[-8:])
    messages.append({"role": "user", "content": user_msg})
    for ki, key in enumerate(GROQ_KEYS, start=1):
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type":  "application/json; charset=utf-8",
        }
        for model in _GROQ_MODEL_ROTATION:
            try:
                body = json.dumps({
                    "model": model, "max_tokens": max_tokens,
                    "temperature": temperature, "messages": messages,
                }, ensure_ascii=False).encode("utf-8")
                resp = await get_http_client().post(
                    GROQ_URL, headers=headers, content=body, timeout=28.0)
                if resp.status_code in (401, 403):
                    # Clé invalide/révoquée : inutile d'insister avec d'autres modèles
                    logger.warning("[COACH GROQ] clé #%d → %s — clé suivante", ki, resp.status_code)
                    break
                if resp.status_code == 429:
                    # Quotas Groq séparés par modèle → modèle suivant, puis clé suivante
                    logger.warning("[COACH GROQ] clé #%d %s → 429 rate limit", ki, model)
                    continue
                if resp.status_code >= 500:
                    logger.warning("[COACH GROQ] clé #%d %s → %s server error", ki, model, resp.status_code)
                    continue
                data = resp.json()
                if resp.status_code >= 400:
                    err = data.get("error")
                    msg = err.get("message") if isinstance(err, dict) else data.get("message", "?")
                    logger.warning("[COACH GROQ] clé #%d %s → %s: %.60s", ki, model, resp.status_code, str(msg))
                    continue
                raw = data["choices"][0]["message"]["content"].strip()
                for pfx in ["Reponse:", "Coach:", "CoachIA:", "Réponse :", "RÉPONSE :",
                            "Here is", "Based on", "Voici ma", "D'accord,"]:
                    if raw.lower().startswith(pfx.lower()):
                        raw = raw[len(pfx):].strip()
                if raw.startswith("{"):
                    m = re.search(r'"(?:reply|response|conseil|text|message)"\s*:\s*"([^"]+)"', raw)
                    raw = m.group(1).strip() if m else ""
                ms = (time.time() - t0) * 1000
                logger.info("[COACH GROQ] %dc | %.0fms | clé #%d %s", len(raw), ms, ki, model)
                return raw, ms
            except Exception as e:
                logger.warning("[COACH GROQ] clé #%d %s exception: %.60s", ki, model, str(e))
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
            resp = await get_http_client().post(
                MISTRAL_URL, headers=headers, content=body, timeout=28.0)
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
        client = get_http_client()
        tags = await client.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        models = [m["name"] for m in tags.json().get("models", [])]
        chat_model = next(
            (m for m in models if any(p in m for p in
             ["llama3","llama2","mistral","mixtral","phi","gemma","qwen"])),
            None,
        )
        if not chat_model:
            return ""
        resp = await client.post(f"{OLLAMA_URL}/api/generate", timeout=22.0, json={
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
    # Bloc de grounding : le texte EXACT injecté dans le prompt (situation
    # cross-domaine + catalogue). Réservé au harnais d'évaluation, qui note
    # `ancrage` en recoupant chaque chiffre de la réponse avec ce que le modèle
    # avait réellement sous les yeux. `context_used` ne suffit pas : c'est un
    # résumé de KPIs, alors que le prompt contient aussi le catalogue, les
    # substituts, les scripts RAG et les sorties d'agents. Juger l'ancrage sur
    # le résumé revient à compter comme halluciné tout chiffre venu du reste —
    # c'est ce qui produisait 60 % d'« hallucinations » sur des données vraies.
    debug_grounding = bool(body.get("debug_grounding"))
    _grounding: dict | None = None

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
    # Contourné en mode évaluation : un second run noterait la réponse mise en
    # cache au premier, sans grounding et sans passer par le modèle — on
    # mesurerait le cache, pas le coach.
    cached = None if debug_grounding else _cache_get(advisor_name, store_id, message)
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

    # ── Chargement contexte en parallèle — CROSS-DOMAINE systématique ───────
    # Le coach reçoit toujours les sorties des agents SALES (analyste, stratège)
    # ET INVENTORY (alertes stock, agent décision) : il peut répondre vente,
    # stock, ou mixer les deux quel que soit l'intent détecté.
    loop = asyncio.get_event_loop()

    # POS d'abord (1 requête rapide) : le Stratège reçoit le vrai CA du jour
    # → gap exact + clé de cache orchestrateur stable.
    pos_data = await loop.run_in_executor(None, _load_pos_sync, store_id)
    ca     = ca_ctx or pos_data.get("ca", 0.0)
    target = target_ctx

    # Le Stratège serveur-side n'est invoqué que si le frontend n'a pas déjà
    # fourni ses actions (cycle planifié) — cache orchestrateur 30 min.
    strat_coro = (
        _get_stratege_for_chat(store_id, ca, target, urgency)
        if not actions_ctx else None
    )

    sales_detail, inv_ctx, day_history, catalog, adv_profile, strat = await asyncio.gather(
        loop.run_in_executor(None, _load_sales_detail_sync, store_id),
        loop.run_in_executor(None, _load_inventory_context_sync, store_id),
        loop.run_in_executor(None, _load_day_history_sync, advisor_name, store_id),
        loop.run_in_executor(None, _load_catalog, store_id),
        loop.run_in_executor(None, _load_advisor_profile_sync, advisor_name, store_id),
        strat_coro if strat_coro is not None else asyncio.sleep(0, result=dict(_EMPTY_STRAT)),
    )

    strat_extras = strat.get("extras") or {}
    strat_source = strat.get("source", "frontend" if actions_ctx else "none")
    if not actions_ctx:
        actions_ctx = strat.get("actions") or []
    if not cause_ctx and strat_extras.get("cause_racine"):
        cause_ctx = strat_extras["cause_racine"]

    perf   = round((ca / target * 100), 1) if target > 0 else 0.0
    gap    = max(0.0, target - ca)
    nb_tx  = pos_data.get("nb_tx", 0) or len(sales_detail.get("recent_tx", []))

    top_sellers = sales_detail.get("top_sellers", [])
    recent_tx   = sales_detail.get("recent_tx", [])

    # ── RAG cross-domaine + sorties vivantes des agents ──────────────────────
    _rag_t0 = time.time()
    rag_scripts, rag_relevant, rag_block = await loop.run_in_executor(
        None, _search_rag_sync, message, hour, store_id, 6, _out_of_stock(inv_ctx)
    )
    rag_ms = (time.time() - _rag_t0) * 1000
    rag_query = message

    agent_block, substitutes_block = await asyncio.gather(
        loop.run_in_executor(None, _agent_block_sync, store_id),
        loop.run_in_executor(None, _load_substitutes_sync, store_id, message, gap),
    )

    # ── Prompt (système v11 + situation cross-domaine) ──────────────────────
    system_prompt = _build_system_prompt(catalog)

    situation_block = _build_situation(
        advisor_name=advisor_name, store_id=store_id, hour=hour,
        ca=ca, target=target, perf=perf, gap=gap, hours_left=hours_left,
        urgency=urgency, weather=weather, cause=cause_ctx,
        mode=mode, qtype=qtype,
        actions=actions_ctx, rag_scripts=rag_scripts,
        top_sellers=top_sellers, recent_tx=recent_tx,
        inv_ctx=inv_ctx, advisor_profile=adv_profile,
        strat_extras=strat_extras,
        rag_block=rag_block, agent_block=agent_block,
        substitutes_block=substitutes_block,
    )
    user_message = f"{situation_block}\n\nQUESTION DU CONSEILLER : {message}"

    if debug_grounding:
        _grounding = {
            "situation_block": situation_block,
            "catalog":         catalog,
            "rag_block":       rag_block,
            "agent_block":     agent_block,
            "substitutes_block": substitutes_block,
        }

    # Paramètres LLM selon intent
    if qtype in ("script", "objectif"):
        max_tokens, temp = 450, 0.17
    elif mode in ("inventory",):
        max_tokens, temp = 380, 0.20
    elif urgency in ("HIGH", "CRITICAL"):
        max_tokens, temp = 280, 0.17
    else:
        max_tokens, temp = 350, 0.22

    # ── Tentative 1 : Groq (rotation multi-clés — primaire si configuré) ────
    reply, llm_ms = await _call_groq(system_prompt, user_message, max_tokens, temp, day_history)
    model_used = "groq" if _is_valid_reply(reply) else ""

    # ── Tentative 2 : OpenRouter / nano-30b ─────────────────────────────────
    if not _is_valid_reply(reply):
        if GROQ_KEYS:
            logger.warning("[COACH] Groq failed (%.0fms) — retry OpenRouter", llm_ms)
        reply, llm_ms = await _call_openrouter(system_prompt, user_message, max_tokens, temp, day_history)
        model_used = OPENROUTER_MODEL if _is_valid_reply(reply) else ""

    # ── Tentative 3 : Mistral direct (quota indépendant d'OpenRouter) ───────
    if not _is_valid_reply(reply):
        if OPENROUTER_KEY:
            logger.warning("[COACH] OpenRouter failed (%.0fms) — retry Mistral", llm_ms)
        reply, llm_ms = await _call_mistral(system_prompt, user_message, max_tokens, temp, day_history)
        model_used = "mistral" if _is_valid_reply(reply) else ""

    # ── Tentative 4 : OpenRouter stripped ───────────────────────────────────
    if not _is_valid_reply(reply):
        logger.warning("[COACH] Attempt 3 failed (%.0fms) — retry stripped", llm_ms)
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
            # ── Tentative 5 : Ollama local ───────────────────────────────────
            logger.warning("[COACH] Attempt 4 failed — trying Ollama")
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
        _focus = (strat_extras.get("focus_produits")
                  or inv_ctx.get("focus_produits") or [])
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
                                  "top_score": rag_scripts[0].score if rag_scripts else 0},
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
        "source":          f"v11_{model_used}",
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
            {"categorie": s.categorie or s.doc_type, "domaine": s.domain,
             "action": (s.payload or {}).get("action", s.title),
             "produit": s.produit, "score": s.score}
            for s in rag_scripts[:2]
        ],
        "inventory_alerts": [
            {"product": a["nom"], "qty": a["qty"], "level": a["level"],
             "jours": a["jours"], "eoq": a.get("eoq", 0)}
            for a in inv_ctx.get("alerts", [])[:3]
        ],
        "agent_recos":      inv_ctx.get("agent_recos", [])[:3],
        "strategie_actions": actions_ctx[:3],
        "stratege_source":  strat_source,
        "cause_racine":     cause_ctx,
        "scored_products":  _scored_products,
        "guardrail_status": _grd.get("status", "APPROVE"),
        "guardrail_issues": _grd.get("issues", []),
        "requires_hitl":    _grd.get("requires_human_validation", False),
        # Absent des réponses normales : quelques Ko de prompt qui n'ont rien à
        # faire dans le payload du frontend.
        **({"grounding": _grounding} if _grounding else {}),
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

    # Contexte CROSS-DOMAINE systématique (agents sales + inventory) + Stratège
    # serveur-side si le frontend n'a pas fourni ses actions.
    loop = asyncio.get_event_loop()

    pos_data = await loop.run_in_executor(None, _load_pos_sync, store_id)
    ca     = ca_ctx or pos_data.get("ca", 0.0)
    target = target_ctx

    strat_coro = (
        _get_stratege_for_chat(store_id, ca, target, urgency)
        if not actions_ctx else None
    )

    sales_detail, inv_ctx_data, day_history, catalog, adv_profile, strat = await asyncio.gather(
        loop.run_in_executor(None, _load_sales_detail_sync, store_id),
        loop.run_in_executor(None, _load_inventory_context_sync, store_id),
        loop.run_in_executor(None, _load_day_history_sync, advisor_name, store_id),
        loop.run_in_executor(None, _load_catalog, store_id),
        loop.run_in_executor(None, _load_advisor_profile_sync, advisor_name, store_id),
        strat_coro if strat_coro is not None else asyncio.sleep(0, result=dict(_EMPTY_STRAT)),
    )

    strat_extras = strat.get("extras") or {}
    strat_source = strat.get("source", "frontend" if actions_ctx else "none")
    if not actions_ctx:
        actions_ctx = strat.get("actions") or []
    if not cause_ctx and strat_extras.get("cause_racine"):
        cause_ctx = strat_extras["cause_racine"]

    perf   = round((ca / target * 100), 1) if target > 0 else 0.0
    gap    = max(0.0, target - ca)

    top_sellers = sales_detail.get("top_sellers", [])
    recent_tx   = sales_detail.get("recent_tx", [])

    _rag_t0 = time.time()
    rag_scripts, rag_relevant, rag_block = await loop.run_in_executor(
        None, _search_rag_sync, message, hour, store_id, 6, _out_of_stock(inv_ctx_data)
    )
    rag_ms = (time.time() - _rag_t0) * 1000
    rag_query = message

    agent_block, substitutes_block = await asyncio.gather(
        loop.run_in_executor(None, _agent_block_sync, store_id),
        loop.run_in_executor(None, _load_substitutes_sync, store_id, message, gap),
    )

    system_prompt   = _build_system_prompt(catalog)
    situation_block = _build_situation(
        advisor_name=advisor_name, store_id=store_id, hour=hour,
        ca=ca, target=target, perf=perf, gap=gap, hours_left=hours_left,
        urgency=urgency, weather=weather, cause=cause_ctx,
        mode=mode, qtype=qtype,
        actions=actions_ctx, rag_scripts=rag_scripts,
        top_sellers=top_sellers, recent_tx=recent_tx,
        inv_ctx=inv_ctx_data, advisor_profile=adv_profile,
        strat_extras=strat_extras,
        rag_block=rag_block, agent_block=agent_block,
        substitutes_block=substitutes_block,
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

        messages = [{"role": "system", "content": system_prompt}]
        if day_history:
            messages.extend(day_history[-8:])
        messages.append({"role": "user", "content": user_message})

        # Providers streaming par ordre de priorité : Groq (rotation clés ×
        # modèles), puis OpenRouter (nano-30b + rotation de secours), puis
        # Mistral dont le quota est indépendant.
        providers: list[tuple[str, str, dict]] = []
        for _gkey in GROQ_KEYS:
            _g_headers = {
                "Authorization": f"Bearer {_gkey}",
                "Content-Type":  "application/json; charset=utf-8",
            }
            for _m in _GROQ_MODEL_ROTATION:
                providers.append((_m, GROQ_URL, _g_headers))
        if OPENROUTER_KEY:
            _or_headers = {
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type":  "application/json; charset=utf-8",
                "HTTP-Referer":  "https://github.com/MALEKALADAB11/multi-agent-sales-inventory",
                "X-Title":       "AI Sales Coach Ooredoo v11 stream",
            }
            for _m in _OR_MODEL_ROTATION:
                providers.append((_m, OPENROUTER_URL, _or_headers))
        if MISTRAL_KEY:
            for _m in _MISTRAL_MODEL_ROTATION:
                providers.append((_m, MISTRAL_URL, {
                    "Authorization": f"Bearer {MISTRAL_KEY}",
                    "Content-Type":  "application/json; charset=utf-8",
                }))

        for _model, _url, _headers in providers:
            if streamed_ok:
                break
            try:
                # `reasoning` est une extension OpenRouter : Mistral rejette le champ.
                _extra = _NO_REASONING if _url == OPENROUTER_URL else {}
                body = json.dumps({
                    "model": _model, "max_tokens": max_tokens,
                    "temperature": temp, "messages": messages, "stream": True,
                    **_extra,
                }, ensure_ascii=False).encode("utf-8")

                client = get_http_client()
                async with client.stream("POST", _url, headers=_headers,
                                         content=body, timeout=30.0) as resp:
                    if resp.status_code >= 400:
                        logger.warning("[COACH STREAM] %s → HTTP %s", _model, resp.status_code)
                        continue
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
                if streamed_ok:
                    model_used = _model
            except Exception as e:
                logger.warning("[COACH STREAM] %s error: %.80s", _model, str(e))
                # Si des tokens sont déjà partis, ne pas re-streamer un autre
                # provider (texte dupliqué côté client) — on garde le partiel.
                if full_reply:
                    streamed_ok = True
                    model_used = _model
                    break

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
                                      "top_score": rag_scripts[0].score if rag_scripts else 0},
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

        yield f'data: {json.dumps({"done": True, "reply": final_reply, "rag_used": rag_relevant, "source": f"v11_{model_used}", "model": model_used, "stratege_source": strat_source, "strategie_actions": actions_ctx[:3], "cause_racine": cause_ctx, "guardrail_status": _sgrd.get("status", "APPROVE"), "guardrail_issues": _sgrd.get("issues", []), "requires_hitl": _sgrd.get("requires_human_validation", False)})}\n\n'

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
        "version":      "11.0.0",
        "architecture": "persona-first cross-domain, stratège server-side, RAG unifié",
        "features": [
            "persona_first_prompting_v11",
            "few_shot_in_system_prompt",
            "cross_domain_context_always_loaded",
            "stratege_server_side_orchestrated",
            "stratege_cache_30min_background_warm",
            "rag_unified_milvus_plus_corpus_fallback",
            "intent_selective_detail",
            "response_dedup_cache_20s",
            "greeting_fast_path_no_llm",
            "off_topic_guard",
            "retry_chain_4_levels",
            "ollama_chat_fallback",
            "intent_aware_fallback",
            "response_validator",
            "async_persist_nonblocking",
            "parallel_context_loading",
            "dynamic_catalog_db_10min_ttl",
        ],
        "llm_primary":  OPENROUTER_MODEL,
        "llm_fallback": "ollama_local_chat_model",
        "llm_available": bool(OPENROUTER_KEY),
        "stratege_timeout_s": COACH_STRATEGE_TIMEOUT,
        "prompt_tokens_approx": {
            "system_persona": "~320",
            "catalog":        "~80 (dynamique DB)",
            "situation_block": "100-220 (intent-sélectif)",
            "total_per_call":  "~500-620",
        },
    })
