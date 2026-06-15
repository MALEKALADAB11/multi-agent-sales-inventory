"""
rag_retriever.py — Retriever RAG pour l'agent stratège Ooredoo.
Lit les scripts de vente depuis Milvus et PostgreSQL.

Utilisation:
    from data.rag_retriever import get_rag_context
    ctx = await get_rag_context(store_id, gap_pct, current_hour, query)
"""

import logging
import os
import requests
from datetime import datetime
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

MILVUS_URI  = "http://localhost:19530"
COLLECTION  = "coaching_scripts"
EMBED_MODEL = "nomic-embed-text"
OLLAMA_URL  = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# Cache singleton du client Milvus
_milvus_client = None


def _get_client():
    global _milvus_client
    if _milvus_client is None:
        try:
            from pymilvus import MilvusClient
            if True:
                _milvus_client = MilvusClient(uri="http://localhost:19530")
                logger.info(f"[RAG] Milvus connecté: {MILVUS_URI}")
            else:
                logger.warning(f"[RAG] Milvus DB non trouvée: {MILVUS_DB} — RAG désactivé")
        except ImportError:
            logger.warning("[RAG] pymilvus non installé — RAG désactivé")
    return _milvus_client


def _embed(text: str) -> Optional[list]:
    """Génère un embedding via Ollama."""
    try:
        # Timeout increased to 120s (2 min) because OLLAMA can be slow under load
        # First attempt: 120s full timeout
        resp = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=120,  # Increased from 30 to 120 seconds
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info(f"[RAG] Embedding succès ({len(text)} chars)")
        return result.get("embedding")
    except requests.Timeout:
        logger.warning(f"[RAG] Embedding timeout (120s) — OLLAMA trop lent")
        return None
    except requests.ConnectionError as e:
        logger.warning(f"[RAG] Embedding connexion échouée: {e}")
        return None
    except Exception as e:
        logger.warning(f"[RAG] Embedding échoué: {e}")
        return None


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
    store_id: str = "I63",
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
    store_id: str = "I63",
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