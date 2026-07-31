# Architecture globale — Système multi-agents Ooredoo (Sales + Inventory)

> Document dérivé **uniquement du code source** (points d'entrée, orchestrateurs, graphes d'agents, triggers, temps réel, MCP, API, frontend).
> Diagramme visuel associé : [`ARCHITECTURE_GLOBALE.drawio`](./ARCHITECTURE_GLOBALE.drawio) (ouvrir dans [draw.io / diagrams.net](https://app.diagrams.net)).

---

## 1. Vue d'ensemble

Le projet est un **moteur agentique retail temps réel** unifiant deux domaines : **coaching de vente** (sales) et **optimisation des stocks** (inventory). Un seul serveur FastAPI (`app/main.py`, exposé via le shim `main.py` → `uvicorn main:app`) héberge :

- Des **agents LangGraph** (chacun = un graphe d'états compilé en singleton)
- Un **méta-orchestrateur** (`SupervisorAgent`) qui fait tourner 4 branches en parallèle
- Des **déclencheurs** (cron, événement-vente, alerte-stock Redis)
- Un **simulateur temps réel** de ventes qui alimente tout le système
- Des canaux de sortie **WebSocket + REST + Redis pub/sub + MCP**

**Stack :** FastAPI · LangGraph / LangChain · PostgreSQL (schémas `sales` / `inventory` / `supply` / `monitoring` / `public`) · Redis (bus d'alertes) · Milvus (RAG vectoriel) · LLM multi-fournisseurs (Mistral / Groq / OpenRouter / Ollama) · Angular 21 (frontend).

---

## 2. Entrées et Sorties globales (Input / Output)

### 2.1 Entrées (ce qui déclenche / nourrit le système)

| Source d'entrée | Mécanisme | Point de code |
|---|---|---|
| **Ventes temps réel** (simulées) | `RealtimeSimulator` émet `on_sale` / `on_stockout` | `app/main.py` → `_on_sale` (L325) |
| **Connexion frontend au dashboard** | WebSocket `/ws/store/{store_id}` | `app/main.py` (L1485) |
| **Message conseiller (chat coach)** | POST `/api/v1/coach/chat` et `/stream` | `coach_chat.py` (L1582) |
| **Timer cron 15 min** | `CronTrigger` → `run_cycle` | `app/main.py` (L503) |
| **Alerte stock critique** | Redis pub/sub `alerts:store:*:stock` | `alert_trigger.py` |
| **Actions manager** (approuver PO, HITL, objectifs) | REST POST | `supply_routes.py`, `hitl.py` |
| **Données PG** | POS, historique, stock, forecasts | `postgres_provider.py`, `inventory_repo.py` |

### 2.2 Sorties (ce que le système produit)

| Sortie | Canal | Contenu |
|---|---|---|
| **Payload dashboard** (`metrics_update`) | WebSocket | CA, gap, urgence, forecast EOD, actions stratège, heatmap, advisors, hourly, stock health — construit par `_build_payload` (L972) |
| **Événements temps réel** | WebSocket | `realtime_stock_update`, `stockout`, `stock_alert`, `analyst_update`, `inventory_alerts`, `coach_recommendation` |
| **Réponse coach** | REST / SSE | Conseil de vente ancré données + RAG |
| **Recommandations inventory** | PG `inventory.recommendations` + Kanban PO | ORDER / EXPEDITE / HOLD / MONITOR |
| **Suggestions de bon de commande** | WebSocket Kanban (`po_suggested`) | statut SUGGERE |
| **Traces d'observabilité** | Langfuse + `logs/errors.log` + PG `agent_runs` | spans hiérarchiques par cycle |

---

## 3. L'état partagé : `RetailState`

Pierre angulaire de la communication inter-agents. C'est un `TypedDict` unique (`app/sales/core/retail_state.py`) où **chaque agent n'écrit que ses propres champs**. Trois canaux ont des *reducers* obligatoires car écrits par plusieurs branches parallèles dans le même superstep LangGraph :

```python
agents_invoked : Annotated[List[str], operator.add]   # concaténation
metrics        : Annotated[Dict, _merge_dict]          # fusion de dicts
errors         : Annotated[List[str], operator.add]
```

**Contrat critique** (répété dans le code) : chaque node retourne **uniquement son delta**, jamais l'état cumulé — sinon `InvalidUpdateError` ou duplication. Les champs sont regroupés par branche : `_SALES_BRANCH_KEYS`, `inventory_decisions`, `rag_context`, `external_context`, `coach_recommendation`, `guardrail_*`, `hitl_*`.

Il existe aussi un `SalesAgentState` (`app/sales/core/state.py`) plus ancien, utilisé par le pipeline sales séquentiel (Analyste → Stratège → Coach).

---

## 4. Les agents en détail (mécanisme interne)

Chaque agent est un **graphe LangGraph compilé en singleton** (compilé une fois par process, réutilisé — car un graphe compilé est *stateless*).

### 4.1 Agent Analyste (Sales) — déterministe temps réel

`analyst/agent.py` — graphe 7 nodes :
```
receive_pos → validate_data → load_memory → ts_analyst
            → compare_with_memory → build_strategy_query → save_memory → END
```
Le cœur est **`ts_analyst`** (`ts_engine.py`) : **zéro LLM dans le chemin critique** (< 1 s). Pipeline statistique pur :
1. Holt-Winters saisonnier (période 7) + backtest rolling-origin → MAPE réel
2. Profil intraday par jour de semaine
3. **Prévision EOD hybride** (modèle + déroulé intraday, pondéré par la part de journée écoulée)
4. **Gap horaire** (attendu vs réel, z-score, statut OK / WATCH / ALERT)
5. Prévision h+1..h+3
6. Urgence composite + faisabilité (ACHIEVED / ACHIEVABLE / CHALLENGING / VERY_HARD / CLOSED)

Cascade de modèles : modèle global entraîné (rang 0, WAPE 33 %) → statsforecast AutoETS → Holt-Winters numpy interne.

**Sortie :** `forecast_eod`, `gap_pct`, `urgency_level`, `hourly_gaps`, `next_hours_forecast`, `analyst_summary`, et surtout `build_strategy_query` qui construit la requête que le Stratège consommera.

### 4.2 Agent Stratège (Sales) — Reflexion pattern

`stratege/agent.py` — 6 nodes :
```
fetch_context → rag_search → analyze_context → generate_strategy → build_output → self_critique → END
```
- `fetch_context` : météo réelle (Open-Meteo), jours fériés, événements / festivals (`market.events`), promos Ooredoo
- `rag_search` : Milvus → scripts de vente similaires
- `generate_strategy` : LLM → actions concrètes (`strategie_actions` avec priorité, produit cible, argument)
- `self_critique` : auto-évalue cohérence / complétude (pattern Reflexion)

**Dépendance de données réelle :** le Stratège consomme la `rag_query` construite par l'Analyste → c'est pourquoi Analyste et Stratège sont **séquentiels** dans le CycleOrchestrator, pas parallèles.

### 4.3 Agent Coach — conseiller conversationnel RAG

`coach_chat.py` — le plus riche (~2500 lignes). Architecture « persona-first » :
1. **Classification d'intent** (`_classify_intent`) : greeting / off_topic / recap / inventory / cross_domain / coaching / conversation
2. **Loaders de contexte sélectifs** (parallèles, timeouts courts) : POS, détail ventes, contexte inventaire complet (alertes, top-sellers, recos agent, PO en cours, décisions humaines), profil conseiller
3. **RAG unifié cross-domaine** : scripts `[S*]`, playbooks `[I*]`, fiches produit `[P*]`, décisions `[D*]` — Milvus sémantique + fallback lexical corpus embarqué (ne tombe jamais à vide)
4. **Stratège câblé serveur-side** (`_get_stratege_for_chat`) via orchestrateur résilient (cache 30 min, timeout borné, warm en arrière-plan)
5. **Substituts anti-hallucination** : si produit nommé en rupture, propose des substituts réellement en stock (SQL par gamme / quartile de prix + scoring agents)
6. **Retry 4 niveaux** : Mistral → OpenRouter → OpenRouter stripped → Ollama → fallback intent
7. **Dedup cache 20 s**

Le prompt système impose une **hiérarchie de sources** stricte (agents vivants > SITUATION > fiches `[P*]` > scripts) et interdit toute invention de prix / SKU.

### 4.4 Agent Guardrail — validation

`guardrail_agent.py` — `guardrail_node` valide la recommandation du Coach et renvoie un statut routant : **APPROVE / REWRITE / ESCALATE / BLOCK** + issues + safe_fallback.

### 4.5 Chaîne Inventory — 3 agents

Orchestrés par `InventoryOrchestrator` (`orchestrator.py`) qui traite les SKU en **batch parallèle (8 workers)** :

```
Par SKU :  [Analysis ∥ Context]  →  Decision
```
- **Analysis** (`analysis/agent.py`) : `fetch → compute → reason`. Calcule EOQ, point de commande, safety stock, jours de couverture, niveau de risque (DB-first). LLM = *évaluateur* de conflits, pas narrateur.
- **Context** (`context/agent.py`) : `fetch_signals → interpret`. Apprend les uplifts historiques d'événements / promos et produit un `demand_uplift_pct` calibré sur 7 jours.
- **Decision** (`decision/agent.py`) : `constraints_check → decide`. Fusionne analysis + context (uplift appliqué aux métriques), produit ORDER / HOLD / MONITOR / EXPEDITE. Persiste dans `inventory.recommendations` **et auto-suggère un bon de commande** (statut SUGGERE) poussé au Kanban via WebSocket — boucle fermée avec approbation humaine.

Optimisations clés notées dans le code : graphes compilés en singleton de classe (évite 330 `compile()` par batch), 3 requêtes DB batch au lieu de 330, dispatch alertes Redis non bloquant.

### 4.6 SupervisorAgent — le méta-orchestrateur

`supervisor_agent.py` — c'est le **cerveau qui unifie tout**. Topologie :

```
                        ┌─→ sales_branch ──────┐
                        │   (Analyste→Stratège) │
START → initialize ─────┼─→ knowledge_branch ───┤
                        │   (RAG Milvus)         │
                        ├─→ context_branch ──────┼─→ merge_outputs → coach_agent → guardrail
                        │   (météo/events)       │                                    │
                        └─→ inventory_branch ────┘                    ┌───────────────┤
                            (analyze_store)                           │  route selon statut
                                                                      ▼
                          approve  → notify_frontend ──────────┐
                          rewrite  → coach_agent (1 boucle)     ├→ save_memory → END
                          escalate → human_validation (HITL) ───┤
                          block    → safe_fallback ─────────────┘
```

- Les **4 branches tournent en parallèle** (fan-out LangGraph). Chacune wrappe du code existant et écrit une **whitelist de champs** dans `RetailState` (jamais `**result` — sinon conflit parallèle sur `external_context` / `cycle_id`).
- `coach_agent` fait le **scoring cross-domaine** (`rank_products`, formule pondérée 6 critères) fusionnant Sales + Inventory + RAG + Context → `coach_recommendation` + `scored_products`.
- `guardrail_agent` route ; `human_validation` soumet à la file HITL ; `notify_frontend` broadcast WS ; `save_memory` persiste dans `coach_interactions` (mémoire RAG future).

---

## 5. Les orchestrateurs (3 niveaux)

| Orchestrateur | Rôle | Fichier |
|---|---|---|
| **CycleOrchestrator** | Pipeline sales séquentiel Analyste → Stratège → Coach + trace Langfuse hiérarchique | `graph.py` |
| **InventoryOrchestrator** | Batch parallèle des SKU (Analysis ∥ Context → Decision) | `orchestrator.py` |
| **SupervisorGraph** | Méta : lance les 4 branches + coach + guardrail + HITL | `supervisor_agent.py` |

Le `SupervisorAgent.sales_branch` **réutilise le CycleOrchestrator singleton** créé au démarrage (`app/main.py` L471), et `inventory_branch` appelle `analyze_store` (route inventory) en mode `fast=True` (règles déterministes, pas de LLM par SKU pour tenir le budget temps).

---

## 6. Les déclencheurs (quand les agents tournent)

Trois voies complémentaires, câblées au startup (`app/main.py` L503-565) :

1. **CronTrigger** (15 min) — cycle proactif sur les top-N boutiques par CA.
2. **SaleEventTrigger** — **deux étages** (`sale_trigger.py`) :
   - **Étage 1** (~20 s après dernière vente, coalescence / debounce) : recalcul analytique déterministe seul (< 1 s, zéro LLM) → poussé au frontend (`analyst_update`).
   - **Étage 2** (cycle agent complet) : **seulement si** l'urgence monte, le gap bouge de ≥ 5 pts, ou la faisabilité se dégrade — avec garde-fou de coût `min_full_cycle_s = 180 s`.
3. **AlertCycleTrigger** (`alert_trigger.py`) : souscrit Redis `alerts:store:*:stock`, déclenche un cycle immédiat sur rupture critique (debounce 180 s, priorités URGENT / HIGH seulement). N'écoute **pas** `:sales` / `:cross` pour éviter les boucles infinies.

Plus la boucle WS elle-même (`_agent_loop`) : tant qu'un dashboard est connecté, un cycle tourne toutes les 2 min.

---

## 7. Le temps réel — cœur de la boucle vente ↔ stock

Le `RealtimeSimulator` émet des ventes. Le callback `_on_sale` (`app/main.py` L325) fait, de façon non bloquante :

```
Vente simulée
   │
   ├─ sale_trigger.notify_sale()        → alimente l'étage 1/2 (compteur sync)
   ├─ décrément stock (trigger DB trg_sync_stock_on_sale, pas l'app)
   ├─ _live_stock[sku] mis à jour + record_sale (cache)
   └─ _persist_and_broadcast (async task) :
         ├─ WS "realtime_stock_update" (stock avant→après, sévérité)
         ├─ invalidate_store (cache inventory)
         └─ si critique/rupture :
               ├─ WS "stock_alert"
               └─ AlertBus Redis publish_stock_alert
                     └─→ AlertCycleTrigger → cycle de coaching immédiat
```

La persistance stock (décrément `stock_levels` + mouvement VENTE) est faite par un **trigger PostgreSQL** (`trg_sync_stock_on_sale`, migration 0001/0009), pas par l'application — évite le double décrément.

---

## 8. Communication globale (tous les canaux)

```
┌─────────────┐   WebSocket /ws/store/{id}      ┌──────────────────────────┐
│  Frontend   │◄────── metrics_update, ────────►│   FastAPI (app/main.py)  │
│  Angular 21 │        realtime_stock_update,    │  _broadcast (multi-conn, │
│  (signals)  │        analyst_update, ...       │   cache dernier payload) │
│             │                                  │                          │
│  websocket. │   REST /api/v1/coach/chat        │  ┌────────────────────┐  │
│  service.ts │◄──────── POST/SSE ──────────────►│  │  SupervisorGraph   │  │
│  api.ts     │                                  │  │  (4 branches ∥)    │  │
│  inventory- │   REST /api/inventory/*          │  └─────────┬──────────┘  │
│  api.service│◄──────── GET/POST ──────────────►│            │             │
│  purchase-  │   WS Kanban po_suggested         │  Cron/Sale/Alert triggers│
│  order-api  │◄────────────────────────────────►│            │             │
└─────────────┘                                  └────────────┼─────────────┘
                                                              │
              ┌───────────────┬──────────────────────┬───────┴────────┐
              ▼               ▼                      ▼                ▼
        PostgreSQL        Redis pub/sub          Milvus          LLM APIs
     sales/inventory/    alerts:store:*:*      (RAG vectoriel)  Mistral/Groq/
     supply/monitoring   (bus d'alertes)                        OpenRouter/Ollama
              ▲
              │ MCP stdio (inventory-advisor)
        ┌─────┴──────┐
        │ MCP server │  get_stock_status, get_forecast_summary,
        │(mcp_server)│  suggest_purchase_order, move_purchase_order...
        └────────────┘
```

- **WebSocket** : canal principal push dashboard. `_broadcast` gère le multi-connexion + résolution d'alias boutique (`store-lac2` → `I63` canonique) + cache du dernier payload renvoyé immédiatement à toute nouvelle connexion.
- **REST** : ~90 endpoints (auth, cycle, forecast, stores, inventory, supply / Kanban, HITL, KPIs, monitoring, coach, supervisor). Voir `@router` dans `app/api/` et `app/inventory/api/`.
- **Redis** : bus d'alertes découplant producteurs (simulateur, DecisionAgent) et consommateur (AlertCycleTrigger).
- **MCP** : serveur `inventory-advisor` exposant les outils stock / PO en stdio (mêmes repos que le REST → règles identiques).
- **Frontend** (`websocket.service.ts`, `api.ts`) : Angular 21 à Signals, features dashboard / inventory / chat / purchase-board / monitoring / requests.

---

## 9. Workflow bout-en-bout (scénario complet)

**Exemple : une vente fait basculer la boutique en zone critique**

```
1.  Simulateur vend "iPhone 16 Pro" (I63) → _on_sale
2.  Trigger DB décrémente stock_levels ; WS "realtime_stock_update" poussé au dashboard
3.  sale_trigger.notify_sale() incrémente le compteur
4.  ~20 s plus tard (debounce) → Étage 1 : ts_engine.analyze_store()
        → gap monte à 32 %, urgence LOW→HIGH → WS "analyst_update"
5.  Changement matériel détecté → Étage 2 : orchestrator.run_cycle()
        → CycleOrchestrator : Analyste → Stratège (météo+RAG+actions) → Coach
        → si urgence HIGH : AlertBus publish_sales_alert
6.  En parallèle, le stock critique a publié alerts:store:I63:stock
        → AlertCycleTrigger → 2e cycle immédiat
7.  Le dashboard WS reçoit un metrics_update complet (_build_payload) :
        CA/gap/urgence, forecast EOD + IC, hourly_performance réel,
        strategie_actions, advisors, stock_health, coaching_cards
8.  Côté inventory : DecisionAgent a produit une reco ORDER
        → inventory.recommendations + PO SUGGERE → WS Kanban "po_suggested"
9.  Le manager approuve le PO (REST) ; le conseiller demande au Coach un script
        → /api/v1/coach/chat : intent=script, RAG [S*]+[P*], stratège câblé,
          substituts si rupture → conseil ancré données
10. Guardrail valide/escalade ; save_memory persiste pour le RAG futur
```

---

## 10. Points d'architecture remarquables (issus du code)

- **Séparation coût / latence** : le déterministe (ts_engine, < 1 s) est sur le chemin critique temps réel ; le LLM (Stratège / Coach) est réservé aux changements matériels et au chat.
- **Résilience systématique** : chaque intégration (Langfuse, Redis, Milvus, LLM, DB) a un fallback ; le système « ne tombe jamais à vide ».
- **Anti-hallucination** : le Coach n'énonce un prix / SKU que depuis une fiche `[P*]` réelle ; substituts vérifiés en stock ; hiérarchie de sources stricte.
- **Parallélisme maîtrisé dans LangGraph** : whitelists de champs + reducers pour éviter les conflits d'écriture entre branches parallèles.
- **Schéma DB = source de vérité** : migrations Alembic (0001 → 0013), aucun DDL runtime ; l'app vérifie mais ne crée pas.

---

## Annexe — Cartographie des modules

```
app/
├── main.py                     # Serveur FastAPI unifié : startup, WS, triggers, _broadcast, _build_payload
├── api/                        # Routers REST : auth, cycle, forecast, stores, hitl, kpis, monitoring, supervisor, feedback, product_requests
├── core/                       # config, db (pool asyncpg), langfuse, schema_check, agent_logger, feedback_service
├── sales/
│   ├── core/                   # retail_state, state, alert_bus, config, models
│   ├── orchestration/          # graph (CycleOrchestrator), supervisor_agent, trigger (cron), sale_trigger, alert_trigger
│   ├── coaching/agents/
│   │   ├── analyst/            # agent + ts_engine (moteur TS déterministe)
│   │   ├── stratege/           # agent (Reflexion) + events_scraper + catalog
│   │   ├── coach/              # coach_chat + cross_domain_tools + agent_outputs
│   │   └── guardrail/          # guardrail_agent
│   ├── data/                   # postgres_provider, realtime_simulator, rag/ (Milvus + fallback), seeds
│   ├── forecasting/            # modèle global entraîné (rang 0 cascade)
│   └── mcp/timefm/             # TimesFM tools
└── inventory/
    ├── agents/                 # analysis, context, decision (chaîne 3 agents)
    ├── services/               # orchestrator (batch), mcp_server, po_auto_confirm, po_ws_bus, redis_alert_bus
    ├── api/                    # routes (analyze_store), supply_routes (Kanban PO)
    ├── forecasting/            # demand sensing (baseline MSTL + XGBoost), timesfm
    ├── repositories/           # inventory_repo, supply_repo
    └── tools/internal/         # stock_tools (data layer)

db/migrations/                  # Alembic 0001 → 0013 (source de vérité du schéma)
```
