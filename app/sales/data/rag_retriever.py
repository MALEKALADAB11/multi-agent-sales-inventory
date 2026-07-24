"""
rag_retriever.py — Façade de compatibilité vers `app.sales.data.rag`.

Le moteur vit désormais dans le package `app/sales/data/rag/` : recherche hybride
Milvus (dense + BM25 natif, fusion RRF), rerank métier, diversité MMR, corpus
cross-domaine (scripts de vente, playbooks stock, catalogue produits, mémoire des
décisions). Voir `rag/retriever.py`.

Ce module conserve l'ancienne signature pour les appelants existants
(coach_chat.py, stratege/nodes.py). Pour tout nouveau code :

    from app.sales.data.rag import retrieve, format_context_block
"""

import logging
from datetime import datetime
from typing import Optional

from app.core.config import DEFAULT_STORE_ID
from app.sales.data.rag import retriever as _r
from app.sales.data.rag import store as _store
from app.sales.data.rag.settings import MIN_SCORE, TOP_K

logger = logging.getLogger(__name__)


def _as_legacy_script(doc) -> dict:
    """Aplati un RetrievedDocument au format attendu par l'ancien code."""
    p = doc.payload or {}
    return {
        "score":     doc.score,
        "categorie": doc.categorie or doc.doc_type,
        "situation": p.get("situation", doc.title),
        "action":    p.get("action", ""),
        "produit":   doc.produit,
        "argument":  p.get("argument", ""),
        "impact":    p.get("impact", ""),
        # Champs ajoutés par le nouveau moteur — ignorés par l'ancien code.
        "domain":    doc.domain,
        "doc_id":    doc.doc_id,
    }


def search_scripts(
    query: str,
    hour: Optional[int] = None,
    top_k: int = TOP_K,
    min_score: float = MIN_SCORE,
) -> dict:
    """
    Recherche unifiée. Retourne {"scripts": [...], "relevant": bool, "source": str}.

    `source` vaut désormais le mode de recherche Milvus ("hybrid", "bm25",
    "dense") au lieu de "milvus"/"corpus". Les appelants ne testent que
    l'égalité à "corpus" pour logger une dégradation — ce qui n'arrive plus.
    """
    result = _r.retrieve(query, hour=hour, top_k=top_k, min_score=min_score)
    return {
        "scripts":  [_as_legacy_script(d) for d in result.docs],
        "relevant": result.relevant,
        "source":   result.mode,
    }


def format_scripts_block(scripts: list, max_n: int = 3) -> str:
    """Formate des scripts (dicts legacy) pour injection dans un prompt."""
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


async def get_rag_context(
    store_id: str = DEFAULT_STORE_ID,
    gap_pct: float = 0.0,
    current_hour: int = None,
    urgency: str = "MEDIUM",
    context_summary: dict = None,
    advisor_name: str = "",
    top_k: int = 3,
) -> dict:
    """Contexte RAG du cycle autonome (requête dérivée de la situation)."""
    context_summary = context_summary or {}
    result = _r.retrieve_for_cycle(
        store_id=store_id,
        gap_pct=gap_pct,
        hour=current_hour if current_hour is not None else datetime.now().hour,
        urgency=urgency,
        weather_effect=float(context_summary.get("weather_effect", 0.0) or 0.0),
        advisor_name=advisor_name,
        critical_stock=int(context_summary.get("critical_stock", 0) or 0),
        top_k=top_k,
    )
    return {
        "scripts":     [_as_legacy_script(d) for d in result.docs],
        "rag_context": _r.format_context_block(result),
        "available":   result.mode != "none",
        "query":       result.expanded_query,
    }


async def get_coach_chat_context(
    advisor_name: str,
    question: str,
    store_id: str = DEFAULT_STORE_ID,
    current_hour: int = None,
) -> dict:
    """Contexte RAG du Coach Chat (requête = question du conseiller)."""
    result = _r.retrieve(
        question,
        store_id=store_id,
        hour=current_hour if current_hour is not None else datetime.now().hour,
        top_k=4,
    )
    return {
        "scripts":     [_as_legacy_script(d) for d in result.docs],
        "rag_context": _r.format_context_block(result),
        "available":   result.mode != "none",
    }


def health() -> dict:
    """Sonde RAG exposée par /coach/health."""
    from app.sales.data.rag.embeddings import health as embed_health
    return {"milvus": _store.stats(), "embeddings": embed_health()}
