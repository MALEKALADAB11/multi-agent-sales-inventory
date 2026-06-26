from datetime import date, datetime
from data.repositories.postgres_repo import PostgresRepo


class PostgresTools:

    def __init__(self, repo: PostgresRepo):
        self.repo = repo

    async def query_pos_transactions(
        self,
        store_id:   str,
        date_from:  str,
        date_to:    str,
        advisor_id: str = None
    ) -> dict:
        rows = await self.repo.get_pos_transactions(
            store_id   = store_id,
            date_from  = datetime.fromisoformat(date_from),
            date_to    = datetime.fromisoformat(date_to),
            advisor_id = advisor_id
        )
        return {
            "transactions": rows,
            "total_ca":     sum(r["amount"] for r in rows),
            "total_units":  sum(r["units"] for r in rows),
            "count":        len(rows)
        }

    async def get_advisor_profile(self, advisor_id: str) -> dict:
        return await self.repo.get_advisor(advisor_id)

    async def get_daily_targets(
        self,
        store_id: str,
        target_date: str = None
    ) -> list:
        d = date.fromisoformat(target_date) if target_date else date.today()
        return await self.repo.get_targets_for_date(store_id, d)

    async def get_sales_by_sku(
        self,
        store_id:  str,
        days_back: int = 90
    ) -> dict:
        return await self.repo.get_sales_aggregated_by_sku(store_id, days_back)

    async def save_coaching_card(self, card: dict) -> str:
        return await self.repo.insert_coaching_card(card)

    async def save_cycle_snapshot(self, cycle: dict) -> None:
        await self.repo.upsert_cycle(cycle)

    async def get_advisors_by_store(self, store_id: str) -> list:
        return await self.repo.get_advisors(store_id)

    async def get_store(self, store_id: str) -> dict:
        return await self.repo.get_store(store_id)
