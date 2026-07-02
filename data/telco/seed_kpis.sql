-- ── Compute agent_kpi_daily from sales.transactions ─────────────────────────
-- Covers last 6 months for coaching agents
INSERT INTO agent_kpi_daily
    (agent_id, store_id, kpi_date,
     ca_realise, ca_cible,
     nb_transactions, nb_clients_uniques, panier_moyen,
     nb_forfaits, nb_terminaux, nb_sim_activations, nb_recharges, nb_accessoires,
     nb_postpaye,
     ca_terminaux, ca_forfaits, ca_sim, ca_recharges, ca_accessoires)
SELECT
    t.agent_id,
    t.store_id,
    t.date_only,
    COALESCE(SUM(t.lig_ttc), 0),
    COALESCE(MAX(o.objectif_ca) / 26.0, NULL),
    COUNT(DISTINCT t.id),
    COUNT(DISTINCT t.heure),
    ROUND(SUM(t.lig_ttc) / NULLIF(COUNT(DISTINCT t.id),0), 2),
    COUNT(*) FILTER (WHERE p.flag_forfait=TRUE),
    COUNT(*) FILTER (WHERE p.flag_terminal=TRUE),
    COUNT(*) FILTER (WHERE p.flag_sim=TRUE),
    COUNT(*) FILTER (WHERE p.flag_recharge=TRUE),
    COUNT(*) FILTER (WHERE p.categorie='70'),
    COUNT(*) FILTER (WHERE p.flag_forfait=TRUE AND p.nom ILIKE '%POSTPAYE%'),
    COALESCE(SUM(t.lig_ttc) FILTER (WHERE p.flag_terminal=TRUE),0),
    COALESCE(SUM(t.lig_ttc) FILTER (WHERE p.flag_forfait=TRUE),0),
    COALESCE(SUM(t.lig_ttc) FILTER (WHERE p.flag_sim=TRUE),0),
    COALESCE(SUM(t.lig_ttc) FILTER (WHERE p.flag_recharge=TRUE),0),
    COALESCE(SUM(t.lig_ttc) FILTER (WHERE p.categorie='70'),0)
FROM sales.transactions t
JOIN sales.produits p ON p.sku=t.sku
LEFT JOIN sales.objectifs o
    ON o.store_id=t.store_id AND o.agent_id=t.agent_id AND o.date_objectif=t.date_only
WHERE t.date_only >= CURRENT_DATE - INTERVAL '180 days'
  AND t.quantity > 0
GROUP BY t.agent_id, t.store_id, t.date_only
ON CONFLICT (agent_id, kpi_date) DO UPDATE SET
    ca_realise=EXCLUDED.ca_realise,
    nb_transactions=EXCLUDED.nb_transactions,
    nb_forfaits=EXCLUDED.nb_forfaits,
    nb_terminaux=EXCLUDED.nb_terminaux,
    nb_sim_activations=EXCLUDED.nb_sim_activations,
    nb_recharges=EXCLUDED.nb_recharges,
    nb_postpaye=EXCLUDED.nb_postpaye,
    ca_terminaux=EXCLUDED.ca_terminaux,
    ca_forfaits=EXCLUDED.ca_forfaits;

SELECT 'agent_kpi_daily: ' || COUNT(*) || ' rows (last 180 days)' FROM agent_kpi_daily;

-- ── Compute gap_ca_pct ─────────────────────────────────────────────────────
UPDATE agent_kpi_daily SET
    gap_ca_pct = CASE
        WHEN ca_cible IS NOT NULL AND ca_cible > 0
        THEN ROUND(((ca_realise - ca_cible) / ca_cible) * 100, 2)
        ELSE NULL
    END;

-- ── Compute urgency_level ─────────────────────────────────────────────────
UPDATE agent_kpi_daily SET urgency_level =
    CASE
        WHEN gap_ca_pct < -40 THEN 'CRITIQUE'
        WHEN gap_ca_pct < -20 THEN 'ELEVE'
        WHEN gap_ca_pct < -10 THEN 'MODERE'
        ELSE 'OK'
    END
WHERE gap_ca_pct IS NOT NULL;

-- ── Rank agents within their boutique ────────────────────────────────────
UPDATE agent_kpi_daily a SET rang_boutique = r.rang
FROM (
    SELECT agent_id, kpi_date,
           RANK() OVER (PARTITION BY store_id, kpi_date ORDER BY ca_realise DESC) AS rang
    FROM agent_kpi_daily
    WHERE kpi_date >= CURRENT_DATE - INTERVAL '180 days'
) r
WHERE a.agent_id=r.agent_id AND a.kpi_date=r.kpi_date
  AND a.kpi_date >= CURRENT_DATE - INTERVAL '180 days';

-- ── Compute store_kpi_daily ───────────────────────────────────────────────
INSERT INTO store_kpi_daily
    (store_id, kpi_date,
     ca_realise,
     nb_transactions, nb_clients, panier_moyen_store,
     nb_forfaits, nb_terminaux, nb_sim_activations, nb_recharges,
     nb_postpaye,
     ca_terminaux, ca_forfaits,
     nb_agents_actifs, ca_par_agent)
SELECT
    t.store_id,
    t.date_only,
    COALESCE(SUM(t.lig_ttc), 0),
    COUNT(DISTINCT t.id),
    COUNT(DISTINCT t.heure),
    ROUND(SUM(t.lig_ttc) / NULLIF(COUNT(DISTINCT t.id),0), 2),
    COUNT(*) FILTER (WHERE p.flag_forfait=TRUE),
    COUNT(*) FILTER (WHERE p.flag_terminal=TRUE),
    COUNT(*) FILTER (WHERE p.flag_sim=TRUE),
    COUNT(*) FILTER (WHERE p.flag_recharge=TRUE),
    COUNT(*) FILTER (WHERE p.flag_forfait=TRUE AND p.nom ILIKE '%POSTPAYE%'),
    COALESCE(SUM(t.lig_ttc) FILTER (WHERE p.flag_terminal=TRUE),0),
    COALESCE(SUM(t.lig_ttc) FILTER (WHERE p.flag_forfait=TRUE),0),
    COUNT(DISTINCT t.agent_id),
    ROUND(SUM(t.lig_ttc)/NULLIF(COUNT(DISTINCT t.agent_id),0), 2)
FROM sales.transactions t
JOIN sales.produits p ON p.sku=t.sku
WHERE t.date_only >= CURRENT_DATE - INTERVAL '180 days'
  AND t.quantity > 0
GROUP BY t.store_id, t.date_only
ON CONFLICT (store_id, kpi_date) DO UPDATE SET
    ca_realise=EXCLUDED.ca_realise,
    nb_transactions=EXCLUDED.nb_transactions,
    nb_forfaits=EXCLUDED.nb_forfaits,
    nb_terminaux=EXCLUDED.nb_terminaux,
    nb_sim_activations=EXCLUDED.nb_sim_activations,
    nb_recharges=EXCLUDED.nb_recharges,
    nb_postpaye=EXCLUDED.nb_postpaye,
    ca_terminaux=EXCLUDED.ca_terminaux,
    ca_forfaits=EXCLUDED.ca_forfaits,
    nb_agents_actifs=EXCLUDED.nb_agents_actifs,
    ca_par_agent=EXCLUDED.ca_par_agent;

SELECT 'store_kpi_daily: ' || COUNT(*) || ' rows (last 180 days)' FROM store_kpi_daily;

-- ── Compute cumulative monthly CA ─────────────────────────────────────────
UPDATE store_kpi_daily s SET ca_cumul_mois = m.ca_cumul
FROM (
    SELECT store_id, kpi_date,
           SUM(ca_realise) OVER (
               PARTITION BY store_id, DATE_TRUNC('month', kpi_date)
               ORDER BY kpi_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) AS ca_cumul
    FROM store_kpi_daily
    WHERE kpi_date >= CURRENT_DATE - INTERVAL '180 days'
) m
WHERE s.store_id=m.store_id AND s.kpi_date=m.kpi_date
  AND s.kpi_date >= CURRENT_DATE - INTERVAL '180 days';

-- ── Monthly telco targets from historical data ─────────────────────────────
-- Last 6 months, store level
INSERT INTO telco_targets_monthly
    (store_id, mois, annee, niveau, ca_cible_mensuel,
     activations_totales, activations_postpaye, ventes_terminaux, facteur_saisonnier)
SELECT
    store_id,
    EXTRACT(MONTH FROM target_mois)::INTEGER   AS mois,
    EXTRACT(YEAR  FROM target_mois)::INTEGER   AS annee,
    'BOUTIQUE',
    ROUND(AVG(monthly_ca) * 1.05, 2),
    ROUND(AVG(monthly_sim))::INTEGER,
    GREATEST(1, ROUND(AVG(monthly_postpaye)))::INTEGER,
    ROUND(AVG(monthly_terminaux))::INTEGER,
    CASE EXTRACT(MONTH FROM target_mois)::INTEGER
        WHEN 3 THEN 1.40 WHEN 4 THEN 1.70 WHEN 6 THEN 1.30
        WHEN 9 THEN 1.35 WHEN 12 THEN 1.40 ELSE 1.0
    END
FROM (
    SELECT
        store_id,
        DATE_TRUNC('month', kpi_date) AS target_mois,
        SUM(ca_realise) AS monthly_ca,
        SUM(nb_sim_activations) AS monthly_sim,
        SUM(nb_postpaye) AS monthly_postpaye,
        SUM(nb_terminaux) AS monthly_terminaux
    FROM store_kpi_daily
    WHERE kpi_date >= CURRENT_DATE - INTERVAL '6 months'
    GROUP BY store_id, DATE_TRUNC('month', kpi_date)
) hist
GROUP BY store_id, target_mois
ON CONFLICT DO NOTHING;

SELECT 'telco_targets_monthly: ' || COUNT(*) || ' store targets' FROM telco_targets_monthly;

-- ── Weekly KPI summary ────────────────────────────────────────────────────
INSERT INTO weekly_kpi_summary
    (store_id, agent_id, annee_semaine, semaine_debut, semaine_fin,
     niveau, ca_semaine, nb_transactions, nb_postpaye, nb_terminaux, nb_forfaits,
     panier_moyen, top_categorie, nb_jours_actifs)
SELECT
    store_id, agent_id,
    TO_CHAR(kpi_date, 'IYYY-IW'),
    MIN(kpi_date), MAX(kpi_date),
    'AGENT',
    SUM(ca_realise),
    SUM(nb_transactions),
    SUM(nb_postpaye),
    SUM(nb_terminaux),
    SUM(nb_forfaits),
    CASE WHEN SUM(nb_transactions)>0
         THEN ROUND(SUM(ca_realise)/SUM(nb_transactions),2) ELSE 0 END,
    CASE
        WHEN SUM(nb_terminaux) >= SUM(nb_forfaits) AND SUM(nb_terminaux) >= SUM(nb_recharges) THEN 'TERMINAL'
        WHEN SUM(nb_forfaits) >= SUM(nb_recharges) THEN 'FORFAIT'
        ELSE 'RECHARGE'
    END,
    COUNT(DISTINCT kpi_date)
FROM agent_kpi_daily
WHERE kpi_date >= CURRENT_DATE - INTERVAL '90 days'
GROUP BY store_id, agent_id, TO_CHAR(kpi_date, 'IYYY-IW')
ON CONFLICT DO NOTHING;

SELECT 'weekly_kpi_summary: ' || COUNT(*) || ' weekly rows' FROM weekly_kpi_summary;

-- ── Load market events from CSV via COPY ─────────────────────────────────
-- (CSV import done via Python seed script)

SELECT 'KPI SEED COMPLETE' AS status;
