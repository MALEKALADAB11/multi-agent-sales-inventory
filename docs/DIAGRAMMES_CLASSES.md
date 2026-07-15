# Diagrammes de Classes — Moteur Agentique Retail (Sales + Inventory)

> Diagrammes UML de classes conçus à partir du code réel du package `app/`.
> Format **Mermaid** : rendu natif dans GitHub/GitLab/VS Code, exportable en
> PNG/SVG haute résolution via <https://mermaid.live> pour insertion dans le rapport.

Convention : les `TypedDict` LangGraph sont stéréotypés `<<State>>`, les modèles
Pydantic `<<Entity>>`, les dataclasses `<<DTO>>`, les énumérations `<<enumeration>>`.

---

## 1. Vue d'ensemble — Diagramme de packages

Positionne les grands sous-systèmes avant le détail des classes.

```mermaid
classDiagram
    direction TB

    class API_Layer {
        <<package>>
        FastAPI app.main
        /api/v1/chat · /supervisor · /hitl
        /api/v1/inventory · /supply (Kanban PO)
        WebSocket : coach, guardrail, inventory, PO
    }

    class Sales_Module {
        <<package>>
        AnalystAgent (TS Engine)
        StrategistAgent (RAG + Reflexion)
        CoachAgent (fusion cross-domaine)
        GuardrailAgent (G1..G7)
        CycleOrchestrator / SupervisorGraph
    }

    class Inventory_Module {
        <<package>>
        InventoryAnalysisAgent
        InventoryContextAgent
        InventoryDecisionAgent
        InventoryOrchestrator
        Repositories (stock, PO)
    }

    class Core_Transverse {
        <<package>>
        Config · AgentLogger
        LangfuseTracer (observabilité)
        CircuitBreaker · exceptions
    }

    class Data_Infra {
        <<package>>
        PostgreSQL (PostgresProvider, repos)
        Redis (AlertBus, StateBus, cache)
        Milvus (RAG Retriever)
        TimesFM / Holt-Winters (forecast)
    }

    API_Layer --> Sales_Module : expose
    API_Layer --> Inventory_Module : expose
    Sales_Module --> Core_Transverse : utilise
    Inventory_Module --> Core_Transverse : utilise
    Sales_Module --> Data_Infra : lit/écrit
    Inventory_Module --> Data_Infra : lit/écrit
    Sales_Module <--> Inventory_Module : RetailState partagé + AlertBus
```

---

## 2. Modèle de domaine & états partagés

Le cœur du système : l'état unifié `RetailState` circule entre toutes les
branches du graphe LangGraph ; les entités Pydantic modélisent le domaine métier.

```mermaid
classDiagram
    direction LR

    class RetailState {
        <<State>>
        +cycle_id : str
        +store_id : str
        +advisor_id : str
        +trigger_type : Literal
        +pos_data : Dict
        +stock_data : Dict
        +context_data : Dict
        +user_message : str
        .. Branche Sales — Analyste ..
        +gap_pct : float
        +gap_amount : float
        +forecast_eod : float
        +attainment : float
        +urgency_level : str
        +urgency_score : float
        +analyst_summary : str
        +intraday_trend : str
        .. Branche Stratégie ..
        +strategie : str
        +strategie_actions : List~Dict~
        +focus_produits : List~str~
        +cause_racine : str
        +rag_used : bool
        +critique_score : float
        .. Branche Inventory ..
        +inventory_decisions : List~Dict~
        +critical_stock_alerts : List~Dict~
        +stock_report : Dict
        .. Coach / Guardrail / HITL ..
        +coach_message : str
        +scored_products : List~Dict~
        +guardrail_status : str
        +guardrail_issues : List~Dict~
        +human_validation : Dict
    }

    class SalesAgentState {
        <<State>>
        cycle sales legacy
    }

    class Store {
        <<Entity>>
        +store_id : str
        +name : str
        +region : str
        +daily_target : float
    }

    class StoreMetrics {
        <<Entity>>
        +ca_actuel : float
        +objectif_jour : float
        +gap_pct : float
        +transactions : int
        +panier_moyen : float
    }

    class Advisor {
        <<Entity>>
        +advisor_id : str
        +name : str
        +store_id : str
        +profile : str
    }

    class AdvisorPerformance {
        <<Entity>>
        +ventes_jour : float
        +taux_conversion : float
        +rank_magasin : int
    }

    class CoachingCard {
        <<Entity>>
        +card_id : str
        +advisor_id : str
        +message : str
        +urgency : UrgencyLevel
        +status : CardStatus
        +actions : List~str~
    }

    class ForecastEOD {
        <<Entity>>
        +store_id : str
        +forecast_ca : float
        +ci_low : float
        +ci_high : float
        +method : str
        +confidence : float
    }

    class InventoryItem {
        <<Entity>>
        +sku : str
        +store_id : str
        +stock_actuel : int
        +seuil_alerte : int
        +days_of_stock : float
    }

    class InventoryAlert {
        <<Entity>>
        +alert_id : str
        +sku : str
        +severity : str
        +status : str
    }

    class InventoryReco {
        <<Entity>>
        +recommendation_id : str
        +sku : str
        +action : str
        +quantite : int
        +justification : str
    }

    class UrgencyLevel {
        <<enumeration>>
        LOW
        MEDIUM
        HIGH
        CRITICAL
    }

    class CardStatus {
        <<enumeration>>
        PENDING
        DELIVERED
        ACKNOWLEDGED
    }

    RetailState ..> SalesAgentState : remplace (v2)
    Store "1" --> "*" Advisor : emploie
    Store "1" --> "1" StoreMetrics : mesuré par
    Advisor "1" --> "1" AdvisorPerformance : évalué par
    Advisor "1" --> "*" CoachingCard : reçoit
    CoachingCard --> UrgencyLevel
    CoachingCard --> CardStatus
    Store "1" --> "*" ForecastEOD : prévu par
    Store "1" --> "*" InventoryItem : stocke
    InventoryItem "1" --> "*" InventoryAlert : déclenche
    InventoryAlert "1" --> "0..1" InventoryReco : génère
```

---

## 3. Module Sales — Agents de coaching & orchestration

Le `SupervisorGraph` (LangGraph) exécute 4 branches en parallèle puis fusionne
dans le CoachAgent, validé par le GuardrailAgent avant notification.

```mermaid
classDiagram
    direction TB

    class SupervisorGraph {
        <<LangGraph StateGraph>>
        +node_initialize_state(state) Dict
        +node_sales_branch(state) Dict
        +node_knowledge_branch(state) Dict
        +node_context_branch(state) Dict
        +node_inventory_branch(state) Dict
        +node_merge_outputs(state) Dict
        +node_coach_agent(state) Dict
        +node_guardrail(state) Dict
        +route_after_guardrail(state) str
        +node_human_validation(state) Dict
        +node_notify_frontend(state) Dict
        +node_safe_fallback(state) Dict
        +node_save_memory(state) Dict
    }

    class CycleOrchestrator {
        -json_svc : JsonDataService
        -timefm : TimesFMTools
        -graph : CompiledGraph
        +run_cycle(store_id, trigger, user_message) Dict
    }

    class TSEngine {
        <<module analyste v4>>
        +forecast_daily_series(values, horizon) dict
        +analyze_store(store_id, current_hour) dict
        -_hw_gridsearch(y, period) dict
        -_backtest_mape(y, period, params) float
        -_fetch_hourly_profile(conn, sid, ...) 
        -_classify_hour(deviation, z) str
        -_trend_signal(ledger) str
    }

    class AnalysteAgent {
        -llm : BaseChatModel
        +analyser(state) Dict
    }

    class CoachStrategeOrchestrator {
        -cache : StrategyCache
        -timeout_s : float
        +invoke(state) StrategieOutput
        -_build_fallback(state) StrategieOutput
        -_fetch_fallback_from_db(store_id) StrategieOutput
        -_generic_actions(gap_pct, urgency) List~Dict~
        +get_stats() Dict
    }

    class StrategyCache {
        -ttl_seconds : int = 1800
        -max_size : int = 50
        +get(store_id, gap_pct) StrategieOutput
        +get_stale(store_id, gap_pct) StrategieOutput
        +set(store_id, gap_pct, output) void
        +stats() Dict
    }

    class StrategieOutput {
        <<DTO>>
        +strategie : str
        +actions : List~Dict~
        +focus_produits : List~str~
        +cause_racine : str
        +source : str
        +rag_used : bool
    }

    class GuardrailAgent {
        <<module de validation>>
        +evaluate_guardrails(state) Dict
        +guardrail_node(state) Dict
        +route_guardrail(state) str
        -_g1_stock_available() str
        -_g2_stockout_imminent() str
        -_g3_rag_source() str
        -_g4_business_rules() str
        -_g5_network_eligibility() str
        -_g6_confidence(confidence) str
        -_g7_budget() str
        -_compute_status(issues) str
    }

    class RAGRetriever {
        <<module rag/>>
        +retrieve(query, domains, top_k) RetrievalResult
        +retrieve_for_cycle(state) RetrievalResult
        +format_context_block(result) str
        +citation_index(result) dict
    }

    class RetrievalResult {
        <<DTO>>
        +documents : List~RetrievedDocument~
        +top_score() float
        +by_domain(domain) List~RetrievedDocument~
    }

    class RetrievedDocument {
        <<DTO>>
        +doc : Document
        +score : float
        +domain : str
    }

    class BalancingEngine {
        +score_products(ctx) List~ProductCandidate~
    }

    class ProductCandidate {
        <<DTO>>
        +sku : str
        +score : float
        +stock_ok : bool
        +marge : float
    }

    CycleOrchestrator --> SupervisorGraph : compile & invoque
    SupervisorGraph ..> RetailState : lit / écrit (reducers)
    SupervisorGraph --> AnalysteAgent : branche sales
    AnalysteAgent --> TSEngine : forecast Holt-Winters + gap horaire
    SupervisorGraph --> CoachStrategeOrchestrator : branche stratégie
    CoachStrategeOrchestrator --> StrategyCache : cache TTL 30 min
    CoachStrategeOrchestrator ..> StrategieOutput : produit
    SupervisorGraph --> GuardrailAgent : validation G1..G7
    SupervisorGraph --> RAGRetriever : branche knowledge
    RAGRetriever ..> RetrievalResult : retourne
    RetrievalResult o-- RetrievedDocument
    SupervisorGraph --> BalancingEngine : scored_products
    BalancingEngine ..> ProductCandidate : score
```

---

## 4. Module Inventory — Pipeline multi-agents & approvisionnement (Kanban PO)

Pipeline séquentiel Analysis → Context → Decision par SKU, avec persistance
des recommandations et suggestion automatique de bons de commande (HITL).

```mermaid
classDiagram
    direction TB

    class InventoryOrchestrator {
        -analysis_agent : InventoryAnalysisAgent
        -context_agent : InventoryContextAgent
        -decision_agent : InventoryDecisionAgent
        -use_llm : bool
        +analyze_sku(sku, store_id) Dict
        +analyze_batch(skus, store_id, parallel) Dict
        -_run_pipeline(sku, store_id) Dict
        +get_risk_level(result) str
        +get_decision_action(result) str
        +get_days_of_stock(result) float
    }

    class InventoryAnalysisAgent {
        -llm : BaseChatModel
        -graph : CompiledGraph$
        +run(sku, store_id) AgentState
        -_persist_result(state) void
    }

    class AgentState {
        <<State>>
        +sku : str
        +store_id : str
        +stock_data : Dict
        +velocity : float
        +days_of_stock : float
        +risk_level : str
        +analysis_summary : str
    }

    class InventoryContextAgent {
        -llm : BaseChatModel
        -graph : CompiledGraph$
        +run(sku, store_id, analysis) ContextAgentState
        -_persist_result(state) void
    }

    class ContextAgentState {
        <<State>>
        +sku : str
        +trends : Dict
        +events : List~Dict~
        +seasonality_factor : float
        +demand_adjustment : float
        +context_summary : str
    }

    class InventoryDecisionAgent {
        -llm : BaseChatModel
        -graph : CompiledGraph$
        +run(sku, store_id, analysis, context) DecisionAgentState
        -_compute_adjusted_metrics(state) Dict
        -_persist_recommendation(state) str
        -_suggest_purchase_order(recommendation_id) void
    }

    class DecisionAgentState {
        <<State>>
        +sku : str
        +action : str
        +quantite : int
        +urgence : str
        +justification : str
        +recommendation_id : str
    }

    class SyncInventoryRepo {
        <<Repository>>
        +get_product(sku) dict
        +get_stock_level(sku, store_id) dict
        +get_stock_levels_batch(skus, store_id) dict
        +get_active_objective() dict
        +upsert_alert_and_return_id(...) str
        +upsert_alerts_batch(alerts) int
        +resolve_stale_alerts(store_id, skus) int
        +save_recommendation(...) str
        +update_recommendation_status(id, status) bool
        +save_context_adjustment(...) void
        +start_agent_run(agent, store_id) str
        +complete_agent_run(run_id, ...) void
    }

    class SyncPurchaseOrderRepo {
        <<Repository>>
        +list_purchase_orders(store_id, statut) list
        +get_purchase_order_by_id(po_id) dict
        +create_from_recommendation(reco_id, ...) dict
        +create_suggestion_from_recommendation(reco_id) dict
        +approve_suggestion(po_id, decided_by) dict
        +reject_suggestion(po_id, decided_by) dict
        +update_status(po_id, new_statut, qte_recue) dict
        +auto_confirm_stale_soumis(max_age_hours) list
        -_select_supplier(cur, sku) dict
        -_enrich_kanban_fields(po) dict
    }

    class PurchaseOrderTransitionError {
        <<Exception>>
        +current : str
        +requested : str
    }

    class TimesFMForecaster {
        -context_length : int = 512
        -horizon : int = 30
        +prepare_data(df) ndarray
        +forecast(series) Dict
        +forecast_multiple(series_map) Dict
    }

    class RedisAlertBus {
        <<Service>>
        +publish(alert) void
        +subscribe(handler) void
    }

    class StockSimulator {
        +run(store_id) void
        simulation temps réel des ventes/stocks
    }

    class InventoryMCPClient {
        +call_tool(name, args) Dict
        get_stock_status · suggest_purchase_order
        move_purchase_order · compute_metrics
    }

    InventoryOrchestrator *-- InventoryAnalysisAgent
    InventoryOrchestrator *-- InventoryContextAgent
    InventoryOrchestrator *-- InventoryDecisionAgent
    InventoryAnalysisAgent ..> AgentState : produit
    InventoryContextAgent ..> ContextAgentState : produit
    InventoryDecisionAgent ..> DecisionAgentState : produit
    InventoryAnalysisAgent --> SyncInventoryRepo : persiste
    InventoryContextAgent --> SyncInventoryRepo : persiste
    InventoryDecisionAgent --> SyncInventoryRepo : save_recommendation
    InventoryDecisionAgent --> SyncPurchaseOrderRepo : suggère PO (statut SUGGERE)
    SyncPurchaseOrderRepo ..> PurchaseOrderTransitionError : lève
    InventoryAnalysisAgent --> TimesFMForecaster : prévision demande
    InventoryOrchestrator --> RedisAlertBus : publie alertes
    StockSimulator --> RedisAlertBus : événements stock
    InventoryMCPClient ..> SyncPurchaseOrderRepo : via serveur MCP
```

---

## 5. Communication inter-modules & infrastructure transverse

Bus Redis (alertes + état unifié), observabilité Langfuse, résilience
CircuitBreaker et accès données PostgreSQL.

```mermaid
classDiagram
    direction TB

    class AlertBus {
        -redis_url : str
        -_client : Redis
        +evaluate_priority(alert) str$
        +publish_stock_alert(store_id, sku, ...) str
        +publish_sales_alert(store_id, ...) str
        +publish_cross_alert(store_id, ...) str
        +publish_cycle_event(store_id, ...) str
    }

    class AsyncAlertListener {
        -store_id : str
        -_running : bool
        +listen(handler) async
        +stop() void
    }

    class AlertType {
        <<enumeration>>
        STOCK_CRITICAL
        SALES_GAP
        CROSS_DOMAIN
        CYCLE_EVENT
    }

    class AlertSeverity {
        <<enumeration>>
        INFO
        WARNING
        CRITICAL
    }

    class StateBus {
        -redis_url : str
        +publish_cycle_state(store_id, state) str
        +publish_inventory_snapshot(store_id, snap) str
        +read_inventory_snapshot(store_id) InventorySnapshot
        +publish_event(store_id, event) str
        +request_human_approval(store_id, payload) HumanGateResult
        +publish_feedback(store_id, feedback) str
        +read_recent_feedback(store_id, n) List
        +health_check() Dict
    }

    class UnifiedCycleState {
        <<DTO>>
        +cycle_id : str
        +store_id : str
        +sales_summary : Dict
        +inventory_snapshot : InventorySnapshot
        +critique : CritiqueResult
        +human_gate : HumanGateResult
        +to_dict() Dict
        +from_dict(data) UnifiedCycleState$
    }

    class InventorySnapshot {
        <<DTO>>
        +store_id : str
        +critical_skus : List
        +alerts_count : int
        +timestamp : str
    }

    class HumanGateResult {
        <<DTO>>
        +approved : bool
        +decided_by : str
        +comment : str
    }

    class LangfuseTracer {
        <<Singleton>>
        +cycle(cycle_id, store_id) _CycleTrace
        +agent(name) _AgentTrace
        +node(name) _NodeTrace
        +llm(model) _LLMTrace
        +rag(query) _RAGTrace
        +flush() void
    }

    class CircuitBreaker {
        -config : CircuitBreakerConfig
        -stats : CircuitBreakerStats
        -state : CircuitState
        +call(func, *args) Any
        +record_success() void
        +record_failure() void
    }

    class CircuitState {
        <<enumeration>>
        CLOSED
        OPEN
        HALF_OPEN
    }

    class CircuitBreakerConfig {
        <<DTO>>
        +failure_threshold : int
        +recovery_timeout : float
        +success_threshold : int
    }

    class AgentLogger {
        +log_cycle(cycle_id, ...) void
        +log_node_start(node) int
        +log_node_complete(node, ms) void
        +log_node_error(node, err) void
    }

    class Config {
        <<Singleton>>
        +DATABASE_URL : str
        +REDIS_URL : str
        +LLM_PROVIDER : str
        +LANGFUSE_ENABLED : bool
        validation fail-fast au démarrage
    }

    class PostgresProvider {
        <<Repository>>
        +get_store_metrics(store_id) Dict
        +get_hourly_sales(store_id, date) List
        +get_advisor_performance(advisor_id) Dict
        +get_kpis(store_id) Dict
        pool asyncpg partagé
    }

    class JsonDataService {
        <<Fallback Provider>>
        +get_store(store_id) Dict
        +get_context(store_id) Dict
        fallback si PG indisponible
    }

    AlertBus --> AlertType
    AlertBus --> AlertSeverity
    AsyncAlertListener --> AlertBus : consomme streams
    StateBus ..> UnifiedCycleState : sérialise
    UnifiedCycleState *-- InventorySnapshot
    UnifiedCycleState *-- HumanGateResult
    CircuitBreaker --> CircuitState
    CircuitBreaker *-- CircuitBreakerConfig
    LangfuseTracer <.. AgentLogger : complète
    PostgresProvider <.. JsonDataService : fallback de
    Config <.. AlertBus : REDIS_URL
    Config <.. PostgresProvider : DATABASE_URL
    Config <.. LangfuseTracer : clés Langfuse
```

---

## 6. Lecture recommandée pour le rapport

| # | Diagramme | Chapitre suggéré |
|---|-----------|------------------|
| 1 | Packages | Architecture générale (vue macroscopique) |
| 2 | Domaine & états | Conception — modèle de données / état partagé |
| 3 | Module Sales | Conception détaillée — coaching agentique |
| 4 | Module Inventory | Conception détaillée — optimisation stocks & Kanban PO |
| 5 | Infrastructure | Conception — communication, résilience, observabilité |

**Export haute qualité** : coller chaque bloc dans <https://mermaid.live> →
*Actions → PNG (scale 3)* ou SVG, puis insérer dans le rapport.
