"""
rag/documents.py — Le document unique indexé par le RAG.

Tous les corpus (scripts de vente, playbooks stock, catalogue produits, mémoire
des décisions) se ramènent à ce même objet. C'est ce qui permet une collection
Milvus unique, une seule recherche hybride, et un coach réellement cross-domaine :
une requête « rupture iPhone » peut ramener un playbook stock ET un script de
substitution ET la fiche produit, classés ensemble par le même ranker.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

# jour_semaine = -1 → applicable tous les jours
ANY_DAY = -1


@dataclass
class Document:
    doc_id: str                      # clé primaire stable → upsert idempotent
    domain: str                      # settings.DOMAIN_* (clé de partition Milvus)
    title: str
    text: str                        # champ indexé (dense + BM25)
    doc_type: str = ""               # sous-type libre : "closing", "rupture", "sku"...
    categorie: str = ""
    produit: str = ""
    sku: str = ""
    store_id: str = ""               # "" → applicable à toutes les boutiques
    heure_min: int = 0
    heure_max: int = 24
    jour_semaine: int = ANY_DAY
    updated_at: int = 0              # epoch secondes → boost de fraîcheur
    payload: dict[str, Any] = field(default_factory=dict)

    def to_row(self, dense: list[float]) -> dict:
        """Ligne Milvus. `sparse` est absent : la fonction BM25 le calcule côté serveur."""
        # Coercition explicite : Postgres renvoie des sku numériques, des Decimal
        # et des None. Milvus refuse tout ce qui n'est pas une str sur un VARCHAR.
        def s(value, limit: int) -> str:
            return ("" if value is None else str(value))[:limit]

        return {
            "doc_id":       s(self.doc_id, 128),
            "domain":       s(self.domain, 32),
            "doc_type":     s(self.doc_type, 64),
            "title":        s(self.title, 512),
            "text":         s(self.text, 8000),
            "categorie":    s(self.categorie, 100),
            "produit":      s(self.produit, 200),
            "sku":          s(self.sku, 64),
            "store_id":     s(self.store_id, 20),
            "heure_min":    int(self.heure_min),
            "heure_max":    int(self.heure_max),
            "jour_semaine": int(self.jour_semaine),
            "updated_at":   int(self.updated_at),
            "payload":      self.payload,
            "dense":        dense,
        }


@dataclass
class RetrievedDocument:
    """Document remonté par le retriever, avec sa traçabilité de scoring."""
    doc_id: str
    domain: str
    title: str
    text: str
    doc_type: str = ""
    categorie: str = ""
    produit: str = ""
    sku: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    # Signaux bruts des deux branches de recherche (voir store.dual_search).
    bm25: float = 0.0                # score BM25 Milvus — comparable entre domaines
    cosine: float = 0.0              # similarité dense bge-m3
    bm25_rank: int = -1              # rang lexical dans son domaine (-1 = absent)
    dense_rank: int = -1             # rang sémantique dans son domaine (-1 = absent)

    score: float = 0.0               # score normalisé 0..1 après rerank
    boosts: dict[str, float] = field(default_factory=dict)   # explicabilité
    dense_vector: Optional[list[float]] = None               # pour la diversité MMR

    def cite(self) -> str:
        """Référence courte affichable dans une réponse LLM."""
        return f"{self.domain}/{self.doc_type or self.categorie or 'doc'}"
