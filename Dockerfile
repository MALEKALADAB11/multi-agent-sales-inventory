# ==============================================================================
# Backend FastAPI — AI Sales Coach & Inventory Ooredoo Tunisia
# ==============================================================================
# Build en deux étages : les compilateurs (numba, xgboost, prophet) restent dans
# l'étage builder, l'image finale n'embarque que le venv et le code.
#
#   docker build -t retail-backend .
#   docker compose up -d backend
# ------------------------------------------------------------------------------

# ── Étage 1 — dépendances ─────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# build-essential : roues sources éventuelles (prophet/cmdstanpy, coreforecast).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements-docker.txt .
RUN pip install --upgrade pip setuptools wheel \
    && pip install -r requirements-docker.txt


# ── Étage 2 — runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# postgresql-client : pg_isready + psql, utilisés par les scripts docker/
#                     (attente de la base, restauration du dump).
# curl              : healthcheck HTTP du conteneur.
# libgomp1          : runtime OpenMP requis par xgboost et statsforecast.
RUN apt-get update && apt-get install -y --no-install-recommends \
        postgresql-client curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Africa/Tunis

WORKDIR /srv/app

# Le code seulement : .dockerignore écarte venv/, volumes/, backups/, docs/…
# et surtout .env — app/main.py fait load_dotenv(override=True), un .env
# embarqué écraserait les variables injectées par docker compose (DB_HOST=postgres
# redeviendrait localhost) et l'API ne trouverait plus la base.
COPY alembic.ini main.py ./
COPY app/     ./app/
COPY db/      ./db/
COPY data/    ./data/
COPY scripts/ ./scripts/
COPY docker/entrypoint.sh docker/bootstrap-db.sh /usr/local/bin/

# tr : si le dépôt a été cloné avec core.autocrlf=true (Windows), les scripts
# arrivent en CRLF et le noyau refuse le shebang (« no such file or directory »).
RUN for f in /usr/local/bin/entrypoint.sh /usr/local/bin/bootstrap-db.sh; do \
        tr -d '\r' < "$f" > "$f.tmp" && mv "$f.tmp" "$f" && chmod +x "$f"; \
    done \
    && useradd --create-home --uid 1000 appuser \
    && mkdir -p /srv/app/logs /srv/app/.rag_cache \
    && chown -R appuser:appuser /srv/app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
