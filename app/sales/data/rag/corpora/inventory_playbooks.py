"""
rag/corpora/inventory_playbooks.py — Corpus métier stock & approvisionnement.

Ce corpus donne au coach le vocabulaire et les procédures du domaine inventory.
Sans lui, une question « le S25 est en rupture, je fais quoi ? » ne récupérait
que des scripts de vente — le coach improvisait la partie stock.

Chaque entrée = une situation reconnaissable + la procédure exacte + la règle
chiffrée qui la déclenche + l'effet attendu. Les règles reflètent la logique
réellement implémentée par le DecisionAgent (seuils, MOQ, lead time, Kanban).
"""

# doc_type | situation | action | regle | impact | produit | heure_min/max
PLAYBOOKS: list[dict] = [

    # ══════════════════════════════════════════════════════════════════
    # RUPTURE & PRÉ-RUPTURE
    # ══════════════════════════════════════════════════════════════════
    {
        "doc_type": "rupture_imminente",
        "categorie": "rupture",
        "situation": "Produit à forte rotation dont le stock couvre moins que le délai fournisseur",
        "action": "Déclencher un PO en urgence (statut SUGGERE sur le Kanban), et en attendant "
                  "basculer le discours de vente sur le substitut de même gamme.",
        "regle": "jours_de_couverture < lead_time_days → rupture certaine avant livraison",
        "impact": "Évite la rupture sèche ; conserve ~70% du CA via la substitution",
        "produit": "",
    },
    {
        "doc_type": "rupture_seche",
        "categorie": "rupture",
        "situation": "Produit à zéro stock, client déjà en boutique et demandeur",
        "action": "Ne jamais promettre une date sans consulter le lead time. Proposer le substitut "
                  "immédiat, sinon réservation avec acompte et rappel à réception.",
        "regle": "quantity_available == 0",
        "impact": "Transforme 40-60% des ruptures en vente reportée au lieu d'une vente perdue",
        "produit": "",
    },
    {
        "doc_type": "substitution",
        "categorie": "rupture",
        "situation": "Le modèle demandé est indisponible mais un équivalent existe en stock",
        "action": "Argumenter sur l'usage, pas sur la marque : mêmes specs clés (RAM, stockage, 5G), "
                  "disponibilité immédiate. Ne pas dénigrer le produit initial.",
        "regle": "substitut = même famille + même gamme + quantity_available > 5",
        "impact": "Taux de conversion de substitution observé : 55%",
        "produit": "",
    },
    {
        "doc_type": "rupture_accessoire",
        "categorie": "rupture",
        "situation": "Rupture sur un accessoire attaché à un terminal qui, lui, est en stock",
        "action": "Vendre le terminal seul sans bloquer la vente, et proposer l'accessoire en "
                  "commande client. Ne pas conditionner la vente du terminal à l'accessoire.",
        "regle": "accessoire en rupture ET terminal disponible",
        "impact": "Préserve la vente principale (marge terminal >> marge accessoire)",
        "produit": "",
    },

    # ══════════════════════════════════════════════════════════════════
    # RÉAPPROVISIONNEMENT & PURCHASE ORDERS
    # ══════════════════════════════════════════════════════════════════
    {
        "doc_type": "reappro_standard",
        "categorie": "reappro",
        "situation": "Le stock passe sous le point de commande",
        "action": "Créer un PO au statut SUGGERE. La quantité respecte le MOQ fournisseur et vise "
                  "à couvrir la demande prévue sur le lead time plus le stock de sécurité.",
        "regle": "point_de_commande = demande_moyenne × lead_time_days + stock_securite ; "
                 "qty_commandee = max(MOQ, besoin_calcule)",
        "impact": "Taux de service maintenu >95% sans surstock",
        "produit": "",
    },
    {
        "doc_type": "po_kanban",
        "categorie": "reappro",
        "situation": "Un PO suggéré par l'agent attend une décision humaine sur le Kanban",
        "action": "Le PO reste en SUGGERE tant qu'un humain ne l'a pas approuvé. Après approbation "
                  "il passe en COMMANDE, puis RECU à réception, ce qui recrédite le stock.",
        "regle": "SUGGERE → (approbation humaine) → COMMANDE → RECU ; aucune commande automatique",
        "impact": "Boucle stock fermée, traçabilité complète, humain toujours dans la boucle",
        "produit": "",
    },
    {
        "doc_type": "moq_contrainte",
        "categorie": "reappro",
        "situation": "Le besoin réel est inférieur à la quantité minimale de commande du fournisseur",
        "action": "Soit commander le MOQ et accepter le surstock temporaire si la rotation le justifie, "
                  "soit regrouper avec d'autres SKU du même fournisseur pour atteindre le seuil.",
        "regle": "besoin < MOQ → arbitrer coût de possession vs coût de rupture",
        "impact": "Réduit le coût de commande unitaire ; évite les micro-commandes",
        "produit": "",
    },
    {
        "doc_type": "lead_time_variable",
        "categorie": "reappro",
        "situation": "Fournisseur au délai de livraison irrégulier (lead_time_std élevé)",
        "action": "Augmenter le stock de sécurité proportionnellement à l'écart-type du délai, "
                  "pas au délai moyen. Un fournisseur lent mais régulier est moins risqué qu'un rapide erratique.",
        "regle": "stock_securite = z × sqrt(lead_time × var_demande + demande² × lead_time_std²)",
        "impact": "Absorbe la variabilité fournisseur sans surstock généralisé",
        "produit": "",
    },
    {
        "doc_type": "escalade_humaine",
        "categorie": "reappro",
        "situation": "Commande d'un montant élevé ou confiance de l'agent insuffisante",
        "action": "L'agent escalade au superviseur avec le motif, les trade-offs chiffrés "
                  "(coût de commande vs coût de possession vs risque de rupture) et sa recommandation.",
        "regle": "escalate_to_human = True si confidence < 0.7 ou order_cost élevé",
        "impact": "Décision humaine informée sur les engagements financiers importants",
        "produit": "",
    },

    # ══════════════════════════════════════════════════════════════════
    # SURSTOCK & ÉCOULEMENT
    # ══════════════════════════════════════════════════════════════════
    {
        "doc_type": "surstock",
        "categorie": "surstock",
        "situation": "Stock dormant : couverture très supérieure à la rotation observée",
        "action": "Prioriser ce produit dans le discours de vente, l'intégrer en bundle avec un "
                  "produit à forte rotation, et suspendre tout réappro jusqu'à normalisation.",
        "regle": "jours_de_couverture > 90 et rotation faible → écoulement prioritaire",
        "impact": "Libère de la trésorerie immobilisée et réduit le coût de possession",
        "produit": "",
    },
    {
        "doc_type": "fin_de_vie",
        "categorie": "surstock",
        "situation": "Produit en fin de cycle de vie (EOL) encore en stock",
        "action": "Écouler avant l'annonce du successeur. Remise progressive plutôt qu'une "
                  "démarque brutale qui casse la valeur perçue de la gamme.",
        "regle": "lifecycle_stage == 'end_of_life' ou date_eol proche",
        "impact": "Limite la dépréciation ; évite l'invendu total",
        "produit": "",
    },
    {
        "doc_type": "bundle_ecoulement",
        "categorie": "surstock",
        "situation": "Un produit en surstock et un produit en forte demande partagent une famille",
        "action": "Construire un bundle où le produit demandé porte l'offre et le surstock apporte "
                  "la remise perçue. La marge combinée doit rester positive.",
        "regle": "marge_bundle = marge_A + marge_B - remise > 0",
        "impact": "Écoule le dormant sans sacrifier la marge globale",
        "produit": "",
    },

    # ══════════════════════════════════════════════════════════════════
    # ALERTES & LECTURE DES SIGNAUX
    # ══════════════════════════════════════════════════════════════════
    {
        "doc_type": "alerte_critique",
        "categorie": "alerte",
        "situation": "Une alerte stock de sévérité haute remonte sur le tableau de bord",
        "action": "Vérifier d'abord si le stock physique correspond au stock système (écart d'inventaire), "
                  "puis seulement déclencher le réappro. Une alerte sur donnée fausse coûte une commande inutile.",
        "regle": "severity == 'high' → contrôle physique avant action",
        "impact": "Évite les commandes déclenchées par des écarts d'inventaire",
        "produit": "",
    },
    {
        "doc_type": "pic_demande",
        "categorie": "alerte",
        "situation": "Demande anormalement élevée détectée sur un SKU (anomalie statistique)",
        "action": "Distinguer un pic durable (lancement, promo, saisonnalité) d'un accident ponctuel. "
                  "Ne réapprovisionner sur un pic que si la cause est structurelle.",
        "regle": "z-score demande > 3 sur la fenêtre glissante",
        "impact": "Empêche le bullwhip effect déclenché par un pic isolé",
        "produit": "",
    },
    {
        "doc_type": "promo_stock",
        "categorie": "alerte",
        "situation": "Une promotion démarre sur un produit dont le stock est limite",
        "action": "Sécuriser le stock avant le lancement de la promo, ou décaler la promo. "
                  "Une promo en rupture dégrade l'image et gaspille le budget marketing.",
        "regle": "promo active et couverture < durée de la promo",
        "impact": "Protège le taux de service pendant les pics promotionnels",
        "produit": "",
    },

    # ══════════════════════════════════════════════════════════════════
    # CROSS-DOMAINE : là où vente et stock se parlent
    # ══════════════════════════════════════════════════════════════════
    {
        "doc_type": "quoi_pousser",
        "categorie": "cross_domaine",
        "situation": "Le conseiller demande quel produit pousser maintenant",
        "action": "Croiser trois signaux : stock disponible suffisant, marge élevée, et demande "
                  "portée par le créneau horaire. Ne jamais recommander un produit sous le seuil d'alerte.",
        "regle": "score = w1×marge + w2×disponibilité + w3×vélocité ; exclure si quantity_available < seuil",
        "impact": "Aligne l'effort commercial sur ce que la boutique peut réellement livrer",
        "produit": "",
    },
    {
        "doc_type": "gap_et_stock",
        "categorie": "cross_domaine",
        "situation": "Gap objectif important alors que les produits à forte marge sont en tension",
        "action": "Viser le volume sur les références disponibles plutôt que la marge sur des "
                  "références qu'on va mettre en rupture. Signaler la tension au superviseur.",
        "regle": "gap_pct > 30 et top_marge en pré-rupture → arbitrage volume",
        "impact": "Rattrape le CA sans créer de rupture sur les produits stratégiques",
        "produit": "",
    },
    {
        "doc_type": "vente_sur_reservation",
        "categorie": "cross_domaine",
        "situation": "Client décidé sur un produit indisponible, PO déjà en cours",
        "action": "Vendre sur réservation en annonçant la date de livraison issue du lead time réel "
                  "du fournisseur, jamais une date optimiste. Encaisser un acompte pour sécuriser.",
        "regle": "PO au statut COMMANDE → date = created_at + lead_time_days",
        "impact": "Sécurise le CA et fiabilise la promesse client",
        "produit": "",
    },
    {
        "doc_type": "meteo_stock",
        "categorie": "cross_domaine",
        "situation": "Météo défavorable, trafic boutique en baisse, stock élevé",
        "action": "Basculer l'effort sur les canaux non physiques (rappel des paniers abandonnés, "
                  "clients en attente de réappro) plutôt que d'attendre le passage en boutique.",
        "regle": "weather_effect <= -0.15 et couverture élevée",
        "impact": "Compense la baisse de trafic sans démarque",
        "produit": "",
    },
]
