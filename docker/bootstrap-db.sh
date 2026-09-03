#!/usr/bin/env bash
# ==============================================================================
# bootstrap-db.sh — prépare la base avant le démarrage de l'API (service db-init)
# ==============================================================================
# Reproduit la procédure documentée dans scripts/import_db.py :
#
#     createdb ooredoo_sales     -> fait par l'image postgres (POSTGRES_DB)
#     alembic upgrade head       -> schéma 0001 .. 0018
#     import du dump données     -> backups/ooredoo_sales_data_<date>.sql.gz
#
# L'ordre est imposé : le dump est « données seules », il exige que les tables
# existent déjà. La restauration ne s'exécute qu'une fois — si sales.boutiques
# contient déjà des lignes, elle est sautée (redémarrages idempotents).
# ------------------------------------------------------------------------------
set -euo pipefail

DB_HOST="${DB_HOST:-postgres}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-ooredoo_sales}"
DB_USER="${DB_USER:-postgres}"
export PGPASSWORD="${DB_PASSWORD:-postgres}"

PSQL=(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -X -q)

log() { printf '\n\033[1;36m[db-init]\033[0m %s\n' "$*"; }

# ── 1. Attendre PostgreSQL ────────────────────────────────────────────────────
log "attente de PostgreSQL sur ${DB_HOST}:${DB_PORT} …"
for i in $(seq 1 60); do
    if pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
        break
    fi
    [ "$i" = "60" ] && { echo "PostgreSQL injoignable après 60 tentatives" >&2; exit 1; }
    sleep 2
done
log "PostgreSQL prêt."

# ── 2. Migrations Alembic (source de vérité unique du schéma) ─────────────────
log "alembic upgrade head …"
cd /srv/app
alembic upgrade head
log "schéma à jour : $(alembic current 2>/dev/null | tail -1)"

# ── 3. Restauration des données de référence (première fois seulement) ────────
if [ "${AUTO_RESTORE:-1}" != "1" ]; then
    log "AUTO_RESTORE=0 — restauration ignorée."
    exit 0
fi

rows=$("${PSQL[@]}" -tAc "SELECT count(*) FROM sales.boutiques" 2>/dev/null || echo 0)
if [ "${rows:-0}" -gt 0 ]; then
    log "base déjà peuplée (${rows} boutiques) — restauration ignorée."
    exit 0
fi

# Dump choisi : DUMP_FILE, sinon le plus récent des exports « données seules ».
DUMP="${DUMP_FILE:-$(ls -1 /backups/ooredoo_sales_data_*.sql.gz 2>/dev/null | sort | tail -1 || true)}"
if [ -z "$DUMP" ] || [ ! -f "$DUMP" ]; then
    log "aucun dump dans /backups — base laissée vide (schéma seul)."
    log "  -> exporte-en un avec: python scripts/export_db.py"
    exit 0
fi

log "restauration de $(basename "$DUMP") ($(du -h "$DUMP" | cut -f1)) — quelques minutes…"
# --single-transaction : une erreur au milieu annule tout, jamais de base à
#   moitié remplie qu'on croirait bonne.
# sed : les méta-commandes \restrict/\unrestrict émises par pg_dump 17.10 ne
#   ne sont comprises que par les psql >= 17.6. L'image en livre 17.11, donc
#   elles passeraient — on les retire quand même : elles ne portent aucune
#   donnée, et ce filet évite un échec si l'image de base repassait un jour à un
#   client plus ancien. Classe [\] et non deux antislashs : selon les versions
#   de sed, ces derniers ne valent pas un antislash littéral.
if gzip -dc "$DUMP" \
     | sed -e '/^[\]restrict/d' -e '/^[\]unrestrict/d' \
     | "${PSQL[@]}" -v ON_ERROR_STOP=1 --single-transaction; then
    log "restauration terminée : $("${PSQL[@]}" -tAc "SELECT count(*) FROM sales.boutiques") boutiques, \
$("${PSQL[@]}" -tAc "SELECT count(*) FROM sales.transactions" 2>/dev/null || echo '?') ventes."
else
    echo "[db-init] ÉCHEC de la restauration — base restaurée à son état d'avant (transaction annulée)." >&2
    exit 1
fi
