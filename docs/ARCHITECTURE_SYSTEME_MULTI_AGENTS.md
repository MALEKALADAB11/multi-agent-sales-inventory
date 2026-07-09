# Architecture du Système Multi-Agents — État Actuel

> Document descriptif de l'architecture en production sur la branche `refactor/monolith-v2`.
> Date : 2026-07-09 — Aucun code, uniquement le fonctionnement, les entrées/sorties, les données et les relations entre agents.

---

## 1. Vue d'ensemble

Le système est un **moteur agentique retail temps réel** pour les boutiques Ooredoo Tunisie, organisé en un monolithe Python unique (`app/`) exposé par FastAPI. Il couvre deux domaines métier qui coopèrent :

| Domaine | Package | Mission | Agents |
|---|---|---|---|
| **Ventes (coaching)** | `app/sales/` | Coacher les vendeurs en boutique en temps réel : détecter les écarts de performance, produire des stratégies et des conseils personnalisés | Analyste, Stratège, Coach, Guardrail, Supervisor |
| **Inventaire** | `app/inventory/` | Optimiser les stocks : métriques de réapprovisionnement, signaux de demande, décisions de commande | Analysis, Context, Decision |

Trois couches transversales relient l'ensemble :

1. **Orchestration** — SupervisorAgent (graphe maître), CycleOrchestrator (cycle sales), InventoryOrchestrator (batch stocks), CoachStrategeOrchestrator (résilience Coach↔Stratège).
2. **Communication** — bus d'alertes Redis Pub/Sub, State Bus Redis Streams, WebSockets frontend, serveur MCP.
3. **Contrôle humain (HITL)** — Guardrail 7 règles, revues manager, Kanban de bons de commande à approbation humaine, boucle de feedback.

### Flux global résumé

```
Données POS / stocks / contexte externe (météo, fériés, événements)
        │
        ▼
┌─ Domaine VENTES ──────────────┐   ┌─ Domaine INVENTAIRE ─────────────┐
│ Analyste (gap, urgence)       │   │ Analysis ─┐ (parallèles)         │
│    ▼                          │   │ Context ──┴─► Decision           │
│ Stratège (actions, cause)     │   │      (reorder / hold / monitor)  │
│    ▼                          │   └───────────┬──────────────────────┘
│ Coach (conseil personnalisé)  │◄── snapshot ──┤
│    ▼                          │── pulse* ────►│   (*partiel, voir §6)
│ Guardrail (validation)        │◄── alertes ───┘
└───────────────┬───────────────┘
                ▼
   HITL (manager) → Frontend Angular → Feedback → réinjection
```

---

## 2. Données du système

### 2.1 PostgreSQL — base `ooredoo_sales` (~50 tables, source de vérité Alembic 0001→0009)

| Schéma | Contenu | Producteurs | Consommateurs |
|---|---|---|---|
| `sales` | `transactions_rt` (ventes du jour temps réel), `transactions` (historique 1,49 M lignes journalières, 4,5 ans, features saisonnières), `produits` (catalogue), `boutiques` (magasins + géoloc), `objectifs` (cibles CA) | Simulateur RT / POS | Analyste, Coach (outils cross-domain), Context Agent, Decision Agent, moteur de forecast |
| `inventory` | `stock_levels`, `sales_history`, `recommendations`, `context_adjustments`, `agent_runs`, `supplier_products`, mouvements de stock (migration 0009) | Analysis, Context, Decision | Coach (snapshot stock), Guardrail (G1/G2), monitoring, frontend |
| `supply` | `purchase_orders` (Kanban PO : SUGGERE → APPROUVE → COMMANDE → RECU) | Decision Agent (suggestions), manager (transitions) | MCP Kanban, frontend Kanban, boucle de clôture stock |
| `public` | `hitl_reviews` (revues manager), tables feedback (migration 0008) | Guardrail/Stratège/Decision (escalades), utilisateurs | UI manager, Feedback Collector |

### 2.2 Autres magasins de données

| Store | Rôle |
|---|---|
| **Milvus** | Base vectorielle RAG — 200+ scripts de vente Ooredoo indexés ; fallback corpus local (CSV) si Milvus indisponible |
| **Redis** | Trois usages : (a) cache LRU stratégies et caches applicatifs, (b) bus d'alertes Pub/Sub, (c) State Bus en Streams (`cycle_state:{store}`, `events:{store}`, `inventory_snapshot:{store}`, `feedback:{store}`) |
| **APIs externes** | Météo (réelle, géolocalisée par boutique), jours fériés tunisiens, événements locaux |
| **Forecast** | TimesFM (préchargé au démarrage) + moteur time-series maison (Holt ~4,4 % d'erreur, benchmark ≥ Prophet) + fallback SQL ; Chronos désactivé (DLL torch) |

### 2.3 Points d'entrée (FastAPI + WebSockets)

- `POST /api/v1/coach/chat` et `POST /api/v1/coach/stream` (SSE) — chat vendeur ↔ Coach.
- `POST /api/v1/cycle/trigger` — cycle de coaching manuel ; CronTrigger toutes les 15 min.
- `/api/inventory/*` — analyse batch, stocks, recommandations ; `/api/v1/supervisor` — cycle maître.
- Routes HITL, feedback, KPIs, forecast, monitoring, stores, auth (RBAC au niveau boutique).
- WebSockets frontend Angular : événements guardrail, flux inventaire, Kanban PO.
- Serveur MCP standalone : outils inventaire + Kanban PO exposés aux clients MCP.

---

## 3. Domaine Ventes — les agents du coaching

Tous les agents sales partagent le même état LangGraph `SalesAgentState` (TypedDict à champs optionnels) qui circule de nœud en nœud.

### 3.1 Agent Analyste — diagnostic de performance (architecture ReAct)

**Rôle.** Mesurer en continu la performance de la boutique par rapport à ses objectifs et qualifier l'urgence.

**Graphe.** `receive_pos → validate_data → load_memory → react_analyst → build_strategy_query → save_memory`. Le nœud central `react_analyst` est un véritable agent ReAct (boucle Raisonner/Agir) qui a remplacé six nœuds statiques.

**Entrées.**
- Transactions POS du jour (`sales.transactions_rt`) : CA, panier moyen, nombre de tickets.
- Objectif journalier (`sales.objectifs`, fallback : moyenne historique 30 jours).
- Mémoire des analyses précédentes (comparaison inter-cycles).

**Outils à disposition du nœud ReAct.** Prévision TimesFM/fallback SQL, `detect_anomalies`, `ts_decomposition` (tendance/saisonnalité), `forecast_multi_horizon`, `product_velocity` — quatre outils time-series robustes ; accès inventaire possible via le client MCP.

**Traitement.** Calcule l'écart objectif/réalisé (`gap_pct`, `gap_amount`), projette le CA de fin de journée (`forecast_eod`), détecte anomalies et catégories sous-performantes, qualifie l'urgence (LOW → CRITICAL avec score 0–1), identifie la tendance intraday (accélération/stable/décélération).

**Sorties.**
- Champs écrits dans l'état : `gap_pct`, `gap_amount`, `forecast_eod`, `urgency_level`, `urgency_score`, `analyst_summary`, `underperforming_categories`, `intraday_trend`.
- Une **`strategy_query`** structurée — c'est le contrat de déclenchement de l'agent Stratège.
- Alertes ventes publiées sur le canal Redis `alerts:store:{id}:sales`.
- Mémoire persistée en fin de graphe.

### 3.2 Agent Stratège — stratégie commerciale (pattern Reflexion)

**Rôle.** Transformer un diagnostic (« la boutique est à −30 % ») en stratégie actionnable (« pousser tel produit, tel argument, telle priorité »).

**Graphe.** `fetch_context → rag_search → analyze_context → generate_strategy → build_output → self_critique`.

**Entrées.** La `strategy_query` de l'Analyste (gap, urgence, catégories concernées) via l'état partagé.

**Données consommées.**
- Contexte externe : météo réelle de la boutique, jours fériés, événements locaux (`fetch_context`).
- Scripts de vente similaires via RAG Milvus, fallback corpus local (`rag_search`).
- Catalogue produits et stocks filtrés (injectés par le StateMerger, voir §5.3).

**Traitement.** `analyze_context` identifie la **cause racine** de l'écart (météo ? férié ? mix produit ?) ; `generate_strategy` fait générer par le LLM des actions concrètes ancrées dans les scripts RAG ; `self_critique` auto-évalue la cohérence et la complétude (pattern Reflexion) et produit un `critique_score`.

**Sorties.** `strategie` (résumé), `strategie_actions` (liste priorisée : action, produit cible, priorité), `focus_produits`, `cause_racine`, `message_manager`, indicateurs contexte (météo, férié), `critique_score`, `rag_used`/`nb_rag_scripts`. Si `critique_passed=False` ET urgence CRITICAL/HIGH ET gap > 40 % → escalade HITL (`public.hitl_reviews`).

### 3.3 CoachStrategeOrchestrator — médiateur de résilience (non-LLM)

**Rôle.** Garantir que le Coach ne plante **jamais**, même si le Stratège est lent ou hors service.

**Fonctionnement.** Pipeline en quatre étages : (1) cache LRU 30 min — si le `gap_pct` est stable, réponse < 1 ms ; (2) invocation du Stratège avec timeout 30 s ; (3) retry ×3 avec backoff exponentiel ; (4) fallback à trois niveaux — cache périmé → actions contextuelles calculées → template statique.

**Entrée.** Demande du Coach (gap, contexte boutique). **Sortie.** `StrategieOutput` : actions, provenance (`success`/`cached`/`stale_cache`/`fallback`), confiance, latence, et `extras` (cause racine, résumé stratégie, focus produits, météo, score de critique) que le Coach utilise pour ancrer ses réponses.

### 3.4 Agent Coach — génération du conseil vendeur

**Rôle.** Interlocuteur direct du vendeur : produire un conseil personnalisé, cross-domaine ventes + stocks.

**Graphe.** `load_context → rag_search → load_advisor_history → invoke_stratege_for_coach → generate_conseil → save_conseil`. Câblé serveur-side sur `/chat` et `/stream` (SSE token par token) depuis la v11, avec warm-up du Stratège en arrière-plan.

**Entrées.** Message du vendeur, identité/profil du conseiller (`advisor_id`), `store_id`.

**Données consommées.**
- Contexte boutique temps réel via **8 outils cross-domain** : contexte sales (CA, gap, panier depuis `sales.transactions_rt`), santé du stock (lecture directe du repo inventaire), scoring produit.
- Scripts RAG Milvus + historique personnel du conseiller (profil advisor).
- `StrategieOutput` du Stratège via l'orchestrateur de résilience.

**Traitement.** Le LLM (Mistral primaire) génère un conseil ancré dans les actions du Stratège et le contexte réel. Le **scoring cross-domaine** fusionne ventes et inventaire par produit :
`score final = 0,30×alignement gap ventes + 0,20×santé stock + 0,15×marge + 0,15×priorité promo + 0,10×adéquation vendeur + 0,10×adéquation client`.

**Sorties.** Conseil streamé au frontend (après passage Guardrail), `scored_products` (chips produits classés), conseil persisté pour le suivi et le feedback.

### 3.5 Guardrail Agent — critique et validation (déterministe, non-LLM)

**Rôle.** Aucune recommandation du Coach n'atteint le frontend sans validation.

**Entrées.** Recommandation du Coach + snapshot inventaire + indicateur `rag_used` + score de confiance.

**Les 7 règles.** G1 produit disponible en stock ; G2 pas de produit en rupture imminente (< 3 jours) sauf déstockage ; G3 arguments commerciaux issus d'une source RAG fiable ; G4 aucune remise/offre non autorisée ; G5 pas de recommandation 5G/Fibre si client non éligible ; G6 confiance ≥ 0,65 sinon réécriture/escalade ; G7 commandes > 100 000 DT → approbation manager. Seuils surchargeables par variables d'environnement.

**Sorties.** `status` ∈ {**APPROVE** (envoi tel quel), **REWRITE** (retour au Coach avec feedback, 1 itération max), **ESCALATE** (route HITL manager), **BLOCK** (remplacement par un fallback sûr)} + liste des règles violées. Les verdicts sont poussés en WebSocket vers le badge chat et le panneau de monitoring Angular.

### 3.6 SupervisorAgent — orchestrateur maître

**Rôle.** Graphe LangGraph de plus haut niveau qui exécute un cycle complet de bout en bout. Exposé sur `/api/v1/supervisor` (activable par configuration).

**Topologie.**
```
initialize_state
   → [sales_branch ∥ knowledge_branch ∥ context_branch ∥ inventory_branch]   (fan-out parallèle)
   → merge_outputs (RetailState unifié)
   → coach_agent (scoring cross-domaine)
   → guardrail_agent
        ├─ approve  → notify_frontend
        ├─ rewrite  → coach_agent (1 itération max)
        ├─ escalate → human_validation → notify_frontend
        └─ block    → safe_fallback → notify_frontend
   → save_memory → END
```

**Branches.** `sales_branch` enveloppe le CycleOrchestrator (Analyste → Stratège) ; `knowledge_branch` la recherche RAG du Stratège ; `context_branch` le contexte externe complet ; `inventory_branch` appelle l'analyse inventaire (aujourd'hui par import direct de la fonction de route — voir §6.2).

**État.** `RetailState` — TypedDict unifié à champs optionnels : identification du cycle (`cycle_id`, `store_id`, `trigger_type` ∈ {scheduled_cycle, advisor_message, stock_event, sales_event, manager_action}), entrées brutes (POS, stock, contexte, message), puis les sorties de chaque branche sous convention `<branche>_<champ>`. Aucune branche en échec ne supprime de champ : absent = vide pour le nœud suivant (dégradation gracieuse).

---

## 4. Domaine Inventaire — les agents du stock

Les trois agents sont des graphes LangGraph compilés **une fois par processus** (cache de classe, verrou double-vérifié) — optimisation majeure : le batch de 110 SKUs est passé de ~15 min à un temps raisonnable en supprimant 330 recompilations. Chaque exécution ouvre/ferme une ligne `inventory.agent_runs` (santé agent, monitoring).

### 4.1 Analysis Agent — métriques baseline

**Graphe.** `fetch → compute → reason`.

**Entrées.** `sku`, `store_id`, `business_objective` (ex. balanced), plus les données pré-chargées en batch par l'orchestrateur (stock courant, fiche produit) pour éviter 330+ connexions DB par run.

**Données consommées.** `inventory.stock_levels`, historique de ventes, prévisions de demande — DB d'abord, fallback CSV.

**Traitement.** `compute` calcule de façon déterministe : EOQ (quantité économique de commande), stock de sécurité, point de commande, jours de stock restants, et une **classification de risque à deux couches**. `reason` fait intervenir le LLM (tier FAST — OpenRouter) comme **évaluateur, pas narrateur** : il détecte les conflits entre dimensions (ex. risque faible mais tendance de vente contradictoire) et lève un `analyst_flag`.

**Sorties.** `analysis_report` = {stock, forecast, metrics, risk_assessment, constraints, objective_note, analyst_flag, reasoning_source, report_type=BASELINE}. **Persistance** : mise à jour de `remaining_days_of_stock` dans `inventory.stock_levels`. En cas d'échec : dictionnaire d'erreur (le Decision Agent ne peut alors pas tourner pour ce SKU).

### 4.2 Context Agent — signaux de demande externes

**Graphe.** `fetch_signals → interpret`.

**Entrées.** `sku`, `store_id`.

**Données consommées** (collecte 100 % Python, sans LLM) :
- **Patterns historiques** : uplifts réellement observés lors d'événements/promos passés sur la catégorie du produit (`inventory.sales_history`).
- Promotions actives, météo (géolocalisée via `sales.boutiques`), jours fériés, événements en base.
- Catégorie produit résolue via `sales.produits`.

**Traitement.** Le LLM (tier FAST, fallback règles produisant la même structure) évalue le moment présent à travers le prisme historique et produit un uplift calibré.

**Sorties.** `context_report` = {**`demand_uplift_pct`** pour les 7 prochains jours, `interpretation`, `confidence` (high/medium/low), `dominant_signal`, `signals` détaillés (météo, promos, fériés, événements, patterns)}. Passé **en mémoire** au Decision Agent (le résultat n'est jamais relu depuis la DB). **Persistance** : `inventory.context_adjustments` (piste d'audit pour le monitoring, validité datée du jour à J+7) + `agent_runs`. En cas d'erreur : le Decision Agent applique uplift = 0.

### 4.3 Decision Agent — recommandation actionnable

**Graphe.** `constraints_check → decide` ; si `constraints_check` détecte un blocage dur, il pose lui-même la décision et court-circuite directement vers la fin.

**Entrées.** `baseline_report` (Analysis) + `context_report` (Context) — les deux agents amont tournent **en parallèle** ; dégradation gracieuse si le contexte manque (uplift 0), erreur si l'analyse manque.

**Traitement.** L'uplift de demande est appliqué aux métriques baseline **avant** l'exécution du graphe → métriques ajustées (EOQ, stock de sécurité, point de commande recalculés). `constraints_check` vérifie les contraintes métier ; le LLM **tier SMART** (raisonnement fort, décision critique) tranche : ORDER / EXPEDITE / HOLD / MONITOR, avec quantité, urgence et justification.

**Sorties et effets aval.**
- `inventory.recommendations` : une ligne par décision actionnable (reorder).
- **Alertes temps réel** : si action ∈ {EXPEDITE, ORDER} ou risque CRITICAL → publication sur le bus d'alertes Redis sales (`alerts:store:{id}:stock`) avec valeurs 100 % dynamiques (stock, jours restants, CA à risque, top seller) — c'est ce qui peut déclencher un cycle de coaching (voir §5.4). Un second bus inventaire (`alerts:{store_id}` + historique trié TTL 1 h) alimente monitoring et SMS gateway.
- **Suggestions de PO** : création de bons de commande au statut `SUGGERE` sur le Kanban (`supply.purchase_orders`) — porte HITL : seul un humain fait passer SUGGERE → APPROUVE → COMMANDE ; la réception (`RECU`) génère les mouvements de stock (migration 0009) et **ferme la boucle**.
- `escalate_to_human=True` possible → `public.hitl_reviews`.

### 4.4 InventoryOrchestrator — batch et cycles événementiels

**Rôle.** Piloter le pipeline des trois agents sur l'ensemble des SKUs (~110) d'une boutique.

**Fonctionnement.** Pré-chargement batch des données communes (objectif actif, stocks, fiches produits) → pool de threads sur les SKUs → pour chaque SKU : Analysis ∥ Context (pool interne à 2 threads, ils sont indépendants) puis Decision → dispatch asynchrone des alertes critiques sans bloquer le pipeline. Tracing Langfuse par pipeline (spans par agent/nœud/appel LLM). Déclenchement : planifié, manuel via API, ou événementiel (V6 : AlertBus → cycles).

---

## 5. Relations entre agents et mécanismes de communication

### 5.1 Chaînes de commandement (contrôle)

| Orchestrateur | Périmètre | Déclencheurs |
|---|---|---|
| **SupervisorAgent** | Cycle complet 4 branches → coach → guardrail | `/api/v1/supervisor`, activable par config |
| **CycleOrchestrator** (+ CronTrigger) | Cycle sales Analyste → Stratège | Cron 15 min, `/api/v1/cycle/trigger`, alertes stock (§5.4) |
| **InventoryOrchestrator** | Batch 3 agents × N SKUs | Planifié, API inventaire, événementiel V6 |
| **CoachStrategeOrchestrator** | Un seul lien : Coach → Stratège | Chaque message vendeur nécessitant une stratégie |

### 5.2 Bus d'alertes Redis (Pub/Sub) — plan événements

Canaux côté sales : `alerts:store:{id}:stock` (produit par le Decision Agent inventaire), `alerts:store:{id}:sales` (Analyste), `alerts:store:{id}:cross` (Stratège), `events:cycle:{cycle_id}` (orchestration). Le Coach souscrit à tous et évalue la priorité (latence cible ~300 ms jusqu'au frontend). Côté inventaire, un second bus (`alerts:{store_id}` + sorted set d'historique TTL 1 h) sert monitoring/WS/SMS. Fallback : Redis indisponible → log WARNING, pipeline non bloqué.

### 5.3 State Bus Redis Streams + StateMerger — plan données

- Streams : `cycle_state:{store}` (état complet du cycle), `events:{store}`, `inventory_snapshot:{store}` (**publié par l'InventoryOrchestrator**), `feedback:{store}` (CA réels pour le Feedback Collector).
- **StateMerger** (fan-in) : au début de chaque cycle sales, lit le snapshot stock depuis Redis (ignoré si > 10 min), filtre les produits disponibles (stock > 0), injecte `inventory_snapshot` + `stock_filtered_products` dans l'état sales, et applique la règle **G9 STOCK_INTEGRITY** : retire les SKUs en rupture des recommandations du Stratège *avant* la génération du conseil.

### 5.4 Déclenchement événementiel croisé (AlertTrigger)

Le module d'écoute souscrit au pattern `alerts:store:*:stock` et déclenche un **cycle de coaching immédiat** sur la boutique concernée quand une alerte critique arrive (rupture détectée par le simulateur RT ou le Decision Agent). Garde-fous anti-tempête : seul le canal `:stock` est écouté (les canaux `:sales` et `:cross` sont produits par les cycles eux-mêmes — boucle infinie sinon) ; debounce par magasin (180 s par défaut) ; seules les priorités URGENT/HIGH déclenchent.

### 5.5 Balancing Engine — fusion des priorités Sales × Inventory

Moteur de scoring multi-critères : `score = w1×alignement gap + w2×santé stock + w3×marge + w4×urgence rupture + w5×adéquation vendeur`. Toutes les valeurs proviennent de la DB ou du snapshot Redis (zéro valeur codée en dur). Utilisé par le Coach pour classer les produits à pousser (`scored_products`).

### 5.6 Serveur MCP

Serveur MCP standalone exposant les outils inventaire aux clients externes : `get_stock_status`, `compute_inventory_metrics`, `get_forecast_summary`, `suggest_purchase_order`, et les 4 outils Kanban PO (`list/get/move_purchase_order`) avec la porte HITL préservée (aucun outil ne peut approuver un PO). L'Analyste sales l'utilise aussi via un client MCP interne.

### 5.7 HITL et boucle de feedback

- **`public.hitl_reviews`** : alimentée par le Stratège (critique échouée + urgence haute + gap > 40 %) et le Decision Agent (`escalate_to_human`). UI manager dédiée (panneau Angular) pour valider/rejeter.
- **Kanban PO** : approbation humaine obligatoire des commandes suggérées ; la réception ferme la boucle stock.
- **Feedback humain** (migration 0008) : retours des managers/vendeurs sur conseils et recommandations, persistés et réinjectés dans les cycles suivants (Feedback Collector via le stream `feedback:{store}`).

### 5.8 Observabilité et niveaux de LLM

- **Langfuse** : traces par pipeline avec spans par agent/nœud/appel LLM ; `inventory.agent_runs` trace chaque exécution d'agent inventaire.
- **Tiers LLM** : FAST (OpenRouter) pour Analysis/Context (interprétation, évaluation), SMART pour Decision (décision critique), Mistral primaire pour le Coach. Chaque agent inventaire a un mode `use_llm=False` entièrement à base de règles (même structure de sortie) pour la vitesse ou la panne.
- **Frontend Angular 21** (Signals) : dashboard, chat coach SSE, panneau HITL, Kanban PO, 3 flux WebSocket (guardrail, inventaire, PO), RBAC au niveau boutique, responsive mobile.

---

## 6. Matrice des échanges inter-domaines — état actuel

### 6.1 Ce qui circule aujourd'hui

| Flux | Mécanisme | Contenu | Fraîcheur |
|---|---|---|---|
| Inventory → Sales | Snapshot Redis (`inventory_snapshot:{store}`) + StateMerger | Stocks par SKU, produits disponibles | Rejeté si > 10 min |
| Inventory → Sales | Bus d'alertes (`alerts:store:{id}:stock`) → AlertTrigger | Ruptures critiques → cycle coaching immédiat | Temps réel (~300 ms) |
| Inventory → Sales | Lectures directes du repo inventaire par les outils cross-domain du Coach et le Guardrail | Santé stock par produit, jours restants | À la demande |
| Sales → Inventory | SQL direct sur `sales.produits`, `sales.boutiques`, `sales.transactions` | Catalogue, géoloc boutiques, historique de ventes | À la demande |
| Sales → Inventory | *(inexistant)* | Le pulse temps réel de l'Analyste (gap, urgence, vélocité du jour) **n'atteint pas** le Context/Decision Agent | — |

### 6.2 Points de couplage connus (dette architecturale)

1. Le Decision Agent (inventaire) importe le bus d'alertes du package sales — dépendance inversée, tolérée par un try/except ImportError.
2. Le SupervisorAgent importe une **fonction de route FastAPI** de l'inventaire pour sa branche inventory — mélange couche HTTP / couche domaine.
3. Le Coach et le CoachStrategeOrchestrator importent directement le repository interne de l'inventaire (3+ points) — aucun contrat de lecture.
4. L'inventaire fait du SQL direct sur les tables du schéma `sales` — pas de vue/contrat de lecture.
5. **Deux bus d'alertes Redis** coexistent avec des conventions de canaux différentes (`alerts:store:{id}:stock` côté sales vs `alerts:{store_id}` côté inventaire).
6. Pas de flux Sales → Inventory temps réel : le Context Agent ne connaît la demande que par l'historique, jamais par le pulse du jour.

Ces points sont fonctionnels aujourd'hui (fallbacks systématiques) mais constituent la cible de la refonte de synchronisation envisagée (contrats versionnés, bus unifié, gateways de domaine, pulse Sales→Inventory) — voir la discussion d'architecture correspondante.

---

## 7. Récapitulatif entrées/sorties par agent

| Agent | Entrées principales | Données lues | Sorties | Persistance |
|---|---|---|---|---|
| **Analyste** (sales) | POS du jour, objectifs, mémoire | `sales.transactions_rt`, `sales.objectifs`, forecast TimesFM/SQL | gap %, urgence, forecast EOD, `strategy_query`, alertes sales | Mémoire agent |
| **Stratège** (sales) | `strategy_query` | Météo/fériés/événements, RAG Milvus, stocks filtrés | actions priorisées, cause racine, message manager, score critique | HITL si critique échouée |
| **Coach** (sales) | Message vendeur, profil conseiller | Contexte sales + stock (8 outils), RAG, historique advisor, `StrategieOutput` | Conseil streamé SSE, `scored_products` | Conseil sauvegardé |
| **Guardrail** (sales) | Reco Coach + snapshot stock + confiance | Snapshot inventaire, règles G1–G7 | APPROVE / REWRITE / ESCALATE / BLOCK + violations | Événements WS, HITL |
| **Supervisor** (sales) | Requête cycle, trigger_type | RetailState (fan-in 4 branches) | Réponse coach validée, routage | Checkpoint mémoire |
| **Analysis** (inventaire) | sku, store, objectif métier | stock_levels, historique, forecast | `analysis_report` BASELINE (EOQ, ROP, risque 2 couches) | `stock_levels.remaining_days_of_stock` |
| **Context** (inventaire) | sku, store | uplifts historiques, promos, météo, fériés, événements | `demand_uplift_pct` J+7, signal dominant, confiance | `context_adjustments`, `agent_runs` |
| **Decision** (inventaire) | `analysis_report` + `context_report` | Contraintes métier, métriques ajustées | ORDER/EXPEDITE/HOLD/MONITOR, quantité, urgence | `recommendations`, PO `SUGGERE`, alertes Redis, `agent_runs` |
