# Diagramme de composants

> Rendu cible : `img/diagramme_composants.png` — modules logiciels et dépendances.
> À rendre via https://mermaid.live (Export PNG) ou `mmdc`.

```mermaid
flowchart TB
    subgraph FRONT["Frontend Angular"]
        direction LR
        UI1["Composant<br/>Dashboard conseiller"]
        UI2["Composant<br/>Chat Coach"]
        UI3["Composant<br/>Supervision manager"]
        UI4["Composant<br/>Inventory"]
        UI5["Composant<br/>Kanban commandes"]
        UI6["Composant<br/>Panneau HITL"]
        UI7["Composant<br/>Monitoring agents"]
        CORE["Services core<br/>(Auth · API · WS · SSE · Signals)"]
    end

    subgraph IFACES["Interfaces de communication"]
        direction LR
        REST["◯ API REST"]
        SSE["◯ Flux SSE"]
        WS["◯ WebSockets"]
    end

    subgraph BACK["Backend FastAPI"]
        direction TB
        AUTH["«composant»<br/>Authentification<br/>JWT · RBAC boutique"]
        SUP["«composant»<br/>Superviseur LangGraph<br/>routage · branches parallèles · RetailState"]

        subgraph MSALES["Module Sales"]
            SAG["Agents Sales<br/>Analyste · Stratège · Coach · Guardrail"]
            SSRV["Services Sales<br/>moteur TS · scoring · contexte marché"]
            SREPO["Repositories Sales"]
        end

        subgraph MINV["Module Inventory"]
            IAG["Agents Inventory<br/>Analysis · Context · Decision"]
            ISRV["Services Inventory<br/>couverture · EOQ · prévision demande"]
            IREPO["Repositories Inventory"]
        end

        subgraph SHARED["Composants partagés"]
            RAG["«composant» RAG<br/>Milvus + corpus de repli<br/>recherche hybride"]
            LLMF["«composant» Fabrique LLM<br/>cascade multi-fournisseurs"]
            BUS["«composant» AlertBus<br/>bus d'alertes Redis"]
            HITLC["«composant» HITL<br/>file de validations humaines"]
            KAN["«composant» Kanban commandes<br/>cycle SUGGÉRÉ → REÇU"]
            MCP["«composant» Serveur MCP<br/>outils cross-domaine"]
            OBS["«composant» Observabilité<br/>traces Langfuse"]
        end
    end

    subgraph DATA["Données"]
        direction LR
        PG[("PostgreSQL")]
        RD[("Redis")]
        MV[("Milvus")]
    end

    FRONT --> CORE
    CORE --> REST
    CORE --> SSE
    CORE --> WS

    REST --> AUTH
    SSE --> AUTH
    WS --> AUTH
    AUTH --> SUP

    SUP --> MSALES
    SUP --> MINV
    SUP --> RAG
    SUP --> HITLC

    SAG --> SSRV --> SREPO
    IAG --> ISRV --> IREPO
    SAG --> LLMF
    IAG --> LLMF
    SAG --> MCP
    IAG --> MCP
    SUP --> OBS

    BUS --> WS
    KAN --> WS
    HITLC --> WS
    IAG --> KAN

    SREPO --> PG
    IREPO --> PG
    BUS --> RD
    SSRV --> RD
    RAG --> MV

    style FRONT fill:#fff3e0,stroke:#e65100
    style IFACES fill:#fce4ec,stroke:#880e4f
    style BACK fill:#e3f2fd,stroke:#0d47a1
    style MSALES fill:#bbdefb,stroke:#1565c0
    style MINV fill:#c8e6c9,stroke:#2e7d32
    style SHARED fill:#ede7f6,stroke:#4527a0
    style DATA fill:#e8f5e9,stroke:#1b5e20
```

## Légende

- **Frontend Angular** : 7 composants d'interface + services core (auth, API, WebSocket, SSE, Signals).
- **Interfaces** : trois canaux exposés par le backend — REST (consultations), SSE (streaming Coach), WebSockets (alertes, guardrail, Kanban).
- **Modules Sales et Inventory** : chacun regroupe ses agents, services et repositories — la séparation des domaines est structurelle.
- **Composants partagés** : RAG, fabrique LLM (cascade), AlertBus, HITL, Kanban, serveur MCP (outils cross-domaine) et observabilité.
- **Données** : PostgreSQL (métier), Redis (cache/pub-sub), Milvus (vecteurs).
