-- Validation qualite serie temporelle pour agent analyste

-- 1. Distribution annuelle + saisonnalite visible
SELECT year_num AS annee,
       month_num AS mois,
       SUM(quantity_sold) AS qte_totale,
       ROUND(AVG(quantity_sold), 2) AS qte_moy_jour,
       ROUND(AVG(uplift_factor), 3) AS uplift_moy,
       COUNT(*) AS nb_lignes,
       SUM(CASE WHEN is_event_day THEN 1 ELSE 0 END) AS jours_event
FROM inventory.sales_history
WHERE category = 'TERMINAL'
GROUP BY year_num, month_num
ORDER BY year_num, month_num;

-- 2. Saisonnalite par categorie (facteur vs mois de base Jan)
SELECT category,
       month_num AS mois,
       ROUND(AVG(quantity_sold), 2) AS qte_moy,
       ROUND(AVG(uplift_factor), 3) AS uplift_moy,
       ROUND(
           AVG(quantity_sold) / NULLIF(
               AVG(AVG(quantity_sold)) OVER (PARTITION BY category),
               0
           ), 3
       ) AS ratio_vs_moyenne
FROM inventory.sales_history
GROUP BY category, month_num
ORDER BY category, month_num;

-- 3. Impact events sur demande
SELECT event_type,
       ROUND(AVG(quantity_sold), 2) AS qte_event,
       ROUND(AVG(uplift_factor), 3) AS uplift_moy,
       COUNT(*) AS nb_lignes,
       COUNT(DISTINCT record_date) AS jours
FROM inventory.sales_history
WHERE event_type IS NOT NULL
GROUP BY event_type
ORDER BY qte_event DESC;

-- 4. Densité de couverture (% jours avec données par year/store)
SELECT year_num,
       COUNT(DISTINCT store_id) AS boutiques,
       COUNT(DISTINCT record_date) AS jours_couverts,
       COUNT(DISTINCT sku) AS skus,
       COUNT(*) AS total_lignes
FROM inventory.sales_history
GROUP BY year_num
ORDER BY year_num;

-- 5. Stock history : toujours mensuel -> alerter
SELECT 'stock_history jours uniques: ' || COUNT(DISTINCT record_date) AS info
FROM inventory.stock_history;
