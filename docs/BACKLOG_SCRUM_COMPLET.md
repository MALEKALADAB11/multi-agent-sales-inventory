# Backlog Produit Complet — Méthodologie Scrum

**Projet :** Moteur agentique Retail temps réel — Coaching commercial & Optimisation des stocks (Ooredoo Tunisie)
**Périmètre :** Backend FastAPI multi-agents (`multi-agent-sales-inventory`) + Frontend Angular (`PFE`)
**Cadence :** 12 sprints de 2 semaines (6 mois) · Estimation en points de story (Fibonacci)
**Priorisation :** MoSCoW (M/S/C/W) croisée avec la valeur métier (WSJF simplifié)

---

## 1. Vision produit

> **Pour** les conseillers de vente, gestionnaires de stock et managers des boutiques Ooredoo,
> **qui** doivent vendre mieux et éviter ruptures et surstocks dans un contexte de forte saisonnalité (Ramadan, rentrée, festivals),
> **le système** est une plateforme multi-agents temps réel
> **qui** combine coaching conversationnel contextualisé (RAG + LLM), prévision de la demande (séries temporelles), recommandations de réapprovisionnement expliquées et validation humaine (HITL),
> **contrairement aux** tableurs et outils BI statiques,
> **notre produit** est proactif (bus d'alertes → cycles d'analyse automatiques), traçable (Langfuse), gouverné (Guardrail, RBAC) et ferme la boucle jusqu'à la réception des commandes (Kanban).

### Objectifs mesurables (OKR produit)

| Objectif | Résultat clé | Cible |
|---|---|---|
| Réduire les ruptures de stock | Taux de rupture sur produits classe A | −30 % |
| Améliorer la précision des prévisions | WAPE Holt-Winters backtesté | ≤ 5 % (atteint : 4,4 %) |
| Accélérer la décision de réappro | Délai alerte → PO suggéré sur Kanban | < 2 min (temps réel) |
| Fiabiliser l'assistant | Réponses validées par le Guardrail | 100 % des réponses contrôlées |
| Garder l'humain dans la boucle | PO appliqués sans validation humaine | 0 (porte HITL obligatoire) |

---

## 2. Personas

| Persona | Rôle | Besoins clés |
|---|---|---|
| **Amina** — Conseillère de vente | Boutique, face client | Scripts d'argumentation contextualisés, produits à pousser (scorés), réponses rapides en streaming, mobile/tablette |
| **Karim** — Gestionnaire de stock | Back-office boutique | État des stocks et risques, quantités à commander expliquées, Kanban des PO, alertes temps réel, validation/refus des suggestions |
| **Sonia** — Manager régional | Multi-boutiques | KPIs consolidés, recommandations stratégiques cross-domaine (ventes × stocks), impact des événements/offres |
| **Yassine** — Administrateur / Ops | IT | Monitoring des agents, traces, timeline temps réel, gestion des accès (RBAC), santé des services |
| **Équipe Dev** | Développement | Migrations reproductibles, CI/CD, observabilité, tests, replis (LLM, RAG, forecast) |

---

## 3. Cadre Scrum

### 3.1 Rôles et cérémonies

| Élément | Modalité |
|---|---|
| Product Owner | Priorise le backlog, arbitre MoSCoW, valide en Sprint Review |
| Scrum Master | Facilite, lève les obstacles (ex. réseau bloquant ooredoo.tn, DLL torch) |
| Équipe de développement | Full-stack (FastAPI/LangGraph + Angular 21/Signals) |
| Sprint Planning | 2 h en début de sprint — sélection selon vélocité constatée (~30–38 pts) |
| Daily Scrum | 15 min — avancement, obstacles |
| Sprint Review | 1 h — démo bout-en-bout sur environnement de dev (smoke tests) |
| Rétrospective | 45 min — actions d'amélioration (ex. « Alembic = source de vérité unique » issue de la rétro S11) |
| Backlog Refinement | 1 h/semaine — découpage, critères d'acceptation, estimation en planning poker |

### 3.2 Definition of Ready (DoR)

Une story entre en sprint si :
1. elle est formulée « En tant que… je veux… afin de… » ;
2. ses critères d'acceptation sont écrits et testables ;
3. elle est estimée (≤ 13 pts, sinon découpée) ;
4. ses dépendances (données, API, migration, maquette) sont identifiées ;
5. l'impact frontend **et** backend est explicité si la story traverse les deux dépôts.

### 3.3 Definition of Done (DoD)

Une story est terminée lorsque :
1. le code est développé, revu (PR) et fusionné sur la branche principale ;
2. les tests associés passent en CI (pytest backend, Karma/Jasmine frontend, Playwright E2E si écran critique) ;
3. la fonctionnalité est vérifiée bout-en-bout (smoke test) sur l'environnement de dev ;
4. les exigences transverses sont respectées : latence, gestion d'erreurs, RBAC, pas de DDL à l'exécution, pas d'URL en dur ;
5. migrations Alembic et documentation à jour ;
6. traçabilité Langfuse en place pour tout nouveau nœud d'agent.

---

## 4. Cartographie des épics

| Épic | Intitulé | Dépôt(s) | Valeur métier |
|---|---|---|---|
| **EP-1** | Socle technique et données | Backend | Prérequis à tout |
| **EP-2** | Tableaux de bord et visualisation | Frontend + Backend | Visibilité immédiate |
| **EP-3** | Prévisions et analyse des séries temporelles | Backend | Anticipation de la demande |
| **EP-4** | Assistant de coaching conversationnel | Frontend + Backend | Aide à la vente en direct |
| **EP-5** | Agents d'analyse et de décision des stocks | Backend | Automatisation du diagnostic |
| **EP-6** | Contexte marché et stratégie | Backend + Frontend | Recommandations cross-domaine |
| **EP-7** | Orchestration multi-agents | Backend | Latence, cohérence, traçabilité |
| **EP-8** | Contrôle, sécurité et validation humaine | Frontend + Backend | Confiance et gouvernance |
| **EP-9** | Commandes (Kanban) et temps réel | Frontend + Backend | Fermeture de la boucle stock |
| **EP-10** | Expérience utilisateur frontend | Frontend | Adoption terrain |
| **EP-11** | Qualité, robustesse et industrialisation | Backend + Frontend | Pérennité |
| **EP-12** | Évaluation LLM et amélioration continue | Backend | Mesure de la qualité IA |

---

## 5. Backlog produit détaillé

Chaque story : **ID · story · priorité MoSCoW · points · sprint · critères d'acceptation (CA)**.

### EP-1 — Socle technique et données

| ID | User story | Prio | Pts | Sprint |
|---|---|---|---|---|
| US-1.1 | En tant que développeur, je veux une base PostgreSQL alimentée par l'historique des ventes (1,49 M lignes journalières, 4,5 ans) afin de disposer de données réalistes pour les agents. | M | 8 | S1 |
| US-1.2 | En tant que développeur, je veux un backend FastAPI structuré (routes → services → repositories) afin de poser les fondations de l'API. | M | 5 | S1 |
| US-1.3 | En tant que développeur, je veux un schéma versionné par migrations Alembic, unique source de vérité (zéro DDL à l'exécution), afin de garantir la reproductibilité. | M | 5 | S1 |
| US-1.4 | En tant que développeur, je veux un squelette Angular (standalone components, Signals, routage, layout navbar/sidebar) afin de servir de socle au frontend. | M | 5 | S2 |
| US-1.5 | En tant que développeur, je veux enrichir les données de features saisonnières (fériés, Ramadan, rentrée, promotions) afin d'alimenter les analyses. | M | 8 | S2 |
| US-1.6 | En tant que développeur, je veux une configuration d'environnements frontend (dev/prod) sans URL en dur afin de déployer sans modifier le code. | M | 3 | S2 |

**CA US-1.3 :** `alembic upgrade head` sur base vide reproduit le schéma complet ; aucun `CREATE TABLE` dans le code applicatif ; FK vérifiées via `pg_constraint`.
**CA US-1.6 :** toutes les URLs API/WS proviennent de `environment.ts` ; build prod pointe vers l'URL de prod sans édition manuelle.

### EP-2 — Tableaux de bord et visualisation

| ID | User story | Prio | Pts | Sprint |
|---|---|---|---|---|
| US-2.1 | En tant que gestionnaire, je veux consulter l'état des stocks par point de vente (quantités, couverture, rotation) afin d'évaluer la situation. *(écran `inventory`)* | M | 8 | S3 |
| US-2.2 | En tant que gestionnaire, je veux des indicateurs visuels de risque (rupture, insuffisant, surstock) afin d'identifier les produits critiques. | M | 5 | S3 |
| US-2.3 | En tant que conseiller, je veux un tableau de bord des ventes (volumes horaires réels, tendances, atteinte d'objectif cohérente) afin de suivre ma performance. *(écran `dashboard`)* | M | 8 | S3 |
| US-2.4 | En tant qu'utilisateur, je veux des KPIs synthétiques animés en tête de page (`flip-kpi-card`, `metric-card`, `progress-bar`) afin de comprendre la situation en un coup d'œil. | S | 3 | S4 |
| US-2.5 | En tant que manager, je veux un écran conseiller (`conseiller`) avec profil, historique et lignes conseillers (`advisor-row`) afin de suivre l'équipe. | S | 5 | S5 |
| US-2.6 | En tant qu'utilisateur, je veux que le graphique de prévision affiche la sortie réelle du moteur TS (et non des données simulées) afin de me fier au dashboard. | M | 5 | S12 |

**CA US-2.3 :** les ventes horaires proviennent de la chaîne réelle nœud analyste → whitelist → RetailState → payload ; l'attainment affiché est cohérent avec l'objectif du magasin.

### EP-3 — Prévisions et séries temporelles

| ID | User story | Prio | Pts | Sprint |
|---|---|---|---|---|
| US-3.1 | En tant qu'utilisateur, je veux visualiser des prévisions de demande par produit afin d'anticiper les besoins. | M | 8 | S4 |
| US-3.2 | En tant que développeur, je veux un moteur Holt-Winters saisonnier avec backtest WAPE et gap horaire déterministe afin de produire des prévisions fiables et mesurables, LLM hors chemin critique. | M | 13 | S4 |
| US-3.3 | En tant que développeur, je veux intégrer un modèle de fondation TS (TimesFM, préchargé) avec repli statistique automatique afin d'améliorer la précision quand il est disponible. | S | 8 | S5 |
| US-3.4 | En tant qu'utilisateur, je veux une détection automatique d'anomalies (pics, chutes, ruptures de tendance) afin d'être alerté des comportements atypiques. | M | 8 | S9 |
| US-3.5 | En tant qu'utilisateur, je veux la décomposition des séries (tendance, saisonnalité) et la vélocité produit afin de comprendre la dynamique des ventes. | S | 8 | S9 |
| US-3.6 | En tant que développeur, je veux un benchmark des méthodes (Holt-Winters vs Prophet vs fondation) sur des métriques communes (SUM, pas AVG) afin de choisir la méthode par défaut sur mesure. | S | 5 | S11 |

**CA US-3.2 :** WAPE ≤ 5 % sur le backtest de référence ; la prévision reste servie si le LLM est indisponible.
**CA US-3.3 :** si torch/TimesFM indisponible (`DISABLE_CHRONOS`), le repli statistique est transparent pour l'utilisateur.

### EP-4 — Assistant de coaching conversationnel

| ID | User story | Prio | Pts | Sprint |
|---|---|---|---|---|
| US-4.1 | En tant que conseillère, je veux poser des questions en langage naturel (écran `chat`) afin d'obtenir de l'aide pendant la vente. | M | 13 | S3 |
| US-4.2 | En tant que conseillère, je veux la réponse en streaming SSE progressif afin de ne pas attendre la génération complète. | M | 5 | S3 |
| US-4.3 | En tant que conseillère, je veux des scripts de vente contextualisés (produit, heure, client) issus d'un RAG (200+ scripts, Milvus + repli corpus local) afin de mieux argumenter. | M | 8 | S4 |
| US-4.4 | En tant que conseillère, je veux des produits recommandés avec score et justification (chips `scored_products`, cartes `coaching-card`) afin de savoir quoi proposer en priorité. | M | 8 | S7 |
| US-4.5 | En tant que conseillère, je veux un coaching tenant compte de mon profil et de mon historique (stockage conversation côté client) afin de recevoir des conseils personnalisés. | S | 5 | S5 |
| US-4.6 | En tant que développeur, je veux le Coach Stratège câblé côté serveur sur `/chat` et `/stream` (timeout borné, warm-up en arrière-plan, prompt cross-domaine ventes+stocks, Mistral primaire) afin d'unifier le coaching. | M | 8 | S11 |
| US-4.7 | En tant que développeur, je veux un repli automatique vers le corpus local quand Milvus est indisponible afin de garantir la continuité du RAG. | M | 5 | S11 |

**CA US-4.2 :** premier token < 2 s en conditions normales ; interruption propre du flux à la fermeture de la connexion.
**CA US-4.6 :** timeout borné — si le stratège dépasse le budget, la réponse coach de base part sans lui ; aucun blocage de l'event loop.

### EP-5 — Agents d'analyse et de décision des stocks

| ID | User story | Prio | Pts | Sprint |
|---|---|---|---|---|
| US-5.1 | En tant que développeur, je veux un agent Analysis (couverture, rotation, classification des risques) afin d'automatiser le diagnostic. | M | 13 | S5 |
| US-5.2 | En tant que développeur, je veux un agent Context (prévisions, promotions, événements) afin d'enrichir le diagnostic avec la demande future. | M | 8 | S5 |
| US-5.3 | En tant que gestionnaire, je veux des recommandations d'action par produit (commander, accélérer, maintenir, surveiller) afin de préparer mes décisions. | M | 13 | S6 |
| US-5.4 | En tant que gestionnaire, je veux une quantité de commande recommandée par produit (chaîne causale via `supplier_products`) afin de préparer mes bons de commande. | M | 8 | S6 |
| US-5.5 | En tant que gestionnaire, je veux que les recommandations s'adaptent aux objectifs du magasin (disponibilité vs coût de stockage) afin de refléter mes priorités. | S | 5 | S6 |
| US-5.6 | En tant que gestionnaire, je veux une explication compréhensible de chaque recommandation afin de comprendre la décision proposée. | M | 5 | S6 |
| US-5.7 | En tant que développeur, je veux exposer les outils d'inventaire via un serveur MCP maison (stock, prévisions, métriques, PO, Kanban) afin de les rendre interopérables, porte HITL préservée. | S | 8 | S8 |

**CA US-5.7 :** aucun outil MCP ne peut faire passer un PO au-delà du statut nécessitant validation humaine.

### EP-6 — Contexte marché et stratégie

| ID | User story | Prio | Pts | Sprint |
|---|---|---|---|---|
| US-6.1 | En tant qu'utilisateur, je veux consulter les événements datés (festivals, concerts) et les offres actives (modal `active_offers`) afin d'anticiper leur impact sur la demande. | M | 8 | S7 |
| US-6.2 | En tant que développeur, je veux que le stratège distingue événements **en cours** et **à venir** dans son contexte afin de contextualiser correctement. | M | 8 | S7 |
| US-6.3 | En tant que manager, je veux des recommandations stratégiques cross-domaine (ne pas promouvoir un produit en rupture) afin d'aligner ventes et stocks. | M | 13 | S8 |
| US-6.4 | En tant que développeur, je veux un collecteur d'événements externes (scraper + seed local, cache `ooredoo_events.json`) afin d'alimenter `market.events` malgré les blocages réseau. | C | 5 | S11 |
| US-6.5 | En tant que manager, je veux que les StrateActions soient rendues dans l'interface (et pas seulement présentes dans le payload) afin d'agir sur les recommandations stratégiques. | M | 5 | S12 |

### EP-7 — Orchestration multi-agents

| ID | User story | Prio | Pts | Sprint |
|---|---|---|---|---|
| US-7.1 | En tant que développeur, je veux un état partagé unifié `RetailState` avec reducers (les nœuds n'émettent que des deltas) afin de fusionner les contributions sans conflit ni écrasement. | M | 8 | S7 |
| US-7.2 | En tant que développeur, je veux un SupervisorAgent LangGraph exécutant les branches en parallèle (ventes, connaissances, contexte, stocks) afin de réduire la latence globale. | M | 13 | S8 |
| US-7.3 | En tant que développeur, je veux un nœud de fusion + `constraints_check` afin de produire une réponse unique et cohérente. | M | 8 | S8 |
| US-7.4 | En tant que développeur, je veux le traçage complet des exécutions (entrées, sorties, durées, coûts) via Langfuse afin d'analyser le système. | M | 5 | S8 |
| US-7.5 | En tant que développeur, je veux un repli automatique entre fournisseurs LLM (factory multi-providers) afin d'assurer la continuité de service. | M | 5 | S9 |
| US-7.6 | En tant que développeur, je veux que les opérations bloquantes (enrichissement RAG) s'exécutent hors event loop afin de ne jamais figer le serveur. | M | 5 | S11 |

**CA US-7.2 :** latence du graphe complet < somme des branches séquentielles ; timeline visible dans l'écran admin (`agent-timeline`).

### EP-8 — Contrôle, sécurité et validation humaine

| ID | User story | Prio | Pts | Sprint |
|---|---|---|---|---|
| US-8.1 | En tant qu'utilisateur, je veux m'authentifier (JWT, écran `login`) et n'accéder qu'aux données de mon rôle et de mon point de vente (RBAC store-level) afin de protéger les données. | M | 8 | S2 |
| US-8.2 | En tant que gestionnaire, je veux accepter, modifier ou refuser chaque recommandation avant application (panneau `hitl-panel`) afin de garder le contrôle. | M | 8 | S6 |
| US-8.3 | En tant que développeur, je veux un agent Guardrail qui vérifie chaque réponse générée (conformité, contenus sensibles) câblé sur `/chat` afin de fiabiliser l'assistant. | M | 13 | S9 |
| US-8.4 | En tant que conseillère, je veux un badge de statut de vérification sur chaque réponse (`agent-status-badge`) et des événements guardrail en WebSocket afin de connaître la fiabilité. | S | 3 | S10 |
| US-8.5 | En tant que développeur, je veux une réponse de repli sûre quand le Guardrail bloque afin d'éviter tout contenu risqué. | M | 5 | S9 |
| US-8.6 | En tant que développeur, je veux une limitation de débit (slowapi) sur les endpoints exposés afin de protéger le service et les coûts. | S | 3 | S6 |
| US-8.7 | En tant qu'utilisateur, je veux un rafraîchissement de token transparent afin de ne pas être déconnecté en pleine session. | S | 3 | S10 |
| US-8.8 | En tant qu'admin, je veux un panneau de monitoring du Guardrail (taux de blocage, motifs) afin de surveiller la gouvernance. | S | 5 | S10 |

### EP-9 — Commandes (Kanban) et temps réel

| ID | User story | Prio | Pts | Sprint |
|---|---|---|---|---|
| US-9.1 | En tant que gestionnaire, je veux un Kanban des PO (SUGGÉRÉ → EN ATTENTE → LIVRAISON → REÇU, écran `purchase-board`) afin de suivre leur cycle de vie. | M | 8 | S11 |
| US-9.2 | En tant que gestionnaire, je veux que le DecisionAgent crée automatiquement des PO au statut SUGGÉRÉ sur le Kanban afin de fermer la boucle alerte → décision. | M | 8 | S11 |
| US-9.3 | En tant qu'utilisateur, je veux les alertes de stock en temps réel (WebSocket, feed inventory) afin de réagir sans recharger la page. | M | 5 | S10 |
| US-9.4 | En tant que développeur, je veux un AlertBus (Redis) déclenchant des cycles d'analyse événementiels afin de rendre le système proactif. | M | 8 | S11 |
| US-9.5 | En tant que gestionnaire, je veux que le passage d'un PO au statut REÇU mette à jour le stock afin de garder des données cohérentes. | M | 5 | S12 |
| US-9.6 | En tant qu'utilisateur, je veux voir les mouvements du Kanban en temps réel (multi-utilisateurs) afin de partager le même état avec mes collègues. | S | 5 | S12 |
| US-9.7 | En tant qu'admin, je veux une page temps réel (`realtime-page`) et un simulateur RT isolé (purge dédiée) afin de démontrer et tester les flux sans polluer les données. | S | 5 | S10 |

**CA US-9.2 :** aucune commande auto-appliquée — statut initial toujours SUGGÉRÉ ; l'approbation passe par le `hitl-panel`.
**CA US-9.5 :** transition REÇU idempotente ; le stock est incrémenté exactement une fois.

### EP-10 — Expérience utilisateur frontend

| ID | User story | Prio | Pts | Sprint |
|---|---|---|---|---|
| US-10.1 | En tant qu'utilisatrice mobile, je veux une interface responsive (breakpoints tablette/mobile) afin d'utiliser l'application en boutique. | S | 5 | S9 |
| US-10.2 | En tant qu'utilisateur, je veux des squelettes de chargement et un error boundary global afin de comprendre ce que fait l'application. | S | 3 | S9 |
| US-10.3 | En tant qu'utilisateur, je veux une couche service unifiée (`api`, `inventory-api`, `purchase-order-api`, `websocket`, `monitoring`) afin d'avoir un comportement réseau cohérent (retry, erreurs). | M | 5 | S4 |
| US-10.4 | En tant que développeur, je veux des données mock (`mock-data`) commutables afin de développer le frontend sans backend disponible. | C | 3 | S2 |
| US-10.5 | En tant qu'utilisatrice, je veux l'accessibilité de base (contrastes, navigation clavier, labels ARIA sur le chat et le Kanban) afin d'utiliser l'app dans toutes les conditions. | C | 5 | S12 |

### EP-11 — Qualité, robustesse et industrialisation

| ID | User story | Prio | Pts | Sprint |
|---|---|---|---|---|
| US-11.1 | En tant que développeur, je veux des tests unitaires et d'intégration backend (agents, API, guardrail — 80+ tests, 34 guardrail) afin de sécuriser les évolutions. | M | 8 | S6–S12 |
| US-11.2 | En tant que développeur, je veux des tests Angular (specs par composant/service) et E2E Playwright sur les parcours critiques afin de valider les écrans. | M | 8 | S8–S10 |
| US-11.3 | En tant que développeur, je veux une CI GitHub Actions (lint, tests, build back + front) afin de détecter les régressions à chaque commit. | S | 5 | S8 |
| US-11.4 | En tant que gestionnaire, je veux que mes retours (accepté/modifié/refusé) soient persistés (migration 0008) afin d'améliorer les suggestions futures. | S | 8 | S11 |
| US-11.5 | En tant que développeur, je veux consolider l'architecture en package `app/` unique (Alembic 0001–0007, zéro CSV/hardcode, fixes hangs Langfuse/Redis) afin de fiabiliser les déploiements. | M | 13 | S12 |
| US-11.6 | En tant que développeur, je veux un nettoyage de la base (suppression tables mortes, ajout FK) afin de réduire la dette de schéma. | S | 5 | S11 |
| US-11.7 | En tant qu'ops, je veux des caches Redis + index PostgreSQL sur les chemins chauds afin de tenir la latence cible. | M | 5 | S6 |

### EP-12 — Évaluation LLM et amélioration continue

| ID | User story | Prio | Pts | Sprint |
|---|---|---|---|---|
| US-12.1 | En tant que développeur, je veux une suite d'évaluation `evals/` (guardrail, benchmark modèles, coach E2E, juge automatique) afin de mesurer objectivement la qualité IA. | S | 8 | S12 |
| US-12.2 | En tant que développeur, je veux exposer le contexte serveur au juge LLM afin d'évaluer la fidélité des réponses au contexte réel. | C | 5 | Backlog |
| US-12.3 | En tant que PO, je veux un tableau de bord des métriques d'éval dans le temps afin de suivre les régressions qualité entre versions de prompts/modèles. | C | 5 | Backlog |

---

## 6. Planification des sprints (12 × 2 semaines)

| Sprint | Objectif de sprint | Stories | Pts |
|---|---|---|---|
| **S1** | Socle données et backend | US-1.1, 1.2, 1.3 | 18 |
| **S2** | Socle frontend et sécurité | US-1.4, 1.5, 1.6, 8.1, 10.4 | 27 |
| **S3** | Premiers écrans + chat streaming | US-2.1, 2.2, 2.3, 4.1, 4.2 | 39 |
| **S4** | Prévisions et connaissances | US-3.1, 3.2, 4.3, 2.4, 10.3 | 37 |
| **S5** | Agents d'analyse des stocks | US-5.1, 5.2, 4.5, 3.3, 2.5 | 39 |
| **S6** | Décision et validation humaine | US-5.3, 5.4, 5.5, 5.6, 8.2, 8.6, 11.7 | 42 |
| **S7** | Contexte marché et état unifié | US-6.1, 6.2, 7.1, 4.4 | 32 |
| **S8** | Orchestration multi-agents | US-7.2, 7.3, 7.4, 6.3, 5.7, 11.3 | 52 |
| **S9** | Contrôle et robustesse | US-8.3, 8.5, 7.5, 3.4, 3.5, 10.1, 10.2 | 47 |
| **S10** | Temps réel et gouvernance UI | US-9.3, 8.4, 8.7, 8.8, 9.7, 11.2 | 29 |
| **S11** | Boucle proactive et Kanban | US-9.1, 9.2, 9.4, 4.6, 4.7, 3.6, 6.4, 7.6, 11.4, 11.6 | 65* |
| **S12** | Industrialisation et fermeture de boucle | US-9.5, 9.6, 6.5, 2.6, 11.5, 12.1, 10.5 | 46 |

\* S11 surchargé dans les faits (dette absorbée) — signal de rétro : lisser vers S10 (29 pts) au prochain cycle.

**Vélocité moyenne constatée : ~39 pts/sprint.**

### Burn-up par mois

| Mois | Sprints | Thème dominant | Pts cumulés |
|---|---|---|---|
| M1 | S1–S2 | Fondations (données, API, Angular, auth) | 45 |
| M2 | S3–S4 | Visualisation, chat, prévisions, RAG | 121 |
| M3 | S5–S6 | Agents stocks, décision, HITL | 202 |
| M4 | S7–S8 | Stratégie, orchestration | 286 |
| M5 | S9–S10 | Guardrail, robustesse, temps réel | 362 |
| M6 | S11–S12 | Proactivité, Kanban, industrialisation | 473 |

---

## 7. Backlog restant (post-S12) — priorisé

Éléments identifiés par les audits (architecture ~80 % → ~98 %) non encore soldés :

| ID | Item | Prio | Pts | Justification |
|---|---|---|---|---|
| BL-1 | Rendu complet des StrateActions dans l'UI (si non soldé en S12) | M | 5 | Valeur stratège invisible sinon |
| BL-2 | Réactivation Milvus + réindexation RAG (actuellement en repli corpus) | M | 5 | Qualité de récupération des scripts |
| BL-3 | Réparation environnement torch (DLL) ou conteneurisation pour réactiver TimesFM/Chronos | S | 8 | Benchmark fondation vs Holt (4,4 %) |
| BL-4 | HITL complet : modification de quantité avant approbation + journal d'audit des décisions | M | 8 | Gap audit « HITL incomplet » |
| BL-5 | Couverture de tests frontend cible ≥ 60 % (services WS, Kanban drag & drop) | S | 8 | Dette test historique côté Angular |
| BL-6 | Exposition du contexte serveur au juge d'éval (US-12.2) + dashboard qualité (US-12.3) | C | 10 | Boucle d'amélioration continue |
| BL-7 | Notifications push / e-mail sur alertes critiques (hors session ouverte) | C | 5 | Réactivité hors application |
| BL-8 | Multi-boutiques consolidé pour le persona manager (agrégats régionaux) | C | 8 | Extension du RBAC store-level |
| BL-9 | Déploiement conteneurisé complet (Docker Compose : API, Postgres, Redis, Milvus) + runbook | S | 8 | Reproductibilité hors poste de dev |

---

## 8. Risques et plans de mitigation

| Risque | Prob. | Impact | Mitigation en place |
|---|---|---|---|
| Indisponibilité fournisseur LLM | Moyenne | Fort | Factory multi-providers avec repli automatique (US-7.5), Mistral primaire |
| Milvus/base vectorielle down | Avérée | Moyen | Repli corpus local (US-4.7) |
| Environnement ML cassé (DLL torch) | Avérée | Moyen | `DISABLE_CHRONOS` + repli Holt-Winters ; conteneurisation (BL-3) |
| Réseau sortant bloqué (scraping ooredoo.tn) | Avérée | Faible | Seed local + cache d'événements (US-6.4) |
| Dérive du schéma DB | Avérée (77→50 tables) | Fort | Alembic source de vérité unique, zéro DDL runtime (US-1.3, 11.5) |
| Serveur figé par appels bloquants | Avérée | Fort | Travaux hors event loop, py-spy en diagnostic (US-7.6) |
| Commande auto sans contrôle humain | — | Critique | Porte HITL structurelle : statut SUGGÉRÉ obligatoire (US-9.2, 5.7) |
| Réponse LLM non conforme | Moyenne | Fort | Guardrail 100 % des réponses + repli sûr (US-8.3, 8.5) |

---

## 9. Traçabilité épics ↔ écrans frontend

| Écran / composant Angular | Épics servis |
|---|---|
| `features/dashboard` | EP-2, EP-3 |
| `features/inventory` | EP-2, EP-5, EP-9 |
| `features/chat` (+ `coaching-card`, `agent-status-badge`) | EP-4, EP-8 |
| `features/conseiller` (+ `advisor-row`) | EP-2, EP-4 |
| `features/purchase-board` | EP-9, EP-8 (HITL) |
| `features/monitoring` (+ `monitoring.service`, `monitoring-adapter`) | EP-7, EP-8, EP-12 |
| `features/admin` (`agent-timeline`, `realtime-page`) | EP-7, EP-9 |
| `features/auth/login` | EP-8 |
| `shared/hitl-panel` | EP-8, EP-9 |
| `core/services/websocket.service` | EP-9, EP-8 |
| `core/services/evaluation-kpi.service` | EP-12 |

---

*Document généré le 2026-07-18 — à faire vivre en Backlog Refinement hebdomadaire.*
