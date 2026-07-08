"""Boucle de feedback humain → agents.

Ferme le gap "le système génère des incitations mais ne mesure pas leur
exécution" : les conseillers déclarent suivi/ignoré sur les incitations,
les managers approuvent/rejettent (HITL, PO Kanban). Ces signaux sont
agrégés et réinjectés dans les prompts du DecisionAgent et du Stratège
(app/core/feedback_service.py).

Revision ID: 0008
Revises: 0007
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE public.agent_feedback (
            id          SERIAL PRIMARY KEY,
            store_id    VARCHAR(50) NOT NULL,
            source      VARCHAR(30) NOT NULL,   -- 'incitation' | 'hitl' | 'po'
            ref_id      VARCHAR(80),            -- cycle_id, review_id, po_id…
            sku         INTEGER,
            action_type VARCHAR(50),            -- ex: 'push_produit', 'relance_client'
            decision    VARCHAR(20) NOT NULL,   -- 'followed'|'ignored'|'approved'|'rejected'
            reason      TEXT,
            payload     JSONB,
            created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
            CONSTRAINT chk_agent_feedback_decision
                CHECK (decision IN ('followed','ignored','approved','rejected'))
        );
        CREATE INDEX idx_agent_feedback_store_date
            ON public.agent_feedback (store_id, created_at DESC);
        CREATE INDEX idx_agent_feedback_sku
            ON public.agent_feedback (sku) WHERE sku IS NOT NULL;
        COMMENT ON TABLE public.agent_feedback IS
            'Feedback humain sur les sorties agents (incitations coach, HITL, PO). '
            'Agrégé par feedback_service et réinjecté dans les prompts des agents '
            'Decision et Stratège — boucle d''apprentissage continue.';
    """)


def downgrade() -> None:
    op.execute("DROP TABLE public.agent_feedback")
