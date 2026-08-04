> ⚠️ **Fichier remplacé.** Deux plans complets, couvrant tous les chapitres et les pages liminaires,
> l'ont supplanté :
> - [PLAN_A_SCRUM_CRISPDM.md](PLAN_A_SCRUM_CRISPDM.md) — Scrum piloté, CRISP-DM encastré (modèle : rapport *taptab.social*)
> - [PLAN_B_CRISPDM.md](PLAN_B_CRISPDM.md) — conduit par CRISP-DM (modèle : rapport *Audit ISO*), reprend et étend le présent fichier
>
> Conservé pour référence uniquement.

# Plan du rapport de PFE — structure CRISP-DM

> **Principe directeur.** Le rapport n'est pas un rapport agile déguisé : chaque chapitre correspond
> à une phase du cycle CRISP-DM. Un jury qui connaît la méthode doit pouvoir retrouver les six phases
> dans la table des matières. C'est ce qui distingue un rapport « qui cite CRISP-DM » d'un rapport
> « conduit par CRISP-DM ».

## 1. Correspondance phases ↔ chapitres

| Chapitre | Titre | Phase CRISP-DM | État |
|---|---|---|---|
| Ch. 1 | Cadre général du projet | *(hors cycle — cadrage et choix de la méthode)* | ✅ rédigé |
| Ch. 2 | Compréhension métier | Phase 1 — *Business Understanding* | ✅ rédigé |
| Ch. 3 | Compréhension et préparation des données | Phases 2 et 3 — *Data Understanding / Data Preparation* | à rédiger |
| Ch. 4 | Modélisation : conception du moteur agentique | Phase 4 — *Modeling* | à rédiger |
| Ch. 5 | Évaluation | Phase 5 — *Evaluation* | à rédiger |
| Ch. 6 | Déploiement et réalisation | Phase 6 — *Deployment* | à rédiger |

**Pourquoi 6 chapitres et non 4 :** les phases 2 et 3 sont fusionnées (elles partagent les mêmes
sources et s'entremêlent naturellement), mais les phases 5 et 6 restent séparées. C'est un choix
défendable en soutenance : l'évaluation est le cœur scientifique du projet (backtests, juge
automatique, feedback humain) et la noyer dans un chapitre « réalisation » l'affaiblirait.

**Repli si le volume impose 5 chapitres :** fusionner Ch. 5 et Ch. 6 en « Évaluation et déploiement »,
en gardant l'évaluation en première section. Ne jamais fusionner Ch. 3 et Ch. 4.

---

## 2. Chapitre 3 — Compréhension et préparation des données

*Objectif : confronter les objectifs analytiques du Ch. 2 à la réalité des sources, puis construire
le jeu de données exploitable. C'est le chapitre qui prouve la maîtrise des données, pas des outils.*

### Introduction

### 3.1 Collecte des données initiales
- Sources mobilisées et mode d'acquisition
- Périmètre : points de vente, période couverte, granularité (journalière et horaire)
- Volumétrie par table

### 3.2 Description des données
- Dictionnaire de données par schéma métier (ventes, stocks, approvisionnement, marché, transverse)
- Modèle conceptuel / modèle logique de données → **figure : schéma relationnel**
- Typage, clés, cardinalités, vues d'agrégation

### 3.3 Exploration des données
- Distribution du chiffre d'affaires : par boutique, par jour de semaine, par heure
- Saisonnalités : hebdomadaire, mensuelle, effets calendaires (fêtes, Ramadan, rentrée)
- Analyse de la rotation des références : loi de Pareto sur le catalogue
- Corrélations ventes ↔ stock ↔ contexte
- → **figures : profil horaire, saisonnalité hebdomadaire, distribution de la couverture, matrice de corrélation**

### 3.4 Vérification de la qualité des données
- Valeurs manquantes, doublons, incohérences relevées
- Ruptures d'historique et références à faible historique
- **Tableau : anomalie constatée → impact sur quel objectif analytique → traitement retenu**
- Contraintes d'intégrité mises en place (clés étrangères, contrôles)

### 3.5 Préparation des données
- **3.5.1 Sélection** : critères d'inclusion des références et des périodes
- **3.5.2 Nettoyage** : traitement des valeurs manquantes et aberrantes
- **3.5.3 Construction** : variables calendaires, variables de saisonnalité, agrégats de vente,
  couverture, rotation, indicateurs contextuels
- **3.5.4 Intégration** : jointures entre domaines ventes / stocks / contexte
- **3.5.5 Formatage** : structures d'entrée des modèles de prévision
- Versionnement du schéma par migrations : principe « aucune modification de schéma à l'exécution »

### 3.6 Jeu de données final
- Description des jeux d'entraînement, de validation et de test
- Protocole de découpage temporel (pas de fuite de données)

### Conclusion

**Figures à produire :** schéma relationnel, 4 à 6 graphiques d'exploration, diagramme du pipeline de préparation.
**Tableaux :** dictionnaire de données, volumétrie, qualité des données, variables construites.

---

## 3. Chapitre 4 — Modélisation : conception du moteur agentique

*Objectif : décrire les modèles et l'architecture qui les met en œuvre. C'est ici, et pas avant,
qu'on parle de conception — parce que CRISP-DM veut le problème posé avant la technique.*

### Introduction

### 4.1 Architecture générale de la solution
- Architecture globale en trois tiers → **figure**
- Architecture logique en couches (API, orchestration, agents, services et dépôts) → **figure**
- Principe fondateur : **les calculs qui engagent une décision restent déterministes** ;
  le modèle de langage formule, il ne calcule pas
- Diagramme de composants → **figure**
- Diagramme de classes des entités métier → **figure**

### 4.2 Sélection des techniques de modélisation
- Justification par objectif analytique (OD-1 à OD-8 du Ch. 2)
- Hypothèses de chaque technique et leur vérification sur les données du Ch. 3

### 4.3 Modélisation prédictive du domaine Ventes (OD-1, OD-2)
- Moteur de séries temporelles : lissage exponentiel saisonnier, recherche de paramètres
- Modèle appris global et cascade de repli
- Profil intra-journalier et prévision de fin de journée hybride
- Détection d'anomalies horaires : écart attendu / réel, score normalisé, statut et tendance
- → **figure : pipeline de prévision**

### 4.4 Modélisation prédictive du domaine Stocks (OD-3)
- Prévision de demande de référence et couche de correction contextuelle
- Variables exogènes retenues
- → **figure : pipeline de prévision de la demande**

### 4.5 Modèles de décision de stock (OD-4, OD-5)
- Formules retenues : stock de sécurité, point de commande, quantité économique de commande
- Niveau de service et facteur de sécurité
- Règles de classification du risque
- Arbre de décision : commander / accélérer / maintenir / surveiller → **figure**

### 4.6 Conception des agents intelligents (OD-6, OD-7)
Pour chaque agent : rôle, entrées, outils, sorties, prompt (extrait), garanties.
- Agent Analyste · Agent Stratège · Agent Coach
- Agent Analyse / Contexte / Décision Inventaire
- **Tableau récapitulatif : agent → entrées → outils → sortie**

### 4.7 Base de connaissances métier et recherche hybride (OD-7)
- Constitution du corpus, découpage, vectorisation
- Recherche sémantique + lexicale, reclassement
- Mécanisme de repli sur corpus local

### 4.8 Orchestration multi-agents
- Graphe d'états : nœuds, branches parallèles, fusion par réducteurs → **figure**
- État partagé et gestion des écritures concurrentes
- Déclencheurs : requête utilisateur, alerte, cycle périodique

### 4.9 Contrôle de conformité et validation humaine (OD-8)
- Règles de garde-fous et leur sévérité → **tableau**
- Verdicts : diffusion / réécriture / escalade / blocage → **figure : automate d'états**
- Point d'arrêt humain avant tout engagement

### 4.10 Robustesse de la chaîne de génération
- Cascade multi-fournisseurs, sélection par rôle, replis en cascade
- Mise en cache et budget de latence

### Conclusion

---

## 4. Chapitre 5 — Évaluation

*Objectif : confronter les résultats obtenus aux critères de succès fixés au Ch. 2 — techniques
d'abord, métier ensuite. Un critère n'est jamais ajusté après coup : c'est ce qui rend l'évaluation crédible.*

### Introduction

### 5.1 Protocole d'évaluation
- Rappel des critères de succès techniques (OD-1 à OD-8) et métier (CSM-1 à CSM-6)
- Jeux de test, découpage temporel, métriques retenues et leur justification
- Précautions : absence de fuite de données, comparaison à une référence naïve

### 5.2 Évaluation des modèles de prévision
- Prévision du chiffre d'affaires : rétro-test à origine glissante, erreur mesurée
- Comparaison des méthodes candidates → **tableau + figure : réel vs prévu**
- Prévision de la demande : erreur par référence, effet de la correction contextuelle
- Analyse des cas d'échec (références à faible historique, ruptures d'historique)

### 5.3 Évaluation des décisions de stock
- Cohérence de la classification du risque
- Pertinence des quantités recommandées

### 5.4 Évaluation des recommandations générées
- Grille du juge automatique : critères et échelle
- Résultats par domaine → **figure : radar ou barres par critère**
- Évaluation de la chaîne de recherche documentaire (fidélité aux sources, pertinence)
- Limites méthodologiques du juge automatique — **à assumer explicitement, c'est un point fort en soutenance**

### 5.5 Évaluation du contrôle de conformité
- Taux de déclenchement par règle, répartition des verdicts
- Vérification : aucune diffusion contrevenant à une règle bloquante

### 5.6 Évaluation par le retour humain
- Taux d'acceptation des propositions par le manager
- Analyse des motifs de rejet → enseignements pour les agents
- → **figure : approuvées / rejetées par boucle de décision**

### 5.7 Évaluation des exigences non fonctionnelles
- Latences mesurées, comportement en cas de défaillance d'un composant
- Couverture de tests

### 5.8 Bilan : atteinte des objectifs
- **Tableau de synthèse : objectif → critère fixé → résultat → atteint / partiellement / non atteint**
- Retour sur les objectifs métier : ce que le système change réellement pour l'utilisateur
- Décision CRISP-DM de fin de phase : passage au déploiement, et sous quelles réserves

### Conclusion

---

## 5. Chapitre 6 — Déploiement et réalisation

*Objectif : montrer le système en fonctionnement. C'est le chapitre des captures d'écran — pas avant.*

### Introduction

### 6.1 Stratégie de déploiement
- Architecture physique et conteneurisation → **figure**
- Environnements et configuration
- Chaîne d'intégration continue

### 6.2 Interfaces réalisées — espace conseiller
- Espace conseiller : performance, alertes temps réel, recommandations → **captures**
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
Dérouler un cas complet : anomalie détectée → analyse → recommandation → contrôle →
validation humaine → commande suggérée → suivi jusqu'à réception.

### 6.5 Surveillance et maintenance
- Observabilité des cycles d'agents : durées, erreurs, coûts
- Suivi de la qualité en production et boucle de feedback
- Plan de maintenance et de réentraînement des modèles

### 6.6 Bilan du projet
- Difficultés rencontrées et solutions apportées
- Écarts au plan initial et arbitrages assumés
- Perspectives d'évolution (dont les items *Won't have* du Ch. 2)

### Conclusion

---

## 6. Éléments transverses à préparer

### Figures manquantes (par priorité)

| Priorité | Figure | Chapitre |
|---|---|---|
| 🔴 | Diagramme de cas d'utilisation global | 2 |
| 🔴 | Séquence — dialogue avec l'assistant | 2 |
| 🔴 | Séquence — de l'alerte à la suggestion de commande | 2 |
| 🔴 | Schéma relationnel de la base | 3 |
| 🔴 | Architecture globale · logique · composants | 4 |
| 🔴 | Graphe d'orchestration multi-agents | 4 |
| 🟠 | Graphiques d'exploration des données (4 à 6) | 3 |
| 🟠 | Pipelines de prévision (ventes, demande) | 4 |
| 🟠 | Automate d'états du contrôle de conformité | 4 |
| 🟠 | Diagramme de classes métier | 4 |
| 🟢 | Graphiques de résultats d'évaluation | 5 |
| 🟢 | Architecture physique | 6 |

### Sections d'ouverture et de clôture
- Dédicaces, remerciements
- Résumé / Abstract / ملخص
- Table des matières, liste des figures, liste des tableaux, liste des abréviations
- **Introduction générale** (2 pages) : contexte, problématique, objectifs, annonce du plan par phase CRISP-DM
- **Conclusion générale** (2 pages) : bilan, apports, limites, perspectives
- **Bibliographie** — références minimales à citer :
  - Chapman et al., *CRISP-DM 1.0 Step-by-step data mining guide*, 2000
  - Wirth & Hipp, *CRISP-DM: Towards a standard process model for data mining*, 2000
  - Hyndman & Athanasopoulos, *Forecasting: Principles and Practice*
  - Une référence sur les systèmes multi-agents à base de LLM
  - Une référence sur la génération augmentée par recherche (RAG)
  - Une référence sur la gestion des stocks (EOQ, stock de sécurité, niveau de service)
- Annexes : extraits de prompts, dictionnaire de données complet, backlog intégral

### Règles de cohérence à tenir sur tout le rapport
1. **Un identifiant, un sens** : OM-*n*, CSM-*n*, BF-*n*, BNF-*n*, OD-*n* sont définis au Ch. 2 et
   réutilisés tels quels ensuite. Jamais renumérotés.
2. **Aucun résultat chiffré avant le Ch. 5.** Les chapitres 2 à 4 annoncent des critères, pas des scores.
3. **Aucune capture d'écran avant le Ch. 6.**
4. **Chaque chapitre** s'ouvre sur une introduction annonçant son plan et se ferme sur une conclusion
   qui fait le lien avec le suivant.
5. **Chaque figure et chaque tableau** est appelé dans le texte (`voir figure~\ref{...}`) et commenté :
   une figure non commentée est une figure inutile.
6. **Vocabulaire** : s'en tenir aux termes définis dans le glossaire du Ch. 2.
