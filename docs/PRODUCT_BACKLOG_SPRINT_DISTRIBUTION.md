# Product Backlog & Sprint Distribution

**Projet :** Moteur agentique Retail temps réel — Coaching commercial & Optimisation des stocks
**Périmètre :** Backend FastAPI multi-agents + Frontend Angular (PFE)
**Priorités MoSCoW :** M = Must have · S = Should have · C = Could have · W = Won't have (hors périmètre)
**Estimation :** points de story, suite de Fibonacci (1, 2, 3, 5, 8, 13)
**Cadence :** 12 sprints de 2 semaines (6 mois)

### Décisions de refinement

| Décision | Justification |
|---|---|
| US-04 et US-05 **fusionnées dans US-03** | Le tableau de bord commercial inclut nativement le CA temps réel et l'écart réalisé/objectif — une seule story livrable. |
| US-42 **fusionnée dans US-41** | « État des stocks » et « quantité disponible » constituent le même incrément fonctionnel. |
| US-83/84/85 conservées distinctes de US-66/67/68 | Deux portes HITL volontairement séparées : validation des **commandes** (Kanban) vs validation des **recommandations escaladées** (Guardrail). |
| EN-47 et EN-48 déplacés en **Livrables académiques** | Ce ne sont pas des enablers produit mais des livrables PFE (rapport, démo). |
| EN-40 à EN-43 marqués **transverses (S8–S12)** | Tests et corrections sont des activités continues, pas des items d'un seul sprint. |
| US-89 et US-90 (priorité C) placés en **backlog post-projet** | Capacité des 12 sprints réservée aux M/S ; les C non critiques sortent du plan. |

---

## 1. Product Backlog — User Stories

| ID | User Story | Priorité | Points | Sprint |
| ----- | --- | :------: | :---: | :---: |
| US-01 | En tant que conseiller, je veux me connecter à l'application afin d'accéder aux fonctionnalités correspondant à mon rôle et à ma boutique. | M | 3 | S2 |
| US-02 | En tant que manager, je veux me connecter à l'application afin d'accéder aux fonctions de supervision, de stock et de validation. | M | 2 | S2 |
| US-03 | En tant que conseiller, je veux consulter mon tableau de bord commercial (chiffre d'affaires en temps réel, écart réalisé/objectif, taux d'atteinte) afin de suivre ma performance actuelle. *(fusion US-03 + US-04 + US-05)* | M | 5 | S2 |
| US-04 | *Fusionnée dans US-03.* | — | — | — |
| US-05 | *Fusionnée dans US-03.* | — | — | — |
| US-06 | En tant que conseiller, je veux consulter la prévision de chiffre d'affaires de fin de journée afin d'anticiper l'atteinte de mon objectif. | M | 2 | S3 |
| US-07 | En tant que conseiller, je veux consulter les prévisions des prochaines heures afin d'adapter mes actions commerciales. | M | 2 | S3 |
| US-08 | En tant que conseiller, je veux être alerté lorsqu'une anomalie de vente est détectée afin de réagir rapidement. | M | 2 | S3 |
| US-09 | En tant que conseiller, je veux connaître la tendance des ventes afin de savoir si l'activité accélère, ralentit ou reste stable. | S | 2 | S3 |
| US-10 | En tant que conseiller, je veux visualiser les produits les plus performants afin de mieux orienter mes propositions commerciales. | M | 2 | S3 |
| US-11 | En tant que conseiller, je veux poser une question en langage naturel au Coach afin d'obtenir une assistance pendant la vente. | M | 5 | S3 |
| US-12 | En tant que conseiller, je veux recevoir la réponse du Coach progressivement afin de réduire le temps d'attente. | M | 3 | S3 |
| US-13 | En tant que conseiller, je veux recevoir des recommandations de produits prioritaires afin de savoir quoi proposer au client. | M | 2 | S5 |
| US-14 | En tant que conseiller, je veux voir un score associé à chaque produit recommandé afin de comprendre son niveau de priorité. | M | 2 | S5 |
| US-15 | En tant que conseiller, je veux obtenir une justification pour chaque produit recommandé afin de comprendre la logique de la recommandation. | M | 2 | S5 |
| US-16 | En tant que conseiller, je veux recevoir un argumentaire de vente adapté à chaque produit afin d'améliorer mon discours commercial. | M | 3 | S5 |
| US-17 | En tant que conseiller, je veux consulter des scripts de vente contextualisés afin de mieux traiter les besoins et objections du client. | M | 3 | S4 |
| US-18 | En tant que conseiller, je veux recevoir des recommandations tenant compte de l'heure, du contexte et de l'état de la boutique afin d'obtenir un coaching pertinent. | M | 5 | S5 |
| US-19 | En tant que conseiller, je veux recevoir un coaching personnalisé selon mon profil et mon historique afin d'obtenir des recommandations adaptées. | S | 3 | S5 |
| US-20 | En tant que conseiller, je veux recevoir un produit de substitution lorsqu'un produit demandé est indisponible afin de ne pas perdre la vente. | M | 3 | S5 |
| US-21 | En tant que conseiller, je veux être informé des produits à éviter afin de ne pas proposer un produit en rupture ou en quasi-rupture. | M | 2 | S5 |
| US-22 | En tant que conseiller, je veux consulter les offres commerciales actives afin de proposer les offres pertinentes au client. | M | 2 | S5 |
| US-23 | En tant que conseiller, je veux consulter les événements en cours ou à venir afin d'adapter mon argumentaire commercial. | S | 2 | S5 |
| US-24 | En tant que conseiller, je veux recevoir des recommandations tenant compte de la météo et des événements afin de mieux exploiter le contexte local. | S | 3 | S5 |
| US-25 | En tant que conseiller, je veux voir le statut de vérification d'une réponse afin de connaître son niveau de fiabilité. | M | 2 | S10 |
| US-26 | En tant que conseiller, je veux recevoir une réponse sûre lorsque la recommandation initiale est bloquée afin de toujours disposer d'une réponse exploitable. | M | 2 | S10 |
| US-27 | En tant que conseiller, je veux accéder à une interface responsive afin d'utiliser l'application sur tablette ou mobile en boutique. | S | 5 | S6 |
| US-28 | En tant que conseiller, je veux voir des indicateurs de chargement afin de comprendre que l'application traite ma demande. | S | 2 | S2 |
| US-29 | En tant que conseiller, je veux recevoir un message clair lorsqu'une erreur survient afin de comprendre la situation. | S | 2 | S2 |
| US-30 | En tant que conseiller, je veux consulter l'historique de mes échanges avec le Coach afin de retrouver les recommandations précédentes. | S | 3 | S4 |
| US-31 | En tant que manager, je veux consulter les performances commerciales de la boutique afin de suivre l'évolution des ventes. | M | 2 | S6 |
| US-32 | En tant que manager, je veux consulter les objectifs et les taux d'atteinte afin d'identifier les écarts de performance. | M | 2 | S6 |
| US-33 | En tant que manager, je veux consulter les performances des conseillers afin d'identifier les besoins d'accompagnement. | S | 2 | S6 |
| US-34 | En tant que manager, je veux consulter les prévisions de ventes afin d'anticiper la performance de fin de journée. | M | 2 | S6 |
| US-35 | En tant que manager, je veux consulter les anomalies horaires afin d'identifier les périodes de sous-performance. | M | 2 | S6 |
| US-36 | En tant que manager, je veux consulter une analyse des causes racines afin de comprendre pourquoi les objectifs ne sont pas atteints. | S | 5 | S6 |
| US-37 | En tant que manager, je veux consulter les actions stratégiques prioritaires afin de décider des actions commerciales à lancer. | M | 3 | S6 |
| US-38 | En tant que manager, je veux consulter une carte de chaleur du contexte afin d'identifier les signaux ayant le plus d'impact sur les ventes. | S | 5 | S6 |
| US-39 | En tant que manager, je veux recevoir des alertes commerciales en temps réel afin de réagir rapidement aux écarts importants. | M | 3 | S6 |
| US-40 | En tant que manager, je veux consulter les recommandations croisant ventes et stocks afin d'éviter de promouvoir des produits indisponibles. | M | 3 | S8 |
| US-41 | En tant que manager, je veux consulter l'état des stocks par produit et par boutique (quantités disponibles) afin d'évaluer la disponibilité réelle et de détecter les produits critiques. *(fusion US-41 + US-42)* | M | 3 | S7 |
| US-42 | *Fusionnée dans US-41.* | — | — | — |
| US-43 | En tant que manager, je veux consulter la couverture de stock en jours afin d'anticiper les ruptures. | M | 2 | S7 |
| US-44 | En tant que manager, je veux consulter la vitesse d'écoulement des produits afin d'identifier les références à forte rotation. | M | 2 | S7 |
| US-45 | En tant que manager, je veux consulter le point de commande de chaque produit afin de savoir quand déclencher un réapprovisionnement. | M | 2 | S7 |
| US-46 | En tant que manager, je veux consulter la quantité économique de commande afin d'optimiser le coût de réapprovisionnement. | S | 3 | S7 |
| US-47 | En tant que manager, je veux voir chaque produit classé comme rupture, insuffisant, sain ou surstock afin d'identifier rapidement les risques. | M | 3 | S7 |
| US-48 | En tant que manager, je veux filtrer les produits selon leur niveau de criticité afin de prioriser les actions. | M | 2 | S7 |
| US-49 | En tant que manager, je veux consulter les produits en rupture imminente afin de lancer une action avant l'indisponibilité. | M | 2 | S7 |
| US-50 | En tant que manager, je veux consulter les produits en surstock afin de limiter les coûts de stockage. | M | 2 | S7 |
| US-51 | En tant que manager, je veux consulter les prévisions de demande par produit afin d'anticiper les besoins futurs. | M | 2 | S8 |
| US-52 | En tant que manager, je veux que les prévisions tiennent compte de la saisonnalité afin d'obtenir des estimations plus fiables. | M | 2 | S8 |
| US-53 | En tant que manager, je veux que les prévisions tiennent compte des promotions afin d'anticiper les hausses de demande. | M | 2 | S8 |
| US-54 | En tant que manager, je veux que les prévisions tiennent compte des événements afin d'adapter le niveau de stock. | M | 2 | S8 |
| US-55 | En tant que manager, je veux que les recommandations tiennent compte des délais fournisseurs afin d'éviter une rupture pendant l'approvisionnement. | M | 2 | S8 |
| US-56 | En tant que manager, je veux que les recommandations tiennent compte des quantités minimales fournisseurs afin de proposer des commandes réalisables. | M | 2 | S8 |
| US-57 | En tant que manager, je veux que les recommandations tiennent compte du fournisseur disponible afin d'identifier la meilleure source d'approvisionnement. | M | 2 | S8 |
| US-58 | En tant que manager, je veux recevoir une décision d'approvisionnement par produit afin de savoir s'il faut commander, maintenir, surveiller ou accélérer. | M | 3 | S9 |
| US-59 | En tant que manager, je veux recevoir une quantité de commande recommandée afin de préparer le réapprovisionnement. | M | 2 | S9 |
| US-60 | En tant que manager, je veux consulter la justification de la quantité recommandée afin de comprendre la décision proposée. | M | 2 | S9 |
| US-61 | En tant que manager, je veux consulter le fournisseur recommandé afin de faciliter la préparation de la commande. | M | 2 | S9 |
| US-62 | En tant que manager, je veux adapter les recommandations selon l'objectif de la boutique afin d'arbitrer entre disponibilité et coût de stockage. | S | 3 | S9 |
| US-63 | En tant que manager, je veux qu'une décision de commande crée un bon de commande au statut « SUGGÉRÉ » afin de préparer le réapprovisionnement sans l'exécuter automatiquement. | M | 3 | S12 |
| US-64 | En tant que manager, je veux consulter les commandes suggérées dans un Kanban afin de suivre leur cycle de traitement. | M | 5 | S12 |
| US-65 | En tant que manager, je veux déplacer une commande entre les statuts « SUGGÉRÉ », « EN ATTENTE », « EN LIVRAISON » et « REÇU » afin de suivre son avancement. | M | 3 | S12 |
| US-66 | En tant que manager, je veux accepter une commande suggérée afin d'autoriser son traitement. | M | 2 | S12 |
| US-67 | En tant que manager, je veux refuser une commande suggérée afin d'empêcher une décision inadaptée. | M | 2 | S12 |
| US-68 | En tant que manager, je veux modifier la quantité proposée avant validation afin d'adapter la commande à la situation réelle. | M | 3 | S12 |
| US-69 | En tant que manager, je veux ajouter un commentaire à ma décision afin de justifier mon choix. | S | 2 | S12 |
| US-70 | En tant que manager, je veux consulter l'historique des validations et refus afin d'assurer la traçabilité des décisions. | M | 2 | S12 |
| US-71 | En tant que manager, je veux que la réception d'une commande mette automatiquement à jour le stock afin de conserver des données cohérentes. | M | 3 | S12 |
| US-72 | En tant que manager, je veux que la mise à jour du stock lors de la réception soit exécutée une seule fois afin d'éviter les doublons. | M | 2 | S12 |
| US-73 | En tant que manager, je veux voir les mouvements du Kanban en temps réel afin de partager le même état avec les autres utilisateurs. | S | 3 | S12 |
| US-74 | En tant que manager, je veux recevoir des alertes de stock en temps réel afin de réagir sans recharger la page. | M | 2 | S11 |
| US-75 | En tant que manager, je veux être alerté lorsqu'un seuil de rupture est atteint afin de déclencher une analyse automatique. | M | 3 | S11 |
| US-76 | En tant que manager, je veux qu'une anomalie de vente déclenche automatiquement un cycle d'analyse afin d'obtenir une recommandation proactive. | M | 5 | S11 |
| US-77 | En tant que manager, je veux consulter l'état d'exécution des agents afin de savoir quels traitements sont en cours. | M | 2 | S10 |
| US-78 | En tant que manager, je veux consulter la chronologie d'un cycle multi-agents afin de comprendre l'ordre d'exécution. | S | 2 | S10 |
| US-79 | En tant que manager, je veux consulter la durée d'exécution de chaque agent afin d'identifier les traitements lents. | S | 2 | S10 |
| US-80 | En tant que manager, je veux consulter les erreurs des agents afin d'identifier les dysfonctionnements. | M | 2 | S10 |
| US-81 | En tant que manager, je veux consulter les verdicts du Guardrail afin de connaître les recommandations approuvées, réécrites, bloquées ou escaladées. | M | 2 | S11 |
| US-82 | En tant que manager, je veux consulter les motifs de blocage du Guardrail afin de comprendre pourquoi une réponse a été rejetée. | M | 2 | S11 |
| US-83 | En tant que manager, je veux valider une recommandation escaladée afin d'autoriser sa diffusion ou son application. | M | 2 | S11 |
| US-84 | En tant que manager, je veux modifier une recommandation escaladée afin de corriger une proposition avant sa diffusion. | M | 2 | S11 |
| US-85 | En tant que manager, je veux refuser une recommandation escaladée afin d'empêcher son application. | M | 2 | S11 |
| US-86 | En tant que manager, je veux consulter les indicateurs de qualité des réponses afin de suivre la performance du système. | S | 2 | S11 |
| US-87 | En tant que manager, je veux donner un retour utile ou non utile sur une recommandation afin d'améliorer les décisions futures. | S | 2 | S11 |
| US-88 | En tant que manager, je veux consulter l'historique des cycles d'analyse afin de comparer l'évolution de la boutique. | S | 2 | S11 |
| US-89 | En tant que manager, je veux consulter les résultats consolidés de plusieurs boutiques afin de suivre la performance régionale. | C | 3 | Backlog |
| US-90 | En tant que manager, je veux recevoir une notification lorsqu'une alerte critique survient afin de réagir même lorsque l'application n'est pas ouverte. | C | 3 | Backlog |

**82 user stories actives** (87 formulées − 3 fusionnées − 2 en backlog post-projet)

---

## 2. Product Backlog — Enablers techniques

| ID | Enabler | Priorité | Points | Sprint |
| ----- | --- | :------: | :---: | :---: |
| EN-01 | Mettre en place un backend FastAPI organisé en routes, services et repositories. | M | 5 | S1 |
| EN-02 | Mettre en place un frontend Angular structuré avec composants standalone, Signals et routage. | M | 5 | S1 |
| EN-03 | Intégrer PostgreSQL comme base principale des ventes, stocks, produits, objectifs et commandes. | M | 8 | S1 |
| EN-04 | Versionner le schéma de données avec Alembic comme source de vérité unique. | M | 5 | S1 |
| EN-05 | Consolider le schéma Inventory et les relations entre produits, stocks, fournisseurs et commandes. | M | 5 | S1 |
| EN-06 | Configurer Redis pour le cache, le bus d'alertes et les flux temps réel. | M | 3 | S1 |
| EN-07 | Intégrer Milvus pour la recherche vectorielle des scripts de vente. | S | 5 | S4 |
| EN-08 | Mettre en place un corpus local de repli lorsque Milvus est indisponible. | M | 3 | S4 |
| EN-09 | Mettre en place une recherche hybride combinant embeddings, BM25 et reranking. | M | 8 | S4 |
| EN-10 | Intégrer le modèle d'embedding multilingue `bge-m3`. | S | 3 | S4 |
| EN-11 | Développer un moteur Holt-Winters saisonnier avec backtest WAPE. | M | 8 | S2 |
| EN-12 | Intégrer TimesFM ou Chronos avec un repli automatique vers Holt-Winters. | S | 5 | S8 |
| EN-13 | Stocker les prévisions en base afin de les historiser et de les réutiliser. | M | 3 | S2 |
| EN-14 | Développer l'Agent Analyste pour calculer les écarts, prévisions et anomalies. | M | 8 | S2 |
| EN-15 | Développer l'Agent Stratège pour produire des actions commerciales cross-domaine. | M | 8 | S6 |
| EN-16 | Développer l'Agent Analysis Inventory pour calculer la couverture, la rotation, le point de commande et l'EOQ. | M | 8 | S7 |
| EN-17 | Développer l'Agent Context Inventory pour intégrer prévisions, promotions, événements et fournisseurs. | M | 8 | S8 |
| EN-18 | Développer l'Agent Decision pour produire une décision d'approvisionnement explicable. | M | 8 | S9 |
| EN-19 | Développer l'Agent Coach pour générer les messages et recommandations destinés au conseiller. | M | 8 | S3 |
| EN-20 | Développer un Supervisor LangGraph capable de router les requêtes selon l'intention. | M | 8 | S9 |
| EN-21 | Exécuter en parallèle les branches Sales, Inventory, Knowledge et Context. | M | 8 | S9 |
| EN-22 | Mettre en place un `RetailState` hiérarchique avec reducers afin de fusionner les sorties des agents. | M | 8 | S10 |
| EN-23 | Exécuter le traitement Inventory en parallèle par SKU avec `Send()` et un mécanisme map-reduce. | M | 8 | S10 |
| EN-24 | Mettre en place un checkpointer PostgreSQL pour reprendre les cycles interrompus. | M | 5 | S10 |
| EN-25 | Mettre en place une mémoire long terme pour les profils, feedbacks et historiques de cycles. | S | 5 | S5 |
| EN-26 | Mettre en place une cascade LLM Mistral, OpenRouter, Groq et Ollama. | M | 5 | S4 |
| EN-27 | Mettre en place des réponses déterministes lorsque tous les fournisseurs LLM sont indisponibles. | M | 3 | S4 |
| EN-28 | Développer un Guardrail déterministe contrôlant toutes les réponses visibles. | M | 8 | S10 |
| EN-29 | Bloquer automatiquement les recommandations de produits à stock nul. | M | 3 | S10 |
| EN-30 | Réécrire automatiquement une réponse non conforme avec un nombre maximal de tentatives. | M | 5 | S10 |
| EN-31 | Mettre en place un mécanisme HITL avec pause et reprise du graphe. | M | 8 | S11 |
| EN-32 | Empêcher toute validation automatique d'une commande d'approvisionnement. | M | 3 | S11 |
| EN-33 | Mettre en place les WebSockets pour les alertes, recommandations et mouvements Kanban. | M | 5 | S11 |
| EN-34 | Mettre en place le streaming SSE pour les réponses du Coach. | M | 3 | S3 |
| EN-35 | Intégrer Langfuse pour tracer les agents, durées, erreurs et coûts LLM. | M | 5 | S10 |
| EN-36 | Exécuter les traitements bloquants hors de l'event loop afin d'éviter le gel du serveur. | M | 5 | S12 |
| EN-37 | Ajouter des caches Redis et des index PostgreSQL sur les requêtes critiques. | S | 3 | S7 |
| EN-38 | Réviser le simulateur de stock afin de produire des scénarios réalistes. | S | 3 | S3 |
| EN-39 | Remplacer les données simulées de la page Inventory par les sorties réelles des agents. | M | 5 | S7 |
| EN-40 | Mettre en place des tests unitaires et d'intégration pour les agents Sales et Inventory. | M | 5 | S8–S12 * |
| EN-41 | Mettre en place des tests frontend et E2E pour le Chat, Inventory, HITL et Kanban. | M | 5 | S8–S12 * |
| EN-42 | Mettre en place une chaîne CI avec lint, tests et build frontend/backend. | S | 3 | S8–S12 * |
| EN-43 | Corriger les bugs restants et améliorer les performances du frontend. | M | 3 | S8–S12 * |
| EN-44 | Nettoyer et réorganiser les modules Sales, Inventory et Shared afin de réduire la dette technique. | M | 3 | S12 |
| EN-45 | Créer un environnement Docker reproductible pour FastAPI, Angular, PostgreSQL, Redis et Milvus. | S | 5 | S12 |
| EN-46 | Mettre en place une suite d'évaluation pour le Coach, les modèles et le Guardrail. | S | 5 | S12 |

\* **Transverses** : capacité réservée en continu de S8 à S12 (~3 pts/sprint), pas un livrable d'un sprint unique.

---

## 3. Livrables académiques (PFE)

| ID | Livrable | Priorité | Points | Sprint |
| ----- | --- | :------: | :---: | :---: |
| LA-01 *(ex EN-47)* | Préparer les diagrammes, captures, résultats et éléments nécessaires au rapport de PFE. | M | 3 | S12 |
| LA-02 *(ex EN-48)* | Préparer un scénario de démonstration bout-en-bout Sales Coach et Inventory Advisor. | M | 3 | S12 |

---

## 4. Won't have — hors périmètre (arbitrages assumés)

| ID | Item exclu | Justification |
| ----- | --- | --- |
| W-01 | Interface multilingue (arabe / anglais) | Périmètre PFE limité au français ; l'embedding `bge-m3` reste multilingue pour préparer l'évolution. |
| W-02 | Application mobile native (iOS/Android) | Le responsive web (US-27) couvre l'usage tablette en boutique. |
| W-03 | Intégration à un ERP / système de facturation réel | Données simulées et historisées suffisent pour valider l'approche agentique. |
| W-04 | Fine-tuning d'un modèle LLM propriétaire | Le RAG + prompt engineering + cascade multi-fournisseurs répondent au besoin à coût maîtrisé. |

---

## 5. Sprint Distribution

| Sprint | Période | Objectif de sprint | Items affectés | Points |
|:---:|---|---|---|:---:|
| **S1** | M1 · sem. 1–2 | Fondations backend, frontend et données | EN-01, EN-02, EN-03, EN-04, EN-05, EN-06 | 31 |
| **S2** | M1 · sem. 3–4 | Authentification, moteur de prévision, premier dashboard conseiller | US-01, US-02, US-03, US-28, US-29, EN-11, EN-13, EN-14 | 33 |
| **S3** | M2 · sem. 1–2 | Dashboard conseiller complet + Coach conversationnel (SSE) | US-06, US-07, US-08, US-09, US-10, US-11, US-12, EN-19, EN-34, EN-38 | 32 |
| **S4** | M2 · sem. 3–4 | RAG (Milvus + hybride + repli) et robustesse LLM | US-17, US-30, EN-07, EN-08, EN-09, EN-10, EN-26, EN-27 | 33 |
| **S5** | M3 · sem. 1–2 | Coaching enrichi (scores, substitution, contexte, personnalisation) | US-13, US-14, US-15, US-16, US-18, US-19, US-20, US-21, US-22, US-23, US-24, EN-25 | 34 |
| **S6** | M3 · sem. 3–4 | Supervision manager ventes + Agent Stratège + responsive | US-27, US-31, US-32, US-33, US-34, US-35, US-36, US-37, US-38, US-39, EN-15 | 39 |
| **S7** | M4 · sem. 1–2 | Inventory : état des stocks et analyse (couverture, rotation, EOQ) | US-41, US-43, US-44, US-45, US-46, US-47, US-48, US-49, US-50, EN-16, EN-37, EN-39 | 37 |
| **S8** | M4 · sem. 3–4 | Prévisions de demande, contraintes fournisseurs, cross-domaine | US-40, US-51, US-52, US-53, US-54, US-55, US-56, US-57, EN-12, EN-17 | 30 |
| **S9** | M5 · sem. 1–2 | Agent Decision + démarrage orchestration LangGraph | US-58, US-59, US-60, US-61, US-62, EN-18, EN-20, EN-21 | 36 |
| **S10** | M5 · sem. 3–4 | Orchestration avancée, observabilité, Guardrail | US-25, US-26, US-77, US-78, US-79, US-80, EN-22, EN-23, EN-24, EN-28, EN-29, EN-30, EN-35 | 46 |
| **S11** | M6 · sem. 1–2 | HITL, temps réel, boucle proactive, feedback | US-74, US-75, US-76, US-81, US-82, US-83, US-84, US-85, US-86, US-87, US-88, EN-31, EN-32, EN-33 | 50 |
| **S12** | M6 · sem. 3–4 | Kanban commandes, industrialisation, livrables PFE | US-63 → US-73, EN-36, EN-44, EN-45, EN-46, LA-01, LA-02 | 54 |
| — | Transverse S8–S12 | Tests, CI, corrections continues | EN-40, EN-41, EN-42, EN-43 | 16 |
| — | Backlog post-projet | Extensions (priorité C) | US-89, US-90 | 6 |

### Synthèse par mois

| Mois | Sprints | Thème dominant | Points |
|:---:|:---:|---|:---:|
| M1 | S1–S2 | Fondations, auth, moteur de prévision | 64 |
| M2 | S3–S4 | Dashboard conseiller, Coach, RAG | 65 |
| M3 | S5–S6 | Coaching enrichi, supervision manager, Stratège | 73 |
| M4 | S7–S8 | Inventory : analyse, prévisions, fournisseurs | 67 |
| M5 | S9–S10 | Décision, orchestration, Guardrail | 82 |
| M6 | S11–S12 | HITL, temps réel, Kanban, industrialisation | 104 |

**Total planifié : 455 pts en sprint + 16 pts transverses · Vélocité moyenne : ~38 pts/sprint**

### Note de pilotage

La charge croît en fin de projet (S10 = 46, S11 = 50, S12 = 54 contre ~33 en début de cycle) : c'est le point de vigilance principal du plan. Leviers prévus si la vélocité réelle décroche : (1) déprioriser les S de S11–S12 (US-73, US-86 à US-88, EN-45, EN-46) ; (2) livrer le Kanban en deux incréments (colonnes + drag-and-drop d'abord, temps réel multi-utilisateurs ensuite) ; (3) la réserve transverse (16 pts) absorbe les débordements de test.
