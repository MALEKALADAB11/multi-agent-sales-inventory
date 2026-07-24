"""
rag/retriever.py — Point d'entrée unique du RAG.

Pipeline complet :

    requête ──▶ expansion vocabulaire ──▶ hybrid_search (dense + BM25, RRF)
                                              │
                                              ▼
                          rerank métier (créneau, boutique, fraîcheur,
                          rupture, recouvrement lexical)
                                              │
                                              ▼
                             MMR (diversité des angles) ──▶ top_k
                                              │
                                              ▼
                          bloc de contexte cité [S1] [P2] [D3]

Les citations ne sont pas cosmétiques : le prompt du coach lui interdit
d'affirmer un chiffre ou un argument sans référencer le document qui le porte.
C'est ce qui rend l'hallucination détectable au lieu d'invisible.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np

from app.sales.data.rag import query as q
from app.sales.data.rag import store
from app.sales.data.rag.documents import RetrievedDocument
from app.sales.data.rag.embeddings import embed_query
from app.sales.data.rag.live import hydrate_products
from app.sales.data.rag.rerank import is_relevant, mmr, score_documents
from app.sales.data.rag.settings import (
    CANDIDATE_K,
    DOMAIN_DECISION,
    DOMAIN_INVENTORY_PLAYBOOK,
    DOMAIN_PRODUCT,
    DOMAIN_SALES_SCRIPT,
    MIN_SCORE,
    TOP_K,
)

logger = logging.getLogger(__name__)

# Préfixe de citation par domaine — stable, le prompt s'y réfère.
_CITE_PREFIX = {
    DOMAIN_SALES_SCRIPT:       "S",
    DOMAIN_INVENTORY_PLAYBOOK: "I",
    DOMAIN_PRODUCT:            "P",
    DOMAIN_DECISION:           "D",
}

_DOMAIN_TITLE = {
    DOMAIN_SALES_SCRIPT:       "SCRIPTS DE VENTE ÉPROUVÉS (terrain Ooredoo)",
    DOMAIN_INVENTORY_PLAYBOOK: "PLAYBOOKS STOCK & APPROVISIONNEMENT",
    DOMAIN_PRODUCT:            "FICHES PRODUIT (catalogue réel, chiffres exacts)",
    DOMAIN_DECISION:           "MÉMOIRE DES DÉCISIONS PASSÉES (ce qui a marché)",
}


def _fan_out(
    expanded_query: str,
    domains: list[str],
    store_id: str,
    candidate_k: int,
    qvec: list[float] | None,
) -> tuple[list[RetrievedDocument], str]:
    """
    Une recherche hybride par domaine, puis union.

    Un classement global unique affame systématiquement les domaines dont le
    texte ne ressemble pas à la question. Mesuré sur « combien coûte l'iPhone 16
    Pro » : les 12 premiers résultats étaient des scripts de vente (qui *parlent*
    d'iPhone), la fiche produit portant le vrai prix arrivait 16e — hors top_k.

    En cherchant par domaine, chaque domaine propose ses meilleurs candidats.
    C'est ensuite le rerank qui arbitre, sur des scores absolus (cosinus, BM25
    normalisé) et des critères métier explicites.

    La clé de partition `domain` fait de chaque recherche un scan local : le
    fan-out coûte quelques dizaines de ms, pas N fois la recherche complète.
    """
    if not domains:
        return [], "none"

    # Chaque domaine doit proposer un pool suffisant : à 6 candidats, le bon
    # script « bundle accessoire » sortait du top-6 dense de son domaine et
    # disparaissait avant même le rerank. Les recherches étant locales et
    # partitionnées, sur-échantillonner ne coûte que quelques millisecondes.
    per_domain_k = max(12, candidate_k // 2)

    def _search(domain: str):
        return domain, store.dual_search(
            expanded_query, domains=[domain], store_id=store_id,
            limit=per_domain_k, qvec=qvec, with_vectors=True,
        )

    merged: list[RetrievedDocument] = []
    modes: set[str] = set()

    with ThreadPoolExecutor(max_workers=min(4, len(domains))) as pool:
        for _domain, (docs, mode) in pool.map(_search, domains):
            modes.add(mode)
            merged.extend(docs)

    if not merged:
        return [], "none"

    # Un document trouvé par BM25 seul n'a pas de cosinus : Milvus ne l'a pas
    # renvoyé côté dense. Comme on rapatrie son vecteur de toute façon, on
    # calcule la similarité ici. Sans ça, la quasi-totalité des candidats
    # arriverait au reranker avec cosine=0 et le score dense serait ignoré.
    if qvec is not None:
        q = np.asarray(qvec, dtype=np.float32)
        q_norm = float(np.linalg.norm(q)) or 1.0
        for doc in merged:
            if doc.cosine == 0.0 and doc.dense_vector:
                v = np.asarray(doc.dense_vector, dtype=np.float32)
                denom = float(np.linalg.norm(v)) * q_norm
                doc.cosine = round(float(np.dot(q, v) / denom), 4) if denom else 0.0

    effective = ("hybrid" if "hybrid" in modes else
                 "dense" if "dense" in modes else
                 "bm25" if "bm25" in modes else "none")
    return merged, effective


@dataclass
class RetrievalResult:
    docs: list[RetrievedDocument] = field(default_factory=list)
    relevant: bool = False          # au moins un document au-dessus de MIN_SCORE
    mode: str = "none"              # hybrid | bm25 | dense | none
    expanded_query: str = ""
    latency_ms: float = 0.0

    @property
    def top_score(self) -> float:
        return self.docs[0].score if self.docs else 0.0

    def by_domain(self, domain: str) -> list[RetrievedDocument]:
        return [d for d in self.docs if d.domain == domain]


def retrieve(
    query: str,
    *,
    store_id: str = "",
    hour: int | None = None,
    top_k: int = TOP_K,
    domains: list[str] | None = None,
    out_of_stock_skus: frozenset[str] = frozenset(),
    min_score: float = MIN_SCORE,
    candidate_k: int = CANDIDATE_K,
    hydrate: bool = True,
) -> RetrievalResult:
    """
    Recherche hybride + rerank. Ne lève jamais : un RAG muet vaut mieux qu'un crash.

    `hydrate` relit prix, marge, stock et promo en base pour les fiches produit
    retenues : Milvus dit quels documents sont pertinents, Postgres dit ce qu'ils
    valent maintenant. Le désactiver n'a de sens que pour l'évaluation hors ligne.
    """
    started = time.perf_counter()

    if not query or not query.strip():
        return RetrievalResult()

    target_domains = domains if domains is not None else q.domains_for(query)
    expanded = q.expand(query)

    # Embedding calculé une seule fois, partagé par les N recherches du fan-out
    # et réutilisé par le reranker pour le cosinus absolu.
    qvec = embed_query(expanded)

    try:
        candidates, mode = _fan_out(expanded, target_domains, store_id, candidate_k, qvec)
    except Exception as e:
        logger.error("[RAG] retrieve échoué: %.140s", str(e))
        return RetrievalResult(expanded_query=expanded)

    if not candidates:
        return RetrievalResult(
            mode=mode, expanded_query=expanded,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    # Avant le rerank, pas après : les bonus « en stock » / « rupture » doivent
    # porter sur le stock réel, sinon on remonte un produit vendu depuis.
    if hydrate and store_id:
        candidates = hydrate_products(candidates, store_id)

    scored = score_documents(
        candidates, query,
        hour=hour, store_id=store_id,
        preferred_domains=q.preferred_domains(query),
        out_of_stock_skus=out_of_stock_skus,
    )
    if not scored:
        return RetrievalResult(
            mode=mode, expanded_query=expanded,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
    # MMR choisit *quels* documents retenir (diversité d'angles) ; l'ordre de
    # présentation au LLM reste celui de la pertinence.
    selected = sorted(mmr(scored, top_k), key=lambda d: d.score, reverse=True)

    elapsed = (time.perf_counter() - started) * 1000
    # `relevant` juge les preuves du meilleur candidat AVANT diversification :
    # MMR peut placer en tête un document choisi pour son angle, pas son score.
    relevant = is_relevant(scored) and scored[0].score >= min_score

    logger.info(
        "[RAG] %s | %d/%d docs | top=%.2f | relevant=%s | %.0fms | q='%.50s'",
        mode, len(selected), len(candidates),
        selected[0].score if selected else 0.0, relevant, elapsed, query,
    )

    return RetrievalResult(
        docs=selected, relevant=relevant, mode=mode,
        expanded_query=expanded, latency_ms=elapsed,
    )


def retrieve_for_cycle(
    *,
    store_id: str = "",
    gap_pct: float = 0.0,
    hour: int | None = None,
    urgency: str = "MEDIUM",
    weather_effect: float = 0.0,
    advisor_name: str = "",
    critical_stock: int = 0,
    top_k: int = TOP_K,
) -> RetrievalResult:
    """Retrieval du cycle autonome : la requête est dérivée de la situation."""
    synthetic = q.build_context_query(
        gap_pct=gap_pct, hour=hour, urgency=urgency,
        weather_effect=weather_effect, advisor_name=advisor_name,
        critical_stock=critical_stock,
    )
    domains = [DOMAIN_SALES_SCRIPT, DOMAIN_DECISION]
    if critical_stock > 0:
        domains.append(DOMAIN_INVENTORY_PLAYBOOK)
    return retrieve(synthetic, store_id=store_id, hour=hour,
                    top_k=top_k, domains=domains)


# ══════════════════════════════════════════════════════════════════════════════
# FORMATAGE POUR PROMPT
# ══════════════════════════════════════════════════════════════════════════════

def _render(doc: RetrievedDocument, tag: str) -> str:
    p = doc.payload or {}

    if doc.domain == DOMAIN_SALES_SCRIPT:
        return (
            f"[{tag}] {doc.categorie} — {p.get('situation', doc.title)}\n"
            f"     Action : {p.get('action', '')}\n"
            f"     Argument : «{p.get('argument', '')}»\n"
            f"     Impact observé : {p.get('impact', '')}"
        )

    if doc.domain == DOMAIN_INVENTORY_PLAYBOOK:
        return (
            f"[{tag}] {doc.doc_type} — {p.get('situation', doc.title)}\n"
            f"     Procédure : {p.get('action', '')}\n"
            f"     Seuil/règle : {p.get('regle', '—')}\n"
            f"     Effet attendu : {p.get('impact', '')}"
        )

    if doc.domain == DOMAIN_PRODUCT:
        if p.get("retire_du_catalogue"):
            return (f"[{tag}] {doc.produit} (SKU {doc.sku}) — RETIRÉ DU CATALOGUE, "
                    f"ne le propose pas")

        stock = p.get("stock_dispo")
        stock_txt = ("disponibilité inconnue" if stock is None
                     else "INDISPONIBLE en boutique" if stock == 0
                     else f"{stock} en stock")
        marge = p.get("marge_pct")
        marge_txt = "marge non renseignée" if marge is None else f"marge {marge:.0f}%"
        promo = f" | PROMO -{p.get('discount_pct'):.0f}%" if p.get("promo_active") else ""
        # Chiffres non relus en base : le coach doit pouvoir nuancer.
        stale = "" if p.get("live") else "  (chiffres non confirmés en base)"
        # `categorie` est un code numérique ('50', '70'). Collé après un tiret, il
        # se lisait comme un prix : le coach a annoncé « 50 DT » pour un iPhone à
        # 6 349 TND. Chaque nombre exposé au LLM doit porter son unité.
        gamme = p.get("gamme") or ""
        return (
            f"[{tag}] {doc.produit} (SKU {doc.sku})"
            + (f" — gamme {gamme}" if gamme else "") + "\n"
            f"     Prix {p.get('prix_ttc', 0):.0f} TND TTC | {marge_txt} "
            f"| {stock_txt}{promo}{stale}"
        )

    if doc.domain == DOMAIN_DECISION:
        return (
            f"[{tag}] {doc.title}\n"
            f"     Décision : {p.get('action', '')}\n"
            f"     Résultat : {p.get('impact', 'non mesuré')}"
        )

    return f"[{tag}] {doc.title}\n     {doc.text[:200]}"


def format_context_block(result: RetrievalResult | list[RetrievedDocument],
                         max_per_domain: int = 3) -> str:
    """Bloc injectable dans un prompt système, groupé par domaine et cité."""
    docs = result.docs if isinstance(result, RetrievalResult) else result
    if not docs:
        return ""

    counters: dict[str, int] = {}
    grouped: dict[str, list[str]] = {}

    for doc in docs:
        prefix = _CITE_PREFIX.get(doc.domain, "X")
        counters[prefix] = counters.get(prefix, 0) + 1
        if counters[prefix] > max_per_domain:
            continue
        tag = f"{prefix}{counters[prefix]}"
        grouped.setdefault(doc.domain, []).append(_render(doc, tag))

    blocks: list[str] = []
    for domain in (DOMAIN_SALES_SCRIPT, DOMAIN_INVENTORY_PLAYBOOK,
                   DOMAIN_PRODUCT, DOMAIN_DECISION):
        if domain in grouped:
            blocks.append(_DOMAIN_TITLE[domain] + " :\n" + "\n".join(grouped[domain]))

    return "\n\n".join(blocks)


def citation_index(result: RetrievalResult) -> dict[str, str]:
    """tag → doc_id, pour vérifier a posteriori qu'une citation du LLM existe."""
    counters: dict[str, int] = {}
    index: dict[str, str] = {}
    for doc in result.docs:
        prefix = _CITE_PREFIX.get(doc.domain, "X")
        counters[prefix] = counters.get(prefix, 0) + 1
        index[f"{prefix}{counters[prefix]}"] = doc.doc_id
    return index
