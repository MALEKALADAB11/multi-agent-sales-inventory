-- ═══════════════════════════════════════════════════════════════════════════
-- Migration 008 — Tables temps réel : contexte horaire, objectifs catégorie,
--                  features forecasting, stock enrichi, recommandations IA
-- Adapté depuis data_generer/ pour alimentation dynamique des agents
-- Prérequis : migrations 001-007, sales.boutiques, inventory.stock_levels OK
-- ═══════════════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ─────────────────────────────────────────────────────────────────────────────
-- SCHEMA context — Données environnementales temps réel par boutique
-- ─────────────────────────────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS context;

-- 1. context.context_hourly_store
--    Contexte météo/trafic/réseau par boutique, par heure.
--    Source temps réel : API météo + capteurs réseau + données trafic urbain.
--    Consommé par : get_seasonal_context (react_tools), CoachAgent cross-domain.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS context.context_hourly_store (
    context_id          VARCHAR(60)     PRIMARY KEY,
    store_id            VARCHAR(50)     NOT NULL REFERENCES sales.boutiques(store_id),
    city                VARCHAR(100),
    context_hour        TIMESTAMP       NOT NULL,
    weather_condition   VARCHAR(30)     DEFAULT 'clear'
        CHECK (weather_condition IN ('clear','cloudy','rain','storm','fog','hot','sand')),
    rain_mm             NUMERIC(6,2)    DEFAULT 0.0,
    temperature_c       NUMERIC(5,2),
    event_type          VARCHAR(50),
    event_strength      NUMERIC(4,2)    DEFAULT 0.0
        CHECK (event_strength BETWEEN 0 AND 1),
    traffic_index       NUMERIC(6,2)    DEFAULT 50.0
        CHECK (traffic_index BETWEEN 0 AND 100),
    cell_load_pct       NUMERIC(6,2)    DEFAULT 50.0
        CHECK (cell_load_pct BETWEEN 0 AND 100),
    outage_flag         SMALLINT        DEFAULT 0
        CHECK (outage_flag IN (0, 1)),
    network_status      VARCHAR(20)     DEFAULT 'nominal'
        CHECK (network_status IN ('nominal','busy','degraded','outage')),
    footfall_estimate   INTEGER         DEFAULT 0,
    source_flag         VARCHAR(60)     DEFAULT 'REALTIME_API',
    created_at          TIMESTAMP       DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ctx_store_hour
    ON context.context_hourly_store (store_id, context_hour DESC);
CREATE INDEX IF NOT EXISTS idx_ctx_event
    ON context.context_hourly_store (event_type, context_hour)
    WHERE event_type IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ctx_network
    ON context.context_hourly_store (network_status, context_hour)
    WHERE network_status != 'nominal';

COMMENT ON TABLE context.context_hourly_store IS
    'Contexte environnemental par boutique/heure. Alimenté en temps réel (API météo + réseau). '
    'Consommé par les agents analyste et coach pour ajuster les recommandations.';


-- ─────────────────────────────────────────────────────────────────────────────
-- SCHEMA forecasting — Features pré-calculées pour le moteur de prévision
-- ─────────────────────────────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS forecasting;

-- 2. forecasting.forecast_features_daily
--    Features lag+rolling+forecast par boutique et catégorie produit.
--    Recalculé chaque nuit (job cron) depuis inventory.sales_history.
--    Consommé par : compute_eod_forecast (react_tools), TimesFM preload.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS forecasting.forecast_features_daily (
    id                          BIGSERIAL       PRIMARY KEY,
    feature_date                DATE            NOT NULL,
    store_id                    VARCHAR(50)     NOT NULL REFERENCES sales.boutiques(store_id),
    product_category            VARCHAR(100)    NOT NULL,
    actual_revenue_ttc          NUMERIC(14,2)   DEFAULT 0.0,
    actual_units                INTEGER         DEFAULT 0,
    lag_1d_revenue              NUMERIC(14,2)   DEFAULT 0.0,
    lag_7d_revenue              NUMERIC(14,2)   DEFAULT 0.0,
    rolling_7d_revenue          NUMERIC(14,2)   DEFAULT 0.0,
    rolling_30d_revenue         NUMERIC(14,2)   DEFAULT 0.0,
    forecast_next_day_revenue   NUMERIC(14,2),
    forecast_confidence         NUMERIC(4,2)    DEFAULT 0.70
        CHECK (forecast_confidence BETWEEN 0 AND 1),
    gap_vs_objective_pct        NUMERIC(7,2),
    model_used                  VARCHAR(30)     DEFAULT 'LINEAR'
        CHECK (model_used IN ('LINEAR','SEASONAL','TIMESFM','ENSEMBLE')),
    source_flag                 VARCHAR(60)     DEFAULT 'DERIVED_FEATURES',
    created_at                  TIMESTAMP       DEFAULT NOW(),
    UNIQUE (feature_date, store_id, product_category)
);

CREATE INDEX IF NOT EXISTS idx_ffd_store_date
    ON forecasting.forecast_features_daily (store_id, feature_date DESC);
CREATE INDEX IF NOT EXISTS idx_ffd_category_date
    ON forecasting.forecast_features_daily (product_category, feature_date DESC);
CREATE INDEX IF NOT EXISTS idx_ffd_gap
    ON forecasting.forecast_features_daily (feature_date, gap_vs_objective_pct)
    WHERE gap_vs_objective_pct < -10;

COMMENT ON TABLE forecasting.forecast_features_daily IS
    'Features pré-calculées chaque nuit pour le forecasting EOD. '
    'Lags 1j/7j, rolling 7j/30j, prévision J+1 et confidence par boutique × catégorie.';


-- ─────────────────────────────────────────────────────────────────────────────
-- Extension de sales — Objectifs par catégorie produit (granularité fine)
-- Plus précis que sales.objectifs (global par boutique/agent)
-- ─────────────────────────────────────────────────────────────────────────────

-- 3. sales.daily_objectives_category
--    Objectifs journaliers par boutique ET catégorie produit.
--    Permet au coach de cibler "Postpayé -38%, mais Recharge +5%".
--    Alimenté depuis telco_targets_monthly (ventilation catégorielle).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sales.daily_objectives_category (
    objective_id        VARCHAR(60)     PRIMARY KEY,
    store_id            VARCHAR(50)     NOT NULL REFERENCES sales.boutiques(store_id),
    objective_date      DATE            NOT NULL,
    product_category    VARCHAR(100)    NOT NULL,
    target_revenue_ttc  NUMERIC(14,2)   NOT NULL,
    target_units        INTEGER         DEFAULT 0,
    priority_weight     NUMERIC(4,2)    DEFAULT 1.0,
    source_flag         VARCHAR(50)     DEFAULT 'COMPUTED_FROM_MONTHLY_TARGET',
    created_at          TIMESTAMP       DEFAULT NOW(),
    UNIQUE (store_id, objective_date, product_category)
);

CREATE INDEX IF NOT EXISTS idx_doc_store_date
    ON sales.daily_objectives_category (store_id, objective_date);
CREATE INDEX IF NOT EXISTS idx_doc_category
    ON sales.daily_objectives_category (product_category, objective_date);

COMMENT ON TABLE sales.daily_objectives_category IS
    'Objectifs journaliers décomposés par catégorie produit. '
    'Ventilés depuis telco_targets_monthly. Consommé par CoachAgent pour le gap scoring catégoriel.';


-- ─────────────────────────────────────────────────────────────────────────────
-- Extension de inventory — Snapshot stock enrichi + métriques dérivées
-- ─────────────────────────────────────────────────────────────────────────────

-- 4. inventory.stock_snapshot_enriched
--    Snapshot quotidien du stock avec métriques supply chain calculées.
--    Recalculé chaque soir depuis inventory.stock_levels + supply.reorder_params.
--    Consommé par : CoachAgent cross-domain scoring, InventoryDecisionAgent.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS inventory.stock_snapshot_enriched (
    id                  BIGSERIAL       PRIMARY KEY,
    snapshot_ts         TIMESTAMP       NOT NULL,
    store_id            VARCHAR(50)     NOT NULL REFERENCES sales.boutiques(store_id),
    product_id          VARCHAR(50)     NOT NULL,
    product_name        TEXT,
    product_category    VARCHAR(100),
    stock_on_hand       INTEGER         DEFAULT 0,
    reserved_qty        INTEGER         DEFAULT 0,
    available_qty       INTEGER         GENERATED ALWAYS AS (stock_on_hand - reserved_qty) STORED,
    avg_daily_demand_30d NUMERIC(10,4)  DEFAULT 0.0,
    demand_last_7d      NUMERIC(10,4)   DEFAULT 0.0,
    coverage_days       NUMERIC(10,2)   DEFAULT 999.0,
    safety_stock        INTEGER         DEFAULT 1,
    reorder_point       INTEGER         DEFAULT 2,
    risk_level          VARCHAR(20)     DEFAULT 'OK'
        CHECK (risk_level IN ('CRITICAL','HIGH','MEDIUM','OK','OVERSTOCK')),
    recommended_action  VARCHAR(50)     DEFAULT 'MONITOR'
        CHECK (recommended_action IN (
            'EXPEDITE_TRANSFER_OR_REPLENISH','REPLENISH','MONITOR',
            'HOLD_OR_PROMOTE','LIQUIDATE'
        )),
    data_quality_flag   VARCHAR(50)     DEFAULT 'OK',
    source_flag         VARCHAR(60)     DEFAULT 'COMPUTED_FROM_STOCK_LEVELS',
    created_at          TIMESTAMP       DEFAULT NOW(),
    UNIQUE (snapshot_ts, store_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_sse_store_risk
    ON inventory.stock_snapshot_enriched (store_id, risk_level, snapshot_ts DESC);
CREATE INDEX IF NOT EXISTS idx_sse_critical
    ON inventory.stock_snapshot_enriched (snapshot_ts DESC, store_id)
    WHERE risk_level IN ('CRITICAL','HIGH');
CREATE INDEX IF NOT EXISTS idx_sse_product
    ON inventory.stock_snapshot_enriched (product_id, snapshot_ts DESC);

COMMENT ON TABLE inventory.stock_snapshot_enriched IS
    'Snapshot quotidien enrichi : coverage_days, safety_stock, risk_level, recommended_action. '
    'Calculé depuis inventory.stock_levels + supply.reorder_params. '
    'Permet au CoachAgent d''accéder au profil stock sans appeler l''InventoryAgent.';


-- 5. inventory.recommendations_computed
--    Recommandations IA préparées : transferts, réapprovisionnements, promotions.
--    Générées par l''InventoryDecisionAgent et stockées pour lecture cross-domain.
--    Consommé par : CoachAgent, SupervisorAgent, frontend dashboard.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS inventory.recommendations_computed (
    recommendation_id       VARCHAR(30)     PRIMARY KEY,
    store_id                VARCHAR(50)     NOT NULL REFERENCES sales.boutiques(store_id),
    product_id              VARCHAR(50)     NOT NULL,
    product_name            TEXT,
    risk_level              VARCHAR(20),
    recommended_action      VARCHAR(50),
    recommended_qty         INTEGER         DEFAULT 0,
    source_store_id         VARCHAR(50),
    reason                  TEXT,
    expected_impact_tnd     NUMERIC(12,2),
    requires_manager_approval BOOLEAN       DEFAULT FALSE,
    status                  VARCHAR(20)     DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE','EXECUTED','EXPIRED','CANCELLED','PENDING_APPROVAL')),
    source_flag             VARCHAR(50)     DEFAULT 'INVENTORY_DECISION_AGENT',
    created_at              TIMESTAMP       DEFAULT NOW(),
    expires_at              TIMESTAMP       DEFAULT NOW() + INTERVAL '24 hours'
);

CREATE INDEX IF NOT EXISTS idx_rc_store_status
    ON inventory.recommendations_computed (store_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rc_active
    ON inventory.recommendations_computed (created_at DESC)
    WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_rc_approval
    ON inventory.recommendations_computed (requires_manager_approval, status)
    WHERE requires_manager_approval = TRUE AND status = 'ACTIVE';

COMMENT ON TABLE inventory.recommendations_computed IS
    'Recommandations IA pré-calculées par l''InventoryDecisionAgent. '
    'Expose un snapshot cross-domain pour le CoachAgent sans appel inter-agents. '
    'TTL 24h — rafraîchi chaque nuit et à chaque décision agent.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 6. coaching.coaching_recommendations
--    Recommandations coaching pré-générées (RAG seed + fallback LLM indispo).
--    Différent de coaching.coaching_events (archive) : ici c'est un pool actif
--    que les agents peuvent piocher par trigger_type + severity + context.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS coaching.coaching_recommendations (
    recommendation_id       VARCHAR(30)     PRIMARY KEY,
    created_ts              TIMESTAMP       NOT NULL DEFAULT NOW(),
    store_id                VARCHAR(50)     REFERENCES sales.boutiques(store_id),
    advisor_id              VARCHAR(20),
    advisor_name            VARCHAR(200),
    trigger_type            VARCHAR(50)
        CHECK (trigger_type IN (
            'OBJECTIVE_GAP','UPSELL_OPPORTUNITY','STOCK_RISK',
            'CHURN_SIGNAL','CROSS_SELL','DOWNTIME_ACTION'
        )),
    severity                VARCHAR(10)
        CHECK (severity IN ('HIGH','MEDIUM','LOW')),
    product_to_push         VARCHAR(50),
    product_to_avoid        VARCHAR(50),
    recommendation_text     TEXT            NOT NULL,
    business_justification  TEXT,
    confidence              NUMERIC(4,2)    DEFAULT 0.75
        CHECK (confidence BETWEEN 0 AND 1),
    expected_impact_ttc     NUMERIC(12,2),
    approval_required       BOOLEAN         DEFAULT FALSE,
    status                  VARCHAR(20)     DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE','USED','EXPIRED','REJECTED')),
    used_at                 TIMESTAMP,
    feedback_score          INTEGER         CHECK (feedback_score BETWEEN 1 AND 5),
    source_flag             VARCHAR(50)     DEFAULT 'AGENTIC_GENERATED'
);

CREATE INDEX IF NOT EXISTS idx_crec_store_trigger
    ON coaching.coaching_recommendations (store_id, trigger_type, status);
CREATE INDEX IF NOT EXISTS idx_crec_severity
    ON coaching.coaching_recommendations (severity, status, created_ts DESC)
    WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_crec_product
    ON coaching.coaching_recommendations (product_to_push, status)
    WHERE status = 'ACTIVE';

COMMENT ON TABLE coaching.coaching_recommendations IS
    'Pool de recommandations coaching actives. RAG seed + output agents. '
    'Filtré par trigger_type, severity, product pour matching contextuel rapide. '
    'Status USED positionné après envoi au conseiller.';


-- ─────────────────────────────────────────────────────────────────────────────
-- VUE TEMPS RÉEL : monitoring.realtime_store_pulse
--    Vue consolidée pour le dashboard — snapshot boutique en 1 requête.
--    Sources : transactions_rt + daily_objectives_category + context_hourly + stock_snapshot
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW monitoring.realtime_store_pulse AS
SELECT
    b.store_id,
    b.nom                                       AS store_name,
    b.wilaya,
    b.zone_commerciale,

    -- CA du jour (depuis transactions_rt)
    COALESCE(tx.ca_today, 0)                    AS ca_today_tnd,

    -- Objectif du jour (depuis sales.objectifs)
    COALESCE(obj_global.objectif_ca, 0)         AS ca_objectif_tnd,

    -- Taux d'atteinte
    CASE WHEN COALESCE(obj_global.objectif_ca, 0) > 0
         THEN ROUND(COALESCE(tx.ca_today, 0) / obj_global.objectif_ca * 100, 1)
         ELSE 0
    END                                         AS attainment_pct,

    -- Nb transactions aujourd'hui
    COALESCE(tx.nb_tx, 0)                       AS nb_transactions,

    -- Contexte env. de la dernière heure connue
    ctx.weather_condition,
    ctx.temperature_c,
    ctx.traffic_index,
    ctx.network_status,
    ctx.event_type,
    ctx.cell_load_pct,

    -- Alertes stock (depuis stock_snapshot_enriched)
    COALESCE(stk.nb_critical, 0)               AS nb_stock_critical,
    COALESCE(stk.nb_high_risk, 0)              AS nb_stock_high_risk,

    -- Recommandations inventory actives
    COALESCE(inv_rec.nb_active, 0)             AS nb_inv_recommendations_active,

    NOW()                                       AS pulse_ts

FROM sales.boutiques b

-- CA du jour
LEFT JOIN (
    SELECT store_id,
           SUM(lig_ttc) AS ca_today,
           COUNT(*)     AS nb_tx
    FROM sales.transactions_rt
    WHERE date_only = CURRENT_DATE
    GROUP BY store_id
) tx ON tx.store_id = b.store_id

-- Objectif global du jour
LEFT JOIN (
    SELECT store_id,
           SUM(objectif_ca) AS objectif_ca
    FROM sales.objectifs
    WHERE date_objectif = CURRENT_DATE
    GROUP BY store_id
) obj_global ON obj_global.store_id = b.store_id

-- Contexte dernière heure
LEFT JOIN LATERAL (
    SELECT weather_condition, temperature_c, traffic_index,
           network_status, event_type, cell_load_pct
    FROM context.context_hourly_store
    WHERE store_id = b.store_id
    ORDER BY context_hour DESC
    LIMIT 1
) ctx ON TRUE

-- Alertes stock
LEFT JOIN (
    SELECT store_id,
           COUNT(*) FILTER (WHERE risk_level = 'CRITICAL') AS nb_critical,
           COUNT(*) FILTER (WHERE risk_level = 'HIGH')     AS nb_high_risk
    FROM inventory.stock_snapshot_enriched
    WHERE snapshot_ts >= NOW() - INTERVAL '26 hours'
    GROUP BY store_id
) stk ON stk.store_id = b.store_id

-- Recommandations inventory actives
LEFT JOIN (
    SELECT store_id, COUNT(*) AS nb_active
    FROM inventory.recommendations_computed
    WHERE status = 'ACTIVE' AND expires_at > NOW()
    GROUP BY store_id
) inv_rec ON inv_rec.store_id = b.store_id

WHERE b.statut = 'ACTIVE' OR b.statut IS NULL;

COMMENT ON VIEW monitoring.realtime_store_pulse IS
    'Vue temps réel consolidée par boutique. Agrège CA/objectif/contexte/stock en 1 requête. '
    'Utilisée par le SupervisorAgent et le dashboard Angular pour le KPI board.';


-- ─────────────────────────────────────────────────────────────────────────────
-- VUE : monitoring.category_gap_live
--    Gap par catégorie produit en temps réel vs objectifs catégoriels.
--    Permet au CoachAgent de cibler la catégorie sous-performante.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW monitoring.category_gap_live AS
SELECT
    doc.store_id,
    doc.objective_date,
    doc.product_category,
    doc.target_revenue_ttc,
    COALESCE(actual.revenue_today, 0)           AS revenue_today,
    ROUND(
        (COALESCE(actual.revenue_today, 0) - doc.target_revenue_ttc)
        / NULLIF(doc.target_revenue_ttc, 0) * 100, 1
    )                                           AS gap_pct,
    CASE
        WHEN (COALESCE(actual.revenue_today, 0) - doc.target_revenue_ttc)
             / NULLIF(doc.target_revenue_ttc, 1) < -0.40 THEN 'CRITICAL'
        WHEN (COALESCE(actual.revenue_today, 0) - doc.target_revenue_ttc)
             / NULLIF(doc.target_revenue_ttc, 1) < -0.20 THEN 'HIGH'
        WHEN (COALESCE(actual.revenue_today, 0) - doc.target_revenue_ttc)
             / NULLIF(doc.target_revenue_ttc, 1) < -0.10 THEN 'MEDIUM'
        ELSE 'OK'
    END                                         AS urgency_level,
    doc.target_units,
    doc.priority_weight,
    NOW()                                       AS computed_at

FROM sales.daily_objectives_category doc

LEFT JOIN (
    SELECT
        t.store_id,
        CASE
            WHEN p.flag_terminal  THEN 'TERMINAL'
            WHEN p.flag_forfait   THEN 'FORFAIT'
            WHEN p.flag_sim       THEN 'SIM'
            WHEN p.flag_recharge  THEN 'RECHARGE'
            WHEN p.categorie='70' THEN 'ACCESSOIRE'
            ELSE 'AUTRE'
        END                                     AS product_category,
        SUM(t.lig_ttc)                          AS revenue_today
    FROM sales.transactions_rt t
    JOIN sales.produits p ON p.sku = t.cod_prod
    WHERE t.date_only = CURRENT_DATE
    GROUP BY t.store_id,
        CASE
            WHEN p.flag_terminal  THEN 'TERMINAL'
            WHEN p.flag_forfait   THEN 'FORFAIT'
            WHEN p.flag_sim       THEN 'SIM'
            WHEN p.flag_recharge  THEN 'RECHARGE'
            WHEN p.categorie='70' THEN 'ACCESSOIRE'
            ELSE 'AUTRE'
        END
) actual ON actual.store_id = doc.store_id
        AND actual.product_category = doc.product_category

WHERE doc.objective_date = CURRENT_DATE;

COMMENT ON VIEW monitoring.category_gap_live IS
    'Gap en temps réel par catégorie produit vs objectifs journaliers. '
    'Consommé par CoachAgent pour identifier quelle catégorie prioriser dans le pitch.';


-- ─────────────────────────────────────────────────────────────────────────────
-- Chargement initial depuis data_generer/ — à exécuter avec \copy psql
-- ─────────────────────────────────────────────────────────────────────────────
-- \copy context.context_hourly_store (context_id,store_id,city,context_hour,weather_condition,rain_mm,temperature_c,event_type,event_strength,traffic_index,cell_load_pct,outage_flag,network_status) FROM 'data_generer/context_hourly_store_2026.csv' CSV HEADER;
-- \copy sales.daily_objectives_category (objective_id,store_id,objective_date,product_category,target_revenue_ttc,target_units,priority_weight,source_flag) FROM 'data_generer/daily_objectives_store_category_2026.csv' CSV HEADER;
-- \copy forecasting.forecast_features_daily (feature_date,store_id,product_category,actual_revenue_ttc,actual_units,lag_1d_revenue,lag_7d_revenue,rolling_7d_revenue,forecast_next_day_revenue,forecast_confidence,gap_vs_objective_pct,source_flag) FROM 'data_generer/forecast_daily_store_category_features.csv' CSV HEADER ON CONFLICT DO NOTHING;
-- \copy inventory.stock_snapshot_enriched (snapshot_ts,store_id,product_id,product_name,product_category,stock_on_hand,reserved_qty,avg_daily_demand_30d,demand_last_7d,coverage_days,safety_stock,reorder_point,risk_level,recommended_action,data_quality_flag,source_flag) FROM 'data_generer/stock_snapshot_current_enriched.csv' CSV HEADER ON CONFLICT DO NOTHING;
-- \copy inventory.recommendations_computed (recommendation_id,store_id,product_id,product_name,risk_level,recommended_action,recommended_qty,reason,requires_manager_approval,source_flag) FROM 'data_generer/inventory_recommendations_current.csv' CSV HEADER ON CONFLICT DO NOTHING;
-- \copy coaching.coaching_recommendations (recommendation_id,created_ts,store_id,advisor_id,advisor_name,trigger_type,severity,product_to_push,product_to_avoid,recommendation_text,business_justification,confidence,expected_impact_ttc,approval_required,status,source_flag) FROM 'data_generer/coaching_recommendations_sample.csv' CSV HEADER ON CONFLICT DO NOTHING;

SELECT 'Migration 008 OK — Schemas : context, forecasting' AS status;
SELECT 'Nouvelles tables :' AS info;
SELECT table_schema || '.' || table_name AS table_name
FROM information_schema.tables
WHERE table_schema IN ('context','forecasting')
   OR (table_schema IN ('sales','inventory','coaching') AND table_name IN (
       'daily_objectives_category','stock_snapshot_enriched',
       'recommendations_computed','coaching_recommendations'
   ))
ORDER BY table_schema, table_name;
