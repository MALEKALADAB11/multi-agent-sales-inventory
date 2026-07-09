# Description détaillée de la base de données « ooredoo_sales »

Document descriptif rédigé en texte simple, sans code. Il décrit fidèlement l'état réel de la base au 9 juillet 2026 (schéma vérifié directement sur la base, version de migration Alembic 0008). Pour chaque table, deux rubriques sont systématiquement renseignées : **ce qu'elle fait** (son contenu et sa structure) et **à quoi elle sert** (qui la lit, qui l'écrit, dans quel but).

---

## 1. Vue d'ensemble

La base de données s'appelle **ooredoo_sales**. C'est une base **PostgreSQL 17.10** hébergée en local (localhost, port 5432). Elle pèse environ un gigaoctet et constitue le cœur de données du moteur agentique Retail d'Ooredoo Tunisie : coaching de vente en temps réel et optimisation des stocks pilotés par des agents d'intelligence artificielle (agents Analyste, Contexte, Décision, Coach et Stratège).

La base utilise deux extensions PostgreSQL : le langage procédural standard (plpgsql) et le générateur d'identifiants uniques universels (uuid-ossp), utilisé pour les clés primaires de plusieurs tables opérationnelles.

Le schéma est versionné par l'outil de migration Alembic : huit migrations numérotées de 0001 à 0008 constituent l'unique source de vérité de la structure. Aucune table n'est créée au démarrage de l'application : tout passe par les migrations.

La base est organisée en **neuf espaces logiques (schémas)** :

1. **sales** — le référentiel commercial : boutiques, vendeurs, produits, transactions, objectifs, scripts de coaching.
2. **inventory** — l'historique et le pilotage des stocks : ventes journalières agrégées, niveaux de stock, prévisions de demande, alertes, recommandations, exécutions d'agents.
3. **supply** — la chaîne d'approvisionnement : fournisseurs, catalogue de sourcing, bons de commande, mouvements de stock, transferts entre boutiques, numéros de série.
4. **market** — le contexte marché : concurrents, prix concurrents, événements nationaux, flux de portabilité des numéros, motifs saisonniers.
5. **customer** — la voix du client : enquêtes de satisfaction et segments de clientèle.
6. **coaching** — la trace détaillée des séances de coaching générées par les agents.
7. **monitoring** — un schéma ne contenant que des vues de supervision (aucune table).
8. **public** — les tables transverses : comptes utilisateurs, sessions, indicateurs de performance journaliers et hebdomadaires, objectifs mensuels, journalisation des agents, validation humaine, boucle de feedback, télémétrie de la recherche documentaire (RAG).

Au total la base compte **51 tables** (dont la table technique de version Alembic) et **16 vues**.

### Volumes principaux

Les tables les plus volumineuses donnent l'échelle du système :

- Transactions de vente historiques : environ **1,93 million de lignes**, couvrant la période du 1er octobre 2024 au 30 juillet 2026.
- Historique de stock (instantanés mensuels) : environ **1,12 million de lignes**, du 22 janvier 2025 au 22 juillet 2026.
- Historique des ventes agrégées par jour, boutique et produit : environ **694 000 lignes**.
- Exécutions d'agents inventaire : environ **127 000 lignes**.
- Indicateurs journaliers par vendeur : environ **114 000 lignes**, du 25 décembre 2025 au 30 juillet 2026.

Le référentiel compte **201 boutiques** (toutes actives), **4 593 produits** dont 2 277 actifs, et **699 vendeurs** en poste.

---

## 2. Schéma « sales » — le référentiel commercial

C'est le schéma pivot : la quasi-totalité des clés étrangères de la base pointent vers ses trois tables de référence (boutiques, produits, vendeurs).

### 2.1 Table des boutiques (sales.boutiques) — 201 lignes

**Ce qu'elle fait.** Chaque ligne représente un point de vente Ooredoo en Tunisie. La clé primaire est l'identifiant de boutique (texte). On y trouve : le nom de la boutique, l'adresse, la ville, la région commerciale (Grand Tunis, Sahel, Sud, Cap Bon, Nord, Centre, Sud-Ouest ou Autre), le gouvernorat (wilaya), la zone commerciale, les coordonnées GPS (latitude et longitude), le nom du manager, le téléphone et l'email de la boutique, le type de boutique et son canal (physique par défaut), la capacité en nombre de conseillers (quatre par défaut), la date d'ouverture, un indicateur de boutique officielle, le rang de chiffre d'affaires dans sa région et un indicateur d'activité.

**À quoi elle sert.** C'est le référentiel maître des points de vente : quatorze tables de la base y sont rattachées par clé étrangère. Elle sert au contrôle d'accès au niveau boutique (chaque utilisateur applicatif est rattaché à une boutique), au filtrage de toutes les pages du tableau de bord, aux comparaisons régionales de l'agent Analyste, et les coordonnées GPS permettent de mesurer la proximité des événements (concerts, matchs) exploitée par l'agent Contexte.

### 2.2 Table des produits (sales.produits) — 4 593 lignes

**Ce qu'elle fait.** Le catalogue produit complet. La clé primaire est le SKU (code produit numérique). Chaque produit porte : son nom, sa catégorie (codes numériques internes plus une catégorie TELECOM de 478 références), sa famille, sa marque, son modèle, ses libellés de gamme et de famille, ses caractéristiques techniques pour les terminaux (stockage en gigaoctets, mémoire vive, couleur, compatibilité 4G et 5G). Côté commercial : prix hors taxes, prix toutes taxes comprises, prix d'achat hors taxes, pourcentage de marge déclaré et marge recalculée. Des indicateurs booléens classent chaque produit : terminal, forfait, carte SIM, recharge, produit sérialisable, produit stockable. Côté approvisionnement : délai de livraison moyen et écart-type (14 jours et 3 jours par défaut), quantité minimale de commande, coût de possession (20 % par défaut), coût de passation de commande (50 dinars par défaut). Enfin le cycle de vie : date de lancement, date de fin de vie, étape du cycle de vie (mature par défaut).

**À quoi elle sert.** Référentiel maître des produits, référencé par treize tables. Le Coach s'appuie sur les marges et les indicateurs de famille pour recommander « le produit à pousser » ; l'agent Décision utilise les paramètres logistiques (délai, quantité minimale, coûts) pour calculer les quantités économiques de commande ; le tableau de bord affiche les libellés ; le garde-fou vérifie que les produits recommandés existent et sont actifs.

### 2.3 Table des vendeurs (sales.agents) — 687 lignes

**Ce qu'elle fait.** Chaque ligne est un conseiller de vente rattaché à une boutique (clé étrangère obligatoire). On y trouve : le nom, le rôle, le téléphone, l'email, le niveau de performance, les dates d'embauche et de départ (699 vendeurs en poste), le niveau de certification, l'ancienneté en mois, la spécialisation, la couleur d'avatar pour l'interface, les quotas mensuels (chiffre d'affaires, activations — 60 par défaut —, postpayé — 10 par défaut) et un score de coaching cumulé.

**À quoi elle sert.** C'est la fiche d'identité de chaque conseiller. Elle permet de personnaliser le coaching (le Coach adapte son ton et ses conseils au profil : ancienneté, spécialisation, niveau), de calculer les écarts individuels aux quotas, d'attribuer chaque transaction à un vendeur, et d'afficher les classements et avatars dans l'interface Angular.

### 2.4 Table des transactions historiques (sales.transactions) — environ 1,93 million de lignes

**Ce qu'elle fait.** Le grand livre des ventes, du 1er octobre 2024 au 30 juillet 2026, à la ligne de ticket. Chaque ligne référence obligatoirement une boutique, un vendeur et un produit, et porte l'horodatage complet, la date seule et l'heure (dénormalisées pour accélérer les agrégations), la quantité, le prix unitaire, les montants hors taxes et toutes taxes comprises, la marge et le mode de paiement.

**À quoi elle sert.** C'est la matière première de toute l'intelligence du système : l'agent Analyste y calcule les séries temporelles (tendance, saisonnalité, anomalies), les outils de prévision y estiment la demande, les tables de KPI en sont dérivées, les vitesses de rotation produit en proviennent. Près de deux ans d'historique permettent de capturer les saisonnalités annuelles (Ramadan, rentrée scolaire, fêtes).

### 2.5 Table des transactions temps réel (sales.transactions_rt) — environ 9 500 lignes

**Ce qu'elle fait.** Alimentée en continu par le simulateur de ventes. Structure proche de la table historique (identifiant unique universel, date de vente, boutique, vendeur, code et libellé produit, montants, quantité) mais volontairement permissive : les colonnes vendeur et produit acceptent les valeurs manquantes pour ne jamais bloquer le flux entrant.

**À quoi elle sert.** C'est elle qui donne le « pouls » de la journée en cours : la position de caisse en temps réel se calcule en faisant l'union de cette table et de l'historique. Le déclenchement des cycles de coaching (détection d'un écart à l'objectif en cours de journée) et les flux WebSocket du tableau de bord en direct reposent dessus.

### 2.6 Table des objectifs (sales.objectifs) — environ 23 500 lignes

**Ce qu'elle fait.** Objectifs journaliers par boutique et éventuellement par vendeur : chiffre d'affaires cible, nombre de transactions cible, panier moyen cible, pour une date donnée.

**À quoi elle sert.** C'est la référence de comparaison du moteur de coaching : l'écart entre le réalisé du jour (transactions temps réel) et l'objectif du jour détermine le niveau d'urgence qui déclenche ou non un cycle d'agents. Sans cette table, pas de notion de « retard sur l'objectif ».

### 2.7 Table des scripts de coaching (sales.coaching_scripts) — 1 348 lignes

**Ce qu'elle fait.** Le corpus de connaissances métier : chaque script décrit une situation de vente, l'action recommandée, le produit à cibler, l'argument de vente, l'impact observé, et des conditions d'applicabilité (plage horaire, jour de semaine, boutique éventuelle).

**À quoi elle sert.** C'est la base de connaissances de la recherche documentaire (RAG) du Coach et du Stratège : quand un vendeur est en difficulté, le système recherche les scripts les plus pertinents pour la situation (indexés dans la base vectorielle Milvus, cette table servant de source de repli) et les injecte dans le conseil généré. Les 200+ scripts d'origine ont été enrichis jusqu'à 1 348.

---

## 3. Schéma « inventory » — pilotage des stocks

Ce schéma contient à la fois les données historiques servant aux prévisions et les objets produits par les agents (alertes, recommandations).

### 3.1 Historique des ventes agrégées (inventory.sales_history) — environ 694 000 lignes

**Ce qu'elle fait.** Une ligne par jour, par boutique et par produit, du 1er octobre 2024 au 30 juillet 2026 : quantité vendue, chiffre d'affaires, prix unitaire, enrichis de caractéristiques prêtes pour l'apprentissage automatique — jour de la semaine, semaine de l'année, mois, année, indicateur de week-end, indicateur et intensité de jour d'événement, nom et type d'événement, saison, indicateur et type de promotion, facteur d'amplification de la demande. Les libellés de boutique, région, produit et catégorie sont dénormalisés pour éviter les jointures.

**À quoi elle sert.** C'est le jeu d'entraînement et d'inférence des modèles de prévision de demande (TimesFM et replis statistiques) et la source des quatre outils d'analyse de séries temporelles de l'agent Analyste (détection d'anomalies, décomposition, prévision multi-horizons, vitesse produit). Sa forme agrégée par jour évite de rebalayer les 1,9 million de transactions à chaque calcul.

### 3.2 Historique des stocks (inventory.stock_history) — environ 1,12 million de lignes

**Ce qu'elle fait.** Instantanés **mensuels** (pris le 22 de chaque mois) du niveau de stock par boutique et par produit, du 22 janvier 2025 au 22 juillet 2026, soit dix-neuf photographies successives, avec un indicateur de rupture de stock. Les six derniers instantanés (février à juillet 2026) ont été reconstitués à partir du dernier état connu en déduisant la demande réelle mensuelle observée dans l'historique des ventes, avec réapprovisionnement simulé quand le stock s'épuisait.

**À quoi elle sert.** Elle permet d'analyser l'évolution des positions de stock dans le temps : fréquence historique des ruptures par produit et par boutique, produits dormants (stock immobile sur plusieurs mois), calibrage des stocks de sécurité. Elle complète la table des niveaux courants qui, elle, ne garde que le présent.

### 3.3 Niveaux de stock courants (inventory.stock_levels) — environ 46 000 lignes

**Ce qu'elle fait.** L'état de stock actuel, unique par couple produit-boutique : quantité en stock, quantité réservée, quantité disponible (calculée par défaut comme la différence), date de dernière réception, date de dernière vente, nombre de jours de stock restants estimé, horodatages de mise à jour.

**À quoi elle sert.** C'est la table la plus consultée du domaine stock : l'outil de statut de stock du serveur MCP la lit pour répondre au Coach et au tableau de bord, l'agent Décision la compare au point de commande pour déclencher les alertes de rupture, et le flux WebSocket inventaire la diffuse en direct.

### 3.4 Référentiel produit inventaire (inventory.product_master) — 4 178 lignes

**Ce qu'elle fait.** Extension du catalogue dédiée au calcul d'approvisionnement : coût unitaire, prix unitaire, délai de livraison moyen et écart-type, quantité minimale de commande, coût de possession, coût de commande, étape du cycle de vie. Sa clé primaire (le SKU) est aussi une clé étrangère vers le catalogue commercial : un enregistrement ne peut exister ici que si le produit existe dans le référentiel.

**À quoi elle sert.** Elle fournit aux formules de gestion de stock (quantité économique de commande, stock de sécurité, point de commande) leurs paramètres d'entrée, sans surcharger le catalogue commercial. C'est la vue « logistique » du produit là où sales.produits est sa vue « commerciale ».

### 3.5 Prévisions de demande (inventory.demand_forecast) — 840 lignes

**Ce qu'elle fait.** Prévisions produites par les modèles de séries temporelles (TimesFM par défaut, avec repli statistique) : une ligne par produit, boutique et date de prévision (unicité garantie sur ce triplet), avec la demande prévue à 24 heures, les bornes basse et haute de l'intervalle de confiance et la version du modèle.

**À quoi elle sert.** C'est le maillon prédictif de la chaîne stock : l'agent Décision confronte la demande prévue au stock disponible pour anticiper les ruptures avant qu'elles n'arrivent, et l'intervalle de confiance module la prudence des quantités recommandées. La version du modèle permet de comparer les performances des différents moteurs de prévision.

### 3.6 Ajustements contextuels (inventory.context_adjustments) — environ 2 800 lignes

**Ce qu'elle fait.** Sorties de l'agent Contexte : pour un produit et une boutique, sur une fenêtre de validité (par défaut sept jours), le pourcentage d'ajustement de demande à appliquer, décomposé par signal (météo, promotion, événement, jour férié), avec le signal dominant, le détail structuré des signaux, une note de confiance, une interprétation en langage naturel et le lien vers l'exécution d'agent d'origine. Unicité sur le triplet produit, boutique, début de validité.

**À quoi elle sert.** Elle corrige les prévisions brutes avec l'intelligence contextuelle : une canicule annoncée, une promotion en cours ou l'Aïd qui approche modifient la demande au-delà de ce que l'historique seul prédit. L'agent Décision applique ces pourcentages avant de calculer les quantités, et l'interprétation en langage naturel est montrée au manager pour justifier la recommandation.

### 3.7 Alertes (inventory.alerts) — 258 lignes

**Ce qu'elle fait.** Alertes émises par les agents (risque de rupture par défaut) avec gravité, message, action recommandée, et un statut suivant un cycle de vie contrôlé : en attente, prise en compte, validée, rejetée, écartée, résolue. Chaque alerte référence la boutique, le produit, et l'exécution d'agent qui l'a générée.

**À quoi elle sert.** C'est le point de départ de la chaîne causale du domaine stock : l'alerte matérialise le problème détecté, alimente le bus d'alertes qui déclenche les cycles événementiels (architecture V6), s'affiche dans le panneau d'alertes du tableau de bord et sert de justification traçable à la recommandation qui en découle.

### 3.8 Recommandations (inventory.recommendations) — 251 lignes

**Ce qu'elle fait.** Décisions proposées par l'agent Décision : type, action, quantité à commander, urgence, confiance, texte explicatif, arbitrages considérés, coûts de commande et de possession estimés. Le statut suit un cycle contrôlé (en attente, approuvée, rejetée, exécutée, annulée) avec le nom du décideur et la date de décision. Chaque recommandation peut pointer vers l'alerte déclenchante et vers l'exécution d'agent d'origine. Clé primaire en identifiant unique universel.

**À quoi elle sert.** C'est la porte de validation humaine du domaine stock : rien ne se commande sans qu'un humain approuve. Une recommandation approuvée devient un bon de commande (le lien est conservé), et le devenir de chaque recommandation (approuvée ou rejetée, et pourquoi) alimente la boucle de feedback qui rend l'agent plus pertinent au fil du temps.

### 3.9 Exécutions d'agents (inventory.agent_runs) — environ 127 000 lignes

**Ce qu'elle fait.** Journal d'exécution des agents du domaine inventaire : identifiant de cycle, nom d'agent, boutique et produit traités, horodatages, durée, statut, résumés d'entrée et de sortie structurés, message d'erreur éventuel, compteurs d'éléments traités, réussis et échoués, identifiant de lot, nombre d'alertes et de recommandations générées.

**À quoi elle sert.** C'est la boîte noire du pipeline inventaire : elle permet d'auditer chaque décision (quelle exécution a produit quelle alerte et quelle recommandation), de mesurer les performances (durées, taux d'échec) et de diagnostiquer les dysfonctionnements depuis le panneau de supervision.

### 3.10 Tables de contexte : événements, promotions, objectifs métier

- **Événements (inventory.events, 24 lignes).** *Ce qu'elle fait :* événements affectant la demande (nom, type, dates, portée, catégories affectées, impacts estimés), éventuellement ciblés sur un produit ou une boutique. *À quoi elle sert :* c'est l'agenda que consulte l'agent Contexte pour anticiper les pics de demande du domaine stock.
- **Promotions (inventory.promotions, 27 lignes).** *Ce qu'elle fait :* campagnes promotionnelles (identifiant fonctionnel unique, nom, dates, produit ou catégorie, remise, type, portée). *À quoi elle sert :* l'agent Contexte y détecte les promotions actives ou imminentes pour majorer la demande prévue ; la vue des promotions actives du tableau de bord la lit directement.
- **Objectifs métier (inventory.business_objectives, 6 lignes).** *Ce qu'elle fait :* la stratégie d'optimisation du moteur de décision (équilibrée par défaut ; un seul objectif actif à la fois, avec libellé, description et priorité). *À quoi elle sert :* elle oriente les arbitrages de l'agent Décision — minimiser les ruptures, minimiser le capital immobilisé, ou équilibrer les deux — sans modifier le code.

---

## 4. Schéma « supply » — chaîne d'approvisionnement

### 4.1 Fournisseurs (supply.suppliers) — 10 lignes

**Ce qu'elle fait.** Référentiel des fournisseurs : identifiant, nom, pays d'origine, type, catégories et marques couvertes (listes structurées), délai de livraison moyen et écart-type, taux de fiabilité (90 % par défaut), commande minimale et multiple de commande, devise (dinar tunisien par défaut), conditions de paiement, contact, score global, indicateur d'activité.

**À quoi elle sert.** Elle permet de choisir à qui commander : le taux de fiabilité et les délais alimentent le calcul du stock de sécurité, et le score global sert à comparer les fournisseurs quand plusieurs proposent le même produit.

### 4.2 Catalogue de sourcing (supply.supplier_products) — 1 370 lignes

**Ce qu'elle fait.** Table d'association « quel fournisseur fournit quel produit », avec pour chaque couple : délai de livraison, quantité minimale de commande, coût unitaire, devise, et un indicateur de fournisseur préféré (règle métier : un seul préféré par produit). Unicité garantie sur le couple fournisseur-produit.

**À quoi elle sert.** C'est elle qui permet à l'agent Décision de transformer une recommandation en bon de commande concret : sans elle, on saurait qu'il faut commander mais pas auprès de qui ni à quel prix. Le fournisseur préféré est retenu automatiquement dans les suggestions de bons de commande.

### 4.3 Bons de commande (supply.purchase_orders) — 4 lignes

**Ce qu'elle fait.** Les commandes d'approvisionnement : produit, fournisseur, boutique de destination, quantités commandée et reçue, prix et montant hors taxes, devise, priorité, dates (commande, livraison prévue, livraison réelle), délai réel constaté, conformité de livraison, référence externe, notes. Le statut suit un cycle strictement contrôlé en neuf états : suggéré, brouillon, soumis, confirmé, expédié, reçu partiellement, reçu, annulé, litige. La colonne source distingue les bons manuels des bons suggérés par l'agent ; ces derniers portent l'urgence, la confiance et le lien vers la recommandation d'origine.

**À quoi elle sert.** C'est le support du tableau Kanban de l'application : chaque bon est une carte que le manager fait avancer de colonne en colonne. Le statut « suggéré » est la matérialisation des propositions automatiques de l'agent Décision, et le passage de « suggéré » à « brouillon » (acceptation) ou « annulé » (refus) est un signal d'apprentissage capté par la boucle de feedback. La réception d'un bon déclenche les mouvements de stock et clôt la boucle : alerte → recommandation → bon suggéré → approbation → réception.

### 4.4 Paramètres de réapprovisionnement (supply.reorder_params) — 945 lignes

**Ce qu'elle fait.** Les paramètres calculés de la politique de stock, par couple produit-boutique (clé primaire composée) : demande moyenne et écart-type journaliers, délai moyen et écart-type, stock de sécurité, point de commande, quantité économique de commande, niveau de service cible (95 % par défaut), jours de stock cible (30 par défaut), date de dernière mise à jour.

**À quoi elle sert.** Ce sont les seuils opérationnels du pilotage : quand le stock disponible passe sous le point de commande, une alerte se justifie ; la quantité économique de commande donne la quantité optimale à proposer. Ces valeurs pré-calculées évitent de refaire les calculs statistiques à chaque cycle d'agent.

### 4.5 Mouvements de stock (supply.stock_movements) — environ 157 000 lignes

**Ce qu'elle fait.** Le journal comptable des stocks. Chaque mouvement porte un type strictement contrôlé parmi douze valeurs (réception de bon de commande, vente, retour client, retour fournisseur, transfert entrant, transfert sortant, ajustement d'inventaire, casse ou perte, gain et perte d'inventaire, réservation, libération de réservation), la quantité, le stock avant et après, une référence croisée vers le document d'origine, le vendeur impliqué, la date et des notes.

**À quoi elle sert.** C'est la traçabilité intégrale : chaque variation de stock est explicable et rattachable à son document d'origine (quel bon de commande, quelle vente, quel transfert). Elle permet les audits d'inventaire, la détection d'écarts (démarque) et la reconstruction de l'état de stock à toute date.

### 4.6 Transferts inter-boutiques (supply.transfers) — 34 lignes

**Ce qu'elle fait.** Déplacements de stock entre deux boutiques (clés étrangères distinctes vers la source et la destination) : produit, quantité, motif, priorité, statut contrôlé en sept états (demandé, approuvé, expédié, reçu, rejeté, annulé, en litige) avec les dates de chaque étape.

**À quoi elle sert.** Elle offre une alternative à la commande fournisseur : quand une boutique est en rupture et qu'une voisine est en surstock, un transfert est plus rapide et moins coûteux qu'un réapprovisionnement. C'est un levier d'équilibrage du réseau que l'agent Décision peut recommander.

### 4.7 Numéros de série (supply.serial_numbers) — 177 lignes

**Ce qu'elle fait.** Suivi unitaire des produits sérialisés. Chaque numéro est unique et typé parmi quatre familles : IMEI (terminaux), ICCID (cartes SIM), eSIM, EAN. Le statut est contrôlé en sept états : en stock, vendu, réservé, défectueux, retourné, volé, en transit. Chaque numéro est relié au produit, à la boutique, au bon de commande de réception, à la vente et au client éventuels.

**À quoi elle sert.** Obligation métier des télécoms : chaque téléphone (IMEI) et chaque carte SIM (ICCID) doit être individuellement traçable, de la réception fournisseur jusqu'au client final, notamment pour la lutte contre le vol et les obligations réglementaires. Elle porte aussi la gestion des retours et du service après-vente.

---

## 5. Schéma « market » — contexte marché

### 5.1 Concurrents (market.competitors) — 3 lignes

**Ce qu'elle fait.** Les opérateurs concurrents du marché tunisien : part de marché, nombre d'abonnés, positionnement, points forts et points faibles structurés, date d'entrée sur le marché.

**À quoi elle sert.** Elle donne au Stratège le paysage concurrentiel : savoir face à qui l'on se bat permet d'adapter les argumentaires de vente (contrer une offre agressive, valoriser un avantage réseau).

### 5.2 Prix concurrents (market.competitor_pricing) — 80 lignes

**Ce qu'elle fait.** Relevés d'offres concurrentes rattachés à un concurrent : catégorie, type de produit, volume de données, minutes de voix, SMS, prix hors taxes et toutes taxes comprises, durée d'engagement, date et source du relevé.

**À quoi elle sert.** Elle permet la comparaison tarifaire en temps réel : quand un client hésite en citant l'offre d'un concurrent, le Coach peut fournir au vendeur la comparaison factuelle et l'argument différenciant.

### 5.3 Événements marché (market.events) — 165 lignes

**Ce qu'elle fait.** Calendrier événementiel national typé en huit familles contrôlées (religieux, scolaire, sportif, commercial, national, concurrentiel, météo, réseau), avec dates, portée, régions et catégories impactées, coefficients d'amplification distincts par famille de produits (terminaux, forfaits, cartes SIM, recharges, accessoires), intensité contrôlée (faible à extrême) et note de stratégie.

**À quoi elle sert.** C'est la mémoire événementielle du système : l'agent Contexte y lit qu'un Ramadan approche (recharges en hausse), qu'une rentrée scolaire arrive (forfaits jeunes) ou qu'un match important a lieu (affluence en boutique), et traduit ces signaux en ajustements de demande et en conseils de vente datés.

### 5.4 Flux de portabilité (market.mnp_flows) — 88 lignes

**Ce qu'elle fait.** Volumes mensuels de portabilité des numéros, en entrée ou en sortie (direction contrôlée), avec opérateurs d'origine et de destination, catégorie de client, raison principale, gouvernorat.

**À quoi elle sert.** C'est le thermomètre de l'attrition : les sorties massives vers un concurrent dans une région signalent un problème (couverture, prix) que le Stratège peut adresser ; les entrées mesurent l'efficacité des offres de conquête.

### 5.5 Motifs saisonniers (market.seasonal_patterns) — 42 lignes

**Ce qu'elle fait.** Facteurs de demande par catégorie et par maille temporelle (mois obligatoire, éventuellement semaine du mois, jour de semaine, plage horaire — toutes bornes contrôlées), avec écart-type, nombre d'années de données et niveau de confiance.

**À quoi elle sert.** Ces coefficients pré-calculés donnent une saisonnalité de repli rapide : quand les modèles lourds de séries temporelles ne sont pas disponibles ou pas pertinents (produit trop récent), le système applique ces facteurs pour corriger une moyenne simple.

---

## 6. Schéma « customer » — voix du client

### 6.1 Enquêtes de satisfaction (customer.nps_csat) — 342 lignes

**Ce qu'elle fait.** Retours clients typés en trois familles contrôlées : score de recommandation (NPS), satisfaction (CSAT), effort client (CES). Chaque retour porte la boutique et le vendeur concernés, la date, le score, le verbatim, la catégorie de motif, le canal et un indicateur de résolution.

**À quoi elle sert.** Elle relie la performance commerciale à la qualité perçue : un vendeur qui vend beaucoup mais dégrade la satisfaction est repérable, les scores agrégés remontent dans les KPI journaliers, et les verbatims donnent au Coach des causes racines qualitatives (attente trop longue, conseil incompris).

### 6.2 Segments de clientèle (customer.segments) — 8 lignes

**Ce qu'elle fait.** Segmentation marketing : pour chaque segment, libellé, description, revenu moyen par utilisateur et écart-type, taux d'attrition de base, durée de vie moyenne en mois, canal préféré, produits préférés (liste structurée), taille estimée, poids de marché.

**À quoi elle sert.** Elle permet des conseils ciblés : proposer le bon produit au bon profil (un forfait data massif à un jeune urbain, une offre simple à un senior), et estimer la valeur à vie d'un client pour arbitrer les efforts de rétention.

---

## 7. Schéma « coaching » — trace des séances de coaching

### 7.1 Événements de coaching (coaching.coaching_events) — 95 lignes

**Ce qu'elle fait.** Chaque ligne est une incitation de coaching générée par le pipeline multi-agents pour un vendeur d'une boutique (clés étrangères vers les deux référentiels). Elle capture : le diagnostic (niveau d'urgence contrôlé, score, écart à l'objectif en pourcentage et en montant, prévision de fin de journée) ; le conseil (texte, produit à pousser, produit à éviter, stratégie, cause racine) ; la traçabilité RAG (usage, nombre de scripts, identifiants, empreinte du contexte) ; le contexte externe (météo avec température et effet, événement proche avec distance) ; la sécurité (statut du garde-fou contrôlé — approuvé, bloqué, réécrit — et règle déclenchée) ; la boucle de retour (note de 1 à 5, efficacité constatée, chiffre d'affaires après coaching).

**À quoi elle sert.** C'est le journal de bord complet du coaching : elle permet de mesurer l'efficacité réelle des conseils (le chiffre d'affaires a-t-il progressé après l'incitation ?), d'auditer les interventions du garde-fou, d'évaluer la contribution du RAG, et chaque événement génère un feedback conseiller (suivi ou ignoré) qui nourrit la boucle d'apprentissage.

---

## 8. Schéma « public » — tables transverses

### 8.1 Authentification et sessions

- **Comptes applicatifs (app_users, 7 lignes).** *Ce qu'elle fait :* les utilisateurs de l'application (3 managers, 4 vendeurs) : identifiant fonctionnel unique, nom d'utilisateur unique, empreinte de mot de passe, nom complet, rôle, boutique de rattachement, initiales et couleur d'interface, lien vers la fiche vendeur, activité, dernière connexion. *À quoi elle sert :* c'est la porte d'entrée de l'application — authentification, contrôle d'accès par rôle (un manager voit sa boutique et valide les décisions, un vendeur voit son coaching) et cloisonnement des données au niveau boutique.
- **Sessions applicatives (app_sessions, 76 lignes).** *Ce qu'elle fait :* sessions par jeton unique, avec utilisateur, expiration, dernière utilisation, adresse IP. *À quoi elle sert :* maintien et renouvellement des connexions du frontend Angular (rafraîchissement de jeton), révocation possible et traçabilité des accès.
- **Sessions d'agents (agent_sessions, 2 lignes).** *Ce qu'elle fait :* sessions de conversation avec les agents, avec état de mémoire et contexte externe structurés. *À quoi elle sert :* conserver le fil d'une conversation de coaching entre deux échanges — le Coach se souvient de ce qui a déjà été dit dans la session.

### 8.2 Indicateurs de performance (KPI)

- **KPI journaliers par vendeur (agent_kpi_daily, environ 114 000 lignes).** *Ce qu'elle fait :* une ligne par vendeur et par jour (unicité garantie), du 25 décembre 2025 au 30 juillet 2026 : chiffre d'affaires réalisé et cible avec écart, transactions, clients uniques, panier moyen, volumes par famille (forfaits, terminaux, SIM, recharges, accessoires, bons électroniques, postpayé avec cible et écart), taux de montée en gamme et de conversion, chiffre d'affaires ventilé par famille, classements (boutique, région, national), réclamations, score de recommandation, score de coaching, niveau d'urgence contrôlé en quatre valeurs. *À quoi elle sert :* c'est le tableau de bord individuel du vendeur et la matière du diagnostic du Coach — l'écart du jour, la composition des ventes et le rang alimentent directement le contenu des conseils.
- **KPI journaliers par boutique (store_kpi_daily, environ 32 000 lignes).** *Ce qu'elle fait :* l'équivalent au niveau boutique (unicité sur boutique et date), avec en plus le cumul mensuel face à l'objectif, le taux de conversion et la fréquentation estimée, la qualité de service stock (ruptures, taux de service), la satisfaction, les classements, la productivité par vendeur. *À quoi elle sert :* c'est la vue manager du tableau de bord et la base des comparaisons entre boutiques de l'agent Analyste ; elle croise ventes, stock et satisfaction en une seule ligne quotidienne.
- **Synthèse hebdomadaire (weekly_kpi_summary, environ 67 000 lignes).** *Ce qu'elle fait :* agrégats par semaine, au niveau vendeur ou boutique (contrôlé), avec réalisé et cible, volumes clés, panier moyen, meilleur produit et meilleure catégorie, jours actifs. *À quoi elle sert :* elle donne la tendance de fond au-delà du bruit quotidien — le benchmark hebdomadaire et les analyses « semaine contre semaine » la lisent directement, sans recalcul.
- **Objectifs mensuels télécom (telco_targets_monthly, environ 6 900 lignes).** *Ce qu'elle fait :* les cibles mensuelles par boutique et éventuellement par vendeur, à trois niveaux contrôlés (vendeur, boutique, région) : chiffre d'affaires mensuel décomposé en quatre semaines, activations totales, postpayées et prépayées, ventes de terminaux, montées en gamme, conversions, renouvellements, cibles de satisfaction, plafond de réclamations, événements du mois, facteur saisonnier et raison d'ajustement. *À quoi elle sert :* c'est la déclinaison opérationnelle de la stratégie commerciale — les objectifs journaliers en dérivent, et le facteur saisonnier documente pourquoi la cible d'un mois de Ramadan diffère d'un mois ordinaire.

### 8.3 Journalisation et supervision des agents

- **Cycles d'orchestration (agent_cycles, environ 1 100 lignes).** *Ce qu'elle fait :* une ligne par cycle complet du pipeline multi-agents : identifiant unique, boutique, déclencheur, diagnostic d'urgence complet, résumé de l'analyste, stratégie, nombre d'actions, cause racine, usage du RAG, météo, durée totale, nœuds exécutés, erreurs, statut. *À quoi elle sert :* c'est la vue « une ligne par décision » de la supervision — elle permet de répondre à « que s'est-il passé à 14 h 30 dans la boutique I63 et pourquoi ? » sans plonger dans les journaux détaillés.
- **Journaux par nœud (agent_logs, environ 12 800 lignes).** *Ce qu'elle fait :* le détail de chaque nœud du graphe LangGraph : cycle, boutique, agent, nœud, statut, états d'entrée et de sortie structurés, durée, erreur, métadonnées. *À quoi elle sert :* c'est l'outil de débogage fin — quand un cycle donne un résultat étrange, on y retrouve exactement ce que chaque étape a reçu et produit, avec les durées pour repérer les goulets d'étranglement.
- **Erreurs d'agents (agent_errors, 21 lignes).** *Ce qu'elle fait :* erreurs typées avec trace complète, contexte structuré, indicateur de résolution. *À quoi elle sert :* file de traitement des incidents, gérable depuis le panneau de supervision (marquer résolu), et matière à statistiques de fiabilité par agent et par nœud.
- **Mémoire d'agent (agent_memory, 917 lignes).** *Ce qu'elle fait :* mémoire épisodique de l'agent Analyste : par agent, boutique et cycle, un type de souvenir et son contenu structuré. *À quoi elle sert :* elle donne de la continuité aux analyses — l'Analyste se souvient de ses conclusions passées sur une boutique (« le créneau 12 h-14 h est structurellement faible ») au lieu de tout redécouvrir à chaque cycle.

### 8.4 Validation humaine et boucle de feedback

- **Revues de validation humaine (hitl_reviews, 9 lignes).** *Ce qu'elle fait :* les décisions d'agents mises en attente d'approbation : boutique, cycle, urgence, écart, score et commentaire de l'agent critique, résumé de stratégie, actions proposées structurées, source, statut (7 en attente, 2 rejetées à ce jour), puis approbateur, note et date de revue. *À quoi elle sert :* c'est le panneau de validation du manager dans l'interface Angular — les stratégies sensibles n'atteignent les vendeurs qu'après son feu vert, et ses notes de rejet sont réinjectées dans les prompts des agents.
- **Feedback humain sur les agents (agent_feedback, 101 lignes).** *Ce qu'elle fait :* la boucle d'apprentissage continue (migration 0008). Chaque retour rattache une boutique, une source (incitation, validation humaine ou bon de commande), une référence, éventuellement un produit et un type d'action, à une décision humaine strictement contrôlée en quatre valeurs : suivi, ignoré, approuvé, rejeté, avec raison et charge utile structurée. La table contient actuellement 95 retours de conseillers sur les incitations de coaching (62 suivies, 33 ignorées avec raisons), 2 rejets de stratégies par le manager et 4 devenirs de bons de commande suggérés (3 acceptés, 1 annulé). *À quoi elle sert :* le service de feedback agrège ces signaux en un contexte d'apprentissage (« les conseillers ont suivi 71 % des incitations », « raisons de rejet récentes : … ») injecté dans les prompts des agents Décision et Stratège — les agents s'adaptent ainsi aux arbitrages humains passés sans réentraînement.

### 8.5 Coaching et qualité du RAG

- **Interactions du coach (coach_interactions, 253 lignes).** *Ce qu'elle fait :* l'historique des conversations coach-vendeur : conseiller, boutique, message, réponse, écart courant, urgence, usage du RAG et nombre de scripts, type de conseil, confiance. *À quoi elle sert :* elle donne l'historique conversationnel affiché dans le chat, permet d'analyser les questions les plus fréquentes des vendeurs et de mesurer la confiance moyenne des réponses du Coach.
- **Requêtes RAG (rag_queries, 150 lignes), feedback RAG (rag_feedback, 150 lignes), métriques de feedback RAG (rag_feedback_metrics, 150 lignes).** *Ce qu'elles font :* trois tables de télémétrie de la recherche documentaire : texte de la requête, nombre de résultats, meilleure catégorie et meilleur score, action retenue, utilité constatée, contexte. *À quoi elles servent :* elles mesurent si le RAG aide réellement — les requêtes sans bon résultat révèlent les trous du corpus de scripts à combler, et les scores d'utilité permettent d'ajuster les seuils de similarité de la recherche vectorielle.

### 8.6 Table technique

- **Version de migration (alembic_version, 1 ligne).** *Ce qu'elle fait :* stocke le numéro de la dernière migration appliquée (0008). *À quoi elle sert :* Alembic la lit pour savoir quelles migrations restent à appliquer ; elle garantit que la structure de la base et le code restent synchronisés.

---

## 9. Relations et intégrité référentielle

La base compte **45 clés étrangères**. Le motif dominant est en étoile autour du schéma sales :

- **La table des boutiques** est référencée par quatorze tables : les vendeurs, les objectifs, les deux tables de transactions, les historiques de ventes et de stocks, les niveaux de stock, les ajustements contextuels, les prévisions, les alertes, les recommandations, les événements d'inventaire, les enquêtes clients, les événements de coaching, les bons de commande, les paramètres de réapprovisionnement, les mouvements de stock et les transferts (ces derniers deux fois : source et destination).
- **La table des produits** est référencée par treize tables couvrant les ventes, tout le domaine inventaire et toute la chaîne d'approvisionnement.
- **La table des vendeurs** est référencée par les transactions, les transactions temps réel, les enquêtes clients et les événements de coaching.

Trois chaînes causales structurent le fonctionnement agentique :

1. **Chaîne stock** : une exécution d'agent produit des alertes ; une alerte peut engendrer une recommandation (lien avec mise à néant en cas de suppression de l'alerte) ; une recommandation approuvée peut devenir un bon de commande (lien avec mise à néant également) ; le bon de commande reçu génère des mouvements de stock et des numéros de série.
2. **Chaîne coaching** : un cycle d'orchestration produit des journaux de nœuds, des événements de coaching, des requêtes RAG et éventuellement une revue de validation humaine ; le feedback du vendeur remonte dans l'événement de coaching.
3. **Chaîne feedback** : les décisions humaines (validation, rejet, suivi, ignorance) sont capturées dans la table de feedback des agents et réinjectées dans les instructions des agents décisionnaires.

L'intégrité par valeurs est assurée par une trentaine de contraintes de vérification qui verrouillent tous les vocabulaires métier : statuts des bons de commande, des transferts, des alertes, des recommandations, des numéros de série, types de mouvements de stock, types d'événements marché, niveaux d'urgence, décisions de feedback, statuts du garde-fou, types d'enquêtes, bornes temporelles des motifs saisonniers, notes de feedback.

Deux tables font exception voulue : la table des transactions temps réel garde des colonnes optionnelles pour ne jamais bloquer le flux du simulateur, et la table de version Alembic reste purement technique.

---

## 10. Vues

Seize vues facilitent la consultation sans dupliquer les données :

- **Dans le schéma monitoring** (quatre vues) : pouls temps réel des boutiques, journaux de cycles, interactions de coaching, écarts par catégorie en direct. Ce schéma ne contient aucune table : il sert de façade de supervision pour le tableau de bord.
- **Dans le schéma sales** (huit vues) : ventes par vendeur, performance vendeur, chiffre d'affaires par boutique, meilleurs produits, historique unifié des transactions, stock enrichi.
- **Dans le schéma inventory** (cinq vues) : produits, boutiques, niveaux de stock, stock enrichi, promotions actives — des façades de compatibilité qui permettent au code inventaire de lire les référentiels sales sous ses propres noms.
- **Dans le schéma public** (une vue) : une vue de compatibilité sur les produits.

---

## 11. Performances et indexation

La base compte environ **170 index**. Au-delà des index automatiques des clés primaires et des contraintes d'unicité, des index ciblés accélèrent les accès les plus fréquents : recherches par boutique et par date sur les transactions et les KPI, recherches par produit et boutique sur les niveaux de stock et les prévisions, filtrage par statut sur les alertes, recommandations et bons de commande, et recherche par cycle sur les tables de journalisation. Les colonnes dénormalisées (date seule, heure, jour de semaine, mois) sur les grandes tables historiques évitent des calculs coûteux à la volée dans les agrégations des agents.

---

## 12. Points d'attention connus

- L'historique de stock est constitué d'instantanés **mensuels** (le 22 de chaque mois), pas de photographies journalières : les analyses de rupture infra-mensuelles doivent s'appuyer sur les mouvements de stock et les niveaux courants. Les six instantanés de février à juillet 2026 ont été reconstitués à partir de la demande réelle (voir section 3.2) ; les treize précédents proviennent du générateur de données synthétiques.
- Les catégories de produits sont majoritairement des codes numériques internes (50, 88, 70…) hérités du système source ; seule la catégorie TELECOM est libellée. Les libellés de famille sont vides.
- La colonne région des boutiques contient une valeur fourre-tout « Autre » pour près de la moitié des boutiques, et deux boutiques n'ont pas de région renseignée.
- L'instantané de stock de janvier 2026 contient environ 18 800 doublons de couples produit-boutique (héritage du générateur) ; les instantanés reconstitués de 2026 sont dédoublonnés (46 213 couples chacun).
- Les bases annexes (traçabilité Langfuse, base vectorielle Milvus, cache Redis) sont extérieures à cette base PostgreSQL et ne sont pas décrites ici.
