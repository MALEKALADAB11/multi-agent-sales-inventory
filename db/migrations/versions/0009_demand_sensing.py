"""Add baseline/corrected forecast columns + forecast_accuracy table.

Backward-compatible: existing demand_24h/confidence_low/confidence_high/
model_version columns on inventory.demand_forecast are untouched.

Revision ID: 0009
Revises: 0008
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE inventory.demand_forecast
          ADD COLUMN baseline_demand         NUMERIC,
          ADD COLUMN corrected_demand        NUMERIC,
          ADD COLUMN correction_method       TEXT,
          ADD COLUMN correction_features     JSONB,
          ADD COLUMN baseline_generated_at   TIMESTAMPTZ,
          ADD COLUMN corrected_generated_at  TIMESTAMPTZ;

        CREATE TABLE inventory.forecast_accuracy (
            id              SERIAL PRIMARY KEY,
            sku             INTEGER NOT NULL,
            store_id        TEXT NOT NULL,
            forecast_date   DATE NOT NULL,
            baseline_demand NUMERIC,
            corrected_demand NUMERIC,
            actual_demand   NUMERIC,
            baseline_error  NUMERIC,
            corrected_error NUMERIC,
            logged_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_forecast_accuracy_sku_store
            ON inventory.forecast_accuracy (sku, store_id, forecast_date);
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE inventory.forecast_accuracy;
        ALTER TABLE inventory.demand_forecast
          DROP COLUMN baseline_demand, DROP COLUMN corrected_demand,
          DROP COLUMN correction_method, DROP COLUMN correction_features,
          DROP COLUMN baseline_generated_at, DROP COLUMN corrected_generated_at;
    """)
