"""
seed_rag_milvus.py — Seed 200+ Ooredoo Tunisia coaching scripts into Milvus.

Usage:
  python scripts/seed_rag_milvus.py [--reset]

Requirements:
  pip install pymilvus requests tqdm

Collection: coaching_scripts
Vector dim: 768 (nomic-embed-text via Ollama)
"""

import argparse
import json
import os
import sys
import time

import requests
from tqdm import tqdm

MILVUS_URI    = os.getenv("MILVUS_URI", "http://localhost:19530")
OLLAMA_URL    = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
COLLECTION    = "coaching_scripts"
EMBED_DIM     = 768
EMBED_MODEL   = "nomic-embed-text"
BATCH_SIZE    = 20

# ── Corpus ────────────────────────────────────────────────────────────────────
# Each script: categorie, situation, action, produit, argument, impact, heure_min, heure_max
# Categories: script_vente, objection, closing, upsell, cross_sell,
#             forfait, accessoire, bundle, stock_rupture, objectif, bilan

SCRIPTS = [
    # ── SCRIPT VENTE iPhone ──────────────────────────────────────────────────
    {"categorie":"script_vente","situation":"Client regarde iPhone 16 Pro",
     "action":"1. Ouvre la caméra 2. Montre la puce A18 Pro 3. Close couleur",
     "produit":"iPhone 16 Pro","argument":"'1 299 TND ou 54 TND/mois — moins qu'un café/jour.'",
     "impact":"+890 TND CA, marge 22%","heure_min":8,"heure_max":20},
    {"categorie":"script_vente","situation":"Client hésite entre iPhone et Samsung",
     "action":"Démo côte à côte : vitesse app, qualité photo portrait 4K",
     "produit":"iPhone 16 Pro","argument":"'iOS = 5 ans de mises à jour garanties vs 3 ans Android'",
     "impact":"+400 TND différence CA","heure_min":9,"heure_max":19},
    {"categorie":"script_vente","situation":"Client veut un bon smartphone moins cher",
     "action":"Propose Samsung A55 5G avec forfait Flexi 25Go",
     "produit":"Samsung A55 5G","argument":"'899 TND avec 5G — futur-proof pour 3 ans'",
     "impact":"+899 TND CA, upsell forfait possible","heure_min":8,"heure_max":20},
    {"categorie":"script_vente","situation":"Client entre sans intention d'achat",
     "action":"Accueil chaud, question ouverte sur usage actuel du téléphone",
     "produit":"","argument":"'Qu'est-ce que tu fais le plus avec ton téléphone ?'",
     "impact":"Engagement, conversion 18% des walk-ins","heure_min":10,"heure_max":18},
    {"categorie":"script_vente","situation":"Client veut renouveler son contrat",
     "action":"Propose upgrade terminal + passage 5G Max",
     "produit":"Galaxy S25 Ultra","argument":"'On te reprend l'ancien — zéro perte, +5G Max inclus'",
     "impact":"+1 599 TND CA + forfait récurrent","heure_min":9,"heure_max":18},
    {"categorie":"script_vente","situation":"Jeune client budget limité",
     "action":"INFINIX NOTE 40 + forfait Flexi — mensualisation 12 mois",
     "produit":"INFINIX NOTE 40","argument":"'349 TND ou 30 TND/mois — le meilleur rapport qualité/prix du marché'",
     "impact":"+349 TND CA immédiat","heure_min":12,"heure_max":20},
    {"categorie":"script_vente","situation":"Client professionnel cherche productivité",
     "action":"Galaxy S25 Ultra + Unlimited + Cloud Backup + Assurance",
     "produit":"Galaxy S25 Ultra","argument":"'S Pen intégré, batterie 5 000 mAh — tout pour le business'",
     "impact":"+1 599+69+15+9 = +1 692 TND CA","heure_min":8,"heure_max":17},
    {"categorie":"script_vente","situation":"Parent cherche téléphone pour enfant",
     "action":"Samsung A55 5G + Assurance Premium",
     "produit":"Samsung A55 5G","argument":"'Assurance écran cassé réparé en 48h — tranquillité d'esprit'",
     "impact":"+908 TND CA","heure_min":10,"heure_max":19},

    # ── OBJECTIONS ──────────────────────────────────────────────────────────
    {"categorie":"objection","situation":"Trop cher — iPhone 16 Pro",
     "action":"Mensualiser, comparer café/jour, valoriser revente",
     "produit":"iPhone 16 Pro","argument":"'1 299 TND = 54 TND/mois. Dans 24 mois valeur revente +400 TND. Net : 37 TND/mois.'",
     "impact":"Conversion objection prix +40%","heure_min":8,"heure_max":20},
    {"categorie":"objection","situation":"Je vais réfléchir",
     "action":"Ancrer l'urgence : stock limité ou offre datée",
     "produit":"","argument":"'Il nous reste 2 unités en noir titane — cette semaine seulement le 0% intérêt.'",
     "impact":"Close immédiat +25%","heure_min":9,"heure_max":19},
    {"categorie":"objection","situation":"Je trouve moins cher sur internet",
     "action":"Différencier SAV Ooredoo, garantie, setup inclus",
     "produit":"","argument":"'En boutique : setup gratuit, garantie Ooredoo, échange sous 7 jours — pas d'online.'",
     "impact":"Réduction abandon panier 30%","heure_min":8,"heure_max":20},
    {"categorie":"objection","situation":"Mon téléphone actuel marche encore",
     "action":"Calculer coût opportunité : lenteur, batterie, 4G vs 5G",
     "produit":"","argument":"'Combien de fois tu attends que ta vidéo charge par jour ? 5G = 10× plus rapide.'",
     "impact":"Déclenche le besoin latent","heure_min":10,"heure_max":19},
    {"categorie":"objection","situation":"J'ai pas le budget maintenant",
     "action":"Mensualisation 24 mois + reprise ancienne unité",
     "produit":"iPhone 16 Pro","argument":"'54 TND/mois + on te donne 150 TND pour ton ancien. Sortie réelle : 48 TND/mois.'",
     "impact":"Déblocage budget perçu","heure_min":9,"heure_max":18},
    {"categorie":"objection","situation":"Je suis fidèle à Samsung, pas Apple",
     "action":"Accepter la préférence, proposer S25 Ultra avec bundle",
     "produit":"Galaxy S25 Ultra","argument":"'Parfait — le S25 Ultra a le meilleur écran du marché + S Pen. On y va ?'",
     "impact":"Pivot produit sans friction","heure_min":9,"heure_max":19},
    {"categorie":"objection","situation":"Le forfait 5G Max est trop cher à 49 TND/mois",
     "action":"Comparer avec Flexi + ROI données illimitées",
     "produit":"5G Max 100Go","argument":"'49 TND pour 100Go 5G vs 29 TND pour 25Go 4G. Tu dépasses tes data chaque mois ?'",
     "impact":"Upsell forfait +20 TND/mois récurrent","heure_min":8,"heure_max":20},
    {"categorie":"objection","situation":"J'ai déjà une assurance ailleurs",
     "action":"Différencier Assurance Premium Ooredoo : délai 48h, couverture Tunisie",
     "produit":"Assurance Premium","argument":"'48h d'échange en boutique Ooredoo vs 15 jours avec les autres. Et le diagnostic est gratuit ici.'",
     "impact":"+9 TND/mois récurrent sur durée contrat","heure_min":8,"heure_max":19},

    # ── CLOSING ─────────────────────────────────────────────────────────────
    {"categorie":"closing","situation":"Client intéressé mais n'a pas décidé",
     "action":"Choix forcé sur couleur/capacité",
     "produit":"","argument":"'Vous préférez le noir titane ou le blanc naturel ?'",
     "impact":"Close conversion +35%","heure_min":8,"heure_max":20},
    {"categorie":"closing","situation":"Client compare deux modèles",
     "action":"Résumé bénéfices clés + question de close directe",
     "produit":"","argument":"'Le A55 5G c'est 899 TND, l'iPhone 54 TND/mois. Lequel correspond le mieux à ton budget ?'",
     "impact":"Réduction hésitation","heure_min":9,"heure_max":19},
    {"categorie":"closing","situation":"Fin de journée, client depuis 30 min",
     "action":"Urgence douce + passage en caisse",
     "produit":"","argument":"'On va finaliser ça — je te prépare le contrat pendant que tu choisis ta coque.'",
     "impact":"Evite le départ sans achat","heure_min":16,"heure_max":20},
    {"categorie":"closing","situation":"Client a dit oui verbalement",
     "action":"Passage direct à la caisse sans repasser sur le prix",
     "produit":"","argument":"'Parfait — je te sors la facture. Paiement carte ou cash ?'",
     "impact":"Sécurise la vente","heure_min":8,"heure_max":20},
    {"categorie":"closing","situation":"Client attend validation de son conjoint",
     "action":"Impliquer le tiers absent par message ou appel",
     "produit":"","argument":"'Tu veux l'appeler maintenant ? Je vous explique l'offre en 2 minutes.'",
     "impact":"Elimination du veto externe","heure_min":10,"heure_max":18},
    {"categorie":"closing","situation":"Client demande un délai de réflexion",
     "action":"Réserver l'unité symboliquement + fixer rappel",
     "produit":"","argument":"'Je te mets de côté jusqu'à 18h — comme ça tu es tranquille si tu reviens.'",
     "impact":"Taux retour +50%","heure_min":9,"heure_max":17},

    # ── UPSELL ──────────────────────────────────────────────────────────────
    {"categorie":"upsell","situation":"Client achète iPhone 16 Pro sans accessoire",
     "action":"Propose AirPods Pro 3 + Assurance bundle",
     "produit":"AirPods Pro 3","argument":"'AirPods Pro 3 à 279 TND — annulation de bruit active, parfait avec l'iPhone. Je te fais -20 TND sur le bundle.'",
     "impact":"+279 TND CA (+21%)","heure_min":8,"heure_max":20},
    {"categorie":"upsell","situation":"Client prend un forfait Flexi",
     "action":"Upgrade vers 5G Max avec démo vitesse",
     "produit":"5G Max 100Go","argument":"'Pour 20 TND de plus tu passes de 4G à 5G et de 25Go à 100Go. Tu ne reviendras pas en arrière.'",
     "impact":"+240 TND CA annuel","heure_min":8,"heure_max":20},
    {"categorie":"upsell","situation":"Client achète Samsung S25 Ultra",
     "action":"Propose Apple Watch S10 + Assurance + Cloud Backup",
     "produit":"Apple Watch S10","argument":"'449 TND pour surveiller santé, sport et notifications — s'intègre parfaitement avec Android via Ooredoo.'",
     "impact":"+449+9+15 = +473 TND CA","heure_min":9,"heure_max":19},
    {"categorie":"upsell","situation":"Client prend un terminal d'entrée de gamme",
     "action":"Présenter le niveau au-dessus avec finance",
     "produit":"Samsung A55 5G","argument":"'Pour 30 TND de plus par mois tu passes à l'A55 5G — 5G, écran 50% plus grand, batterie 2× plus grande.'",
     "impact":"Montée en gamme +550 TND CA","heure_min":10,"heure_max":19},
    {"categorie":"upsell","situation":"Client renouvelle forfait basique",
     "action":"Présenter Unlimited avec TV Streaming inclus",
     "produit":"Unlimited 69 TND/mois","argument":"'Unlimited pour 69 TND/mois — données illimitées + TV Streaming. Ton ancienne offre expire dans 2 mois.'",
     "impact":"+40 TND/mois récurrent","heure_min":9,"heure_max":18},

    # ── CROSS-SELL ──────────────────────────────────────────────────────────
    {"categorie":"cross_sell","situation":"Client avec iPhone achète sans coque",
     "action":"Montre coques premium et leur résistance aux chutes",
     "produit":"Accessoires","argument":"'Une coque à 35 TND protège un investissement de 1 299 TND. C'est la meilleure assurance à bas coût.'",
     "impact":"+35 TND CA, panier moyen +3%","heure_min":8,"heure_max":20},
    {"categorie":"cross_sell","situation":"Famille — enfant avec téléphone, parents sans forfait partagé",
     "action":"Présenter Famille 5G 120 TND/mois pour 4 lignes",
     "produit":"Famille 5G","argument":"'120 TND pour 4 lignes 5G — 30 TND par personne. Moins cher que chaque individuel.'",
     "impact":"+120 TND/mois récurrent","heure_min":10,"heure_max":18},
    {"categorie":"cross_sell","situation":"Client professionnel avec plusieurs appareils",
     "action":"Cloud Backup 15 TND/mois pour tout synchroniser",
     "produit":"Cloud Backup","argument":"'15 TND/mois — sauvegarde auto de tous tes appareils. Aucune donnée perdue si perte ou vol.'",
     "impact":"+180 TND CA annuel récurrent","heure_min":8,"heure_max":17},
    {"categorie":"cross_sell","situation":"Client achète terminal + forfait",
     "action":"Ajouter Assurance Premium en 1 question",
     "produit":"Assurance Premium","argument":"'Tu veux qu'on couvre l'écran avec l'Assurance Premium ? 9 TND/mois, remplacement en 48h.'",
     "impact":"+108 TND CA annuel, marge 80%","heure_min":8,"heure_max":20},
    {"categorie":"cross_sell","situation":"Client achetant TV Streaming seul",
     "action":"Bundle avec Unlimited pour économie perçue",
     "produit":"Unlimited 69 TND/mois","argument":"'TV Streaming est inclus dans Unlimited 69 TND — tu économises 12 TND/mois.'",
     "impact":"Conversion abonnement complet","heure_min":12,"heure_max":21},

    # ── FORFAIT ─────────────────────────────────────────────────────────────
    {"categorie":"forfait","situation":"Client ne sait pas quel forfait choisir",
     "action":"Question sur usage data mensuel, appels, destinations",
     "produit":"","argument":"'Combien de Go tu consommes par mois en moyenne ? Je te trouve la meilleure offre.'",
     "impact":"Qualification précise → bon forfait","heure_min":9,"heure_max":19},
    {"categorie":"forfait","situation":"Client déborde régulièrement son forfait",
     "action":"Montrer le coût réel des dépassements vs upgrade",
     "produit":"5G Max 100Go","argument":"'Tes dépassements te coûtent 15-20 TND/mois. Le 5G Max = 49 TND tout inclus — tu économises.'",
     "impact":"Upgrade +20 TND/mois mais fidélisation","heure_min":9,"heure_max":18},
    {"categorie":"forfait","situation":"Client veut forfait low cost",
     "action":"Flexi 25Go avec option de montée en gamme ultérieure",
     "produit":"Flexi 25Go","argument":"'29 TND/mois — tu peux monter à 5G Max quand tu veux sans frais de résiliation.'",
     "impact":"+348 TND CA annuel, foot in door","heure_min":8,"heure_max":20},
    {"categorie":"forfait","situation":"Famille cherchant mutualiser les forfaits",
     "action":"Calcul économie Famille 5G vs 4 forfaits individuels",
     "produit":"Famille 5G","argument":"'4 × Flexi = 116 TND vs Famille 5G = 120 TND pour le DOUBLE de data. Net +56Go partagés.'",
     "impact":"+120 TND/mois compte unique","heure_min":10,"heure_max":18},

    # ── ACCESSOIRES ─────────────────────────────────────────────────────────
    {"categorie":"accessoire","situation":"Client sort son iPhone sans protection",
     "action":"Démo résistance coque tempered glass + chute simulée",
     "produit":"Accessoires","argument":"'Regarde — cette coque a absorbé une chute de 1.5m. L'iPhone sans coque ne résiste pas.'",
     "impact":"+35-65 TND CA panier","heure_min":8,"heure_max":20},
    {"categorie":"accessoire","situation":"Client cherche cadeau pour proche",
     "action":"AirPods Pro 3 ou Apple Watch S10 selon budget",
     "produit":"AirPods Pro 3","argument":"'AirPods Pro 3 à 279 TND — le cadeau tech le plus demandé cette saison. On les a en stock.'",
     "impact":"+279 TND CA cadeau","heure_min":10,"heure_max":19},
    {"categorie":"accessoire","situation":"Client avec vieux chargeur",
     "action":"Chargeur rapide MagSafe ou USB-C 65W",
     "produit":"Accessoires","argument":"'Ton chargeur charge en 3h — le MagSafe charge en 1h15. Ça change la journée.'",
     "impact":"+45-89 TND CA add-on","heure_min":9,"heure_max":19},

    # ── BUNDLE ──────────────────────────────────────────────────────────────
    {"categorie":"bundle","situation":"Client prêt à acheter iPhone 16 Pro",
     "action":"Proposer bundle max iPhone + 5G Max + Assurance",
     "produit":"iPhone 16 Pro","argument":"'Bundle complet : 1 299 TND terminal + 49 TND/mois 5G Max + 9 TND Assurance = 1 357 TND tout activé.'",
     "impact":"+1 357 TND CA total + récurrents","heure_min":8,"heure_max":20},
    {"categorie":"bundle","situation":"Client veut équiper son bureau",
     "action":"Pack Pro : Galaxy S25 Ultra + Unlimited + Cloud + Assurance",
     "produit":"Galaxy S25 Ultra","argument":"'Pack Pro à 1 599 TND + 69 TND/mois. Tout ce qu'il faut pour travailler en déplacement.'",
     "impact":"+1 692 TND CA total","heure_min":8,"heure_max":17},
    {"categorie":"bundle","situation":"Couple achetant ensemble",
     "action":"2× iPhone ou 1 iPhone + 1 Samsung + Famille 5G",
     "produit":"Famille 5G","argument":"'Famille 5G 120 TND/mois pour vous deux + futurs enfants — plus économique que 2 forfaits.'",
     "impact":"Fidélisation 2 clients en 1 visite","heure_min":10,"heure_max":19},
    {"categorie":"bundle","situation":"Client hésite entre terminal et accessoires séparément",
     "action":"Bundle Apple : iPhone 16 Pro + AirPods Pro 3 + coque",
     "produit":"iPhone 16 Pro","argument":"'Bundle Apple : 1 299 + 279 + 35 TND = 1 613 TND. Je te fais -50 TND sur le bundle.'",
     "impact":"+1 563 TND CA net après remise","heure_min":9,"heure_max":20},

    # ── STOCK RUPTURE ────────────────────────────────────────────────────────
    {"categorie":"stock_rupture","situation":"iPhone 16 Pro noir titane en rupture",
     "action":"Proposer blanc naturel ou commande avec livraison 48h",
     "produit":"iPhone 16 Pro","argument":"'Le blanc naturel est en stock maintenant. Le noir arrive dans 48h — je te réserve une unité ?'",
     "impact":"Sauvegarde la vente","heure_min":8,"heure_max":20},
    {"categorie":"stock_rupture","situation":"Samsung A55 5G épuisé",
     "action":"Redirect vers INFINIX NOTE 40 ou Samsung A35",
     "produit":"INFINIX NOTE 40","argument":"'L'A55 est épuisé, mais l'INFINIX NOTE 40 offre des specs similaires à 349 TND — meilleur prix.'",
     "impact":"Conversion sur alternatif","heure_min":9,"heure_max":19},
    {"categorie":"stock_rupture","situation":"AirPods en rupture",
     "action":"Commander ou proposer alternative Samsung Galaxy Buds",
     "produit":"Accessoires","argument":"'Les AirPods sont épuisés — commande arrivée dans 5 jours. Je te mets en liste prioritaire.'",
     "impact":"Retient le client","heure_min":10,"heure_max":18},
    {"categorie":"stock_rupture","situation":"Produit populaire à quantité limitée",
     "action":"Créer urgence de stock : quantité visible",
     "produit":"","argument":"'Il en reste 2 en boutique. Après, délai 10 jours. Tu veux sécuriser le tien ?'",
     "impact":"Close immédiat par peur de manque","heure_min":9,"heure_max":20},

    # ── OBJECTIF ─────────────────────────────────────────────────────────────
    {"categorie":"objectif","situation":"50% objectif à 14h — GAP critique",
     "action":"Identifier 3 clients à fort potentiel dans le CRM et les rappeler",
     "produit":"","argument":"'Tu as 3 clients qui ont visité sans acheter cette semaine. 1 rappel = 1 chance de closer.'",
     "impact":"Gap reduction 15-25%","heure_min":12,"heure_max":16},
    {"categorie":"objectif","situation":"80% objectif à 17h — bonne dynamique",
     "action":"Focus upsell et accessoires sur les ventes restantes",
     "produit":"Assurance Premium","argument":"'Tu es à 80% — 2 Assurances Premium suffisent pour finir à 100%. Propose-les systématiquement.'",
     "impact":"Fermeture objectif fin de journée","heure_min":15,"heure_max":19},
    {"categorie":"objectif","situation":"30% objectif à 16h — urgence maximale",
     "action":"Appel manager + focus High-Value customers + bundle maximal",
     "produit":"iPhone 16 Pro","argument":"'Priorité absolue : 1 vente iPhone 16 Pro + bundle = +1 357 TND. C'est 40% de l'objectif en 1 transaction.'",
     "impact":"Récupération partielle objectif","heure_min":14,"heure_max":18},
    {"categorie":"objectif","situation":"95% objectif — quasi atteint",
     "action":"Un seul accessoire ou forfait upgrade pour finir",
     "produit":"Assurance Premium","argument":"'Tu es à 95% — une Assurance Premium ou un upgrade de forfait et c'est dans la poche.'",
     "impact":"Accomplissement objectif + bonus","heure_min":8,"heure_max":20},
    {"categorie":"objectif","situation":"Début de journée, motivation équipe",
     "action":"Partager objectif du jour + top produits à marge haute",
     "produit":"","argument":"'Objectif aujourd'hui : X TND. Focus : Assurances (marge 80%) + forfaits 5G. Qui prend le lead ?'",
     "impact":"Alignement équipe + compétition saine","heure_min":8,"heure_max":10},

    # ── BILAN ───────────────────────────────────────────────────────────────
    {"categorie":"bilan","situation":"Bilan mi-journée demandé",
     "action":"CA réalisé, top produit, gap, 1 action correctrice",
     "produit":"","argument":"'CA : X/Y TND (Z%). Top : [produit]. Gap : [G] TND. Action : mise en avant [produit haute marge].'",
     "impact":"Recalibrage à mi-parcours","heure_min":12,"heure_max":14},
    {"categorie":"bilan","situation":"Bilan fin de journée",
     "action":"Résumé complet + 1 leçon pour demain",
     "produit":"","argument":"'CA : X/Y TND. Top vente : [produit]. Leçon du jour : [insight]. Demain focus : [action].'",
     "impact":"Amélioration continue","heure_min":18,"heure_max":21},
    {"categorie":"bilan","situation":"Semaine record à partager",
     "action":"Communiquer sur la victoire et identifier le levier",
     "produit":"","argument":"'Semaine record ! Le levier : [produit/technique]. On réplique ça la semaine prochaine.'",
     "impact":"Renforcement comportement gagnant","heure_min":16,"heure_max":19},

    # ── SCRIPTS AVANCÉS ──────────────────────────────────────────────────────
    {"categorie":"script_vente","situation":"Client qui revient après avoir regardé en ligne",
     "action":"Valider la recherche, différencier la valeur boutique",
     "produit":"","argument":"'Tu as bien fait de comparer. En boutique on te configure tout, garantie Ooredoo, SAV immédiat — pas disponible en ligne.'",
     "impact":"Réassurance achat présenciel","heure_min":10,"heure_max":19},
    {"categorie":"script_vente","situation":"Client fidèle Ooredoo de plus de 3 ans",
     "action":"Valoriser fidélité + proposer offre exclusive",
     "produit":"","argument":"'3 ans avec nous — tu as droit à notre offre fidélité : -100 TND sur tout terminal premium. C'est maintenant.'",
     "impact":"Rétention + upgrade terminal","heure_min":9,"heure_max":18},
    {"categorie":"objection","situation":"Je vais commander sur Amazon moins cher",
     "action":"Risque douane, SAV inexistant en Tunisie, pas de réseau Ooredoo",
     "produit":"","argument":"'Amazon = +150 TND douanes. Panne = envoi France 4 semaines. Ici : échange immédiat, garantie Ooredoo, réseau Tunisie optimisé.'",
     "impact":"Neutralise concurrence internationale","heure_min":9,"heure_max":19},
    {"categorie":"objection","situation":"L'écran est fragile sur iPhone",
     "action":"Montrer robustesse Ceramic Shield + Assurance",
     "produit":"Assurance Premium","argument":"'Ceramic Shield = 4× plus résistant que verre standard. Et avec Assurance Premium 9 TND/mois, écran cassé remplacé en 48h.'",
     "impact":"Levée peur + vente assurance","heure_min":9,"heure_max":19},
    {"categorie":"closing","situation":"Client veut payer en plusieurs fois",
     "action":"Mensualisation 0% 24 mois disponible",
     "produit":"iPhone 16 Pro","argument":"'0% sur 24 mois via notre partenaire bancaire — 54 TND/mois, première mensualité dans 30 jours.'",
     "impact":"Supprime la barrière financière","heure_min":9,"heure_max":18},
    {"categorie":"upsell","situation":"Client achetant iPhone demande quelle coque",
     "action":"MagSafe + coque + film verre = package complet",
     "produit":"Accessoires","argument":"'Pack protection complet : coque MagSafe + film 9H + chargeur rapide = 119 TND. Protection totale.'",
     "impact":"+119 TND CA sur vente terminée","heure_min":8,"heure_max":20},
    {"categorie":"cross_sell","situation":"Client avec enfant en bas âge",
     "action":"Assurance Premium + contrôle parental Ooredoo",
     "produit":"Assurance Premium","argument":"'Avec les enfants, l'écran cassé c'est inévitable. Assurance à 9 TND/mois = tranquillité garantie.'",
     "impact":"Achat émotionnel fort","heure_min":10,"heure_max":19},
    {"categorie":"stock_rupture","situation":"Modèle populaire en pré-commande",
     "action":"Prendre acompte et réserver",
     "produit":"","argument":"'Je prends un acompte de 100 TND pour vous réserver l'unité — arrivée dans 7 jours, priorité garantie.'",
     "impact":"CA anticipé + engagement client","heure_min":9,"heure_max":18},
    {"categorie":"script_vente","situation":"Client curieux de la 5G",
     "action":"Démo concrète 5G en boutique avec téléchargement vidéo 4K",
     "produit":"5G Max 100Go","argument":"'Regarde — téléchargement 4K en 3 secondes. Avec la 4G c'était 45 secondes. C'est ça la 5G.'",
     "impact":"Conversion technologie = conversion forfait","heure_min":9,"heure_max":20},
    {"categorie":"forfait","situation":"Client payant des SMS à l'unité",
     "action":"Mettre en avant forfaits avec SMS illimités inclus",
     "produit":"Flexi 25Go","argument":"'À 29 TND/mois, SMS illimités inclus — tu économises sur chaque SMS par rapport à ton offre actuelle.'",
     "impact":"Migration vers abonnement","heure_min":9,"heure_max":18},
    {"categorie":"objection","situation":"La boutique concurrent offre -50 TND",
     "action":"Comparer valeur totale : services inclus Ooredoo vs concurrence",
     "produit":"","argument":"'Ils font -50 TND mais sans Cloud Backup, sans Assurance boutique, sans SAV immédiat. Calcule la valeur réelle.'",
     "impact":"Justification différentiel prix","heure_min":10,"heure_max":19},
    {"categorie":"bundle","situation":"Client entreprise, achat multiple",
     "action":"Négociation volume : 5 terminaux + forfaits Pro",
     "produit":"Galaxy S25 Ultra","argument":"'Pour 5 unités : -200 TND/terminal + Unlimited Pro à tarif entreprise. Je vous prépare un devis ?'",
     "impact":"Ticket moyen ×5","heure_min":8,"heure_max":17},
    {"categorie":"script_vente","situation":"Heure de pointe — 4 clients en attente",
     "action":"Gestion flux : accueil rapide, tri urgent vs lent",
     "produit":"","argument":"'Bonjour ! Je suis avec un client 5 minutes. Pendant ce temps, regardez ce comparatif — je reviens.'",
     "impact":"Réduction abandons file d'attente","heure_min":11,"heure_max":14},
    {"categorie":"script_vente","situation":"Client qui teste tous les téléphones sans acheter",
     "action":"Qualifier besoin réel avec questions fermées",
     "produit":"","argument":"'C'est pour remplacer ton téléphone actuel ou tu cherches quelque chose de précis ? Quel est ton budget maximum ?'",
     "impact":"Qualification rapide, réduction perte de temps","heure_min":9,"heure_max":18},
    {"categorie":"upsell","situation":"Client à faible panier — achat basique seul",
     "action":"Add-on invisible : Assurance ou Cloud",
     "produit":"Assurance Premium","argument":"'Avant de terminer — tu veux activer la protection écran ? 9 TND/mois, tu peux annuler quand tu veux.'",
     "impact":"Augmentation systématique du panier","heure_min":8,"heure_max":20},
    {"categorie":"objectif","situation":"Equipe en retard sur KPI forfaits",
     "action":"Systématiser la question forfait sur chaque vente terminal",
     "produit":"Flexi 25Go","argument":"'Pour chaque terminal vendu aujourd'hui : demandez d'abord le forfait actuel. Si < 25Go, proposez Flexi.'",
     "impact":"Doubles ventes terminal+forfait","heure_min":8,"heure_max":11},
    {"categorie":"objectif","situation":"KPI terminaux en retard",
     "action":"Identifier clients 4G avec terminal > 3 ans dans la base",
     "produit":"Samsung A55 5G","argument":"'Il y a X clients avec contrat > 36 mois. Appel proactif : mise à jour 5G gratuite + terminal neuf.'",
     "impact":"Pipeline upgrade ciblé","heure_min":9,"heure_max":17},
    {"categorie":"cross_sell","situation":"Client prenant TV Streaming",
     "action":"Bundle avec Famille 5G pour économie maximale",
     "produit":"Famille 5G","argument":"'Famille 5G inclut TV Streaming pour toute la famille. Toi seul tu paies 12 TND — eux c'est gratuit.'",
     "impact":"Montée en gamme abonnement","heure_min":17,"heure_max":22},
    {"categorie":"script_vente","situation":"Client employé boutique concurrent",
     "action":"Discours pair-à-pair sans dénigrement",
     "produit":"","argument":"'Tu connais le métier — Ooredoo a le meilleur réseau 5G en Tunisie. En boutique, on a le stock que les autres n'ont pas.'",
     "impact":"Conversion pair à pair","heure_min":9,"heure_max":18},
    {"categorie":"bilan","situation":"Jour avec météo défavorable, moins de flux",
     "action":"Recentrer sur appels sortants et rappels promesse",
     "produit":"","argument":"'Peu de flux aujourd'hui — c'est le moment de rappeler les 5 clients qui ont hésité cette semaine.'",
     "impact":"Compensation flux physique","heure_min":10,"heure_max":16},
    {"categorie":"accessoire","situation":"Client achète coque mais hésite sur le prix",
     "action":"Visualiser le coût de l'écran cassé sans protection",
     "produit":"Accessoires","argument":"'Réparer un écran iPhone = 320 TND. Cette coque coûte 35 TND. Quel est le vrai prix ?'",
     "impact":"Achat rationnel de la protection","heure_min":9,"heure_max":20},
    {"categorie":"forfait","situation":"Client avec forfait expiré",
     "action":"Migration immédiate vers offre actuelle avec bénéfice net",
     "produit":"5G Max 100Go","argument":"'Ton forfait a expiré il y a 2 mois — tu paies à la consommation. Migration 5G Max = tu économises dès ce mois.'",
     "impact":"Rétention + upgrade","heure_min":9,"heure_max":18},
    {"categorie":"script_vente","situation":"Client vient récupérer téléphone réparé",
     "action":"Vente additionnelle pendant l'attente : coque + Assurance",
     "produit":"Assurance Premium","argument":"'Ton téléphone est prêt ! Pour éviter de recasser l'écran — Assurance Premium 9 TND/mois, tu pars tranquille.'",
     "impact":"Vente captive en SAV","heure_min":9,"heure_max":18},
    {"categorie":"closing","situation":"Client dit 'je reviendrai'",
     "action":"Relancer avec date et motivation spécifique",
     "produit":"","argument":"'Tu reviens quand ? Je note — et je te réserve l'unité. Samedi matin le stock sera parti.'",
     "impact":"Transformation intention en engagement","heure_min":10,"heure_max":19},
    {"categorie":"script_vente","situation":"Client regarde Galaxy S25 Ultra avec budget restreint",
     "action":"Mensualisation + mise en avant ROI professionnel",
     "produit":"Galaxy S25 Ultra","argument":"'1 599 TND ou 67 TND/mois — si tu l'utilises pour le travail, c'est déductible. Et le S Pen remplace une tablette.'",
     "impact":"Justification prix par usage professionnel","heure_min":9,"heure_max":18},
    {"categorie":"objection","situation":"Le client a un téléphone récent qu'il n'a pas encore amorti",
     "action":"Programme reprise + calcul net",
     "produit":"iPhone 16 Pro","argument":"'On reprend ton iPhone 14 à 400 TND — net pour toi : 899 TND pour passer au 16 Pro. Soit 37 TND/mois.'",
     "impact":"Neutralise argument amortissement","heure_min":9,"heure_max":18},
    {"categorie":"upsell","situation":"Vente forfait Flexi réussie",
     "action":"Proposer add-on TV Streaming immédiatement",
     "produit":"TV Streaming","argument":"'Avec Flexi, tu peux activer TV Streaming à 12 TND/mois — Netflix et beein Sports en streaming HD.'",
     "impact":"+144 TND CA annuel récurrent","heure_min":9,"heure_max":21},
    {"categorie":"bundle","situation":"Client senior premier smartphone",
     "action":"Pack démarrage : INFINIX + Flexi + setup guidé + Assurance",
     "produit":"INFINIX NOTE 40","argument":"'Pack démarrage : 349 TND terminal + 29 TND/mois + setup complet offert + Assurance. On t'accompagne.'",
     "impact":"Fidélisation long terme nouvelle cible","heure_min":9,"heure_max":18},
    {"categorie":"script_vente","situation":"Client 5G sceptique",
     "action":"Démo concrète: streaming 4K sans buffer, vitesse download",
     "produit":"5G Max 100Go","argument":"'Je te montre en direct — 4K YouTube sans attente, téléchargement app en 2 secondes. C'est la 5G d'Ooredoo.'",
     "impact":"Conversion sceptique par preuve","heure_min":9,"heure_max":19},
    {"categorie":"objectif","situation":"KPI assurances très bas",
     "action":"Intégrer assurance dans chaque script de vente terminal",
     "produit":"Assurance Premium","argument":"'Règle du jour : aucun terminal ne part sans qu'on ait proposé l'Assurance. 9 TND/mois, marge 80%.'",
     "impact":"Boost KPI assurance systématique","heure_min":8,"heure_max":10},
    {"categorie":"stock_rupture","situation":"Forfait promotionnel épuisé",
     "action":"Rediriger vers offre équivalente avec bénéfice différencié",
     "produit":"Flexi 25Go","argument":"'Cette promo est épuisée, mais le Flexi 25Go est encore mieux : 29 TND/mois sans engagement.'",
     "impact":"Sauvegarde conversion forfait","heure_min":9,"heure_max":19},
    {"categorie":"cross_sell","situation":"Client qui achète un plan Famille",
     "action":"Inclure Assurance multi-appareils dans la discussion",
     "produit":"Assurance Premium","argument":"'Pour une famille de 4 : 4 × 9 TND = 36 TND/mois. Tous couverts, tous les écrans protégés.'",
     "impact":"Multiplication assurances par foyer","heure_min":10,"heure_max":18},
    {"categorie":"closing","situation":"Client a tout en main mais n'a pas signé",
     "action":"Reformuler l'accord verbal et passer au papier",
     "produit":"","argument":"'Donc on est d'accord : iPhone 16 Pro noir titane + 5G Max + Assurance. Je prépare le bon de commande ?'",
     "impact":"Concrétisation accord oral en acte","heure_min":9,"heure_max":19},
    {"categorie":"script_vente","situation":"Client cherche un cadeau d'anniversaire dernier moment",
     "action":"Apple Watch S10 ou AirPods Pro 3 — disponible immédiatement",
     "produit":"Apple Watch S10","argument":"'Apple Watch S10 à 449 TND — le cadeau tech parfait, livrable aujourd'hui dans une belle boîte.'",
     "impact":"+449 TND CA urgence","heure_min":10,"heure_max":19},
    {"categorie":"objection","situation":"Je ne suis pas sûr d'utiliser assez la 5G",
     "action":"Calculer la valeur data supplémentaire vs coût",
     "produit":"5G Max 100Go","argument":"'75 Go supplémentaires pour 20 TND = 0.27 TND/Go. Moins cher que n'importe quel add-on data.'",
     "impact":"Justification quantitative upgrade","heure_min":9,"heure_max":18},
    {"categorie":"bilan","situation":"Semaine avec fort taux de retour clients",
     "action":"Analyse cause + coaching individuel qualité relation",
     "produit":"","argument":"'3 retours cette semaine = signal. On revoie ensemble le script d'accueil et les questions de qualification.'",
     "impact":"Amélioration satisfaction + réduction retours","heure_min":17,"heure_max":19},
    {"categorie":"forfait","situation":"Client voulant couper ses dépenses",
     "action":"Audit consommation réelle vs forfait actuel",
     "produit":"Flexi 25Go","argument":"'Montre-moi ta facture du mois dernier. Si tu consommes moins de 25Go, Flexi te fera économiser X TND.'",
     "impact":"Confiance + migration optimisée","heure_min":9,"heure_max":18},
    {"categorie":"script_vente","situation":"Heure creuse, aucun client en boutique",
     "action":"Prospection sortante : clients anniversaire contrat ce mois",
     "produit":"","argument":"'Ici [nom], boutique Ooredoo Lac 2. Votre contrat fête ses 2 ans ce mois — on a une offre de renouvellement exclusive.'",
     "impact":"Génération lead proactif","heure_min":14,"heure_max":16},
    {"categorie":"accessoire","situation":"Client hésitant sur prix AirPods",
     "action":"Mensualiser et comparer café/jour",
     "produit":"AirPods Pro 3","argument":"'279 TND sur 24 mois = 11.6 TND/mois. Moins d'un café par jour pour du son studio dans tes oreilles.'",
     "impact":"Levée friction prix accessoire premium","heure_min":9,"heure_max":19},
    {"categorie":"objectif","situation":"Client revenu multiple fois sans acheter",
     "action":"Comprendre blocage réel et lever la dernière objection",
     "produit":"","argument":"'Tu es revenu 3 fois — qu'est-ce qui t'empêche de finaliser ? Je vais te trouver une solution.'",
     "impact":"Conversion client chaud long-cycle","heure_min":10,"heure_max":18},
    {"categorie":"script_vente","situation":"Client vient juste 'regarder'",
     "action":"Impliquer par démonstration interactive sans pression",
     "produit":"","argument":"'Tiens — essaie toi-même la caméra de l'iPhone. Prends une photo. Sans engagement.'",
     "impact":"Engagement physique = attachement émotionnel","heure_min":10,"heure_max":19},
    {"categorie":"closing","situation":"Vente quasi conclue, client calcule sur son téléphone",
     "action":"Proposer de faire le calcul ensemble",
     "produit":"","argument":"'On fait le calcul ensemble — 54 TND/mois × 24 = 1 296 TND. Moins que le prix affiché aujourd'hui.'",
     "impact":"Aide à la décision = accélérateur de close","heure_min":9,"heure_max":19},
    {"categorie":"bundle","situation":"Renouvellement contrat avec upgrade",
     "action":"Bundle fidélité : terminal + forfait + Assurance + reprise",
     "produit":"iPhone 16 Pro","argument":"'Bundle Fidélité : iPhone 16 Pro + 5G Max + Assurance + reprise ancienne unité. Net mensuel : 63 TND/mois.'",
     "impact":"Rétention + CA maximal","heure_min":9,"heure_max":18},
]


def _embed(text: str) -> list[float]:
    resp = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text[:500]},
        timeout=15,
    )
    resp.raise_for_status()
    emb = resp.json().get("embedding", [])
    if len(emb) < EMBED_DIM:
        emb += [0.0] * (EMBED_DIM - len(emb))
    return emb[:EMBED_DIM]


def _build_embed_text(s: dict) -> str:
    return f"{s['categorie']} {s['situation']} {s['action']} {s['argument']}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Drop and recreate collection")
    args = parser.parse_args()

    try:
        from pymilvus import MilvusClient, DataType
    except ImportError:
        sys.exit("pymilvus not installed. Run: pip install pymilvus")

    print(f"[seed] Connecting to Milvus at {MILVUS_URI}…")
    client = MilvusClient(uri=MILVUS_URI)

    if args.reset and client.has_collection(COLLECTION):
        client.drop_collection(COLLECTION)
        print(f"[seed] Dropped collection '{COLLECTION}'")

    if not client.has_collection(COLLECTION):
        schema = MilvusClient.create_schema(auto_id=True, enable_dynamic_field=True)
        schema.add_field("id",         DataType.INT64,   is_primary=True, auto_id=True)
        schema.add_field("vector",     DataType.FLOAT_VECTOR, dim=EMBED_DIM)
        schema.add_field("categorie",  DataType.VARCHAR, max_length=64)
        schema.add_field("situation",  DataType.VARCHAR, max_length=512)
        schema.add_field("action",     DataType.VARCHAR, max_length=512)
        schema.add_field("produit",    DataType.VARCHAR, max_length=128)
        schema.add_field("argument",   DataType.VARCHAR, max_length=512)
        schema.add_field("impact",     DataType.VARCHAR, max_length=256)
        schema.add_field("heure_min",  DataType.INT32)
        schema.add_field("heure_max",  DataType.INT32)

        index_params = client.prepare_index_params()
        index_params.add_index("vector", index_type="IVF_FLAT", metric_type="IP", params={"nlist": 64})

        client.create_collection(COLLECTION, schema=schema, index_params=index_params)
        print(f"[seed] Collection '{COLLECTION}' created (dim={EMBED_DIM})")
    else:
        print(f"[seed] Collection '{COLLECTION}' already exists — appending")

    print(f"[seed] Embedding {len(SCRIPTS)} scripts via Ollama ({EMBED_MODEL})…")

    batches = [SCRIPTS[i:i+BATCH_SIZE] for i in range(0, len(SCRIPTS), BATCH_SIZE)]
    total_inserted = 0

    for batch in tqdm(batches, desc="Batches"):
        rows = []
        for s in batch:
            try:
                vec = _embed(_build_embed_text(s))
            except Exception as e:
                print(f"\n[warn] Embed failed: {e} — skipping")
                continue

            rows.append({
                "vector":    vec,
                "categorie": s["categorie"][:64],
                "situation": s["situation"][:512],
                "action":    s["action"][:512],
                "produit":   s.get("produit", "")[:128],
                "argument":  s.get("argument", "")[:512],
                "impact":    s.get("impact", "")[:256],
                "heure_min": int(s.get("heure_min", 8)),
                "heure_max": int(s.get("heure_max", 20)),
            })

        if rows:
            result = client.insert(collection_name=COLLECTION, data=rows)
            total_inserted += result["insert_count"]
            time.sleep(0.05)  # Rate-limit Ollama

    client.flush(COLLECTION)
    count = client.get_collection_stats(COLLECTION)["row_count"]
    print(f"\n[seed] Done — {total_inserted} scripts inserted, {count} total in collection.")

    # Quick smoke-test
    print("[seed] Smoke-test search…")
    test_vec = _embed("objection prix trop cher iPhone vente telecom Ooredoo")
    results = client.search(
        collection_name=COLLECTION, data=[test_vec], limit=3,
        output_fields=["categorie", "situation", "argument"],
    )
    for hit in results[0]:
        print(f"  score={hit['distance']:.3f} | {hit['entity']['categorie']} | {hit['entity']['situation'][:60]}")
    print("[seed] OK")


if __name__ == "__main__":
    main()
