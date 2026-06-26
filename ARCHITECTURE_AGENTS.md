# Architecture Multi-Agents — Inventory & Sales

> Système agentique dual pour Ooredoo Tunisia Retail  
> Inventory : optimisation stocks temps réel | Sales : coaching conseiller temps réel

---

## 1. VUE D'ENSEMBLE

```
┌─────────────────────────────────────────────────────────────────────┐
│                        INVENTORY MODULE                             │
│                                                                     │
│  Orchestrator (batch 8 workers)                                     │
│    ├── [parallel] AnalysisAgent  →  fetch → compute → reason       │
│    ├── [parallel] ContextAgent   →  fetch_signals → interpret       │
│    └──            DecisionAgent  →  decide  →  persist DB           │
│                                              ↓                      │
│                              MonitoringAgent (lecture DB)           │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                          SALES MODULE                               │
│                                                                     │
│  CycleOrchestrator (trigger: cron / WebSocket)                      │
│    ├── [1] AnalystAgent   →  receive_pos → … → react_analyst → …   │
│    ├── [2] StrategistAgent →  fetch_context → … → self_critique     │
│    └── [3] CoachAgent     →  load_context → … → save_conseil        │
│               └── node_invoke_stratege ← CoachStrategeOrchestrator  │
│                                          (cache LRU + retry + fallb) │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. INVENTORY MODULE

### 2.1 Agents

| Agent | Fichier | Nodes (séquence) | Pattern | Rôle |
|---|---|---|---|---|
| **InventoryAnalysisAgent** | `inventory-module/src/agents/analysis/agent.py` | `fetch → compute → reason` | Pipeline 3 nodes | Calcule métriques baseline stock + classification risque (CRITICAL / HIGH / MEDIUM / LOW) |
| **InventoryContextAgent** | `inventory-module/src/agents/context/agent.py` | `fetch_signals → interpret` | Pipeline 2 nodes | Traduit signaux externes (events, promos, météo, historique) en `demand_uplift_pct` calibré (7j) |
| **InventoryDecisionAgent** | `inventory-module/src/agents/decision/agent.py` | `decide` | Single node | Fusionne analysis + context → recommandation finale (ORDER / HOLD / MONITOR / EXPEDITE) |
| **InventoryOrchestrator** | `inventory-module/src/services/orchestrator.py` | *(pas un graphe LangGraph)* | ThreadPool + pipeline | Chef d'orchestre : séquence les 3 agents, gère batch 8 workers, pre-fetch DB |
| **MonitoringAgent** | `monitoring-module/monitoring.py` | *(FastAPI endpoints)* | Read-only polling | Expose KPIs d'exécution : latence, taux succès, coût tokens — lecture `agent_runs` DB |

---

### 2.2 Détail des nodes par agent

#### AnalysisAgent — `fetch → compute → reason`

| Node | Type | Ce qu'il fait |
|---|---|---|
| `fetch` | Python pur | Lit stock, produit, historique ventes depuis DB (ou CSV fallback) — utilise `preloaded_stock` / `preloaded_product` en batch |
| `compute` | Python pur | Calcule EOQ, safety stock, reorder point, days_of_stock — intègre profil saisonnier 3 ans (`get_seasonal_demand_profile`) |
| `reason` | LLM / rule-based | Détecte conflits cross-dimensionnels, produit `risk_assessment` + `analyst_flag` — LLM comme évaluateur, pas narrateur |

#### ContextAgent — `fetch_signals → interpret`

| Node | Type | Ce qu'il fait |
|---|---|---|
| `fetch_signals` | Python pur | Collecte météo, promotions actives, jours fériés, événements marché, patterns historiques depuis `inventory.sales_history` |
| `interpret` | LLM / rule-based | Synthétise tous les signaux → `demand_uplift_pct`, `confidence`, `dominant_signal`, `interpretation` |

#### DecisionAgent — `decide`

| Node | Type | Ce qu'il fait |
|---|---|---|
| `decide` | LLM / rule-based | Reçoit `baseline_report` + `context_report`, re-calcule métriques ajustées avec uplift, génère action + `order_qty` + `rationale` |

---

### 2.3 Synchronisation inter-agents inventory

| Étape | De | Vers | Canal | Données transmises | Mode |
|---|---|---|---|---|---|
| **1** | Orchestrator | AnalysisAgent | `.run()` | `sku`, `store_id`, `business_objective`, `preloaded_stock`, `preloaded_product` | Synchrone |
| **2** | Orchestrator | ContextAgent | `.run()` | `sku`, `store_id` | **Parallèle** avec AnalysisAgent (ThreadPool) |
| **3** | AnalysisAgent → | DecisionAgent | dict en mémoire | `analysis_report` : `{stock, forecast, metrics, risk_assessment, constraints}` | In-process |
| **4** | ContextAgent → | DecisionAgent | dict en mémoire | `context_report` : `{demand_uplift_pct, confidence, dominant_signal, signals}` | In-process |
| **5** | DecisionAgent | PostgreSQL | `SyncInventoryRepo` | `inventory.recommendations` (ORDER / EXPEDITE uniquement) | Write |
| **6** | ContextAgent | PostgreSQL | `SyncInventoryRepo` | `inventory.context_adjustments` (audit trail 7j) | Write |
| **7** | AnalysisAgent | PostgreSQL | `SyncInventoryRepo` | `inventory.stock_levels.remaining_days_of_stock` | Write |
| **8** | Tous agents | PostgreSQL | `SyncInventoryRepo` | `inventory.agent_runs` (start / complete, status, latence) | Write |
| **9** | PostgreSQL | MonitoringAgent | FastAPI GET | `agent_runs`, `recommendations`, `context_adjustments` | Read polling |

---

### 2.4 Flux batch (110 SKUs)

```
Orchestrator.analyze_batch(skus=[...], store_id="I63")
  │
  ├── Pre-fetch DB (3 requêtes au lieu de 330)
  │     get_active_objective()       → resolved_objective, service_level_target
  │     get_stock_levels_batch()     → preloaded_stock {sku: stock_current}
  │     get_products_batch()         → preloaded_products {sku: product_row}
  │
  └── ThreadPoolExecutor(max_workers=8)
        per SKU → _run_pipeline(sku)
          ├── AnalysisAgent.run()   ─┐ parallèle
          ├── ContextAgent.run()    ─┘
          └── DecisionAgent.run(analysis_report, context_report)
                └── persist → inventory.recommendations
```

> **Optimisation clé** : le graphe LangGraph est compilé **une seule fois** (singleton `_compiled_graphs`) et réutilisé pour les 110 SKUs × 3 agents. Avant : 330 `workflow.compile()` = 15 minutes. Après : < 2 minutes.

---

### 2.5 Dégradation inventory

| Agent en échec | Comportement |
|---|---|
| AnalysisAgent | DecisionAgent ne peut pas tourner — retourne `{"error": ...}` |
| ContextAgent | DecisionAgent continue avec `demand_uplift_pct = 0` (baseline sans uplift) |
| DB indisponible | Les 3 agents tournent en lecture-seule CSV — aucune écriture |
| LLM indisponible | `use_llm=False` → rule-based fallback sur les 3 agents, même structure de sortie |

---

## 3. SALES MODULE

### 3.1 Agents

| Agent | Fichier | Nodes (séquence) | Pattern | Rôle |
|---|---|---|---|---|
| **AnalystAgent** | `sales-module/modules/coaching/agents/analyst/agent.py` | `receive_pos → validate_data → load_memory → react_analyst → build_strategy_query → save_memory` | **ReAct** (Reason + Act) | Lit POS temps réel, calcule gap objectif, urgence, forecast EOD, résumé analytique |
| **StrategistAgent** | `sales-module/modules/coaching/agents/stratege/agent.py` | `fetch_context → rag_search → analyze_context → generate_strategy → build_output → self_critique` | **Reflexion** (self-critique) | Génère actions concrètes depuis gap + contexte + scripts RAG, s'auto-évalue |
| **CoachAgent** | `sales-module/modules/coaching/agents/coach/agent.py` | `load_context → rag_search → load_advisor_history → invoke_stratege_for_coach → generate_conseil → save_conseil` | **Conversationnel** | Répond au message du conseiller en intégrant actions du Stratège + historique advisor |

---

### 3.2 Les 2 orchestrateurs sales

| Orchestrateur | Fichier | Mode | Rôle |
|---|---|---|---|
| **CycleOrchestrator** | `sales-module/orchestration/graph.py` | LangGraph async + routing conditionnel | Pipeline principal : Analyste → Stratège → (Coach si message) — trace Langfuse hiérarchique complète |
| **CoachStrategeOrchestrator** | `sales-module/modules/coaching/orchestrator/coach_stratege_orchestrator.py` | Résilient — cache LRU + retry | Appelé depuis node 4 du Coach : cache 30 min → invoke timeout 30s → retry ×3 → fallback 3 niveaux |

---

### 3.3 Détail des nodes par agent

#### AnalystAgent — ReAct (6 nodes)

| Node | Type | Ce qu'il fait |
|---|---|---|
| `receive_pos` | Python pur | Ingère données POS temps réel (CA, target, heure fermeture) |
| `validate_data` | Python pur | Vérifie cohérence et complétude des données reçues |
| `load_memory` | Python pur | Charge historique du store depuis PostgreSQL / BigQuery |
| `react_analyst` | **LLM ReAct** | Raisonne + appelle outils (TimesFM forecast, calcul gap, détection urgence, résumé) — remplace 6 nodes statiques |
| `build_strategy_query` | Python pur | Formate la requête pour le StrategistAgent |
| `save_memory` | Python pur | Persist résultat en mémoire store pour le prochain cycle |

#### StrategistAgent — Reflexion (6 nodes)

| Node | Type | Ce qu'il fait |
|---|---|---|
| `fetch_context` | Python pur | Collecte météo, jours fériés, événements du moment |
| `rag_search` | Python pur | Recherche scripts similaires dans Milvus (RAG) |
| `analyze_context` | LLM | Identifie cause racine + facteurs contextuels du gap |
| `generate_strategy` | LLM + RAG | Génère liste d'actions concrètes et priorisées |
| `build_output` | Python pur | Formate la sortie finale pour le frontend |
| `self_critique` | LLM | Auto-évalue cohérence et complétude des actions — pattern Reflexion |

#### CoachAgent — Conversationnel (6 nodes)

| Node | Type | Ce qu'il fait |
|---|---|---|
| `load_context` | Python pur | Charge contexte store + profil conseiller |
| `rag_search` | Python pur | Recherche scripts de vente similaires (Milvus) |
| `load_advisor_history` | Python pur | Charge historique des conseils précédents pour ce conseiller |
| `invoke_stratege_for_coach` | **Orchestrateur résilient** | Appelle CoachStrategeOrchestrator → actions du Stratège (cache / invoke / fallback) |
| `generate_conseil` | LLM | Génère le conseil personnalisé conseiller en intégrant actions + contexte + historique |
| `save_conseil` | Python pur | Persist le conseil en DB pour traçabilité et enrichissement RAG |

---

### 3.4 Synchronisation inter-agents sales

| Étape | De | Vers | Canal | Données transmises | Mode |
|---|---|---|---|---|---|
| **1** | CycleOrchestrator | AnalystAgent | `ainvoke(state)` | `pos_data` (store_id, daily_target, current_revenue, closing_hour) | Async |
| **2** | AnalystAgent → state | StrategistAgent | `SalesAgentState` partagé | `urgency_level`, `gap_objectif`, `forecast_eod`, `analyst_summary` | Async séquentiel |
| **3** | CycleOrchestrator | StrategistAgent | `ainvoke(state)` | State enrichi par Analyste | Toujours déclenché |
| **4** | StrategistAgent → state | CoachAgent | `SalesAgentState` partagé | `strategie_actions[]`, `cause_racine`, `focus_produits`, `strategie` | Conditionnel (si `coach_message`) |
| **5** | CoachAgent node 4 | CoachStrategeOrchestrator | `orchestrator.invoke(state)` | State complet (gap, urgence, store_id) | Async interne |
| **6** | CoachStrategeOrchestrator | StrategistAgent | `ainvoke(state)` | State filtré (cache miss uniquement) | Cache LRU 30 min ou invoke |
| **7** | StrategistAgent → | CoachAgent node 4 | `StrategieOutput.actions` | Liste d'actions concrètes | Retour vers node 4 |
| **8** | Tous agents | PostgreSQL / RAG | `agent_logger`, `rag_retriever` | Logs cycles, scripts, historique conseiller | Write async non-bloquant |

---

### 3.5 Routing conditionnel (CycleOrchestrator)

| Après | Condition | Destination |
|---|---|---|
| AnalystAgent | Toujours | → StrategistAgent |
| StrategistAgent | `pos_data.coach_message` présent | → CoachAgent |
| StrategistAgent | Pas de message | → END |
| CoachAgent | Toujours | → END |

---

### 3.6 Fallback CoachStrategeOrchestrator (4 niveaux)

| Niveau | Condition | Action | Latence |
|---|---|---|---|
| **Cache hit** | `gap_pct` stable, entrée < 30 min | Retourne actions cachées | < 1 ms |
| **Invoke normal** | Cache miss | Appel StrategistAgent, timeout 30s, retry ×3 backoff exponentiel | 3–48 s |
| **Stale cache** | Stratège down, cache expiré | Retourne actions périmées | < 1 ms |
| **Fallback L2** | Pas de cache du tout | Actions contextuelles générées par règles métier | < 5 ms |
| **Fallback L3** | Tout échoue | Template statique — CoachAgent ne plante **jamais** | < 1 ms |

---

### 3.7 Flux cycle complet sales

```
CycleOrchestrator.run_cycle(store_id, triggered_by="cron")
  │
  ├── [1] AnalystAgent.ainvoke(state)
  │     receive_pos → validate_data → load_memory
  │       → react_analyst (ReAct: TimesFM + gap calc + urgence)
  │       → build_strategy_query → save_memory
  │     └── output: urgency_level, gap_objectif, forecast_eod, analyst_summary
  │
  ├── [2] StrategistAgent.ainvoke(state)    ← toujours
  │     fetch_context → rag_search → analyze_context
  │       → generate_strategy → build_output → self_critique
  │     └── output: strategie_actions[], cause_racine, focus_produits
  │
  └── [3] CoachAgent.ainvoke(state)         ← si coach_message présent
        load_context → rag_search → load_advisor_history
          → invoke_stratege_for_coach
          │     └── CoachStrategeOrchestrator
          │           ├── cache LRU hit  → actions < 1ms
          │           ├── invoke Stratège → actions 3–48s
          │           └── fallback        → actions règles
          → generate_conseil → save_conseil
        └── output: conseil_final (personnalisé conseiller)
```

---

## 4. COMPARAISON INVENTORY vs SALES

| Dimension | Inventory Module | Sales Module |
|---|---|---|
| **Déclenchement** | Batch planifié (cron) ou API | Cron + WebSocket (message conseiller temps réel) |
| **Parallélisme** | ThreadPool 8 workers (Analysis ‖ Context) | Séquentiel async (Analyste → Stratège → Coach) |
| **État partagé** | Dict Python passé en mémoire entre agents | `SalesAgentState` TypedDict LangGraph partagé |
| **Persistence inter-agents** | DB uniquement en fin de pipeline | Aucune DB entre agents — state in-memory |
| **LLM calls par cycle** | 3 (reason + interpret + decide) | 3–5 (react_analyst + analyze_context + generate_strategy + self_critique + generate_conseil) |
| **Fallback LLM** | `use_llm=False` → rule-based sur les 3 agents | Fallback par niveaux dans CoachStrategeOrchestrator |
| **Observabilité** | Langfuse + `inventory.agent_runs` PostgreSQL | Langfuse hiérarchique (trace → span par agent) + `agent_logger` |
| **Volume** | 110 SKUs × 8 stores = ~880 analyses / run | 1 cycle / store / déclenchement |
| **RAG** | Non | Oui (Milvus — scripts de vente + historique cycles) |

---

## 5. ÉTAT D'IMPLÉMENTATION

| Composant | Statut |
|---|---|
| InventoryAnalysisAgent | Opérationnel |
| InventoryContextAgent | Opérationnel |
| InventoryDecisionAgent | Opérationnel |
| InventoryOrchestrator (batch 8 workers) | Opérationnel |
| MonitoringAgent | Endpoints actifs — données partiellement mockées |
| AnalystAgent (ReAct) | Opérationnel |
| StrategistAgent (Reflexion + self-critique) | Opérationnel |
| CoachAgent (conversationnel) | Opérationnel |
| CycleOrchestrator (Analyste → Stratège → Coach) | Opérationnel |
| CoachStrategeOrchestrator (cache + retry + fallback) | Implémenté — intégration `main.py` en cours (`SETUP_COACH_STRATEGE.md`) |
| Données time-series | 1.49M lignes journalières, 4.5 ans (Jan 2022 → Jul 2026) |
| TimesFM Forecaster | Intégré dans AnalystAgent (via MCP tools) |
