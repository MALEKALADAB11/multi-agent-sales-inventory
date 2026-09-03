# Déploiement Docker — Backend + Frontend

Stack complète en une commande : API FastAPI, dashboard Angular, PostgreSQL,
Redis, Milvus et Langfuse.

## 1. Prérequis

- Docker Desktop (Windows) — testé avec Docker 28.3 / Compose v2.38
- ~12 Go d'espace disque (images + volume PostgreSQL peuplé)
- Le dépôt frontend cloné à côté du backend :

  ```
  D:\backend\multi-agent-sales-inventory   <- ce dépôt
  D:\frontend\PFE                          <- dashboard Angular
  ```

  Autre emplacement ? Ajouter `FRONTEND_PATH=/chemin/vers/PFE` dans `.env`.

- Un fichier `.env` à la racine (copier `.env.example` et renseigner les clés
  LLM). Les valeurs `DB_HOST`, `MILVUS_URI`, `REDIS_HOST`… n'ont pas besoin
  d'être modifiées : `docker-compose.yml` les surcharge pour le réseau Docker.

## 2. Démarrage

```bash
docker compose up -d --build
```

Premier lancement : ~15 min de build, puis quelques minutes de restauration de
la base. L'API répond ensuite en moins d'une minute, mais la page Stocks reste
vide le temps du préchauffage (~20 min, voir §4). Aux lancements suivants,
`docker compose up -d` démarre en moins d'une minute.

| Service | URL | Notes |
|---|---|---|
| Dashboard | http://localhost:8080 | nginx : sert Angular et relaie `/api` et `/ws` |
| API | http://localhost:8000/docs | Swagger, accès direct sans proxy |
| Langfuse | http://localhost:3001 | `admin@ooredoo.tn` / `admin123` |
| PostgreSQL | `localhost:5433` | 5433 pour ne pas heurter le PostgreSQL de l'hôte |
| Milvus | `localhost:19530` | |

Suivre le démarrage :

```bash
docker compose logs -f db-init   # migrations + restauration du dump
docker compose logs -f backend   # cycles d'agents, WebSockets
docker compose ps                # état et santé des conteneurs
```

## 3. Ce que fait `db-init`

Service à usage unique, exécuté avant le backend (`service_completed_successfully`).
Il reproduit la procédure de `scripts/import_db.py` :

1. attend que PostgreSQL réponde ;
2. `alembic upgrade head` — migrations `0001` à `0018`, seule source de vérité
   du schéma ;
3. si `sales.boutiques` est vide, restaure le dump « données seules » le plus
   récent de `backups/` (`ooredoo_sales_data_*.sql.gz`), monté en lecture seule.

La restauration tourne dans **une seule transaction** : en cas d'erreur, rien
n'est écrit — jamais de base à moitié remplie. Aux démarrages suivants elle est
sautée puisque la base est peuplée.

Repartir d'une base vide (schéma seul, à peupler avec `db/seeds/run_all_seeds.py`) :

```bash
AUTO_RESTORE=0 docker compose up -d
```

Remettre la base à zéro complètement :

```bash
docker compose down
docker volume rm multi-agent-sales-inventory_pgdata
docker compose up -d
```

## 4. Points d'architecture

**Le `.env` n'entre pas dans l'image.** `app/main.py` appelle
`load_dotenv(override=True)` : un `.env` embarqué écraserait les variables
injectées par Compose et le backend chercherait sa base sur `localhost`. Il est
donc listé dans `.dockerignore` et ses valeurs arrivent par `env_file`, que le
bloc `environment:` surcharge pour les hôtes réseau.

**Une seule origine pour le navigateur.** `environment.prod.ts` pose
`apiUrl = window.location.origin` ; nginx route ensuite par préfixe (`/api`,
`/ws`, le reste vers `index.html`). Aucune URL de backend à recompiler pour
changer de poste, et pas de préflight CORS.

**SSE et WebSockets.** Le proxy `/api` désactive `proxy_buffering` (sans quoi la
réponse du coach, diffusée en SSE, arriverait d'un bloc à la fin) et propage
l'en-tête `Upgrade` — le flux WebSocket de l'inventaire vit sous
`/api/inventory/ws/{store}`. Le proxy `/ws` garde les connexions ouvertes 24 h.

**Ollama reste sur l'hôte.** Les modèles pèsent plusieurs Go et sont déjà
téléchargés ; le conteneur les atteint via
`OLLAMA_BASE_URL=http://host.docker.internal:11434`. Ollama n'est qu'un dernier
recours dans la cascade LLM (OpenRouter → Groq → Mistral → Ollama) : s'il n'est
pas lancé, les providers distants suffisent.

**Redis vient de l'environnement.** `Config.redis_url()`
(`app/core/config.py`) construit l'URL depuis `REDIS_HOST`/`REDIS_PORT`
(`REDIS_URL` l'emporte s'il est posé). Côté sales, `AlertBus`, `StateBus` et
`AlertCycleTrigger` avaient `redis://localhost:6379` en dur : dans un
conteneur, le bus d'alertes tournait à vide en boucle de reconnexion et aucun
cycle événementiel ne partait, sans que rien d'autre ne le signale qu'une ligne
`Redis KO` toutes les 5 s.

**Préchauffage du cache inventory.** Le pipeline stocks à froid coûte 7 à 15 min
par boutique (ajustement `statsforecast`, mesuré : 441 s sur I63, 914 s sur
I14), alors que le dashboard abandonne ses appels à 60 s : sans préchauffage la
page Stocks affiche 0 partout, ce qui se lit à tort comme « aucune rupture ».
`INVENTORY_PREWARM_STORES` couvre donc `I63` (défaut backend) **et** `I14`
(boutique ouverte par le build production du front, `environment.prod.ts`). En
ajouter une autre : la lister dans cette variable — les deux passes de
préchauffage, la rapide et celle qui appelle les LLM, lisent la même liste via
`_prewarm_store_ids()`.

Compter donc ~20 min après le démarrage avant que la page Stocks soit complète.
Le reste du dashboard (ventes temps réel, coach) est utilisable immédiatement.

**Image backend allégée — 1,92 Go.** `requirements-docker.txt` reprend les pins
de `requirements.txt` mais retire ce qu'aucun module de `app/` n'importe :
`torch`/`timesfm`, `mlflow`, `ragas`/`datasets`, `scikit-learn`, `pytest`,
ainsi que `sympy`/`networkx` (dépendances de torch). Il substitue aussi
`xgboost-cpu` à `xgboost` : sous Linux le paquet standard tire
`nvidia-nccl-cu12` (454 Mo) et un `libxgboost.so` CUDA (333 Mo) dont le serveur
n'a aucun usage — il ne fait qu'appeler `Booster.load_model()` sur des `.ubj`
déjà entraînés. À elle seule cette substitution fait passer l'image de 3,41 Go
à 1,92 Go.

Toute dépendance ajoutée à `requirements.txt` et importée par `app/` doit être
répercutée ici.

## 5. Opérations courantes

```bash
# Rebuild après modification du code backend
docker compose up -d --build backend

# Rebuild du dashboard
docker compose up -d --build frontend

# Shell dans le conteneur backend
docker compose exec backend bash

# Migration manuelle
docker compose exec backend alembic upgrade head

# Peupler Milvus (RAG) — sinon le retriever utilise son corpus de repli
docker compose exec backend python scripts/seed_rag_milvus.py

# psql sur la base applicative
docker compose exec postgres psql -U postgres -d ooredoo_sales
```

## 6. Dépannage

| Symptôme | Cause probable | Correctif |
|---|---|---|
| `db-init` échoue sur « PostgreSQL injoignable » | volume `pgdata` corrompu | `docker compose down && docker volume rm multi-agent-sales-inventory_pgdata` |
| Dashboard à 0, backend sain | base vide (restauration sautée ou `AUTO_RESTORE=0`) | vérifier `docker compose logs db-init` |
| Page Stocks à 0 les premières minutes | cache inventory encore froid pour cette boutique | attendre le log `✅ Cache inventory prechauffe`, ou ajouter la boutique à `INVENTORY_PREWARM_STORES` |
| `502 Bad Gateway` sur `/api` | backend encore en démarrage (préchargement des agents) | `docker compose logs -f backend`, attendre le healthcheck |
| Réponse du coach qui arrive d'un bloc | proxy modifié, `proxy_buffering` réactivé | vérifier `docker/nginx.conf` côté frontend |
| Port 5433 ou 8080 déjà pris | autre service sur l'hôte | changer le mapping dans `docker-compose.yml` |
| `[AlertCycleTrigger] Redis KO … localhost:6379` en boucle | `REDIS_HOST` non transmis au conteneur | vérifier le bloc `environment:` du service `backend` ; contrôle : `docker compose exec redis redis-cli pubsub numpat` doit renvoyer 1 |
| `npm ci` échoue (« Missing: @emnapi/core from lock file ») | npm de l'image plus ancien que celui du lock | le Dockerfile front installe `npm@11.9.0`, la version du champ `packageManager` ; garder les deux alignés |
| 502 sur `/api` juste après un rebuild backend | ancienne IP en cache côté nginx | déjà traité : `docker/nginx.conf` résout `backend` à chaque requête via le DNS Docker |
