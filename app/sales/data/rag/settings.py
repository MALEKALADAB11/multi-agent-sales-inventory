"""
rag/settings.py — Paramètres du moteur RAG (une seule source de vérité).

Toutes les valeurs sont surchargeables par variable d'environnement afin que
le comportement du retrieval soit ajustable sans toucher au code (utile pour
l'évaluation : on fait varier RAG_TOP_K / RAG_MIN_SCORE et on rejoue le golden set).
"""

import os
from pathlib import Path


def _prefer_ipv4(url: str) -> str:
    """
    `localhost` résout en ::1 avant 127.0.0.1 sur Windows. Ollama n'écoute qu'en
    IPv4 : chaque appel paie ~2,1 s d'échec de connexion IPv6 avant de retomber
    sur IPv4 (mesuré : 2227 ms vs 87 ms). On force donc l'IPv4 littérale.
    """
    return url.replace("//localhost:", "//127.0.0.1:")


# ── Milvus ────────────────────────────────────────────────────────────────────
MILVUS_URI       = _prefer_ipv4(os.getenv("MILVUS_URI", "http://localhost:19530"))
COLLECTION       = os.getenv("RAG_COLLECTION", "retail_knowledge")
# Court : c'est un service local. Un Milvus qui ne répond pas en 4 s est en panne,
# pas lent — et le coach a une réponse à rendre. Le disjoncteur (store.py) évite
# de repayer ce délai à chaque message.
MILVUS_TIMEOUT   = float(os.getenv("RAG_MILVUS_TIMEOUT", "4"))

# ── Embeddings (Ollama) ───────────────────────────────────────────────────────
# bge-m3 plutôt que nomic-embed-text : nomic est entraîné majoritairement sur de
# l'anglais et ne sépare pas nos textes courts en français. Mesuré sur la requête
# « combien coûte l'iPhone 16 Pro » — écart cosinus entre la bonne fiche et une
# coque iPhone6 : nomic -0,053 (il classe la coque devant), bge-m3 +0,296.
OLLAMA_URL       = _prefer_ipv4(os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
EMBED_MODEL      = os.getenv("RAG_EMBED_MODEL", "bge-m3")
EMBED_DIM        = int(os.getenv("RAG_EMBED_DIM", "1024"))
EMBED_TIMEOUT    = float(os.getenv("RAG_EMBED_TIMEOUT", "60"))
EMBED_BATCH      = int(os.getenv("RAG_EMBED_BATCH", "16"))
EMBED_MAX_CHARS  = int(os.getenv("RAG_EMBED_MAX_CHARS", "2000"))

# Préfixes de tâche : nomic-embed-text est asymétrique et les exige, bge-m3 est
# entraîné sans. Un préfixe parasite y dégrade le cosinus.
_PREFIXED_MODELS = ("nomic-embed",)
_needs_prefix = any(EMBED_MODEL.startswith(m) for m in _PREFIXED_MODELS)

EMBED_QUERY_PREFIX = os.getenv("RAG_EMBED_QUERY_PREFIX",
                               "search_query: " if _needs_prefix else "")
EMBED_DOC_PREFIX   = os.getenv("RAG_EMBED_DOC_PREFIX",
                               "search_document: " if _needs_prefix else "")

# Cache disque des embeddings — évite de ré-embedder 4 500 produits à chaque ingestion.
CACHE_DIR = Path(os.getenv("RAG_CACHE_DIR", ".rag_cache")).resolve()

# ── Retrieval ─────────────────────────────────────────────────────────────────
TOP_K            = int(os.getenv("RAG_TOP_K", "3"))
# Nombre de candidats tirés de chaque branche (dense / BM25) avant rerank.
# Sur-échantillonner est ce qui donne au reranker de quoi travailler.
CANDIDATE_K      = int(os.getenv("RAG_CANDIDATE_K", "20"))

# Seuil de pertinence appliqué au score *normalisé* post-rerank (0..1).
MIN_SCORE        = float(os.getenv("RAG_MIN_SCORE", "0.35"))

# Plancher de similarité cosinus requête↔document. En dessous, le document est
# écarté : c'est ce qui empêche un domaine de proposer son "moins mauvais"
# document quand il n'a rien de pertinent à dire. Calibré sur bge-m3, dont les
# cosinus hors-sujet tombent sous 0,45 (voir rag/evaluation/).
RELEVANCE_FLOOR  = float(os.getenv("RAG_RELEVANCE_FLOOR", "0.45"))

# Un document que le dense ignore mais que BM25 place très haut (nom de produit,
# SKU, acronyme métier) est repêché : le lexical sait ce que le vecteur ignore.
# Le seuil absolu évite de repêcher le "moins mauvais" d'une requête hors-sujet.
LEXICAL_RESCUE_RANK     = int(os.getenv("RAG_LEXICAL_RESCUE_RANK", "2"))
LEXICAL_RESCUE_MIN_BM25 = float(os.getenv("RAG_LEXICAL_RESCUE_MIN_BM25", "8.0"))

# Seuils d'ABSTENTION : ce qui décide si le RAG se déclare pertinent. Ils portent
# sur les preuves brutes (cosinus, BM25), jamais sur le score post-boosts — un
# empilement de bonus métier suffirait sinon à faire passer un document hors-sujet.
#
# Calibrés sur le golden set (voir rag/evaluation/) :
#   cosinus max — requêtes pertinentes : 0,558 à 0,734
#               — requêtes hors-sujet  : 0,358 à 0,450
#   0,52 tombe dans le trou entre les deux populations.
# Le BM25 ne sépare pas seul (couscous 5,8 contre « conseil récent » 5,6) : il
# n'est retenu que comme condition alternative, à un niveau qu'aucune requête
# hors-sujet du golden set n'atteint.
RELEVANT_MIN_COSINE = float(os.getenv("RAG_RELEVANT_MIN_COSINE", "0.52"))
RELEVANT_MIN_BM25   = float(os.getenv("RAG_RELEVANT_MIN_BM25",   "12.0"))

# Poids du score BM25 normalisé face au cosinus dans le score de base.
W_LEXICAL        = float(os.getenv("RAG_W_LEXICAL", "0.45"))
W_SEMANTIC       = float(os.getenv("RAG_W_SEMANTIC", "0.55"))

# Diversité MMR : 0 = pertinence pure, 1 = diversité pure.
MMR_LAMBDA       = float(os.getenv("RAG_MMR_LAMBDA", "0.30"))

# Rerank LLM (optionnel, coûteux) — désactivé par défaut, le rerank heuristique suffit.
LLM_RERANK       = os.getenv("RAG_LLM_RERANK", "false").lower() == "true"

# ── Domaines indexés ──────────────────────────────────────────────────────────
DOMAIN_SALES_SCRIPT       = "sales_script"
DOMAIN_INVENTORY_PLAYBOOK = "inventory_playbook"
DOMAIN_PRODUCT            = "product"
DOMAIN_DECISION           = "decision"

ALL_DOMAINS = (
    DOMAIN_SALES_SCRIPT,
    DOMAIN_INVENTORY_PLAYBOOK,
    DOMAIN_PRODUCT,
    DOMAIN_DECISION,
)
