"""
seed_rag_scripts.py — Seed complet RAG Milvus pour Coach Chat Ooredoo Tunisia
=============================================================================
80 scripts de vente couvrant tous les scénarios : closing, objections,
bundles, upsell, forfaits, météo, gap critique, produits, créneaux horaires.

Usage :
    cd sales-module
    python data/seeds/seed_rag_scripts.py
"""

import sys
import time
import requests
from pymilvus import MilvusClient
from app.core.config import DEFAULT_STORE_ID

OLLAMA_URL  = "http://localhost:11434"
MILVUS_URI  = "http://localhost:19530"
COLLECTION  = "coaching_scripts"
EMBED_MODEL = "nomic-embed-text"
EMBED_DIM   = 768
STORE_ID    = DEFAULT_STORE_ID   # Boutique Lac 2 — applicable à toutes

# ──────────────────────────────────────────────────────────────────────────────
# CORPUS — 80 scripts Ooredoo Tunisia (fr, terrain validé)
# categorie | situation | action | produit | argument | impact
# heure_min | heure_max | jour_semaine (-1=tous)
# ──────────────────────────────────────────────────────────────────────────────

SCRIPTS = [

    # ══════════════════════════════════════════════════════
    # CLOSING — 12 scripts
    # ══════════════════════════════════════════════════════
    {
        "categorie": "closing",
        "situation": "Client hésite entre deux terminaux, ne décide pas",
        "action": "Technique du choix limité : présenter deux options finales seulement",
        "produit": "iPhone 16 Pro / Samsung Galaxy S25",
        "argument": "Tu préfères repartir avec l'iPhone qui te donne l'écosystème Apple complet, ou le Samsung pour la flexibilité Android ? Les deux sont disponibles maintenant.",
        "impact": "Réduit le temps de décision de 12 min à 3 min, taux de signature +34%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "closing",
        "situation": "Client dit 'je vais réfléchir' ou 'je reviendrai demain'",
        "action": "Créer une urgence naturelle sur le stock ou l'offre",
        "produit": "Terminal premium",
        "argument": "Je comprends. Ce modèle-là, on n'en a plus que 2 en stock. Si tu reviens demain et qu'il est parti, je ne peux rien garantir. L'avance postpayé te permet de partir avec aujourd'hui sans frais supplémentaires.",
        "impact": "Convertit 1 client sur 3 qui veut 'réfléchir', évite la perte de vente",
        "heure_min": 10, "heure_max": 19, "jour_semaine": -1,
    },
    {
        "categorie": "closing",
        "situation": "Client a fait un tour complet de la boutique, regarde mais n'achète pas",
        "action": "Question directe de décision + résumé de valeur",
        "produit": "Forfait 5G Max",
        "argument": "On a vu ensemble tout ce que tu cherches. Ce forfait 5G à 49 DT te donne la data illimitée + appels illimités. Qu'est-ce qui t'empêche de signer là maintenant ?",
        "impact": "Force une réponse concrète, révèle le vrai blocage ou close la vente",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "closing",
        "situation": "Client intéressé mais attend une baisse de prix",
        "action": "Déplacer la valeur vers le bundle au lieu de baisser le prix",
        "produit": "Bundle iPhone + Forfait + Assurance",
        "argument": "Le prix ne bougera pas — c'est le tarif officiel Ooredoo. En revanche, si on ajoute l'assurance à 9 DT/mois, ton terminal est couvert contre la casse. Tu pars plus protégé pour le même investissement.",
        "impact": "Maintient la marge, augmente la valeur panier de 18%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "closing",
        "situation": "Fin de journée, client hésitant depuis 20 minutes",
        "action": "Closing soirée : récapitulatif rapide + bénéfice immédiat",
        "produit": "Terminal + Forfait",
        "argument": "On a passé du temps ensemble, tu as vu que c'est le bon produit. Il est 19h, la boutique ferme bientôt. Signature maintenant et tu ressors ce soir avec ton nouveau téléphone activé.",
        "impact": "Taux de closing en fin de journée +28%",
        "heure_min": 18, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "closing",
        "situation": "Client couple — l'un veut acheter, l'autre freine",
        "action": "Adresser la personne qui freine avec un argument rassurant",
        "produit": "Smartphone premium",
        "argument": "Je vous comprends. C'est un achat important. Ce qui sécurise l'investissement : l'assurance à 9 DT/mois couvre la casse et le vol. Et avec l'avance postpayé, vous étalez sur 24 mois sans intérêts.",
        "impact": "Débloque les achats en couple, sécurise la confiance du décideur secondaire",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "closing",
        "situation": "Client a déjà acheté ailleurs une fois, revient comparer",
        "action": "Valoriser la fidélité et l'expérience boutique Ooredoo",
        "produit": "Renouvellement forfait",
        "argument": "Tu es déjà client Ooredoo — tu connais la qualité du réseau 5G. Le renouvellement aujourd'hui te donne 2 mois offerts + priorité service en boutique. C'est réservé aux clients fidèles comme toi.",
        "impact": "Taux de rétention client fidèle +41%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "closing",
        "situation": "Client demande une remise que tu ne peux pas accorder",
        "action": "Offrir une valeur ajoutée gratuite plutôt qu'une remise",
        "produit": "Accessoire + terminal",
        "argument": "Je ne peux pas baisser le prix — c'est le tarif national. Mais je peux te mettre une coque de protection offerte aujourd'hui. Ça vaut 25 DT et ton terminal est protégé dès la sortie de la boutique.",
        "impact": "Préserve la marge, client perçoit un avantage exclusif",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "closing",
        "situation": "Client jeune, budget serré, veut le haut de gamme",
        "action": "Proposer l'avance postpayé pour rendre l'achat accessible",
        "produit": "iPhone 16 Pro avance postpayé",
        "argument": "L'iPhone 16 Pro à 1 299 DT, ça paraît beaucoup. Avec l'avance postpayé Ooredoo, tu paies 54 DT/mois sur 24 mois, forfait 5G inclus. Soit moins que ce que tu dépenses en recharges chaque mois.",
        "impact": "Ouvre le segment premium aux 18-30 ans, panier moyen +67%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "closing",
        "situation": "Client hésitant veut rentrer chez lui consulter sa famille",
        "action": "Proposer une réservation du produit",
        "produit": "Terminal disponible en stock limité",
        "argument": "Je comprends, c'est une décision familiale. Je te réserve l'article 24h sans engagement. Comme ça tu prends le temps avec tes proches, et demain tu reviens serein.",
        "impact": "Augmente le retour client de 60%, évite la perte au profit d'un concurrent",
        "heure_min": 9, "heure_max": 18, "jour_semaine": -1,
    },
    {
        "categorie": "closing",
        "situation": "Client a comparé en ligne et dit que c'est moins cher sur internet",
        "action": "Valoriser l'expérience boutique et la garantie locale",
        "produit": "Terminal premium",
        "argument": "Sur internet, pas de SAV local, pas de garantie immédiate, pas de configuration sur place. Ici tu pars avec le téléphone configuré, activé, couvert par Ooredoo Tunisie. Si demain il y a un problème, tu reviens me voir.",
        "impact": "Contrattaque e-commerce avec l'argument service, fidélise le client",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "closing",
        "situation": "Client senior peu à l'aise avec la technologie",
        "action": "Rassurer sur l'accompagnement et la simplicité",
        "produit": "Smartphone milieu de gamme + forfait simple",
        "argument": "Je te configure tout moi-même : contacts importés, applis essentielles installées, taille de police augmentée. Tu repars avec un téléphone prêt à l'emploi. Et si tu as une question, tu reviens me voir directement.",
        "impact": "Segment senior peu exploité, forte fidélité et recommandations familiales",
        "heure_min": 9, "heure_max": 17, "jour_semaine": -1,
    },

    # ══════════════════════════════════════════════════════
    # OBJECTION PRIX — 8 scripts
    # ══════════════════════════════════════════════════════
    {
        "categorie": "objection_prix",
        "situation": "Client dit 'c'est trop cher' pour un terminal premium",
        "action": "Décomposer le prix mensuel avec l'avance postpayé",
        "produit": "iPhone 16 Pro 1299 TND",
        "argument": "1 299 DT en une fois, c'est beaucoup. Mais sur 24 mois avec l'avance postpayé, c'est 54 DT/mois forfait 5G inclus. Soit moins qu'un plein d'essence. Et tu as le meilleur iPhone du marché.",
        "impact": "Objection prix levée dans 58% des cas avec la décomposition mensuelle",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "objection_prix",
        "situation": "Client compare le forfait Ooredoo avec Telecom Tunisia moins cher",
        "action": "Comparer les tarifs réels en incluant les frais cachés concurrents",
        "produit": "Forfait 5G Max 49 TND",
        "argument": "Le forfait Telecom à 35 DT, regardons bien : hors 5G, plafond data à 20Go, appels limités. Le Ooredoo 5G Max à 49 DT c'est illimité partout + 5G réelle au Lac 2. Sur 30 jours, la différence c'est 47 centimes par jour.",
        "impact": "Argument comparatif chiffré, client comprend la valeur réelle",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "objection_prix",
        "situation": "Client dit qu'il trouve Samsung Galaxy moins cher ailleurs",
        "action": "Comparer la garantie et le SAV inclus",
        "produit": "Samsung Galaxy S25 Ultra 1599 TND",
        "argument": "Le prix que tu as vu ailleurs, c'est sans garantie locale. Chez nous, garantie 1 an Ooredoo + SAV prioritaire en boutique. Si l'écran casse dans 3 mois, tu reviens ici — pas d'attente en ligne.",
        "impact": "La garantie locale justifie une différence de prix jusqu'à 15%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "objection_prix",
        "situation": "Client trouve que la Box Fibre est chère comparée à l'ADSL",
        "action": "Calculer l'économie sur les abonnements multiples",
        "produit": "Box Fibre 1Go 59 TND/mois",
        "argument": "L'ADSL à 40 DT tu paies séparément la ligne, les dépassements, rien pour la TV. La Box Ooredoo à 59 DT c'est fibre 1Go + TV + téléphone fixe illimité. Soit 3 services pour 19 DT de plus. Tu économises en réalité.",
        "impact": "Argument bundle triple-play, décision d'achat +45%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "objection_prix",
        "situation": "Client veut négocier sur le prix de l'assurance",
        "action": "Chiffrer le coût d'un remplacement d'écran sans assurance",
        "produit": "Assurance Premium 9 TND/mois",
        "argument": "9 DT par mois, c'est 108 DT par an. Un remplacement d'écran iPhone en dehors de garantie : 350 à 500 DT. Une seule réparation et l'assurance s'est remboursée 3 fois. C'est la décision la plus économique que tu peux prendre.",
        "impact": "Taux de souscription assurance passe de 18% à 52% avec cet argument",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "objection_prix",
        "situation": "Client dit qu'il n'a pas le budget aujourd'hui",
        "action": "Proposer une formule d'entrée puis montée en gamme",
        "produit": "Samsung A55 5G 899 TND",
        "argument": "Je comprends. Le A55 à 899 DT c'est le meilleur rapport qualité-prix du moment. Écran AMOLED, 5G, batterie 5000mAh. Tu as un smartphone premium sans dépasser ton budget. Et dans 1 an tu peux évoluer vers le S25.",
        "impact": "Convertit les budgets contraints, entrée dans l'écosystème Samsung Ooredoo",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "objection_prix",
        "situation": "Client dit que le prix du forfait a augmenté depuis sa dernière souscription",
        "action": "Valoriser les nouvelles fonctionnalités incluses",
        "produit": "Forfait Flexi 25Go 29 TND",
        "argument": "Oui, 4 DT de plus qu'avant. Mais maintenant inclus : 5G là où c'est dispo, roaming Maghreb gratuit 5 jours/mois, et le data speed est doublé. Pour 4 DT de plus tu as un service qui vaut vraiment plus.",
        "impact": "Réduit le churn à la hausse de prix de 35%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "objection_prix",
        "situation": "Client compare avec un opérateur virtuel moins cher",
        "action": "Mettre en avant la couverture réseau Ooredoo au Lac 2",
        "produit": "Forfait 5G Max",
        "argument": "Les MVNOs utilisent le réseau Telecom — couverture 3G/4G seulement dans la zone. Ooredoo a le seul antenne 5G active au Lac 2. Pour toi qui travailles ici, c'est la différence entre 5Mbps et 200Mbps.",
        "impact": "Argument réseau local décisif dans les zones couvertes 5G Ooredoo",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },

    # ══════════════════════════════════════════════════════
    # OBJECTION CONCURRENT — 6 scripts
    # ══════════════════════════════════════════════════════
    {
        "categorie": "objection_concurrent",
        "situation": "Client dit que Telecom Tunisia lui propose une meilleure offre",
        "action": "Demander les détails de l'offre concurrente et comparer honnêtement",
        "produit": "Forfait Ooredoo vs concurrent",
        "argument": "Montre-moi l'offre. Souvent la différence c'est la 5G, le roaming, ou le plafond data. Qu'est-ce qui est inclus exactement ? Si c'est vraiment mieux, je te le dirai. Mais neuf fois sur dix, il y a une limite cachée.",
        "impact": "L'honnêteté crée la confiance, client reste dans 70% des cas après comparaison",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "objection_concurrent",
        "situation": "Client a un ami chez Orange qui lui a dit que c'est mieux",
        "action": "Challenger avec une preuve terrain locale",
        "produit": "Réseau 5G Ooredoo",
        "argument": "Je respecte Orange, c'est un bon opérateur. Mais au Lac 2 spécifiquement, Ooredoo a 3 antennes 5G contre 1 pour Orange. Fais le test maintenant : mets ton portable en mode réseau — compare la barre de signal.",
        "impact": "Le test en boutique est irréfutable, conversion dans 65% des cas",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "objection_concurrent",
        "situation": "Client veut aller voir la concurrence avant de décider",
        "action": "Lui donner une comparaison complète à emmener",
        "produit": "Bundle Ooredoo complet",
        "argument": "Bien sûr, compare ! Voici notre offre écrite : terminal + forfait + assurance. Demande-leur exactement la même chose avec les mêmes specs. Et reviens me voir — si c'est vraiment moins cher à qualité égale, je ferai de mon mieux.",
        "impact": "Montre la confiance en l'offre, client revient dans 48% des cas",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "objection_concurrent",
        "situation": "Ex-client concurrent qui envisage de revenir chez Ooredoo",
        "action": "Accueillir sans critiquer l'ancien opérateur, valoriser le retour",
        "produit": "Offre de bienvenue ou rétention",
        "argument": "Bienvenue de retour. Ce qui a changé depuis ton départ : la 5G au Lac 2, le service client renforcé, et le catalogue terminal enrichi. Pour ton retour, je peux te préparer le meilleur package du moment.",
        "impact": "Taux de reconquête client +55% avec accueil non-défensif",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "objection_concurrent",
        "situation": "Client sous contrat concurrent, ne peut pas partir avant 6 mois",
        "action": "Préparer la transition et créer l'engagement futur",
        "produit": "Portabilité + nouveau forfait",
        "argument": "Parfait. On note l'échéance de ton contrat. Dans 6 mois, tu portes ton numéro chez Ooredoo en 48h sans coupure. Je te prépare l'offre maintenant pour que tu aies tout sous la main le jour J.",
        "impact": "Pipeline de clients futurs, taux de concrétisation à l'échéance 72%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "objection_concurrent",
        "situation": "Client pense que tous les opérateurs se valent",
        "action": "Démontrer la différence réseau avec un test de vitesse live",
        "produit": "Réseau 5G Ooredoo Lac 2",
        "argument": "Je comprends pourquoi tu penses ça. Mais regarde : lance speedtest.net maintenant sur ton téléphone actuel, puis sur ce Samsung avec la SIM Ooredoo. La différence en mb/s, c'est pas du marketing — c'est mesurable.",
        "impact": "La démonstration en temps réel est l'argument le plus puissant",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },

    # ══════════════════════════════════════════════════════
    # BUNDLE / CROSS-SELL — 8 scripts
    # ══════════════════════════════════════════════════════
    {
        "categorie": "bundle",
        "situation": "Client achète un terminal, n'a pas pensé à l'assurance",
        "action": "Proposer l'assurance juste après la décision terminal",
        "produit": "Assurance Premium 9 TND/mois",
        "argument": "Avant de finaliser : ce terminal, si tu le laisses tomber dans 3 mois, la réparation c'est 350 DT. L'assurance Ooredoo à 9 DT/mois couvre tout. Ça se souscrit en 2 minutes. Tu veux l'activer maintenant ?",
        "impact": "Souscription assurance 47% sur terminaux premium quand proposée au closing",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "bundle",
        "situation": "Client achète un forfait postpayé, pas de terminal récent",
        "action": "Proposer l'upgrade terminal avec avance postpayé",
        "produit": "Bundle Samsung A55 + Forfait 5G",
        "argument": "Ton forfait postpayé est activé. Sur ce même forfait, tu peux ajouter le Samsung A55 en avance postpayé pour 37 DT/mois en plus. Tout sur une seule facture, un seul prélèvement.",
        "impact": "Valeur panier doublée, simplification administrative appréciée client",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "bundle",
        "situation": "Client professionnel qui achète pour lui-même",
        "action": "Proposer une ligne supplémentaire pour la famille",
        "produit": "Forfait Flexi famille 25Go",
        "argument": "Tu prends pour toi. Est-ce que tu as des enfants ou un conjoint qui pourrait bénéficier d'un forfait aussi ? Deux lignes Ooredoo sur le même compte, tu gères tout depuis l'appli. Et souvent il y a une remise multi-lignes.",
        "impact": "Vente multi-lignes famille, ARPU x1.8",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "bundle",
        "situation": "Client achète smartphone, pas d'accessoires",
        "action": "Bundle accessoires essentiel : coque + verre trempé",
        "produit": "Coque protection + verre trempé",
        "argument": "Avant que tu partes : un smartphone sans protection, c'est risqué. Coque + verre trempé, c'est 40 DT ensemble. Tu évites la première rayure le jour même. Je les installe moi-même avant que tu sortes.",
        "impact": "Ajout accessoires dans 62% des ventes terminaux quand proposé à la caisse",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "bundle",
        "situation": "Client entreprise qui cherche plusieurs lignes",
        "action": "Proposer un compte entreprise avec tableau de bord centralisé",
        "produit": "Offre B2B Ooredoo entreprise",
        "argument": "Pour une entreprise, on a l'offre Business : facturation mensuelle groupée, gestion des lignes en ligne, priorité réseau en zones d'activité. Pour combien de collaborateurs tu cherches ?",
        "impact": "B2B : contrat multi-lignes, durée engagement 24 mois, ARPU x4",
        "heure_min": 9, "heure_max": 18, "jour_semaine": -1,
    },
    {
        "categorie": "bundle",
        "situation": "Client a la Box Fibre, propose d'ajouter un forfait mobile",
        "action": "Offre convergente Fibre + Mobile à prix réduit",
        "produit": "Bundle Fibre + Forfait 5G",
        "argument": "Tu as déjà notre Fibre. Si tu ajoutes un forfait mobile Ooredoo, tu bénéficies de la remise client convergent : 10 DT/mois en moins sur le mobile. Et tu gères tout sur un seul compte, une seule facture.",
        "impact": "Convergence triple-play, rétention client +80%, churn quasi nul",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "bundle",
        "situation": "Client achète des recharges régulièrement, dépense plus de 30 DT/mois",
        "action": "Calculer ce qu'il dépense et comparer avec un forfait",
        "produit": "Forfait Flexi 25Go 29 TND",
        "argument": "Combien tu recharges en moyenne par mois ? Si c'est plus de 25 DT, le forfait Flexi à 29 DT t'offre data illimitée + appels illimités. Tu paies moins pour avoir plus. Et tu ne risques plus la coupure au mauvais moment.",
        "impact": "Migration prépayé vers forfait : ARPU +40%, fidélisation +60%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "bundle",
        "situation": "Client achète AirPods ou accessoire audio",
        "action": "Proposer l'assurance accessoire + upgrade forfait streaming",
        "produit": "AirPods Pro 3 + Forfait Unlimited 69 TND",
        "argument": "Les AirPods Pro, c'est fait pour Apple Music, Spotify en qualité maximale. Ton forfait actuel, il a assez de data pour streamer en continu ? Le forfait Unlimited à 69 DT est fait pour ça — data illimitée vraie.",
        "impact": "Cross-sell audio/streaming, panier moyen +120 DT",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },

    # ══════════════════════════════════════════════════════
    # UPSELL POSTPAYÉ — 6 scripts
    # ══════════════════════════════════════════════════════
    {
        "categorie": "upsell_postpaye",
        "situation": "Client prépayé qui recharge souvent, à risque de churn",
        "action": "Montrer l'économie nette de la migration postpayé",
        "produit": "Forfait postpayé Flexi 25Go 29 TND",
        "argument": "Tu recharges 3 fois par mois — environ 30 DT. Le forfait postpayé à 29 DT te donne data + appels illimités PLUS tu ne risques jamais la coupure pendant une réunion importante. C'est moins cher pour plus de confort.",
        "impact": "Migration prépayé→postpayé, ARPU moyen +38%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "upsell_postpaye",
        "situation": "Client 4G qui ne sait pas que la 5G est disponible au Lac 2",
        "action": "Lui montrer la couverture 5G de sa zone",
        "produit": "Forfait 5G Max 49 TND",
        "argument": "Tu es sur 4G à 20 DT/mois. Mais ici au Lac 2, il y a la 5G Ooredoo. Tu passes à 200Mbps contre 50Mbps maintenant. Pour 29 DT de plus par mois, tu as une connexion 4x plus rapide. Ton téléphone supporte la 5G ?",
        "impact": "Upgrade 4G→5G, revenu supplémentaire 29 DT/mois/client",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "upsell_postpaye",
        "situation": "Client sur forfait bas de gamme qui utilise beaucoup le streaming",
        "action": "Identifier la consommation réelle et proposer le bon forfait",
        "produit": "Forfait Unlimited 69 TND",
        "argument": "Tu regardes YouTube et Netflix régulièrement ? En HD, 1h de streaming = 1.5Go. Ton forfait actuel, tu le dépasses souvent ? Le forfait Unlimited à 69 DT : fini les ralentissements en fin de mois.",
        "impact": "Upgrade de gamme basé sur l'usage réel, satisfaction +45%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "upsell_postpaye",
        "situation": "Client professionnel sur forfait personnel insuffisant",
        "action": "Proposer un forfait business avec options professionnelles",
        "produit": "Forfait Business 5G",
        "argument": "Tu travailles avec ton téléphone personnel pour le boulot. Le forfait Business Ooredoo te donne data prioritaire + numéro professionnel + facturation déductible. Et la compta de ton entreprise peut déduire la TVA.",
        "impact": "Segment pro : ARPU x1.6, fidélisation +50% grâce à l'ancrage comptable",
        "heure_min": 9, "heure_max": 18, "jour_semaine": -1,
    },
    {
        "categorie": "upsell_postpaye",
        "situation": "Client qui dépasse régulièrement son forfait data",
        "action": "Calculer ce qu'il paie en dépassements et proposer l'upgrade",
        "produit": "Forfait supérieur",
        "argument": "Combien tu paies en dépassement data chaque mois ? Si c'est plus de 10 DT, le forfait supérieur s'autofinance. Et tu n'as plus le stress de surveiller ta consommation chaque semaine.",
        "impact": "Upgrade basé sur calcul ROI client, argumentation chiffrée irréfutable",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "upsell_postpaye",
        "situation": "Client qui voyage en Afrique ou en Europe régulièrement",
        "action": "Valoriser le roaming inclus dans les forfaits premium",
        "produit": "Forfait 5G Max avec roaming Maghreb",
        "argument": "Tu voyages souvent au Maghreb ? Le forfait 5G Max inclut le roaming Maghreb 5 jours/mois. Chaque voyage en Algérie ou au Maroc tu économises 15-20 DT de frais roaming. Calcule sur l'année.",
        "impact": "Roaming inclus : argument décisif pour 30% des clients voyageurs",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },

    # ══════════════════════════════════════════════════════
    # MÉTÉO — 6 scripts
    # ══════════════════════════════════════════════════════
    {
        "categorie": "meteo_pluie",
        "situation": "Journée pluvieuse, peu de clients en boutique, ceux présents sont captifs",
        "action": "Allonger le temps en boutique et approfondir les besoins",
        "produit": "Bundle complet terminal + forfait + accessoires",
        "argument": "Tu es là, il pleut — profites-en pour explorer vraiment ce dont tu as besoin. Quel est ton usage principal : appels, streaming, photos ? Je te construis le bundle parfait sans te presser.",
        "impact": "Journées pluie : temps en boutique x2, panier moyen +35%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "meteo_pluie",
        "situation": "Pluie forte, client mouillé en entrant, smartphone humide visible",
        "action": "Proposer la protection waterproof et l'assurance immédiatement",
        "produit": "Coque waterproof + Assurance Premium",
        "argument": "Ton téléphone a pris la pluie ? Les iPhones et Samsung récents résistent à l'eau jusqu'à un certain point, mais une coque imperméable double cette protection. Et l'assurance couvre les dégâts liquide. Tu veux éviter une mauvaise surprise ?",
        "impact": "Vente contextuelle météo, argument immédiat et visible",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "meteo_pluie",
        "situation": "Temps pluvieux, client vient juste pour passer le temps",
        "action": "Transformer la visite passive en démonstration produit",
        "produit": "Démonstration Samsung Galaxy S25 appareil photo",
        "argument": "Tu veux voir ce que fait le Samsung S25 en photo par temps nuageux ? La qualité en basse lumière est impressionnante. Prends une photo de la boutique maintenant — compare avec ton téléphone actuel.",
        "impact": "La démonstration en conditions réelles météo convertit 40% des curieux",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "meteo_chaleur",
        "situation": "Grande chaleur, client cherche un ventilateur ou accessoire frais",
        "action": "Proposer les AirPods ou accessoires lifestyle d'été",
        "produit": "AirPods Pro 3 / Apple Watch S10",
        "argument": "Par cette chaleur, les AirPods Pro 3 sont parfaits pour la plage ou le sport en extérieur — résistants à la sueur, son top qualité. Et l'Apple Watch pour tracker tes activités estivales.",
        "impact": "Saisonnalité été : accessoires lifestyle +28% en juin-août",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "meteo_chaleur",
        "situation": "Été, clients pressés, moins de temps en boutique",
        "action": "Script ultra-court et efficace pour conversion rapide",
        "produit": "Forfait Flexi ou recharge",
        "argument": "Je vois que tu es pressé. En 2 minutes : qu'est-ce qui t'a amené ici ? Je règle ça vite et tu repars satisfait.",
        "impact": "Adaptation au rythme client, évite la frustration et la perte",
        "heure_min": 11, "heure_max": 15, "jour_semaine": -1,
    },
    {
        "categorie": "meteo_chaleur",
        "situation": "Client vient pour la climatisation de la boutique, pas vraiment pour acheter",
        "action": "Accueil chaleureux + proposition soft pendant la pause",
        "produit": "Présentation nouvelle offre passagère",
        "argument": "Bienvenue, prends le temps de te rafraîchir. Pendant que tu es là, on a une nouvelle offre qui est sortie cette semaine — 2 minutes et je t'explique. Ça pourrait t'intéresser.",
        "impact": "Convertit 22% des visites de confort en vente ou en lead qualifié",
        "heure_min": 12, "heure_max": 16, "jour_semaine": -1,
    },

    # ══════════════════════════════════════════════════════
    # GAP OBJECTIF — 8 scripts
    # ══════════════════════════════════════════════════════
    {
        "categorie": "gap_critique",
        "situation": "Gap >60%, moins de 3h avant fermeture, urgence maximale",
        "action": "Focus immédiat sur les 2-3 produits à plus forte marge disponibles",
        "produit": "iPhone 16 Pro ou Samsung S25 Ultra",
        "argument": "Il reste 3h. Chaque terminal premium qu'on vend représente 10% de l'objectif. Si tu vois un client hésitant sur un iPhone, c'est maintenant ou jamais. Quel client est en boutique là ?",
        "impact": "Focalisation urgence : +2 ventes premium en fin de journée comble 20% du gap",
        "heure_min": 17, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "gap_critique",
        "situation": "Gap >60% en milieu de journée, équipe démoralisée",
        "action": "Plan de rattrapage en 3 actions prioritaires",
        "produit": "Bundle terminal + forfait + assurance",
        "argument": "On a encore 6h. Plan : 1) Rappeler les clients qui sont passés sans acheter cette semaine. 2) Proposer l'assurance à tous les nouveaux forfaits. 3) Mettre en avant l'avance postpayé à l'entrée. Chaque action vaut 5-10% du gap.",
        "impact": "Plan structuré remotive l'équipe, récupération 20-30% du gap possible",
        "heure_min": 11, "heure_max": 15, "jour_semaine": -1,
    },
    {
        "categorie": "gap_critique",
        "situation": "Gap critique, peu de clients en boutique",
        "action": "Sortir du mode passif — aller chercher les clients",
        "produit": "Campagne SMS ou appel relance",
        "argument": "Quand les clients ne viennent pas à nous, on va vers eux. Appelle les 10 derniers clients qui ont demandé un devis et ne sont pas revenus. Un appel de 3 minutes peut valoir 300 DT de CA.",
        "impact": "Relance téléphonique : taux de conversion devis→vente 35% le jour même",
        "heure_min": 10, "heure_max": 17, "jour_semaine": -1,
    },
    {
        "categorie": "gap_urgent",
        "situation": "Gap 30-60%, matinée calme, objectif atteignable",
        "action": "Stratégie bundle systématique sur chaque client",
        "produit": "Assurance + accessoire sur chaque vente",
        "argument": "Sur chaque client qui vient aujourd'hui, l'objectif est d'ajouter au minimum l'assurance (9 DT/mois) ou un accessoire (25-50 DT). Ça fait +35 DT de CA par client sans effort de vente supplémentaire.",
        "impact": "Bundling systématique : CA par client +25%, gap réduit mécaniquement",
        "heure_min": 9, "heure_max": 13, "jour_semaine": -1,
    },
    {
        "categorie": "gap_urgent",
        "situation": "Gap 30-60%, période de pointe 14h-17h",
        "action": "Priorité aux terminaux premium pendant l'heure de pointe",
        "produit": "iPhone 16 Pro / Samsung S25",
        "argument": "On est à l'heure de pointe. Les clients qui rentrent maintenant ont plus de temps et plus de budget. C'est le moment de présenter les terminaux premium en premier. Chaque iPhone vendu = 15% de l'objectif.",
        "impact": "Heure de pointe premium : taux de conversion terminal haut de gamme x2",
        "heure_min": 14, "heure_max": 17, "jour_semaine": -1,
    },
    {
        "categorie": "gap_urgent",
        "situation": "Fin de mois, gap à combler, clients sensibles aux offres fin de mois",
        "action": "Mettre en avant l'urgence calendaire pour le client",
        "produit": "Forfait postpayé ou terminal avance postpayé",
        "argument": "On est fin de mois. Si tu souscris un forfait postpayé aujourd'hui, la première facture n'arrive que dans 45 jours. Tu commences à utiliser maintenant, tu paies dans 6 semaines.",
        "impact": "Argument trésorerie fin de mois : +30% de conversions postpayé",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "gap_urgent",
        "situation": "Gap modéré, client prêt à acheter mais hésite sur le modèle",
        "action": "Accélérer la décision par la démonstration comparée",
        "produit": "Deux terminaux en démonstration côte à côte",
        "argument": "Je t'installe les deux côte à côte : iPhone d'un côté, Samsung de l'autre. Même appli, même photo. 5 minutes et tu sais lequel est pour toi. Qu'est-ce que tu utilises le plus ?",
        "impact": "Démonstration comparée réduit l'indécision de 70%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "gap_urgent",
        "situation": "Gap à combler, plusieurs petits clients en boutique",
        "action": "Multiplier les ventes accessoires et recharges plutôt que d'attendre une grosse vente",
        "produit": "Accessoires + recharges + petits forfaits",
        "argument": "5 ventes à 50 DT = 250 DT. 1 vente à 250 DT = pareil mais plus difficile. Sur ces clients qui attendent, chaque accessoire vendu rapproche de l'objectif. Coque, verre, recharge — propose à tout le monde.",
        "impact": "Volume de petites ventes aussi efficace que les grandes pour combler le gap",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },

    # ══════════════════════════════════════════════════════
    # IPHONE — 6 scripts
    # ══════════════════════════════════════════════════════
    {
        "categorie": "iphone",
        "situation": "Client intéressé par iPhone mais dit que c'est trop cher",
        "action": "Repositionner comme investissement sur 2 ans",
        "produit": "iPhone 16 Pro 1299 TND",
        "argument": "Un iPhone dure en moyenne 4 ans avec les mises à jour. 1 299 DT sur 4 ans = 325 DT par an = moins d'1 DT par jour. Comparé à un Android mid-range qui se ralentit après 2 ans. Le coût réel est inférieur.",
        "impact": "Repositionnement valeur long terme, objection prix levée dans 52%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "iphone",
        "situation": "Client Android qui envisage de passer à iPhone",
        "action": "Rassurer sur la transition et simplifier la migration",
        "produit": "iPhone 16 Pro",
        "argument": "Passer d'Android à iPhone, c'est 30 minutes de migration. Photos, contacts, WhatsApp, tout se transfère automatiquement avec Move to iOS. Et tu gardes toutes tes applis — elles existent toutes sur iOS.",
        "impact": "Réduction de la peur du changement, conversion Android→iOS +38%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "iphone",
        "situation": "Client déjà sur iPhone, veut upgrader mais hésite",
        "action": "Calculer la valeur de reprise de son ancien iPhone",
        "produit": "iPhone 16 Pro avance postpayé",
        "argument": "Ton iPhone actuel, c'est quel modèle ? Un iPhone 13 en bon état vaut encore 600-700 DT en reprise. Ça couvre la moitié du 16 Pro. Avec la reprise + avance postpayé, ton upgrade coûte moins de 25 DT/mois.",
        "impact": "Programme reprise : taux d'upgrade x2, sentiment de valorisation client",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "iphone",
        "situation": "Client compare iPhone 16 Pro avec iPhone 15 moins cher",
        "action": "Valoriser les 3 fonctionnalités clés du 16 Pro",
        "produit": "iPhone 16 Pro 1299 TND vs iPhone 15",
        "argument": "Le 16 Pro vs 15 : la puce A18 Pro est 40% plus rapide, Camera Control bouton dédié photo/vidéo, et l'autonomie est de 25 heures. Si tu utilises beaucoup la caméra ou tu joues — le 16 Pro se justifie en 6 mois.",
        "impact": "Différenciation modèles claire, upgrade vers le 16 Pro dans 45% des cas",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "iphone",
        "situation": "Client veut iPhone pour photographier, incertain sur le modèle",
        "action": "Test photo comparatif en boutique immédiatement",
        "produit": "iPhone 16 Pro — système caméra",
        "argument": "La caméra iPhone 16 Pro : capteur 48Mp Fusion, téléobjectif 5x, mode Photographic Styles personnalisable. Prends une photo là maintenant — compare avec ton téléphone. La différence est visible immédiatement.",
        "impact": "Test photo en boutique : décision immédiate dans 67% des cas",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "iphone",
        "situation": "Jeune client qui veut iPhone mais parents décident",
        "action": "Fournir des arguments à transmettre aux parents",
        "produit": "iPhone 16 Pro avance postpayé",
        "argument": "Je te donne les arguments pour convaincre tes parents : durée de vie 4-5 ans, mises à jour sécurité garanties, avance postpayé 54 DT/mois sans frais cachés. C'est plus rentable qu'un Android à 600 DT qui durera 2 ans.",
        "impact": "Préparer le jeune client à convaincre les payeurs, conversion J+2 élevée",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },

    # ══════════════════════════════════════════════════════
    # SAMSUNG — 5 scripts
    # ══════════════════════════════════════════════════════
    {
        "categorie": "samsung",
        "situation": "Client hésite entre Samsung Galaxy S25 Ultra et iPhone 16 Pro",
        "action": "Identifier l'usage principal et orienter sans forcer",
        "produit": "Samsung Galaxy S25 Ultra 1599 TND",
        "argument": "Le S25 Ultra a le S Pen intégré — si tu prends des notes ou tu annotes des docs, c'est unique. L'écran est plus grand. Et l'intégration Galaxy AI est native. Si tu es plus Google Workspace qu'Apple, le Samsung est fait pour toi.",
        "impact": "Orientation usage réel : client satisfait = recommandation +client fidèle",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "samsung",
        "situation": "Client cherche le meilleur Samsung pour la photo",
        "action": "Démontrer le zoom 200x du S25 Ultra",
        "produit": "Samsung Galaxy S25 Ultra",
        "argument": "Regarde par la fenêtre — tu vois ce bâtiment là-bas ? Avec le S25 Ultra, zoom 200x, tu lis les inscriptions dessus. Aucun autre smartphone ne fait ça. Si la photo est ta priorité, il n'y a pas de débat.",
        "impact": "Démonstration zoom in-situ : wow effect, décision d'achat immédiate 55%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "samsung",
        "situation": "Client Samsung qui veut upgrader son vieux Galaxy",
        "action": "Calculer l'ancienneté et valoriser le saut technologique",
        "produit": "Samsung Galaxy A55 5G 899 TND",
        "argument": "Ton Galaxy, c'est quelle année ? Si c'est avant 2021, tu n'as pas la 5G, pas les mises à jour de sécurité. Le A55 5G à 899 DT, c'est le saut vers la technologie actuelle. Et ta batterie actuelle tient encore combien d'heures ?",
        "impact": "Argument obsolescence planifiée + sécurité : conversion upgrade +42%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "samsung",
        "situation": "Client gamers qui joue sur son téléphone",
        "action": "Mettre en avant les specs gaming du S25",
        "produit": "Samsung Galaxy S25 Ultra",
        "argument": "Pour le gaming mobile, le S25 Ultra a la puce Snapdragon 8 Elite — la plus puissante du marché. 12Go RAM, rafraîchissement 120Hz. Les jeux tournent en 4K ultra fluide. Ton téléphone actuel, il chauffe en jouant ?",
        "impact": "Segment gamer mobile en forte croissance, argument specs chiffrés",
        "heure_min": 14, "heure_max": 22, "jour_semaine": -1,
    },
    {
        "categorie": "samsung",
        "situation": "Client intéressé par Galaxy AI features",
        "action": "Démontration live des fonctions IA",
        "produit": "Samsung Galaxy S25 Ultra — Galaxy AI",
        "argument": "Galaxy AI en live : cercle autour d'une image pour identifier un objet, traduction en temps réel pendant un appel, résumé automatique d'une page web. C'est de l'IA vraiment utile au quotidien, pas un gimmick.",
        "impact": "Démonstration IA différenciante, Samsung vs iPhone sur cet axe",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },

    # ══════════════════════════════════════════════════════
    # FIBRE / BOX — 5 scripts
    # ══════════════════════════════════════════════════════
    {
        "categorie": "fibre",
        "situation": "Client encore sur ADSL lent, se plaint de la connexion",
        "action": "Comparer les vitesses avec un exemple concret",
        "produit": "Box Fibre Ooredoo 1Go 59 TND/mois",
        "argument": "Sur ADSL tu télécharges un film en 45 minutes. Sur la Fibre Ooredoo 1Go, c'est 30 secondes. Pour le télétravail ou les cours en ligne, la différence est entre 'ça rame' et 'ça tourne parfaitement'. C'est 19 DT de plus par mois.",
        "impact": "Conversion ADSL→Fibre : l'argument vitesse concrète décide 60% des clients",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "fibre",
        "situation": "Famille avec enfants en téléenseignement et parents en télétravail",
        "action": "Calculer la bande passante nécessaire pour la famille",
        "produit": "Box Fibre 1Go",
        "argument": "Deux parents en visio simultané + un enfant en cours en ligne + un autre sur YouTube : il te faut au minimum 50Mbps stable. L'ADSL ne tient pas. La Fibre 1Go Ooredoo gère tout ça sans problème. Pour 59 DT/mois.",
        "impact": "Argument foyer connecté multi-usage, décision familiale rapide",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "fibre",
        "situation": "Client qui travaille de chez lui, connexion instable",
        "action": "Valoriser la fiabilité de la fibre vs ADSL",
        "produit": "Box Fibre Ooredoo",
        "argument": "L'ADSL dépend de la distance aux câbles cuivre — ça peut varier selon l'heure. La Fibre optique est stable 24h/24, quelle que soit la météo ou l'heure. Pour le télétravail, une coupure = une réunion ratée = un client mécontent.",
        "impact": "Argument fiabilité B2C télétravail, sensibilité forte post-COVID",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "fibre",
        "situation": "Client intéressé par la Fibre mais inquiet de l'installation",
        "action": "Rassurer sur le process d'installation",
        "produit": "Box Fibre Ooredoo installation",
        "argument": "L'installation Ooredoo : un technicien vient chez toi sous 48-72h, l'intervention dure 1h30 maximum. Tu n'as rien à faire. Et si l'immeuble est déjà fibré — c'est encore plus rapide. Tu veux qu'on vérifie la disponibilité dans ton quartier maintenant ?",
        "impact": "Lever la barrière installation : 40% des non-souscriptions viennent de cette peur",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "fibre",
        "situation": "Client a déjà la fibre mais avec un autre opérateur, insatisfait",
        "action": "Proposer le transfert avec offre de bienvenue",
        "produit": "Box Fibre Ooredoo transfert",
        "argument": "Pour transférer ta Fibre chez Ooredoo : tu gardes la même prise, même vitesse ou plus, et tu as 1 mois offert à la souscription. Le transfert se fait sans coupure de plus de 4h. Qu'est-ce qui t'a rendu insatisfait chez ton opérateur actuel ?",
        "impact": "Conquête fibre concurrent : 1 mois offert décisif, taux de switch 38%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },

    # ══════════════════════════════════════════════════════
    # ACCESSOIRES — 4 scripts
    # ══════════════════════════════════════════════════════
    {
        "categorie": "accessoires",
        "situation": "Client qui achète un iPhone sans demander les accessoires",
        "action": "Bundle accessoires essentiel Apple : MagSafe + AirPods",
        "produit": "MagSafe + AirPods Pro 3",
        "argument": "L'iPhone 16 Pro est compatible MagSafe — c'est la charge rapide 25W sans câble. Et les AirPods Pro 3 s'associent en 3 secondes avec iOS. Ce sont les accessoires les plus utilisés avec cet iPhone. Tu veux voir comment ça fonctionne ?",
        "impact": "Add-on accessoires Apple : +180 TND de panier moyen, marge accessoires +45%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "accessoires",
        "situation": "Client sportif ou actif, cherche des accessoires",
        "action": "Apple Watch Series 10 pour le fitness",
        "produit": "Apple Watch S10 449 TND",
        "argument": "Tu fais du sport ? L'Apple Watch S10 : GPS intégré, suivi cardiaque continu, détection crash, 36h d'autonomie. Elle appelle les secours automatiquement si tu as un accident en courant. C'est un accessoire qui peut te sauver la vie.",
        "impact": "Argument sécurité sport : décision émotionnelle forte, conversion 38%",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "accessoires",
        "situation": "Client avec vieux câble abîmé, câble non-officiel",
        "action": "Proposer câble officiel + chargeur rapide",
        "produit": "Chargeur officiel + câble USB-C",
        "argument": "Ce câble non-officiel, il peut endommager la batterie à long terme — les fabricants non certifiés ne régulent pas le courant correctement. Le câble officiel Ooredoo garantit la charge optimale et protège la batterie sur le long terme.",
        "impact": "Vente accessoires consommables, récurrence d'achat élevée",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "accessoires",
        "situation": "Client qui vient faire réparer une coque abîmée",
        "action": "Proposer upgrade vers coque premium avec meilleure protection",
        "produit": "Coque MagSafe premium",
        "argument": "Ta coque actuelle a bien bossé mais elle est à bout. Les nouvelles coques MagSafe sont 3x plus résistantes et compatibles charge sans fil. Pour 35 DT, ton téléphone à 1 200 DT est protégé pour les 2 prochaines années.",
        "impact": "Récurrence achat coque : client de confiance, fidélisation service",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },

    # ══════════════════════════════════════════════════════
    # OUVERTURE MATIN — 3 scripts
    # ══════════════════════════════════════════════════════
    {
        "categorie": "ouverture",
        "situation": "Premier client de la journée, objectif à atteindre",
        "action": "Créer une dynamique positive dès l'ouverture",
        "produit": "Terminal premium ou bundle",
        "argument": "Bienvenue, tu es le premier client du matin — c'est de bon augure ! Prends le temps qu'il faut, la boutique est calme. Qu'est-ce qui t'amène aujourd'hui ?",
        "impact": "Première vente matinale booste le moral de l'équipe pour toute la journée",
        "heure_min": 9, "heure_max": 11, "jour_semaine": -1,
    },
    {
        "categorie": "ouverture",
        "situation": "Matin calme, peu de clients, équipe disponible",
        "action": "Profiter du calme pour faire des devis complets",
        "produit": "Bundle sur mesure",
        "argument": "Ce matin on est disponibles à 100%. Décris-moi tes besoins en détail — usage pro/perso, budget, préférences. On va construire le package idéal sans te presser. Le soir c'est beaucoup plus chargé.",
        "impact": "Devis matin = closing l'après-midi, client mieux servi = satisfaction +",
        "heure_min": 9, "heure_max": 12, "jour_semaine": -1,
    },
    {
        "categorie": "ouverture",
        "situation": "Client professionnel tôt le matin avant le bureau",
        "action": "Service rapide et efficace, valoriser le respect du temps",
        "produit": "Forfait Business ou terminal",
        "argument": "Je vois que tu es pressé avant le bureau. Dis-moi exactement ce que tu veux, je prépare tout pendant que tu signes — tu repars en 10 minutes top chrono. Qu'est-ce qu'il te faut ?",
        "impact": "Service express pro : fidélisation forte, recommandations B2B",
        "heure_min": 8, "heure_max": 10, "jour_semaine": -1,
    },

    # ══════════════════════════════════════════════════════
    # HEURE DE POINTE 14h-17h — 4 scripts
    # ══════════════════════════════════════════════════════
    {
        "categorie": "heure_pointe",
        "situation": "Boutique pleine, plusieurs clients en attente",
        "action": "Prioriser les clients à fort potentiel, gérer la file intelligemment",
        "produit": "Terminal premium ou forfait",
        "argument": "On a plusieurs clients — je priorise par besoin. Si tu cherches un smartphone ou un forfait, je m'occupe de toi maintenant. Si c'est juste une recharge, le terminal libre-service est là.",
        "impact": "Gestion file intelligente : revenu/heure optimisé, 0 client perdu",
        "heure_min": 14, "heure_max": 17, "jour_semaine": -1,
    },
    {
        "categorie": "heure_pointe",
        "situation": "Client arrive pendant l'heure de pointe, regard sur les terminaux",
        "action": "Accroche rapide et puissante pour capter l'attention",
        "produit": "Nouveauté du moment",
        "argument": "Tu arrives au bon moment — on vient de recevoir le Samsung S25 Ultra. C'est le meilleur smartphone du marché là. Tu veux voir les specs en 2 minutes ?",
        "impact": "Accroche nouveauté : curiosité maximale pendant l'heure de pointe",
        "heure_min": 14, "heure_max": 17, "jour_semaine": -1,
    },
    {
        "categorie": "heure_pointe",
        "situation": "Pause déjeuner, clients pressés entre midi et 14h",
        "action": "Script ultra-rapide, décision en 5 minutes",
        "produit": "Forfait ou accessoire",
        "argument": "Tu as combien de temps ? 10 minutes ? Parfait. Je te propose exactement ce qu'il te faut, on signe, tu repars. Dis-moi ce que tu cherches.",
        "impact": "Adaptation rythme pause déjeuner : clientèle professionnelle captée",
        "heure_min": 12, "heure_max": 14, "jour_semaine": -1,
    },
    {
        "categorie": "heure_pointe",
        "situation": "Fin de semaine vendredi, clients de fin de semaine",
        "action": "Créer l'urgence weekend : profiter avant la fermeture",
        "produit": "Terminal ou bundle",
        "argument": "C'est vendredi, on ferme à 20h. Si tu veux profiter de ton nouveau téléphone tout le weekend, c'est maintenant qu'il faut agir. Lundi matin tu le configureras en arrivant au bureau.",
        "impact": "Urgence calendaire vendredi : +22% de ventes vs autres jours fin de journée",
        "heure_min": 16, "heure_max": 20, "jour_semaine": 4,
    },

    # ══════════════════════════════════════════════════════
    # ASSURANCE — 3 scripts
    # ══════════════════════════════════════════════════════
    {
        "categorie": "assurance",
        "situation": "Client qui a déjà cassé un téléphone ou perdu un précédent",
        "action": "Réactiver le souvenir douloureux de la perte",
        "produit": "Assurance Premium 9 TND/mois",
        "argument": "Tu m'as dit que tu as cassé ton dernier téléphone. Combien ça t'a coûté la réparation ? Avec l'assurance à 9 DT/mois, la même situation t'aurait coûté 0 DT. Sur 12 mois, c'est 108 DT pour une sérénité totale.",
        "impact": "Argument vécu personnel : taux de souscription assurance x3",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "assurance",
        "situation": "Client qui refuse l'assurance car 'ça ne m'arrivera pas'",
        "action": "Statistiques chocs sur les dommages smartphones",
        "produit": "Assurance Premium",
        "argument": "1 smartphone sur 3 est cassé ou volé dans les 2 premières années. C'est une stat Ooredoo sur notre base clients. 9 DT/mois sur 24 mois = 216 DT. Une réparation d'écran iPhone = 350-500 DT. La question c'est pas si ça arrivera, c'est quand.",
        "impact": "Argument statistique : moins anecdotique, plus persuasif pour les sceptiques",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie": "assurance",
        "situation": "Client qui souscrit un terminal à crédit, inquiet des risques",
        "action": "Lier l'assurance à la protection de l'engagement financier",
        "produit": "Assurance Premium liée à avance postpayé",
        "argument": "Tu t'engages sur 24 mois de paiement. Si le téléphone est cassé le mois 6, tu continues quand même à payer les 18 mois restants. L'assurance couvre ça : tu pars avec un remplacement, tu continues à payer normalement.",
        "impact": "Association assurance + crédit : sécurité financière, argument fort",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
]


# ──────────────────────────────────────────────────────────────────────────────
# MOTEUR DE SEED
# ──────────────────────────────────────────────────────────────────────────────

def trunc(s: str, max_bytes: int) -> str:
    """Truncate string so its UTF-8 encoding fits within max_bytes."""
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def embed(text: str) -> list:
    for attempt in range(3):
        try:
            r = requests.post(
                f"{OLLAMA_URL}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": text},
                timeout=60,
            )
            r.raise_for_status()
            emb = r.json().get("embedding", [])
            if not emb:
                raise ValueError("embedding vide")
            if len(emb) < EMBED_DIM:
                emb = emb + [0.0] * (EMBED_DIM - len(emb))
            return emb[:EMBED_DIM]
        except Exception as e:
            print(f"  [retry {attempt+1}/3] embed error: {e}")
            time.sleep(2)
    return []


def seed():
    client = MilvusClient(uri=MILVUS_URI)

    # Vider les données sans supprimer/recréer (évite le conflit de schéma Milvus)
    count_before = client.get_collection_stats(COLLECTION)["row_count"]
    print(f"Collection '{COLLECTION}' : {count_before} entrées avant seed")
    if count_before > 0:
        print("Purge des données existantes...")
        try:
            client.delete(collection_name=COLLECTION, filter="pg_id >= 0")
            time.sleep(2)
        except Exception as e:
            print(f"  Purge partielle ({e}) — continuation quand même")
    print("Insertion des nouveaux scripts...")

    inserted = 0
    errors   = 0

    for i, s in enumerate(SCRIPTS):
        text = (
            f"Situation: {s['situation']} "
            f"Action: {s['action']} "
            f"Produit: {s['produit']} "
            f"Argument: {s['argument']}"
        )
        print(f"[{i+1:02d}/{len(SCRIPTS)}] {s['categorie']:20s} — embed...", end=" ", flush=True)

        emb = embed(text)
        if not emb:
            print("ERREUR embedding — skipped")
            errors += 1
            continue

        row = {
            "vector":       emb,
            "pg_id":        i + 1,
            "categorie":    trunc(s["categorie"], 100),
            "situation":    trunc(s["situation"], 200),
            "action":       trunc(s["action"], 200),
            "produit":      trunc(s["produit"], 100),
            "argument":     trunc(s["argument"], 200),
            "impact":       trunc(s["impact"], 100),
            "heure_min":    s["heure_min"],
            "heure_max":    s["heure_max"],
            "jour_semaine": s["jour_semaine"],
            "store_id":     trunc(STORE_ID, 20),
        }
        try:
            client.insert(collection_name=COLLECTION, data=[row])
            inserted += 1
            print("OK")
        except Exception as e:
            print(f"ERREUR insert: {e}")
            errors += 1

    print()
    print(f"{'='*60}")
    print(f"Seed terminé : {inserted} scripts insérés | {errors} erreurs")
    final = client.get_collection_stats(COLLECTION)["row_count"]
    print(f"Total Milvus '{COLLECTION}' : {final} vecteurs")
    print(f"{'='*60}")

    if inserted > 0:
        print("\nTest recherche rapide...")
        test_emb = embed("client hésite trop cher comment closer")
        if test_emb:
            results = client.search(
                collection_name=COLLECTION,
                data=[test_emb],
                limit=3,
                output_fields=["categorie", "action", "impact"],
            )
            for hit in results[0]:
                e = hit["entity"]
                print(f"  score={hit['distance']:.3f} [{e['categorie']}] {e['action'][:60]}")


if __name__ == "__main__":
    seed()
