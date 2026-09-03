#!/usr/bin/env bash
# ==============================================================================
# entrypoint.sh — démarrage du conteneur backend
# ==============================================================================
# Le schéma et les données sont préparés par le service db-init (compose) ; ici
# on ne fait qu'attendre que PostgreSQL réponde, car uvicorn ouvre son pool
# asyncpg dès le startup FastAPI et échouerait sur une base encore froide.
# ------------------------------------------------------------------------------
set -euo pipefail

DB_HOST="${DB_HOST:-postgres}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
export PGPASSWORD="${DB_PASSWORD:-postgres}"

echo "[entrypoint] attente de PostgreSQL sur ${DB_HOST}:${DB_PORT} …"
for i in $(seq 1 60); do
    pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" >/dev/null 2>&1 && break
    [ "$i" = "60" ] && { echo "[entrypoint] PostgreSQL injoignable" >&2; exit 1; }
    sleep 2
done

# Filet de sécurité quand le backend est lancé seul (docker run), sans db-init.
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    echo "[entrypoint] alembic upgrade head …"
    (cd /srv/app && alembic upgrade head)
fi

echo "[entrypoint] démarrage : $*"
exec "$@"
