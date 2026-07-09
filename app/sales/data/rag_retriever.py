"""
rag_retriever.py — Retriever RAG unifié (Coach Chat + Agent Stratège).
Source primaire : Milvus (recherche sémantique via embeddings Ollama).
Fallback       : corpus de scripts embarqué (scoring lexical) — le RAG ne
                 retourne jamais "rien" parce qu'un service est down.

Utilisation:
    from app.sales.data.rag_retriever import search_scripts, format_scripts_block
    result = search_scripts("client hésite trop cher", hour=16, top_k=3)
    prompt_block = format_scripts_block(result["scripts"])
"""

import logging
import os
import re
import requests
from datetime import datetime
from typing import Optional
from pathlib import Path
from app.core.config import DEFAULT_STORE_ID

logger = logging.getLogger(__name__)

MILVUS_URI  = os.getenv("MILVUS_URI", "http://localhost:19530")
COLLECTION  = "coaching_scripts"
EMBED_MODEL = "nomic-embed-text"
EMBED_DIM   = 768
OLLAMA_URL  = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

RAG_TOP_K         = int(os.getenv("RAG_TOP_K", "3"))
RAG_MIN_SCORE     = float(os.getenv("RAG_MIN_SCORE", "0.30"))
RAG_EMBED_TIMEOUT = float(os.getenv("RAG_EMBED_TIMEOUT", "15"))

# Cache singleton du client Milvus
_milvus_client = None


def _ensure_collection(client) -> bool:
    """Create coaching_scripts collection if it doesn't exist. Returns True if ready."""
    try:
        if client.has_collection(COLLECTION):
            return True
        from pymilvus import DataType
        schema = client.create_schema(auto_id=True, enable_dynamic_field=True)
        schema.add_field("id",           DataType.INT64,         is_primary=True, auto_id=True)
        schema.add_field("vector",       DataType.FLOAT_VECTOR,  dim=EMBED_DIM)
        schema.add_field("pg_id",        DataType.INT64)
        schema.add_field("categorie",    DataType.VARCHAR,        max_length=100)
        schema.add_field("situation",    DataType.VARCHAR,        max_length=200)
        schema.add_field("action",       DataType.VARCHAR,        max_length=200)
        schema.add_field("produit",      DataType.VARCHAR,        max_length=100)
        schema.add_field("argument",     DataType.VARCHAR,        max_length=200)
        schema.add_field("impact",       DataType.VARCHAR,        max_length=100)
        schema.add_field("heure_min",    DataType.INT64)
        schema.add_field("heure_max",    DataType.INT64)
        schema.add_field("jour_semaine", DataType.INT64)
        schema.add_field("store_id",     DataType.VARCHAR,        max_length=20)
        idx = client.prepare_index_params()
        idx.add_index("vector", index_type="FLAT", metric_type="COSINE")
        client.create_collection(COLLECTION, schema=schema, index_params=idx)
        logger.info(f"[RAG] Collection '{COLLECTION}' créée (vide — sera peuplée par les cycles)")
        return True
    except Exception as e:
        logger.warning(f"[RAG] ensure_collection: {e}")
        return False


def _get_client():
    global _milvus_client
    if _milvus_client is None:
        try:
            from pymilvus import MilvusClient
            _milvus_client = MilvusClient(uri=MILVUS_URI)
            _ensure_collection(_milvus_client)
            logger.info(f"[RAG] Milvus connecté: {MILVUS_URI}")
        except ImportError:
            logger.warning("[RAG] pymilvus non installé — RAG désactivé")
        except Exception as e:
            logger.warning(f"[RAG] Milvus connexion échouée: {e}")
    return _milvus_client


def _embed(text: str) -> Optional[list]:
    """Génère un embedding via Ollama (timeout court — le fallback lexical prend le relais)."""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text[:500]},
            timeout=RAG_EMBED_TIMEOUT,
        )
        resp.raise_for_status()
        emb = resp.json().get("embedding")
        if emb:
            if len(emb) < EMBED_DIM:
                emb = emb + [0.0] * (EMBED_DIM - len(emb))
            return emb[:EMBED_DIM]
        return None
    except requests.Timeout:
        logger.warning(f"[RAG] Embedding timeout ({RAG_EMBED_TIMEOUT:.0f}s) — fallback lexical")
        return None
    except requests.ConnectionError as e:
        logger.warning(f"[RAG] Embedding connexion échouée: {e}")
        return None
    except Exception as e:
        logger.warning(f"[RAG] Embedding échoué: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# RECHERCHE UNIFIÉE — Milvus (sémantique) + corpus embarqué (lexical)
# ══════════════════════════════════════════════════════════════════════════════

_STOPWORDS_FR = {
    "le", "la", "les", "un", "une", "des", "de", "du", "au", "aux", "et", "ou",
    "en", "sur", "pour", "avec", "sans", "dans", "que", "qui", "quoi", "est",
    "sont", "mon", "ma", "mes", "ton", "ta", "tes", "son", "sa", "ses", "ce",
    "cette", "ces", "il", "elle", "je", "tu", "on", "nous", "vous", "ils",
    "pas", "plus", "moins", "tres", "comment", "quel", "quelle", "veut", "dit",
}


def _norm_txt(t: str) -> str:
    t = t.lower()
    for a, b in [("é", "e"), ("è", "e"), ("ê", "e"), ("ë", "e"), ("à", "a"),
                 ("â", "a"), ("ô", "o"), ("î", "i"), ("ï", "i"), ("ç", "c"),
                 ("ù", "u"), ("û", "u"), ("œ", "oe"), ("'", " "), ("-", " ")]:
        t = t.replace(a, b)
    return t


def _tokens(t: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]{3,}", _norm_txt(t)) if w not in _STOPWORDS_FR}


_corpus_cache: Optional[list] = None


def _load_corpus() -> list:
    """Corpus de scripts embarqué (même source que le seed Milvus)."""
    global _corpus_cache
    if _corpus_cache is None:
        try:
            from app.sales.data.seeds.seed_rag_scripts import SCRIPTS
            _corpus_cache = SCRIPTS
        except Exception as e:
            logger.warning(f"[RAG] Corpus embarqué indisponible: {e}")
            _corpus_cache = []
    return _corpus_cache


def _corpus_fallback(query: str, hour: int, top_k: int) -> list:
    """Scoring lexical sur le corpus embarqué — utilisé si Milvus/Ollama down."""
    corpus = _load_corpus()
    if not corpus:
        return []
    q_tokens = _tokens(query)
    if not q_tokens:
        return []
    scored = []
    for s in corpus:
        doc_tokens = _tokens(f"{s['situation']} {s['action']} {s['argument']}")
        overlap = len(q_tokens & doc_tokens)
        if overlap == 0:
            continue
        score = overlap / max(len(q_tokens), 1)
        if _norm_txt(s["categorie"]) in _norm_txt(query):
            score += 0.25
        if int(s.get("heure_min", 0)) <= hour <= int(s.get("heure_max", 24)):
            score += 0.10
        scored.append({
            "score":     round(min(score, 1.0), 3),
            "categorie": s["categorie"],
            "situation": s["situation"],
            "action":    s["action"],
            "produit":   s["produit"],
            "argument":  s["argument"],
            "impact":    s["impact"],
        })
    return sorted(scored, key=lambda x: x["score"], reverse=True)[:top_k]


def search_scripts(
    query: str,
    hour: Optional[int] = None,
    top_k: int = RAG_TOP_K,
    min_score: float = RAG_MIN_SCORE,
) -> dict:
    """
    Recherche unifiée de scripts de vente.

    Returns:
        {
            "scripts":  [ {score, categorie, situation, action, produit, argument, impact}, ... ],
            "relevant": bool,   # top score >= min_score
            "source":   "milvus" | "corpus" | "none",
        }
    """
    if hour is None:
        hour = datetime.now().hour

    # ── 1. Voie sémantique : Ollama embeddings + Milvus ──────────────────────
    client = _get_client()
    emb = _embed(query) if client is not None else None
    if client is not None and emb:
        try:
            results = client.search(
                collection_name=COLLECTION,
                data=[emb],
                limit=top_k + 3,
                output_fields=["categorie", "situation", "action", "produit",
                               "argument", "impact", "heure_min", "heure_max"],
            )
            scripts = []
            for hit in results[0]:
                e = hit["entity"]
                score = float(hit["distance"])
                if int(e.get("heure_min", 0)) <= hour <= int(e.get("heure_max", 24)):
                    score += 0.10
                scripts.append({
                    "score":     round(score, 3),
                    "categorie": e.get("categorie", ""),
                    "situation": e.get("situation", ""),
                    "action":    e.get("action", ""),
                    "produit":   e.get("produit", ""),
                    "argument":  e.get("argument", ""),
                    "impact":    e.get("impact", ""),
                })
            # Dédupe par catégorie+action pour varier les angles proposés
            seen, deduped = set(), []
            for s in sorted(scripts, key=lambda x: x["score"], reverse=True):
                key = (s["categorie"], s["action"][:60])
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(s)
            scripts = deduped[:top_k]
            if scripts:
                relevant = scripts[0]["score"] >= min_score
                return {"scripts": scripts, "relevant": relevant, "source": "milvus"}
        except Exception as e:
            logger.warning(f"[RAG] Recherche Milvus échouée: {str(e)[:100]}")

    # ── 2. Fallback lexical : corpus embarqué ────────────────────────────────
    scripts = _corpus_fallback(query, hour, top_k)
    if scripts:
        relevant = scripts[0]["score"] >= min_score
        logger.info(f"[RAG] Fallback corpus — {len(scripts)} scripts (top={scripts[0]['score']:.2f})")
        return {"scripts": scripts, "relevant": relevant, "source": "corpus"}

    return {"scripts": [], "relevant": False, "source": "none"}


def format_scripts_block(scripts: list, max_n: int = 3) -> str:
    """Formate les scripts RAG pour injection prompt — compact mais complet."""
    if not scripts:
        return ""
    lines = ["SCRIPTS TERRAIN PROUVÉS (base Ooredoo — adapte ces arguments) :"]
    for i, s in enumerate(scripts[:max_n], 1):
        lines.append(
            f"[{i}] ({s['categorie']}, score {s['score']:.2f}) "
            f"Situation : {s['situation'][:110]}\n"
            f"    Action : {s['action'][:130]}\n"
            f"    Argument : «{s['argument'][:180]}»\n"
            f"    Impact observé : {s['impact'][:100]}"
        )
    return "\n".join(lines)


def _build_query(
    gap_pct: float,
    current_hour: int,
    urgency: str,
    context_summary: dict,
    advisor_name: str = "",
) -> str:
    """Construit la requête RAG depuis le contexte actuel."""
    parts = []

    if gap_pct > 60:
        parts.append("gap critique objectif éloigné")
    elif gap_pct > 30:
        parts.append("gap modéré performance insuffisante")
    else:
        parts.append("gap faible objectif proche")

    if 14 <= current_hour <= 17:
        parts.append("heure de pointe pic ventes")
    elif 19 <= current_hour <= 20:
        parts.append("soirée dernières heures")
    elif current_hour <= 10:
        parts.append("matin ouverture boutique")

    if context_summary.get("weather_effect", 0) <= -0.15:
        parts.append("météo défavorable pluie")

    if urgency == "HIGH":
        parts.append("urgence haute action immédiate")

    if advisor_name:
        parts.append(f"coaching conseiller {advisor_name}")

    return " ".join(parts)


async def get_rag_context(
    store_id: str = DEFAULT_STORE_ID,
    gap_pct: float = 0.0,
    current_hour: int = None,
    urgency: str = "MEDIUM",
    context_summary: dict = None,
    advisor_name: str = "",
    top_k: int = 3,
) -> dict:
    """
    Récupère les scripts de vente pertinents depuis Milvus.

    Returns:
        {
            "scripts": [...],       # scripts pertinents
            "rag_context": "...",   # texte formaté pour le prompt LLM
            "available": bool,      # RAG disponible
        }
    """
    client = _get_client()
    if client is None:
        return {"scripts": [], "rag_context": "", "available": False}

    if current_hour is None:
        current_hour = datetime.now().hour
    if context_summary is None:
        context_summary = {}

    # Construire la requête
    query = _build_query(gap_pct, current_hour, urgency, context_summary, advisor_name)
    logger.info(f"[RAG] Requête: '{query}'")

    # Générer l'embedding
    embedding = _embed(query)
    if embedding is None:
        return {"scripts": [], "rag_context": "", "available": False}

    # Recherche Milvus
    try:
        results = client.search(
            collection_name = COLLECTION,
            data            = [embedding],
            limit           = top_k + 2,  # marge pour filtrage heure
            output_fields   = [
                "pg_id", "categorie", "situation", "action",
                "produit", "argument", "impact",
                "heure_min", "heure_max", "jour_semaine",
            ],
        )
    except Exception as e:
        logger.warning(f"[RAG] Recherche Milvus échouée: {e}")
        return {"scripts": [], "rag_context": "", "available": False}

    # Filtrer et scorer les résultats
    scripts = []
    dow = datetime.now().weekday()

    for hit in results[0]:
        e     = hit["entity"]
        score = float(hit["distance"])

        # Bonus si créneau horaire correspond
        if e["heure_min"] <= current_hour <= e["heure_max"]:
            score += 0.15

        # Bonus si jour de semaine correspond
        if e["jour_semaine"] == dow or e["jour_semaine"] == -1:
            score += 0.05

        scripts.append({
            "score":       round(score, 3),
            "categorie":   e["categorie"],
            "situation":   e["situation"],
            "action":      e["action"],
            "produit":     e["produit"],
            "argument":    e["argument"],
            "impact":      e["impact"],
        })

    # Trier par score et garder top_k
    scripts = sorted(scripts, key=lambda x: x["score"], reverse=True)[:top_k]

    # Formater le contexte RAG pour le prompt
    rag_lines = ["## Scripts de vente similaires (base historique Ooredoo I63)"]
    for i, s in enumerate(scripts, 1):
        rag_lines.append(
            f"\n### Script #{i} — {s['categorie']} (score: {s['score']:.2f})\n"
            f"**Situation**: {s['situation']}\n"
            f"**Action**: {s['action']}\n"
            f"**Produit**: {s['produit']}\n"
            f"**Argument**: {s['argument']}\n"
            f"**Impact observé**: {s['impact']}"
        )

    rag_context = "\n".join(rag_lines)
    logger.info(f"[RAG] {len(scripts)} scripts récupérés (top: {scripts[0]['categorie'] if scripts else 'N/A'})")

    return {
        "scripts":     scripts,
        "rag_context": rag_context,
        "available":   True,
        "query":       query,
    }


async def get_coach_chat_context(
    advisor_name: str,
    question: str,
    store_id: str = DEFAULT_STORE_ID,
    current_hour: int = None,
) -> dict:
    """
    RAG spécifique pour le Coach Chat.
    Recherche les scripts pertinents pour répondre à la question du conseiller.
    """
    client = _get_client()
    if client is None:
        return {"scripts": [], "rag_context": "", "available": False}

    if current_hour is None:
        current_hour = datetime.now().hour

    # Construire requête depuis la question du conseiller
    query = f"{question} conseiller {advisor_name} heure {current_hour}"
    embedding = _embed(query)

    if embedding is None:
        return {"scripts": [], "rag_context": "", "available": False}

    try:
        results = client.search(
            collection_name = COLLECTION,
            data            = [embedding],
            limit           = 4,
            output_fields   = ["categorie", "action", "produit", "argument", "impact"],
        )
    except Exception as e:
        logger.warning(f"[RAG] Coach chat search failed: {e}")
        return {"scripts": [], "rag_context": "", "available": False}

    scripts = []
    for hit in results[0]:
        e = hit["entity"]
        scripts.append({
            "score":     round(float(hit["distance"]), 3),
            "categorie": e["categorie"],
            "action":    e["action"],
            "produit":   e["produit"],
            "argument":  e["argument"],
            "impact":    e["impact"],
        })

    rag_lines = ["Exemples de réponses qui ont fonctionné dans des situations similaires:"]
    for s in scripts[:3]:
        rag_lines.append(f"- [{s['produit']}]: {s['action']} → {s['impact']}")

    return {
        "scripts":     scripts,
        "rag_context": "\n".join(rag_lines),
        "available":   True,
    }
