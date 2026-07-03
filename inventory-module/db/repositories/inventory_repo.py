"""
inventory_repo.py
All database access for the inventory module.
No raw SQL outside this file.

Usage from any agent or service:
    from db.repositories.inventory_repo import InventoryRepo

    repo = InventoryRepo()
    await repo.connect()
"""
import os
import logging
from datetime import date, datetime
from typing import Optional
from pathlib import Path
import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")

logger = logging.getLogger(__name__)


class InventoryRepo:

    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        if self.pool is not None:
            return
        self.pool = await asyncpg.create_pool(
            host     = os.getenv("DOCKER_DB_HOST"),
            port     = int(os.getenv("DOCKER_DB_PORT", "5433")),
            database = os.getenv("DOCKER_DB_NAME"),
            user     = os.getenv("DOCKER_DB_USER"),
            password = os.getenv("DOCKER_DB_PASSWORD"),
            min_size = 2,
            max_size = 10,
        )
        logger.info("[InventoryRepo] Connection pool created")

    async def close(self):
        if self.pool:
            await self.pool.close()
            self.pool = None

    # ── Business Objectives ───────────────────────────────────────────────────

    async def list_objectives(self) -> list[dict]:
        """Get all business objectives ordered by priority"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM inventory.business_objectives ORDER BY priority ASC"
            )
            return [dict(r) for r in rows]

    async def get_active_objective(self) -> Optional[dict]:
        """Get the currently active business objective"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM inventory.business_objectives WHERE is_active = TRUE LIMIT 1"
            )
            return dict(row) if row else None

    async def set_active_objective(self, objective_type: str) -> bool:
        """Set a business objective as active (deactivates all others)"""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE inventory.business_objectives SET is_active = FALSE"
                )
                result = await conn.execute(
                    "UPDATE inventory.business_objectives SET is_active = TRUE WHERE objective_type = $1",
                    objective_type
                )
                # Check if any row was updated
                return result.split()[-1] != '0'

    # ── Alerts ────────────────────────────────────────────────────────────────

    async def create_alert(
        self,
        sku:         str,
        store_id:    str,
        alert_type:  str,
        severity:    str,
        recommended_action: str,
        estimated_stockout_date: Optional[date] = None,
    ) -> str:
        """Create a new alert and return its ID"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO inventory.alerts
                    (sku, store_id, alert_type, severity, recommended_action, 
                     estimated_stockout_date, status, triggered_at, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, 'pending', NOW(), NOW())
                RETURNING id
            """,
                sku, store_id, alert_type, severity, recommended_action,
                estimated_stockout_date
            )
            return str(row['id'])

    async def get_store_alerts(
        self,
        store_id: str,
        status: Optional[str] = None
    ) -> list[dict]:
        """Get alerts for a store, optionally filtered by status"""
        async with self.pool.acquire() as conn:
            if status:
                rows = await conn.fetch("""
                    SELECT a.*, p.product_name
                    FROM inventory.alerts a
                    LEFT JOIN inventory.products p ON p.sku = a.sku
                    WHERE a.store_id = $1 AND a.status = $2
                    ORDER BY a.severity DESC, a.triggered_at DESC
                    LIMIT 100
                """, store_id, status)
            else:
                rows = await conn.fetch("""
                    SELECT a.*, p.product_name
                    FROM inventory.alerts a
                    LEFT JOIN inventory.products p ON p.sku = a.sku
                    WHERE a.store_id = $1
                    ORDER BY a.severity DESC, a.triggered_at DESC
                    LIMIT 100
                """, store_id)
            return [dict(r) for r in rows]

    async def update_alert_status(
        self,
        alert_id: str,
        status: str,
    ) -> bool:
        """
        Update an alert's status.

        Terminal statuses (validated, resolved, dismissed, rejected) stamp resolved_at.
        Returns True if a row was updated.
        """
        _TERMINAL = ["validated", "resolved", "dismissed", "rejected"]
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE inventory.alerts
                SET
                    status      = $1,
                    resolved_at = CASE
                        WHEN $1 = ANY($2::text[]) THEN NOW()
                        ELSE resolved_at
                    END
                WHERE id = $3
            """, status, _TERMINAL, alert_id)
            return result.split()[-1] != '0'

    async def get_pending_alerts_count(self, store_id: str) -> int:
        """Get count of pending alerts for a store"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT COUNT(*) as count
                FROM inventory.alerts
                WHERE store_id = $1 AND status = 'pending'
            """, store_id)
            return row['count'] if row else 0

    # ── Products ──────────────────────────────────────────────────────────────

    async def get_all_products(self) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM inventory.products ORDER BY sku")
            return [dict(r) for r in rows]

    async def get_product(self, sku: str) -> Optional[dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM inventory.products WHERE sku = $1", sku
            )
            return dict(row) if row else None

    async def get_products_by_category(self, category: str) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM inventory.products WHERE category = $1 ORDER BY sku",
                category
            )
            return [dict(r) for r in rows]

    async def upsert_product(self, product: dict) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO inventory.products
                    (sku, product_name, category, unit_cost, unit_price,
                     lead_time_days, lead_time_std, moq, holding_cost_pct,
                     order_cost, lifecycle_stage)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                ON CONFLICT (sku) DO UPDATE SET
                    product_name     = EXCLUDED.product_name,
                    category         = EXCLUDED.category,
                    unit_cost        = EXCLUDED.unit_cost,
                    unit_price       = EXCLUDED.unit_price,
                    lead_time_days   = EXCLUDED.lead_time_days,
                    lead_time_std    = EXCLUDED.lead_time_std,
                    moq              = EXCLUDED.moq,
                    holding_cost_pct = EXCLUDED.holding_cost_pct,
                    order_cost       = EXCLUDED.order_cost,
                    lifecycle_stage  = EXCLUDED.lifecycle_stage,
                    updated_at       = NOW()
            """,
                product["sku"], product["product_name"],
                product.get("category"),
                product.get("unit_cost"),    product.get("unit_price"),
                product.get("lead_time_days"), product.get("lead_time_std"),
                product.get("moq"),          product.get("holding_cost_pct"),
                product.get("order_cost"),   product.get("lifecycle_stage"),
            )

    # ── Stores ────────────────────────────────────────────────────────────────

    async def get_all_stores(self) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM inventory.stores WHERE active = TRUE ORDER BY store_id"
            )
            return [dict(r) for r in rows]

    async def get_store(self, store_id: str) -> Optional[dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM inventory.stores WHERE store_id = $1", store_id
            )
            return dict(row) if row else None

    # ── Stock levels ──────────────────────────────────────────────────────────

    async def get_stock_level(
        self, sku: str, store_id: str
    ) -> Optional[dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT sku, store_id,
                       quantity                          AS stock_current,
                       COALESCE(quantity_reserved, 0)   AS stock_in_transit,
                       NULL::INTEGER                    AS stock_min,
                       NULL::INTEGER                    AS stock_max,
                       remaining_days_of_stock,
                       last_updated
                FROM inventory.stock_levels
                WHERE sku = $1 AND store_id = $2
            """, sku, store_id)
            return dict(row) if row else None

    async def get_store_stock_levels(self, store_id: str) -> list[dict]:
        """All SKUs for a store with product info joined."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    sl.sku, sl.store_id,
                    sl.quantity                        AS stock_current,
                    COALESCE(sl.quantity_reserved, 0) AS stock_in_transit,
                    NULL::INTEGER                      AS stock_min,
                    NULL::INTEGER                      AS stock_max,
                    sl.remaining_days_of_stock,
                    sl.last_updated,
                    p.product_name,
                    p.category,
                    p.lead_time_days,
                    p.moq
                FROM inventory.stock_levels sl
                JOIN inventory.products p ON p.sku = sl.sku
                WHERE sl.store_id = $1
                ORDER BY sl.remaining_days_of_stock ASC NULLS LAST
            """, store_id)
            return [dict(r) for r in rows]

    async def get_low_stock_items(
        self, store_id: str = None
    ) -> list[dict]:
        """Items with low stock (remaining_days_of_stock < 7 or quantity = 0)."""
        async with self.pool.acquire() as conn:
            if store_id:
                rows = await conn.fetch("""
                    SELECT sl.sku, sl.store_id,
                           sl.quantity AS stock_current,
                           COALESCE(sl.quantity_reserved, 0) AS stock_in_transit,
                           NULL::INTEGER AS stock_min, NULL::INTEGER AS stock_max,
                           sl.remaining_days_of_stock, sl.last_updated,
                           p.product_name, p.lead_time_days, p.moq
                    FROM inventory.stock_levels sl
                    JOIN inventory.products p ON p.sku = sl.sku
                    WHERE sl.store_id = $1
                      AND (sl.quantity = 0 OR sl.remaining_days_of_stock < 7)
                    ORDER BY sl.quantity ASC
                """, store_id)
            else:
                rows = await conn.fetch("""
                    SELECT sl.sku, sl.store_id,
                           sl.quantity AS stock_current,
                           COALESCE(sl.quantity_reserved, 0) AS stock_in_transit,
                           NULL::INTEGER AS stock_min, NULL::INTEGER AS stock_max,
                           sl.remaining_days_of_stock, sl.last_updated,
                           p.product_name, p.lead_time_days, p.moq
                    FROM inventory.stock_levels sl
                    JOIN inventory.products p ON p.sku = sl.sku
                    WHERE sl.quantity = 0 OR sl.remaining_days_of_stock < 7
                    ORDER BY sl.store_id, sl.quantity ASC
                """)
            return [dict(r) for r in rows]

    async def upsert_stock_level(
        self, sku: str, store_id: str, **kwargs
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO inventory.stock_levels
                    (sku, store_id, quantity, quantity_reserved,
                     remaining_days_of_stock, last_updated)
                VALUES ($1,$2,$3,$4,$5,NOW())
                ON CONFLICT (sku, store_id) DO UPDATE SET
                    quantity                = EXCLUDED.quantity,
                    quantity_reserved       = EXCLUDED.quantity_reserved,
                    remaining_days_of_stock = EXCLUDED.remaining_days_of_stock,
                    last_updated            = NOW()
            """,
                sku, store_id,
                kwargs.get("stock_current", 0),
                kwargs.get("stock_in_transit", 0),
                kwargs.get("remaining_days_of_stock"),
            )

    # ── Demand forecast ───────────────────────────────────────────────────────

    async def insert_forecast(self, forecast: dict) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO inventory.demand_forecast
                    (sku, store_id, forecast_date, demand_24h,
                     confidence_low, confidence_high, model_version)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                ON CONFLICT (sku, store_id, forecast_date) DO UPDATE SET
                    demand_24h      = EXCLUDED.demand_24h,
                    confidence_low  = EXCLUDED.confidence_low,
                    confidence_high = EXCLUDED.confidence_high,
                    model_version   = EXCLUDED.model_version
            """,
                forecast["sku"],
                forecast["store_id"],
                forecast["forecast_date"],
                float(forecast["demand_24h"]),
                forecast.get("confidence_low"),
                forecast.get("confidence_high"),
                forecast.get("model_version", "timesfm-v1"),
            )

    async def get_latest_forecast(
        self, sku: str, store_id: str
    ) -> Optional[dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM inventory.demand_forecast
                WHERE sku = $1 AND store_id = $2
                ORDER BY forecast_date DESC
                LIMIT 1
            """, sku, store_id)
            return dict(row) if row else None

    async def get_forecasts_for_date(
        self, forecast_date: date, store_id: str = None
    ) -> list[dict]:
        async with self.pool.acquire() as conn:
            if store_id:
                rows = await conn.fetch("""
                    SELECT df.*, p.product_name, p.lead_time_days
                    FROM inventory.demand_forecast df
                    JOIN inventory.products p ON p.sku = df.sku
                    WHERE df.forecast_date = $1 AND df.store_id = $2
                    ORDER BY df.demand_24h DESC
                """, forecast_date, store_id)
            else:
                rows = await conn.fetch("""
                    SELECT df.*, p.product_name
                    FROM inventory.demand_forecast df
                    JOIN inventory.products p ON p.sku = df.sku
                    WHERE df.forecast_date = $1
                    ORDER BY df.store_id, df.demand_24h DESC
                """, forecast_date)
            return [dict(r) for r in rows]

    # ── Agent runs ────────────────────────────────────────────────────────────

    async def start_agent_run(
        self, agent_name: str, store_id: str = None
    ) -> str:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO inventory.agent_runs (agent_name, store_id, status)
                VALUES ($1, $2, 'running')
                RETURNING id
            """, agent_name, store_id)
            return str(row["id"])

    async def complete_agent_run(self, run_id: str, **kwargs) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE inventory.agent_runs SET
                    status                    = $2,
                    completed_at              = NOW(),
                    error_message             = $3,
                    items_processed           = $4,
                    alerts_generated          = $5,
                    recommendations_generated = $6
                WHERE id = $1
            """,
                run_id,
                kwargs.get("status", "completed"),
                kwargs.get("error_message"),
                kwargs.get("items_processed", 0),
                kwargs.get("alerts_generated", 0),
                kwargs.get("recommendations_generated", 0),
            )

    async def get_recent_runs(
        self, agent_name: str = None, limit: int = 20
    ) -> list[dict]:
        async with self.pool.acquire() as conn:
            if agent_name:
                rows = await conn.fetch("""
                    SELECT * FROM inventory.agent_runs
                    WHERE agent_name = $1
                    ORDER BY started_at DESC LIMIT $2
                """, agent_name, limit)
            else:
                rows = await conn.fetch("""
                    SELECT * FROM inventory.agent_runs
                    ORDER BY started_at DESC LIMIT $1
                """, limit)
            return [dict(r) for r in rows]

    # ── Alerts ────────────────────────────────────────────────────────────────

    async def insert_alert(self, alert: dict) -> str:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO inventory.alerts
                    (sku, store_id, alert_type, severity,
                     recommended_action, estimated_stockout_date)
                VALUES ($1,$2,$3,$4,$5,$6)
                RETURNING id
            """,
                alert["sku"],
                alert["store_id"],
                alert["alert_type"],
                alert["severity"],
                alert.get("recommended_action"),
                alert.get("estimated_stockout_date"),
            )
            return str(row["id"])

    async def get_pending_alerts(
        self, store_id: str = None
    ) -> list[dict]:
        severity_order = """
            CASE severity
                WHEN 'critical' THEN 1
                WHEN 'high'     THEN 2
                WHEN 'medium'   THEN 3
                ELSE 4
            END
        """
        async with self.pool.acquire() as conn:
            if store_id:
                rows = await conn.fetch(f"""
                    SELECT a.*, p.product_name
                    FROM inventory.alerts a
                    JOIN inventory.products p ON p.sku = a.sku
                    WHERE a.status = 'pending' AND a.store_id = $1
                    ORDER BY {severity_order}, a.triggered_at ASC
                """, store_id)
            else:
                rows = await conn.fetch(f"""
                    SELECT a.*, p.product_name
                    FROM inventory.alerts a
                    JOIN inventory.products p ON p.sku = a.sku
                    WHERE a.status = 'pending'
                    ORDER BY {severity_order}, a.triggered_at ASC
                """)
            return [dict(r) for r in rows]

    async def resolve_alert(
        self, alert_id: str, was_accurate: bool = None
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE inventory.alerts
                SET status = 'resolved',
                    resolved_at = NOW(),
                    was_accurate = $2
                WHERE id = $1
            """, alert_id, was_accurate)

    # ── Recommendations ───────────────────────────────────────────────────────

    async def insert_recommendation(self, rec: dict) -> str:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO inventory.recommendations
                    (sku, store_id, agent_run_id, recommendation_type,
                     recommendation_text, suggested_quantity, confidence)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                RETURNING id
            """,
                rec["sku"],
                rec["store_id"],
                rec.get("agent_run_id"),
                rec["recommendation_type"],
                rec.get("recommendation_text"),
                rec.get("suggested_quantity"),
                rec.get("confidence"),
            )
            return str(row["id"])

    async def get_pending_recommendations(
        self, store_id: str = None
    ) -> list[dict]:
        async with self.pool.acquire() as conn:
            if store_id:
                rows = await conn.fetch("""
                    SELECT r.*, p.product_name, p.moq, p.lead_time_days
                    FROM inventory.recommendations r
                    JOIN inventory.products p ON p.sku = r.sku
                    WHERE r.status = 'pending' AND r.store_id = $1
                    ORDER BY r.confidence DESC NULLS LAST
                """, store_id)
            else:
                rows = await conn.fetch("""
                    SELECT r.*, p.product_name, p.moq, p.lead_time_days
                    FROM inventory.recommendations r
                    JOIN inventory.products p ON p.sku = r.sku
                    WHERE r.status = 'pending'
                    ORDER BY r.store_id, r.confidence DESC NULLS LAST
                """)
            return [dict(r) for r in rows]

    async def decide_recommendation(
        self, rec_id: str, status: str, decided_by: str
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE inventory.recommendations
                SET status = $2, decided_by = $3, decided_at = NOW()
                WHERE id = $1
            """, rec_id, status, decided_by)

    # ── Promotions ────────────────────────────────────────────────────────────

    async def get_active_promotions(
        self, target_date: date = None
    ) -> list[dict]:
        d = target_date or date.today()
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM inventory.promotions
                WHERE $1 BETWEEN start_date AND end_date
                ORDER BY discount_pct DESC
            """, d)
            return [dict(r) for r in rows]

    async def get_promotions_for_sku(
        self, sku: str, target_date: date = None
    ) -> list[dict]:
        d = target_date or date.today()
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM inventory.promotions
                WHERE (sku = $1 OR sku IS NULL)
                  AND $2 BETWEEN start_date AND end_date
            """, sku, d)
            return [dict(r) for r in rows]

    # ── Events ────────────────────────────────────────────────────────────────

    async def get_active_events(
        self, target_date: date = None
    ) -> list[dict]:
        d = target_date or date.today()
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM inventory.events
                WHERE $1 BETWEEN start_date AND end_date
                ORDER BY estimated_uplift_pct DESC NULLS LAST
            """, d)
            return [dict(r) for r in rows]

    async def insert_event(self, event: dict) -> str:
        import json
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO inventory.events
                    (event_name, event_type, start_date, end_date,
                     affected_categories, estimated_uplift_pct, scope)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                RETURNING event_id
            """,
                event["event_name"],
                event.get("event_type"),
                event["start_date"],
                event["end_date"],
                json.dumps(event.get("affected_categories", [])),
                event.get("estimated_uplift_pct"),
                event.get("scope", "national"),
            )
            return str(row["event_id"])

    # ── Business objectives ───────────────────────────────────────────────────

    async def get_active_objectives(self) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM inventory.business_objectives
                WHERE is_active = TRUE
                ORDER BY priority ASC
            """)
            return [dict(r) for r in rows]

# =============================================================================
# SyncInventoryRepo
# =============================================================================
# stock_tools.py and analysis_agent.py run synchronously inside LangGraph's
# executor — asyncpg cannot be used there without event-loop gymnastics.
# This thin psycopg2 wrapper exposes exactly what those files need.
#
# Same .env variables as the async InventoryRepo above.
# Opens/closes one connection per call — acceptable because calls happen
# once per SKU per batch run, not in a tight loop.
# =============================================================================

try:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG2_OK = True
except ImportError:
    _PSYCOPG2_OK = False
    logger.warning(
        "psycopg2 not installed — SyncInventoryRepo disabled. "
        "Run: pip install psycopg2-binary"
    )


class SyncInventoryRepo:
    """
    Synchronous DB access for sync code paths (stock_tools, analysis_agent,
    orchestrator).  All methods are @staticmethod so callers never need to
    instantiate — just call SyncInventoryRepo.get_product(sku).
    """

    @staticmethod
    def _conn():
        if not _PSYCOPG2_OK:
            return None
        try:
            return psycopg2.connect(
                host     = os.getenv("DOCKER_DB_HOST"),
                port     = int(os.getenv("DOCKER_DB_PORT", "5433")),
                dbname   = os.getenv("DOCKER_DB_NAME"),
                user     = os.getenv("DOCKER_DB_USER"),
                password = os.getenv("DOCKER_DB_PASSWORD"),
            )
        except Exception as exc:
            logger.warning("SyncInventoryRepo: DB connection failed: %s", exc)
            return None

    # ── Reads ─────────────────────────────────────────────────────────────────
    @staticmethod
    def save_context_adjustment(
        sku: str,
        store_id: str,
        valid_from: date,
        valid_to: date,
        demand_uplift_pct: float,
        dominant_signal: str,
        confidence: float,
        interpretation: str,
        agent_run_id: str = None,
    ) -> bool:
        """
        Upsert context adjustment row for a SKU/store/date window.

        Conflict key:
            (sku, store_id, valid_from)

        Returns True on success, False otherwise.
        """
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return False

        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO inventory.context_adjustments (
                        sku,
                        store_id,
                        valid_from,
                        valid_to,
                        demand_uplift_pct,
                        dominant_signal,
                        confidence,
                        interpretation,
                        agent_run_id
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)

                    ON CONFLICT (sku, store_id, valid_from)
                    DO UPDATE SET
                        valid_to           = EXCLUDED.valid_to,
                        demand_uplift_pct  = EXCLUDED.demand_uplift_pct,
                        dominant_signal    = EXCLUDED.dominant_signal,
                        confidence         = EXCLUDED.confidence,
                        interpretation     = EXCLUDED.interpretation,
                        agent_run_id       = EXCLUDED.agent_run_id
                """, (
                    sku,
                    store_id,
                    valid_from,
                    valid_to,
                    demand_uplift_pct,
                    dominant_signal,
                    confidence,
                    interpretation,
                    agent_run_id,
                ))

                conn.commit()
                return True

        except Exception as exc:
            logger.warning(
                "SyncInventoryRepo.save_context_adjustment(%s, %s): %s",
                sku, store_id, exc,
            )
            return False

        finally:
            conn.close()

    @staticmethod
    def get_context_adjustment(
        sku: str,
        store_id: str,
    ) -> Optional[dict]:
        """
        Get latest currently-valid context adjustment for a SKU/store.

        Returns:
            dict or None
        """
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return None

        try:
            with conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor
            ) as cur:

                cur.execute("""
                    SELECT *
                    FROM inventory.context_adjustments
                    WHERE sku = %s
                      AND store_id = %s
                      AND CURRENT_DATE BETWEEN valid_from AND valid_to
                    ORDER BY valid_from DESC
                    LIMIT 1
                """, (sku, store_id))

                row = cur.fetchone()
                return dict(row) if row else None

        except Exception as exc:
            logger.warning(
                "SyncInventoryRepo.get_context_adjustment(%s, %s): %s",
                sku, store_id, exc,
            )
            return None

        finally:
            conn.close()
    @staticmethod
    def get_product(sku: str) -> Optional[dict]:
        """
        One row from inventory.products, or None if not found / DB unavailable.
        Column names match product_master.csv so stock_tools needs no changes.
        """
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return None
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM inventory.products WHERE sku = %s", (sku,))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as exc:
            logger.warning("SyncInventoryRepo.get_product(%s): %s", sku, exc)
            return None
        finally:
            conn.close()

    @staticmethod
    def get_stock_level(sku: str, store_id: str) -> Optional[dict]:
        """One row from inventory.stock_levels, or None."""
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return None
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT sku, store_id,
                           quantity                        AS stock_current,
                           COALESCE(quantity_reserved, 0) AS stock_in_transit,
                           NULL::INTEGER                  AS stock_min,
                           NULL::INTEGER                  AS stock_max,
                           remaining_days_of_stock, last_updated
                    FROM inventory.stock_levels
                    WHERE sku = %s AND store_id = %s
                """, (sku, store_id))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as exc:
            logger.warning(
                "SyncInventoryRepo.get_stock_level(%s, %s): %s", sku, store_id, exc
            )
            return None
        finally:
            conn.close()

    @staticmethod
    def get_stock_levels_batch(skus: list, store_id: str) -> dict:
        """
        Fetch quantity for multiple SKUs in a single query.

        Returns {sku: quantity} for every SKU found in inventory.stock_levels.
        SKUs not present in the table are simply absent from the dict — callers
        should fall back to report/CSV values for those.

        This replaces the N individual get_stock_level() calls that happen
        inside _to_inventory_item() during a batch pipeline run, cutting
        N DB round-trips down to 1.
        """
        if not skus:
            return {}
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return {}
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT sku, quantity
                    FROM inventory.stock_levels
                    WHERE store_id = %s
                      AND sku = ANY(%s::integer[])
                    """,
                    (store_id, list(skus)),
                )
                return {
                    str(row[0]): int(row[1])
                    for row in cur.fetchall()
                    if row[1] is not None
                }
        except Exception as exc:
            logger.warning(
                "SyncInventoryRepo.get_stock_levels_batch(%s, %d skus): %s",
                store_id, len(skus), exc,
            )
            return {}
        finally:
            conn.close()

    @staticmethod
    def get_products_batch(skus: list) -> dict:
        """
        Fetch product master rows for multiple SKUs in a single query.

        Returns {sku: product_dict} for every SKU found in inventory.products.
        SKUs not in the table are absent from the dict — callers fall back
        to CSV for those (same logic as the single-SKU get_product path).

        Replaces 110 individual get_product() calls in a batch pipeline run.
        """
        if not skus:
            return {}
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return {}
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT sku, product_name, category, unit_cost, moq,
                           lead_time_days, lead_time_std, lifecycle_stage
                    FROM inventory.products
                    WHERE sku = ANY(%s::integer[])
                    """,
                    (list(skus),),
                )
                cols = [d[0] for d in cur.description]
                return {
                    str(row[0]): dict(zip(cols, row))
                    for row in cur.fetchall()
                }
        except Exception as exc:
            logger.warning("SyncInventoryRepo.get_products_batch(%d skus): %s", len(skus), exc)
            return {}
        finally:
            conn.close()

    @staticmethod
    def get_active_objective() -> Optional[dict]:
        """
        The currently active row from inventory.business_objectives.
        Returns full row including label and metadata (JSONB → plain dict).
        None if DB unavailable or no active row.
        """
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return None
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT *
                    FROM inventory.business_objectives
                    WHERE is_active = TRUE
                    ORDER BY priority ASC
                    LIMIT 1
                """)
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as exc:
            logger.warning("SyncInventoryRepo.get_active_objective(): %s", exc)
            return None
        finally:
            conn.close()

    # ── Writes ────────────────────────────────────────────────────────────────

    @staticmethod
    def start_agent_run(agent_name: str, store_id: str) -> Optional[str]:
        """
        Insert a new row in inventory.agent_runs with status='running'.
        Returns the UUID as a string, or None if DB unavailable.
        """
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO inventory.agent_runs (agent_name, store_id, status)
                    VALUES (%s, %s, 'running')
                    RETURNING id
                """, (agent_name, store_id))
                conn.commit()
                return str(cur.fetchone()[0])
        except Exception as exc:
            logger.warning("SyncInventoryRepo.start_agent_run(): %s", exc)
            return None
        finally:
            conn.close()

    @staticmethod
    def complete_agent_run(
        run_id: str,
        status: str = "completed",
        items_processed: int = 0,
        alerts_generated: int = 0,
        recommendations_generated: int = 0,
        error_message: str = None,
    ) -> None:
        """Update inventory.agent_runs row with final status and counts."""
        if run_id is None:
            return
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE inventory.agent_runs SET
                        status                    = %s,
                        completed_at              = NOW(),
                        items_processed           = %s,
                        alerts_generated          = %s,
                        recommendations_generated = %s,
                        error_message             = %s
                    WHERE id = %s
                """, (status, items_processed, alerts_generated,
                      recommendations_generated, error_message, run_id))
                conn.commit()
        except Exception as exc:
            logger.warning("SyncInventoryRepo.complete_agent_run(): %s", exc)
        finally:
            conn.close()

    @staticmethod
    def upsert_stock_level_sync(
        sku: str,
        store_id: str,
        stock_current: int,
        remaining_days_of_stock: float = None,
    ) -> None:
        """
        Update inventory.stock_levels with the latest computed values.
        Only touches stock_current and remaining_days_of_stock —
        stock_min / stock_max were set by init_stock_levels.py and stay.
        """
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE inventory.stock_levels SET
                        quantity                = %s,
                        remaining_days_of_stock = %s,
                        last_updated            = NOW()
                    WHERE sku = %s AND store_id = %s
                """, (stock_current, remaining_days_of_stock, sku, store_id))
                conn.commit()
        except Exception as exc:
            logger.warning(
                "SyncInventoryRepo.upsert_stock_level_sync(%s, %s): %s",
                sku, store_id, exc,
            )
        finally:
            conn.close()

    @staticmethod
    def insert_alert_if_new(
        sku: str,
        store_id: str,
        alert_type: str,
        severity: str,
        recommended_action: str,
        agent_run_id: str = None,
    ) -> bool:
        """
        Insert a row into inventory.alerts only if no pending alert of the same
        type already exists for this (sku, store_id).
        Returns True if inserted, False if skipped (dedup).
        """
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                # FIX: use = ANY(%s) with a plain Python list — psycopg2 adapts
                # lists to Postgres arrays automatically. The old %s::text[] cast
                # caused silent failures so the dedup check never matched.
                # Cooldown extended from 4h to 24h.
                cur.execute("""
                    SELECT 1 FROM inventory.alerts
                    WHERE sku = %s AND store_id = %s
                      AND alert_type = %s
                      AND (
                            status = 'pending'
                            OR (
                                status = ANY(%s)
                                AND resolved_at > NOW() - INTERVAL '24 hours'
                            )
                          )
                    LIMIT 1
                """, (sku, store_id, alert_type,
                      ['validated', 'rejected', 'resolved', 'dismissed']))
                if cur.fetchone():
                    return False

                cur.execute("""
                    INSERT INTO inventory.alerts
                        (sku, store_id, alert_type, severity, recommended_action, agent_run_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (sku, store_id, alert_type, severity, recommended_action, agent_run_id))
                conn.commit()
                return True
        except Exception as exc:
            logger.warning(
                "SyncInventoryRepo.insert_alert_if_new(%s, %s): %s",
                sku, store_id, exc,
            )
            return False
        finally:
            conn.close()

    @staticmethod
    def upsert_alert_and_return_id(
        sku: str,
        store_id: str,
        alert_type: str,
        severity: str,
        recommended_action: str,
        agent_run_id: str = None,
    ) -> Optional[str]:
        """
        Get the UUID of an existing pending alert for (sku, store_id, alert_type),
        or insert a new one and return its UUID.

        This is what _build_alerts() uses so that every pipeline alert has a
        real DB id — enabling the frontend PATCH /alerts/{id} call to work.

        Returns the UUID string, or None if DB is unavailable.
        """
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                # FIX: use = ANY(%s) with a plain Python list — psycopg2 adapts
                # lists to Postgres arrays automatically. The old %s::text[] cast
                # caused silent failures so the dedup check never matched.
                # Cooldown extended from 4h to 24h.
                cur.execute("""
                    SELECT id FROM inventory.alerts
                    WHERE sku = %s AND store_id = %s
                      AND alert_type = %s
                      AND (
                            status = 'pending'
                            OR (
                                status = ANY(%s)
                                AND resolved_at > NOW() - INTERVAL '24 hours'
                            )
                          )
                    ORDER BY
                        CASE status WHEN 'pending' THEN 0 ELSE 1 END,
                        triggered_at DESC
                    LIMIT 1
                """, (sku, store_id, alert_type,
                      ['validated', 'rejected', 'resolved', 'dismissed']))
                row = cur.fetchone()
                if row:
                    return str(row[0])

                # None found — insert new
                cur.execute("""
                    INSERT INTO inventory.alerts
                        (sku, store_id, alert_type, severity, recommended_action, agent_run_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (sku, store_id, alert_type, severity, recommended_action, agent_run_id))
                conn.commit()
                return str(cur.fetchone()[0])
        except Exception as exc:
            logger.warning(
                "SyncInventoryRepo.upsert_alert_and_return_id(%s, %s): %s",
                sku, store_id, exc,
            )
            return None
        finally:
            conn.close()

    @staticmethod
    def upsert_alerts_batch(
        alerts: list[dict],
    ) -> dict[str, str]:
        """
        Upsert multiple alerts in a single connection + transaction.

        Each dict in `alerts` must contain:
            sku, store_id, alert_type, severity, recommended_action
        Optional:
            agent_run_id

        Returns a mapping of  "{sku}:{store_id}:{alert_type}" → uuid_str
        for every alert that was successfully inserted or already existed.
        Alerts that fail the DB check constraint (e.g. invalid alert_type)
        are skipped and logged — they will not appear in the returned map,
        so _build_alerts() will fall back to a fake id for them.

        FIX 1 — savepoint per item: the original conn.rollback() on any item
        error wiped the entire transaction. Now each item is wrapped in its own
        SAVEPOINT so a single failure only rolls back that item.
        FIX 2 — ANY(%s) with a plain list instead of %s::text[]: psycopg2
        adapts Python lists to Postgres arrays automatically with ANY(%s). The
        old explicit cast caused the dedup check to silently never match.
        FIX 3 — cooldown extended from 4h to 24h.
        """
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return {}
        result: dict[str, str] = {}
        try:
            conn.autocommit = False
            with conn.cursor() as cur:
                for a in alerts:
                    sku        = a["sku"]
                    store_id   = a["store_id"]
                    alert_type = a["alert_type"]
                    key        = f"{sku}:{store_id}:{alert_type}"

                    cur.execute("SAVEPOINT sp_alert")
                    try:
                        cur.execute("""
                            SELECT id FROM inventory.alerts
                            WHERE sku = %s AND store_id = %s
                              AND alert_type = %s
                              AND (
                                    status = 'pending'
                                    OR (
                                        status = ANY(%s)
                                        AND resolved_at > NOW() - INTERVAL '24 hours'
                                    )
                                  )
                            ORDER BY
                                CASE status WHEN 'pending' THEN 0 ELSE 1 END,
                                triggered_at DESC
                            LIMIT 1
                        """, (sku, store_id, alert_type,
                              ['validated', 'rejected', 'resolved', 'dismissed']))

                        row = cur.fetchone()
                        if row:
                            result[key] = str(row[0])
                            cur.execute("RELEASE SAVEPOINT sp_alert")
                            continue

                        # None found — insert new
                        cur.execute("""
                            INSERT INTO inventory.alerts
                                (sku, store_id, alert_type, severity,
                                 recommended_action, agent_run_id)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            RETURNING id
                        """, (
                            sku, store_id, alert_type,
                            a["severity"], a["recommended_action"],
                            a.get("agent_run_id"),
                        ))
                        row = cur.fetchone()
                        if row:
                            result[key] = str(row[0])

                        cur.execute("RELEASE SAVEPOINT sp_alert")

                    except Exception as item_exc:
                        # Roll back ONLY this savepoint — all other items are safe
                        cur.execute("ROLLBACK TO SAVEPOINT sp_alert")
                        cur.execute("RELEASE SAVEPOINT sp_alert")
                        logger.warning(
                            "upsert_alerts_batch: skipped %s@%s (%s): %s",
                            sku, store_id, alert_type, item_exc,
                        )
                        continue

            conn.commit()
        except Exception as exc:
            logger.warning("upsert_alerts_batch: outer failure: %s", exc)
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            try:
                conn.autocommit = True
            except Exception:
                pass
            conn.close()
        return result

    @staticmethod
    def get_any_objective() -> Optional[dict]:
        """
        Fallback: fetch the highest-priority row from inventory.business_objectives
        regardless of is_active. Used when no row is active.
        """
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return None
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT *
                    FROM inventory.business_objectives
                    ORDER BY priority ASC
                    LIMIT 1
                """)
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as exc:
            logger.warning("SyncInventoryRepo.get_any_objective(): %s", exc)
            return None
        finally:
            conn.close()

    @staticmethod
    def list_objectives() -> list[dict]:
        """Get all business objectives ordered by priority"""
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return []
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM inventory.business_objectives ORDER BY priority ASC"
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            logger.warning("SyncInventoryRepo.list_objectives(): %s", exc)
            return []
        finally:
            conn.close()

    @staticmethod
    def set_active_objective(objective_type: str) -> bool:
        """
        Set a business objective as active (deactivates all others).
        Note: objective_type parameter is actually the label value (e.g., 'standard', 'cost_savings').
        """
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE inventory.business_objectives SET is_active = FALSE")
                cur.execute(
                    "UPDATE inventory.business_objectives SET is_active = TRUE WHERE label = %s",
                    (objective_type,)
                )
                conn.commit()
                return cur.rowcount > 0
        except Exception as exc:
            logger.warning("SyncInventoryRepo.set_active_objective(%s): %s", objective_type, exc)
            return False
        finally:
            conn.close()

    @staticmethod
    def get_store_alerts(store_id: str, status: Optional[str] = "pending") -> list[dict]:
        """
        Get alerts from DB for a store.
        Pass status=None to return all alerts regardless of status.
        Pass status='pending' (default) for only unresolved alerts.
        """
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return []
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if status:
                    cur.execute("""
                        SELECT a.*, p.product_name
                        FROM inventory.alerts a
                        LEFT JOIN inventory.products p ON p.sku = a.sku
                        WHERE a.store_id = %s AND a.status = %s
                        ORDER BY
                            CASE a.severity
                                WHEN 'critical' THEN 1
                                WHEN 'high'     THEN 2
                                WHEN 'medium'   THEN 3
                                ELSE 4
                            END,
                            a.triggered_at DESC
                        LIMIT 200
                    """, (store_id, status))
                else:
                    # All statuses — used by frontend "show all" views
                    cur.execute("""
                        SELECT a.*, p.product_name
                        FROM inventory.alerts a
                        LEFT JOIN inventory.products p ON p.sku = a.sku
                        WHERE a.store_id = %s
                        ORDER BY
                            CASE a.status
                                WHEN 'pending' THEN 0
                                ELSE 1
                            END,
                            CASE a.severity
                                WHEN 'critical' THEN 1
                                WHEN 'high'     THEN 2
                                WHEN 'medium'   THEN 3
                                ELSE 4
                            END,
                            a.triggered_at DESC
                        LIMIT 200
                    """, (store_id,))
                rows = cur.fetchall()
                result = []
                for r in rows:
                    d = dict(r)
                    # Serialize datetime fields for JSON serialisation downstream
                    for col in ("triggered_at", "created_at", "resolved_at", "decided_at"):
                        if d.get(col) and hasattr(d[col], "isoformat"):
                            d[col] = d[col].isoformat()
                    result.append(d)
                return result
        except Exception as exc:
            logger.warning("SyncInventoryRepo.get_store_alerts(%s): %s", store_id, exc)
            return []
        finally:
            conn.close()

    @staticmethod
    def update_alert_status(
        alert_id: str,
        status: str,
        decided_by: Optional[str] = None,
    ) -> bool:
        """
        Update an alert's status.
        Terminal statuses also stamp resolved_at = NOW().
        Returns True if a row was updated.
        """
        _TERMINAL = {"validated", "resolved", "dismissed", "rejected"}
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                if status in _TERMINAL:
                    cur.execute("""
                        UPDATE inventory.alerts
                        SET status      = %s,
                            decided_by  = %s,
                            resolved_at = NOW()
                        WHERE id = %s
                    """, (status, decided_by, alert_id))
                else:
                    cur.execute("""
                        UPDATE inventory.alerts
                        SET status     = %s,
                            decided_by = %s
                        WHERE id = %s
                    """, (status, decided_by, alert_id))
                conn.commit()
                return cur.rowcount > 0
        except Exception as exc:
            logger.warning("SyncInventoryRepo.update_alert_status(%s): %s", alert_id, exc)
            return False
        finally:
            conn.close()

    @staticmethod
    def resolve_stale_alerts(store_id: str, current_skus: set) -> int:
        """
        Auto-resolve pending alerts for SKUs no longer at risk.

        Root cause of the "pending alert count keeps growing forever" bug:
        alerts were only ever moved out of 'pending' by an explicit operator
        action (validate/reject/dismiss). If a SKU's risk cleared naturally
        (e.g. restocked) between two pipeline runs, its old alert stayed
        'pending' indefinitely — the /alerts/{storeId} count drifted away
        from the live risk snapshot returned by /store/{storeId}.

        Called by _build_alerts() after each upsert with the current set of
        (still) at-risk SKUs for the store — anything pending outside that
        set gets status='resolved' with resolved_at=NOW().

        Returns the number of rows resolved.
        """
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE inventory.alerts
                    SET status = 'resolved', resolved_at = NOW()
                    WHERE store_id = %s
                      AND status = 'pending'
                      AND NOT (sku = ANY(%s))
                """, (store_id, list(current_skus)))
                conn.commit()
                return cur.rowcount
        except Exception as exc:
            logger.warning("SyncInventoryRepo.resolve_stale_alerts(%s): %s", store_id, exc)
            return 0
        finally:
            conn.close()

    @staticmethod
    def get_non_pending_alert_ids(uuids: set) -> set:
        """
        Given a set of alert UUIDs, return the subset whose current status is
        NOT 'pending' (i.e. already actioned: validated / rejected / dismissed /
        resolved).

        Used by _build_alerts to suppress actioned alerts from the pipeline's
        returned list so they don't reappear as ghost alerts on the frontend.
        Returns an empty set if DB is unavailable or uuids is empty.
        """
        if not uuids:
            return set()
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return set()
        try:
            with conn.cursor() as cur:
                # Cast explicite text[] → uuid[] requis car la colonne id est uuid
                # et psycopg2 passe les strings Python comme text sans cast automatique
                cur.execute("""
                    SELECT id::text
                    FROM inventory.alerts
                    WHERE id::text = ANY(%s)
                      AND status != 'pending'
                """, (list(uuids),))
                return {str(row[0]) for row in cur.fetchall()}
        except Exception as exc:
            logger.warning("SyncInventoryRepo.get_non_pending_alert_ids(): %s", exc)
            return set()
        finally:
            conn.close()

    @staticmethod
    def get_alert_store_id(alert_id: str) -> Optional[str]:
        """
        Return the store_id of a given alert UUID, or None if not found / DB
        unavailable.

        Used by update_alert in routes.py to know which store cache to
        invalidate after the operator validates / rejects / dismisses an alert.
        """
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT store_id FROM inventory.alerts WHERE id = %s",
                    (alert_id,),
                )
                row = cur.fetchone()
                return str(row[0]) if row else None
        except Exception as exc:
            logger.warning("SyncInventoryRepo.get_alert_store_id(%s): %s", alert_id, exc)
            return None
        finally:
            conn.close()

    @staticmethod
    def save_recommendation(
        sku:                 str,
        store_id:            str,
        recommendation_type: str,
        recommendation_text: str,
        suggested_quantity:  Optional[int] = None,
        confidence:          float = 0.3,
        agent_run_id:        Optional[str] = None,
    ) -> Optional[str]:
        """
        Insert one row into inventory.recommendations.
        Only called for ORDER / EXPEDITE actions — HOLD / MONITOR produce no row.
        Returns the UUID string of the inserted row, or None on failure.
        """
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO inventory.recommendations
                        (sku, store_id, agent_run_id, recommendation_type,
                         recommendation_text, suggested_quantity, confidence)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    sku, store_id,
                    agent_run_id,
                    recommendation_type,
                    recommendation_text,
                    suggested_quantity,
                    confidence,
                ))
                conn.commit()
                row = cur.fetchone()
                return str(row[0]) if row else None
        except Exception as exc:
            logger.warning(
                "SyncInventoryRepo.save_recommendation(%s, %s): %s", sku, store_id, exc
            )
            return None
        finally:
            conn.close()

    @staticmethod
    def update_recommendation_status(
        recommendation_id: str,
        status: str,
        decided_by: Optional[str] = None,
    ) -> bool:
        """
        Update a recommendation's status (approved / rejected).
        Stamps decided_at = NOW() and records who actioned it.
        Returns True if a row was updated.
        """
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE inventory.recommendations
                    SET status     = %s,
                        decided_by = %s,
                        decided_at = NOW()
                    WHERE id = %s
                """, (status, decided_by, recommendation_id))
                conn.commit()
                return cur.rowcount > 0
        except Exception as exc:
            logger.warning(
                "SyncInventoryRepo.update_recommendation_status(%s): %s",
                recommendation_id, exc,
            )
            return False
        finally:
            conn.close()

    @staticmethod
    def get_recommendation_by_id(recommendation_id: str) -> Optional[dict]:
        """
        Return a single recommendation row by UUID, or None.
        Used by routes.py _to_inventory_item() to fetch live status for UI rehydration.
        """
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return None
        try:
            import psycopg2.extras
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, status, decided_by, decided_at
                    FROM inventory.recommendations
                    WHERE id = %s
                """, (recommendation_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as exc:
            logger.warning(
                "SyncInventoryRepo.get_recommendation_by_id(%s): %s",
                recommendation_id, exc,
            )
            return None
        finally:
            conn.close()

    @staticmethod
    def get_latest_recommendation(sku: str, store_id: str) -> Optional[dict]:
        """
        Return the most recent recommendation row for a SKU/store, or None.
        Used by main.py --db-check and routes.py to surface the last decision.
        """
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return None
        try:
            import psycopg2.extras
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT *
                    FROM inventory.recommendations
                    WHERE sku = %s AND store_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (sku, store_id))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as exc:
            logger.warning(
                "SyncInventoryRepo.get_latest_recommendation(%s, %s): %s", sku, store_id, exc
            )
            return None
        finally:
            conn.close()

    @staticmethod
    def get_store(store_id: str) -> Optional[dict]:
        """
        Return the store row for a given store_id, or None.
        Used by main.py --db-check and stock_tools.get_store().
        """
        conn = SyncInventoryRepo._conn()
        if conn is None:
            return None
        try:
            import psycopg2.extras
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM inventory.stores WHERE store_id = %s",
                    (store_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as exc:
            logger.warning("SyncInventoryRepo.get_store(%s): %s", store_id, exc)
            return None
        finally:
            conn.close()