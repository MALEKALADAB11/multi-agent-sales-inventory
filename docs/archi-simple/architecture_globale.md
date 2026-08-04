# Architecture globale

Plateforme agentique retail : elle observe les ventes et les stocks en temps réel,
et produit deux sorties — un **conseil de vente** au conseiller, une **suggestion de
commande** au manager.

```mermaid
flowchart LR
    subgraph S["1 · Sources"]
        S1[Ventes temps réel<br/>POS boutique]
        S2[Stocks &<br/>approvisionnement]
        S3[Signaux externes<br/>météo, événements, concurrence]
    end

    subgraph D["2 · Données"]
        D1[(PostgreSQL<br/>7 schémas)]
        D2[(Milvus<br/>RAG scripts de vente)]
        D3[(Redis<br/>cache + bus d'alertes)]
    end

    subgraph I["3 · Intelligence — FastAPI"]
        I1[Orchestrateur LangGraph<br/>4 agents]
        I2[Guardrail<br/>7 règles métier]
        I3[Prévision<br/>MSTL / Holt-Winters · XGBoost]
        I4[LLM<br/>Mistral · Groq]
    end

    subgraph F["4 · Interface — Angular"]
        F1[Dashboard ventes]
        F2[Chat coach]
        F3[Inventaire & alertes]
        F4[Kanban commandes]
        F5[Supervision & qualité]
    end

    S --> D --> I --> F
    F -. feedback humain .-> D
    I -. traces .-> OBS[Observabilité<br/>Langfuse · logs · KPIs]
```

## Les 4 couches

| Couche | Rôle | Technique |
|---|---|---|
| **Sources** | Ventes, stocks, signaux marché, historique 4,5 ans | transactions POS, mouvements de stock, scrapers |
| **Données** | Vérité métier, mémoire sémantique, temps réel | PostgreSQL (schéma versionné Alembic), Milvus, Redis |
| **Intelligence** | Décide et rédige | LangGraph, garde-fous, modèles de prévision, LLM |
| **Interface** | Rend actionnable | Angular, REST + WebSocket + streaming |

## Points structurants

- **Une seule API** (FastAPI) expose ventes, inventaire et agents ; le front ne parle qu'à elle.
- **Temps réel par WebSocket** : le dashboard et les alertes stock sont poussés, pas interrogés.
- **Schéma de base versionné** : toute évolution passe par une migration, aucune création de table à l'exécution.
- **Aucune décision automatique irréversible** : commandes et cas sensibles passent par une validation humaine.
- **Boucle fermée** : la note humaine sur un conseil (👍/👎) redevient une donnée d'évaluation des agents.

→ Diagramme éditable : [architecture_globale.drawio](architecture_globale.drawio)
→ Détail des agents : [architecture_agentique.md](architecture_agentique.md)
