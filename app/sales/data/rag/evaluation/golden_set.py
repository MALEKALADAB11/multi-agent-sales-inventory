"""
rag/evaluation/golden_set.py — Jeu de requêtes de référence.

Chaque cas dit ce qu'une bonne réponse DOIT contenir, sans figer un doc_id précis
(le corpus bouge : les produits et les décisions sont réindexés). On vérifie donc
des propriétés vérifiables :

    expect_domain   : le domaine qui doit apparaître dans le top_k
    expect_tokens   : au moins un de ces termes doit figurer dans un document retenu
    forbid_tokens   : aucun document retenu ne doit contenir ces termes
    expect_relevant : le retriever doit se déclarer confiant (ou non)

Les cas `expect_relevant=False` sont les plus importants : un RAG qui ne sait pas
dire « je n'ai rien de pertinent » fait halluciner le LLM en aval.
"""

from app.sales.data.rag.settings import (
    DOMAIN_DECISION,
    DOMAIN_INVENTORY_PLAYBOOK,
    DOMAIN_PRODUCT,
    DOMAIN_SALES_SCRIPT,
)

GOLDEN: list[dict] = [
    # ── Vente pure ───────────────────────────────────────────────────────────
    {
        "query": "le client trouve que c'est trop cher, comment je fais ?",
        "expect_domain": DOMAIN_SALES_SCRIPT,
        "expect_tokens": ["prix", "cher", "objection"],
        "expect_relevant": True,
    },
    {
        "query": "il hésite entre deux téléphones et n'arrive pas à choisir",
        "expect_domain": DOMAIN_SALES_SCRIPT,
        "expect_tokens": ["hesit", "choix", "closing"],
        "expect_relevant": True,
    },
    {
        "query": "comment vendre un accessoire avec un téléphone",
        "expect_domain": DOMAIN_SALES_SCRIPT,
        "expect_tokens": ["bundle", "accessoire", "upsell"],
        "expect_relevant": True,
    },

    # ── Stock pur ────────────────────────────────────────────────────────────
    {
        "query": "quelle quantité commander quand le besoin est sous le MOQ ?",
        "expect_domain": DOMAIN_INVENTORY_PLAYBOOK,
        "expect_tokens": ["moq", "minimale", "commande"],
        "expect_relevant": True,
    },
    {
        "query": "j'ai du stock dormant depuis des mois, je fais quoi ?",
        "expect_domain": DOMAIN_INVENTORY_PLAYBOOK,
        "expect_tokens": ["surstock", "dormant", "ecoulement"],
        "expect_relevant": True,
    },
    {
        "query": "le fournisseur livre avec des délais irréguliers",
        "expect_domain": DOMAIN_INVENTORY_PLAYBOOK,
        "expect_tokens": ["lead", "delai", "securite"],
        "expect_relevant": True,
    },

    # ── Catalogue : la question factuelle qui exige un vrai chiffre ───────────
    {
        "query": "combien coûte l'iPhone 16 Pro ?",
        "expect_domain": DOMAIN_PRODUCT,
        "expect_tokens": ["iphone 16 pro"],
        "forbid_tokens": ["iphone6", "iphone 6"],
        "expect_relevant": True,
    },
    {
        "query": "quel est le prix du Samsung Galaxy S25 Ultra ?",
        "expect_domain": DOMAIN_PRODUCT,
        "expect_tokens": ["galaxy s25"],
        "expect_relevant": True,
    },

    # ── Cross-domaine : le cœur du coach ─────────────────────────────────────
    {
        "query": "le Galaxy S25 est en rupture, qu'est-ce que je propose au client ?",
        "expect_domain": DOMAIN_INVENTORY_PLAYBOOK,
        "expect_tokens": ["substitut", "rupture", "alternatif"],
        "expect_relevant": True,
    },
    {
        "query": "quel produit je dois pousser en priorité maintenant ?",
        "expect_domain": DOMAIN_INVENTORY_PLAYBOOK,
        "expect_tokens": ["pousser", "marge", "disponib"],
        "forbid_tokens": ["paiement par anticipation"],
        "expect_relevant": True,
    },

    # ── Mémoire des décisions ────────────────────────────────────────────────
    {
        "query": "qu'est-ce qui a marché comme conseil récemment ?",
        "expect_domain": DOMAIN_DECISION,
        "expect_tokens": ["conseil", "efficace", "strategie"],
        "expect_relevant": True,
    },

    # ── Hors-sujet : le RAG DOIT se taire ────────────────────────────────────
    {
        "query": "quelle est la capitale de l'Australie ?",
        "expect_relevant": False,
    },
    {
        "query": "donne-moi la recette du couscous",
        "expect_relevant": False,
    },
    {
        "query": "comment configurer un serveur nginx en reverse proxy",
        "expect_relevant": False,
    },
]
