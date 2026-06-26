"""Supply chain schema — suppliers, purchase orders, stock movements, transfers, IMEI.

Revision ID: 006
Revises: 005
Create Date: 2026-06-23
"""
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:

    op.execute('CREATE SCHEMA IF NOT EXISTS supply')

    # ── supply.suppliers ─────────────────────────────────────────────────────
    # Device and SIM card suppliers. Decision agent checks lead time + reliability
    # before recommending reorder quantities.
    op.execute("""
        CREATE TABLE supply.suppliers (
            supplier_id          VARCHAR(30)  PRIMARY KEY,
            nom                  VARCHAR(200) NOT NULL,
            pays_origine         VARCHAR(50),
            type_fournisseur     VARCHAR(30)  NOT NULL
                CHECK (type_fournisseur IN (
                    'CONSTRUCTEUR', 'DISTRIBUTEUR_NATIONAL',
                    'DISTRIBUTEUR_REGIONAL', 'OPERATEUR_INTERNE', 'SIM_MANUFACTURER'
                )),
            categories           JSONB,
            marques              JSONB,
            delai_livraison_moy  INTEGER      DEFAULT 14,
            delai_livraison_std  INTEGER      DEFAULT 3,
            taux_fiabilite       NUMERIC(5, 4) DEFAULT 0.90,
            commande_min         INTEGER      DEFAULT 1,
            commande_multiple    INTEGER      DEFAULT 1,
            devise               VARCHAR(5)   DEFAULT 'TND',
            conditions_paiement  VARCHAR(100),
            contact_nom          VARCHAR(100),
            contact_email        VARCHAR(200),
            contact_phone        VARCHAR(30),
            actif                BOOLEAN      DEFAULT TRUE,
            score_global         NUMERIC(5, 2) DEFAULT 0.0,
            created_at           TIMESTAMP    DEFAULT NOW(),
            updated_at           TIMESTAMP    DEFAULT NOW()
        )
    """)

    # ── supply.purchase_orders ────────────────────────────────────────────────
    # Each reorder recommendation that becomes a real BC.
    # Decision agent writes here when action=ORDER is approved.
    op.execute("""
        CREATE TABLE supply.purchase_orders (
            po_id                UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
            sku                  INTEGER      NOT NULL,
            supplier_id          VARCHAR(30)
                REFERENCES supply.suppliers(supplier_id),
            store_id             VARCHAR(50),
            quantite_commandee   INTEGER      NOT NULL,
            quantite_recue       INTEGER      DEFAULT 0,
            prix_unitaire_ht     NUMERIC(12, 4),
            montant_total_ht     NUMERIC(14, 4),
            devise               VARCHAR(5)   DEFAULT 'TND',
            statut               VARCHAR(20)  DEFAULT 'BROUILLON'
                CHECK (statut IN (
                    'BROUILLON', 'SOUMIS', 'CONFIRME', 'EXPEDIE',
                    'RECU_PARTIEL', 'RECU', 'ANNULE', 'LITIGE'
                )),
            priorite             VARCHAR(10)  DEFAULT 'NORMAL'
                CHECK (priorite IN ('URGENTE', 'HAUTE', 'NORMAL', 'BASSE')),
            date_commande        TIMESTAMP    DEFAULT NOW(),
            date_livraison_prevue DATE,
            date_livraison_reelle DATE,
            delai_reel_jours     INTEGER,
            livraison_conforme   BOOLEAN,
            reference_externe    VARCHAR(100),
            agent_decision_id    UUID,
            recommendation_id    UUID,
            notes                TEXT,
            created_at           TIMESTAMP    DEFAULT NOW(),
            updated_at           TIMESTAMP    DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_po_sku ON supply.purchase_orders(sku, statut)")
    op.execute("CREATE INDEX idx_po_store ON supply.purchase_orders(store_id, statut)")
    op.execute("CREATE INDEX idx_po_date ON supply.purchase_orders(date_livraison_prevue) WHERE statut NOT IN ('RECU','ANNULE')")

    # ── supply.stock_movements ────────────────────────────────────────────────
    # Complete audit trail of every stock change.
    # Analysis agent reads recent movements to detect velocity changes.
    op.execute("""
        CREATE TABLE supply.stock_movements (
            mouvement_id     UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
            sku              INTEGER      NOT NULL,
            store_id         VARCHAR(50)  NOT NULL,
            type_mouvement   VARCHAR(30)  NOT NULL
                CHECK (type_mouvement IN (
                    'RECEPTION_BC', 'VENTE', 'RETOUR_CLIENT', 'RETOUR_FOURNISSEUR',
                    'TRANSFERT_ENTRANT', 'TRANSFERT_SORTANT',
                    'AJUSTEMENT_INVENTAIRE', 'CASSE_PERTE',
                    'INVENTAIRE_GAIN', 'INVENTAIRE_PERTE',
                    'RESERVATION', 'LIBERATION_RESERVATION'
                )),
            quantite         INTEGER      NOT NULL,
            stock_avant      INTEGER,
            stock_apres      INTEGER,
            reference_id     VARCHAR(100),
            reference_type   VARCHAR(30)
                CHECK (reference_type IN (
                    'VENTE', 'BC', 'TRANSFERT', 'INVENTAIRE',
                    'RETOUR', 'AJUSTEMENT', NULL
                )),
            agent_id         INTEGER,
            date_mouvement   TIMESTAMP    DEFAULT NOW(),
            notes            TEXT,
            created_at       TIMESTAMP    DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_mvt_sku_store ON supply.stock_movements(sku, store_id, date_mouvement DESC)")
    op.execute("CREATE INDEX idx_mvt_store_date ON supply.stock_movements(store_id, date_mouvement DESC)")
    op.execute("CREATE INDEX idx_mvt_type ON supply.stock_movements(type_mouvement, date_mouvement DESC)")

    # ── supply.transfers ──────────────────────────────────────────────────────
    # Inter-store stock transfers. Decision agent recommends these to rebalance
    # stock between overstock and understock stores.
    op.execute("""
        CREATE TABLE supply.transfers (
            transfer_id      UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
            sku              INTEGER      NOT NULL,
            store_source     VARCHAR(50)  NOT NULL,
            store_dest       VARCHAR(50)  NOT NULL,
            quantite         INTEGER      NOT NULL,
            statut           VARCHAR(20)  DEFAULT 'DEMANDE'
                CHECK (statut IN (
                    'DEMANDE', 'APPROUVE', 'EXPEDIE', 'RECU',
                    'REJETE', 'ANNULE', 'EN_LITIGE'
                )),
            priorite         VARCHAR(10)  DEFAULT 'NORMAL'
                CHECK (priorite IN ('URGENTE', 'HAUTE', 'NORMAL', 'BASSE')),
            motif            VARCHAR(100)
                CHECK (motif IN (
                    'RUPTURE_URGENTE', 'REEQUILIBRAGE_STOCK',
                    'COMMANDE_CLIENT', 'SURSTOCK_SOURCE',
                    'PROMOTION_LOCALE', 'FIN_DE_VIE'
                )),
            demandeur_agent  INTEGER,
            approbateur_agent INTEGER,
            date_demande     TIMESTAMP    DEFAULT NOW(),
            date_approbation TIMESTAMP,
            date_expedition  TIMESTAMP,
            date_reception   TIMESTAMP,
            agent_decision_id UUID,
            recommendation_id UUID,
            notes            TEXT
        )
    """)
    op.execute("CREATE INDEX idx_transfer_sku ON supply.transfers(sku, statut)")
    op.execute("CREATE INDEX idx_transfer_stores ON supply.transfers(store_source, store_dest)")

    # ── supply.serial_numbers ────────────────────────────────────────────────
    # IMEI (terminals) + ICCID (SIM cards) tracking.
    # Needed for warranty, fraud detection, and accurate stock counts.
    op.execute("""
        CREATE TABLE supply.serial_numbers (
            id               UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
            num_serie        VARCHAR(50)  UNIQUE NOT NULL,
            type_serie       VARCHAR(10)  NOT NULL
                CHECK (type_serie IN ('IMEI', 'ICCID', 'ESIM', 'EAN')),
            sku              INTEGER,
            store_id         VARCHAR(50),
            po_id            UUID
                REFERENCES supply.purchase_orders(po_id),
            statut           VARCHAR(20)  DEFAULT 'EN_STOCK'
                CHECK (statut IN (
                    'EN_STOCK', 'VENDU', 'RESERVE',
                    'DEFECTUEUX', 'RETOURNE', 'VOLE', 'EN_TRANSIT'
                )),
            date_reception   DATE,
            date_vente       DATE,
            sale_id          VARCHAR(100),
            num_client       VARCHAR(30),
            notes            TEXT,
            created_at       TIMESTAMP    DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_serial_sku_store ON supply.serial_numbers(sku, store_id, statut)")
    op.execute("CREATE INDEX idx_serial_status ON supply.serial_numbers(statut) WHERE statut='EN_STOCK'")

    # ── supply.forecasts ──────────────────────────────────────────────────────
    # Extended demand forecasts. Replaces/supplements inventory.demand_forecast
    # with richer context factors from the context agent.
    op.execute("""
        CREATE TABLE supply.forecasts (
            id               UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
            sku              INTEGER      NOT NULL,
            store_id         VARCHAR(50)  NOT NULL,
            date_prevision   DATE         NOT NULL,
            horizon_jours    INTEGER      DEFAULT 30,
            demande_j1       NUMERIC(10, 3),
            demande_j7       NUMERIC(10, 3),
            demande_j30      NUMERIC(10, 3),
            ic_bas_j30       NUMERIC(10, 3),
            ic_haut_j30      NUMERIC(10, 3),
            facteur_saisonnier NUMERIC(6, 4) DEFAULT 1.0,
            facteur_evenement  NUMERIC(6, 4) DEFAULT 1.0,
            facteur_promo      NUMERIC(6, 4) DEFAULT 1.0,
            facteur_concurrence NUMERIC(6, 4) DEFAULT 1.0,
            demande_corrigee_j30 NUMERIC(10, 3),
            modele_version   VARCHAR(50)  DEFAULT 'timesfm-v1',
            mae              NUMERIC(10, 4),
            rmse             NUMERIC(10, 4),
            mape             NUMERIC(6, 4),
            run_by_agent     VARCHAR(50),
            created_at       TIMESTAMP    DEFAULT NOW(),
            UNIQUE (sku, store_id, date_prevision, modele_version)
        )
    """)
    op.execute("CREATE INDEX idx_forecast_sku_store ON supply.forecasts(sku, store_id, date_prevision DESC)")

    # ── supply.reorder_suggestions ───────────────────────────────────────────
    # Pre-computed reorder parameters for each SKU/store.
    # Decision agent reads this instead of recomputing EOQ every run.
    op.execute("""
        CREATE TABLE supply.reorder_params (
            sku              INTEGER      NOT NULL,
            store_id         VARCHAR(50)  NOT NULL,
            demande_moy_jour NUMERIC(10, 4),
            demande_std_jour NUMERIC(10, 4),
            lead_time_moy    NUMERIC(8, 2),
            lead_time_std    NUMERIC(8, 2),
            stock_securite   INTEGER,
            point_commande   INTEGER,
            eoq              INTEGER,
            niveau_service   NUMERIC(5, 4) DEFAULT 0.95,
            jours_stock_cible INTEGER     DEFAULT 30,
            derniere_maj     TIMESTAMP    DEFAULT NOW(),
            PRIMARY KEY (sku, store_id)
        )
    """)


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS supply CASCADE")
