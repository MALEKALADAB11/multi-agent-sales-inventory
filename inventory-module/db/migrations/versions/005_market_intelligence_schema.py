"""Telco market intelligence schema — events, seasonality, competitors, MNP, network.

Revision ID: 005
Revises: 004
Create Date: 2026-06-23
"""
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:

    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute('CREATE SCHEMA IF NOT EXISTS market')
    op.execute('CREATE SCHEMA IF NOT EXISTS customer')

    # ── Enrich sales.boutiques ────────────────────────────────────────────────
    # Add geographic precision, store type, and operational metadata.
    op.execute("""
        ALTER TABLE sales.boutiques
        ADD COLUMN IF NOT EXISTS type_boutique  VARCHAR(5),
        ADD COLUMN IF NOT EXISTS canal          VARCHAR(20) DEFAULT 'PHYSIQUE',
        ADD COLUMN IF NOT EXISTS wilaya         VARCHAR(100),
        ADD COLUMN IF NOT EXISTS zone_commerciale VARCHAR(100),
        ADD COLUMN IF NOT EXISTS latitude       NUMERIC(10, 7),
        ADD COLUMN IF NOT EXISTS longitude      NUMERIC(10, 7),
        ADD COLUMN IF NOT EXISTS capacite_conseillers INTEGER DEFAULT 4,
        ADD COLUMN IF NOT EXISTS date_ouverture DATE,
        ADD COLUMN IF NOT EXISTS is_officielle  BOOLEAN DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS rang_ca_region INTEGER,
        ADD COLUMN IF NOT EXISTS email          VARCHAR(200)
    """)

    # ── Enrich sales.produits ─────────────────────────────────────────────────
    # Critical for analysis agent: margin, specs, flags.
    op.execute("""
        ALTER TABLE sales.produits
        ADD COLUMN IF NOT EXISTS marque         VARCHAR(100),
        ADD COLUMN IF NOT EXISTS modele         VARCHAR(200),
        ADD COLUMN IF NOT EXISTS gamme_libelle  VARCHAR(100),
        ADD COLUMN IF NOT EXISTS famille_libelle VARCHAR(100),
        ADD COLUMN IF NOT EXISTS pa_ht          NUMERIC(12, 4),
        ADD COLUMN IF NOT EXISTS marge_pct_calc NUMERIC(6, 2),
        ADD COLUMN IF NOT EXISTS flag_4g        BOOLEAN DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS flag_5g        BOOLEAN DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS flag_terminal  BOOLEAN DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS flag_forfait   BOOLEAN DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS flag_sim       BOOLEAN DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS flag_recharge  BOOLEAN DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS serialisable   BOOLEAN DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS stockable      BOOLEAN DEFAULT TRUE,
        ADD COLUMN IF NOT EXISTS lead_time_days INTEGER DEFAULT 14,
        ADD COLUMN IF NOT EXISTS lead_time_std  INTEGER DEFAULT 3,
        ADD COLUMN IF NOT EXISTS moq            INTEGER DEFAULT 1,
        ADD COLUMN IF NOT EXISTS holding_cost_pct NUMERIC(8, 6) DEFAULT 0.2,
        ADD COLUMN IF NOT EXISTS order_cost     NUMERIC(12, 4) DEFAULT 50,
        ADD COLUMN IF NOT EXISTS date_lancement DATE,
        ADD COLUMN IF NOT EXISTS date_eol       DATE,
        ADD COLUMN IF NOT EXISTS lifecycle_stage VARCHAR(20) DEFAULT 'mature',
        ADD COLUMN IF NOT EXISTS stockage_gb    INTEGER,
        ADD COLUMN IF NOT EXISTS ram_gb         INTEGER,
        ADD COLUMN IF NOT EXISTS couleur        VARCHAR(50)
    """)

    # ── Enrich sales.agents ───────────────────────────────────────────────────
    # Coaching agent needs performance history, certifications, quotas.
    op.execute("""
        ALTER TABLE sales.agents
        ADD COLUMN IF NOT EXISTS date_embauche      DATE,
        ADD COLUMN IF NOT EXISTS date_depart        DATE,
        ADD COLUMN IF NOT EXISTS niveau_certification INTEGER DEFAULT 1,
        ADD COLUMN IF NOT EXISTS quota_mensuel_ca   NUMERIC(12, 2),
        ADD COLUMN IF NOT EXISTS quota_activations  INTEGER DEFAULT 60,
        ADD COLUMN IF NOT EXISTS quota_postpaye     INTEGER DEFAULT 10,
        ADD COLUMN IF NOT EXISTS specialisation     VARCHAR(50),
        ADD COLUMN IF NOT EXISTS avatar_color       VARCHAR(7),
        ADD COLUMN IF NOT EXISTS coach_score        NUMERIC(5, 2) DEFAULT 0.0,
        ADD COLUMN IF NOT EXISTS anciennete_mois    INTEGER DEFAULT 12
    """)

    # ── market.events ─────────────────────────────────────────────────────────
    # Market calendar — Ramadan, Eid, Rentrée, CAN, soldes, etc.
    # Context agent reads this to compute demand_uplift_pct.
    op.execute("""
        CREATE TABLE market.events (
            event_id         UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
            event_name       VARCHAR(200) NOT NULL,
            event_type       VARCHAR(50)  NOT NULL
                CHECK (event_type IN (
                    'RELIGIEUX', 'SCOLAIRE', 'SPORTIF', 'COMMERCIAL',
                    'NATIONAL', 'CONCURRENTIEL', 'METEO', 'RESEAU'
                )),
            sous_type        VARCHAR(100),
            start_date       DATE         NOT NULL,
            end_date         DATE         NOT NULL,
            annee            INTEGER      GENERATED ALWAYS AS (EXTRACT(YEAR FROM start_date)::INTEGER) STORED,
            scope            VARCHAR(20)  DEFAULT 'NATIONAL'
                CHECK (scope IN ('NATIONAL', 'REGIONAL', 'LOCAL', 'GLOBAL')),
            region_ids       JSONB,
            categories_impactees JSONB,
            uplift_terminal  NUMERIC(6, 2) DEFAULT 0,
            uplift_forfait   NUMERIC(6, 2) DEFAULT 0,
            uplift_sim       NUMERIC(6, 2) DEFAULT 0,
            uplift_recharge  NUMERIC(6, 2) DEFAULT 0,
            uplift_accessoire NUMERIC(6, 2) DEFAULT 0,
            intensite        VARCHAR(10)  DEFAULT 'MEDIUM'
                CHECK (intensite IN ('LOW', 'MEDIUM', 'HIGH', 'EXTREME')),
            source_donnee    VARCHAR(50)  DEFAULT 'HISTORIQUE',
            note_strategie   TEXT,
            created_at       TIMESTAMP    DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_market_events_dates ON market.events(start_date, end_date)")
    op.execute("CREATE INDEX idx_market_events_type ON market.events(event_type)")

    # ── market.seasonal_patterns ──────────────────────────────────────────────
    # Historical demand multipliers — calibrated from 2+ years of sales.
    # Analysis agent uses these as baseline uplift before context adjustments.
    op.execute("""
        CREATE TABLE market.seasonal_patterns (
            id               SERIAL       PRIMARY KEY,
            categorie        VARCHAR(50)  NOT NULL,
            mois             INTEGER      NOT NULL CHECK (mois BETWEEN 1 AND 12),
            semaine_mois     INTEGER      CHECK (semaine_mois BETWEEN 1 AND 5),
            jour_semaine     INTEGER      CHECK (jour_semaine BETWEEN 0 AND 6),
            heure_debut      INTEGER      CHECK (heure_debut BETWEEN 0 AND 23),
            heure_fin        INTEGER      CHECK (heure_fin BETWEEN 0 AND 23),
            facteur_demande  NUMERIC(6, 4) NOT NULL DEFAULT 1.0,
            facteur_std      NUMERIC(6, 4) DEFAULT 0.1,
            nb_annees_data   INTEGER      DEFAULT 2,
            confidence       VARCHAR(10)  DEFAULT 'MEDIUM'
                CHECK (confidence IN ('LOW', 'MEDIUM', 'HIGH', 'VERY_HIGH')),
            notes            VARCHAR(200),
            updated_at       TIMESTAMP    DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX idx_seasonal_unique
        ON market.seasonal_patterns(categorie, mois, COALESCE(jour_semaine, -1))
    """)

    # ── market.competitors ────────────────────────────────────────────────────
    # Tunisian telecom competitors: Tunisie Telecom, Orange Tunisie.
    op.execute("""
        CREATE TABLE market.competitors (
            concurrent_id    VARCHAR(20)  PRIMARY KEY,
            nom              VARCHAR(100) NOT NULL,
            code_operateur   VARCHAR(10),
            pays             VARCHAR(50)  DEFAULT 'Tunisia',
            part_marche_pct  NUMERIC(6, 3),
            nb_abonnes       BIGINT,
            positionnement   VARCHAR(20)  DEFAULT 'MID'
                CHECK (positionnement IN ('PREMIUM', 'MID', 'LOW_COST', 'STATE')),
            points_forts     JSONB,
            points_faibles   JSONB,
            date_entree_marche DATE,
            actif            BOOLEAN      DEFAULT TRUE,
            updated_at       TIMESTAMP    DEFAULT NOW()
        )
    """)

    # ── market.competitor_pricing ─────────────────────────────────────────────
    # Weekly price intelligence — key for context agent competitive signals.
    op.execute("""
        CREATE TABLE market.competitor_pricing (
            id               SERIAL       PRIMARY KEY,
            concurrent_id    VARCHAR(20)  NOT NULL
                REFERENCES market.competitors(concurrent_id),
            categorie        VARCHAR(50)  NOT NULL,
            produit_type     VARCHAR(200) NOT NULL,
            donnees_go       NUMERIC(8, 1),
            minutes_voix     INTEGER,
            sms_count        INTEGER,
            prix_ht          NUMERIC(10, 2),
            prix_ttc         NUMERIC(10, 2),
            engagement_mois  INTEGER      DEFAULT 0,
            date_releve      DATE         NOT NULL,
            source           VARCHAR(30)  DEFAULT 'WEB'
                CHECK (source IN ('WEB', 'ENQUETE', 'ARCEP', 'VISITE_BOUTIQUE', 'CLIENT')),
            actif            BOOLEAN      DEFAULT TRUE,
            created_at       TIMESTAMP    DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_competitor_pricing_date ON market.competitor_pricing(date_releve)")
    op.execute("CREATE INDEX idx_competitor_pricing_cat ON market.competitor_pricing(categorie, concurrent_id)")

    # ── market.mnp_flows ──────────────────────────────────────────────────────
    # Mobile Number Portability — PORT_IN/PORT_OUT volumes monthly.
    # Context agent uses PORT_OUT spike as churn warning signal.
    op.execute("""
        CREATE TABLE market.mnp_flows (
            mnp_id           UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
            direction        VARCHAR(10)  NOT NULL
                CHECK (direction IN ('PORT_IN', 'PORT_OUT')),
            operateur_origine  VARCHAR(20),
            operateur_destination VARCHAR(20),
            mois             DATE         NOT NULL,
            volume           INTEGER      NOT NULL DEFAULT 0,
            categorie_client VARCHAR(20)  DEFAULT 'RESI'
                CHECK (categorie_client IN ('RESI', 'CORPO', 'PME')),
            raison_principale VARCHAR(100),
            wilaya           VARCHAR(100),
            created_at       TIMESTAMP    DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_mnp_mois ON market.mnp_flows(mois, direction)")

    # ── market.network_events ─────────────────────────────────────────────────
    # Network outages/upgrades that suppress sales. Context agent uses this.
    op.execute("""
        CREATE TABLE market.network_events (
            event_id         UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
            wilaya           VARCHAR(100),
            store_ids        JSONB,
            event_type       VARCHAR(30)  NOT NULL
                CHECK (event_type IN (
                    'PANNE', 'MAINTENANCE', 'UPGRADE_4G', 'UPGRADE_5G',
                    'CONGESTION', 'COUPURE_FIBRE', 'DEGRADATION'
                )),
            severite         VARCHAR(10)  DEFAULT 'MEDIUM'
                CHECK (severite IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
            start_time       TIMESTAMP    NOT NULL,
            end_time         TIMESTAMP,
            clients_impactes INTEGER,
            impact_ventes_pct NUMERIC(6, 2) DEFAULT 0,
            resolu           BOOLEAN      DEFAULT FALSE,
            notes            TEXT,
            created_at       TIMESTAMP    DEFAULT NOW()
        )
    """)

    # ── customer.segments ────────────────────────────────────────────────────
    # Customer segment definitions — used by coaching and decision agents.
    op.execute("""
        CREATE TABLE customer.segments (
            segment_id       VARCHAR(20)  PRIMARY KEY,
            libelle          VARCHAR(100) NOT NULL,
            description      TEXT,
            arpu_moyen_tnd   NUMERIC(10, 2),
            arpu_std_tnd     NUMERIC(10, 2),
            churn_rate_base  NUMERIC(6, 4),
            duree_vie_mois   INTEGER,
            canal_prefere    VARCHAR(20),
            products_preferes JSONB,
            criteres         JSONB,
            nb_clients_estime INTEGER,
            poids_marche_pct NUMERIC(6, 3),
            actif            BOOLEAN      DEFAULT TRUE
        )
    """)

    # ── customer.churn_signals ────────────────────────────────────────────────
    # Churn risk indicators per store (aggregated, not individual clients).
    op.execute("""
        CREATE TABLE customer.churn_signals (
            id               UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
            store_id         VARCHAR(50),
            signal_date      DATE         NOT NULL,
            nb_inactifs_30j  INTEGER      DEFAULT 0,
            nb_inactifs_60j  INTEGER      DEFAULT 0,
            nb_reclamations  INTEGER      DEFAULT 0,
            nb_port_out      INTEGER      DEFAULT 0,
            taux_renouvellement NUMERIC(6, 4),
            score_risque_churn NUMERIC(5, 2),
            facteurs_risque  JSONB,
            actions_retention JSONB,
            created_at       TIMESTAMP    DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_churn_store_date ON customer.churn_signals(store_id, signal_date)")

    # ── customer.nps_csat ─────────────────────────────────────────────────────
    # NPS and CSAT feedback — coaching agent uses to rank advisor quality.
    op.execute("""
        CREATE TABLE customer.nps_csat (
            id               SERIAL       PRIMARY KEY,
            store_id         VARCHAR(50),
            agent_id         INTEGER,
            feedback_date    DATE         NOT NULL,
            type_enquete     VARCHAR(10)  NOT NULL
                CHECK (type_enquete IN ('NPS', 'CSAT', 'CES')),
            score            NUMERIC(5, 2),
            verbatim         TEXT,
            categorie_motif  VARCHAR(100),
            canal            VARCHAR(20)  DEFAULT 'POST_VENTE',
            resolu           BOOLEAN      DEFAULT FALSE,
            created_at       TIMESTAMP    DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_nps_store_date ON customer.nps_csat(store_id, feedback_date)")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS customer CASCADE")
    op.execute("DROP SCHEMA IF EXISTS market CASCADE")

    op.execute("""
        ALTER TABLE sales.agents
        DROP COLUMN IF EXISTS date_embauche,
        DROP COLUMN IF EXISTS date_depart,
        DROP COLUMN IF EXISTS niveau_certification,
        DROP COLUMN IF EXISTS quota_mensuel_ca,
        DROP COLUMN IF EXISTS quota_activations,
        DROP COLUMN IF EXISTS quota_postpaye,
        DROP COLUMN IF EXISTS specialisation,
        DROP COLUMN IF EXISTS avatar_color,
        DROP COLUMN IF EXISTS coach_score,
        DROP COLUMN IF EXISTS anciennete_mois
    """)

    op.execute("""
        ALTER TABLE sales.produits
        DROP COLUMN IF EXISTS marque,
        DROP COLUMN IF EXISTS modele,
        DROP COLUMN IF EXISTS gamme_libelle,
        DROP COLUMN IF EXISTS famille_libelle,
        DROP COLUMN IF EXISTS pa_ht,
        DROP COLUMN IF EXISTS marge_pct_calc,
        DROP COLUMN IF EXISTS flag_4g,
        DROP COLUMN IF EXISTS flag_5g,
        DROP COLUMN IF EXISTS flag_terminal,
        DROP COLUMN IF EXISTS flag_forfait,
        DROP COLUMN IF EXISTS flag_sim,
        DROP COLUMN IF EXISTS flag_recharge,
        DROP COLUMN IF EXISTS serialisable,
        DROP COLUMN IF EXISTS stockable,
        DROP COLUMN IF EXISTS lead_time_days,
        DROP COLUMN IF EXISTS lead_time_std,
        DROP COLUMN IF EXISTS moq,
        DROP COLUMN IF EXISTS holding_cost_pct,
        DROP COLUMN IF EXISTS order_cost,
        DROP COLUMN IF EXISTS date_lancement,
        DROP COLUMN IF EXISTS date_eol,
        DROP COLUMN IF EXISTS lifecycle_stage,
        DROP COLUMN IF EXISTS stockage_gb,
        DROP COLUMN IF EXISTS ram_gb,
        DROP COLUMN IF EXISTS couleur
    """)

    op.execute("""
        ALTER TABLE sales.boutiques
        DROP COLUMN IF EXISTS type_boutique,
        DROP COLUMN IF EXISTS canal,
        DROP COLUMN IF EXISTS wilaya,
        DROP COLUMN IF EXISTS zone_commerciale,
        DROP COLUMN IF EXISTS latitude,
        DROP COLUMN IF EXISTS longitude,
        DROP COLUMN IF EXISTS capacite_conseillers,
        DROP COLUMN IF EXISTS date_ouverture,
        DROP COLUMN IF EXISTS is_officielle,
        DROP COLUMN IF EXISTS rang_ca_region,
        DROP COLUMN IF EXISTS email
    """)
