# Architecture physique et déploiement

> Rendu cible : `img/architecture_physique.png` — vue déploiement (conteneurs Docker + services externes).
> À rendre via https://mermaid.live (Export PNG) ou `mmdc`.

```mermaid
flowchart LR
    subgraph POSTE["Poste client"]
        NAV["🖥️ Navigateur Web<br/>(PC · tablette boutique)"]
    end

    subgraph HOTE["Hôte Docker — docker-compose"]
        direction TB
        subgraph CFRONT["Conteneur frontend"]
            NGX["Serveur web<br/>Application Angular (build)"]
        end
        subgraph CBACK["Conteneur backend"]
            FASTAPI["FastAPI + Uvicorn<br/>API REST · SSE · WebSocket<br/>Moteur agentique LangGraph"]
        end
        subgraph CPG["Conteneur PostgreSQL"]
            PG[("PostgreSQL<br/>schéma versionné Alembic")]
        end
        subgraph CREDIS["Conteneur Redis"]
            RD[("Redis<br/>cache · pub/sub alertes")]
        end
        subgraph CMILVUS["Conteneur Milvus"]
            MV[("Milvus<br/>index vectoriel scripts")]
        end
        NET["réseau interne Docker"]
    end

    subgraph EXT["Services externes (Internet)"]
        direction TB
        MISTRAL["☁️ Mistral AI"]
        OPENR["☁️ OpenRouter"]
        GROQ["☁️ Groq"]
        LF["☁️ Langfuse<br/>(observabilité)"]
    end

    subgraph LOCAL["Repli local"]
        OLL["Ollama<br/>(LLM local, dernier repli)"]
    end

    NAV -- "HTTPS (pages statiques)" --> NGX
    NAV -- "HTTPS : REST + SSE" --> FASTAPI
    NAV -- "WSS : WebSocket temps réel" --> FASTAPI

    FASTAPI -- "TCP 5432" --> PG
    FASTAPI -- "TCP 6379" --> RD
    FASTAPI -- "TCP 19530" --> MV

    FASTAPI -- "HTTPS (cascade LLM 1)" --> MISTRAL
    FASTAPI -- "HTTPS (cascade LLM 2)" --> OPENR
    FASTAPI -- "HTTPS (cascade LLM 3)" --> GROQ
    FASTAPI -- "HTTP local (cascade LLM 4)" --> OLL
    FASTAPI -- "HTTPS (traces)" --> LF

    style POSTE fill:#fff3e0,stroke:#e65100
    style HOTE fill:#e3f2fd,stroke:#0d47a1
    style EXT fill:#f3e5f5,stroke:#6a1b9a
    style LOCAL fill:#e8f5e9,stroke:#1b5e20
```

## Légende

- **Poste client** : navigateur web (PC ou tablette en boutique), aucun logiciel spécifique requis.
- **Hôte Docker** : 5 conteneurs orchestrés par docker-compose (frontend, backend, PostgreSQL, Redis, Milvus) communiquant sur le réseau interne Docker.
- **Services externes** : fournisseurs LLM appelés en cascade (Mistral → OpenRouter → Groq → Ollama local) et Langfuse pour l'observabilité.
- **Protocoles** : HTTPS pour REST/SSE, WSS pour le temps réel, TCP interne pour les bases de données.
