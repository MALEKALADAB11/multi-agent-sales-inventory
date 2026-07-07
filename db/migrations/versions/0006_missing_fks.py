"""FK manquantes + purge des orphelins synthétiques + normalisation de type.

Audit pg_constraint 2026-07-07 : 27 FK existantes, celles-ci manquaient.
Orphelins purgés (données synthétiques de seed, volumétrie négligeable) :
- inventory.stock_history : 104 lignes / 844 987 sur boutiques inexistantes
  (A01, HLD, OT1)
- inventory.promotions : 1 ligne 'Discount_Default' (sku 2020031 inexistant)
- coaching.coaching_events : 10 lignes sur advisors fantômes (2891, 3104)
- customer.nps_csat : agent_id mis à NULL sur 108 lignes (2 agents fantômes)
  — les enquêtes restent exploitables au niveau boutique

Normalisation : inventory.demand_forecast.sku TEXT → INTEGER (cohérent avec
sales.produits.sku, 0 orphelin après cast).

Les FK sont ajoutées NOT VALID puis VALIDATE : n'exclut aucune écriture
pendant la pose, et le VALIDATE échoue explicitement si un orphelin
réapparaît entre l'audit et l'application.

Revision ID: 0006
Revises: 0005
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_FKS = [
    # (table, nom contrainte, définition)
    ("inventory.demand_forecast", "fk_demand_forecast_store",
     "FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id)"),
    ("inventory.demand_forecast", "fk_demand_forecast_sku",
     "FOREIGN KEY (sku) REFERENCES sales.produits(sku)"),
    ("inventory.sales_history", "fk_sales_history_store",
     "FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id)"),
    ("inventory.sales_history", "fk_sales_history_sku",
     "FOREIGN KEY (sku) REFERENCES sales.produits(sku)"),
    ("inventory.stock_history", "fk_stock_history_store",
     "FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id)"),
    ("inventory.stock_history", "fk_stock_history_sku",
     "FOREIGN KEY (sku) REFERENCES sales.produits(sku)"),
    ("inventory.promotions", "fk_promotions_sku",
     "FOREIGN KEY (sku) REFERENCES sales.produits(sku)"),
    ("inventory.events", "fk_events_sku",
     "FOREIGN KEY (sku) REFERENCES sales.produits(sku)"),
    ("inventory.events", "fk_events_store",
     "FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id)"),
    ("supply.transfers", "fk_transfers_sku",
     "FOREIGN KEY (sku) REFERENCES sales.produits(sku)"),
    ("supply.transfers", "fk_transfers_store_source",
     "FOREIGN KEY (store_source) REFERENCES sales.boutiques(store_id)"),
    ("supply.transfers", "fk_transfers_store_dest",
     "FOREIGN KEY (store_dest) REFERENCES sales.boutiques(store_id)"),
    ("coaching.coaching_events", "fk_coaching_events_store",
     "FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id)"),
    ("coaching.coaching_events", "fk_coaching_events_advisor",
     "FOREIGN KEY (advisor_id) REFERENCES sales.agents(agent_id)"),
    ("customer.nps_csat", "fk_nps_csat_store",
     "FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id)"),
    ("customer.nps_csat", "fk_nps_csat_agent",
     "FOREIGN KEY (agent_id) REFERENCES sales.agents(agent_id)"),
]


def upgrade() -> None:
    # 1. Purge des orphelins synthétiques
    op.execute("""
        DELETE FROM inventory.stock_history t
        WHERE NOT EXISTS (SELECT 1 FROM sales.boutiques b
                          WHERE b.store_id = t.store_id);
        DELETE FROM inventory.promotions t
        WHERE t.sku IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM sales.produits p WHERE p.sku = t.sku);
        DELETE FROM coaching.coaching_events t
        WHERE NOT EXISTS (SELECT 1 FROM sales.agents a
                          WHERE a.agent_id = t.advisor_id);
        UPDATE customer.nps_csat t SET agent_id = NULL
        WHERE t.agent_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM sales.agents a
                          WHERE a.agent_id = t.agent_id);
    """)

    # 2. Normalisation de type
    op.execute("""
        ALTER TABLE inventory.demand_forecast
            ALTER COLUMN sku TYPE INTEGER USING NULLIF(sku, '')::integer;
    """)

    # 3. FK NOT VALID puis VALIDATE
    for table, name, definition in _FKS:
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} {definition} NOT VALID")
        op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")


def downgrade() -> None:
    for table, name, _ in reversed(_FKS):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
    op.execute("""
        ALTER TABLE inventory.demand_forecast
            ALTER COLUMN sku TYPE TEXT USING sku::text;
    """)
    # Les lignes orphelines purgées ne sont pas restaurées.
