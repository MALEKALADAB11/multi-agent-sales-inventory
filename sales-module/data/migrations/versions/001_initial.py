"""Initial tables

Revision ID: 001initial
Revises: 
Create Date: 2025-01-01 00:00:00.000000
"""

# --- OBLIGATOIRE ---
revision = '001initial'
down_revision = None
branch_labels = None
depends_on = None
# -------------------

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:

    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # ── stores ──────────────────────────────────────────
    op.create_table(
        "stores",
        sa.Column("id",         sa.String(),    primary_key=True,
                  server_default=sa.text("uuid_generate_v4()::text")),
        sa.Column("store_code", sa.String(20),  nullable=False),
        sa.Column("name",       sa.String(100), nullable=False),
        sa.Column("city",       sa.String(100), nullable=True),
        sa.Column("capacity",   sa.Integer(),   server_default="20"),
        sa.Column("active",     sa.Boolean(),   server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("store_code", name="uq_store_code")
    )

    # ── advisors ─────────────────────────────────────────
    op.create_table(
        "advisors",
        sa.Column("id",           sa.String(),    primary_key=True,
                  server_default=sa.text("uuid_generate_v4()::text")),
        sa.Column("store_id",     sa.String(),    nullable=False),
        sa.Column("advisor_code", sa.String(20),  nullable=False),
        sa.Column("name",         sa.String(100), nullable=False),
        sa.Column("role",         sa.String(100), nullable=True),
        sa.Column("avatar_color", sa.String(7),   nullable=True),
        sa.Column("coach_score",  sa.Float(),     server_default="0.0"),
        sa.Column("active",       sa.Boolean(),   server_default="true"),
        sa.Column("created_at",   sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"],
                                name="fk_advisor_store"),
        sa.UniqueConstraint("advisor_code", name="uq_advisor_code")
    )

    # ── pos_transactions ─────────────────────────────────
    op.create_table(
        "pos_transactions",
        sa.Column("id",             sa.String(),         primary_key=True,
                  server_default=sa.text("uuid_generate_v4()::text")),
        sa.Column("store_id",       sa.String(),         nullable=False),
        sa.Column("advisor_id",     sa.String(),         nullable=False),
        sa.Column("sku",            sa.String(50),       nullable=True),
        sa.Column("product_name",   sa.String(200),      nullable=True),
        sa.Column("category",       sa.String(100),      nullable=True),
        sa.Column("amount",         sa.Numeric(10, 2),   nullable=False),
        sa.Column("units",          sa.Integer(),        server_default="1"),
        sa.Column("margin_rate",    sa.Numeric(5, 4),    nullable=True),
        sa.Column("transaction_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at",    sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["store_id"],   ["stores.id"],
                                name="fk_pos_store"),
        sa.ForeignKeyConstraint(["advisor_id"], ["advisors.id"],
                                name="fk_pos_advisor")
    )

    op.create_index("idx_pos_store_date",
                    "pos_transactions", ["store_id", "transaction_ts"])
    op.create_index("idx_pos_advisor_date",
                    "pos_transactions", ["advisor_id", "transaction_ts"])

    # ── daily_targets ────────────────────────────────────
    op.create_table(
        "daily_targets",
        sa.Column("id",           sa.String(),       primary_key=True,
                  server_default=sa.text("uuid_generate_v4()::text")),
        sa.Column("store_id",     sa.String(),       nullable=True),
        sa.Column("advisor_id",   sa.String(),       nullable=True),
        sa.Column("target_date",  sa.Date(),         nullable=False),
        sa.Column("ca_target",    sa.Numeric(10, 2), nullable=False),
        sa.Column("units_target", sa.Integer(),      nullable=True),
        sa.Column("adjusted",     sa.Boolean(),      server_default="false"),
        sa.Column("created_at",   sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["store_id"],   ["stores.id"],
                                name="fk_target_store"),
        sa.ForeignKeyConstraint(["advisor_id"], ["advisors.id"],
                                name="fk_target_advisor"),
        sa.UniqueConstraint("advisor_id", "target_date",
                            name="uq_advisor_target_date")
    )

    # ── agent_cycles ─────────────────────────────────────
    op.create_table(
        "agent_cycles",
        sa.Column("id",             sa.String(),  primary_key=True),
        sa.Column("store_id",       sa.String(),  nullable=True),
        sa.Column("triggered_by",   sa.String(20), nullable=True),
        sa.Column("started_at",     sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at",   sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms",    sa.Integer(),  nullable=True),
        sa.Column("state_snapshot", sa.JSON(),     nullable=True),
        sa.Column("errors",         sa.JSON(),     nullable=True),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"],
                                name="fk_cycle_store")
    )

    # ── coaching_cards ───────────────────────────────────
    op.create_table(
        "coaching_cards",
        sa.Column("id",         sa.String(),  primary_key=True,
                  server_default=sa.text("uuid_generate_v4()::text")),
        sa.Column("advisor_id", sa.String(),  nullable=True),
        sa.Column("cycle_id",   sa.String(),  nullable=True),
        sa.Column("priority",   sa.String(10), nullable=True),
        sa.Column("gap",        sa.Float(),   nullable=True),
        sa.Column("context",    sa.Text(),    nullable=True),
        sa.Column("advice",     sa.Text(),    nullable=True),
        sa.Column("confidence", sa.Float(),   nullable=True),
        sa.Column("status",     sa.String(20), server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["advisor_id"], ["advisors.id"],
                                name="fk_card_advisor"),
        sa.ForeignKeyConstraint(["cycle_id"],   ["agent_cycles.id"],
                                name="fk_card_cycle")
    )


def downgrade() -> None:
    op.drop_table("coaching_cards")
    op.drop_table("agent_cycles")
    op.drop_table("daily_targets")
    op.drop_index("idx_pos_advisor_date", table_name="pos_transactions")
    op.drop_index("idx_pos_store_date",   table_name="pos_transactions")
    op.drop_table("pos_transactions")
    op.drop_table("advisors")
    op.drop_table("stores")