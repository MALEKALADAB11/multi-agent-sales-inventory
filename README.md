# Retail AI — Ooredoo Tunisia (Sales Coaching + Inventory Optimization)

Moteur agentique retail temps réel : coaching de vente (analyste / stratège /
coach / guardrail) + optimisation des stocks (analysis / context / decision),
orchestrés en monolithe FastAPI + LangGraph sur PostgreSQL.

## Architecture

```
main.py                 # shim → app.main:app
app/
├── main.py             # app factory, lifespan (verify_schema), WebSockets
├── core/               # config (unique), db, agent_logger, langfuse, schema_check
├── api/                # routers: auth, monitoring, hitl, supervisor, cycle,
│                       #          forecast, stores
├── sales/              # coaching/ (agents), data/, orchestration/, core/, mcp/timefm
└── inventory/          # agents/, api/, repositories/, services/, tools/, forecasting/
db/
├── migrations/         # Alembic — SEULE source de vérité du schéma PostgreSQL
└── seeds/              # seeds idempotents (source: PostgreSQL uniquement, zéro CSV)
scripts/                # smoke_test.py, embed RAG, générateurs
tests/                  # pytest (80+ tests) — tests/{coaching,inventory}
docs/                   # DATABASE_SCHEMA.md, DATA_GAPS.md, BACKLOG.tsv, ...
```

Règles d'or :
- **Le schéma DB appartient aux migrations Alembic** (`db/migrations`). L'app ne
  crée jamais de table : au boot, `app/core/schema_check.py` vérifie que la base
  est à la révision attendue et refuse de démarrer sinon.
- **Zéro CSV** : toutes les données vivent dans PostgreSQL (`ooredoo_sales`,
  8 schémas / 51 tables / 16 vues).
- **Zéro hardcode boutique** : `DEFAULT_STORE_ID` vient de la config (.env) ;
  les ids inconnus passent en pass-through (multi-boutiques).

## Démarrage

```bash
# 1. Infra (Milvus, Redis, Langfuse)
docker compose up -d

# 2. Base de données (première fois : créer ooredoo_sales puis)
alembic upgrade head
python db/seeds/seed_app_users.py
python db/seeds/seed_supplier_products.py

# 3. API (port 8000)
venv/Scripts/python -m uvicorn main:app --port 8000
```

## Vérification

```bash
python scripts/smoke_test.py                   # contrat API complet + 3 WebSockets
SMOKE_STORE=M10 python scripts/smoke_test.py   # autre boutique
python -m pytest tests/                        # suite unitaire/intégration
```

## Base de données

- `alembic current` / `alembic upgrade head` — migrations (versions 0001-0007 :
  baseline introspectée, drop defaults I63, fixes data, supplier_products,
  chaîne causale, FK manquantes, commentaires).
- `docs/DATABASE_SCHEMA.md` — schéma détaillé (régénéré par introspection).
- `docs/DATA_GAPS.md` — gaps data restants, priorisés.

Frontend Angular : `D:\frontend\PFE` (contrat API vérifié par le smoke test).
