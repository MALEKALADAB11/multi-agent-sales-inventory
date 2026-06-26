"""inv schema — initial tables

Revision ID: 001
Revises:
Create Date: 2025-01-01
"""
from alembic import op

revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:

    op.execute('CREATE SCHEMA IF NOT EXISTS inv')
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # ── inventory.stores ────────────────────────────────────────────────────────────
    # Needed only as an anchor for store_id FK columns in other tables.
    # Populated from sales_history.csv (store_id, store_name, region columns).
    op.execute("""
        CREATE TABLE inventory.stores (
            store_id    VARCHAR(50)  PRIMARY KEY,
            store_name  VARCHAR(200) NOT NULL,
            region      VARCHAR(100),
            active      BOOLEAN      DEFAULT TRUE,
            created_at  TIMESTAMP    DEFAULT NOW(),
            updated_at  TIMESTAMP    DEFAULT NOW()
        )
    """)

    # ── inventory.products ──────────────────────────────────────────────────────────
    # Populated once from product_master.csv.
    # Exists in DB so agents read one source instead of loading CSV each time.
    # Every other table FKs here on sku.
    op.execute("""
        CREATE TABLE inventory.products (
            sku               VARCHAR(50)    PRIMARY KEY,
            product_name      VARCHAR(200)   NOT NULL,
            category          VARCHAR(100),
            unit_cost         NUMERIC(12, 4),
            unit_price        NUMERIC(12, 4),
            lead_time_days    INTEGER,
            lead_time_std     NUMERIC(8, 4),
            moq               INTEGER,
            holding_cost_pct  NUMERIC(8, 6),
            order_cost        NUMERIC(12, 4),
            lifecycle_stage   VARCHAR(20)
                CHECK (lifecycle_stage IN
                    ('growth', 'mature', 'decline', 'discontinued')),
            created_at        TIMESTAMP      DEFAULT NOW(),
            updated_at        TIMESTAMP      DEFAULT NOW()
        )
    """)

    # ── inventory.stock_levels ──────────────────────────────────────────────────────
    # Live state — one row per (sku, store_id).
    # Initialized from the most recent row in stock_history.csv.
    # After that, updated by the stock_simulator when sales happen,
    # reorders are approved, or stock is received.
    op.execute("""
        CREATE TABLE inventory.stock_levels (
            sku                     VARCHAR(50)   NOT NULL
                REFERENCES inventory.products(sku),
            store_id                VARCHAR(50)   NOT NULL
                REFERENCES inventory.stores(store_id),
            stock_current           INTEGER       NOT NULL DEFAULT 0,
            stock_in_transit        INTEGER       DEFAULT 0,
            stock_min               INTEGER,
            stock_max               INTEGER,
            remaining_days_of_stock NUMERIC(8, 2),
            last_received_at        TIMESTAMP,
            last_updated            TIMESTAMP     DEFAULT NOW(),
            PRIMARY KEY (sku, store_id)
        )
    """)
    op.execute("CREATE INDEX idx_stock_levels_store ON inventory.stock_levels(store_id)")
    op.execute(
        "CREATE INDEX idx_stock_levels_low "
        "ON inventory.stock_levels(store_id, sku) "
        "WHERE stock_current <= stock_min"
    )

    # ── inventory.demand_forecast ───────────────────────────────────────────────────
    # Output of the TimesFM pipeline — one row per (sku, store_id, date).
    # Keeping history here lets you detect model drift later.
    op.execute("""
        CREATE TABLE inventory.demand_forecast (
            id              SERIAL        PRIMARY KEY,
            sku             VARCHAR(50)   NOT NULL
                REFERENCES inventory.products(sku),
            store_id        VARCHAR(50)   NOT NULL
                REFERENCES inventory.stores(store_id),
            forecast_date   DATE          NOT NULL,
            demand_24h      NUMERIC(12, 4) NOT NULL,
            confidence_low  NUMERIC(12, 4),
            confidence_high NUMERIC(12, 4),
            model_version   VARCHAR(50),
            created_at      TIMESTAMP     DEFAULT NOW(),
            UNIQUE (sku, store_id, forecast_date)
        )
    """)
    op.execute("CREATE INDEX idx_forecast_date ON inventory.demand_forecast(forecast_date)")
    op.execute("CREATE INDEX idx_forecast_sku ON inventory.demand_forecast(sku, store_id)")

    # ── inventory.agent_runs ────────────────────────────────────────────────────────
    # One row per agent execution. Defined before alerts/recommendations
    # because they FK here.
    op.execute("""
        CREATE TABLE inventory.agent_runs (
            id                        UUID         PRIMARY KEY
                DEFAULT uuid_generate_v4(),
            agent_name                VARCHAR(50)  NOT NULL
                CHECK (agent_name IN (
                    'analysis_agent', 'context_agent', 'decision_agent'
                )),
            store_id                  VARCHAR(50)
                REFERENCES inventory.stores(store_id),
            started_at                TIMESTAMP    DEFAULT NOW(),
            completed_at              TIMESTAMP,
            status                    VARCHAR(20)  DEFAULT 'running'
                CHECK (status IN ('running', 'completed', 'failed')),
            error_message             TEXT,
            items_processed           INTEGER      DEFAULT 0,
            alerts_generated          INTEGER      DEFAULT 0,
            recommendations_generated INTEGER      DEFAULT 0,
            created_at                TIMESTAMP    DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX idx_agent_runs_status "
        "ON inventory.agent_runs(agent_name, status)"
    )

    # ── inventory.alerts ────────────────────────────────────────────────────────────
    # Every stockout risk or overstock condition the system detects.
    # estimated_stockout_date = model prediction.
    # actual_stockout_date + was_accurate = filled retroactively.
    op.execute("""
        CREATE TABLE inventory.alerts (
            id                      UUID         PRIMARY KEY
                DEFAULT uuid_generate_v4(),
            sku                     VARCHAR(50)  NOT NULL
                REFERENCES inventory.products(sku),
            store_id                VARCHAR(50)  NOT NULL
                REFERENCES inventory.stores(store_id),
            alert_type              VARCHAR(30)  NOT NULL
                CHECK (alert_type IN (
                    'stockout_critical', 'stockout_risk',
                    'overstock', 'slow_moving'
                )),
            severity                VARCHAR(10)  NOT NULL
                CHECK (severity IN ('critical', 'high', 'medium', 'low')),
            recommended_action      TEXT,
            status                  VARCHAR(20)  DEFAULT 'pending'
                CHECK (status IN (
                    'pending', 'validated', 'dismissed', 'resolved'
                )),
            triggered_at            TIMESTAMP    DEFAULT NOW(),
            resolved_at             TIMESTAMP,
            estimated_stockout_date DATE,
            actual_stockout_date    DATE,
            was_accurate            BOOLEAN,
            created_at              TIMESTAMP    DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX idx_alerts_pending "
        "ON inventory.alerts(store_id, severity) WHERE status = 'pending'"
    )
    op.execute("CREATE INDEX idx_alerts_store_sku ON inventory.alerts(store_id, sku)")

    # ── inventory.recommendations ───────────────────────────────────────────────────
    # Decision agent output — what was suggested and what the user did with it.
    op.execute("""
        CREATE TABLE inventory.recommendations (
            id                  UUID         PRIMARY KEY
                DEFAULT uuid_generate_v4(),
            sku                 VARCHAR(50)  NOT NULL
                REFERENCES inventory.products(sku),
            store_id            VARCHAR(50)  NOT NULL
                REFERENCES inventory.stores(store_id),
            agent_run_id        UUID
                REFERENCES inventory.agent_runs(id),
            recommendation_type VARCHAR(20)  NOT NULL
                CHECK (recommendation_type IN (
                    'reorder', 'transfer', 'promotion', 'markdown'
                )),
            recommendation_text TEXT,
            suggested_quantity  INTEGER,
            confidence          NUMERIC(5, 4),
            status              VARCHAR(20)  DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected')),
            decided_by          VARCHAR(100),
            decided_at          TIMESTAMP,
            created_at          TIMESTAMP    DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX idx_reco_pending "
        "ON inventory.recommendations(store_id) WHERE status = 'pending'"
    )

    # ── inventory.promotions ────────────────────────────────────────────────────────
    # Populated from promotions.csv.
    # Context agent reads this to adjust demand upward before forecast.
    # sku nullable — a promo can apply to a whole category.
    op.execute("""
        CREATE TABLE inventory.promotions (
            promo_id     VARCHAR(50)   PRIMARY KEY,
            promo_name   VARCHAR(200)  NOT NULL,
            promo_type   VARCHAR(100),
            start_date   DATE          NOT NULL,
            end_date     DATE          NOT NULL,
            sku          VARCHAR(50),
            category     VARCHAR(100),
            discount_pct NUMERIC(6, 2),
            scope        VARCHAR(20)
                CHECK (scope IN ('national', 'regional', 'store')),
            is_active    BOOLEAN       DEFAULT FALSE,
            created_at   TIMESTAMP     DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_promo_dates ON inventory.promotions(start_date, end_date)")
    op.execute(
        "CREATE INDEX idx_promo_active ON inventory.promotions(is_active) "
        "WHERE is_active = TRUE"
    )

    # ── inventory.events ────────────────────────────────────────────────────────────
    # External demand events: Ramadan, back-to-school, etc.
    # Context agent reads this alongside promotions.
    # affected_categories stored as JSON array.
    op.execute("""
        CREATE TABLE inventory.events (
            event_id             UUID         PRIMARY KEY
                DEFAULT uuid_generate_v4(),
            event_name           VARCHAR(200) NOT NULL,
            event_type           VARCHAR(100),
            start_date           DATE         NOT NULL,
            end_date             DATE         NOT NULL,
            affected_categories  JSONB,
            estimated_uplift_pct NUMERIC(6, 2),
            scope                VARCHAR(20)
                CHECK (scope IN ('national', 'regional', 'store')),
            created_at           TIMESTAMP    DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_events_dates ON inventory.events(start_date, end_date)")

    # ── inventory.business_objectives ───────────────────────────────────────────────
    # Decision agent reads this to know current priority.
    # For V1 insert rows manually via seed_static_data.py.
    op.execute("""
        CREATE TABLE inventory.business_objectives (
            id             UUID         PRIMARY KEY
                DEFAULT uuid_generate_v4(),
            objective_type VARCHAR(30)  NOT NULL
                CHECK (objective_type IN (
                    'minimize_cost', 'maximize_service_level',
                    'clear_stock', 'prioritize_margin'
                )),
            priority       INTEGER      DEFAULT 1,
            target_value   NUMERIC(12, 4),
            applies_to     VARCHAR(10)
                CHECK (applies_to IN ('all', 'category', 'sku')),
            category       VARCHAR(100),
            sku            VARCHAR(50),
            start_date     DATE,
            end_date       DATE,
            is_active      BOOLEAN      DEFAULT TRUE,
            created_at     TIMESTAMP    DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS inv CASCADE")
