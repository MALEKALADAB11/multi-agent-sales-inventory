import asyncpg
from datetime import date, datetime


class PostgresRepo:

    def __init__(self):
        self.pool = None

    async def connect(self):
        if self.pool is not None:
            return
        self.pool = await asyncpg.create_pool(
            host     = "localhost",
            port     = 5432,
            database = "asc_db",
            user     = "asc_user",
            password = "asc_password",
            min_size = 2,
            max_size = 10
        )

    # ── Stores ───────────────────────────────────────────
    async def get_all_stores(self) -> list:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM stores WHERE active = true"
            )
            return [dict(r) for r in rows]

    async def get_store(self, store_id: str) -> dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM stores WHERE id = $1", store_id
            )
            return dict(row) if row else None

    # ── Advisors ─────────────────────────────────────────
    async def get_advisors(self, store_id: str) -> list:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM advisors WHERE store_id = $1 AND active = true",
                store_id
            )
            return [dict(r) for r in rows]

    async def get_advisor(self, advisor_id: str) -> dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM advisors WHERE id = $1", advisor_id
            )
            return dict(row) if row else None

    # ── Targets ──────────────────────────────────────────
    async def get_targets_for_date(
        self, store_id: str, target_date: date
    ) -> list:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT dt.*, a.name as advisor_name
                FROM daily_targets dt
                JOIN advisors a ON a.id = dt.advisor_id
                WHERE dt.store_id = $1
                  AND dt.target_date = $2
            """, store_id, target_date)
            return [dict(r) for r in rows]

    # ── POS ──────────────────────────────────────────────
    async def insert_pos_transaction(self, **kwargs) -> None:
        async with self.pool.acquire() as conn:
            ts = kwargs["transaction_ts"]
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            await conn.execute("""
                INSERT INTO pos_transactions
                  (store_id, advisor_id, sku, product_name,
                   category, amount, units, margin_rate, transaction_ts)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """,
                kwargs["store_id"],
                kwargs["advisor_id"],
                kwargs.get("sku", ""),
                kwargs.get("product_name", ""),
                kwargs.get("category", ""),
                float(kwargs["amount"]),
                int(kwargs.get("units", 1)),
                kwargs.get("margin_rate"),
                ts
            )

    async def get_pos_summary_today(self, store_id: str) -> dict:
        today = datetime.combine(date.today(), datetime.min.time())
        now   = datetime.utcnow()
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    COALESCE(SUM(amount), 0) AS total_ca,
                    COALESCE(SUM(units),  0) AS total_units,
                    COUNT(*)                 AS transaction_count,
                    MAX(transaction_ts)      AS last_transaction
                FROM pos_transactions
                WHERE store_id = $1
                  AND transaction_ts BETWEEN $2 AND $3
            """, store_id, today, now)
            return dict(row) if row else {
                "total_ca": 0, "total_units": 0,
                "transaction_count": 0, "last_transaction": None
            }

    async def get_pos_by_advisor_today(self, store_id: str) -> list:
        today = datetime.combine(date.today(), datetime.min.time())
        now   = datetime.utcnow()
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    advisor_id,
                    COALESCE(SUM(amount), 0) AS total_ca,
                    COALESCE(SUM(units),  0) AS total_units
                FROM pos_transactions
                WHERE store_id = $1
                  AND transaction_ts BETWEEN $2 AND $3
                GROUP BY advisor_id
            """, store_id, today, now)
            return [dict(r) for r in rows]

    async def get_sales_aggregated_by_sku(
        self, store_id: str, days_back: int = 90
    ) -> dict:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    sku,
                    SUM(amount) AS total_amount,
                    SUM(units)  AS total_units,
                    DATE(transaction_ts) AS sale_date
                FROM pos_transactions
                WHERE store_id = $1
                  AND transaction_ts >= NOW() - ($2 * INTERVAL '1 day')
                GROUP BY sku, DATE(transaction_ts)
                ORDER BY sale_date DESC
            """, store_id, days_back)
            result = {}
            for r in rows:
                sku = r["sku"]
                if sku not in result:
                    result[sku] = []
                result[sku].append({
                    "date":   str(r["sale_date"]),
                    "amount": float(r["total_amount"]),
                    "units":  int(r["total_units"])
                })
            return result

    async def get_pos_transactions(
        self,
        store_id:   str,
        date_from:  datetime,
        date_to:    datetime,
        advisor_id: str = None
    ) -> list:
        async with self.pool.acquire() as conn:
            if advisor_id:
                rows = await conn.fetch("""
                    SELECT * FROM pos_transactions
                    WHERE store_id = $1
                      AND transaction_ts BETWEEN $2 AND $3
                      AND advisor_id = $4
                    ORDER BY transaction_ts DESC
                """, store_id, date_from, date_to, advisor_id)
            else:
                rows = await conn.fetch("""
                    SELECT * FROM pos_transactions
                    WHERE store_id = $1
                      AND transaction_ts BETWEEN $2 AND $3
                    ORDER BY transaction_ts DESC
                """, store_id, date_from, date_to)
            return [dict(r) for r in rows]

    # ── Coaching cards ────────────────────────────────────
    async def insert_coaching_card(self, card: dict) -> str:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO coaching_cards
                  (advisor_id, cycle_id, priority, gap,
                   context, advice, confidence, status)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                RETURNING id
            """,
                card["advisor_id"],
                card.get("cycle_id"),
                card.get("priority", "LOW"),
                card.get("gap", 0.0),
                card.get("context", ""),
                card.get("advice", ""),
                card.get("confidence", 0.0),
                card.get("status", "pending")
            )
            return str(row["id"])

    async def update_card_status(
        self, card_id: str, status: str, manager: str = None
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE coaching_cards SET status = $1 WHERE id = $2
            """, status, card_id)

    # ── Cycles ────────────────────────────────────────────
    async def upsert_cycle(self, cycle: dict) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO agent_cycles
                  (id, store_id, triggered_by, started_at,
                   completed_at, duration_ms, state_snapshot, errors)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                ON CONFLICT (id) DO UPDATE SET
                    completed_at   = EXCLUDED.completed_at,
                    duration_ms    = EXCLUDED.duration_ms,
                    state_snapshot = EXCLUDED.state_snapshot,
                    errors         = EXCLUDED.errors
            """,
                cycle["id"],
                cycle.get("store_id"),
                cycle.get("triggered_by", "manual"),
                cycle.get("started_at",   datetime.utcnow()),
                cycle.get("completed_at"),
                cycle.get("duration_ms"),
                cycle.get("state_snapshot"),
                cycle.get("errors", [])
            )
