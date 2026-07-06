-- Migration 012: stop recommendations from writing to physical stock
--
-- trg_sync_stock_on_reco (AFTER INSERT ON inventory.recommendations) added
-- suggested_quantity straight into stock_levels.quantity the instant a
-- recommendation row was inserted -- before any human approval, before any
-- purchase order, before any goods were physically received. A recommendation
-- is "the AI thinks we should order N units", not a physical stock event.
-- Evidence: SKU 2020030/I63 had 35 recommendation rows summing 12,306
-- suggested units while real sales only removed 53 units from stock.
--
-- Physical stock should only move on a physical event: a sale
-- (trg_sync_stock_on_sale, unaffected by this migration, stays) or a
-- purchase-order receipt (future work, hooked into supply_repo.update_status()
-- on the RECU/RECU_PARTIEL transition -- not built yet).

DROP TRIGGER IF EXISTS trg_sync_stock_on_reco ON inventory.recommendations;
DROP FUNCTION IF EXISTS public.sync_stock_on_recommendation();
