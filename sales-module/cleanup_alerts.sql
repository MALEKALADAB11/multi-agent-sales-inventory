-- cleanup_alerts.sql
-- Removes duplicates created by the old buggy code.
-- Run this ONCE. Safe to re-run.
--
-- ── WITH DOCKER COMPOSE ───────────────────────────────────────────────────────
-- Find your postgres service name first:
--   docker compose ps
-- Then run (replace "db" with your actual service name if different):
--   docker compose exec db psql -U YOUR_DB_USER -d YOUR_DB_NAME -f /dev/stdin < cleanup_alerts.sql
--
-- Or copy the file into the container first:
--   docker compose cp cleanup_alerts.sql db:/tmp/cleanup_alerts.sql
--   docker compose exec db psql -U YOUR_DB_USER -d YOUR_DB_NAME -f /tmp/cleanup_alerts.sql
--
-- ── WITHOUT DOCKER ────────────────────────────────────────────────────────────
--   psql -h HOST -p PORT -U USER -d DBNAME -f cleanup_alerts.sql
-- ─────────────────────────────────────────────────────────────────────────────

BEGIN;

-- 1. Remove duplicate pending rows — keep only the OLDEST per (sku, store_id, alert_type, status)
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY sku, store_id, alert_type, status
               ORDER BY triggered_at ASC
           ) AS rn
    FROM inv.alerts
)
DELETE FROM inv.alerts
WHERE id IN (SELECT id FROM ranked WHERE rn > 1);

-- 2. Stamp resolved_at on terminal rows that are missing it
UPDATE inv.alerts
SET resolved_at = COALESCE(resolved_at, triggered_at, NOW())
WHERE status IN ('validated', 'rejected', 'dismissed', 'resolved')
  AND resolved_at IS NULL;

-- 3. Fix wrong alert_type values written by old sync_alerts_to_db
UPDATE inv.alerts SET alert_type = 'stockout_risk' WHERE alert_type = 'rupture';
UPDATE inv.alerts SET alert_type = 'below_minimum' WHERE alert_type = 'redistribution';

-- 4. Unique partial index — prevents future duplicates at DB level
CREATE UNIQUE INDEX IF NOT EXISTS uq_alerts_pending_per_sku_type
    ON inv.alerts (sku, store_id, alert_type)
    WHERE status = 'pending';

-- Show result
SELECT status, COUNT(*) AS count FROM inv.alerts GROUP BY status ORDER BY status;

COMMIT;
