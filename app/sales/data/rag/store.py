"""
rag/store.py — Accès Milvus : indexation et recherche à deux branches.

    bm25(query_text) ──▶ score lexical absolu, comparable entre domaines
    dense(query_vec) ──▶ rappel des paraphrases

On ne fusionne PAS côté Milvus (RRFRanker) : la fusion par rang jette les scores
bruts, or c'est justement le score BM25 qui porte l'information discriminante.
Mesuré sur ce corpus, le cosinus dense ne sépare rien — « coque IPHONE6 » obtient
0,730 contre 0,623 pour « IPHONE 16 PRO MAX » sur la requête « iPhone 16 Pro »,
tandis que BM25 donne respectivement 2,1 et 11,4. On rapatrie donc les deux
scores bruts et c'est le reranker qui arbitre, en toute transparence.

Si Ollama est indisponible, la branche dense disparaît et BM25 assure seul —
la recherche reste dans Milvus, jamais dans un fallback bricolé.
"""

import logging
import os
import threading
import time
from typing import Optional

from app.sales.data.rag.documents import Document, RetrievedDocument
from app.sales.data.rag.embeddings import embed_documents, embed_query
from app.sales.data.rag.schema import ensure_collection
from app.sales.data.rag.settings import (
    CANDIDATE_K,
    COLLECTION,
    MILVUS_TIMEOUT,
    MILVUS_URI,
)

logger = logging.getLogger(__name__)

_client = None
_client_lock = threading.Lock()
_collection_ready = False

# Disjoncteur. Sans lui, un Milvus éteint coûtait 44,7 s par message du coach :
# chaque recherche retentait la connexion (timeout 10 s), et le fan-out en lance
# une par domaine. Après un échec, on cesse d'essayer pendant BREAKER_COOLDOWN,
# ce qui rend l'indisponibilité instantanée au lieu d'être lente.
_breaker_open_until = 0.0
BREAKER_COOLDOWN = float(os.getenv("RAG_BREAKER_COOLDOWN", "30"))

OUTPUT_FIELDS = [
    "doc_id", "domain", "doc_type", "title", "text",
    "categorie", "produit", "sku", "payload",
    # Colonnes lues par le reranker métier (créneau, boutique, fraîcheur).
    "store_id", "heure_min", "heure_max", "jour_semaine", "updated_at",
]

# Colonnes recopiées dans `payload` à la lecture : le reranker ne lit qu'un dict,
# et la source de vérité reste la colonne Milvus (indexable, filtrable).
_RERANK_FIELDS = ("store_id", "heure_min", "heure_max", "jour_semaine", "updated_at")


def get_client(recreate: bool = False):
    """Client Milvus singleton. Retourne None si Milvus est injoignable."""
    global _client, _collection_ready, _breaker_open_until
    with _client_lock:
        if _client is None:
            if time.monotonic() < _breaker_open_until:
                return None      # échec récent : on n'attend pas un nouveau timeout
            try:
                from pymilvus import MilvusClient
                _client = MilvusClient(uri=MILVUS_URI, timeout=MILVUS_TIMEOUT)
                logger.info("[RAG] Milvus connecté: %s", MILVUS_URI)
            except ImportError:
                logger.error("[RAG] pymilvus non installé")
                _breaker_open_until = time.monotonic() + BREAKER_COOLDOWN
                return None
            except Exception as e:
                logger.warning("[RAG] connexion Milvus échouée (nouvel essai dans %.0fs): %.90s",
                               BREAKER_COOLDOWN, str(e))
                _breaker_open_until = time.monotonic() + BREAKER_COOLDOWN
                return None

        if not _collection_ready or recreate:
            _collection_ready = ensure_collection(_client, recreate=recreate)
            if not _collection_ready:
                _client = None
                _breaker_open_until = time.monotonic() + BREAKER_COOLDOWN
                return None

    return _client


def is_available() -> bool:
    return get_client() is not None


# ══════════════════════════════════════════════════════════════════════════════
# INDEXATION
# ══════════════════════════════════════════════════════════════════════════════

def upsert(docs: list[Document], batch_size: int = 200) -> int:
    """Embedde puis upsert les documents. Retourne le nombre de lignes écrites."""
    client = get_client()
    if client is None or not docs:
        return 0

    written = 0
    for start in range(0, len(docs), batch_size):
        chunk = docs[start:start + batch_size]
        vectors = embed_documents([d.text for d in chunk])

        rows = [d.to_row(v) for d, v in zip(chunk, vectors) if v is not None]
        skipped = len(chunk) - len(rows)
        if skipped:
            logger.warning("[RAG] %d documents sans embedding — ignorés", skipped)
        if not rows:
            continue

        try:
            client.upsert(collection_name=COLLECTION, data=rows)
            written += len(rows)
            logger.info("[RAG] upsert %d/%d", written, len(docs))
        except Exception as e:
            logger.error("[RAG] upsert échoué: %.160s", str(e))

    if written:
        try:
            client.flush(COLLECTION)
        except Exception as e:
            logger.debug("[RAG] flush: %.60s", str(e))
    return written


def delete_domain(domain: str) -> None:
    """Purge un domaine avant réindexation complète (catalogue, décisions)."""
    client = get_client()
    if client is None:
        return
    try:
        client.delete(collection_name=COLLECTION, filter=f'domain == "{domain}"')
        logger.info("[RAG] domaine '%s' purgé", domain)
    except Exception as e:
        logger.warning("[RAG] purge '%s' échouée: %.100s", domain, str(e))


def stats() -> dict:
    client = get_client()
    if client is None:
        return {"available": False}
    try:
        total = client.get_collection_stats(COLLECTION).get("row_count", 0)
        per_domain = {}
        for d in ("sales_script", "inventory_playbook", "product", "decision"):
            try:
                rows = client.query(
                    collection_name=COLLECTION,
                    filter=f'domain == "{d}"',
                    output_fields=["doc_id"],
                    limit=16384,
                )
                per_domain[d] = len(rows)
            except Exception:
                per_domain[d] = -1
        return {"available": True, "collection": COLLECTION,
                "row_count": total, "per_domain": per_domain}
    except Exception as e:
        return {"available": False, "error": str(e)[:120]}


# ══════════════════════════════════════════════════════════════════════════════
# RECHERCHE
# ══════════════════════════════════════════════════════════════════════════════

def build_filter(
    domains: Optional[list[str]] = None,
    store_id: str = "",
    sku: str = "",
) -> str:
    """
    Expression de filtrage Milvus, évaluée *avant* le parcours d'index.

    Volontairement pas de filtre horaire ici : un script hors créneau reste
    pertinent (il devient juste moins prioritaire). Filtrer dur sur l'heure
    viderait le résultat à 8h du matin. Le créneau est un boost, pas un mur.
    """
    clauses = []
    if domains:
        listed = ", ".join(f'"{d}"' for d in domains)
        clauses.append(f"domain in [{listed}]")
    if store_id:
        # store_id == "" → document global, applicable à toutes les boutiques
        clauses.append(f'(store_id == "{store_id}" or store_id == "")')
    if sku:
        clauses.append(f'sku == "{sku}"')
    return " and ".join(clauses)


def _to_retrieved(hit: dict) -> RetrievedDocument:
    e = hit.get("entity", hit)
    payload = dict(e.get("payload") or {})
    for f in _RERANK_FIELDS:
        if e.get(f) is not None:
            payload[f] = e[f]
    return _make_doc(e, payload)


def _make_doc(e: dict, payload: dict) -> RetrievedDocument:
    return RetrievedDocument(
        doc_id=e.get("doc_id", ""),
        domain=e.get("domain", ""),
        title=e.get("title", ""),
        text=e.get("text", ""),
        doc_type=e.get("doc_type", ""),
        categorie=e.get("categorie", ""),
        produit=e.get("produit", ""),
        sku=e.get("sku", ""),
        payload=payload,
        dense_vector=e.get("dense"),
    )


def _search_sparse(client, query: str, expr: str, limit: int, fields: list[str]):
    hits = client.search(
        collection_name=COLLECTION,
        data=[query],
        anns_field="sparse",
        search_params={"drop_ratio_search": 0.2},
        limit=limit,
        filter=expr or "",
        output_fields=fields,
    )
    return hits[0]


def _search_dense(client, qvec: list[float], expr: str, limit: int, fields: list[str]):
    hits = client.search(
        collection_name=COLLECTION,
        data=[qvec],
        anns_field="dense",
        search_params={"metric_type": "COSINE", "params": {"ef": 128}},
        limit=limit,
        filter=expr or "",
        output_fields=fields,
    )
    return hits[0]


def dual_search(
    query: str,
    domains: Optional[list[str]] = None,
    store_id: str = "",
    limit: int = CANDIDATE_K,
    expr: Optional[str] = None,
    qvec: Optional[list[float]] = None,
    with_vectors: bool = False,
) -> tuple[list[RetrievedDocument], str]:
    """
    Lance les deux branches et retourne l'union annotée des deux scores bruts.

    Chaque document remonté porte `bm25` (score lexical Milvus, comparable entre
    documents de la collection) et `cosine` (similarité dense), plus son rang
    dans chaque branche. Un document trouvé par une seule branche a -1 comme
    rang dans l'autre : le reranker sait ainsi distinguer « trouvé par les deux »
    (signal fort) de « trouvé seulement par le lexical » (nom propre, SKU).

    Retourne (candidats, mode) où mode ∈ {"hybrid", "bm25", "dense", "none"}.
    """
    client = get_client()
    if client is None or not query.strip():
        return [], "none"

    if expr is None:
        expr = build_filter(domains, store_id)

    fields = OUTPUT_FIELDS + ["dense"] if with_vectors else list(OUTPUT_FIELDS)

    if qvec is None:
        qvec = embed_query(query)

    by_id: dict[str, RetrievedDocument] = {}
    got_sparse = got_dense = False

    def _collect_sparse():
        nonlocal got_sparse
        try:
            for rank, hit in enumerate(_search_sparse(client, query, expr, limit, fields)):
                doc = _to_retrieved(hit)
                doc.bm25 = float(hit.get("distance", 0.0))
                doc.bm25_rank = rank
                by_id[doc.doc_id] = doc
            got_sparse = True
        except Exception as e:
            logger.warning("[RAG] branche BM25 échouée: %.120s", str(e))

    def _collect_dense():
        nonlocal got_dense
        if qvec is None:
            return
        try:
            for rank, hit in enumerate(_search_dense(client, qvec, expr, limit, fields)):
                doc = _to_retrieved(hit)
                existing = by_id.get(doc.doc_id)
                target = existing or doc
                target.cosine = float(hit.get("distance", 0.0))
                target.dense_rank = rank
                by_id[target.doc_id] = target
            got_dense = True
        except Exception as e:
            logger.warning("[RAG] branche dense échouée: %.120s", str(e))

    # Les deux branches touchent `by_id` : la sparse d'abord (elle crée les
    # entrées), la dense ensuite (elle enrichit). Séquentiel et déterministe —
    # le parallélisme utile est au niveau des domaines, pas des branches.
    _collect_sparse()
    _collect_dense()

    if not by_id:
        return [], "none"

    mode = ("hybrid" if got_sparse and got_dense else
            "bm25" if got_sparse else
            "dense" if got_dense else "none")
    return list(by_id.values()), mode
