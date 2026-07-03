# Architecture des Agents — Moteur Agentique Retail Ooredoo Tunisia

## 1. Vue d'ensemble

Le système répond à deux missions complémentaires, unifiées dans un seul cycle de raisonnement :

1. **Coaching de vente agentique** — combler le fossé entre prévisions statiques et exécution terrain en temps réel : analyser le flux de données, détecter les écarts, générer des incitations actionnables pour les conseillers selon le contexte environnemental et opérationnel.
2. **Inventory Advisor agentique** — aider à la décision de gestion de stock en intégrant stock, prévisions de ventes et signaux contextuels (événements, tendances, objectifs business), pour produire des recommandations explicables et actionnables — pas un simple calcul de réapprovisionnement statique.

Ces deux missions sont orchestrées par un seul point d'entrée : le **SupervisorAgent**, qui exécute un cycle complet regroupant huit agents spécialisés autour d'un état partagé unique, le **RetailState**.

---

## 2. Architecture globale — SupervisorAgent et RetailState

### 2.1 Le RetailState — état partagé unique

Tous les agents lisent et écrivent dans un seul objet d'état partagé pour un cycle donné, plutôt que de se passer des messages point à point. Chaque agent n'écrit que dans ses propres champs ; si un agent échoue, ses champs restent simplement absents et les agents suivants traitent cette absence comme "aucune donnée" sans bloquer le cycle.

Le RetailState regroupe les catégories de données suivantes :

- **Identification du cycle** : identifiant de cycle, identifiant du magasin, identifiant du conseiller (optionnel), type de déclenchement (cycle planifié, message conseiller, événement stock, événement vente, action manager).
- **Entrées brutes** : données du point de vente en temps réel, données de stock, données de contexte (météo/événements/promotions), message éventuel du conseiller.
- **Sorties de la branche Sales** (Agent Analyste) : écart en pourcentage et en montant vis-à-vis de l'objectif journalier, prévision de fin de journée, niveau et score d'urgence, résumé généré par le LLM, catégories sous-performantes, tendance intrajournalière.
- **Sorties de la branche Stratégie** (Agent Stratège) : synthèse stratégique, liste d'actions priorisées avec produit cible, produits à mettre en avant, analyse de cause racine, message pour le manager, alertes temps réel, indicateur d'utilisation du RAG et nombre de scripts récupérés, score et statut d'auto-critique.
- **Sorties de la branche Inventory** : décisions par SKU, alertes de stock critique, rapport de stock, instantané d'inventaire (nombre de ruptures, nombre de SKU critiques, chiffre d'affaires à risque).
- **Sorties de la branche Connaissance (RAG)** : contexte documentaire récupéré, scripts de vente similaires, offres recommandées.
- **Sorties de la branche Contexte (Sentinel)** : rapport de contexte, contexte externe (météo, événements, carte de chaleur), signaux externes, impact estimé.
- **Sorties de l'Agent Coach** (fusion cross-domaine) : recommandation finale de produit classée, produits scorés, message final pour le conseiller, message final pour le manager.
- **Sorties de l'Agent Guardrail** : statut de validation, liste des problèmes détectés, retour d'instruction pour réécriture, score de confiance du guardrail, indicateur de validation humaine requise, message de repli sécurisé.
- **HITL (validation humaine)** : indicateur de validation requise, identifiant de la revue, statut d'approbation, identifiant de l'approbateur.
- **Observabilité** : liste des agents effectivement invoqués ce cycle, latence totale, métriques détaillées par étape, liste des erreurs rencontrées.

### 2.2 Topologie du graphe SupervisorAgent

Le cycle suit la séquence suivante :

1. **Initialisation de l'état** — création de l'identifiant de cycle et des structures de suivi (agents invoqués, erreurs, métriques).
2. **Dispatch parallèle en quatre branches** — les quatre branches suivantes démarrent simultanément à partir de l'état initial :
   - **Branche Sales** — délègue à l'orchestrateur de cycle existant (Agent Analyste puis Agent Stratège, exécutés séquentiellement l'un après l'autre à l'intérieur de cette branche).
   - **Branche Connaissance** — recherche de scripts de vente pertinents dans la base vectorielle.
   - **Branche Contexte** — récupération des signaux externes (météo, jours fériés, événements, promotions).
   - **Branche Inventory** — exécution du pipeline d'analyse de stock (Agent Analyse → Agent Contexte → Agent Décision) sur l'ensemble des SKU du magasin.
3. **Fusion des sorties** — une fois les quatre branches terminées, leurs résultats sont agrégés dans l'état partagé et un résumé est journalisé (écart, urgence, nombre de décisions stock, nombre de scripts RAG, nombre d'erreurs).
4. **Agent Coach (fusion cross-domaine)** — combine les sorties Sales et Inventory pour produire une recommandation de produit unique, justifiée et scorée.
5. **Agent Guardrail** — évalue la recommandation du Coach contre sept règles métier et décide de son sort.
6. **Routage conditionnel selon le verdict du Guardrail** :
   - Approuvé → notification au frontend.
   - Réécriture → retour à l'Agent Coach avec le motif précis (une seule itération maximum).
   - Escalade → passage en validation humaine (HITL) puis notification au frontend.
   - Bloqué → remplacement par un message de repli sécurisé, puis notification au frontend.
7. **Sauvegarde en mémoire** — persistance du résultat du cycle pour l'historique et l'apprentissage futur de l'Agent Analyste.

---

## 3. Domaine Sales — Coaching de vente agentique

### 3.1 Agent Analyste (ReAct)

**Rôle.** Produire une analyse temps réel complète de la performance du magasin : position actuelle, prévisions, anomalies, tendances et état du stock — en choisissant dynamiquement quels outils interroger plutôt que de suivre une séquence figée.

**Déclenchement.** Premier maillon de la branche Sales, à chaque cycle (déclenchement planifié, message conseiller, ou appel manuel).

**Données en entrée.**
- Identifiant de cycle et de magasin, objectif journalier de chiffre d'affaires, heure courante.
- Données du point de vente en direct (chiffre d'affaires du jour, nombre de transactions, panier moyen).
- Historique récent des transactions du magasin.
- Prévision de fin de journée précédemment calculée (moteur TimesFM/Prophet).
- Historique de feedback des cycles précédents (mémoire de l'agent).

**Traitement — cycle Observation → Raisonnement → Action.** L'agent dispose de onze outils qu'il appelle dans l'ordre qu'il juge pertinent, avec un nombre d'itérations plafonné :
- Récupération du point de vente en direct (chiffre d'affaires, transactions, panier moyen) — premier appel obligatoire.
- Calcul de la prévision de fin de journée par ensemble de méthodes (linéaire, saisonnière, vélocité, TimesFM).
- Calcul de l'écart temps réel vis-à-vis de l'objectif, avec score d'urgence et faisabilité.
- Analyse de la tendance intrajournalière (vélocité, accélération, z-score de l'heure courante, heures de pointe).
- Comparaison historique sur le même jour de la semaine, sur quatre semaines glissantes.
- Détection d'anomalies de vente par z-score sur vingt-huit jours de données horaires.
- Décomposition de série temporelle façon STL : tendance sur sept jours, saisonnalité par jour de semaine, autocorrélation à décalage sept jours.
- Prévision multi-horizon : heure suivante, trois heures suivantes, fin de journée, même jour la semaine prochaine — avec intervalle de confiance à 80 %.
- Récupération des alertes de stock du magasin.
- Récupération du contexte saisonnier (uplift lié aux événements du marché).
- Analyse de la vélocité produit par SKU (unités vendues par jour) croisée avec les niveaux de stock, pour estimer les jours avant rupture.

**Données en sortie.**
- Écart à l'objectif en pourcentage et en montant.
- Niveau et score d'urgence.
- Prévision de fin de journée avec intervalle de confiance.
- Résumé d'analyse en langage naturel généré par le LLM.
- Signal de tendance, facteur saisonnier, autocorrélation à sept jours, indicateur de journée atypique.
- Instantané de l'inventaire critique (nombre de produits urgents).
- Requête de recherche pour la branche Connaissance (construite en fin de cycle, à partir du diagnostic).

**Modèle utilisé.** Modèle de langage local (Ollama), choisi après mesure comparative pour ce rôle spécifique : une boucle ReAct enchaîne jusqu'à quatre appels séquentiels au modèle, ce qui favorise un modèle petit et rapide plutôt qu'un grand modèle à latence réseau élevée.

**Repli en cas d'échec.** Un résumé de secours est généré à partir des seules données chiffrées (écart, urgence, chiffre d'affaires, prévision) sans appel au LLM.

### 3.2 Agent Stratège

**Rôle.** Transformer le diagnostic de l'Analyste en plan d'action concret : actions priorisées, produits à mettre en avant, message pour le manager, en s'appuyant sur le contexte externe et sur des scripts de vente similaires déjà validés.

**Déclenchement.** Deuxième maillon de la branche Sales, immédiatement après l'Agent Analyste.

**Données en entrée.** L'intégralité de la sortie de l'Agent Analyste (écart, urgence, résumé, prévisions, données du point de vente).

**Traitement — six nœuds.**
1. Récupération du contexte complet du magasin (météo, événements commerciaux concurrents, bilan de portabilité numérique).
2. Recherche de scripts de vente similaires dans la base vectorielle, à partir de la requête construite par l'Analyste.
3. Analyse du contexte — synthèse des facteurs (météo, alertes, scripts trouvés) en une cause racine.
4. Appel au modèle de langage pour générer le plan d'action structuré.
5. Auto-critique — un score de qualité est calculé sur le plan généré ; si les actions manquent de produit cible ou d'argumentaire, un signal HITL peut être levé.
6. (Cas d'échec du parsing JSON) — extraction par expression régulière en repli si la réponse du modèle est tronquée.

**Données en sortie.**
- Synthèse stratégique textuelle.
- Liste d'actions priorisées, chacune avec un produit cible et un argumentaire de vente.
- Cause racine de l'écart identifié.
- Produits à mettre en avant.
- Message destiné au manager.
- Carte de chaleur contextuelle (trafic, stock, événements, réseau, météo par tranche horaire).
- Alertes temps réel.
- Indicateur d'utilisation du RAG et nombre de scripts exploités.
- Score et statut de l'auto-critique.

**Modèle utilisé.** Modèle de langage via passerelle OpenRouter (modèle à contexte large), adapté à un appel unique et complexe plutôt qu'à une boucle d'itérations.

**Repli en cas d'échec.** Plan vide avec cause racine réduite à l'écart chiffré ; le cycle continue sans plan d'action détaillé.

### 3.3 Agent Coach — fusion cross-domaine

**Rôle.** C'est le point de convergence des deux missions : il combine le contexte commercial (objectif, écart, conseiller) avec les produits disponibles et l'historique du conseiller pour produire une recommandation de produit unique, chiffrée et justifiée — le cœur de la différenciation "recommandations explicables" du projet.

**Déclenchement.** Après la fusion des quatre branches parallèles, avant le passage au Guardrail.

**Données en entrée.**
- Contexte de vente du magasin et du conseiller concerné.
- Liste des produits éligibles à la recommandation, filtrée par montant de l'écart à combler.
- Historique du conseiller (produits déjà recommandés, taux de succès).
- Alertes de stock critique remontées par la branche Inventory (pour exclure les produits en rupture de la recommandation).

**Traitement — scoring pondéré à six critères.** Chaque produit candidat reçoit un score final calculé comme somme pondérée de :
- Alignement avec l'écart de vente (poids le plus important).
- Santé du stock du produit.
- Score de marge.
- Priorité de promotion en cours.
- Adéquation avec le profil du conseiller.
- Adéquation avec le profil client.

Les produits sont classés par score décroissant ; le meilleur devient la recommandation principale, les trois premiers sont conservés comme "produits scorés" pour affichage.

**Données en sortie.**
- Recommandation du coach : priorité, produit à pousser, produit à éviter (s'il y a un risque de rupture), message pour le conseiller, justification métier, score de confiance.
- Liste des produits scorés avec, pour chacun, le détail des six composantes du score et une phrase de justification.

**Modèle utilisé.** Aucun appel LLM à cette étape — le scoring est un calcul déterministe et explicable par construction (chaque composante du score est traçable).

**Repli en cas d'échec.** Recommandation générique de priorité moyenne invitant à consulter le catalogue disponible.

---

## 4. Domaine Inventory — Inventory Advisor agentique

Ce domaine s'exécute par lot sur l'ensemble des SKU suivis par le magasin (typiquement une centaine à quelques centaines de références). Pour chaque SKU, trois agents s'enchaînent : Analyse, Contexte (en parallèle de l'Analyse), puis Décision.

### 4.1 Agent Analyse

**Rôle.** Établir le diagnostic factuel de la situation de stock d'un SKU donné : niveau de risque, métriques de réapprovisionnement, prévision de demande de base.

**Données en entrée.**
- Identifiant du SKU et du magasin, objectif business actif (coût, niveau de service, équilibré).
- Niveau de stock courant (pré-chargé en un seul lot pour l'ensemble des SKU du cycle, plutôt qu'une requête par SKU).
- Informations produit (coût unitaire, quantité minimale de commande, délai de livraison moyen et écart-type, étape de cycle de vie).

**Traitement — trois étapes.**
1. Récupération des données (stock courant, informations produit, historique de ventes).
2. Calcul des métriques de réapprovisionnement : jours de stock restants, point de commande, quantité économique de commande, stock de sécurité, coût de réapprovisionnement total, niveau de service effectif.
3. Raisonnement — validation ou ajustement du niveau de risque calculé par les règles, avec repli sur un calcul purement déterministe si le modèle de langage n'est pas sollicité pour ce cycle (mode rapide) ou indisponible.

**Données en sortie.**
- Niveau de risque (faible, moyen, élevé, critique) avec justification.
- Ensemble des métriques de réapprovisionnement.
- Signalement d'un éventuel conflit avec l'objectif business actif.
- Indicateur de surstock.

### 4.2 Agent Contexte

**Rôle.** Enrichir le diagnostic de stock avec les signaux externes susceptibles de faire varier la demande à court terme.

**Données en entrée.** Identifiant du SKU et du magasin.

**Traitement — cinq signaux récupérés en parallèle, avec mise en cache d'une heure pour les signaux communs à tout un magasin (météo, jours fériés, événements) afin d'éviter des centaines d'appels identiques par cycle :**
- Catégorie du produit.
- Motifs historiques de demande pour cette catégorie dans ce magasin.
- Promotions actives ou démarrant sous sept jours, spécifiques au SKU.
- Météo locale et son effet estimé sur la fréquentation.
- Jours fériés à venir.
- Événements de marché à venir pertinents pour la catégorie.

**Données en sortie.**
- Pourcentage d'ajustement de la demande (hausse ou baisse).
- Signal dominant identifié (promotion, météo, événement, historique, aucun).
- Niveau de confiance dans ce signal.
- Interprétation textuelle de l'ensemble des signaux.

### 4.3 Agent Décision

**Rôle.** C'est la couche de jugement final — transformer le diagnostic (Analyse) et le signal de demande (Contexte) en une décision opérationnelle unique, explicable et actionnable, conforme à l'exigence du projet de dépasser le simple calcul de réapprovisionnement.

**Données en entrée.**
- Rapport d'analyse du stock (niveau de risque, jours de stock, point de commande, quantité formule, coût, contraintes de quantité minimale).
- Rapport de contexte (ajustement de demande, signal dominant, confiance).
- Métriques ajustées (toutes les formules de stock recalculées avec l'ajustement de demande appliqué).
- Objectif business actif.

**Traitement.** Le modèle de langage (ou, en mode rapide, un ensemble de règles déterministes strictement équivalentes) détermine :
- L'action à prendre — commander, expédier en urgence, surveiller, ou ne rien faire.
- Si la quantité calculée par la formule doit être suivie telle quelle ou ajustée.
- Si une escalade vers un humain est nécessaire (coût de commande dépassant un seuil, produit en fin de vie avec risque critique, conflit d'objectif avec coût significatif, ou incertitude réelle du modèle).
- Comment communiquer la décision — un texte de trois à cinq phrases rédigé comme le ferait un acheteur senior s'adressant à un responsable de magasin : l'action et le chiffre clé en premier, puis la contrainte physique ou le coût, puis l'influence du signal de demande si pertinente, puis l'éventuelle alerte à signaler.

Une étape d'auto-critique revoit la décision ; en cas d'échec de cette critique, une révision est tentée auprès du modèle de langage avec le motif du refus, avec repli final sur les règles déterministes si la révision échoue aussi.

**Données en sortie, par SKU.**
- Action retenue, quantité de commande, urgence (immédiate, cette semaine, ce mois-ci, aucune).
- Justification de la décision et de la quantité choisie.
- Niveau de confiance de la décision.
- Compromis assumé par cette décision (le coût ou le risque que le magasin accepte).
- Indicateur et motif d'escalade vers un humain.
- Texte de recommandation en langage naturel destiné à l'opérateur.

**Modèle utilisé.** Modèle de langage via passerelle OpenRouter en mode complet ; ensemble de règles déterministes strictement équivalentes en mode rapide (utilisé lorsque la latence du cycle complet est contrainte, par exemple pour alimenter le tableau de bord en direct).

---

## 5. Agent Guardrail — garde-fou transversal

**Rôle.** Empêcher qu'une recommandation générée automatiquement (par l'Agent Coach) n'atteigne le conseiller ou le manager si elle viole une règle métier — c'est ce qui différencie le système d'un simple assistant conversationnel : chaque sortie est validée avant diffusion.

**Déclenchement.** Après l'Agent Coach, avant la notification au frontend ; également invoqué en ligne à chaque réponse du chat conseiller.

**Données en entrée.** La recommandation du Coach (produit poussé, message, confiance), l'état du stock du produit concerné, l'indicateur d'utilisation du RAG, le coût de la commande d'inventaire associée le cas échéant.

**Traitement — sept règles évaluées indépendamment, chacune associée à une sévérité :**
- **G1 (Stock)** — ne jamais recommander un produit à stock nul → blocage.
- **G2 (Rupture imminente)** — éviter de pousser un produit dont le stock s'épuise dans un délai critique, sauf déstockage volontaire → réécriture.
- **G3 (Source RAG)** — un argumentaire commercial doit s'appuyer sur une source fiable quand l'écart est important et qu'aucun script RAG n'a été trouvé → réécriture.
- **G4 (Règles business)** — détection de motifs de remise ou d'offre non autorisée dans le texte généré → blocage.
- **G5 (Éligibilité réseau)** — ne pas recommander une offre 5G/Fibre sans confirmation d'éligibilité du client → réécriture.
- **G6 (Confiance)** — si le score de confiance de la recommandation passe sous le seuil configuré → escalade vers validation humaine.
- **G7 (Budget)** — une commande de stock dont le coût dépasse le plafond configuré nécessite une validation manager → escalade.

Le statut final retenu est le plus sévère parmi tous les problèmes détectés (blocage > escalade > réécriture > approuvé).

**Données en sortie.**
- Statut : approuvé, réécriture, escalade, ou bloqué.
- Liste des problèmes détectés avec la règle concernée et le message explicatif.
- Instruction de réécriture destinée à l'Agent Coach (si applicable).
- Message de repli sécurisé (si bloqué).

**Modèle utilisé.** Aucun — logique déterministe, seuils configurables (confiance minimale, plafond budgétaire, délai de rupture critique).

---

## 6. HITL — Validation humaine (Human-in-the-Loop)

**Rôle.** Fournir un point de contrôle manager pour les recommandations escaladées par le Guardrail (confiance faible ou budget élevé), avec une trace complète et un délai de rétention.

**Données en entrée.** La recommandation escaladée, le motif de l'escalade, l'identifiant du magasin et du conseiller concernés.

**Traitement.** La revue est stockée avec un statut (en attente, approuvée, rejetée) ; un manager consulte la liste des revues en attente et statue.

**Données en sortie.** Statut final de la revue, identité de l'approbateur, horodatage de la décision, statistiques agrégées (nombre en attente, approuvées, rejetées) exposées au tableau de bord monitoring.

---

## 7. Sources de données et infrastructure

- **Base de données relationnelle (PostgreSQL)** — deux schémas principaux : ventes (transactions historiques et temps réel, catalogue produits, objectifs, événements de marché, promotions) et inventaire (niveaux de stock, catalogue produits enrichi, alertes, décisions, recommandations, exécutions d'agents).
- **Base vectorielle (Milvus)** — scripts de vente et argumentaires commerciaux indexés pour la recherche par similarité (branche Connaissance).
- **File de messages (Redis)** — diffusion des alertes critiques (rupture imminente, décision urgente) de manière asynchrone, sans bloquer le cycle principal.
- **Modèles de langage** — passerelle OpenRouter pour le Stratège, le Coach implicitement via le scoring, et l'Agent Décision inventaire (modèle à grand contexte, appel unique) ; modèle local (Ollama) pour l'Agent Analyste (boucle ReAct multi-appels, favorise la rapidité).
- **Moteur de prévision** — TimesFM et Prophet pour la prévision de fin de journée et les projections multi-horizon.
- **Signaux externes** — API météo, calendrier des jours fériés tunisiens, événements de marché saisis en base.
- **Observabilité** — traçage complet de chaque cycle (Langfuse), journalisation structurée par agent et par cycle, tableau de bord de monitoring exposant guardrail, HITL, latences et taux d'erreur par agent.

---

## 8. Flux de données bout-en-bout — un cycle complet

1. Un déclencheur démarre le cycle (planification périodique, connexion d'un conseiller au tableau de bord, ou appel manuel).
2. Le SupervisorAgent initialise l'état partagé et lance quatre branches en parallèle.
3. La branche Sales exécute l'Analyste puis le Stratège, séquentiellement, et écrit son diagnostic et son plan d'action dans l'état partagé.
4. En parallèle, la branche Connaissance interroge la base vectorielle avec la requête construite par l'Analyste ; la branche Contexte récupère les signaux externes du magasin ; la branche Inventory exécute le pipeline Analyse → Contexte → Décision sur l'ensemble des SKU du magasin.
5. Une fois les quatre branches terminées, leurs sorties sont fusionnées dans l'état partagé.
6. L'Agent Coach combine le contexte de vente et les décisions d'inventaire pour produire une recommandation de produit unique et scorée.
7. L'Agent Guardrail évalue cette recommandation contre les sept règles métier et détermine son sort.
8. Selon le verdict, la recommandation est soit diffusée telle quelle au tableau de bord et au conseiller, soit renvoyée au Coach pour réécriture, soit mise en attente de validation manager (HITL), soit remplacée par un message de repli sécurisé.
9. Le résultat complet du cycle (diagnostic, plan, décisions de stock, recommandation, statut guardrail) est sauvegardé pour alimenter la mémoire de l'Agent Analyste lors du cycle suivant, et diffusé en temps réel au tableau de bord via connexion WebSocket.
