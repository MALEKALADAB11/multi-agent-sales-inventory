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
    {
        "query": "le client veut payer en plusieurs fois, je fais quoi ?",
        "expect_domain": DOMAIN_SALES_SCRIPT,
        "expect_tokens": ["facilite", "paiement", "echeance"],
        "expect_relevant": True,
    },
    {
        "query": "comment conclure une vente quand le client est prêt ?",
        "expect_domain": DOMAIN_SALES_SCRIPT,
        "expect_tokens": ["closing", "signature", "contrat"],
        "expect_relevant": True,
    },
    {
        "query": "le client demande une réduction pour fidélité",
        "expect_domain": DOMAIN_SALES_SCRIPT,
        "expect_tokens": ["fidelite", "remise", "autorisation"],
        "expect_relevant": True,
    },
    {
        "query": "comment gérer un client mécontent ?",
        "expect_domain": DOMAIN_SALES_SCRIPT,
        "expect_tokens": ["meccontent", "reclamation", "satisfaction"],
        "expect_relevant": True,
    },
    {
        "query": "technique pour vendre un forfait data illimité",
        "expect_domain": DOMAIN_SALES_SCRIPT,
        "expect_tokens": ["data", "illimite", "forfait"],
        "expect_relevant": True,
    },
    {
        "query": "comment argumenter sur la qualité du réseau",
        "expect_domain": DOMAIN_SALES_SCRIPT,
        "expect_tokens": ["reseau", "qualite", "couverture"],
        "expect_relevant": True,
    },
    {
        "query": "le client compare avec un opérateur concurrent",
        "expect_domain": DOMAIN_SALES_SCRIPT,
        "expect_tokens": ["concurrent", "comparaison", "avantage"],
        "expect_relevant": True,
    },
    {
        "query": "vendre un téléphone à un senior",
        "expect_domain": DOMAIN_SALES_SCRIPT,
        "expect_tokens": ["senior", "simplicite", "accompagnement"],
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
    {
        "query": "comment calculer le stock de sécurité optimal",
        "expect_domain": DOMAIN_INVENTORY_PLAYBOOK,
        "expect_tokens": ["securite", "calcul", "variabilite"],
        "expect_relevant": True,
    },
    {
        "query": "quand déclencher une commande exceptionnelle",
        "expect_domain": DOMAIN_INVENTORY_PLAYBOOK,
        "expect_tokens": ["exceptionnel", "urgence", "procedure"],
        "expect_relevant": True,
    },
    {
        "query": "gérer les retours clients sur stock",
        "expect_domain": DOMAIN_INVENTORY_PLAYBOOK,
        "expect_tokens": ["retour", "client", "reintegration"],
        "expect_relevant": True,
    },
    {
        "query": "optimiser l espace de stockage en boutique",
        "expect_domain": DOMAIN_INVENTORY_PLAYBOOK,
        "expect_tokens": ["espace", "optimisation", "rayonnage"],
        "expect_relevant": True,
    },
    {
        "query": "stratégie pour les produits saisonniers",
        "expect_domain": DOMAIN_INVENTORY_PLAYBOOK,
        "expect_tokens": ["saisonnier", "saisonalite", "anticipation"],
        "expect_relevant": True,
    },
    {
        "query": "comment suivre la rotation des stocks",
        "expect_domain": DOMAIN_INVENTORY_PLAYBOOK,
        "expect_tokens": ["rotation", "periode", "analyse"],
        "expect_relevant": True,
    },
    {
        "query": "gestion des stocks multi-boutiques",
        "expect_domain": DOMAIN_INVENTORY_PLAYBOOK,
        "expect_tokens": ["multi", "transfert", "centralisation"],
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
    {
        "query": "spécifications techniques du Samsung S24",
        "expect_domain": DOMAIN_PRODUCT,
        "expect_tokens": ["samsung s24", "specifications", "technique"],
        "expect_relevant": True,
    },
    {
        "query": "quelle est la capacité de la batterie iPhone 15",
        "expect_domain": DOMAIN_PRODUCT,
        "expect_tokens": ["iphone 15", "batterie", "capacite"],
        "expect_relevant": True,
    },
    {
        "query": "les différents forfaits disponibles",
        "expect_domain": DOMAIN_PRODUCT,
        "expect_tokens": ["forfait", "offre", "tarif"],
        "expect_relevant": True,
    },
    {
        "query": "options de garantie disponibles",
        "expect_domain": DOMAIN_PRODUCT,
        "expect_tokens": ["garantie", "extension", "protection"],
        "expect_relevant": True,
    },
    {
        "query": "accessoires compatibles iPhone 16",
        "expect_domain": DOMAIN_PRODUCT,
        "expect_tokens": ["iphone 16", "accessoire", "compatible"],
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
    {
        "query": "produits avec forte marge à pousser",
        "expect_domain": DOMAIN_INVENTORY_PLAYBOOK,
        "expect_tokens": ["marge", "rentabilite", "priorite"],
        "expect_relevant": True,
    },
    {
        "query": "adapter les ventes selon le stock disponible",
        "expect_domain": DOMAIN_INVENTORY_PLAYBOOK,
        "expect_tokens": ["stock", "adaptation", "vente"],
        "expect_relevant": True,
    },
    {
        "query": "produits en fin de vie à écouler",
        "expect_domain": DOMAIN_INVENTORY_PLAYBOOK,
        "expect_tokens": ["fin de vie", "ecoulement", "promotion"],
        "expect_relevant": True,
    },
    {
        "query": "nouvelles arrivées à mettre en avant",
        "expect_domain": DOMAIN_INVENTORY_PLAYBOOK,
        "expect_tokens": ["nouveau", "lancement", "mise en avant"],
        "expect_relevant": True,
    },

    # ── Mémoire des décisions ────────────────────────────────────────────────
    {
        "query": "qu'est-ce qui a marché comme conseil récemment ?",
        "expect_domain": DOMAIN_DECISION,
        "expect_tokens": ["conseil", "efficace", "strategie"],
        "expect_relevant": True,
    },
    {
        "query": "quelles stratégies ont fonctionné récemment",
        "expect_domain": DOMAIN_DECISION,
        "expect_tokens": ["strategie", "succes", "resultat"],
        "expect_relevant": True,
    },
    {
        "query": "conseils efficaces pour augmenter panier moyen",
        "expect_domain": DOMAIN_DECISION,
        "expect_tokens": ["panier moyen", "augmentation", "conseil"],
        "expect_relevant": True,
    },
    {
        "query": "actions ayant amélioré satisfaction client",
        "expect_domain": DOMAIN_DECISION,
        "expect_tokens": ["satisfaction", "amelioration", "action"],
        "expect_relevant": True,
    },

    # ── Hors-sujet : le RAG DOIT se taire (vraiment hors contexte télécom/retail) ──
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
    {
        "query": "explique-moi le théorème de Pythagore",
        "expect_relevant": False,
    },
    {
        "query": "qui a gagné la coupe du monde 2018 ?",
        "expect_relevant": False,
    },
    {
        "query": "comment apprendre le python rapidement",
        "expect_relevant": False,
    },
    {
        "query": "quelle est la meilleure voiture électrique en 2024",
        "expect_relevant": False,
    },
    {
        "query": "recette de lasagnes bolognaises",
        "expect_relevant": False,
    },
    {
        "query": "histoire de la révolution française",
        "expect_relevant": False,
    },
    {
        "query": "comment investir en bourse",
        "expect_relevant": False,
    },
    {
        "query": "meilleur film de l'année 2023",
        "expect_relevant": False,
    },
    {
        "query": "tutorial docker pour débutants",
        "expect_relevant": False,
    },
    {
        "query": "comment dresser un chien",
        "expect_relevant": False,
    },
    {
        "query": "les bienfaits du yoga",
        "expect_relevant": False,
    },
    {
        "query": "comment rédiger un CV efficace",
        "expect_relevant": False,
    },
    {
        "query": "top 10 des destinations voyage",
        "expect_relevant": False,
    },
    {
        "query": "explication du quantum computing",
        "expect_relevant": False,
    },
    {
        "query": "comment apprendre l'anglais seul",
        "expect_relevant": False,
    },
    {
        "query": "meilleures recettes vegan",
        "expect_relevant": False,
    },
    {
        "query": "histoire de l'empire romain",
        "expect_relevant": False,
    },
    {
        "query": "comment créer un site web",
        "expect_relevant": False,
    },
    {
        "query": "les avantages du jeûne intermittent",
        "expect_relevant": False,
    },
    {
        "query": "guide voyage Japon",
        "expect_relevant": False,
    },
    {
        "query": "les différents types de café",
        "expect_relevant": False,
    },
    {
        "query": "comment choisir son assurance habitation",
        "expect_relevant": False,
    },
    {
        "query": "histoire de la musique jazz",
        "expect_relevant": False,
    },
    {
        "query": "meilleurs exercices pour abdos",
        "expect_relevant": False,
    },
    {
        "query": "comment économiser de l'argent",
        "expect_relevant": False,
    },
]
