# Retail AI — Ooredoo Tunisia (Sales Coaching + Inventory Optimization)

Moteur agentique retail temps réel : coaching de vente (analyste / stratège /
coach / guardrail) + optimisation des stocks (contexte / analyse / décision),
orchestrés en monolithe FastAPI + LangGraph sur PostgreSQL. Un Agent
Superviseur pilote l'ensemble : il déclenche les branches vente, stock et
connaissance en parallèle, fusionne leurs résultats, et publie (ou bloque)
la recommandation finale après validation du Guardrail.

Documentation complète de chaque agent : [docs/agents/](docs/agents/)
(vue d'ensemble + une fiche par agent : analyste, stratège, coach, guardrail,
contexte, analyse, décision, superviseur).

## Architecture

```
main.py                 # shim → app.main:app
app/
├── main.py             # app factory, lifespan (verify_schema), WebSockets
├── core/                # config (unique), db, agent_logger, langfuse, schema_check
├── api/                 # routers: auth, monitoring, hitl, supervisor, cycle,
│                         #          forecast, stores
├── sales/                # coaching/ (agents), data/, orchestration/, core/, mcp/timefm
└── inventory/           # agents/, api/, repositories/, services/, tools/, forecasting/
db/
├── migrations/          # Alembic — SEULE source de vérité du schéma PostgreSQL (0001-0018)
└── seeds/               # seeds idempotents (source : PostgreSQL uniquement, zéro CSV)
scripts/                 # smoke_test.py, export/import de dump, embed RAG, générateurs
evals/                   # bancs d'évaluation (juge LLM, RAGAS, benchmark modèles)
tests/                   # pytest (80+ tests) — tests/{coaching,inventory,evals}
docs/agents/             # documentation détaillée des 8 agents
docker/, Dockerfile,     # conteneurisation complète (voir DOCKER.md)
docker-compose.yml
```

Règles d'or :
- **Le schéma DB appartient aux migrations Alembic** (`db/migrations`). L'app ne
  crée jamais de table : au boot, `app/core/schema_check.py` vérifie que la base
  est à la révision attendue et refuse de démarrer sinon.
- **Zéro CSV** : toutes les données vivent dans PostgreSQL (`ooredoo_sales`).
- **Zéro hardcode boutique** : `DEFAULT_STORE_ID` vient de la config (`.env`) ;
  les ids inconnus passent en pass-through (multi-boutiques).

Frontend Angular (dashboard) : dépôt séparé, `D:\frontend\PFE` — contrat API
vérifié par le smoke test de ce backend.

## Prérequis

| Outil | Version | Obligatoire ? |
|---|---|---|
| Python | 3.12 | Oui |
| PostgreSQL | 14+ | Oui |
| Redis | 6+ | Oui (bus d'alertes + cache) |
| Docker Desktop | 28+ / Compose v2 | Non — seulement pour l'option "tout Docker" ou pour Milvus/Langfuse en local |
| Milvus | 2.5.x | Non — le RAG bascule sur un corpus de secours si absent |
| Ollama | dernière | Non — dernier maillon de secours de la chaîne LLM |
| Au moins une clé API LLM | OpenRouter, Groq ou Mistral | Oui (une seule suffit, voir plus bas) |

Deux façons de démarrer : **tout Docker** (le plus simple, recommandé pour une
démo complète) ou **installation manuelle** (pour développer/débugger).

## Option A — Tout Docker (API + dashboard + Postgres + Redis + Milvus + Langfuse)

Une seule commande, stack complète. Détails, prérequis, restauration
automatique de la base et dépannage : voir **[DOCKER.md](DOCKER.md)**.

```bash
cp .env.example .env        # puis renseigner au moins une clé LLM
docker compose up -d --build
docker compose logs -f db-init   # suivre migrations + restauration du dump
docker compose logs -f backend   # suivre le démarrage de l'API
```

| Service | URL |
|---|---|
| Dashboard | http://localhost:8080 |
| API (Swagger) | http://localhost:8000/docs |
| Langfuse | http://localhost:3001 (`admin@ooredoo.tn` / `admin123`) |
| PostgreSQL | `localhost:5433` |
| Milvus | `localhost:19530` |

## Option B — Installation manuelle

### 1. Cloner et créer l'environnement virtuel

```bash
git clone https://github.com/MALEKALADAB11/multi-agent-sales-inventory.git
cd multi-agent-sales-inventory

python -m venv venv
# Windows
venv\Scripts\activate
# Linux / macOS
source venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

`requirements.txt` couvre l'intégralité des dépendances du projet (API,
agents, forecasting, RAG, scraping, observabilité, tests, évaluation). Une
version allégée, dérivée de celle-ci pour ne garder que ce que le conteneur
backend importe réellement, existe dans `requirements-docker.txt` (utilisée
uniquement par le `Dockerfile`, voir Option A).

Le scraping des offres Ooredoo (agent Stratège) utilise Playwright, qui a
besoin d'un navigateur téléchargé séparément :

```bash
playwright install chromium
```

Si cette étape est sautée, le scraper bascule silencieusement sur un
fallback dégradé — ce n'est jamais bloquant.

### 2. PostgreSQL

Créer une base vide :

```bash
createdb -U postgres ooredoo_sales
```

Puis appliquer le schéma via Alembic (voir §3) et peupler la base — deux
options :

- **Seeds à partir de zéro** (données de référence, sans historique de
  ventes réel) — voir §4 ;
- **Restaurer un dump existant** (recommandé, données réelles/simulées déjà
  cohérentes) :

  ```bash
  python scripts/import_db.py backups/ooredoo_sales_data_<date>.sql.gz
  # --force pour réimporter sur une base déjà peuplée
  ```

### 3. Variables d'environnement

```bash
cp .env.example .env
```

Puis éditer `.env`. Sections principales :

- **Base de données** : `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` /
  `DB_PASSWORD` (et leurs équivalents `POSTGRES_*` / `DATABASE_URL`, lus par
  des modules différents — garder les deux jeux synchronisés).
- **LLM** — chaîne de secours à 4 niveaux, décrite dans
  `app/inventory/utils/llm_factory.py` : **OpenRouter** (primaire) →
  **Groq** (jusqu'à 4 clés en rotation) → **Mistral** → **Ollama** (local,
  dernier recours). Une seule clé suffit pour démarrer (typiquement
  `OPENROUTER_API_KEY` ou `MISTRAL_API_KEY`) ; les autres peuvent rester à
  leur valeur d'exemple, le système les court-circuite si elles échouent.
- **Redis** : `REDIS_HOST` / `REDIS_PORT` (bus d'alertes, cache stocks).
- **Milvus / RAG** : `MILVUS_URI` — si Milvus n'est pas lancé, le RAG
  bascule sur son corpus de secours (dégradé mais fonctionnel).
- **Langfuse / MLflow** : optionnels, observabilité seulement.
- **Business** : `DEFAULT_STORE_ID`, `DEFAULT_DAILY_TARGET`,
  `INVENTORY_PREWARM_STORES`, etc. — valeurs par défaut déjà cohérentes pour
  la boutique de démo `I63`.

Le fichier `.env.example` documente chaque variable en commentaire.

### 4. Migrations et seeds

```bash
alembic upgrade head                        # schéma — révisions 0001 à 0018
python db/seeds/run_all_seeds.py            # toutes les seeds, idempotent
```

Seeds individuelles disponibles sous `db/seeds/` si besoin d'un sous-jeu
précis (`seed_app_users.py`, `seed_supplier_products.py`,
`seed_coaching_scripts.py`, `seed_reference_data.py`, ...).

### 5. Infrastructure annexe (Redis, Milvus, Langfuse)

Le plus simple, même en installation manuelle, est de ne lancer que les
services d'infra via Docker et de garder l'API en local :

```bash
docker compose up -d redis milvus etcd minio langfuse langfuse-db
```

Sinon, installer/lancer Redis nativement ; Milvus et Langfuse peuvent être
omis (dégradé, cf. §3).

### 6. Lancer l'API

```bash
python -m uvicorn main:app --reload --port 8000
```

L'API répond sur `http://localhost:8000` (Swagger : `/docs`).

## Vérification

```bash
python scripts/smoke_test.py                   # contrat API complet + 3 WebSockets
SMOKE_STORE=I14 python scripts/smoke_test.py   # autre boutique

python -m pytest tests/                        # suite complète (marqueurs db / integration)
python -m pytest tests/ -m "not db and not integration"   # sans PostgreSQL ni serveur lancé
```

## Base de données

- `alembic current` / `alembic upgrade head` — migrations `0001` à `0018`
  (`db/migrations/versions/`) : baseline introspectée, drop defaults I63,
  fixes data, supplier_products, chaîne causale, FK manquantes, feedback
  agent, demand sensing, tracking PO/livraison, demandes de réappro,
  mouvements de stock, vues, scores de recommandation, historique de
  tendances critiques, associations produits.
- `scripts/export_db.py` / `scripts/import_db.py` — export/import de dump
  (`backups/`), données seules par défaut.

## Documentation additionnelle

- [docs/agents/](docs/agents/) — architecture multi-agents en détail (une
  fiche par agent + vue générale du système).
- [DOCKER.md](DOCKER.md) — déploiement Docker complet, dépannage,
  variables spécifiques au conteneur.

## Frontend

Dashboard Angular dans un dépôt séparé : `D:\frontend\PFE`. Le contrat
d'API qu'il consomme est vérifié par `scripts/smoke_test.py` ci-dessus.

## Dépannage

- **Windows : `OSError WinError 1114` / DLL `c10.dll` cassée** — torch est
  optionnel pour le forecasting Chronos. Le code retombe automatiquement sur
  un fallback statistique ; pour désactiver Chronos explicitement :
  `DISABLE_CHRONOS=1` dans `.env`.
- **Le scraping des offres Ooredoo échoue systématiquement** — `ooredoo.tn`
  est injoignable depuis certains réseaux (dont celui utilisé pour le
  déploiement) ; le système bascule sur un fallback dégradé en cache, ce
  n'est pas bloquant. Désactivable via `OOREDOO_SCRAPER_ENABLED=0`.
- **Port PostgreSQL** : `5432` en installation manuelle, `5433` en Docker
  (pour ne pas entrer en conflit avec un PostgreSQL déjà installé sur
  l'hôte) — ne pas mélanger les deux dans le même `.env`.
- **Page Stocks vide au premier démarrage** — le préchauffage du cache
  inventaire prend quelques minutes par boutique listée dans
  `INVENTORY_PREWARM_STORES`.
