-- Migration 014: missing FK constraints on sku/store_id
--
-- inventory.alerts, inventory.recommendations, supply.purchase_orders and
-- supply.stock_movements had zero FK constraints on sku/store_id -- pure
-- correlation-by-convention with no DB-level guarantee. Verified live
-- (2026-07-05): 0 orphaned rows against sales.produits/sales.boutiques on
-- all 4 tables x 2 columns -- same reference tables inventory.stock_levels
-- already uses, safe to add without any cleanup.

ALTER TABLE inventory.alerts
  ADD CONSTRAINT fk_alerts_sku   FOREIGN KEY (sku) REFERENCES sales.produits(sku),
  ADD CONSTRAINT fk_alerts_store FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id);

ALTER TABLE inventory.recommendations
  ADD CONSTRAINT fk_reco_sku   FOREIGN KEY (sku) REFERENCES sales.produits(sku),
  ADD CONSTRAINT fk_reco_store FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id);

ALTER TABLE supply.purchase_orders
  ADD CONSTRAINT fk_po_sku   FOREIGN KEY (sku) REFERENCES sales.produits(sku),
  ADD CONSTRAINT fk_po_store FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id);

ALTER TABLE supply.stock_movements
  ADD CONSTRAINT fk_sm_sku   FOREIGN KEY (sku) REFERENCES sales.produits(sku),
  ADD CONSTRAINT fk_sm_store FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id);
