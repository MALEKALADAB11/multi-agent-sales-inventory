-- Migration 001 — Performance indexes for production
-- Run once on the ooredoo_sales database.
-- All indexes are CONCURRENTLY to avoid locking production tables.

-- ── sales.transactions_rt ──────────────────────────────────────────────────
-- Primary query pattern: WHERE store_id = $1 AND created_at >= ... (ReAct tools)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_rt_store_time
    ON sales.transactions_rt (store_id, created_at DESC);

-- Secondary pattern: WHERE store_id = $1 AND date_only = $2 (fetch_live_pos)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_rt_store_date
    ON sales.transactions_rt (store_id, date_only);

-- Advisor-specific queries (advisor profile S3.3)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_rt_advisor
    ON sales.transactions_rt (store_id, advisor_name, created_at DESC);

-- ── sales.transactions (historical) ───────────────────────────────────────
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_store_date
    ON sales.transactions (store_id, date_only);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transactions_store_heure
    ON sales.transactions (store_id, date_only, heure);

-- ── inventory.stock_levels ────────────────────────────────────────────────
-- Used by get_stock_alerts tool (S3.1)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_stock_levels_store_qty
    ON inventory.stock_levels (store_id, quantity_available ASC);

-- ── public.coach_interactions ─────────────────────────────────────────────
-- Used by _load_day_history_sync
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_coach_interactions_advisor_day
    ON public.coach_interactions (store_id, advisor_name, created_at DESC);

-- ── inventory.recommendations ─────────────────────────────────────────────
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_recommendations_store_status
    ON inventory.recommendations (store_id, status, created_at DESC);

-- ── monitoring.advisor_profile ────────────────────────────────────────────
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_advisor_profile_store
    ON monitoring.advisor_profile (store_id, advisor_name);

-- ── Partial index: only active/recent recommendations ─────────────────────
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_recommendations_active
    ON inventory.recommendations (store_id, created_at DESC)
    WHERE status IN ('pending', 'approved');
