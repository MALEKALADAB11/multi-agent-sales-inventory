# Version B — Plan du rapport : **conduit par CRISP-DM**

> **Modèle de référence.** Ce plan emprunte au rapport *Audit ISO* (Z. Ajmi, TEK-UP) sa structure
> d'ouverture — cadre général, puis besoins + architecture + choix technologiques + environnements —
> et sa discipline de chapitre : introduction, contenu, résultats de tests, interfaces, conclusion.
>
> **Ce qu'on en change.** Chez Ajmi, CRISP-DM est *cité* au chapitre 1 puis abandonné : les chapitres 3
> à 6 sont découpés par module fonctionnel. Ici, **la table des matières est la méthode** : un jury qui
> connaît CRISP-DM doit retrouver les six phases dans le sommaire. C'est ce qui distingue un rapport
> « qui cite CRISP-DM » d'un rapport « conduit par CRISP-DM ».
>
> **Argument de soutenance.** « Le plan du rapport est le cycle de vie du projet. Chaque chapitre est
> une phase, chaque phase se ferme sur la décision qui autorise la suivante. »
>
> *Ce fichier remplace `PLAN_RAPPORT.md`, qu'il étend aux chapitres 1 et 2 et aux pages liminaires.*

---

## 0. Correspondance phases ↔ chapitres

| Chapitre | Titre | Phase CRISP-DM | État |
|---|---|---|---|
| Ch. 1 | Cadre général du projet | *hors cycle — cadrage et choix de la méthode* | ✅ rédigé (`chapitre1.tex`) |
| Ch. 2 | Compréhension métier | Phase 1 — *Business Understanding* | ✅ rédigé (`chapitre2.tex`) |
| Ch. 3 | Compréhension et préparation des données | Phases 2 et 3 | à rédiger |
| Ch. 4 | Modélisation : conception du moteur agentique | Phase 4 — *Modeling* | à rédiger |
| Ch. 5 | Évaluation | Phase 5 — *Evaluation* | à rédiger |
| Ch. 6 | Déploiement et réalisation | Phase 6 — *Deployment* | à rédiger |

**Pourquoi 6 chapitres et non 4.** Les phases 2 et 3 sont fusionnées — elles partagent les mêmes sources
et s'entremêlent naturellement. Les phases 5 et 6 restent séparées : l'évaluation est le cœur scientifique
du projet (rétro-tests, juge automatique, retour humain) et la noyer dans un chapitre « réalisation »
l'affaiblirait.

**Repli si le volume impose 5 chapitres :** fusionner Ch. 5 et Ch. 6 en « Évaluation et déploiement »,
en gardant l'évaluation en première section. **Ne jamais fusionner Ch. 3 et Ch. 4.**

---

## 1. Pages liminaires

| Élément | Contenu | Pages |
|---|---|---|
| Page de garde | République Tunisienne / TEK-UP / titre / encadrants / année 2025–2026 | 1 |
| Autorisations de dépôt | Encadrant professionnel Ooredoo + encadrant académique | 1 |
| Dédicaces | — | 1 |
| Remerciements | Encadrants, équipe Retail Ooredoo, jury, enseignants | 1 |
| Résumé / Abstract / ملخص | 150 mots chacun | 1 |
| Table des matières · figures · tableaux | générées | 4–5 |
| Liste des acronymes | IA, LLM, RAG, SMA, MCP, MAPE, WAPE, EOQ, ROP, SS, HITL, CRISP-DM, SSE, WS, RBAC, ETL, KPI, API, SLA | 1 |
| **Introduction générale** | contexte → problématique → objectifs → **annonce du plan phase par phase** | 2 |

---

## 2. Chapitre 1 — Cadre général du projet *(rédigé — ajustements à faire)*

Structure actuelle, à conserver :

### Introduction
### 1.1 Présentation de l'organisme d'accueil
Ooredoo Tunisie · activités · clientèle · valeurs · organisation · direction concernée
### 1.2 Contexte général du projet
Cadre · problématique · objectifs · périmètre retenu
### 1.3 Étude de l'existant
Existant interne · solutions du marché · synthèse comparative
### 1.4 Critique de l'existant
### 1.5 Solution proposée
### 1.6 Démarche méthodologique du projet
SEMMA · KDD · GIMSI · CRISP-DM · TDSP · étude comparative · **choix de CRISP-DM** ·
instanciation au projet · application itérative sur 12 itérations de deux semaines
### Conclusion

**Ajustements recommandés :**
- Ajouter en 1.6 une **figure du cycle CRISP-DM** annotée du nom des chapitres du présent rapport
  — c'est la figure qui vend la structure au jury dès la page 10.
- Ajouter une sous-section **1.6.9 « Ce que CRISP-DM ne couvre pas »** : le cadencement de livraison
  d'un produit à interface utilisateur. Préciser que ce manque est comblé par une exécution itérative
  et incrémentale, sans adopter un cadre agile formel. Cela désamorce la question du jury
  « pourquoi pas Scrum ? » avant qu'elle ne soit posée.

---

## 3. Chapitre 2 — Compréhension métier *(rédigé — ajustements à faire)*

Structure actuelle, alignée sur les tâches génériques de la phase 1 :

### Introduction
### 2.1 Détermination des objectifs métier
Contexte métier · objectifs métier **OM-1 … OM-7** · critères de succès métier **CSM-1 … CSM-6**
### 2.2 Évaluation de la situation
Inventaire des ressources · identification des acteurs · expression des exigences **BF-1 … BF-8**
et **BNF-1 … BNF-7** · hypothèses et contraintes · risques et plans de contingence · terminologie ·
analyse coûts-bénéfices
### 2.3 Détermination des objectifs de fouille de données
Traduction des objectifs métier en objectifs analytiques **OD-1 … OD-8** · critères de succès
techniques · traçabilité des exigences
### 2.4 Modélisation des besoins
Diagramme de cas d'utilisation global · description des cas d'utilisation principaux · diagrammes de séquence
### 2.5 Production du plan de projet
Backlog priorisé · planification des itérations · évaluation initiale des outils et des techniques
### Conclusion

**Ajustements recommandés :**
- La sous-section « Évaluation initiale des outils et des techniques » (2.5.3) est le bon endroit
  pour la **section « à logos »** que les deux rapports de référence placent au chapitre 2 :
  une famille technologique par paragraphe, logo, justification, alternative écartée.
  L'ajouter y est cohérent avec CRISP-DM — la tâche 1.4 de la phase 1 s'appelle littéralement
  *« Determine tools and techniques »*.
- Ajouter en fin de 2.5 les **environnements de travail** (matériel, logiciel) — attendu du format TEK-UP.
- Terminer le chapitre par la **décision de fin de phase 1** : « les objectifs analytiques sont formulés
  et outillables, la phase 2 est autorisée ». Ce type de phrase, répété à chaque fin de chapitre,
  est ce qui rend la conduite CRISP-DM visible.

---

## 4. Chapitre 3 — Compréhension et préparation des données

*Phases 2 et 3. Objectif : confronter les objectifs analytiques du ch. 2 à la réalité des sources,
puis construire le jeu de données exploitable. C'est le chapitre qui prouve la maîtrise des données,
pas des outils.*

### Introduction

### 3.1 Collecte des données initiales *(tâche 2.1)*
- Sources mobilisées et mode d'acquisition
- Périmètre : points de vente, période couverte, granularité journalière et horaire
- **Tableau : volumétrie par table**
- Rapport de collecte : ce qui a été obtenu, ce qui ne l'a pas été, et l'impact sur les objectifs OD-*n*

### 3.2 Description des données *(tâche 2.2)*
- **Tableau : dictionnaire de données par schéma métier** (ventes, stocks, approvisionnement, marché, transverse)
- Modèle conceptuel puis modèle logique → **figure : schéma relationnel**
- Typage, clés, cardinalités, vues d'agrégation

### 3.3 Exploration des données *(tâche 2.3)*
- Distribution du chiffre d'affaires : par boutique, par jour de semaine, par heure
- Saisonnalités : hebdomadaire, mensuelle, effets calendaires (fêtes, Ramadan, rentrée)
- Rotation des références : loi de Pareto sur le catalogue
- Corrélations ventes ↔ stock ↔ contexte
→ **figures : profil horaire, saisonnalité hebdomadaire, distribution de la couverture, courbe de Pareto, matrice de corrélation**
- **Chaque figure est reliée à un objectif analytique** : ce que l'exploration confirme ou infirme
  des hypothèses posées au ch. 2.

### 3.4 Vérification de la qualité des données *(tâche 2.4)*
- Valeurs manquantes, doublons, incohérences relevées
- Ruptures d'historique et références à faible historique
- Champs calendaires non renseignés et effet de bord sur les profils saisonniers
- **Tableau : anomalie constatée → objectif analytique impacté → traitement retenu**
- Contraintes d'intégrité mises en place (clés étrangères, contrôles)

### 3.5 Préparation des données *(phase 3)*
- **3.5.1 Sélection** *(tâche 3.1)* : critères d'inclusion des références et des périodes
- **3.5.2 Nettoyage** *(tâche 3.2)* : traitement des valeurs manquantes et aberrantes, règles de décision
- **3.5.3 Construction** *(tâche 3.3)* : variables calendaires, variables de saisonnalité, agrégats de vente,
  couverture, rotation, indicateurs contextuels → **tableau : variable construite → formule → objectif servi**
- **3.5.4 Intégration** *(tâche 3.4)* : jointures entre domaines ventes / stocks / contexte, clés de rapprochement, pièges rencontrés
- **3.5.5 Formatage** *(tâche 3.5)* : structures d'entrée attendues par chaque famille de modèles
→ **figure : diagramme du pipeline de préparation**
- **3.5.6 Versionnement du schéma par migrations** : principe « aucune modification de schéma à l'exécution »,
  et pourquoi c'en est un choix d'ingénierie des données, pas d'outillage

### 3.6 Jeu de données final
- Description des jeux d'entraînement, de validation et de test
- Protocole de découpage temporel et garantie d'absence de fuite de données → **figure**

### Conclusion
**Décision de fin de phase :** le jeu de données satisfait les prérequis des objectifs OD-1 à OD-5 ;
réserves explicites sur les références à faible couverture ; la modélisation est autorisée.

**Figures :** schéma relationnel · 5 à 6 graphiques d'exploration · pipeline de préparation · découpage temporel.
**Tableaux :** volumétrie · dictionnaire de données · qualité des données · variables construites.

---

## 5. Chapitre 4 — Modélisation : conception du moteur agentique

*Phase 4. Objectif : décrire les modèles et l'architecture qui les met en œuvre. C'est ici, et pas avant,
qu'on parle de conception — parce que CRISP-DM veut le problème posé avant la technique.*

### Introduction

### 4.1 Architecture générale de la solution
- Architecture globale en trois tiers → **figure**
- Architecture logique en couches : API, orchestration, agents, services, dépôts → **figure**
- **Principe fondateur : les calculs qui engagent une décision restent déterministes ;
  le modèle de langage formule, il ne calcule pas** → **tableau : ce qui est calculé / ce qui est formulé**
- Diagramme de composants → **figure**
- Diagramme de classes des entités métier → **figure**

### 4.2 Sélection des techniques de modélisation *(tâche 4.1)*
- Justification par objectif analytique **OD-1 à OD-8**
- **Tableau : objectif → techniques candidates → hypothèses → vérification sur les données du ch. 3 → technique retenue**
- Techniques écartées et motif du rejet — une section sans rejet documenté n'est pas une sélection

### 4.3 Conception du protocole de test *(tâche 4.2)*
Métriques retenues par famille de modèle, protocole de rétro-test, référence naïve de comparaison.
*Cette tâche appartient bien à la phase 4 : le protocole se conçoit avant d'entraîner, pas après.*

### 4.4 Modélisation prédictive du domaine Ventes *(OD-1, OD-2)*
- Moteur de séries temporelles : lissage exponentiel saisonnier, recherche de paramètres
- Modèle appris global et cascade de repli
- Profil intra-journalier et prévision de fin de journée hybride
- Détection d'anomalies horaires : écart attendu / réel, score normalisé, statut et tendance
→ **figure : pipeline de prévision des ventes**

### 4.5 Modélisation prédictive du domaine Stocks *(OD-3)*
- Prévision de demande par référence : ligne de base par décomposition, puis modèle appris, puis repli
- Variables exogènes retenues → **tableau : variable → source → justification métier**
- Couche de correction contextuelle : comment un événement daté modifie une prévision
→ **figure : pipeline de prévision de la demande**

### 4.6 Modèles de décision de stock *(OD-4, OD-5)*
- Formules retenues : stock de sécurité, point de commande, quantité économique de commande — énoncé, paramètres, unités
- Niveau de service et facteur de sécurité : valeur cible et qui la fixe
- Règles de classification du risque → **tableau : seuil → classe → action par défaut**
- Contraintes fournisseurs : délais, conditionnements, minimums de commande
- Arbre de décision commander / accélérer / maintenir / surveiller → **figure**

### 4.7 Conception des agents intelligents *(OD-6, OD-7)*
Pour chaque agent : rôle, entrées, outils, sortie structurée, extrait de prompt, garanties.
- **Domaine Ventes** : Agent Analyste · Agent Stratège · Agent Coach
- **Domaine Stocks** : Agent Analyse · Agent Contexte · Agent Décision
- **Transverse** : Agent Superviseur
→ **tableau récapitulatif : agent → entrées → outils → sortie → modèle utilisé**

### 4.8 Base de connaissances métier et recherche hybride *(OD-7)*
- Constitution du corpus, découpage, vectorisation
- Recherche sémantique + lexicale, reclassement
- Mécanisme de repli sur corpus local
→ **figure : pipeline d'ingestion documentaire**

### 4.9 Orchestration multi-agents
- Graphe d'états : nœuds, branches parallèles, fusion par réducteurs → **figure : graphe d'orchestration**
- État partagé et gestion des écritures concurrentes
- Déclencheurs : requête utilisateur, alerte, cycle périodique → **tableau : déclencheur → chemin → sortie attendue**
- Couplage des deux domaines : score produit combinant potentiel de vente et disponibilité → **figure**

### 4.10 Contrôle de conformité et validation humaine *(OD-8)*
- Règles de garde-fous et leur sévérité → **tableau**
- Verdicts : diffusion / réécriture / escalade / blocage → **figure : automate d'états**
- Point d'arrêt humain avant tout engagement : où le graphe s'interrompt, ce qui est persisté, comment il repart
- Cycle de vie du bon de commande → **figure : automate**

### 4.11 Robustesse de la chaîne de génération
Cascade multi-fournisseurs, sélection par rôle, replis en cascade, mise en cache, budget de latence
→ **figure : automate de repli**

### Conclusion
**Décision de fin de phase :** les modèles sont construits et instrumentés ; le protocole de test du 4.3
est prêt à être exécuté ; la phase d'évaluation est autorisée.

---

## 6. Chapitre 5 — Évaluation

*Phase 5. Objectif : confronter les résultats obtenus aux critères de succès fixés au ch. 2 —
techniques d'abord, métier ensuite. **Un critère n'est jamais ajusté après coup** : c'est ce qui rend
l'évaluation crédible.*

### Introduction

### 5.1 Protocole d'évaluation
- Rappel des critères techniques (**OD-1 à OD-8**) et métier (**CSM-1 à CSM-6**)
- Jeux de test, découpage temporel, métriques retenues et leur justification
- Précautions : absence de fuite de données, comparaison à une référence naïve

### 5.2 Évaluation des modèles de prévision
- Prévision du chiffre d'affaires : rétro-test à origine glissante, erreur mesurée par boutique et par horizon
- Comparaison des méthodes candidates → **tableau + figure : réel vs prévu**
- Prévision de la demande : erreur par référence, effet mesuré de la correction contextuelle
- Analyse des cas d'échec : références à faible historique, ruptures d'historique

### 5.3 Évaluation des décisions de stock
- Cohérence de la classification du risque
- Pertinence des quantités recommandées, contre-exemples analysés

### 5.4 Évaluation des recommandations générées
- Grille du juge automatique : critères, échelle, protocole
- Résultats par domaine → **figure : radar ou barres par critère**
- Évaluation de la chaîne de recherche documentaire : fidélité aux sources, pertinence
- **Limites méthodologiques du juge automatique — à assumer explicitement, c'est un point fort en soutenance**

### 5.5 Évaluation du contrôle de conformité
- Taux de déclenchement par règle, répartition des verdicts
- Vérification : aucune diffusion contrevenant à une règle bloquante

### 5.6 Évaluation par le retour humain
- Taux d'acceptation des propositions par le manager
- Analyse des motifs de rejet → enseignements pour les agents
→ **figure : approuvées / rejetées par boucle de décision**

### 5.7 Évaluation des exigences non fonctionnelles
- Latences mesurées par type de requête
- Comportement en cas de défaillance d'un composant ou d'un fournisseur de modèle
- Couverture de tests
→ **tableau : BNF-*n* → cible → mesure → verdict**

### 5.8 Revue du processus *(tâche 5.2 — souvent oubliée, et c'est une erreur)*
Ce qui a été fait correctement, ce qui a été négligé, ce qu'il faudrait refaire :
qualité des données amont, choix de découpage, biais possibles.

### 5.9 Bilan : atteinte des objectifs *(tâche 5.3)*
- **Tableau de synthèse : objectif → critère fixé → résultat → atteint / partiellement / non atteint**
- Retour sur les objectifs métier : ce que le système change réellement pour le conseiller et pour le manager
- **Décision CRISP-DM de fin de phase : passage au déploiement, et sous quelles réserves**

### Conclusion

---

## 7. Chapitre 6 — Déploiement et réalisation

*Phase 6. Objectif : montrer le système en fonctionnement. **C'est le chapitre des captures d'écran — pas avant.***

### Introduction

### 6.1 Planification du déploiement *(tâche 6.1)*
- Architecture physique et conteneurisation → **figure**
- Environnements et configuration
- Chaîne d'intégration continue et stratégie de tests

### 6.2 Interfaces réalisées — espace conseiller
- Performance du jour, alertes temps réel, recommandations → **captures**
- Assistant conversationnel : dialogue, réponse progressive, contexte temps réel → **captures**
- Demandes de réapprovisionnement et suivi → **capture**

### 6.3 Interfaces réalisées — espace manager
- Centre de pilotage : indicateurs, performance horaire, carte des risques, plan stratégique → **captures**
- Stock et inventaire : santé du portefeuille, plan de commande, fiche de recommandation → **captures**
- Gestion des conseillers : classement, focus conseiller, actions recommandées → **capture**
- Arbitrage des demandes → **capture**
- Bons de commande : vue liste, vue Kanban, fiche détaillée avec cycle de vie → **captures**
- Supervision métier : acceptation, qualité des recommandations, fiabilité des sources, motifs de rejet → **captures**

### 6.4 Scénario de démonstration bout en bout
Un cas complet déroulé : anomalie détectée → analyse → recommandation → contrôle de conformité →
validation humaine → commande suggérée → suivi jusqu'à réception. Une capture par étape.

### 6.5 Planification de la surveillance et de la maintenance *(tâche 6.2)*
- Observabilité des cycles d'agents : durées, erreurs, coûts → **capture : vue de traçage**
- Suivi de la qualité en production et boucle de retour humain
- Plan de réentraînement des modèles : déclencheurs, fréquence, responsable

### 6.6 Production du rapport final et revue de projet *(tâches 6.3 et 6.4)*
- Difficultés rencontrées et solutions apportées → **tableau**
- Écarts au plan initial et arbitrages assumés
- Enseignements méthodologiques : ce que l'application de CRISP-DM a apporté et où elle a coincé
- Perspectives d'évolution, dont les items *Won't have* du ch. 2

### Conclusion

---

## 8. Conclusion générale et perspectives (2 pages)

## 9. Bibliographie
Chapman et al., *CRISP-DM 1.0 Step-by-step data mining guide*, 2000 ·
Wirth & Hipp, *CRISP-DM: Towards a standard process model for data mining*, 2000 ·
Hyndman & Athanasopoulos, *Forecasting: Principles and Practice* ·
une référence systèmes multi-agents à base de LLM · une référence RAG ·
une référence gestion des stocks (EOQ, stock de sécurité, niveau de service) ·
une référence évaluation de systèmes RAG.

## 10. Annexes
Extraits de prompts · dictionnaire de données complet · backlog intégral · détail des rétro-tests.

---

## 11. Figures manquantes, par priorité

| Priorité | Figure | Chapitre |
|---|---|---|
| 🔴 | Cycle CRISP-DM annoté du nom des chapitres | 1 |
| 🔴 | Schéma relationnel de la base | 3 |
| 🔴 | Architecture globale · logique · composants | 4 |
| 🔴 | Graphe d'orchestration multi-agents | 4 |
| 🟠 | Graphiques d'exploration des données (5 à 6) | 3 |
| 🟠 | Pipelines de prévision (ventes, demande) | 4 |
| 🟠 | Automate d'états du contrôle de conformité | 4 |
| 🟠 | Diagramme de classes métier | 4 |
| 🟠 | Découpage temporel sans fuite | 3 |
| 🟢 | Graphiques de résultats d'évaluation | 5 |
| 🟢 | Architecture physique | 6 |

*(Déjà produites : cartographie des acteurs, cas d'utilisation, chaîne des objectifs, charge et planning
des itérations, couplage des domaines, CRISP-DM phase 1, matrice des risques, processus as-is,
répartition MoSCoW, séquences coach et réappro, tâches phase 1.)*

---

## 12. Règles de cohérence à tenir sur tout le rapport

1. **Un identifiant, un sens.** OM-*n*, CSM-*n*, BF-*n*, BNF-*n*, OD-*n* sont définis au ch. 2 et
   réutilisés tels quels ensuite. Jamais renumérotés.
2. **Aucun résultat chiffré avant le ch. 5.** Les chapitres 2 à 4 annoncent des critères, pas des scores.
3. **Aucune capture d'écran avant le ch. 6.**
4. **Chaque chapitre se ferme sur une décision de fin de phase** qui autorise la suivante.
   C'est la marque distinctive de cette version — sans elle, le plan n'est qu'un sommaire.
5. Chaque chapitre s'ouvre sur une introduction annonçant son plan et se ferme sur une conclusion
   qui fait le lien avec le suivant.
6. Chaque figure et chaque tableau est appelé dans le texte (`voir figure~\ref{...}`) et commenté :
   une figure non commentée est une figure inutile.
7. **Vocabulaire** : s'en tenir aux termes définis dans le glossaire du ch. 2.
