# Architecture Complète — Moteur Agentique Retail Ooredoo Tunisie

> **Système multi-agents temps réel** : coaching de vente + optimisation des stocks.
> Stack : FastAPI · LangGraph · PostgreSQL · Redis · Milvus · Angular 21.
> Document destiné au rapport — chaque diagramme est exportable en image (Mermaid).

---

## 1. Vue d'ensemble globale (macro-architecture)

```mermaid
flowchart TB
    subgraph FRONT["🖥️ FRONTEND — Angular 21 (Signals)"]
        DASH["Dashboard Manager<br/>(KPIs, attainment, forecast)"]
        CHAT["Chat Coach Conseiller<br/>(SSE streaming)"]
        KANBAN["Kanban Achats (PO)<br/>(suggestions auto)"]
        HITLP["Panneau HITL<br/>(validation humaine)"]
        GPANEL["Panneau Guardrail<br/>(monitoring)"]
    end

    subgraph API["⚙️ BACKEND — FastAPI (app/main.py)"]
        REST["REST API<br/>/api/v1/* — metrics, forecast,<br/>advisors, live-analysis, KPIs"]
        WS["WebSockets<br/>/ws/store/{id} · /ws/advisor/{id}<br/>(broadcast temps réel)"]
        SSE["SSE /chat/stream<br/>(coach conversationnel)"]
        SUPAPI["/api/v1/supervisor<br/>(déclenchement cycle)"]
        HITLAPI["/api/hitl<br/>(file de revues humaines)"]
        MCP["Serveur MCP<br/>(7 outils inventory + Kanban PO)"]
        AUTH["Auth JWT + RBAC store-level<br/>+ rate limiting (slowapi)"]
    end

    subgraph ORCH["🧠 ORCHESTRATION — SupervisorAgent (LangGraph)"]
        SUP["Graphe maître<br/>fan-out 4 branches parallèles<br/>→ fusion → coach → guardrail"]
        BUS["AlertBus<br/>(cycles événementiels)"]
    end

    subgraph AGENTS["🤖 COUCHE AGENTS"]
        direction LR
        AN["Analyste v4<br/>(ReAct + TS Engine)"]
        ST["Stratège<br/>(Reflexion + RAG)"]
        CO["Coach Cross-Domain<br/>(scoring 6 critères)"]
        GU["Guardrail<br/>(7 règles G1–G7)"]
        IA["Inventory Analysis"]
        IC["Inventory Context"]
        ID["Inventory Decision<br/>(EOQ, reorder point)"]
    end

    subgraph DATA["💾 COUCHE DONNÉES"]
        PG[("PostgreSQL<br/>ooredoo_sales — 1,49M lignes<br/>ventes journalières 4,5 ans<br/>Alembic 0001–0008")]
        RD[("Redis<br/>cache contexte + pub/sub")]
        MV[("Milvus<br/>RAG — 200+ scripts de vente")]
        TS["Moteur Time Series<br/>Holt-Winters saisonnier<br/>+ WAPE backtest<br/>(TimesFM/Chronos optionnels)"]
    end

    subgraph EXT["🌐 SIGNAUX EXTERNES"]
        MET["Météo"]
        EVT["Événements & festivals<br/>(scraper + seed datés)"]
        PROMO["Offres / promotions<br/>Ooredoo"]
    end

    FRONT <-->|"REST + JWT"| REST
    WS -->|"push temps réel"| DASH
    SSE -->|"tokens streaming"| CHAT
    KANBAN <--> MCP
    HITLP <--> HITLAPI

    REST --> SUP
    SUPAPI --> SUP
    BUS -->|"alerte → déclenche cycle"| SUP
    SUP --> AGENTS
    AGENTS --> DATA
    IC --> EXT
    ST --> EXT
    SUP -->|"recommandation validée"| WS

    classDef front fill:#e8f0fe,stroke:#4285f4,color:#1a3c6e
    classDef api fill:#fef7e0,stroke:#f9ab00,color:#7a5800
    classDef orch fill:#fce8e6,stroke:#ea4335,color:#8a1c12
    classDef agent fill:#e6f4ea,stroke:#34a853,color:#1e5631
    classDef data fill:#f3e8fd,stroke:#a142f4,color:#5b2a91
    class DASH,CHAT,KANBAN,HITLP,GPANEL front
    class REST,WS,SSE,SUPAPI,HITLAPI,MCP,AUTH api
    class SUP,BUS orch
    class AN,ST,CO,GU,IA,IC,ID agent
    class PG,RD,MV,TS data
```

---

## 2. Graphe de l'orchestrateur — SupervisorAgent (LangGraph)

Topologie exacte de `app/sales/orchestration/supervisor_agent.py` : fan-out parallèle sur 4 branches, fan-in, puis pipeline séquentiel avec routage conditionnel du Guardrail.

```mermaid
flowchart TB
    START(["START — trigger :<br/>cycle planifié · AlertBus · /api/v1/supervisor"])
    INIT["initialize_state<br/><i>génère cycle_id, bootstrap RetailState</i>"]

    subgraph PAR["⚡ Exécution PARALLÈLE (4 branches)"]
        SB["🔵 sales_branch<br/>Analyste (ReAct) → Stratège (Reflexion)<br/><i>via CycleOrchestrator</i>"]
        KB["🟣 knowledge_branch<br/>RAG Milvus — scripts de vente<br/><i>node_rag_search</i>"]
        CB["🟠 context_branch<br/>Sentinel : météo, événements,<br/>promos, QoS<br/><i>fetch_full_context</i>"]
        IB["🟢 inventory_branch<br/>InventoryDecisionAgent (fast)<br/>EOQ · riskLevel · orderTiming"]
    end

    MERGE["merge_outputs<br/><i>fan-in → RetailState unifié</i>"]
    COACH["coach_agent (Cross-Domain)<br/>fusion Sales + Inventory + RAG + Contexte<br/>scoring pondéré 6 critères → top 3 produits"]
    GUARD{"guardrail_agent<br/>7 règles G1–G7<br/>(déterministe)"}

    HV["human_validation (HITL)<br/><i>submit_hitl_review →<br/>panneau Angular</i>"]
    SF["safe_fallback<br/><i>message neutre sécurisé</i>"]
    NOTIF["notify_frontend<br/><i>broadcast WebSocket<br/>coach_recommendation</i>"]
    MEM["save_memory<br/><i>PostgreSQL coach_interactions<br/>(mémoire pour RAG futur)</i>"]
    FIN(["END"])

    START --> INIT
    INIT --> SB & KB & CB & IB
    SB & KB & CB & IB --> MERGE
    MERGE --> COACH
    COACH --> GUARD

    GUARD -->|"✅ APPROVE"| NOTIF
    GUARD -->|"🔁 REWRITE<br/>(max 1 itération)"| COACH
    GUARD -->|"⚠️ ESCALATE"| HV
    GUARD -->|"⛔ BLOCK"| SF

    HV --> NOTIF
    SF --> NOTIF
    NOTIF --> MEM
    MEM --> FIN

    classDef par fill:#e6f4ea,stroke:#34a853
    classDef guard fill:#fce8e6,stroke:#ea4335
    classDef seq fill:#e8f0fe,stroke:#4285f4
    class SB,KB,CB,IB par
    class GUARD guard
    class INIT,MERGE,COACH,NOTIF,MEM,HV,SF seq
```

**Contrat d'état** : chaque nœud retourne uniquement son *delta* sur le `RetailState` (TypedDict LangGraph avec *reducers* — `operator.add` pour `agents_invoked`/`errors`, merge pour `metrics`), ce qui rend le fan-out parallèle sûr (pas d'`InvalidUpdateError`).

---

## 3. Fiche détaillée de chaque agent — entrées / sorties / LLM

| Agent | Pattern | LLM utilisé | Entrées (inputs) | Sorties (outputs) |
|---|---|---|---|---|
| **Analyste v4** | ReAct + moteur TS déterministe (LLM **hors chemin critique**) | Ollama local (fallback narration uniquement) | Ventes POS temps réel (PostgreSQL), objectif journalier, historique horaire | `gap_pct`, `forecast_eod` (Holt-Winters), `hourly_gaps`, `next_hours_forecast` (h+1..h+3), `urgency_level`, `urgency_score`, `trend_signal`, `analyst_summary`, anomalies |
| **Stratège** | Reflexion (génération → auto-critique → révision) + RAG | OpenRouter (rôle *smart*) → fallback Ollama local | Sortie Analyste, contexte externe (météo/événements/offres), stock critique (`vw_stock_enriched`), scripts RAG | `strategie`, `strategie_actions`, `focus_produits`, `cause_racine`, `message_manager`, `critique_score`, `critique_passed`, `feasibility` |
| **Knowledge (RAG)** | Retrieval sémantique | — (embeddings uniquement) | Requête construite depuis le gap/contexte | `rag_context` (docs + query), `retrieved_scripts` (top-k scripts Milvus, fallback corpus local) |
| **Context Sentinel** | Collecte de signaux | — (déterministe) | APIs météo, scraper événements/festivals, offres Ooredoo, QoS réseau | `external_context`, `context_report`, `context_heatmap`, `context_signals` (événements en cours / à venir) |
| **Inventory Analysis** | Analyse par SKU | Rôle *fast* (Gemini Flash via OpenRouter) hors mode `fast=True` | Stock par SKU, ventes, lead times fournisseurs (`supplier_products`) | Métriques stock : couverture, vélocité, rotation |
| **Inventory Decision** | Règles + formules (EOQ, point de commande) | Rôle *smart* pour la narration (désactivé en mode `fast`) | Métriques Inventory Analysis, objectif métier (`avoid_stockout`) | `inventory_decisions`, `critical_stock_alerts`, `riskLevel`, `orderTiming`, `formulaOrderQty`, suggestions de PO (Kanban, statut `SUGGERE`) |
| **Coach Cross-Domain** | Fusion multi-domaines, scoring pondéré 6 critères | — (scoring déterministe `rank_products`) | Contexte ventes, produits recommandables, historique conseiller, alertes stock | `coach_recommendation` (produit à pousser / à éviter, message conseiller, justification, confiance), `scored_products` (top 3) |
| **Coach Chat** (conversationnel) | Chat + Stratège serveur-side + RAG unifié | **Mistral primaire** (large→small→nemo) → Groq (rotation clés × modèles) → OpenRouter → Ollama (**retry 4 niveaux**) | Question du conseiller, RetailState du dernier cycle, scripts RAG | Réponse streamée en SSE token par token |
| **Guardrail** | 100 % déterministe — 7 règles G1–G7 (aucun appel LLM) | — | `coach_recommendation`, `strategie_actions`, alertes stock | `guardrail_status` (APPROVE / REWRITE / ESCALATE / BLOCK), `guardrail_issues`, `guardrail_confidence`, `requires_human_validation`, `guardrail_safe_fallback` |
| **Supervisor** | Orchestrateur LangGraph (StateGraph) | — | Trigger (cron, AlertBus, API), `store_id`, `advisor_id` | RetailState final, broadcast WS, persistance `coach_interactions` |

---

## 4. Stratégie LLM — sélection tiérée & chaîne de résilience

```mermaid
flowchart LR
    subgraph FACTORY["LLM Factory (app/inventory/utils/llm_factory.py)"]
        direction TB
        ROLE{"Rôle demandé"}
        ROLE -->|"fast<br/>analyse, contexte, signaux"| F["Gemini Flash 1.5<br/>(OpenRouter)"]
        ROLE -->|"smart<br/>décision, coach, synthèse"| S["Claude 3.5 Sonnet /<br/>Mistral Large<br/>(OpenRouter / Mistral API)"]
        ROLE -->|"guardian<br/>critique, validation"| G["Llama 3.1 70B<br/>(OpenRouter)"]
    end

    subgraph CHAIN["Chaîne de fallback du Coach Chat (retry 4 niveaux)"]
        direction LR
        M1["1️⃣ Mistral API directe<br/>large → small → nemo"] -->|"429 / erreur"| M2["2️⃣ Groq<br/>rotation clés × modèles<br/>gpt-oss-120b → llama-3.3-70b"]
        M2 -->|"429 / erreur"| M3["3️⃣ OpenRouter<br/>full puis stripped"]
        M3 -->|"429 / erreur"| M4["4️⃣ Ollama local<br/>llama3.2 (dernier recours,<br/>jamais indisponible)"]
    end
```

**Principe clé** : les calculs critiques (prévision, gap, EOQ, guardrail, scoring) sont **déterministes** — le LLM n'intervient que pour la génération de langage (stratégie, coaching, narration), avec un fallback local garantissant la disponibilité même sans aucune API externe.

---

## 5. Flux de données de bout en bout (séquence d'un cycle)

```mermaid
sequenceDiagram
    autonumber
    participant TRG as Trigger<br/>(cron / AlertBus / API)
    participant SUP as SupervisorAgent<br/>(LangGraph)
    participant AN as Analyste v4
    participant ST as Stratège
    participant INV as Agents Inventory
    participant RAG as Milvus (RAG)
    participant CTX as Context Sentinel
    participant CO as Coach Cross-Domain
    participant GU as Guardrail
    participant HITL as Manager (HITL)
    participant FE as Frontend Angular
    participant PG as PostgreSQL

    TRG->>SUP: déclenche cycle (store_id)
    SUP->>SUP: initialize_state (cycle_id)

    par Branches parallèles
        SUP->>AN: ventes POS temps réel
        AN->>PG: SELECT ventes horaires
        AN-->>SUP: gap, forecast EOD, urgence
        AN->>ST: signaux analytiques
        ST-->>SUP: stratégie + actions + critique
    and
        SUP->>RAG: recherche scripts de vente
        RAG-->>SUP: top-k scripts pertinents
    and
        SUP->>CTX: météo, événements, offres
        CTX-->>SUP: contexte externe enrichi
    and
        SUP->>INV: analyse stock par SKU
        INV-->>SUP: décisions + alertes critiques
    end

    SUP->>CO: RetailState fusionné
    CO-->>SUP: recommandation + top 3 produits scorés
    SUP->>GU: validation 7 règles

    alt APPROVE
        GU-->>SUP: ✅ approuvé
    else REWRITE
        GU-->>CO: 🔁 réécriture (1 itération max)
    else ESCALATE
        GU-->>HITL: ⚠️ revue humaine requise
        HITL-->>SUP: décision du manager
    else BLOCK
        GU-->>SUP: ⛔ message fallback sécurisé
    end

    SUP->>FE: broadcast WebSocket (recommandation + badge guardrail)
    SUP->>PG: save_memory → coach_interactions
    FE->>FE: dashboard + chat + kanban mis à jour en temps réel
```

---

## 6. Entrées / Sorties du système (contrats d'interface)

### Entrées (Inputs)
| Source | Canal | Contenu |
|---|---|---|
| Point de vente (POS) | PostgreSQL `ooredoo_sales` | Ventes journalières/horaires — 1,49M lignes, 4,5 ans d'historique |
| Conseiller de vente | SSE `/chat/stream`, REST `/chat` | Questions en langage naturel |
| Manager | REST `/api/hitl`, Kanban | Validations HITL, approbation des PO suggérés |
| Signaux externes | Scrapers + seeds | Météo, festivals/concerts datés, offres Ooredoo |
| AlertBus | Interne (événementiel) | Alertes stock/ventes → déclenchement de cycles |
| Feedback humain | Table feedback (migration 0008) | Boucle d'apprentissage sur les recommandations |

### Sorties (Outputs)
| Destination | Canal | Contenu |
|---|---|---|
| Dashboard manager | WS `/ws/store/{id}` | KPIs, attainment, forecast EOD, hourly performance, alertes |
| Conseiller | WS `/ws/advisor/{id}` + SSE | Recommandation coach (produit à pousser/éviter), réponses chat |
| Kanban achats | Outils MCP (`suggest/move/list_purchase_order`) | PO auto-suggérés (SUGGERE → … → RECU, porte HITL préservée) |
| Panneau HITL | REST + WS | Revues escaladées par le Guardrail |
| Mémoire système | PostgreSQL `coach_interactions` | Historique des cycles pour le RAG futur |
| Observabilité | Langfuse + monitoring router | Traces LLM, métriques par nœud (`*_branch_ms`) |

---

## 7. Vue déploiement / composants techniques

```mermaid
flowchart LR
    subgraph CLIENT["Poste client"]
        NG["Angular 21<br/>Signals · 7 pages<br/>3 feeds WebSocket"]
    end

    subgraph SRV["Serveur applicatif"]
        FA["FastAPI (uvicorn)<br/>app/main.py<br/>JWT · RBAC · slowapi"]
        LG["LangGraph<br/>SupervisorAgent<br/>+ CycleOrchestrator"]
        MCPS["Serveur MCP maison<br/>7 outils inventory/Kanban"]
        LF["Langfuse<br/>(traçabilité LLM)"]
    end

    subgraph STORES["Stockage"]
        PG2[("PostgreSQL 16<br/>50 tables · Alembic 0001–0008")]
        RD2[("Redis<br/>cache + TTL")]
        MV2[("Milvus<br/>vecteurs RAG")]
    end

    subgraph LLMS["Fournisseurs LLM"]
        MIS["Mistral API"]
        GRQ["Groq"]
        ORT["OpenRouter<br/>(Claude/Gemini/Llama)"]
        OLL["Ollama (local)"]
    end

    NG <--> FA
    FA <--> LG
    FA <--> MCPS
    LG --> LF
    LG <--> PG2 & RD2 & MV2
    LG --> MIS & GRQ & ORT & OLL

    classDef store fill:#f3e8fd,stroke:#a142f4
    class PG2,RD2,MV2 store
```

---

## 8. Points d'architecture différenciants (à mettre en avant dans le rapport)

1. **Fan-out parallèle LangGraph** — les 4 branches (ventes, connaissance, contexte, inventaire) s'exécutent simultanément ; le `RetailState` avec *reducers* garantit une fusion sans conflit d'écriture.
2. **LLM hors chemin critique** — la prévision (Holt-Winters + backtest WAPE), le scoring produits et le Guardrail sont déterministes : le système reste fonctionnel et fiable même si tous les LLM sont indisponibles.
3. **Résilience LLM à 4 niveaux** — Mistral → Groq (rotation multi-clés) → OpenRouter → Ollama local : aucune dépendance à un fournisseur unique.
4. **Guardrail systématique** — chaque recommandation passe par 7 règles avec 4 issues possibles (approve / rewrite / escalate / block), dont une escalade humaine (HITL) native.
5. **Boucle fermée cross-domain** — les alertes stock influencent le coaching de vente (produit à éviter) et génèrent des suggestions de PO sur le Kanban, avec approbation humaine avant toute commande.
6. **Temps réel de bout en bout** — AlertBus événementiel → cycle agentique → broadcast WebSocket → UI Angular, sans polling.
