-- Migration 013: harden inventory.alerts / inventory.recommendations
--
-- Precondition for any future Kanban-style board over recommendations/alerts
-- (mirroring the purchase-order board already built): today some SKUs carry
-- 29-35 simultaneous 'pending' recommendation rows for the same (sku, store_id)
-- -- a board would show that many duplicate cards for one product. This
-- migration is additive/idempotent-safe and does not touch resolved/decided
-- historical rows beyond the explicit dedup step below.

-- ── Step 1: dedup existing 'pending' recommendations ────────────────────────
-- Keep the most recent pending row per (sku, store_id); reject the rest with
-- a traceable decided_by so this cleanup is auditable, not a silent delete.
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY sku, store_id
               ORDER BY created_at DESC
           ) AS rn
    FROM inventory.recommendations
    WHERE status = 'pending'
)
UPDATE inventory.recommendations r
SET status = 'rejected',
    decided_by = 'system_cleanup_dedup',
    decided_at = NOW()
FROM ranked
WHERE r.id = ranked.id AND ranked.rn > 1;

-- ── Step 2: prevent future duplicates ────────────────────────────────────────
-- A partial unique index (not a blanket UNIQUE constraint) so it only
-- applies to 'pending' rows -- historical approved/rejected rows are exempt.
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_reco_pending_sku_store
    ON inventory.recommendations (sku, store_id)
    WHERE status = 'pending';

-- ── Step 3: real CHECK constraints on status (previously free text) ────────
ALTER TABLE inventory.alerts
    ADD CONSTRAINT alerts_status_check
    CHECK (status IN ('pending','acknowledged','validated','rejected','dismissed','resolved'));

ALTER TABLE inventory.recommendations
    ADD CONSTRAINT reco_status_check
    CHECK (status IN ('pending','approved','rejected','executed','cancelled'));
