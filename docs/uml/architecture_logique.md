# Architecture logique du moteur multi-agents

> Rendu cible : `img/architecture_logique.png` — organisation en couches + flux d'un cycle multi-agents.
> À rendre via https://mermaid.live (Export PNG) ou `mmdc`.

```mermaid
flowchart TB
    REQ(["Requête utilisateur<br/>(question Coach · analyse boutique ·<br/>cycle déclenché par alerte)"])

    subgraph C1["Couche API"]
        ROUTES["Routes FastAPI<br/>REST /chat · /stream (SSE) · /supervisor · WebSockets"]
        SEC["Auth JWT · RBAC boutique · Rate limiting"]
    end

    subgraph C2["Couche Orchestration — Superviseur LangGraph"]
        INTENT["Détection d'intention<br/>& routage"]
        subgraph PAR["Exécution parallèle des branches"]
            direction LR
            BS["Branche Sales"]
            BI["Branche Inventory"]
            BK["Branche Knowledge<br/>(RAG)"]
            BC["Branche Context<br/>(événements · offres · météo)"]
        end
        STATE["RetailState<br/>(état partagé, fusion par reducers)"]
        COACH["Agent Coach<br/>(formulation de la réponse)"]
        GUARD{"Agent Guardrail"}
        HITL["Validation humaine<br/>(HITL)"]
        SAFE["Réponse de repli sûre"]
        OUT(["Réponse finale<br/>+ statut de vérification"])
    end

    subgraph C3["Couche Agents"]
        direction LR
        subgraph AGS["Agents Sales"]
            AN["Analyste<br/>écarts · prévisions Holt-Winters ·<br/>anomalies · tendances"]
            ST["Stratège<br/>actions cross-domaine<br/>ventes × stocks × contexte"]
        end
        subgraph AGI["Agents Inventory"]
            IA["Analysis<br/>couverture · rotation ·<br/>point de commande · EOQ"]
            IC["Context<br/>prévisions · promos ·<br/>événements · fournisseurs"]
            ID["Decision<br/>commander / accélérer /<br/>maintenir / surveiller<br/>+ quantité + justification"]
        end
    end

    subgraph C4["Couche Services & Repositories"]
        direction LR
        SVC["Services métier<br/>(prévision · scoring · alertes)"]
        REPO["Repositories<br/>PostgreSQL · Redis · Milvus"]
    end

    REQ --> ROUTES --> SEC --> INTENT
    INTENT --> PAR
    BS --> STATE
    BI --> STATE
    BK --> STATE
    BC --> STATE
    STATE --> COACH --> GUARD

    GUARD -- "APPROVE" --> OUT
    GUARD -- "REWRITE (borné)" --> COACH
    GUARD -- "ESCALATE" --> HITL --> OUT
    GUARD -- "BLOCK" --> SAFE --> OUT

    BS -.-> AGS
    BI -.-> AGI
    AGS --> SVC
    AGI --> SVC
    SVC --> REPO

    style C1 fill:#fff3e0,stroke:#e65100
    style C2 fill:#e3f2fd,stroke:#0d47a1
    style C3 fill:#ede7f6,stroke:#4527a0
    style C4 fill:#e8f5e9,stroke:#1b5e20
    style GUARD fill:#ffcdd2,stroke:#b71c1c
    style HITL fill:#fff9c4,stroke:#f57f17
```

## Légende

- **Couche API** : points d'entrée REST/SSE/WebSocket, sécurisés (JWT, RBAC, rate limiting).
- **Couche orchestration** : le superviseur route selon l'intention, exécute les 4 branches en parallèle, fusionne les sorties dans le `RetailState` via des reducers, puis la réponse passe par le Coach et le Guardrail.
- **Verdicts Guardrail** : APPROVE (diffusée), REWRITE (re-passe bornée), ESCALATE (validation humaine), BLOCK (réponse de repli sûre).
- **Couche agents** : 4 agents Sales + 3 agents Inventory, chacun encapsulant une expertise.
- **Couche services & repositories** : logique métier réutilisable et accès aux données, isolés des routes et des agents.
