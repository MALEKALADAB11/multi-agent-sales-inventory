"""Chaîne causale vente→alerte→recommandation→PO (backlog lignes 74/87/88).

- alerts.agent_run_id : TEXT (100 % NULL) → INTEGER + FK agent_runs(id)
- recommendations.agent_run_id : TEXT (entiers en texte, 0 orphelin après
  cast — audit 2026-07-07) → INTEGER + FK agent_runs(id)
- recommendations.alert_id : nouvelle colonne, FK alerts(id) — trace quelle
  alerte a déclenché la recommandation
- index (reference_type, reference_id) sur supply.stock_movements pour
  retrouver les mouvements d'une réception PO

purchase_orders.recommendation_id → recommendations(id) existe déjà (0001).

Revision ID: 0005
Revises: 0004
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE inventory.alerts
            ALTER COLUMN agent_run_id TYPE INTEGER
            USING NULLIF(agent_run_id, '')::integer;
        ALTER TABLE inventory.alerts
            ADD CONSTRAINT fk_alerts_agent_run
            FOREIGN KEY (agent_run_id) REFERENCES inventory.agent_runs(id)
            ON DELETE SET NULL;

        ALTER TABLE inventory.recommendations
            ALTER COLUMN agent_run_id TYPE INTEGER
            USING NULLIF(agent_run_id, '')::integer;
        ALTER TABLE inventory.recommendations
            ADD CONSTRAINT fk_recommendations_agent_run
            FOREIGN KEY (agent_run_id) REFERENCES inventory.agent_runs(id)
            ON DELETE SET NULL;

        ALTER TABLE inventory.recommendations
            ADD COLUMN alert_id INTEGER
            REFERENCES inventory.alerts(id) ON DELETE SET NULL;
        COMMENT ON COLUMN inventory.recommendations.alert_id IS
            'Alerte qui a déclenché cette recommandation (chaîne causale).';

        CREATE INDEX idx_stock_movements_reference
            ON supply.stock_movements (reference_type, reference_id);
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX supply.idx_stock_movements_reference;
        ALTER TABLE inventory.recommendations DROP COLUMN alert_id;
        ALTER TABLE inventory.recommendations
            DROP CONSTRAINT fk_recommendations_agent_run;
        ALTER TABLE inventory.recommendations
            ALTER COLUMN agent_run_id TYPE TEXT USING agent_run_id::text;
        ALTER TABLE inventory.alerts DROP CONSTRAINT fk_alerts_agent_run;
        ALTER TABLE inventory.alerts
            ALTER COLUMN agent_run_id TYPE TEXT USING agent_run_id::text;
    """)
