# Backlog produit — Système multi-agents de coaching commercial et d'optimisation des stocks

> Backlog complet du projet sur **6 mois** (12 sprints de 2 semaines), organisé en épics,
> user stories priorisées (méthode MoSCoW) et planification par sprint.

---

## 1. Épics du projet

| Épic | Intitulé | Besoins couverts |
|---|---|---|
| **EP-1** | Socle technique et données | — (prérequis) |
| **EP-2** | Tableaux de bord et visualisation | BF-1.1, BF-1.2, BF-5.1, BNF-4.x |
| **EP-3** | Prévisions et analyse des séries temporelles | BF-1.4, BF-5.2, BF-5.3 |
| **EP-4** | Assistant de coaching conversationnel | BF-4.1 à BF-4.4, BF-5.4 |
| **EP-5** | Agents d'analyse et de décision des stocks | BF-1.x, BF-2.x, BF-3.1 |
| **EP-6** | Contexte marché et stratégie | BF-1.3, BF-6.1, BF-6.2 |
| **EP-7** | Orchestration multi-agents | BF-8.1, BF-8.4, BNF-1.3, BNF-5.4 |
| **EP-8** | Contrôle, sécurité et validation humaine | BF-7.x, BF-3.2, BF-8.2, BNF-3.x |
| **EP-9** | Gestion des commandes et temps réel | BF-3.3, BF-6.3, BF-8.5 |
| **EP-10** | Qualité, robustesse et industrialisation | BF-8.3, BNF-1.x, BNF-2.x, BNF-5.x |

---

## 2. Backlog produit — user stories

Priorités MoSCoW : **M** = Must have, **S** = Should have, **C** = Could have.
Estimation en points de story (suite de Fibonacci).

### EP-1 — Socle technique et données

| ID | User story | Priorité | Pts | Sprint |
|---|---|---|---|---|
| US-1.1 | En tant que développeur, je veux une base PostgreSQL alimentée par l'historique des ventes (plusieurs années, granularité journalière) afin de disposer de données réalistes pour les agents. | M | 8 | S1 |
| US-1.2 | En tant que développeur, je veux un backend FastAPI structuré (routes, services, repositories) afin de poser les fondations de l'API. | M | 5 | S1 |
| US-1.3 | En tant que développeur, je veux un schéma de base versionné par migrations (Alembic) afin de garantir la reproductibilité de la base. | M | 5 | S1 |
| US-1.4 | En tant que développeur, je veux un squelette d'application Angular avec routage et authentification afin de servir de socle au frontend. | M | 5 | S2 |
| US-1.5 | En tant que développeur, je veux enrichir les données de features saisonnières (jours fériés, Ramadan, rentrée, promotions) afin d'alimenter les analyses. | M | 8 | S2 |

### EP-2 — Tableaux de bord et visualisation

| ID | User story | Priorité | Pts | Sprint |
|---|---|---|---|---|
| US-2.1 | En tant que gestionnaire de stock, je veux consulter l'état des stocks par point de vente (quantités, couverture, rotation) afin d'évaluer la situation. | M | 8 | S3 |
| US-2.2 | En tant que gestionnaire de stock, je veux des indicateurs visuels de risque (rupture, insuffisant, surstock) afin d'identifier immédiatement les produits critiques. | M | 5 | S3 |
| US-2.3 | En tant que conseiller de vente, je veux un tableau de bord des ventes (volumes, tendances, comparaisons) afin de suivre la performance de mon point de vente. | M | 8 | S3 |
| US-2.4 | En tant qu'utilisateur, je veux des indicateurs de performance (KPIs) synthétiques en tête de page afin de comprendre la situation en un coup d'œil. | S | 3 | S4 |
| US-2.5 | En tant qu'utilisateur mobile, je veux une interface responsive afin d'utiliser l'application sur tablette en boutique. | S | 5 | S10 |
| US-2.6 | En tant qu'utilisateur, je veux des états de chargement clairs (squelettes) et des messages d'erreur explicites afin de comprendre ce que fait l'application. | S | 3 | S10 |

### EP-3 — Prévisions et analyse des séries temporelles

| ID | User story | Priorité | Pts | Sprint |
|---|---|---|---|---|
| US-3.1 | En tant qu'utilisateur, je veux visualiser des prévisions de demande par produit afin d'anticiper les besoins en stock. | M | 8 | S4 |
| US-3.2 | En tant que développeur, je veux un moteur de prévision statistique (Holt-Winters saisonnier) avec backtest (WAPE) afin de produire des prévisions fiables et mesurables. | M | 13 | S4 |
| US-3.3 | En tant que développeur, je veux intégrer un modèle de fondation de séries temporelles (TimesFM) avec repli statistique afin d'améliorer la précision quand il est disponible. | S | 8 | S5 |
| US-3.4 | En tant qu'utilisateur, je veux une détection automatique des anomalies de vente (pics, chutes, ruptures de tendance) afin d'être alerté des comportements atypiques. | M | 8 | S9 |
| US-3.5 | En tant qu'utilisateur, je veux une décomposition des séries (tendance, saisonnalité) et la vélocité par produit afin de comprendre la dynamique des ventes. | S | 8 | S9 |
| US-3.6 | En tant que développeur, je veux un benchmark des méthodes de prévision (statistique vs modèle de fondation) afin de choisir la méthode par défaut sur des mesures. | S | 5 | S11 |

### EP-4 — Assistant de coaching conversationnel

| ID | User story | Priorité | Pts | Sprint |
|---|---|---|---|---|
| US-4.1 | En tant que conseiller, je veux poser des questions en langage naturel à un assistant afin d'obtenir de l'aide pendant la vente. | M | 13 | S3 |
| US-4.2 | En tant que conseiller, je veux que la réponse s'affiche progressivement (streaming) afin de ne pas attendre la génération complète. | M | 5 | S3 |
| US-4.3 | En tant que conseiller, je veux des scripts et arguments de vente adaptés au contexte (produit, heure, client) issus d'une base de connaissances (RAG) afin de mieux argumenter. | M | 8 | S4 |
| US-4.4 | En tant que conseiller, je veux des produits recommandés avec un score et une justification afin de savoir quoi proposer en priorité. | M | 8 | S7 |
| US-4.5 | En tant que conseiller, je veux que le coaching tienne compte de mon profil et de mon historique afin de recevoir des conseils personnalisés. | S | 5 | S5 |
| US-4.6 | En tant que développeur, je veux un repli automatique vers un corpus local quand la base vectorielle est indisponible afin de garantir la continuité du RAG. | M | 5 | S11 |

### EP-5 — Agents d'analyse et de décision des stocks

| ID | User story | Priorité | Pts | Sprint |
|---|---|---|---|---|
| US-5.1 | En tant que développeur, je veux un agent d'analyse des stocks (couverture, rotation, classification des risques) afin d'automatiser le diagnostic. | M | 13 | S5 |
| US-5.2 | En tant que développeur, je veux un agent de contexte (prévisions, promotions, événements) afin d'enrichir le diagnostic avec la demande future. | M | 8 | S5 |
| US-5.3 | En tant que gestionnaire, je veux des recommandations d'action par produit (commander, accélérer, maintenir, surveiller) afin de préparer mes décisions. | M | 13 | S6 |
| US-5.4 | En tant que gestionnaire, je veux une quantité de commande recommandée par produit afin de préparer mes bons de commande. | M | 8 | S6 |
| US-5.5 | En tant que gestionnaire, je veux que les recommandations s'adaptent aux objectifs du magasin (disponibilité vs coût de stockage) afin de refléter mes priorités. | S | 5 | S6 |
| US-5.6 | En tant que gestionnaire, je veux une explication compréhensible de chaque recommandation afin de comprendre la décision proposée. | M | 5 | S6 |
| US-5.7 | En tant que développeur, je veux exposer les outils d'inventaire via un serveur MCP (stock, prévisions, métriques, commandes) afin de les rendre interopérables. | S | 8 | S8 |

### EP-6 — Contexte marché et stratégie

| ID | User story | Priorité | Pts | Sprint |
|---|---|---|---|---|
| US-6.1 | En tant qu'utilisateur, je veux consulter les événements datés (festivals, périodes particulières) et les offres actives afin d'anticiper leur impact sur la demande. | M | 8 | S7 |
| US-6.2 | En tant que développeur, je veux un agent stratège qui distingue les événements en cours et à venir afin de contextualiser les recommandations. | M | 8 | S7 |
| US-6.3 | En tant que manager, je veux des recommandations stratégiques combinant ventes et stocks (ne pas promouvoir un produit en rupture) afin d'aligner les deux domaines. | M | 13 | S8 |
| US-6.4 | En tant que développeur, je veux un collecteur d'événements externes (scraping + base locale) afin d'alimenter le contexte marché. | C | 5 | S11 |

### EP-7 — Orchestration multi-agents

| ID | User story | Priorité | Pts | Sprint |
|---|---|---|---|---|
| US-7.1 | En tant que développeur, je veux un état partagé unifié (RetailState) entre tous les agents afin de fusionner leurs contributions sans conflit. | M | 8 | S7 |
| US-7.2 | En tant que développeur, je veux un superviseur LangGraph exécutant les branches d'analyse en parallèle (ventes, connaissances, contexte, stocks) afin de réduire la latence globale. | M | 13 | S8 |
| US-7.3 | En tant que développeur, je veux un nœud de fusion des sorties avec reducers afin de produire une réponse unique et cohérente. | M | 8 | S8 |
| US-7.4 | En tant que développeur, je veux le traçage complet des exécutions du graphe (entrées, sorties, durées, coûts) afin d'analyser et d'améliorer le système. | M | 5 | S8 |
| US-7.5 | En tant que développeur, je veux un repli automatique entre fournisseurs de modèles de langage afin d'assurer la continuité du service. | M | 5 | S9 |

### EP-8 — Contrôle, sécurité et validation humaine

| ID | User story | Priorité | Pts | Sprint |
|---|---|---|---|---|
| US-8.1 | En tant qu'utilisateur, je veux m'authentifier et n'accéder qu'aux données de mon rôle et de mon point de vente afin de protéger les données. | M | 8 | S2 |
| US-8.2 | En tant que gestionnaire, je veux accepter, modifier ou refuser chaque recommandation avant application (Human-in-the-Loop) afin de garder le contrôle des décisions. | M | 8 | S6 |
| US-8.3 | En tant que développeur, je veux un agent de contrôle (Guardrail) qui vérifie chaque réponse générée (conformité, contenus sensibles) afin de fiabiliser l'assistant. | M | 13 | S9 |
| US-8.4 | En tant que conseiller, je veux voir le statut de vérification de chaque réponse (badge) afin de connaître son niveau de fiabilité. | S | 3 | S10 |
| US-8.5 | En tant que développeur, je veux une réponse de repli sûre quand le contrôle bloque une réponse afin d'éviter tout contenu risqué. | M | 5 | S9 |
| US-8.6 | En tant que développeur, je veux une limitation de débit sur les endpoints exposés afin de protéger le service et de maîtriser les coûts. | S | 3 | S6 |

### EP-9 — Gestion des commandes et temps réel

| ID | User story | Priorité | Pts | Sprint |
|---|---|---|---|---|
| US-9.1 | En tant que gestionnaire, je veux un tableau Kanban des commandes (suggéré, en attente, livraison, reçu) afin de suivre leur cycle de vie. | M | 8 | S11 |
| US-9.2 | En tant que gestionnaire, je veux que le système crée automatiquement des suggestions de commande sur le Kanban à partir des recommandations validées afin de fermer la boucle. | M | 8 | S11 |
| US-9.3 | En tant qu'utilisateur, je veux recevoir les alertes de stock en temps réel (WebSocket) afin de réagir sans recharger la page. | M | 5 | S10 |
| US-9.4 | En tant que développeur, je veux un bus d'alertes (Redis) déclenchant automatiquement un cycle d'analyse afin de rendre le système proactif. | M | 8 | S11 |
| US-9.5 | En tant que gestionnaire, je veux que la réception d'une commande mette à jour le stock afin de garder des données cohérentes. | M | 5 | S12 |
| US-9.6 | En tant qu'utilisateur, je veux voir les mouvements du Kanban en temps réel afin de partager le même état avec mes collègues. | S | 5 | S12 |

### EP-10 — Qualité, robustesse et industrialisation

| ID | User story | Priorité | Pts | Sprint |
|---|---|---|---|---|
| US-10.1 | En tant que développeur, je veux des tests unitaires et d'intégration sur les agents et l'API afin de sécuriser les évolutions. | M | 8 | S6–S12 |
| US-10.2 | En tant que développeur, je veux une chaîne d'intégration continue (lint, tests, build) afin de détecter les régressions à chaque commit. | S | 5 | S8 |
| US-10.3 | En tant que développeur, je veux des tests bout-en-bout du parcours utilisateur (Playwright) afin de valider les écrans critiques. | S | 5 | S10 |
| US-10.4 | En tant que gestionnaire, je veux que mes retours sur les recommandations (accepté, modifié, refusé) soient enregistrés afin d'améliorer les suggestions futures. | S | 8 | S11 |
| US-10.5 | En tant que développeur, je veux une suite d'évaluation des réponses générées (bancs de test, juge automatique) afin de mesurer la qualité du système. | S | 8 | S12 |
| US-10.6 | En tant que développeur, je veux consolider l'architecture en un package unique versionné (migrations comme source de vérité, zéro DDL à l'exécution) afin de fiabiliser les déploiements. | M | 13 | S12 |

---

## 3. Planification des sprints (6 mois — 12 sprints de 2 semaines)

| Sprint | Période | Objectif de sprint | Livrables principaux |
|---|---|---|---|
| **S1** | Mois 1 (sem. 1–2) | Socle données et backend | Base PostgreSQL alimentée (historique ventes), API FastAPI structurée, migrations Alembic |
| **S2** | Mois 1 (sem. 3–4) | Socle frontend et sécurité | Squelette Angular, authentification JWT + rôles, enrichissement saisonnier des données |
| **S3** | Mois 2 (sem. 1–2) | Premiers écrans + chat | Dashboards stocks et ventes avec indicateurs de risque, premier assistant conversationnel en streaming SSE |
| **S4** | Mois 2 (sem. 3–4) | Prévisions et connaissances | Moteur de prévision Holt-Winters + backtest, base de scripts de vente (RAG), KPIs synthétiques |
| **S5** | Mois 3 (sem. 1–2) | Agents d'analyse des stocks | Agents Analysis et Context, profil conseiller, intégration TimesFM avec repli |
| **S6** | Mois 3 (sem. 3–4) | Décision et validation humaine | Agent Decision (action + quantité + explication), porte HITL, rate limiting, premiers tests |
| **S7** | Mois 4 (sem. 1–2) | Contexte marché et état unifié | Agent Stratège (événements, offres), RetailState unifié, produits recommandés scorés |
| **S8** | Mois 4 (sem. 3–4) | Orchestration multi-agents | Superviseur LangGraph (branches parallèles + fusion), recommandations cross-domaine, traçabilité Langfuse, serveur MCP, CI |
| **S9** | Mois 5 (sem. 1–2) | Contrôle et robustesse | Agent Guardrail + repli sûr, fallback multi-fournisseurs LLM, détection d'anomalies et outils séries temporelles |
| **S10** | Mois 5 (sem. 3–4) | Temps réel et expérience utilisateur | WebSocket alertes + badge guardrail, responsive mobile, squelettes de chargement, tests E2E |
| **S11** | Mois 6 (sem. 1–2) | Boucle proactive et Kanban | Bus d'alertes → cycles automatiques, Kanban commandes + suggestions automatiques, boucle de feedback, repli RAG local, benchmark prévisions |
| **S12** | Mois 6 (sem. 3–4) | Industrialisation et évaluation | Consolidation architecture (package unique, migrations source de vérité), fermeture boucle stock à réception, Kanban temps réel, suite d'évaluation, stabilisation et documentation |

### Répartition de la charge par mois

| Mois | Sprints | Thème dominant | Points |
|---|---|---|---|
| Mois 1 | S1–S2 | Fondations (données, API, frontend, auth) | ~31 |
| Mois 2 | S3–S4 | Visualisation, chat, prévisions, RAG | ~55 |
| Mois 3 | S5–S6 | Agents stocks + décision + HITL | ~73 |
| Mois 4 | S7–S8 | Stratégie + orchestration multi-agents | ~76 |
| Mois 5 | S9–S10 | Guardrail, robustesse, temps réel, UX | ~57 |
| Mois 6 | S11–S12 | Proactivité, Kanban, feedback, industrialisation | ~75 |

---

## 4. Définition de terminé (Definition of Done)

Une user story est considérée comme terminée lorsque :

1. le code est développé, revu et fusionné sur la branche principale ;
2. les tests associés (unitaires et/ou intégration) passent en intégration continue ;
3. la fonctionnalité est vérifiée de bout en bout sur l'environnement de développement ;
4. les besoins non fonctionnels applicables sont respectés (latence, gestion d'erreurs, droits d'accès) ;
5. la documentation (README, migrations, schémas) est mise à jour si nécessaire.
