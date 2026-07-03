-- Migration 011: Kanban board — pont recommendation -> purchase_order
-- Ajoute la colonne manquante recommendation_id sur supply.purchase_orders
-- (la table existe depuis migration_005.sql mais sans lien vers
-- inventory.recommendations). Idempotent — sans risque, table vide à ce jour.

ALTER TABLE supply.purchase_orders
ADD COLUMN IF NOT EXISTS recommendation_id UUID
    REFERENCES inventory.recommendations(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_po_recommendation
    ON supply.purchase_orders(recommendation_id);
