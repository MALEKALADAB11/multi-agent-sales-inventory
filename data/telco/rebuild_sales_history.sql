-- ===========================================================================
-- REBUILD inventory.sales_history — granularite journaliere
-- ===========================================================================

-- Ajouter colonnes manquantes pour time series
ALTER TABLE inventory.sales_history
    ADD COLUMN IF NOT EXISTS promo_type    VARCHAR(50),
    ADD COLUMN IF NOT EXISTS day_of_week   SMALLINT,
    ADD COLUMN IF NOT EXISTS week_of_year  SMALLINT,
    ADD COLUMN IF NOT EXISTS month_num     SMALLINT,
    ADD COLUMN IF NOT EXISTS year_num      SMALLINT,
    ADD COLUMN IF NOT EXISTS is_weekend    BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS is_event_day  BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS event_intensite VARCHAR(10),
    ADD COLUMN IF NOT EXISTS uplift_factor NUMERIC(6,4) DEFAULT 1.0;

-- Index pour time series queries
CREATE INDEX IF NOT EXISTS idx_sh_store_sku_date ON inventory.sales_history(store_id, sku, record_date DESC);
CREATE INDEX IF NOT EXISTS idx_sh_date_cat       ON inventory.sales_history(record_date, category);
CREATE INDEX IF NOT EXISTS idx_sh_event          ON inventory.sales_history(event_type, record_date) WHERE event_type IS NOT NULL;

TRUNCATE inventory.sales_history;

INSERT INTO inventory.sales_history
    (store_id, sku, product_name, category, record_date,
     quantity_sold, revenue, unit_price,
     is_promo, promo_type, event_name, event_type, season,
     is_event_day, event_intensite, uplift_factor,
     is_weekend, day_of_week, week_of_year, month_num, year_num)
SELECT
    t.store_id,
    t.sku,
    COALESCE(p.nom, t.sku::TEXT)                         AS product_name,
    CASE
        WHEN p.flag_terminal    THEN 'TERMINAL'
        WHEN p.flag_forfait     THEN 'FORFAIT'
        WHEN p.flag_sim         THEN 'SIM'
        WHEN p.flag_recharge    THEN 'RECHARGE'
        WHEN p.categorie = '70' THEN 'ACCESSOIRE'
        ELSE                         'AUTRE'
    END                                                  AS category,
    t.date_only                                          AS record_date,
    SUM(t.quantity)                                      AS quantity_sold,
    SUM(t.lig_ttc)                                       AS revenue,
    ROUND(SUM(t.lig_ttc) / NULLIF(SUM(t.quantity), 0), 4) AS unit_price,
    -- Promo flag
    EXISTS (
        SELECT 1 FROM inventory.promotions pr
        WHERE pr.start_date <= t.date_only
          AND pr.end_date   >= t.date_only
          AND (pr.sku = t.sku OR LOWER(pr.scope) IN ('store','all_stores','all'))
    )                                                    AS is_promo,
    (
        SELECT pr.promo_type FROM inventory.promotions pr
        WHERE pr.start_date <= t.date_only
          AND pr.end_date   >= t.date_only
          AND (pr.sku = t.sku OR LOWER(pr.scope) IN ('store','all_stores','all'))
        ORDER BY pr.discount_pct DESC LIMIT 1
    )                                                    AS promo_type,
    -- Event market actif ce jour (priorite : EXTREME > HIGH > MEDIUM > LOW)
    (
        SELECT me.event_name FROM market.events me
        WHERE me.start_date <= t.date_only AND me.end_date >= t.date_only
        ORDER BY CASE me.intensite WHEN 'EXTREME' THEN 1 WHEN 'HIGH' THEN 2
                                   WHEN 'MEDIUM' THEN 3 ELSE 4 END LIMIT 1
    )                                                    AS event_name,
    (
        SELECT me.event_type FROM market.events me
        WHERE me.start_date <= t.date_only AND me.end_date >= t.date_only
        ORDER BY CASE me.intensite WHEN 'EXTREME' THEN 1 WHEN 'HIGH' THEN 2
                                   WHEN 'MEDIUM' THEN 3 ELSE 4 END LIMIT 1
    )                                                    AS event_type,
    -- Saison telco tunisien
    CASE EXTRACT(MONTH FROM t.date_only)::INTEGER
        WHEN 2  THEN 'RAMADAN'
        WHEN 3  THEN 'RAMADAN'
        WHEN 4  THEN 'EID_PRINTEMPS'
        WHEN 5  THEN 'PRINTEMPS'
        WHEN 6  THEN 'EID_ETE'
        WHEN 7  THEN 'ETE'
        WHEN 8  THEN 'ETE'
        WHEN 9  THEN 'RENTREE'
        WHEN 10 THEN 'AUTOMNE'
        WHEN 11 THEN 'PRE_SOLDES'
        WHEN 12 THEN 'SOLDES_HIVER'
        ELSE         'HIVER'
    END                                                  AS season,
    -- Flags event
    EXISTS (
        SELECT 1 FROM market.events me
        WHERE me.start_date <= t.date_only AND me.end_date >= t.date_only
          AND me.intensite IN ('HIGH','EXTREME')
    )                                                    AS is_event_day,
    (
        SELECT me.intensite FROM market.events me
        WHERE me.start_date <= t.date_only AND me.end_date >= t.date_only
        ORDER BY CASE me.intensite WHEN 'EXTREME' THEN 1 WHEN 'HIGH' THEN 2
                                   WHEN 'MEDIUM' THEN 3 ELSE 4 END LIMIT 1
    )                                                    AS event_intensite,
    -- Uplift factor depuis market.seasonal_patterns (mois + jour semaine)
    COALESCE(
        (
            SELECT sp.facteur_demande
            FROM market.seasonal_patterns sp
            WHERE sp.mois = EXTRACT(MONTH FROM t.date_only)::INTEGER
              AND sp.categorie = CASE
                  WHEN p.flag_terminal    THEN 'TERMINAL'
                  WHEN p.flag_forfait     THEN 'FORFAIT'
                  WHEN p.flag_sim         THEN 'SIM'
                  WHEN p.flag_recharge    THEN 'RECHARGE'
                  WHEN p.categorie = '70' THEN 'ACCESSOIRE'
                  ELSE NULL
              END
              AND (sp.jour_semaine IS NULL
                OR sp.jour_semaine = EXTRACT(DOW FROM t.date_only)::INTEGER)
            ORDER BY sp.jour_semaine NULLS LAST, sp.facteur_demande DESC
            LIMIT 1
        ),
        1.0
    )                                                    AS uplift_factor,
    -- Dimensions temporelles
    EXTRACT(DOW  FROM t.date_only)::SMALLINT IN (0, 6) AS is_weekend,
    EXTRACT(DOW  FROM t.date_only)::SMALLINT           AS day_of_week,
    EXTRACT(WEEK FROM t.date_only)::SMALLINT           AS week_of_year,
    EXTRACT(MONTH FROM t.date_only)::SMALLINT          AS month_num,
    EXTRACT(YEAR  FROM t.date_only)::SMALLINT          AS year_num
FROM sales.transactions t
LEFT JOIN sales.produits p ON p.sku = t.sku
WHERE t.quantity > 0
GROUP BY
    t.store_id, t.sku, p.nom, p.flag_terminal, p.flag_forfait,
    p.flag_sim, p.flag_recharge, p.categorie, t.date_only;

SELECT 'sales_history journalier rebuilt: ' || COUNT(*) || ' lignes' FROM inventory.sales_history;
SELECT 'jours_uniques: ' || COUNT(DISTINCT record_date) FROM inventory.sales_history;
SELECT 'avec_event: ' || COUNT(*) FILTER(WHERE event_name IS NOT NULL) FROM inventory.sales_history;
SELECT 'avec_promo: ' || COUNT(*) FILTER(WHERE is_promo) FROM inventory.sales_history;
SELECT 'is_event_day HIGH+: ' || COUNT(*) FILTER(WHERE is_event_day) FROM inventory.sales_history;
SELECT 'debut: ' || MIN(record_date)::TEXT || ' | fin: ' || MAX(record_date)::TEXT FROM inventory.sales_history;
