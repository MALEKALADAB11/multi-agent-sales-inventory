"""Scores du juge LLM sur les recommandations (inventaire ET vente).

Branche le juge (evals/judge.py) en direct : chaque recommandation persistée —
côté inventaire (inventory.recommendations, DecisionAgent) comme côté vente
(public.hitl_reviews, Stratège) — peut être notée /5 par un modèle juge distinct
du candidat, et le score est stocké ici pour alimenter la page Supervision Métier
sans re-scorer à chaque affichage.

Table générique dans public (pas de FK vers un schéma unique) : `domain` +
`ref_id` (id de la recommandation, uuid en texte) permettent de couvrir les deux
domaines dans une seule table, avec la grille de critères adaptée (`criteria_set`).

Revision ID: 0014
Revises: 0013
"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.recommendation_scores (
            id             SERIAL PRIMARY KEY,
            domain         text         NOT NULL
                           CHECK (domain IN ('inventory','sales')),
            ref_id         text         NOT NULL,   -- inventory.recommendations.id | hitl_reviews.id (uuid en texte)
            store_id       text,
            mean_score     numeric(4,2),            -- moyenne des critères, /5
            scores         jsonb,                   -- notes 0-5 par critère
            criteria_set   text,                    -- 'inventory' | 'coach' (grille utilisée)
            hallucination  boolean      DEFAULT false,
            verdict        text,
            judge_model    text,                    -- ex: 'mistral/mistral-large-latest'
            created_at     timestamp    NOT NULL DEFAULT now(),
            CONSTRAINT uq_reco_score_ref_model UNIQUE (domain, ref_id, judge_model)
        );

        COMMENT ON TABLE public.recommendation_scores
            IS '[QUALITE] Notes LLM-as-judge (evals/judge.py) sur les recommandations inventaire et vente — page Supervision.';
        COMMENT ON COLUMN public.recommendation_scores.domain
            IS 'inventory = recommendation_text du DecisionAgent · sales = strategie_summary du Stratège (hitl_reviews)';
        COMMENT ON COLUMN public.recommendation_scores.ref_id
            IS 'id de la recommandation notée (uuid en texte) — inventory.recommendations.id ou public.hitl_reviews.id';

        CREATE INDEX IF NOT EXISTS idx_reco_scores_domain_store_date
            ON public.recommendation_scores (domain, store_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_reco_scores_ref
            ON public.recommendation_scores (ref_id);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.recommendation_scores;")
