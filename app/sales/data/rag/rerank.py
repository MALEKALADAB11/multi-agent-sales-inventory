"""
rag/rerank.py — Rerank métier + diversité MMR.

Pourquoi deux signaux et pas un rang de fusion
──────────────────────────────────────────────
La recherche se fait par domaine (voir retriever._fan_out), donc chaque domaine
renvoie son propre top-1. Classer sur le rang seul les mettrait tous à égalité :
la fiche « PAIEMENT PAR ANTICIPATION », meilleure fiche produit pour « quel
produit pousser ? » faute de mieux, arriverait au même niveau qu'un playbook
parfaitement pertinent.

On combine donc deux scores absolus, tous deux comparables entre domaines :

  • cosinus dense (bge-m3)  → comprend la paraphrase : « ça coûte cher » ≈
    « objection prix ». Aveugle aux noms propres proches (iPhone 16 / iPhone 6).
  • BM25 normalisé          → sait que « iPhone 16 Pro » n'est pas « iPhone 6 ».
    Aveugle à la paraphrase.

Chacun rattrape l'angle mort de l'autre. Un document que le dense ignore mais
que BM25 place en tête est repêché (SKU, acronyme, nom de modèle).

Milvus classe sur la pertinence textuelle. Il ignore qu'à 19 h un script
« pic d'affluence 15 h » est inutile, qu'une décision d'il y a 8 mois pèse moins
qu'une d'hier, et qu'un produit en rupture ne doit pas être poussé. Les boosts
métier réinjectent ces règles, puis MMR force la diversité des angles.
"""

import logging
import math
import re
import time
from datetime import datetime

import numpy as np

from app.sales.data.rag.documents import RetrievedDocument
from app.sales.data.rag.query import normalize
from app.sales.data.rag.settings import (
    DOMAIN_DECISION,
    DOMAIN_PRODUCT,
    LEXICAL_RESCUE_MIN_BM25,
    LEXICAL_RESCUE_RANK,
    MMR_LAMBDA,
    RELEVANCE_FLOOR,
    RELEVANT_MIN_BM25,
    RELEVANT_MIN_COSINE,
    W_LEXICAL,
    W_SEMANTIC,
)

logger = logging.getLogger(__name__)

_SEVEN_DAYS = 7 * 86400

# Similarité implicite entre deux documents du même domaine (voir mmr()).
_SAME_DOMAIN_PENALTY = 0.35


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", normalize(text)))


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denom) if denom else 0.0


def _freshness(updated_at: int) -> float:
    """Décroissance exponentielle, demi-vie ~7 jours. 0 si pas d'horodatage."""
    if not updated_at:
        return 0.0
    age = max(0.0, time.time() - updated_at)
    return math.exp(-age / _SEVEN_DAYS)


def score_documents(
    docs: list[RetrievedDocument],
    query: str,
    *,
    hour: int | None = None,
    store_id: str = "",
    preferred_domains: tuple[str, ...] = (),
    out_of_stock_skus: frozenset[str] = frozenset(),
    relevance_floor: float = RELEVANCE_FLOOR,
) -> list[RetrievedDocument]:
    """Applique le score absolu puis les boosts métier. Chaque boost est tracé."""
    if not docs:
        return []

    if hour is None:
        hour = datetime.now().hour
    dow = datetime.now().weekday()
    q_tokens = _tokens(query)

    # BM25 n'a pas de borne haute : on normalise sur le meilleur candidat de la
    # requête, toutes branches et tous domaines confondus. L'IDF étant calculé
    # sur toute la collection, les scores sont bien comparables entre domaines.
    max_bm25 = max((d.bm25 for d in docs), default=0.0) or 1.0

    kept: list[RetrievedDocument] = []

    for doc in docs:
        boosts: dict[str, float] = {}
        payload = doc.payload or {}

        bm25_norm = min(1.0, doc.bm25 / max_bm25) if doc.bm25 > 0 else 0.0

        # Repêchage lexical : un document que le dense écarte mais que BM25 place
        # en tête AVEC un score absolu solide (SKU, nom de modèle, acronyme).
        # La double condition compte : normalisé par le max de la requête, un
        # BM25 faible paraît excellent quand tous les candidats sont mauvais.
        found_by_lexical = (0 <= doc.bm25_rank <= LEXICAL_RESCUE_RANK
                            and doc.bm25 >= LEXICAL_RESCUE_MIN_BM25)

        # ── Filtre de pertinence ─────────────────────────────────────────────
        if doc.cosine < relevance_floor and not found_by_lexical:
            continue

        # ── Score de base : sémantique + lexical, tous deux absolus ──────────
        # Remappe [floor, 1] sur [0, 1] pour étaler les scores utiles.
        cos_norm = max(0.0, doc.cosine - relevance_floor) / max(1e-6, 1.0 - relevance_floor)
        score = W_SEMANTIC * cos_norm + W_LEXICAL * bm25_norm

        # ── Boosts métier ────────────────────────────────────────────────────
        h_min = int(payload.get("heure_min", 0) or 0)
        h_max = int(payload.get("heure_max", 24) or 24)
        if h_min <= hour <= h_max:
            boosts["creneau"] = 0.10

        jour = payload.get("jour_semaine")
        if jour is not None and int(jour) == dow:
            boosts["jour"] = 0.05

        if store_id and payload.get("store_id") == store_id:
            boosts["boutique"] = 0.08

        if doc.domain in preferred_domains:
            boosts["domaine_intent"] = 0.06

        # Recouvrement lexical : garde-fou contre une dérive purement sémantique.
        if q_tokens:
            overlap = len(q_tokens & _tokens(doc.text)) / len(q_tokens)
            if overlap > 0:
                boosts["lexical"] = round(min(0.12, 0.12 * overlap), 3)

        # La mémoire des décisions ne vaut que si elle est récente.
        if doc.domain == DOMAIN_DECISION:
            fresh = _freshness(int(payload.get("updated_at", 0) or 0))
            boosts["fraicheur"] = round(0.15 * fresh - 0.05, 3)

        # Ne jamais faire remonter un produit qu'on ne peut pas vendre.
        if doc.domain == DOMAIN_PRODUCT:
            if doc.sku and doc.sku in out_of_stock_skus:
                boosts["rupture"] = -0.40
            if payload.get("promo_active"):
                boosts["promo"] = 0.07

            stock = payload.get("stock_dispo")
            if stock is not None and stock > 0:
                boosts["en_stock"] = 0.10
            elif stock == 0:
                boosts["stock_zero"] = -0.20

            # Un SKU qui tourne bat une référence dormante à pertinence égale.
            qte = int(payload.get("qte_30j", 0) or 0)
            if qte > 0:
                boosts["velocite"] = round(min(0.12, 0.03 * math.log1p(qte)), 3)

        doc.boosts = boosts
        doc.score = round(max(0.0, min(1.0, score + sum(boosts.values()))), 3)
        kept.append(doc)

    dropped = len(docs) - len(kept)
    if dropped:
        logger.debug("[RAG] %d candidats sous le plancher de pertinence", dropped)

    return sorted(kept, key=lambda d: d.score, reverse=True)


def is_relevant(docs: list[RetrievedDocument]) -> bool:
    """
    Le RAG a-t-il vraiment trouvé quelque chose ?

    On regarde les preuves brutes du meilleur document, pas son score final :
    créneau + boutique + fraîcheur + domaine cumulent près de 0,3 de bonus, de
    quoi hisser n'importe quel document au-dessus d'un seuil de score. Une
    requête « recette du couscous » ramenait ainsi un conseil de coaching à 0,69.

    Un `relevant` complaisant est pire qu'un RAG vide : il fait croire au LLM
    qu'il tient une source alors qu'il n'a que du bruit.

    On prend la meilleure preuve du lot, pas celle du mieux classé : un document
    repêché par le lexical peut dominer le classement sans porter le cosinus le
    plus élevé.
    """
    if not docs:
        return False
    return (max(d.cosine for d in docs) >= RELEVANT_MIN_COSINE
            or max(d.bm25 for d in docs) >= RELEVANT_MIN_BM25)


def mmr(
    docs: list[RetrievedDocument],
    top_k: int,
    lambda_diversity: float = MMR_LAMBDA,
) -> list[RetrievedDocument]:
    """
    Maximal Marginal Relevance. Similarité cosinus entre documents quand les
    vecteurs sont disponibles, Jaccard sur les tokens sinon.
    """
    if not docs:
        return []
    if top_k >= len(docs):
        return docs[:top_k]

    vectors = {d.doc_id: np.asarray(d.dense_vector, dtype=np.float32)
               for d in docs if d.dense_vector}
    token_sets = {d.doc_id: _tokens(d.text) for d in docs}

    def similarity(a: RetrievedDocument, b: RetrievedDocument) -> float:
        if a.doc_id in vectors and b.doc_id in vectors:
            sim = _cosine(vectors[a.doc_id], vectors[b.doc_id])
        else:
            ta, tb = token_sets[a.doc_id], token_sets[b.doc_id]
            union = len(ta | tb)
            sim = len(ta & tb) / union if union else 0.0
        # Deux documents du même domaine se ressemblent par nature, même quand
        # leurs mots diffèrent : sans cette pénalité le top_k se remplit de trois
        # playbooks stock et le coach perd l'angle vente.
        if a.domain == b.domain:
            sim += _SAME_DOMAIN_PENALTY
        return sim

    selected: list[RetrievedDocument] = [docs[0]]
    pool = docs[1:]

    while len(selected) < top_k and pool:
        best, best_val = None, -1e9
        for cand in pool:
            max_sim = max(similarity(cand, sel) for sel in selected)
            val = (1 - lambda_diversity) * cand.score - lambda_diversity * max_sim
            if val > best_val:
                best, best_val = cand, val
        selected.append(best)
        pool.remove(best)

    return selected
