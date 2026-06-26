-- Couverture temporelle des données
SELECT 'sales.transactions' AS source,
       MIN(date_only) AS debut, MAX(date_only) AS fin,
       COUNT(*) AS lignes,
       COUNT(DISTINCT date_only) AS jours,
       COUNT(DISTINCT store_id) AS boutiques,
       COUNT(DISTINCT sku) AS skus
FROM sales.transactions
UNION ALL
SELECT 'inventory.sales_history',
       MIN(record_date), MAX(record_date),
       COUNT(*), COUNT(DISTINCT record_date),
       COUNT(DISTINCT store_id), COUNT(DISTINCT sku)
FROM inventory.sales_history
UNION ALL
SELECT 'inventory.stock_history',
       MIN(record_date), MAX(record_date),
       COUNT(*), COUNT(DISTINCT record_date),
       COUNT(DISTINCT store_id), COUNT(DISTINCT sku)
FROM inventory.stock_history;

-- Distribution mensuelle des transactions
SELECT TO_CHAR(date_only, 'YYYY-MM') AS mois,
       COUNT(*) AS lignes,
       ROUND(SUM(lig_ttc)) AS ca_total,
       COUNT(DISTINCT store_id) AS boutiques
FROM sales.transactions
GROUP BY TO_CHAR(date_only, 'YYYY-MM')
ORDER BY mois;

-- Qualite des donnees : colonnes enrichies dans sales_history
SELECT
    COUNT(*) AS total,
    SUM(CASE WHEN is_promo THEN 1 ELSE 0 END) AS avec_promo,
    SUM(CASE WHEN event_name IS NOT NULL AND event_name != '' THEN 1 ELSE 0 END) AS avec_event,
    SUM(CASE WHEN season IS NOT NULL AND season != '' THEN 1 ELSE 0 END) AS avec_saison,
    SUM(CASE WHEN event_type IS NOT NULL AND event_type != '' THEN 1 ELSE 0 END) AS avec_event_type
FROM inventory.sales_history;
