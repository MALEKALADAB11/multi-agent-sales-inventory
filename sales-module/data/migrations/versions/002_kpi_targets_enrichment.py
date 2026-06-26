"""KPI daily snapshots + telco-specific monthly targets for coaching agents.

Revision ID: 002kpi
Revises: 001initial
Create Date: 2026-06-23
"""
from alembic import op
import sqlalchemy as sa

revision = "002kpi"
down_revision = "001initial"
branch_labels = None
depends_on = None


def upgrade() -> None:

    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # ── agent_kpi_daily ───────────────────────────────────────────────────────
    # One row per advisor per day. Coaching agent reads this to compute gap%,
    # urgency score, and identify what kind of advice to give.
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_kpi_daily (
            id                   BIGSERIAL    PRIMARY KEY,
            agent_id             INTEGER      NOT NULL,
            store_id             VARCHAR(50)  NOT NULL,
            kpi_date             DATE         NOT NULL,

            -- Revenue
            ca_realise           NUMERIC(14, 2) DEFAULT 0,
            ca_cible             NUMERIC(14, 2),
            gap_ca_pct           NUMERIC(7, 2),

            -- Volume transactions
            nb_transactions      INTEGER      DEFAULT 0,
            nb_clients_uniques   INTEGER      DEFAULT 0,
            panier_moyen         NUMERIC(10, 2),

            -- Product mix (telco KPIs)
            nb_forfaits          INTEGER      DEFAULT 0,
            nb_terminaux         INTEGER      DEFAULT 0,
            nb_sim_activations   INTEGER      DEFAULT 0,
            nb_recharges         INTEGER      DEFAULT 0,
            nb_accessoires       INTEGER      DEFAULT 0,
            nb_evouchers         INTEGER      DEFAULT 0,

            -- Postpaid (key telco KPI)
            nb_postpaye          INTEGER      DEFAULT 0,
            nb_postpaye_cible    INTEGER,
            gap_postpaye_pct     NUMERIC(7, 2),

            -- Upsell metrics
            taux_upsell_accessoire NUMERIC(6, 4),
            taux_upsell_assurance  NUMERIC(6, 4),
            taux_conversion_recharge NUMERIC(6, 4),

            -- Revenue by category
            ca_terminaux         NUMERIC(12, 2) DEFAULT 0,
            ca_forfaits          NUMERIC(12, 2) DEFAULT 0,
            ca_sim               NUMERIC(12, 2) DEFAULT 0,
            ca_recharges         NUMERIC(12, 2) DEFAULT 0,
            ca_accessoires       NUMERIC(12, 2) DEFAULT 0,

            -- Rankings (computed daily by batch)
            rang_boutique        INTEGER,
            rang_region          INTEGER,
            rang_national        INTEGER,

            -- Quality
            nb_reclamations      INTEGER      DEFAULT 0,
            nps_score            NUMERIC(5, 2),

            -- AI score
            coach_score          NUMERIC(5, 2),
            urgency_level        VARCHAR(10)
                CHECK (urgency_level IN ('CRITIQUE', 'ELEVE', 'MODERE', 'OK')),

            created_at           TIMESTAMP    DEFAULT NOW(),
            UNIQUE (agent_id, kpi_date)
        )
    """)
    op.execute("CREATE INDEX idx_agent_kpi_store_date ON agent_kpi_daily(store_id, kpi_date DESC)")
    op.execute("CREATE INDEX idx_agent_kpi_agent_date ON agent_kpi_daily(agent_id, kpi_date DESC)")
    op.execute("CREATE INDEX idx_agent_kpi_gap ON agent_kpi_daily(kpi_date, gap_ca_pct) WHERE gap_ca_pct < -15")

    # ── store_kpi_daily ───────────────────────────────────────────────────────
    # One row per boutique per day. Strategist agent uses these for store-level
    # context and trend analysis.
    op.execute("""
        CREATE TABLE IF NOT EXISTS store_kpi_daily (
            id                   BIGSERIAL    PRIMARY KEY,
            store_id             VARCHAR(50)  NOT NULL,
            kpi_date             DATE         NOT NULL,

            -- Revenue
            ca_realise           NUMERIC(14, 2) DEFAULT 0,
            ca_cible             NUMERIC(14, 2),
            gap_ca_pct           NUMERIC(7, 2),
            ca_cumul_mois        NUMERIC(16, 2),
            ca_objectif_mois     NUMERIC(16, 2),

            -- Volume
            nb_transactions      INTEGER      DEFAULT 0,
            nb_clients           INTEGER      DEFAULT 0,
            taux_conversion      NUMERIC(6, 4),
            footfall_estime      INTEGER,

            -- Product mix
            nb_forfaits          INTEGER      DEFAULT 0,
            nb_terminaux         INTEGER      DEFAULT 0,
            nb_sim_activations   INTEGER      DEFAULT 0,
            nb_recharges         INTEGER      DEFAULT 0,
            ca_terminaux         NUMERIC(14, 2) DEFAULT 0,
            ca_forfaits          NUMERIC(14, 2) DEFAULT 0,

            -- Postpaid
            nb_postpaye          INTEGER      DEFAULT 0,
            nb_postpaye_cible    INTEGER,
            gap_postpaye_pct     NUMERIC(7, 2),

            -- Quality
            nps_score            NUMERIC(5, 2),
            csat_score           NUMERIC(5, 2),
            nb_reclamations      INTEGER      DEFAULT 0,

            -- Stock health
            nb_ruptures_sku      INTEGER      DEFAULT 0,
            taux_service_stock   NUMERIC(6, 4) DEFAULT 1.0,

            -- Rankings
            rang_region          INTEGER,
            rang_national        INTEGER,

            -- Agent stats
            nb_agents_actifs     INTEGER,
            ca_par_agent         NUMERIC(12, 2),

            created_at           TIMESTAMP    DEFAULT NOW(),
            UNIQUE (store_id, kpi_date)
        )
    """)
    op.execute("CREATE INDEX idx_store_kpi_date ON store_kpi_daily(kpi_date DESC)")
    op.execute("CREATE INDEX idx_store_kpi_gap ON store_kpi_daily(kpi_date, gap_ca_pct) WHERE gap_ca_pct < -10")

    # ── telco_targets_monthly ─────────────────────────────────────────────────
    # Monthly objectives split by week + telco-specific KPIs (activations,
    # postpaid targets). Both store-level and advisor-level rows.
    op.execute("""
        CREATE TABLE IF NOT EXISTS telco_targets_monthly (
            id                       BIGSERIAL   PRIMARY KEY,
            store_id                 VARCHAR(50) NOT NULL,
            agent_id                 INTEGER,
            mois                     INTEGER     NOT NULL CHECK (mois BETWEEN 1 AND 12),
            annee                    INTEGER     NOT NULL,
            niveau                   VARCHAR(10) DEFAULT 'AGENT'
                CHECK (niveau IN ('AGENT', 'BOUTIQUE', 'REGION')),

            -- Revenue targets
            ca_cible_mensuel         NUMERIC(14, 2),
            ca_cible_s1              NUMERIC(12, 2),
            ca_cible_s2              NUMERIC(12, 2),
            ca_cible_s3              NUMERIC(12, 2),
            ca_cible_s4              NUMERIC(12, 2),

            -- Telco-specific targets
            activations_totales      INTEGER     DEFAULT 0,
            activations_postpaye     INTEGER     DEFAULT 0,
            activations_prepaye      INTEGER     DEFAULT 0,
            ventes_terminaux         INTEGER     DEFAULT 0,
            upgrades_data            INTEGER     DEFAULT 0,
            conversions_recharge_forfait INTEGER  DEFAULT 0,
            renouvellements_contrat  INTEGER     DEFAULT 0,

            -- Quality targets
            nps_cible                NUMERIC(5, 2),
            taux_reclamation_max     NUMERIC(6, 4),

            -- Context (what drove the target level)
            evenements_mois          JSONB,
            facteur_saisonnier       NUMERIC(6, 4) DEFAULT 1.0,
            ajustement_raison        TEXT,

            created_at               TIMESTAMP   DEFAULT NOW(),
            UNIQUE (store_id, COALESCE(agent_id::text, 'NULL'), mois, annee)
        )
    """)
    op.execute("CREATE INDEX idx_targets_store_month ON telco_targets_monthly(store_id, annee, mois)")
    op.execute("CREATE INDEX idx_targets_agent_month ON telco_targets_monthly(agent_id, annee, mois) WHERE agent_id IS NOT NULL")

    # ── weekly_kpi_summary ────────────────────────────────────────────────────
    # Pre-aggregated weekly view for the coaching strategy agent.
    # Avoids expensive real-time aggregation on 1.9M rows.
    op.execute("""
        CREATE TABLE IF NOT EXISTS weekly_kpi_summary (
            id                   BIGSERIAL    PRIMARY KEY,
            store_id             VARCHAR(50)  NOT NULL,
            agent_id             INTEGER,
            annee_semaine        VARCHAR(8)   NOT NULL,
            semaine_debut        DATE         NOT NULL,
            semaine_fin          DATE         NOT NULL,
            niveau               VARCHAR(10)  DEFAULT 'AGENT'
                CHECK (niveau IN ('AGENT', 'BOUTIQUE')),

            ca_semaine           NUMERIC(14, 2) DEFAULT 0,
            ca_cible_semaine     NUMERIC(14, 2),
            gap_semaine_pct      NUMERIC(7, 2),
            nb_transactions      INTEGER      DEFAULT 0,
            nb_postpaye          INTEGER      DEFAULT 0,
            nb_terminaux         INTEGER      DEFAULT 0,
            nb_forfaits          INTEGER      DEFAULT 0,
            panier_moyen         NUMERIC(10, 2),
            top_produit          VARCHAR(200),
            top_categorie        VARCHAR(50),
            nb_jours_actifs      INTEGER      DEFAULT 6,

            created_at           TIMESTAMP    DEFAULT NOW(),
            UNIQUE (store_id, COALESCE(agent_id::text, 'NULL'), annee_semaine)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS weekly_kpi_summary")
    op.execute("DROP TABLE IF EXISTS telco_targets_monthly")
    op.execute("DROP TABLE IF EXISTS store_kpi_daily")
    op.execute("DROP TABLE IF EXISTS agent_kpi_daily")
