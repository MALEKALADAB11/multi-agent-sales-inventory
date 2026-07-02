-- ═══════════════════════════════════════════════════════════════════════════
-- Migration 007 : Tables opérationnelles coaching, escalation, mémoire agents
-- Schemas : coaching, monitoring (vues)
-- Prérequis : migrations 001-006 exécutées, sales.boutiques et sales.agents OK
-- ═══════════════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE SCHEMA IF NOT EXISTS coaching;
CREATE SCHEMA IF NOT EXISTS monitoring;

-- ───────────────────────────────────────────────────────────────────────────
-- 1. coaching.coaching_events
--    Archive de chaque coaching card générée (SC-001 : Real-Time Coaching Trigger)
--    Un enregistrement par cycle × conseiller coaché
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS coaching.coaching_events (
    id                  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    advisor_id          INTEGER     NOT NULL,
    store_id            VARCHAR(50) NOT NULL,
    cycle_id            VARCHAR(100),

    -- Contexte gap
    urgency_level       VARCHAR(10) NOT NULL
        CHECK (urgency_level IN ('HIGH','MEDIUM','LOW')),
    urgency_score       NUMERIC(5,2) DEFAULT 0,
    gap_pct             NUMERIC(7,2),               -- ex : -32.5 (négatif = sous objectif)
    gap_amount          NUMERIC(12,2),              -- montant manquant en TND
    forecast_eod        NUMERIC(12,2),              -- prévision fin de journée TND

    -- Contenu coaching
    advice_text         TEXT,
    produit_a_pousser   VARCHAR(200),
    produit_a_eviter    VARCHAR(200),
    strategie           TEXT,
    cause_racine        TEXT,

    -- RAG
    rag_used            BOOLEAN     DEFAULT FALSE,
    nb_rag_scripts      INTEGER     DEFAULT 0,
    script_ids          JSONB       DEFAULT '[]',
    context_hash        VARCHAR(64),

    -- Contexte environnemental
    weather_label       VARCHAR(100),
    weather_temp_c      NUMERIC(4,1),
    weather_effect      NUMERIC(5,2),               -- impact estimé sur ventes (%)
    event_name          VARCHAR(200),
    event_proximity_km  NUMERIC(6,2),

    -- Guardrail
    guardrail_status    VARCHAR(20) DEFAULT 'APPROVE'
        CHECK (guardrail_status IN ('APPROVE','BLOCK','REWRITE')),
    guardrail_rule      VARCHAR(100),

    -- Résultat (renseigné après feedback ou fin de journée)
    feedback_score      INTEGER     CHECK (feedback_score BETWEEN 1 AND 5),
    was_effective       BOOLEAN,
    ca_after_coaching   NUMERIC(12,2),              -- CA réalisé après coaching

    created_at          TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ce_advisor  ON coaching.coaching_events(advisor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ce_store    ON coaching.coaching_events(store_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ce_urgency  ON coaching.coaching_events(urgency_level, created_at DESC)
    WHERE urgency_level = 'HIGH';
CREATE INDEX IF NOT EXISTS idx_ce_cycle    ON coaching.coaching_events(cycle_id);
CREATE INDEX IF NOT EXISTS idx_ce_rag      ON coaching.coaching_events(rag_used, created_at DESC)
    WHERE rag_used = TRUE;


-- ───────────────────────────────────────────────────────────────────────────
-- 2. coaching.escalations
--    Escalades manager sur sous-performance persistante (SC-003)
--    Créée quand gap > 25% ET durée > 90 min sans amélioration
-- ───────────────────────────────────────────────────────────────────────────
CREATE SEQUENCE IF NOT EXISTS coaching.escalation_seq START 1 INCREMENT 1;

CREATE TABLE IF NOT EXISTS coaching.escalations (
    id                      VARCHAR(20)  PRIMARY KEY,    -- format : ESC-NNNN
    store_id                VARCHAR(50)  NOT NULL,

    -- Métriques déclencheurs
    gap_pct                 NUMERIC(7,2) NOT NULL,       -- ex : -31.0
    gap_duration_min        INTEGER      NOT NULL,       -- durée en minutes (> 90 pour escalade)
    ca_at_escalation        NUMERIC(12,2),
    ca_target_day           NUMERIC(12,2),

    -- Analyse causes (JSON array de strings)
    root_causes             JSONB        DEFAULT '[]',
    -- ex : ["stock A55 épuisé à 13h45", "ADV-02 taux conversion 38% vs 61% moy"]

    -- Plan d'actions (JSON array d'objets)
    actions                 JSONB        DEFAULT '[]',
    -- ex : [{"type": "STOCK_TRANSFER", "detail": "BTQ-09→BTQ-14", "requires_approval": true}]

    -- HITL
    approver_id             INTEGER,
    approver_name           VARCHAR(100),
    coaching_events_prior   JSONB        DEFAULT '[]',   -- IDs coaching_events sans effet
    hitl_request_ids        JSONB        DEFAULT '[]',   -- IDs hitl_requests associés

    -- Impact
    expected_impact_tnd     NUMERIC(12,2),
    actual_impact_tnd       NUMERIC(12,2),               -- renseigné en fin de journée

    -- Statut workflow
    status                  VARCHAR(20)  DEFAULT 'pending'
        CHECK (status IN ('pending','approved','rejected','executed','cancelled')),
    resolved_at             TIMESTAMP,
    resolution_note         TEXT,

    created_at              TIMESTAMP    DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_esc_store   ON coaching.escalations(store_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_esc_status  ON coaching.escalations(status)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_esc_date    ON coaching.escalations(created_at DESC);


-- ───────────────────────────────────────────────────────────────────────────
-- 3. coaching.hitl_requests
--    Demandes individuelles d'approbation dans un workflow d'escalade
--    Chaque action de l'escalade = 1 hitl_request (ex : transfert stock, réaffectation)
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS coaching.hitl_requests (
    id                  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id            VARCHAR(50) NOT NULL,
    escalation_id       VARCHAR(20) REFERENCES coaching.escalations(id),

    action_type         VARCHAR(50) NOT NULL
        CHECK (action_type IN (
            'STOCK_TRANSFER', 'ADVISOR_REALLOCATION', 'PITCH_CHANGE',
            'EMERGENCY_COACHING', 'GOAL_REVISION', 'INTER_STORE_SUPPORT'
        )),
    description         TEXT,
    target_store_id     VARCHAR(50),
    target_advisor_id   INTEGER,
    qty_units           INTEGER,

    expected_impact_tnd NUMERIC(12,2),
    context             JSONB       DEFAULT '{}',

    -- OPA policy evaluation
    policy_rule         VARCHAR(100),               -- règle OPA déclenchée
    auto_approved       BOOLEAN     DEFAULT FALSE,  -- TRUE si approbation automatique OPA

    -- Workflow
    status              VARCHAR(20) DEFAULT 'pending'
        CHECK (status IN ('pending','approved','rejected','auto_approved','cancelled')),
    approver_id         INTEGER,
    approver_name       VARCHAR(100),
    approver_comment    TEXT,

    created_at          TIMESTAMP   DEFAULT NOW(),
    resolved_at         TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_hitl_store    ON coaching.hitl_requests(store_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_hitl_pending  ON coaching.hitl_requests(status, created_at)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_hitl_esc      ON coaching.hitl_requests(escalation_id);


-- ───────────────────────────────────────────────────────────────────────────
-- 4. coaching.agent_memory
--    Mémoire persistante inter-cycles pour les agents (analyste, stratège, coach)
--    Permet de détecter les dérives et comparer cycle N à N-1
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS coaching.agent_memory (
    id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_name  VARCHAR(50) NOT NULL
        CHECK (agent_name IN ('analyst','stratege','coach','supervisor','guardrail')),
    store_id    VARCHAR(50),
    advisor_id  INTEGER,
    cycle_id    VARCHAR(100),
    content     JSONB       NOT NULL DEFAULT '{}',
    -- Champs typiques pour analyst :
    --   {"gap_history": [...], "avg_gap_7d": -12.3, "urgency_trend": "worsening",
    --    "best_hour": 11, "worst_hour": 15, "top_product": "forfait_flexi_25"}
    -- Pour coach :
    --   {"advisor_style": "data-driven", "objections_frequentes": [...],
    --    "scripts_efficaces": ["S-042","S-077"], "last_coaching_at": "2026-06-29"}
    created_at  TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mem_agent  ON coaching.agent_memory(agent_name, store_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mem_adv    ON coaching.agent_memory(advisor_id, created_at DESC)
    WHERE advisor_id IS NOT NULL;


-- ───────────────────────────────────────────────────────────────────────────
-- 5. monitoring.cycle_logs   (vue sur agent_cycles)
--    Exposée par postgres_provider.fetch_cycle_logs()
--    Si cycle_logs existe déjà comme TABLE (ancienne version), on la renomme
-- ───────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'monitoring'
          AND table_name   = 'cycle_logs'
          AND table_type   = 'BASE TABLE'
    ) THEN
        ALTER TABLE monitoring.cycle_logs RENAME TO cycle_logs_legacy;
    END IF;
END $$;

CREATE OR REPLACE VIEW monitoring.cycle_logs AS
SELECT
    cycle_id,
    store_id,
    triggered_by,
    urgency_level,
    urgency_score,
    gap_pct,
    gap_amount,
    ca_today,
    ca_target,
    forecast_eod,
    analyst_summary,
    strategie,
    nb_actions,
    cause_racine,
    rag_used,
    nb_rag_scripts,
    weather_label,
    weather_effect,
    total_ms,
    nodes_executed,
    errors_count,
    status,
    created_at
FROM public.agent_cycles
ORDER BY created_at DESC;


-- ───────────────────────────────────────────────────────────────────────────
-- 6. monitoring.coaching_interactions   (vue sur coaching.coaching_events)
--    Exposée par postgres_provider.fetch_coaching_interactions()
-- ───────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW monitoring.coaching_interactions AS
SELECT
    ce.id,
    ce.advisor_id,
    ce.store_id,
    ce.cycle_id,
    ce.urgency_level,
    ce.gap_pct,
    ce.advice_text,
    ce.produit_a_pousser,
    ce.rag_used,
    ce.nb_rag_scripts,
    ce.guardrail_status,
    ce.was_effective,
    ce.feedback_score,
    ce.created_at
FROM coaching.coaching_events ce
ORDER BY ce.created_at DESC;


-- ───────────────────────────────────────────────────────────────────────────
-- Vérification
-- ───────────────────────────────────────────────────────────────────────────
SELECT 'Migration 007 OK — Tables créées :' AS status;
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema IN ('coaching','monitoring')
ORDER BY table_schema, table_name;
