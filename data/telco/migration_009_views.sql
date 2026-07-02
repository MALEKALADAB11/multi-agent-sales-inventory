-- ═══════════════════════════════════════════════════════════════════════════
-- Migration 009 — Vues manquantes référencées par les agents
-- Prérequis : inventory.stock_levels (migration Alembic 001_inv_initial),
--             sales.produits (sales schema initial)
-- Note: inventory.stock_levels.sku = INTEGER, sales.produits.sku = INTEGER
--       Colonnes stock réelles: quantity, quantity_available, quantity_reserved
-- ═══════════════════════════════════════════════════════════════════════════

-- ── sales.vw_stock_enriched ───────────────────────────────────────────────
-- Vue joignant inventory.stock_levels avec sales.produits.
-- Utilisée par : react_tools.get_stock_alerts, stratege/nodes.py
DROP VIEW IF EXISTS sales.vw_stock_enriched;
CREATE VIEW sales.vw_stock_enriched AS
SELECT
    sl.store_id,
    sl.sku,
    COALESCE(p.nom, sl.sku::text)               AS product_name,
    p.categorie,
    p.famille                                    AS gamme_libelle,
    COALESCE(p.prix_ttc, 0)                     AS prix_ttc,
    COALESCE(p.marge_pct, 0)                    AS marge_pct,
    p.flag_terminal,
    p.flag_forfait,
    COALESCE(sl.quantity_available, sl.quantity, 0) AS stock_dispo,
    COALESCE(sl.quantity_reserved, 0)           AS stock_in_transit,
    sl.last_updated,
    CASE
        WHEN COALESCE(sl.quantity_available, sl.quantity, 0) = 0    THEN 'rupture'
        WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 3   THEN 'critical'
        WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 10  THEN 'warning'
        ELSE 'ok'
    END AS stock_risk
FROM inventory.stock_levels sl
LEFT JOIN sales.produits p ON p.sku = sl.sku;

-- ── sales.vw_ca_par_boutique ──────────────────────────────────────────────
-- Vue CA journalier par boutique depuis transactions (historique).
-- Utilisée par : postgres_provider.fetch_boutiques
-- DROP d'abord pour éviter l'erreur "ne peut pas modifier le nom de la colonne"
DROP VIEW IF EXISTS sales.vw_ca_par_boutique;
CREATE VIEW sales.vw_ca_par_boutique AS
SELECT
    store_id,
    date_only,
    SUM(lig_ttc)       AS ca_total,
    COUNT(*)           AS nb_transactions,
    AVG(lig_ttc)       AS avg_ticket
FROM sales.transactions
WHERE lig_ttc > 0
GROUP BY store_id, date_only;

-- ── sales.vw_top_products ─────────────────────────────────────────────────
-- Top produits par CA sur les 30 derniers jours, par boutique.
DROP VIEW IF EXISTS sales.vw_top_products;
CREATE VIEW sales.vw_top_products AS
SELECT
    t.store_id,
    t.sku                        AS sku,
    p.nom                        AS product_name,
    p.categorie,
    p.prix_ttc,
    SUM(t.lig_ttc)               AS ca_30j,
    SUM(t.quantity)              AS qty_30j,
    COUNT(DISTINCT t.date_only)  AS nb_jours_vendus
FROM sales.transactions t
LEFT JOIN sales.produits p ON p.sku = t.sku
WHERE t.date_only >= CURRENT_DATE - INTERVAL '30 days'
  AND t.lig_ttc > 0
GROUP BY t.store_id, t.sku, p.nom, p.categorie, p.prix_ttc;

-- ── sales.vw_performance_agent ────────────────────────────────────────────
-- Performance des agents sur les 30 derniers jours.
DROP VIEW IF EXISTS sales.vw_performance_agent;
CREATE VIEW sales.vw_performance_agent AS
SELECT
    t.store_id,
    t.agent_id,
    a.agent_name,
    a.performance_level,
    SUM(t.lig_ttc)                 AS ca_30j,
    COUNT(DISTINCT t.date_only)    AS jours_actifs,
    COUNT(*)                       AS nb_transactions,
    ROUND(SUM(t.lig_ttc) / NULLIF(COUNT(DISTINCT t.date_only), 0), 2) AS ca_par_jour
FROM sales.transactions t
LEFT JOIN sales.agents a ON a.agent_id = t.agent_id
WHERE t.date_only >= CURRENT_DATE - INTERVAL '30 days'
  AND t.lig_ttc > 0
GROUP BY t.store_id, t.agent_id, a.agent_name, a.performance_level;

SELECT 'Migration 009 OK — Vues créées : vw_stock_enriched, vw_ca_par_boutique, vw_top_products, vw_performance_agent' AS status;
