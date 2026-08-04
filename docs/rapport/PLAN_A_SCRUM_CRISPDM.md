# Version A — Plan du rapport : **Scrum piloté, CRISP-DM encastré**

> **Modèle de référence.** Ce plan reprend l'ossature du rapport *taptab.social* (A. Mayoufi, TEK-UP) :
> un chapitre de cadrage, un chapitre besoins/planification/architecture, puis des chapitres
> d'implémentation organisés **par incrément livré**, chacun se refermant sur une revue chiffrée.
>
> **Ce qu'on en change.** Chez Mayoufi le pilotage est Kanban et CRISP-DM n'intervient que pour
> le *fine-tuning*. Ici, Scrum pilote la narration (12 sprints de 2 semaines, déjà planifiés au ch. 2
> existant), et **CRISP-DM est instancié à l'intérieur des sprints**, à chaque fois qu'un composant
> de données ou de modèle est construit. Chaque encadré « Cycle CRISP-DM » est explicitement rattaché
> à ses phases.
>
> **Argument de soutenance.** « Le produit est livré par incréments Scrum ; chaque incrément à
> composante données parcourt un mini-cycle CRISP-DM complet. Les deux méthodes ne sont pas
> juxtaposées, elles sont emboîtées : le sprint est le conteneur, le cycle CRISP-DM est le contenu. »

---

## 0. Pages liminaires

| Élément | Contenu | Pages |
|---|---|---|
| Page de garde | République Tunisienne / TEK-UP / titre / encadrants / année 2025–2026 | 1 |
| Autorisations de dépôt | Encadrant professionnel Ooredoo + encadrant académique | 1 |
| Dédicaces | — | 1 |
| Remerciements | Encadrants, équipe Retail Ooredoo, jury, enseignants | 1 |
| Résumé / Abstract / ملخص | 150 mots chacun | 1 |
| Table des matières · figures · tableaux | générées | 4–5 |
| Liste des acronymes | IA, LLM, RAG, SMA, MCP, MAPE, WAPE, EOQ, ROP, SS, HITL, CRISP-DM, SSE, WS, RBAC, ETL, KPI, API, SLA | 1 |
| **Introduction générale** | contexte Retail télécom → problématique → objectifs → **annonce du plan par incréments** | 2 |

---

## Chapitre 1 — Cadre général et état de l'art

*Rôle : poser le décor et **justifier la méthodologie hybride**. C'est la section 1.7 qui fait la
différence avec un rapport agile ordinaire.*

### Introduction

### 1.1 Présentation de l'organisme d'accueil
- Ooredoo Tunisie : activités, clientèle, valeurs, organisation → **fig. logo, organigramme**
- Direction Retail concernée par le projet

### 1.2 Contexte général du projet
- Cadre du projet et périmètre (réseau de boutiques, conseillers, managers)
- Problématique en trois volets : décision de vente à l'aveugle, pilotage du stock réactif, cloisonnement ventes/stock

### 1.3 Étude de l'existant
- **1.3.1 Existant interne** : outils de reporting, gestion de stock, absence d'aide à la décision temps réel
- **1.3.2 Solutions du marché** : suites de *retail analytics*, assistants commerciaux génériques, moteurs de réappro
- **1.3.3 Synthèse comparative** → **tableau : solution → couverture ventes / couverture stock / temps réel / explicabilité / adaptation au contexte tunisien**

### 1.4 Critique de l'existant
Cinq limites : absence de couplage ventes↔stock, prévision sans contexte local (fêtes, festivals, Ramadan),
recommandations non traçables, aucun point de contrôle humain, latence incompatible avec le comptoir.

### 1.5 Solution proposée
- Vision produit en une phrase
- Les cinq briques : moteur de prévision, moteur de décision de stock, agents conversationnels,
  base de connaissances métier, garde-fous + validation humaine → **fig. vue produit**

### 1.6 État de l'art
*(section absente du plan pur CRISP-DM — c'est un apport de la version Scrum)*
- **1.6.1 IA agentique** : de l'automatisation scriptée à l'agent outillé → **tableau comparatif des paradigmes**
- **1.6.2 Systèmes multi-agents** : modes de coordination (superviseur, pair-à-pair, graphe d'états)
- **1.6.3 LangGraph** comme cadre d'orchestration à état partagé
- **1.6.4 RAG et RAG agentique** : recherche dense, lexicale, hybride, reclassement
- **1.6.5 Prévision de séries temporelles pour le Retail** : lissage exponentiel, décomposition saisonnière, gradient boosting, modèles de fondation
- **1.6.6 Approche hybride déterministe-générative** → **tableau : déterministe / génératif / hybride sur 6 critères**
  *(principe fondateur du projet : le LLM formule, il ne calcule pas)*

### 1.7 Démarche méthodologique adoptée
- **1.7.1 Étude comparative des méthodologies produit** : Cascade, RUP, XP, Kanban, Scrum → **tableau**
- **1.7.2 Étude comparative des méthodologies data** : SEMMA, KDD, GIMSI, TDSP, CRISP-DM → **tableau** *(réutiliser le contenu déjà rédigé du ch. 1 actuel)*
- **1.7.3 Choix retenu : Scrum × CRISP-DM** — pourquoi ni l'un ni l'autre seul ne suffit
  - Scrum seul : ne dit rien de la qualité des données ni de la validation d'un modèle
  - CRISP-DM seul : ne cadence pas la livraison d'un produit à interface utilisateur
- **1.7.4 Modèle d'emboîtement** → **figure clé : sprint = conteneur, cycle CRISP-DM = contenu**
- **1.7.5 Rôles Scrum sur le projet** : Product Owner (encadrant professionnel), Scrum Master, équipe de développement → **tableau rôle → personne → responsabilité**
- **1.7.6 Cérémonies et artefacts** : planning, daily, revue, rétrospective ; backlog produit, backlog de sprint, incrément
- **1.7.7 Définition de « Prêt » (DoR) et de « Terminé » (DoD) au niveau projet** → **tableau**
  *(la DoD projet inclut une clause data : « aucune modification de schéma à l'exécution, migration versionnée fournie »)*

### Conclusion

**Figures :** logo, organigramme, vue produit, paradigmes d'automatisation, **emboîtement Scrum × CRISP-DM**.
**Tableaux :** comparatif existant, comparatif méthodologies produit, comparatif méthodologies data, rôles, DoR/DoD.

---

## Chapitre 2 — Analyse des besoins, planification Scrum et architecture globale

*Rôle : tout ce qui est vrai pour l'ensemble du projet. Après ce chapitre, les chapitres 3 à 5
ne parlent plus que d'incréments.*

### Introduction

### 2.1 Spécification des besoins
- **2.1.1 Acteurs** → **tableau acteur → description → droits** (Conseiller, Manager de boutique, Administrateur, Agents autonomes du système)
  → **figure : cartographie des acteurs**
- **2.1.2 Besoins fonctionnels** — conserver les identifiants **BF-1 … BF-8** du chapitre 2 existant, regroupés par domaine :
  - Ventes et coaching (BF-1, BF-2, BF-5)
  - Stocks et réapprovisionnement (BF-3, BF-4)
  - Conformité et validation humaine (BF-6)
  - Supervision et pilotage (BF-7, BF-8)
  → **un tableau par domaine : identifiant → besoin → acteur → priorité MoSCoW**
- **2.1.3 Besoins non fonctionnels** — **BNF-1 … BNF-7** : latence, disponibilité, sécurité/RBAC, traçabilité, robustesse aux pannes de fournisseur, ergonomie mobile, maintenabilité
- **2.1.4 Diagramme de cas d'utilisation global** → **figure**
- **2.1.5 Description textuelle des cas d'utilisation majeurs** → **tableaux** (préconditions, scénario nominal, alternatifs, postconditions)

### 2.2 Gestion de projet avec Scrum
- **2.2.1 Backlog produit** → **tableau : ID → épopée → user story « En tant que… je veux… afin de… » → priorité MoSCoW → points de complexité**
  *(reprendre le backlog priorisé déjà rédigé ; conserver la répartition MoSCoW et la figure existante)*
- **2.2.2 Découpage en incréments** → **tableau : 4 incréments × 3 sprints**

  | Incrément | Sprints | Thème | Ce qui est démontrable en fin d'incrément |
  |---|---|---|---|
  | I1 | S1–S3 | Socle de données et pilotage des ventes | tableau de bord conseiller alimenté par des données réelles |
  | I2 | S4–S6 | Coaching agentique et supervision manager | assistant conversationnel en flux continu + centre de pilotage |
  | I3 | S7–S9 | Domaine stocks et décision de réapprovisionnement | plan de commande justifié par référence |
  | I4 | S10–S12 | Orchestration, conformité, validation humaine, industrialisation | boucle complète alerte → recommandation → contrôle → approbation → commande |

- **2.2.3 Planification des sprints** → **tableau des 12 sprints** (période, objectif, points) + **diagramme de Gantt**
  *(réutiliser `fig_planning_iterations.png` en remplaçant l'étiquette « phase CRISP-DM dominante » par « incrément »
  et en ajoutant une colonne « cycle CRISP-DM ouvert »)*
- **2.2.4 Vélocité prévisionnelle et gestion de la charge** → **figure : charge par sprint** + commentaire sur le pic des trois derniers sprints
- **2.2.5 Registre des risques et plans de contingence** → **tableau + matrice probabilité/impact**

### 2.3 Choix technologiques et justifications
*(section « à logos », conforme au style des deux rapports de référence — une sous-section par famille,
chaque techno avec son logo, 3–5 lignes de justification et l'alternative écartée)*
- **2.3.1 Langage et API** : Python, FastAPI, Pydantic, Uvicorn
- **2.3.2 Persistance** : PostgreSQL, Alembic (schéma versionné), Redis (cache et diffusion), base vectorielle
- **2.3.3 Socle IA** : LangGraph, LangChain, fournisseurs LLM en cascade, protocole MCP, Langfuse
- **2.3.4 Science des données** : pandas, statsmodels, statsforecast, XGBoost, scikit-learn
- **2.3.5 Frontend** : Angular avec signaux, WebSocket et SSE, Chart.js
- **2.3.6 Qualité et industrialisation** : pytest, Playwright, Docker, GitHub Actions
→ **tableau récapitulatif : couche → technologie → rôle → alternative écartée → motif**

### 2.4 Environnements de travail
- **2.4.1 Environnement matériel** : poste de développement, contraintes GPU/CPU, serveur de démonstration
- **2.4.2 Environnement logiciel** : IDE, gestion de versions, tableau Kanban de suivi, outils de modélisation

### 2.5 Architecture globale de la plateforme
- **2.5.1 Principe directeur** : séparation calcul déterministe / formulation générative
- **2.5.2 Architecture en trois tiers** → **figure**
- **2.5.3 Couches fonctionnelles** → **figure : présentation → API → orchestration → agents → services → dépôts → données**
- **2.5.4 Diagramme de composants** → **figure**
- **2.5.5 Répartition des responsabilités** → **tableau : ce qui est calculé en Python / ce qui est confié au modèle de langage**
- **2.5.6 Modèle de données global** → **figure : schéma relationnel par schéma métier (ventes, stocks, approvisionnement, marché, transverse)**

### Conclusion

---

## Chapitre 3 — Incréments 1 et 2 : socle de données et domaine Ventes

*Rôle : livrer le premier produit démontrable. Contient **deux cycles CRISP-DM complets** —
le socle de données, puis le moteur de prévision des ventes.*

### Introduction

### 3.1 Principes d'implémentation communs
Structure du dépôt, conventions, injection de dépendances, gestion des sessions de base,
règle « aucune instruction DDL à l'exécution ».

### 3.2 Incrément 1 — Socle de données et pilotage des ventes (S1 → S3)

#### 3.2.1 Objectif de l'incrément, DoR et DoD → **tableau : porte de qualité**
#### 3.2.2 Backlog de l'incrément → **tableau : story → points → sprint → statut**
#### 3.2.3 Cas d'utilisation couverts → **figure**

#### 3.2.4 ▣ **Cycle CRISP-DM n° 1 — Construction du socle de données**
*Phases 2 et 3 : compréhension et préparation des données.*
- **a. Collecte** : sources mobilisées, mode d'acquisition, périmètre (boutiques, période, granularité journalière et horaire) → **tableau de volumétrie par table**
- **b. Description** : dictionnaire de données par schéma métier, typage, clés, cardinalités → **tableau**
- **c. Exploration** : distribution du chiffre d'affaires par boutique / jour de semaine / heure, saisonnalités hebdomadaire et mensuelle, effets calendaires (Ramadan, fêtes, rentrée), loi de Pareto sur le catalogue → **4 à 6 figures**
- **d. Qualité** → **tableau : anomalie constatée → objectif impacté → traitement retenu**
  (valeurs manquantes, doublons, ruptures d'historique, calendrier non renseigné, références à faible historique)
- **e. Préparation** : sélection, nettoyage, construction des variables calendaires et de saisonnalité, intégration ventes↔stocks↔contexte, formatage → **figure : pipeline de préparation**
- **f. Versionnement du schéma par migrations** — pourquoi le schéma est un livrable, pas une conséquence
- **g. Sortie du cycle** : jeu de données exploitable, protocole de découpage temporel sans fuite

#### 3.2.5 Tableau de bord conseiller : conception et implémentation
- Chaîne indicateur → service → API → composant → **figure : diagramme de séquence**
- Indicateurs retenus et leur définition exacte → **tableau**

#### 3.2.6 Modèle de données de l'incrément → **figure**
#### 3.2.7 Interfaces réalisées → **captures : espace conseiller, performance du jour**
#### 3.2.8 Revue d'incrément → **tableau : story → prévu → livré → dette reportée**
#### 3.2.9 Rétrospective : ce qui a été ajusté pour l'incrément suivant

### 3.3 Incrément 2 — Coaching agentique et supervision manager (S4 → S6)

#### 3.3.1 Objectif, DoR, DoD → **tableau : porte de qualité**
#### 3.3.2 Backlog de l'incrément → **tableau**
#### 3.3.3 Cas d'utilisation couverts → **figure**

#### 3.3.4 ▣ **Cycle CRISP-DM n° 2 — Moteur de prévision des ventes**
*Phases 3, 4 et 5 : préparation, modélisation, évaluation.*
- **a. Objectifs analytiques** : rappel de **OD-1** (prévision du chiffre d'affaires) et **OD-2** (détection d'anomalies horaires)
- **b. Sélection des techniques** : référence naïve, lissage exponentiel saisonnier, décomposition, modèle appris global → **tableau : technique → hypothèse → vérifiée sur les données ? → retenue ?**
- **c. Modélisation** : recherche de paramètres, cascade de repli, profil intra-journalier, prévision de fin de journée hybride → **figure : pipeline de prévision**
- **d. Détection d'anomalies horaires** : écart attendu/réel, score normalisé, statut et tendance
- **e. Évaluation** : rétro-test à origine glissante, erreur mesurée par boutique et par horizon → **tableau comparatif + figure réel vs prévu**
- **f. Décision de fin de cycle** : modèle retenu, réserves, conditions de réentraînement

#### 3.3.5 Conception des agents du domaine Ventes
Pour chacun : rôle, entrées, outils, sortie structurée, extrait de prompt, garanties.
- **Agent Analyste** — lecture chiffrée de la performance
- **Agent Stratège** — plan d'action commercial croisant ventes et stock
- **Agent Coach** — dialogue avec le conseiller, réponse en flux continu
→ **tableau récapitulatif : agent → entrées → outils → sortie → modèle utilisé**
→ **figure : diagramme de séquence du dialogue avec l'assistant**

#### 3.3.6 ▣ **Cycle CRISP-DM n° 3 — Base de connaissances métier (RAG)**
*Phases 2, 3 et 4 appliquées à une source non structurée.*
- Constitution du corpus (scripts de vente, argumentaires, procédures), découpage, vectorisation
- Recherche hybride sémantique + lexicale, reclassement
- Mécanisme de repli sur corpus local quand la base vectorielle est indisponible
→ **figure : pipeline d'ingestion documentaire**

#### 3.3.7 Robustesse de la chaîne de génération
Cascade multi-fournisseurs, sélection du modèle par rôle, replis en cascade, budget de latence, mise en cache
→ **figure : automate de repli**

#### 3.3.8 Modèle de données de l'incrément → **figure**
#### 3.3.9 Interfaces réalisées → **captures : assistant conversationnel, centre de pilotage manager, classement des conseillers**
#### 3.3.10 Revue d'incrément → **tableau + premiers résultats mesurés**
#### 3.3.11 Rétrospective

### Conclusion

---

## Chapitre 4 — Incrément 3 : domaine Stocks et décision de réapprovisionnement

*Rôle : le deuxième domaine métier, et le cœur quantitatif du projet.*

### Introduction

### 4.1 Objectif de l'incrément, DoR et DoD → **tableau : porte de qualité**
### 4.2 Backlog de l'incrément → **tableau**
### 4.3 Cas d'utilisation couverts → **figure**

### 4.4 ▣ **Cycle CRISP-DM n° 4 — Prévision de la demande par référence**
*Phases 2 à 5, sur un jeu de données beaucoup plus creux que celui des ventes.*
- **a. Objectif analytique** : rappel de **OD-3**
- **b. Compréhension des données de stock** : couverture, rotation, ruptures, références sans historique → **figures : distribution de la couverture, courbe de Pareto des références**
- **c. Préparation spécifique** : agrégation à la maille référence × boutique × jour, traitement des références à faible historique
- **d. Modélisation en cascade** : ligne de base par décomposition saisonnière, puis modèle appris sur variables exogènes, puis repli statistique → **figure : pipeline de prévision de la demande**
- **e. Variables exogènes retenues** → **tableau : variable → source → justification métier**
- **f. Couche de correction contextuelle** : événements datés (festivals, fêtes, promotions) → comment un événement modifie une prévision
- **g. Évaluation** : erreur par référence, effet mesuré de la correction contextuelle, analyse des cas d'échec
- **h. Décision de fin de cycle**

### 4.5 Modèles de décision de stock (OD-4, OD-5)
*Section volontairement déterministe : c'est ici que se joue la crédibilité du système.*
- **4.5.1 Formules retenues** : stock de sécurité, point de commande, quantité économique de commande — énoncé, paramètres, unités
- **4.5.2 Niveau de service et facteur de sécurité** : comment le niveau de service cible est fixé et par qui
- **4.5.3 Règles de classification du risque** → **tableau : seuil → classe → couleur → action par défaut**
- **4.5.4 Arbre de décision** commander / accélérer / maintenir / surveiller → **figure**
- **4.5.5 Contraintes fournisseurs** : délais, conditionnements, minimums de commande → chaîne causale de la recommandation
- **4.5.6 Vérification** : jeux de cas limites et invariants testés

### 4.6 Conception des agents du domaine Stocks
- **Agent Analyse** — état du portefeuille, couverture, rotation
- **Agent Contexte** — enrichissement par les signaux marché et les événements
- **Agent Décision** — proposition de commande, quantité, justification
→ **tableau récapitulatif agent → entrées → outils → sortie**
→ **figure : diagramme de séquence « de l'alerte à la suggestion de commande »**

### 4.7 Croisement des deux domaines
Score produit combinant potentiel de vente et disponibilité — comment une recommandation de vente
tient compte du stock, et inversement → **figure : couplage des domaines**

### 4.8 Modèle de données de l'incrément → **figure**
### 4.9 Interfaces réalisées → **captures : santé du portefeuille, plan de commande, fiche de recommandation par référence**
### 4.10 Revue d'incrément → **tableau + résultats mesurés**
### 4.11 Rétrospective

### Conclusion

---

## Chapitre 5 — Incrément 4 : orchestration multi-agents, conformité et validation humaine

*Rôle : transformer des agents isolés en système. C'est le chapitre le plus « architecture ».*

### Introduction

### 5.1 Objectif de l'incrément, DoR et DoD → **tableau : porte de qualité**
### 5.2 Backlog de l'incrément → **tableau**
### 5.3 Cas d'utilisation couverts → **figure**

### 5.4 Orchestration multi-agents
- **5.4.1 Modèle d'état partagé** : contenu de l'état, propriétaires de chaque champ
- **5.4.2 Graphe d'exécution** : nœuds, branches parallèles, points de fusion → **figure : graphe d'orchestration**
- **5.4.3 Fusion des écritures concurrentes par réducteurs** — problème posé et solution retenue
- **5.4.4 Déclencheurs** : requête utilisateur, alerte de stock, cycle périodique → **tableau : déclencheur → chemin dans le graphe → sortie attendue**
- **5.4.5 Agent superviseur** : arbitrage entre domaines et priorisation des actions
- **5.4.6 Contraintes d'exécution** : bornes de temps, exécution hors boucle d'événements des calculs bloquants

### 5.5 Contrôle de conformité (OD-8)
- **5.5.1 Motivation** : ce qu'une recommandation ne doit jamais dire ni promettre
- **5.5.2 Règles et sévérités** → **tableau : règle → domaine → sévérité → verdict associé**
- **5.5.3 Verdicts** : diffusion / réécriture / escalade / blocage → **figure : automate d'états**
- **5.5.4 Intégration dans le graphe** : position du contrôle, coût en latence

### 5.6 Validation humaine (human-in-the-loop)
- **5.6.1 Principe** : aucun engagement (commande, envoi) sans décision humaine
- **5.6.2 Point d'arrêt et reprise** : où le graphe s'interrompt, ce qui est persisté, comment il repart
- **5.6.3 Boucle de retour** : capture de l'accord/refus et du motif, et réinjection dans le système
- **5.6.4 Suivi des bons de commande** : cycle de vie complet suggéré → approuvé → envoyé → reçu → **figure : automate du bon de commande**

### 5.7 Temps réel et notification
Diffusion des alertes et des mises à jour, files de messages, dégradation en cas de perte de connexion.

### 5.8 Sécurité et contrôle d'accès
Authentification, rafraîchissement de jeton, cloisonnement par rôle et par boutique, limitation de débit
→ **tableau : rôle → ressources accessibles**

### 5.9 Observabilité
Traçage des cycles d'agents, durées, erreurs, coûts ; journalisation structurée → **capture : vue de traçage**

### 5.10 Industrialisation
Conteneurisation, environnements, chaîne d'intégration continue, stratégie de tests (unitaires, intégration, bout en bout)
→ **figure : architecture de déploiement**

### 5.11 Modèle de données de l'incrément → **figure**
### 5.12 Interfaces réalisées → **captures : panneau de validation, tableau Kanban des commandes, supervision métier**
### 5.13 Revue d'incrément → **tableau**
### 5.14 Rétrospective

### Conclusion

---

## Chapitre 6 — Évaluation globale, démonstration et bilan

*Rôle : le chapitre que le jury lira en premier après la conclusion. Rassemble les résultats
qui n'appartiennent à aucun incrément en particulier.*

### Introduction

### 6.1 Protocole d'évaluation
- Rappel des critères de succès techniques (**OD-1 à OD-8**) et métier (**CSM-1 à CSM-6**), fixés au ch. 2 et **non révisés**
- Jeux de test, découpage temporel, métriques et leur justification
- Précautions : absence de fuite de données, comparaison systématique à une référence naïve

### 6.2 Évaluation des modèles de prévision
Synthèse consolidée des cycles CRISP-DM n° 2 et n° 4 → **tableau comparatif + figure réel vs prévu**,
analyse des cas d'échec.

### 6.3 Évaluation des décisions de stock
Cohérence de la classification du risque, pertinence des quantités recommandées, contre-exemples.

### 6.4 Évaluation des recommandations générées
- Grille du juge automatique : critères, échelle, protocole
- Résultats par domaine → **figure : barres ou radar par critère**
- Évaluation de la chaîne de recherche documentaire : fidélité aux sources, pertinence
- **Limites méthodologiques du juge automatique — assumées explicitement**

### 6.5 Évaluation du contrôle de conformité
Taux de déclenchement par règle, répartition des verdicts, vérification qu'aucune sortie
contrevenant à une règle bloquante n'a été diffusée.

### 6.6 Évaluation par le retour humain
Taux d'acceptation des propositions, analyse des motifs de rejet, enseignements pour les agents
→ **figure : approuvées / rejetées par boucle de décision**

### 6.7 Évaluation des exigences non fonctionnelles
Latences mesurées par type de requête, comportement en cas de défaillance d'un fournisseur,
couverture de tests → **tableau : BNF → cible → mesure → verdict**

### 6.8 Scénario de démonstration bout en bout
Un cas complet déroulé : anomalie détectée → analyse → recommandation → contrôle de conformité →
validation du manager → commande suggérée → suivi jusqu'à réception. Une capture par étape.

### 6.9 Bilan d'atteinte des objectifs
→ **tableau de synthèse : objectif → critère fixé → résultat → atteint / partiellement / non atteint**

### 6.10 Bilan de projet
- Vélocité réelle par sprint vs prévisionnel → **figure**
- Difficultés rencontrées et solutions apportées → **tableau**
- Écarts au plan initial et arbitrages assumés
- Perspectives : items *Won't have* du backlog, réentraînement, extension à d'autres boutiques

### Conclusion

---

## Conclusion générale et perspectives (2 pages)

## Bibliographie
Références minimales : Schwaber & Sutherland (*Scrum Guide*) · Chapman et al. (*CRISP-DM 1.0*) ·
Wirth & Hipp · Hyndman & Athanasopoulos (*Forecasting: Principles and Practice*) ·
une référence systèmes multi-agents à base de LLM · une référence RAG ·
une référence gestion des stocks (EOQ, stock de sécurité, niveau de service) ·
une référence évaluation de systèmes RAG.

## Annexes
Extraits de prompts · dictionnaire de données complet · backlog produit intégral ·
tableaux de revue des 12 sprints.

---

## Règles de cohérence propres à cette version

1. **Un identifiant, un sens.** BF-*n*, BNF-*n*, OM-*n*, CSM-*n*, OD-*n* sont définis au ch. 2 et jamais renumérotés.
2. **Chaque incrément se ferme sur une revue chiffrée.** Contrairement à la version pure CRISP-DM,
   les résultats apparaissent au fil de l'eau — mais uniquement les résultats *de l'incrément*.
   L'évaluation transverse reste au ch. 6.
3. **Chaque encadré « Cycle CRISP-DM » nomme ses phases** et se termine par une *décision de fin de cycle*
   (modèle retenu / à réviser), sinon l'emboîtement n'est qu'un habillage.
4. **Les captures d'écran sont autorisées dès le ch. 3**, à la section « Interfaces réalisées » de chaque incrément.
5. Chaque chapitre s'ouvre sur une introduction annonçant son plan et se ferme sur une conclusion faisant le lien avec le suivant.
6. Chaque figure et chaque tableau est appelé dans le texte et commenté.
