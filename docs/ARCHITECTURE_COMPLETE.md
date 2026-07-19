# Architecture Complète — Moteur Agentique Retail Ooredoo Tunisie

> **Solution** : Coaching de vente temps réel + Optimisation des stocks, pilotés par un système
> multi-agents LLM. Deux modules coopérants — **Sales** (coaching commercial des conseillers en
> point de vente) et **Inventory** (analyse des stocks et aide au réapprovisionnement) — orchestrés
> par des superviseurs LangGraph avec validation humaine (Human-in-the-Loop) et agent de contrôle (Guardrail).
>
> **Date** : 2026-07-17 — branche `refactor/monolith-v2`

---

## 1. Présentation générale de la solution

La solution est un moteur agentique destiné au réseau de points de vente Ooredoo Tunisie. Elle
répond à deux problématiques métier complémentaires. La première est le **coaching commercial** :
un conseiller de vente en boutique a besoin, à tout moment de la journée, de savoir quels produits
mettre en avant, avec quels arguments, et où il en est par rapport à son objectif de chiffre
d'affaires. La seconde est l'**optimisation des stocks** : le magasin doit anticiper les ruptures,
éviter le surstock et déclencher les commandes de réapprovisionnement au bon moment et dans les
bonnes quantités.

Ces deux problématiques sont traitées par un même système, car elles partagent les mêmes données
(historique de ventes, état des stocks, contexte marché) et s'enrichissent mutuellement : on ne
recommande pas au conseiller un produit en rupture imminente, et inversement une prévision de pic
de ventes déclenche une suggestion de commande. C'est ce couplage — appelé **cross-domaine** dans
le projet — qui justifie l'architecture multi-agents.

La pile technologique est la suivante : le backend est un **monolithe modulaire Python/FastAPI**
(package unique `app/`), le raisonnement multi-agents repose sur **LangGraph**, le frontend est une
application **Angular 21** utilisant les Signals. Les données sont stockées dans **PostgreSQL**
(schéma géré exclusivement par les migrations Alembic 0001 à 0008), **Redis** sert de cache et de
bus d'événements, et **Milvus** héberge la base vectorielle du RAG. Les modèles de langage sont
appelés via une cascade de fournisseurs : **Mistral en primaire**, puis OpenRouter, Groq et Ollama
en secours. Les prévisions reposent sur **Holt-Winters** (méthode statistique primaire), **TimesFM**
(modèle de fondation préchargé) et un repli SQL. L'observabilité de bout en bout est assurée par
**Langfuse**, complétée par un monitoring des agents et une suite d'évaluation `evals/`.

---

## 2. Vue globale : entrées, système agentique, sorties

Le système peut se lire comme une chaîne de transformation. Des **entrées** brutes traversent un
**système agentique** organisé en quatre étages — perception, analyse et raisonnement, décision et
synthèse, contrôle — pour produire des **sorties actionnables**.

### 2.1 Les entrées du système

Le système consomme six familles d'entrées.

La première est la **requête du conseiller** : une question en langage naturel posée dans le chat
Angular, transmise au backend par les endpoints `/chat` (réponse complète) ou `/stream` (réponse
progressive en SSE). Elle porte le message, l'identifiant du conseiller, celui du point de vente
et l'historique de conversation.

La deuxième est le **flux de ventes temps réel** : les transactions réalisées en boutique arrivent
en continu dans PostgreSQL. Elles alimentent l'analyse en cours de journée et peuvent déclencher
automatiquement un cycle d'agents via le bus d'alertes lorsqu'un seuil est franchi (pic ou chute
de ventes, seuil de stock atteint).

La troisième est l'**historique de ventes** : 4,5 années de données à granularité journalière,
soit environ 1,49 million de lignes, enrichies de caractéristiques saisonnières complètes. C'est
le carburant de l'Agent Analyste pour les KPIs, les tendances et les prévisions.

La quatrième est l'**état des stocks** par point de vente : niveaux par produit, seuils, catalogue
fournisseurs, lue dans PostgreSQL et mise en cache dans Redis.

La cinquième regroupe les **signaux marché** : événements datés (festivals, concerts, périodes
particulières) collectés par un scraper et une base locale, offres commerciales actives,
saisonnalité et tendances. Ces signaux nourrissent l'Agent Stratège côté ventes et l'Agent Context
côté stocks.

La sixième est constituée des **décisions humaines** : chaque validation, modification ou refus
d'une recommandation (dans le panneau HITL ou sur le Kanban) est enregistré et réinjecté dans le
système comme boucle de feedback (table dédiée, migration Alembic 0008).

À cela s'ajoute la base de **connaissances métier** : plus de 200 scripts et arguments de vente
vectorisés dans Milvus, interrogés par recherche sémantique (RAG).

### 2.2 Le système agentique

Au centre, le traitement se déroule en quatre étages successifs.

L'étage de **perception** collecte en parallèle tout ce dont les agents ont besoin : ventes du
jour et historique, scripts de vente pertinents (RAG), contexte marché, instantané des stocks.
Cette collecte parallèle est un choix de conception délibéré : la latence totale correspond à la
branche la plus lente et non à la somme des branches.

L'étage d'**analyse et de raisonnement** fait travailler les agents spécialisés : l'Agent Analyste
(séries temporelles, anomalies, prévisions), l'Agent Stratège (événements, stratégie commerciale)
côté ventes ; les Agents Analysis et Context (diagnostic de stock et facteurs de demande) côté
inventaire.

L'étage de **décision et de synthèse** produit les livrables : l'Agent Coach formule la réponse
destinée au conseiller ; l'Agent Decision détermine l'action d'approvisionnement et la quantité
à commander.

L'étage de **contrôle** ferme la chaîne : l'Agent Guardrail vérifie la conformité de chaque
réponse avant affichage, et les cas sensibles sont routés vers une validation humaine
(Human-in-the-Loop) plutôt que d'être appliqués automatiquement.

### 2.3 Les sorties du système

Le système produit six familles de sorties.

La **réponse de coaching** : texte conversationnel diffusé en streaming SSE dans le chat du
conseiller, accompagné des scripts de vente cités et d'un badge indiquant le statut de vérification
Guardrail.

Les **produits recommandés scorés** (`scored_products`) : une liste de produits à mettre en avant,
classés par un score combinant ventes, disponibilité en stock et contexte du moment, affichée sous
forme de puces dans le chat et le dashboard.

Les **suggestions de commande** : des ordres d'achat (PO) créés automatiquement au statut
« SUGGÉRÉ » sur le Kanban d'approvisionnement, en attente d'approbation humaine.

Les **alertes temps réel** : ruptures imminentes, anomalies de ventes, événements guardrail,
poussées vers le frontend par WebSocket sans que l'utilisateur ait à les chercher.

Les **indicateurs et prévisions** : KPIs de vente, prévisions de fin de journée et multi-horizons,
tendances, exposés aux dashboards par l'API.

Les **traces d'exécution** : chaque nœud de chaque graphe est tracé dans Langfuse (entrées,
sorties, durées, coûts LLM), à des fins d'audit et d'évaluation continue.

Deux boucles referment le cycle : la réception d'une commande met à jour l'état des stocks (la
sortie « PO » redevient une entrée « stock »), et les décisions humaines alimentent l'amélioration
continue des recommandations.

---

## 3. Architecture en couches

L'architecture s'organise en quatre couches.

### 3.1 Couche présentation (Angular 21)

Le frontend expose : le **dashboard ventes** avec le chat Coach en SSE ; le **dashboard
inventory** avec alertes et prévisions ; le **Kanban des commandes** avec le panneau HITL ; et le
**panel de monitoring** (santé des agents, événements guardrail, coûts LLM). Trois flux WebSocket
alimentent l'interface en temps réel (voir section 8). Les services transverses (`api.ts`,
`websocket.service.ts`, `auth.ts` avec rafraîchissement de token, `purchase-order-api.service.ts`,
`monitoring.service.ts`) forment la couche d'accès unifiée, complétée par un error boundary et des
squelettes de chargement.

### 3.2 Couche API (FastAPI)

L'API expose trois styles de communication : **REST** pour les requêtes classiques (chat,
métriques, prévisions, commandes, HITL, feedback, monitoring), **SSE** pour le streaming des
réponses du Coach, et **WebSocket** pour les poussées temps réel (alertes, guardrail, Kanban).
L'authentification est en JWT avec un contrôle d'accès **RBAC au niveau du point de vente** :
un utilisateur ne voit que les données de son magasin selon son rôle. Les endpoints exposés sont
protégés par une limitation de débit (slowapi).

### 3.3 Couche agentique (LangGraph)

Deux graphes d'agents, un par module, partagent un état unifié appelé **`RetailState`**. Le module
Sales est piloté par le **SupervisorAgent** (`app/sales/orchestration/supervisor_agent.py`) et le
module Inventory par son propre superviseur (`app/inventory/core/supervisor.py`). Les deux modules
communiquent par des **outils cross-domaine** : le Coach peut interroger l'état des stocks et les
alertes du module Inventory ; le module Inventory exploite les prévisions de vente du module Sales.

### 3.4 Couche données et services

PostgreSQL est la source de vérité (ventes, stocks, commandes, feedback) ; Redis assure le cache et
le bus d'alertes ; Milvus le RAG ; les moteurs de prévision (Holt-Winters, TimesFM, repli SQL) sont
mutualisés ; les LLM sont accédés via une fabrique avec cascade de fournisseurs ; Langfuse trace
tout ; enfin un **serveur MCP maison** expose sept outils d'inventaire consommables par les agents
et par des clients externes.

### 3.5 Organisation du code

Le backend est un package unique `app/` organisé ainsi :

- `app/main.py` : point d'entrée FastAPI, montage des routers, endpoints WebSocket, cycle de vie
  (démarrage/arrêt : préchargement TimesFM, connexions, simulateur).
- `app/core/` : transverse — configuration, accès base, intégration Langfuse, service de feedback,
  fournisseur de tendances, vérification de schéma.
- `app/api/` : routers REST — auth, cycle, feedback, forecast, hitl, kpis, monitoring, stores,
  supervisor.
- `app/sales/` : le domaine coaching vente. `core/` contient le `RetailState`, le bus d'alertes,
  le bus d'état et le circuit breaker ; `orchestration/` contient le graphe superviseur, les
  déclencheurs et le traceur ; `coaching/agents/` contient les quatre agents (analyst, stratege,
  coach, guardrail) ; `data/` contient le fournisseur PostgreSQL, le récupérateur RAG et le
  simulateur temps réel.
- `app/inventory/` : le domaine optimisation des stocks. `agents/` contient les trois agents
  (analysis, context, decision) ; `core/` le superviseur, le guardrail et l'état ; `forecasting/`
  les moteurs de prévision ; `services/` l'orchestrateur, le serveur MCP, le bus d'alertes Redis,
  le bus WebSocket des commandes et l'auto-confirmation encadrée ; `repositories/` l'accès données ;
  `api/` les routes.

---

## 4. Le module Sales : fonctionnement du graphe superviseur

Le cœur du module Sales est un graphe LangGraph de douze nœuds, construit dans
`app/sales/orchestration/supervisor_agent.py`. Son déroulement est le suivant.

**Étape 1 — Initialisation.** Le nœud `initialize_state` construit le `RetailState` unifié à
partir de la requête : identifiants du magasin et du conseiller, message, horodatage, identifiant
de cycle.

**Étape 2 — Collecte parallèle.** Quatre branches partent simultanément du nœud d'initialisation :

- `sales_branch` exécute l'Agent Analyste : KPIs du jour, tendances, détection d'anomalies,
  prévisions de ventes.
- `knowledge_branch` interroge le RAG Milvus pour récupérer les scripts et arguments de vente
  pertinents.
- `context_branch` exécute l'Agent Stratège : événements datés, offres actives, saisonnalité,
  recommandations stratégiques.
- `inventory_branch` prend un instantané du stock et des alertes de rupture via les outils
  cross-domaine du module Inventory.

**Étape 3 — Fusion.** Le nœud `merge_outputs` fusionne les résultats dans le `RetailState`. La
règle de fusion est essentielle : chaque nœud ne retourne que son **delta** (les champs qu'il a
produits), jamais l'état complet. Les reducers du `RetailState` agrègent ces deltas, ce qui
empêche qu'une branche écrase les résultats d'une autre — c'est ce mécanisme qui avait corrigé le
bug des widgets stratège/analyste vides.

**Étape 4 — Synthèse.** Le nœud `coach_agent` produit la réponse destinée au conseiller : synthèse
conversationnelle des signaux fusionnés et liste de produits recommandés scorés.

**Étape 5 — Contrôle et routage.** Le nœud `guardrail_agent` évalue la réponse candidate. La
fonction de routage `route_after_guardrail` dirige ensuite le flux selon le verdict : si la
réponse est **conforme**, elle part directement vers la diffusion ; si elle est jugée **sensible**,
elle passe par le nœud `human_validation` (porte HITL) ; si elle est **bloquée**, le nœud
`safe_fallback` substitue une réponse de repli sûre plutôt que de laisser l'utilisateur sans réponse.

**Étape 6 — Diffusion et persistance.** Le nœud `notify_frontend` diffuse le résultat en temps
réel (SSE vers le chat, WebSocket vers les widgets du dashboard), puis `save_memory` persiste la
conversation et le feedback avant la fin du graphe. Chaque nœud est tracé dans Langfuse.

---

## 5. Le module Inventory : fonctionnement du pipeline de décision

Le module Inventory enchaîne trois agents spécialisés selon une logique séquentielle : d'abord
comprendre l'état du stock (Analysis), puis comprendre la demande à venir (Context), enfin décider
(Decision).

Le cycle peut être déclenché de deux façons : **à la demande** (requête utilisateur ou appel API)
ou **automatiquement** par le bus d'alertes — lorsque le module détecte un risque de rupture ou
qu'un événement de vente franchit un seuil, l'AlertBus (publish/subscribe sur Redis) déclenche un
cycle sans intervention humaine.

Le résultat du pipeline est une recommandation d'action par produit : **commander**, **accélérer**
une commande en cours, **maintenir** le niveau actuel ou **surveiller**. Lorsque l'action est une
commande, l'Agent Decision crée une suggestion d'ordre d'achat (PO) au statut « SUGGÉRÉ » sur le
Kanban d'approvisionnement. La validation humaine est alors le passage obligé : l'utilisateur
approuve (éventuellement en modifiant la quantité) ou refuse. En cas d'approbation, la commande
suit son cycle de vie — SUGGÉRÉ → EN ATTENTE → EN LIVRAISON → REÇU — visible en temps réel sur le
Kanban grâce au bus WebSocket dédié (`po_ws_bus`). À la réception, le stock est mis à jour, ce qui
**ferme la boucle** : la donnée de stock corrigée alimente le cycle d'analyse suivant. En cas de
refus, le feedback est enregistré pour l'amélioration continue.

Le module possède aussi son propre graphe superviseur (`app/inventory/core/supervisor.py`),
symétrique de celui du module Sales : un nœud d'entrée initialise l'état, un routeur déclenche en
parallèle (primitive LangGraph `Send`) les branches inventory, sales et knowledge, un nœud
`coach_fusion` synthétise, le guardrail contrôle, et un routage conditionnel dirige vers la sortie
directe ou vers une revue humaine (`human_review`). Le graphe est compilé une seule fois puis mis
en cache.

---

## 6. Les agents en détail : rôle et fonctionnement

Cette section décrit chaque agent individuellement : sa raison d'être, son fonctionnement interne
tel qu'implémenté dans le code, ses mécanismes de repli et ses entrées/sorties.

### 6.1 SupervisorAgent (Sales) — le chef d'orchestre

*Fichier : `app/sales/orchestration/supervisor_agent.py`.*

**Rôle.** Coordonner tous les agents du module Sales pour produire une réponse unique, cohérente
et contrôlée. Il ne raisonne pas lui-même sur le fond : il planifie, parallélise, fusionne et
route. C'est lui qui garantit qu'aucune réponse n'atteint l'utilisateur sans être passée par le
Guardrail, et que les cas sensibles passent par l'humain.

**Fonctionnement.** Il implémente le graphe de douze nœuds décrit à la section 4 : initialisation
du `RetailState`, fan-out parallèle des quatre branches de collecte, fusion par deltas, synthèse
par le Coach, contrôle Guardrail avec routage conditionnel à trois issues (diffusion directe,
validation humaine, repli sûr), diffusion frontend et persistance mémoire.

**Entrées / sorties.** Il reçoit une requête conseiller ou un déclencheur du bus d'alertes, plus
le `RetailState` courant ; il produit le `RetailState` final : réponse validée, widgets
analyste/stratège, événements guardrail, le tout diffusé au frontend.

### 6.2 Agent Analyste v4 (Sales) — l'expert en séries temporelles

*Fichiers : `agents/analyst/ts_engine.py`, `ts_node.py`, `react_analyst.py`, `react_tools.py`.*

**Rôle.** Produire un diagnostic chiffré et des prévisions fiables sur les ventes d'un magasin :
où en est le chiffre d'affaires par rapport à l'objectif, comment va finir la journée, quelles
heures sont anormales, quelle est la dynamique.

**Principe de conception.** Le calcul est **entièrement déterministe : le LLM est hors du chemin
critique**. Les chiffres (prévisions, écarts, anomalies) sont produits par des méthodes
statistiques ; le LLM n'intervient qu'en option, pour enrichir la formulation du résumé
(`_maybe_enrich_summary`), jamais pour produire une valeur. Une panne LLM ne dégrade donc pas la
qualité des chiffres.

**Fonctionnement de l'analyse (`analyze_store`).** Premièrement, l'agent charge 120 jours de
série journalière depuis PostgreSQL. Deuxièmement, il ajuste un modèle **Holt-Winters saisonnier**
de période 7 jours (la saisonnalité hebdomadaire domine dans le retail) : une recherche par
grille (`_hw_gridsearch`) teste plusieurs combinaisons de paramètres de lissage, et un **backtest
sur les 28 derniers jours** (`_backtest_mape`, métrique WAPE) départage les candidats — la
combinaison qui aurait le mieux prédit le passé récent gagne. Sur le banc de référence, cette
approche atteint 4,4 % d'erreur, meilleure que Prophet. Troisièmement, pour estimer la **fin de
journée en cours**, l'agent construit le profil horaire moyen du même jour de semaine
(`_fetch_hourly_profile`) et calcule la part de chiffre d'affaires normalement réalisée à l'heure
courante (`_cum_share`) : si à 14 h le magasin réalise habituellement 55 % de sa journée, le
réalisé partiel est extrapolé en conséquence, avec un intervalle de confiance. Quatrièmement, la
**détection d'anomalies** compare chaque heure au profil attendu (`_classify_hour`, déviation et
z-score) et `_trend_signal` qualifie la dynamique récente. Enfin, `_build_summary` assemble le
diagnostic : chiffre d'affaires contre objectif, pourcentage d'écart, prévision de fin de journée
avec bornes, taux d'atteinte.

**Replis.** Si la série est trop courte ou l'ajustement échoue, un repli linéaire
(`_linear_fallback`) prend le relais, puis en dernier recours une agrégation SQL simple.

**Mode ReAct.** Pour les questions ad hoc, une variante ReAct (`react_analyst.py`) donne au LLM
quatre outils d'analyse qu'il peut invoquer en raisonnant : `detect_anomalies`,
`ts_decomposition` (décomposition tendance/saisonnalité/résidu), `forecast_multi_horizon` et
`product_velocity` (vitesse d'écoulement par produit).

**Entrées / sorties.** En entrée : identifiant magasin, heure courante, historique de ventes. En
sortie : prévision de fin de journée avec intervalle, écart à l'objectif, anomalies horaires,
signal de tendance, et le WAPE de backtest comme indicateur de confiance.

### 6.3 Agent Stratège (Sales) — le stratège cross-domaine

*Fichiers : `agents/stratege/agent.py`, `nodes.py`, `events_scraper.py`.*

**Rôle.** Transformer les signaux bruts (ventes, stocks, marché) en un **plan d'action commercial
priorisé** pour le magasin : que faire maintenant, dans quel ordre, avec quel impact attendu — en
tenant compte des événements réels (festivals, concerts) et des offres actives.

**Fonctionnement.** Le Stratège est lui-même un petit graphe. Le nœud `node_fetch_context`
collecte le chiffre d'affaires et l'objectif, le **stock critique** (produits sous 5 unités, pour
ne jamais recommander de pousser un produit en quasi-rupture), la météo, les événements datés
(alimentés par un scraper et une base locale, et **séparés en événements en cours et à venir**,
car on n'agit pas pareil pendant un festival et cinq jours avant) et les offres actives. Le nœud
`node_rag_search` récupère les scripts de vente pertinents. Le nœud `node_analyze_context`
calcule l'écart à l'objectif, le niveau d'urgence, les heures restantes d'ouverture, et génère
des alertes déterministes. Le nœud `node_generate_strategy` appelle alors le LLM (Mistral en
primaire, OpenRouter en secours) avec un prompt cross-domaine combinant ventes et inventaire, et
obtient des actions stratégiques structurées. Vient ensuite une étape d'**auto-critique**
(`node_self_critique_stratege`) : le LLM relit ses propres actions, estime l'impact de chacune et
les réordonne ou en élimine. Le nœud `node_build_output` construit les livrables pour le
frontend : les StrateActions, une carte de chaleur d'urgence et les signaux de contexte. Enfin,
en cas d'écart important, `node_analyze_root_cause` produit une analyse de cause racine.

**Replis.** Si le LLM échoue ou renvoie un JSON tronqué, `_extract_from_partial_json` récupère ce
qui peut l'être, puis `_make_fallback_strategy` construit une stratégie par règles métier sans
LLM — le Stratège ne rend jamais une réponse vide.

**Entrées / sorties.** En entrée : sorties de l'Analyste, stock critique, événements, offres,
météo, scripts RAG. En sortie : actions priorisées avec impact estimé, alertes, carte de chaleur,
recommandations cross-domaine.

### 6.4 Agent Coach (Sales) — l'interlocuteur du conseiller

*Fichier : `agents/coach/coach_chat.py` (~2 500 lignes, endpoints `/chat` et `/stream`).*

**Rôle.** Répondre en langage naturel aux questions du conseiller de vente avec des
recommandations concrètes, sourcées et adaptées au contexte immédiat du magasin. C'est le seul
agent avec lequel l'utilisateur dialogue directement.

**Fonctionnement d'une requête.** Le traitement suit sept étapes.

Première étape, la **garde d'entrée** : limitation de débit, normalisation du message, puis
classification d'intention (`_classify_intent` : question produit, objectif, demande de script,
hors-sujet…). Les salutations pures et le hors-sujet reçoivent une réponse immédiate sans appel
LLM, et un cache Redis répond instantanément aux questions déjà posées.

Deuxième étape, la **collecte parallèle du contexte** : données du point de vente, détail des
ventes du jour, contexte inventaire, historique de conversation de la journée, **profil du
conseiller** (pour personnaliser le ton et les recommandations), et recherche RAG — Milvus en
premier, corpus local en repli si la base vectorielle est indisponible.

Troisième étape, l'**intelligence produit** : la fonction `_out_of_stock` identifie les produits
indisponibles pour que le Coach ne les propose jamais ; `_load_substitutes_sync` prépare des
substituts pour les produits en rupture qui seraient demandés ; `_rank_with_agents` score les
produits candidats en croisant ventes, stock et contexte — c'est l'origine des `scored_products`
affichés dans l'interface.

Quatrième étape, l'**injection du Stratège** : `_get_stratege_for_chat` récupère la stratégie du
jour côté serveur et l'injecte dans le prompt. L'appel est borné par un timeout et le Stratège est
préchauffé en arrière-plan, de sorte que cette injection n'ajoute jamais de latence bloquante.

Cinquième étape, la **génération** : `_build_situation` assemble le prompt final (situation du
magasin, produits scorés, scripts RAG, stratégie, profil conseiller), puis la cascade LLM
s'exécute : Mistral d'abord, puis OpenRouter, Groq et Ollama en secours successifs. La réponse est
validée par `_is_valid_reply` ; si aucun fournisseur ne produit une réponse valide, un repli par
règles construit une réponse selon l'intention détectée (`_build_intent_fallback`).

Sixième étape, le **contrôle et la diffusion** : le Guardrail évalue la réponse, le badge de
vérification est poussé en WebSocket, la validation humaine est déclenchée si nécessaire, et la
conversation est persistée.

Septième étape, la variante **streaming** (`/stream`) : mêmes traitements, mais la réponse est
diffusée mot à mot en SSE dès les premiers tokens, avec des générateurs dédiés pour les cas
rapides (cache, salutation, hors-sujet).

**Entrées / sorties.** En entrée : message, identifiants conseiller et magasin, historique. En
sortie : réponse de coaching (streamée), produits scorés, badge guardrail, scripts cités.

### 6.5 Agent Guardrail (transverse) — le contrôleur de conformité

*Fichier : `agents/guardrail/guardrail_agent.py`.*

**Rôle.** Vérifier chaque réponse candidate avant affichage. C'est un agent volontairement **sans
LLM** : sept règles déterministes, donc rapide, prédictible et testable (34+ tests dédiés, 100 %
de réussite au banc d'évaluation).

**Les sept règles.** G1 « stock disponible » : ne jamais recommander un produit dont le stock est
à zéro. G2 « rupture imminente » : bloquer la promotion d'un produit dont la couverture est
inférieure à 3 jours (seuil configurable). G3 « source RAG » : la réponse doit s'appuyer sur des
scripts de la base de connaissances, pas sur une invention du modèle. G4 « règles métier » :
respect des règles commerciales Ooredoo (conditions d'offres). G5 « éligibilité réseau » :
l'offre proposée doit être éligible sur le réseau du client. G6 « confiance » : la confiance de
la recommandation doit atteindre 0,65 minimum. G7 « budget » : les actions proposées ne doivent
pas dépasser un plafond de 100 000 DT. Tous les seuils sont surchargeables par variables
d'environnement.

**Fonctionnement.** Chaque règle examine la recommandation et retourne une violation éventuelle.
La fonction `_compute_status` retient la **sévérité maximale** parmi les violations et produit le
verdict : `APPROVE` (diffusion directe), `REWRITE` (reformulation ou repli) ou `REJECT` (blocage,
repli sûr ou validation humaine). La fonction `route_guardrail` sert d'arête conditionnelle dans
les graphes LangGraph. Chaque évaluation émet un événement de traçabilité, diffusé en WebSocket
vers le badge du chat et le panel de monitoring.

**Entrées / sorties.** En entrée : la recommandation candidate, l'instantané d'inventaire,
l'indicateur d'usage du RAG, le score de confiance. En sortie : le verdict, la liste des
violations et l'événement de traçabilité.

### 6.6 Agent Analysis (Inventory) — le diagnosticien des stocks

*Fichiers : `inventory/agents/analysis/agent.py`, `nodes.py`.*

**Rôle.** Établir l'état de santé du stock d'un point de vente : combien de jours de couverture
reste-t-il par produit, à quelle vitesse chaque produit s'écoule, et quels produits sont en risque
(rupture, insuffisant, sain, surstock).

**Fonctionnement.** Le pipeline compte trois nœuds ; le graphe LangGraph est compilé une seule
fois au niveau de la classe et réutilisé pour toutes les exécutions. Le nœud `fetch_node` charge
les stocks et les ventes récentes depuis PostgreSQL via le repository d'inventaire. Le nœud
`compute_node` effectue les **calculs déterministes** : jours de couverture, vitesse d'écoulement,
point de commande, classement de risque par référence produit. Le nœud `reason_node` ajoute un
enrichissement LLM **optionnel** qui interprète les métriques et affine la qualification ; si le
LLM échoue ou est désactivé, un repli par règles (`_apply_rule_based_fallback`) applique les
seuils métier — les chiffres restent donc toujours fondés sur le calcul, jamais sur le modèle.
Le résultat est persisté pour le cycle en cours et les dashboards.

**Entrées / sorties.** En entrée : identifiant magasin, niveaux de stock, ventes, seuils. En
sortie : métriques par produit et alertes de rupture ou de surstock avec niveau d'urgence enrichi.

### 6.7 Agent Context (Inventory) — le capteur de demande

*Fichiers : `inventory/agents/context/agent.py`, `nodes.py`.*

**Rôle.** Expliquer et anticiper la **demande** : identifier ce qui va faire vendre plus ou moins
dans les jours à venir — événements, promotions, saisonnalité, tendances Ooredoo — pour que la
décision d'approvisionnement ne repose pas uniquement sur le passé.

**Fonctionnement.** Le nœud `fetch_signals_node` collecte les signaux : prévisions de demande
(moteurs Holt-Winters/TimesFM du module de forecasting), événements datés, offres et promotions
actives, tendances, et la chaîne causale fournisseur (table `supplier_products`, qui relie un
produit à ses fournisseurs et contraintes d'approvisionnement). Chaque signal est ensuite
transformé en **facteur de demande pondéré** par produit : par exemple, un festival à venir se
traduit par un facteur multiplicatif à la hausse sur les recharges et box 4G. Un enrichissement
LLM optionnel affine la pondération, puis le résultat est persisté.

**Entrées / sorties.** En entrée : prévisions, événements, promotions, saisonnalité. En sortie :
des facteurs de contexte pondérés par produit, directement consommables par l'Agent Decision.

### 6.8 Agent Decision (Inventory) — le décideur d'approvisionnement

*Fichiers : `inventory/agents/decision/agent.py`, `nodes.py`.*

**Rôle.** Convertir le diagnostic et le contexte en une **décision d'approvisionnement
explicable** : pour chaque produit, commander, accélérer, maintenir ou surveiller — avec la
quantité, le fournisseur et la justification.

**Fonctionnement.** L'agent consomme les sorties d'Analysis et de Context. La fonction
`_compute_adjusted_metrics` ajuste les métriques de stock (point de commande, quantité à
commander) avec les facteurs de demande du Context : c'est ici que « festival dans cinq jours »
devient concrètement « commander 20 % de plus que le calcul de base ». Le nœud `decide_node`
choisit ensuite l'action par produit en combinant règles métier et arbitrage LLM, en tenant
compte de l'objectif du magasin (privilégier la disponibilité ou maîtriser le coût). La
recommandation motivée est persistée (`_persist_recommendation`), puis — et c'est la
particularité de cet agent — `_suggest_purchase_order` **crée automatiquement un ordre d'achat au
statut « SUGGÉRÉ »** sur le Kanban, poussé en temps réel vers le frontend par le bus WebSocket
dédié. L'agent ne commande jamais directement : l'approbation humaine est obligatoire pour faire
passer la commande à l'étape suivante, et la réception de la marchandise met à jour le stock, ce
qui ferme la boucle d'approvisionnement.

**Entrées / sorties.** En entrée : diagnostic d'Analysis, facteurs de Context, contraintes
fournisseurs. En sortie : recommandations explicables et suggestions de commande sur le Kanban.

### 6.9 Superviseur Inventory — l'orchestrateur du module stock

*Fichier : `app/inventory/core/supervisor.py`.*

**Rôle et fonctionnement.** Équivalent du SupervisorAgent pour le module Inventory. Un nœud
d'entrée initialise l'état ; un routeur déclenche en parallèle (primitive LangGraph `Send`) les
branches inventory, sales et knowledge ; un nœud de fusion (`coach_fusion`) synthétise ; le
guardrail contrôle ; un routage conditionnel envoie le résultat soit directement en sortie, soit
vers une revue humaine. Le graphe est compilé une fois puis mis en cache
(`get_or_build_supervisor`), ce qui évite le coût de reconstruction à chaque cycle.

### 6.10 Le serveur MCP maison — les outils exposés

*Fichier : `app/inventory/services/mcp_server.py`.*

Le serveur MCP expose sept outils d'inventaire, consommés à la fois par les agents internes et par
des clients MCP externes : `get_stock_status` (niveaux, couverture, statut par magasin et
produit), `compute_inventory_metrics` (rotation, DIO, taux de rupture), `get_forecast_summary`
(prévision de demande agrégée), `suggest_purchase_order` (construction d'un PO candidat avec
quantité, fournisseur et coût), `get_purchase_order` et `list_purchase_orders` (consultation), et
`move_purchase_order` (déplacement d'une commande sur le Kanban — en préservant la porte HITL :
les transitions nécessitant une approbation humaine ne peuvent pas être contournées par l'outil).

---

## 7. Récapitulatif des agents

| Agent | Module | Rôle | Entrées principales | Sorties |
|---|---|---|---|---|
| **SupervisorAgent** | Orchestration | Coordonne les branches parallèles, fusionne l'état, route (guardrail / HITL / fallback) | Requête conseiller, RetailState | Réponse validée + événements frontend |
| **Analyste** | Sales | Séries temporelles : KPIs, anomalies, prévisions (Holt-Winters + backtest WAPE) | Historique ventes PostgreSQL | Diagnostic chiffré, prévisions multi-horizon |
| **Stratège** | Sales | Événements datés, offres actives, plan d'action commercial cross-domaine | Événements, offres, tendances, stock critique | Actions priorisées + carte de chaleur |
| **Coach** | Sales | Dialogue avec le conseiller : arguments, produits scorés, scripts RAG | Sorties des branches + RAG Milvus | Réponse coaching (streaming SSE) |
| **Guardrail** | Transverse | 7 règles déterministes : stock, sources, budget, confiance | Réponse candidate | Verdict APPROVE / REWRITE / REJECT |
| **Analysis** | Inventory | Couverture, rotation, classification des risques stock | Stocks, ventes, seuils | Indicateurs et alertes par produit |
| **Context** | Inventory | Facteurs de demande : prévisions, promos, événements, saisonnalité | Prévisions, événements, offres | Facteurs pondérés par produit |
| **Decision** | Inventory | Action + quantité de réapprovisionnement justifiées | Analyse + contexte + contraintes fournisseurs | Suggestions PO sur le Kanban |

---

## 8. Les flux principaux du système

### 8.1 Le cycle événementiel (AlertBus → agents → feedback)

Ce flux est le mode de fonctionnement autonome du système. Une source d'événements (le flux de
ventes réel ou le simulateur temps réel) produit un signal : pic de ventes, chute anormale, seuil
de stock franchi. Ce signal est publié sur l'**AlertBus** (publish/subscribe Redis), qui déclenche
un cycle du SupervisorAgent. Celui-ci exécute l'Analyste (analyse des séries temporelles), puis le
Stratège (synthèse cross-domaine ventes + stocks + événements), et soumet le résultat au
Guardrail. Une fois validé, le résultat est poussé en WebSocket vers le dashboard : les widgets
analyste et stratège se mettent à jour sans action de l'utilisateur. Enfin, l'utilisateur peut
noter chaque résultat (« utile » / « pas utile ») ; ce feedback est persisté (migration 0008) et
réinjecté dans les cycles suivants.

### 8.2 Le flux de chat du Coach

Le conseiller tape un message dans le chat Angular, envoyé sur `/chat` ou `/stream`. Le backend
enchaîne : classification d'intention et cache, injection du contexte Stratège serveur-side
(timeout borné), recherche RAG (Milvus puis corpus local), outils cross-domaine (alertes stock,
produits scorés, substituts), cascade LLM (Mistral → OpenRouter → Groq → Ollama), contrôle
Guardrail, puis diffusion : les tokens de la réponse arrivent en SSE, les puces de produits scorés
et le badge guardrail accompagnent la réponse, et la conversation est persistée.

### 8.3 La boucle d'approvisionnement fermée (Kanban + HITL)

L'Agent Analysis détecte une rupture imminente ; l'Agent Decision génère un ordre d'achat au
statut « SUGGÉRÉ », qui apparaît instantanément sur le Kanban Angular via WebSocket. Un humain
approuve, modifie ou rejette. En cas d'approbation, la commande progresse : EN ATTENTE, puis EN
LIVRAISON, puis REÇU — la réception met à jour le stock, ce qui referme la boucle. En cas de
rejet, le feedback est enregistré. La règle invariante est qu'**aucune commande n'est exécutée
sans passage par la porte HITL** (`app/api/hitl.py` + panneau Angular) ; l'auto-confirmation
(`po_auto_confirm`) ne s'applique qu'aux transitions explicitement autorisées.

---

## 9. Contrats API : entrées et sorties externes

### 9.1 Endpoints REST (préfixe `/api/v1` sauf mention)

| Endpoint | Méthode | Entrée | Sortie |
|---|---|---|---|
| `/auth/login`, `/auth/me` | POST/GET | identifiants / JWT | token, profil, RBAC store-level |
| `/chat`, `/stream` | POST | message, advisor, store | réponse coach / flux SSE |
| `/api/v1/supervisor/run`, `/async` | POST | store_id, déclencheur | RetailState final / identifiant de job |
| `/stores/{id}/metrics`, `/live-analysis`, `/product-mix` | GET | store_id | KPIs, analyse live, mix produits |
| `/forecast/eod/{id}`, `/hourly/{id}`, `/forecast-benchmark` | GET | store_id | prévisions EOD/horaire, benchmark |
| `/api/inventory/status/{store_id}`, `/alerts/{store_id}` | GET | store_id | stocks, alertes d'urgence |
| `/purchase-orders/*` (`suggest`, `approve`, `reject`) | POST/GET | PO / décision humaine | Kanban PO, statuts |
| `/hitl/pending` + résolutions | GET/POST | — / décision | actions en attente d'approbation |
| `/feedback` | POST | note humaine sur cycle/widget | persistée, réinjectée aux agents |
| `/monitoring/*` (`agents`, `guardrail-events`, `costs`, `logs`) | GET | filtres | santé agents, événements guardrail, coûts LLM |
| `/kpis`, `/cycles` | GET | période | KPIs d'évaluation, historique des cycles |

### 9.2 Canaux temps réel

Le WebSocket `/ws/store/{store_id}` pousse vers le frontend les widgets analyste et stratège, les
événements guardrail et le fil d'inventaire du magasin. Le WebSocket `/ws/advisor/{advisor_id}`
pousse le coaching personnalisé du conseiller. Le bus WebSocket du Kanban (`po_ws_bus`) notifie en
direct chaque création ou mouvement de commande. Enfin, le SSE de `/stream` transporte les tokens
du Coach au fil de la génération.

---

## 10. La couche données

**PostgreSQL** est la source de vérité : une cinquantaine de tables dont le schéma est géré
exclusivement par les migrations **Alembic 0001 à 0008** — aucun DDL n'est exécuté à l'exécution,
aucune donnée de production ne provient de CSV ou de valeurs codées en dur. Les ventes
(`ooredoo_sales`) sont contraintes par sept clés étrangères, les index nécessaires aux requêtes
d'analyse sont en place, et la table `supplier_products` matérialise la chaîne causale
produit-fournisseur utilisée par le module Inventory.

Le **jeu de données de séries temporelles** compte environ 1,49 million de lignes journalières
sur 4,5 ans, avec des caractéristiques saisonnières complètes : c'est la matière première de
l'Agent Analyste et des moteurs de prévision.

**Redis** joue trois rôles : cache (prévisions, contexte, réponses du Coach), bus d'alertes
(publish/subscribe qui déclenche les cycles événementiels) et support de la limitation de débit.

**Milvus** héberge les plus de 200 scripts de vente vectorisés pour le RAG ; si Milvus est
indisponible, le système bascule sur un corpus local sans interrompre le service.

**Langfuse** trace chaque exécution : spans par nœud de graphe, générations LLM avec coûts. Son
initialisation est non bloquante (un correctif a éliminé les blocages au démarrage).

La **fabrique LLM** (`llm_factory.py`) abstrait les fournisseurs : Mistral en primaire, avec
bascule automatique vers OpenRouter, Groq puis Ollama.

Les **moteurs de prévision** sont hiérarchisés : Holt-Winters saisonnier validé par backtest WAPE
en primaire, TimesFM préchargé au démarrage, Chronos désactivable par variable d'environnement
(`DISABLE_CHRONOS`), et un repli SQL en dernier recours.

---

## 11. Principes transverses de conception

1. **Monolithe modulaire.** Un seul package `app/`, deux domaines (`sales`, `inventory`)
   découplés par le bus d'alertes et l'état partagé `RetailState`, dont les reducers n'acceptent
   que des deltas — jamais d'écrasement d'état entre branches parallèles.
2. **LLM hors chemin critique.** Les calculs déterministes (moteur de séries temporelles,
   métriques de stock) ne dépendent d'aucun appel LLM ; le modèle enrichit la formulation, il ne
   bloque jamais le flux et ne produit jamais un chiffre.
3. **Human-in-the-loop systématique.** Toute action à impact (commande, action stratégique
   sensible) passe par une porte d'approbation humaine avant application.
4. **Guardrail en sortie.** Chaque réponse visible par l'utilisateur est validée par sept règles
   déterministes avant affichage, avec badge de statut visible.
5. **Dégradation contrôlée.** Chaque dépendance a un repli : Milvus → corpus local ;
   Holt-Winters → régression linéaire → SQL ; Mistral → OpenRouter → Groq → Ollama ;
   raisonnement LLM → règles métier. Aucune panne de composant ne bloque le flux principal.
6. **Observabilité de bout en bout.** Langfuse pour les LLM, monitoring des agents, événements
   guardrail historisés, suite d'évaluation `evals/` (quatre bancs).
7. **Zéro dérive de schéma.** Alembic est l'unique source de vérité du schéma ; l'introspection
   des clés étrangères passe par `pg_constraint`.

---

## 12. Besoins fonctionnels — Module Sales

> Codification **BF-S**, dans le même cadre que les besoins du module Inventory (BF-1.x à BF-3.x).

### 1. Coaching commercial en temps réel

- **BF-S-1.1 — Assistant conversationnel de coaching :** le conseiller de vente doit pouvoir
  interagir en langage naturel avec un assistant qui répond à ses questions sur les produits,
  les ventes et les actions à mener dans son point de vente.
- **BF-S-1.2 — Recommandations de produits à proposer :** le système doit suggérer au conseiller
  les produits les plus pertinents à mettre en avant, classés par un score tenant compte des ventes,
  de la disponibilité en stock et du contexte du moment.
- **BF-S-1.3 — Scripts et arguments de vente :** le système doit fournir des scripts et arguments
  de vente adaptés à la situation (produit, moment de la journée, profil client), issus d'une base
  de connaissances métier.
- **BF-S-1.4 — Réponse progressive en temps réel :** les réponses de l'assistant doivent s'afficher
  au fur et à mesure de leur génération afin que le conseiller puisse les exploiter sans délai.

### 2. Analyse des performances de vente

- **BF-S-2.1 — Consultation des indicateurs de vente :** l'utilisateur doit pouvoir consulter les
  indicateurs clés de son point de vente (volumes, tendances, comparaisons périodiques) sous forme
  de tableaux de bord synthétiques.
- **BF-S-2.2 — Prévisions de ventes :** le système doit produire des prévisions de ventes
  multi-horizons afin d'anticiper l'activité et d'alimenter les décisions commerciales et de stock.
- **BF-S-2.3 — Détection d'anomalies et de tendances :** le système doit détecter automatiquement
  les comportements de vente atypiques (chute, pic, rupture de tendance, saisonnalité) et les
  signaler à l'utilisateur.
- **BF-S-2.4 — Prise en compte du profil du conseiller :** le coaching doit pouvoir s'adapter à
  l'historique et au profil du conseiller afin de personnaliser les recommandations.

### 3. Contexte marché et stratégie commerciale

- **BF-S-3.1 — Intégration du contexte marché :** le système doit intégrer les événements datés
  (festivals, périodes particulières) et les offres commerciales actives dans ses analyses et
  recommandations, en distinguant les événements en cours et à venir.
- **BF-S-3.2 — Recommandations stratégiques cross-domaine :** le système doit produire des
  recommandations combinant les signaux de vente et l'état des stocks, afin d'éviter par exemple
  de promouvoir un produit en risque de rupture.
- **BF-S-3.3 — Alertes commerciales proactives :** le système doit notifier l'utilisateur en temps
  réel des situations nécessitant une action commerciale, sans que celui-ci ait à les rechercher.

### 4. Contrôle et fiabilité des réponses

- **BF-S-4.1 — Contrôle automatique des réponses :** chaque réponse générée doit être vérifiée par
  un agent de contrôle (Guardrail) avant affichage : conformité métier, absence de contenus sensibles
  ou erronés, avec indication visible du statut de vérification.
- **BF-S-4.2 — Validation humaine des cas sensibles :** lorsque la réponse ou la décision est jugée
  sensible, le système doit la soumettre à une validation humaine avant application (Human-in-the-Loop).
- **BF-S-4.3 — Réponse de repli :** en cas de réponse jugée non conforme, le système doit fournir
  une réponse de repli sûre plutôt qu'une absence de réponse ou un contenu risqué.

### 5. Besoins fonctionnels transverses

- **BF-T-1 — Orchestration multi-agents :** le système doit coordonner l'exécution parallèle des
  agents spécialisés (analyse, connaissance, contexte, stock) et fusionner leurs résultats en une
  réponse unique et cohérente.
- **BF-T-2 — Authentification et contrôle d'accès :** l'accès aux fonctionnalités et aux données
  doit être restreint selon le rôle de l'utilisateur et son point de vente (RBAC store-level).
- **BF-T-3 — Boucle de feedback :** les décisions humaines (acceptation, modification, refus des
  recommandations) doivent être enregistrées et exploitables pour améliorer les recommandations futures.
- **BF-T-4 — Communication inter-modules :** le module Sales doit pouvoir consulter l'état des
  stocks et les alertes du module Inventory, et réciproquement le module Inventory doit exploiter
  les prévisions de vente, via des outils partagés.

---

## 13. Besoins non fonctionnels — compléments Sales

> Les besoins non fonctionnels définis pour le module Inventory (BNF-1 à BNF-5 : performance,
> disponibilité, sécurité, utilisabilité, traçabilité) s'appliquent à l'ensemble du système.
> Les besoins suivants les complètent pour la partie Sales et l'orchestration multi-agents.

- **BNF-S-1 — Latence du coaching conversationnel :** le premier élément de réponse de l'assistant
  doit apparaître dans un délai de quelques secondes, le raisonnement multi-agents étant exécuté
  en parallèle et le premier token diffusé en streaming.
- **BNF-S-2 — Résilience aux fournisseurs LLM :** en cas d'indisponibilité du fournisseur de modèle
  principal, le système doit basculer automatiquement vers un fournisseur alternatif sans
  interruption du service.
- **BNF-S-3 — Dégradation contrôlée des composants :** l'indisponibilité d'un composant (base
  vectorielle, service de prévision, cache) doit déclencher un mécanisme de repli — corpus local
  pour le RAG, méthode statistique pour les prévisions — sans bloquer le flux principal.
- **BNF-S-4 — Observabilité des agents :** chaque exécution du graphe multi-agents doit être tracée
  (entrées, sorties, durées, coûts LLM) afin de permettre l'analyse des performances et l'évaluation
  continue de la qualité des réponses.
- **BNF-S-5 — Limitation de charge :** les points d'accès exposés doivent être protégés par une
  limitation de débit afin de préserver la stabilité du service et de maîtriser la consommation
  des API de modèles.

---

*Sources code : `app/sales/orchestration/supervisor_agent.py` (graphe superviseur sales),
`app/inventory/core/supervisor.py` (graphe inventory), `app/sales/coaching/agents/`
et `app/inventory/agents/` (agents), `app/sales/coaching/agents/analyst/ts_engine.py`
(moteur séries temporelles), `app/sales/coaching/agents/coach/coach_chat.py` (chat coach),
`app/sales/coaching/agents/guardrail/guardrail_agent.py` (7 règles),
`app/inventory/services/mcp_server.py` (outils MCP).
Voir aussi : `docs/ARCHITECTURE_V6_FLUX_FEEDBACK.md`, `docs/DATABASE_SCHEMA.md`.*
