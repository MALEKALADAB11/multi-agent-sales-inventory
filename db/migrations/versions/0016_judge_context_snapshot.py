"""Rend l'ancrage vérifiable pour le juge LLM en production.

Problème fermé ici : `evals/judge.py` note `ancrage` en recoupant chaque chiffre
de la recommandation avec le contexte fourni. En batch (evals/), ce contexte est
le scénario complet du dataset — la vérification est totale. En direct
(app/core/quality_service.py), les rapports amont (baseline_report /
context_report / adjusted_metrics) n'étaient nulle part : le juge ne recevait que
les colonnes scalaires de la ligne, donc il notait `ancrage` sur un contexte
amputé et signalait des « hallucinations » sur des chiffres parfaitement réels
mais invisibles pour lui.

Deux colonnes :

  inventory.recommendations.context_snapshot (jsonb)
      Les trois rapports que le DecisionAgent avait sous les yeux au moment
      d'écrire recommendation_text, figés avec la ligne. Même contenu et même
      découpage que `build_context_string()` dans
      evals/run_inventory_recommendations.py — c'est ce qui rend les scores du
      juge live comparables à ceux du banc hors-ligne.

  public.recommendation_scores.context_level (text)
      'full'    → le juge a vu le snapshot ; `ancrage` est pleinement mesuré.
      'partial' → snapshot absent (lignes antérieures à cette migration) ;
                  `ancrage` reste indicatif.
      Sans cette colonne, une moyenne d'ancrage mélangerait deux mesures qui
      n'ont pas le même sens — c'est la moyenne qui devient ininterprétable, pas
      seulement la ligne.

Nullable et sans backfill : les 765 recommandations existantes n'ont pas de
snapshot et n'en auront jamais (le contexte n'a pas été conservé). Elles restent
notables, en 'partial'.

Revision ID: 0016
Revises: 0015
"""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE inventory.recommendations
            ADD COLUMN IF NOT EXISTS context_snapshot jsonb;

        COMMENT ON COLUMN inventory.recommendations.context_snapshot
            IS '[QUALITE] baseline_report / context_report / adjusted_metrics vus par le DecisionAgent au moment de la décision — contexte de référence du juge LLM (evals/judge.py, critère ancrage).';

        ALTER TABLE public.recommendation_scores
            ADD COLUMN IF NOT EXISTS context_level text
                CHECK (context_level IN ('full','partial'));

        COMMENT ON COLUMN public.recommendation_scores.context_level
            IS 'full = le juge a vu le contexte complet de la décision (ancrage mesuré) · partial = contexte reconstruit depuis les colonnes seules (ancrage indicatif). Ne jamais moyenner les deux ensemble.';

        -- Le tri de la page Supervision filtre sur context_level : sans index,
        -- séparer full/partial ajoute un seq scan à chaque affichage.
        CREATE INDEX IF NOT EXISTS idx_reco_scores_ctx_level
            ON public.recommendation_scores (domain, context_level, created_at DESC);
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS public.idx_reco_scores_ctx_level;
        ALTER TABLE public.recommendation_scores DROP COLUMN IF EXISTS context_level;
        ALTER TABLE inventory.recommendations   DROP COLUMN IF EXISTS context_snapshot;
    """)
