"""
app.sales.data.rag — Moteur RAG unifié du coach (vente + stock).

    from app.sales.data.rag import retrieve, format_context_block

    result = retrieve("le S25 est en rupture, je propose quoi ?",
                      store_id="I63", hour=16, top_k=4)
    prompt_block = format_context_block(result)

Recherche hybride Milvus (dense nomic-embed + BM25 natif, fusion RRF), rerank
métier, diversité MMR, contexte cité. Voir retriever.py pour le pipeline complet.
"""

from app.sales.data.rag.documents import Document, RetrievedDocument
from app.sales.data.rag.retriever import (
    RetrievalResult,
    citation_index,
    format_context_block,
    retrieve,
    retrieve_for_cycle,
)
from app.sales.data.rag.settings import (
    DOMAIN_DECISION,
    DOMAIN_INVENTORY_PLAYBOOK,
    DOMAIN_PRODUCT,
    DOMAIN_SALES_SCRIPT,
)
from app.sales.data.rag.store import is_available, stats

__all__ = [
    "Document",
    "RetrievedDocument",
    "RetrievalResult",
    "retrieve",
    "retrieve_for_cycle",
    "format_context_block",
    "citation_index",
    "is_available",
    "stats",
    "DOMAIN_SALES_SCRIPT",
    "DOMAIN_INVENTORY_PLAYBOOK",
    "DOMAIN_PRODUCT",
    "DOMAIN_DECISION",
]
