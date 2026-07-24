# Synchroniser l'équipe sur les mêmes données

Deux morceaux séparés, deux transports différents :

| Quoi | Source de vérité | Transport |
|---|---|---|
| **Schéma** (tables, FK, triggers, fonctions) | `db/migrations/versions/0001..0012` | git |
| **Données** (1,9 M transactions, historiques, référentiels) | dump PostgreSQL | fichier à partager hors git |

Le schéma n'est **jamais** exporté dans le dump de données : Alembic est déjà
la définition versionnée du schéma (refonte monolithe 2026-07-07). Un DDL
exporté en parallèle divergerait dès la migration suivante.

`backups/` est dans `.gitignore` et le dump fait ~70 Mo compressé : il passe par
Drive / OneDrive / clé USB, pas par `git push`.

---

## Côté toi — produire le dump

```bash
python scripts/export_db.py
```

Sortie dans `backups/` :

```
ooredoo_sales_data_<date>.sql       468 Mo   (intermédiaire)
ooredoo_sales_data_<date>.sql.gz     71 Mo   <- le fichier à partager
```

Options : `--full` ajoute un dump autonome schéma+données (dépannage seulement,
ne pas en faire la procédure standard) ; `--no-gzip` saute la compression.

**Tables exclues du dump** — traces d'exécution que chaque poste regénère en
faisant tourner l'app, ~430 Mo sur 1,3 Go et zéro valeur métier partagée :
`agent_logs`, `agent_cycles`, `agent_errors`, `agent_sessions`, `agent_memory`,
`app_sessions`, `rag_queries`, `inventory.agent_runs`, `sales.transactions_rt`.
Plus `alembic_version`, renseignée par `alembic upgrade head` côté coéquipier.

Le dump est un instantané cohérent (pg_dump = une seule transaction). Si le
backend tourne pendant l'export, les écritures postérieures ne sont pas dedans —
c'est normal, ce sont des traces temps réel.

---

## Côté coéquipier — charger le dump

Prérequis : PostgreSQL 17, `.env` renseigné (`DB_*`), venv installé.

```bash
# 1. base vide
createdb -U postgres ooredoo_sales

# 2. schéma depuis git
alembic upgrade head          # doit finir sur 0012

# 3. données
python scripts/import_db.py backups/ooredoo_sales_data_<date>.sql.gz
```

Le script accepte `.sql` comme `.sql.gz` (décompression en flux, pas de fichier
temporaire de 468 Mo), affiche un décompte de contrôle à la fin, et s'arrête à
la première erreur SQL (`ON_ERROR_STOP=1`).

Compte attendu après import :

| Table | Lignes |
|---|---|
| `sales.transactions` | 1 929 823 |
| `inventory.stock_history` | 1 122 161 |
| `inventory.sales_history` | 693 954 |
| `sales.produits` | 4 593 |

**Réimporter sur une base déjà peuplée** : `--force` vide d'abord les tables
cibles (`TRUNCATE ... CASCADE`, hors tables de traces). Sans ce flag chaque
`COPY` échoue en violation de clé primaire.

---

## Vérifier que deux bases sont identiques

```bash
psql -U postgres -d ooredoo_sales -tAc "
SELECT string_agg(format('SELECT %L AS t, count(*) FROM %I.%I',
       schemaname||'.'||relname, schemaname, relname),
       ' UNION ALL ' ORDER BY schemaname, relname)
FROM pg_stat_user_tables;"
```

Exécuter la requête produite des deux côtés et comparer. Seules les tables de
traces exclues doivent différer.

---

## Notes

- **`alembic upgrade head` depuis une base neuve** ne passait pas avant le
  2026-07-24 : `0001_baseline.sql` est un export pg_dump, il commence par
  `set_config('search_path','',false)` — le `false` vide le search_path pour
  toute la session, pas juste la transaction. Alembic enchaînant toutes les
  révisions sur une seule connexion, le `CREATE FUNCTION sync_stock_on_sale()`
  non qualifié de 0009 échouait en `InvalidSchemaName`. Corrigé en qualifiant
  `public.sync_stock_on_sale`. Toute nouvelle migration doit qualifier ses
  objets — ne jamais compter sur le search_path après 0001.
- Milvus (RAG) n'est pas dans ce dump : `docker compose up -d` puis
  `python scripts/seed_rag_milvus.py`.
- Les mots de passe viennent de `.env` (non versionné) — chacun le sien.
