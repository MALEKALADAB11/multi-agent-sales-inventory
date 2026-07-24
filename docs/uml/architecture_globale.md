# Architecture globale du système

> Rendu cible : `img/architecture_globale.png` — vue 3 tiers (présentation / applicatif / données & services).
> À rendre via https://mermaid.live (Export PNG) ou `mmdc -i architecture_globale.md -o architecture_globale.png`.

```mermaid
flowchart TB
    subgraph TIER1["Tiers Présentation"]
        direction LR
        CONSEILLER(["👤 Conseiller de vente"])
        MANAGER(["👤 Manager"])
        ANGULAR["Application Web Angular<br/>(Dashboards · Chat Coach · Inventory ·<br/>Kanban commandes · Panneaux HITL)"]
        CONSEILLER --> ANGULAR
        MANAGER --> ANGULAR
    end

    subgraph TIER2["Tiers Applicatif — Backend FastAPI"]
        direction TB
        API["Couche API<br/>(REST · SSE · WebSocket)<br/>Auth JWT · RBAC · Rate limiting"]
        MOTEUR["Moteur agentique LangGraph<br/>(Superviseur + RetailState)"]
        subgraph SALES["Domaine Sales"]
            A1["Agent Analyste"]
            A2["Agent Stratège"]
            A3["Agent Coach"]
            A4["Agent Guardrail"]
        end
        subgraph INV["Domaine Inventory"]
            B1["Agent Analysis"]
            B2["Agent Context"]
            B3["Agent Decision"]
        end
        API --> MOTEUR
        MOTEUR --> SALES
        MOTEUR --> INV
    end

    subgraph TIER3["Tiers Données & Services"]
        direction LR
        PG[("PostgreSQL<br/>ventes · stocks · produits ·<br/>objectifs · commandes · prévisions")]
        REDIS[("Redis<br/>cache · bus d'alertes ·<br/>flux temps réel")]
        MILVUS[("Milvus<br/>scripts de vente<br/>(RAG vectoriel)")]
        LLM["☁️ Fournisseurs LLM<br/>Mistral → OpenRouter → Groq → Ollama<br/>(cascade avec repli)"]
    end

    ANGULAR -- "REST (consultations)" --> API
    ANGULAR -- "SSE (réponses Coach en streaming)" --> API
    ANGULAR -- "WebSocket (alertes · guardrail · Kanban)" --> API

    MOTEUR --> PG
    MOTEUR --> REDIS
    MOTEUR --> MILVUS
    MOTEUR -- "HTTPS" --> LLM

    style TIER1 fill:#fff3e0,stroke:#e65100
    style TIER2 fill:#e3f2fd,stroke:#0d47a1
    style TIER3 fill:#e8f5e9,stroke:#1b5e20
    style SALES fill:#bbdefb,stroke:#1565c0
    style INV fill:#c8e6c9,stroke:#2e7d32
```

## Légende

- **Tiers présentation** : application Angular unique, deux profils d'utilisateurs (conseiller / manager), trois canaux de communication (REST, SSE, WebSocket).
- **Tiers applicatif** : FastAPI expose les API et héberge le moteur multi-agents orchestré par LangGraph.
- **Tiers données & services** : PostgreSQL (données métier), Redis (cache + temps réel), Milvus (RAG), cascade de fournisseurs LLM avec repli automatique.
