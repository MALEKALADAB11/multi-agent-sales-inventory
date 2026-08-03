"""Historique du % de produits critiques — alimente le mini chart de
tendance du risque (24-48h) sur le dashboard inventory.

Pourquoi une table de snapshots plutôt qu'une reconstruction on-demand
depuis supply.stock_movements (cf. options du guide d'implémentation) :

  - Le "% critique" affiché partout ailleurs sur le dashboard (KPI cards,
    donut de statut, alertes) vient de riskLevel, calculé par le pipeline
    d'analyse (lead time fournisseur + demande prévue + fenêtre de
    couverture) — PAS d'un seuil fixe genre stock_apres <= 3. Reconstruire
    depuis stock_movements avec un seuil arbitraire donnerait un chiffre
    incohérent avec le reste de l'écran (deux définitions différentes du
    mot "critique" sur la même page).
  - riskLevel n'existe qu'après un run de analyze_store() ; impossible à
    dériver en SQL pur sans réimplémenter le modèle de risque dans la
    requête.
  - analyze_store() est cher à froid (minutes sur ~150 SKUs) mais son
    résultat est déjà mis en cache et rafraîchi en continu par le reste du
    système (CronTrigger, sale triggers). Le job de snapshot
    (critical_trend_snapshot.snapshot_loop, démarré dans main.py) lit ce
    cache toutes les heures — il ne recalcule jamais rien, donc jamais de
    surcoût pipeline dû au chart lui-même.

Résultat : lecture du chart = simple SELECT sur une table indexée
(store_id, snapshot_time), résolution fixe (1 pt/heure) mais définition de
"critique" garantie identique à tout le reste du dashboard, et données
disponibles même si l'historique des mouvements est partiel ou purgé.

Revision ID: 0017
Revises: 0016
"""
from alembic import op

revision = "0017"
down_revision = "0016"  # À ajuster si une révision plus récente existe déjà
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS inventory.critical_trend_history (
            id             BIGSERIAL PRIMARY KEY,
            store_id       TEXT NOT NULL,
            snapshot_time  TIMESTAMP NOT NULL DEFAULT NOW(),
            total_skus     INTEGER NOT NULL,
            critical_count INTEGER NOT NULL,
            high_count     INTEGER NOT NULL DEFAULT 0,
            critical_pct   NUMERIC(5,2) NOT NULL,
            created_at     TIMESTAMP NOT NULL DEFAULT NOW()
        );

        COMMENT ON TABLE inventory.critical_trend_history IS
            'Snapshot horaire du % de SKUs en riskLevel=critical par magasin. '
            'Peuplé par critical_trend_snapshot.snapshot_loop() depuis le cache '
            'analyze_store() — jamais recalculé à la lecture. Alimente le mini '
            'chart "Tendance du risque (24-48h)" du dashboard inventory.';
        COMMENT ON COLUMN inventory.critical_trend_history.critical_pct IS
            'ROUND(critical_count / total_skus * 100, 2) au moment du snapshot';

        -- Une lecture par (store_id, fenêtre de temps), tri chronologique :
        -- index composite couvrant directement la requête du endpoint.
        CREATE INDEX IF NOT EXISTS idx_cth_store_time
            ON inventory.critical_trend_history (store_id, snapshot_time DESC);

        -- Purge : au-delà de 14 jours d'historique horaire (~336 lignes/magasin),
        -- plus aucune fenêtre du frontend (max 14j) ne les lit. Pas de job dédié
        -- pour l'instant — la table reste petite (quelques centaines de lignes
        -- par magasin) ; à revisiter si le nombre de magasins actifs explose.
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS inventory.critical_trend_history;
    """)
