-- ── 1. ENRICH sales.produits ─────────────────────────────────────────────────
UPDATE sales.produits SET
    flag_terminal = (categorie = '50'),
    flag_forfait  = (categorie IN ('88','80')),
    flag_sim      = (categorie IN ('20','40')),
    flag_recharge = (categorie = '30'),
    flag_5g = (nom ILIKE '%5G%' OR nom ILIKE '%5 G%' OR nom ILIKE '%Fixe Jdid%'),
    flag_4g = (nom ILIKE '%4G%' OR nom ILIKE '%Box%' OR nom ILIKE '%MiFi%' OR nom ILIKE '%MIFI%'),
    serialisable = (categorie = '50' AND actif = TRUE),
    stockable = (categorie NOT IN ('88','80','99','90'));

-- brands
UPDATE sales.produits SET marque='Samsung'  WHERE nom ILIKE '%SAMSUNG%' AND marque IS NULL;
UPDATE sales.produits SET marque='Apple'    WHERE (nom ILIKE '%APPLE%' OR nom ILIKE '%IPHONE%') AND marque IS NULL;
UPDATE sales.produits SET marque='Nokia'    WHERE nom ILIKE '%NOKIA%' AND marque IS NULL;
UPDATE sales.produits SET marque='Xiaomi'   WHERE (nom ILIKE '%XIAOMI%' OR nom ILIKE '%REDMI%' OR nom ILIKE '%POCO%') AND marque IS NULL;
UPDATE sales.produits SET marque='Huawei'   WHERE nom ILIKE '%HUAWEI%' AND marque IS NULL;
UPDATE sales.produits SET marque='Honor'    WHERE nom ILIKE '%HONOR%' AND marque IS NULL;
UPDATE sales.produits SET marque='Tecno'    WHERE (nom ILIKE '%TECNO%' OR nom ILIKE '%SPARK%') AND marque IS NULL;
UPDATE sales.produits SET marque='Infinix'  WHERE nom ILIKE '%INFINIX%' AND marque IS NULL;
UPDATE sales.produits SET marque='Mibro'    WHERE nom ILIKE '%MIBRO%' AND marque IS NULL;
UPDATE sales.produits SET marque='Oraimo'   WHERE nom ILIKE '%ORAIMO%' AND marque IS NULL;
UPDATE sales.produits SET marque='Motorola' WHERE nom ILIKE '%MOTOROLA%' AND marque IS NULL;
UPDATE sales.produits SET marque='Alcatel'  WHERE nom ILIKE '%ALCATEL%' AND marque IS NULL;

-- compute margin (pa_ht + marge_pct_calc)
UPDATE sales.produits SET
    pa_ht = CASE
        WHEN categorie = '50'         THEN ROUND(prix_ht * 0.80, 4)
        WHEN categorie IN ('88','80') THEN ROUND(prix_ht * 0.30, 4)
        WHEN categorie IN ('20','40') THEN ROUND(prix_ht * 0.95, 4)
        WHEN categorie = '30'         THEN ROUND(prix_ht * 0.97, 4)
        WHEN categorie = '70'         THEN ROUND(prix_ht * 0.55, 4)
        WHEN categorie = '32'         THEN ROUND(prix_ht * 0.60, 4)
        WHEN categorie = '10'         THEN ROUND(prix_ht * 0.40, 4)
        ELSE ROUND(prix_ht * 0.75, 4)
    END,
    marge_pct_calc = CASE
        WHEN categorie = '50'         THEN 20.0
        WHEN categorie IN ('88','80') THEN 70.0
        WHEN categorie IN ('20','40') THEN 5.0
        WHEN categorie = '30'         THEN 3.0
        WHEN categorie = '70'         THEN 45.0
        WHEN categorie = '32'         THEN 40.0
        WHEN categorie = '10'         THEN 60.0
        ELSE 25.0
    END
WHERE prix_ht IS NOT NULL AND prix_ht > 0 AND pa_ht IS NULL;

-- gamme libelle
UPDATE sales.produits SET gamme_libelle='TERMINAL'          WHERE categorie='50'          AND gamme_libelle IS NULL;
UPDATE sales.produits SET gamme_libelle='FORFAIT'           WHERE categorie='88'          AND gamme_libelle IS NULL;
UPDATE sales.produits SET gamme_libelle='FORFAIT_BOX'       WHERE categorie='80'          AND gamme_libelle IS NULL;
UPDATE sales.produits SET gamme_libelle='SIM_KIT'           WHERE categorie='20'          AND gamme_libelle IS NULL;
UPDATE sales.produits SET gamme_libelle='SIM_SERVICE'       WHERE categorie='40'          AND gamme_libelle IS NULL;
UPDATE sales.produits SET gamme_libelle='RECHARGE'          WHERE categorie='30'          AND gamme_libelle IS NULL;
UPDATE sales.produits SET gamme_libelle='ACCESSOIRE'        WHERE categorie='70'          AND gamme_libelle IS NULL;
UPDATE sales.produits SET gamme_libelle='ACCESSOIRE_PREMIUM' WHERE categorie='32'         AND gamme_libelle IS NULL;
UPDATE sales.produits SET gamme_libelle='SERVICE'           WHERE categorie='99'          AND gamme_libelle IS NULL;
UPDATE sales.produits SET gamme_libelle='SERVICE_DIGITAL'   WHERE categorie='90'          AND gamme_libelle IS NULL;
UPDATE sales.produits SET gamme_libelle='BUSINESS'          WHERE categorie='10'          AND gamme_libelle IS NULL;
UPDATE sales.produits SET gamme_libelle='TELECOM_DIRECT'    WHERE categorie='TELECOM'     AND gamme_libelle IS NULL;

-- lead times
UPDATE sales.produits SET
    lead_time_days = CASE
        WHEN categorie='50' AND marque='Apple'   THEN 21
        WHEN categorie='50' AND marque='Samsung' THEN 14
        WHEN categorie='50'                      THEN 12
        WHEN categorie IN ('88','80')            THEN 0
        WHEN categorie IN ('20','40')            THEN 5
        WHEN categorie = '30'                    THEN 3
        ELSE 10
    END,
    lead_time_std = CASE
        WHEN categorie='50' AND marque='Apple' THEN 5
        WHEN categorie='50'                    THEN 3
        ELSE 2
    END,
    moq = CASE
        WHEN categorie='50' AND prix_ttc > 500 THEN 5
        WHEN categorie='50'                    THEN 10
        WHEN categorie IN ('20','40')          THEN 100
        WHEN categorie = '30'                  THEN 50
        ELSE 20
    END,
    lifecycle_stage = CASE
        WHEN actif = FALSE THEN 'discontinued'
        ELSE 'mature'
    END
WHERE lead_time_days = 14 AND categorie IS NOT NULL;

SELECT 'produits enriched: flags=' ||
    COUNT(*) FILTER (WHERE flag_terminal=TRUE) || ' terminals, ' ||
    COUNT(*) FILTER (WHERE flag_forfait=TRUE) || ' forfaits, ' ||
    COUNT(*) FILTER (WHERE marge_pct_calc IS NOT NULL) || ' with margin'
FROM sales.produits;

-- ── 2. ENRICH sales.boutiques ─────────────────────────────────────────────────
UPDATE sales.boutiques SET
    type_boutique = LEFT(store_id, 1),
    canal = CASE WHEN LEFT(store_id,1)='T' THEN 'ONLINE'
                 WHEN LEFT(store_id,1)='C' THEN 'B2B'
                 ELSE 'PHYSIQUE' END,
    is_officielle = (LEFT(store_id,1) = 'M')
WHERE type_boutique IS NULL;

UPDATE sales.boutiques SET wilaya = CASE region
    WHEN 'Grand Tunis' THEN 'Tunis'
    WHEN 'Sahel'       THEN 'Sousse'
    WHEN 'Cap Bon'     THEN 'Nabeul'
    WHEN 'Centre'      THEN 'Kairouan'
    WHEN 'Sud'         THEN 'Sfax'
    WHEN 'Sud-Ouest'   THEN 'Kasserine'
    WHEN 'Nord'        THEN 'Bizerte'
    ELSE 'Tunis'
END WHERE wilaya IS NULL;

-- specific coordinates for known stores
UPDATE sales.boutiques SET latitude=36.8892, longitude=10.3211, zone_commerciale='La Marsa', date_ouverture='2004-03-01'::DATE WHERE store_id='M01';
UPDATE sales.boutiques SET latitude=36.7983, longitude=10.1718, zone_commerciale='Tunis Centre', date_ouverture='2004-04-04'::DATE WHERE store_id='M02';
UPDATE sales.boutiques SET latitude=34.7406, longitude=10.7603, zone_commerciale='Sfax Ville', date_ouverture='2004-06-10'::DATE WHERE store_id='M03';
UPDATE sales.boutiques SET latitude=35.8256, longitude=10.6369, zone_commerciale='Sousse Centre', date_ouverture='2004-07-08'::DATE WHERE store_id='M04';
UPDATE sales.boutiques SET latitude=36.8511, longitude=10.2272, zone_commerciale='Aéroport Tunis-Carthage', date_ouverture='2005-11-11'::DATE WHERE store_id='M10';
UPDATE sales.boutiques SET latitude=36.8403, longitude=10.2189, zone_commerciale='Tunisia Mall Lac2' WHERE store_id='I63';
UPDATE sales.boutiques SET latitude=36.8089, longitude=10.2356, zone_commerciale='Géant Tunis' WHERE store_id='I38';
UPDATE sales.boutiques SET latitude=36.8892, longitude=10.3211, zone_commerciale='Carrefour La Marsa' WHERE store_id='S47';

SELECT 'boutiques enriched: ' || COUNT(*) || ' with wilaya, ' ||
    COUNT(*) FILTER (WHERE latitude IS NOT NULL) || ' with coordinates'
FROM sales.boutiques;

-- ── 3. ENRICH sales.agents ─────────────────────────────────────────────────
UPDATE sales.agents SET
    date_embauche = CASE performance_level
        WHEN 'senior'    THEN (CURRENT_DATE - (RANDOM()*1825+730)::INTEGER * INTERVAL '1 day')::DATE
        WHEN 'confirmed' THEN (CURRENT_DATE - (RANDOM()*730+365)::INTEGER * INTERVAL '1 day')::DATE
        WHEN 'junior'    THEN (CURRENT_DATE - (RANDOM()*180+30)::INTEGER * INTERVAL '1 day')::DATE
        ELSE              (CURRENT_DATE - (RANDOM()*365+180)::INTEGER * INTERVAL '1 day')::DATE
    END,
    niveau_certification = CASE performance_level
        WHEN 'senior'    THEN 4 + (RANDOM()*1)::INTEGER
        WHEN 'confirmed' THEN 3
        WHEN 'junior'    THEN 1 + (RANDOM()*1)::INTEGER
        ELSE 2
    END,
    quota_mensuel_ca = CASE performance_level
        WHEN 'senior'    THEN (12000 + RANDOM()*3000)::NUMERIC(10,2)
        WHEN 'confirmed' THEN (8000 + RANDOM()*2000)::NUMERIC(10,2)
        WHEN 'junior'    THEN (4000 + RANDOM()*1500)::NUMERIC(10,2)
        ELSE              (6000 + RANDOM()*2000)::NUMERIC(10,2)
    END,
    quota_activations = CASE performance_level
        WHEN 'senior'    THEN 90 + (RANDOM()*30)::INTEGER
        WHEN 'confirmed' THEN 60 + (RANDOM()*20)::INTEGER
        WHEN 'junior'    THEN 30 + (RANDOM()*20)::INTEGER
        ELSE              50 + (RANDOM()*20)::INTEGER
    END,
    quota_postpaye = CASE performance_level
        WHEN 'senior'    THEN 15
        WHEN 'confirmed' THEN 10
        WHEN 'junior'    THEN 5
        ELSE 8
    END,
    anciennete_mois = CASE performance_level
        WHEN 'senior'    THEN 48 + (RANDOM()*24)::INTEGER
        WHEN 'confirmed' THEN 24 + (RANDOM()*12)::INTEGER
        WHEN 'junior'    THEN 3 + (RANDOM()*9)::INTEGER
        ELSE              12 + (RANDOM()*12)::INTEGER
    END,
    coach_score = CASE performance_level
        WHEN 'senior'    THEN (75 + RANDOM()*25)::NUMERIC(5,2)
        WHEN 'confirmed' THEN (55 + RANDOM()*25)::NUMERIC(5,2)
        WHEN 'junior'    THEN (25 + RANDOM()*30)::NUMERIC(5,2)
        ELSE              (40 + RANDOM()*30)::NUMERIC(5,2)
    END
WHERE date_embauche IS NULL;

SELECT 'agents enriched: ' || COUNT(*) || ' with dates/quotas' FROM sales.agents WHERE date_embauche IS NOT NULL;

-- ── 4. CUSTOMER SEGMENTS ─────────────────────────────────────────────────────
INSERT INTO customer.segments VALUES
('RESI_STANDARD', 'Résidentiel Standard',
 'Client grand public, usage voice + data modéré',
 28.0, 8.0, 0.0420, 36, 'BOUTIQUE',
 '["FORFAIT_PREPAYE","RECHARGE"]'::JSONB, 5800000, 32.0, TRUE),
('RESI_DATA', 'Résidentiel Data-First',
 'Fort consommateur data, probable millennial',
 55.0, 15.0, 0.0280, 48, 'DIGITAL',
 '["FORFAIT_DATA","BOX_4G","TERMINAL_MIDRANGE"]'::JSONB, 1800000, 9.9, TRUE),
('RESI_PREMIUM', 'Résidentiel Premium',
 'Client postpayé, smartphone haut de gamme',
 120.0, 30.0, 0.0180, 60, 'BOUTIQUE',
 '["TERMINAL_PREMIUM","FORFAIT_POSTPAYE","ASSURANCE"]'::JSONB, 450000, 2.5, TRUE),
('CORPO_PME', 'Corporate PME',
 'PME lignes multiples, account manager boutique',
 200.0, 50.0, 0.0150, 24, 'B2B',
 '["FORFAIT_POSTPAYE_CORPO","TERMINAUX_FLOTTE"]'::JSONB, 85000, 0.5, TRUE),
('CORPO_GRAND', 'Grand Compte Corporate',
 'Grande entreprise, contrat annuel dédié',
 800.0, 200.0, 0.0080, 36, 'B2B',
 '["OFFRE_SUR_MESURE","FLOTTE_TERMINAUX"]'::JSONB, 12000, 0.07, TRUE),
('SENIOR', 'Senior 55+',
 'Usage vocal principalement, besoin assistance',
 18.0, 5.0, 0.0350, 30, 'BOUTIQUE',
 '["FORFAIT_BASIQUE","RECHARGE","TERMINAL_FEATURE"]'::JSONB, 650000, 3.6, TRUE),
('ETUDIANT', 'Étudiant 18-25 ans',
 'Budget serré, fort usage data mobile',
 30.0, 10.0, 0.0380, 24, 'DIGITAL',
 '["FORFAIT_ETUDIANT","TERMINAL_ENTREE_GAMME"]'::JSONB, 920000, 5.1, TRUE),
('ROAMING', 'Client Diaspora / Roaming',
 'Tunisien résidant à l''étranger, achat SIM lors des visites',
 45.0, 20.0, 0.0600, 12, 'BOUTIQUE',
 '["SIM_PREPAYEE","RECHARGE","TERMINAL"]'::JSONB, 180000, 1.0, TRUE)
ON CONFLICT (segment_id) DO NOTHING;

SELECT 'segments: ' || COUNT(*) FROM customer.segments;

-- ── 5. COMPUTE REORDER PARAMS ─────────────────────────────────────────────────
INSERT INTO supply.reorder_params
    (sku, store_id, demande_moy_jour, demande_std_jour,
     lead_time_moy, lead_time_std, stock_securite, point_commande, eoq,
     niveau_service, jours_stock_cible)
SELECT
    t.sku,
    t.store_id,
    AVG(daily.qty)::NUMERIC(10,4),
    COALESCE(STDDEV(daily.qty),0)::NUMERIC(10,4),
    COALESCE(p.lead_time_days, 14)::NUMERIC(8,2),
    COALESCE(p.lead_time_std, 3)::NUMERIC(8,2),
    CEIL(1.65 * COALESCE(STDDEV(daily.qty),0) * SQRT(COALESCE(p.lead_time_days,14))),
    CEIL(AVG(daily.qty) * COALESCE(p.lead_time_days,14) +
         1.65 * COALESCE(STDDEV(daily.qty),0) * SQRT(COALESCE(p.lead_time_days,14))),
    GREATEST(1, CEIL(SQRT(
        2 * AVG(daily.qty) * 365 * COALESCE(p.order_cost,50) /
        (GREATEST(COALESCE(p.prix_ttc,100), 1) * 0.20)
    ))),
    0.95,
    30
FROM (
    SELECT store_id, sku, date_only AS day, SUM(quantity) AS qty
    FROM sales.transactions
    WHERE transaction_date >= CURRENT_DATE - INTERVAL '90 days' AND quantity > 0
    GROUP BY store_id, sku, date_only
) daily
JOIN (SELECT DISTINCT store_id, sku FROM sales.transactions) t
    ON t.store_id=daily.store_id AND t.sku=daily.sku
JOIN sales.produits p ON p.sku=t.sku
WHERE p.stockable = TRUE
GROUP BY t.sku, t.store_id, p.lead_time_days, p.lead_time_std, p.order_cost, p.prix_ttc
HAVING AVG(daily.qty) > 0
ON CONFLICT (sku, store_id) DO UPDATE SET
    demande_moy_jour = EXCLUDED.demande_moy_jour,
    stock_securite   = EXCLUDED.stock_securite,
    point_commande   = EXCLUDED.point_commande,
    eoq              = EXCLUDED.eoq,
    derniere_maj     = NOW();

SELECT 'reorder_params: ' || COUNT(*) || ' (sku,store) pairs computed' FROM supply.reorder_params;

-- ── 6. BACKFILL STOCK MOVEMENTS (last 14 days) ────────────────────────────────
INSERT INTO supply.stock_movements
    (sku, store_id, type_mouvement, quantite, reference_type, agent_id, date_mouvement)
SELECT
    t.sku, t.store_id, 'VENTE', -t.quantity, 'VENTE', t.agent_id, t.transaction_date
FROM sales.transactions t
JOIN sales.produits p ON p.sku=t.sku
WHERE p.stockable=TRUE
  AND t.transaction_date >= CURRENT_DATE - INTERVAL '14 days'
  AND t.quantity > 0
ON CONFLICT DO NOTHING;

SELECT 'stock_movements: ' || COUNT(*) || ' total rows' FROM supply.stock_movements;

SELECT 'SEED_DATA COMPLETE' AS status;
