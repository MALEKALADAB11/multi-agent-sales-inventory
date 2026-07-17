"""
seed_static_data.py  (fixed)
==============================
Seeds:
  inventory.business_objectives  <- hardcoded OBJECTIVES below
  inventory.events               <- hardcoded EVENTS_HISTORICAL below

Fixes vs. the version you had:
- Schema was "inv." everywhere — the real schema is "inventory." (both
  tables exist in the baseline under that name, confirmed from
  0001_baseline.sql). No migration needed for that part.
- inventory.business_objectives in the live schema only has
  (id, objective_type, label, description, is_active, priority, created_at)
  — no target_value, applies_to, category, sku, start_date, end_date, or
  metadata jsonb. Rather than writing a migration to add those columns back,
  this version just inserts what the table actually has; target_value and
  safety_stock_factor from your original OBJECTIVES list are folded into the
  `description` text instead of being dropped silently.
- inventory.events.affected_categories is `text`, not jsonb — stored as a
  comma-joined string instead of json.dumps'd.

Fix (2026-07-16): inventory.events was seeded with only 6 hand-picked 2026
events and a single flat estimated_uplift_pct guess per event, while
market.events (seed_market_reference.py) carries 33 events from 2023-2026
with a real per-category uplift breakdown (uplift_terminal, uplift_forfait,
uplift_sim, uplift_recharge, uplift_accessoire). That made market.events
meaningfully more useful than inventory.events for the same underlying
calendar. EVENTS_HISTORICAL below replaces EVENTS_2026: it's the same 33
events, mirrored 1:1 from the market.events rows (name, dates, scope,
affected categories), so the two tables now agree on what happened and when.

inventory.events has NO columns for per-category uplift (only a single
estimated_uplift_pct numeric and a separate estimated_uplift currency
value) — so the 5 category-level uplifts from market.events had to be
collapsed into one number per event. Taking a plain average would wash out
the signal (e.g. Eid Al-Fitr 2024's uplift_recharge=150 vs uplift_sim=30
average out to something misleading), and taking a plain max would hide
negative events entirely (competitor promos in market.events are negative
uplifts — a discount by Orange/TT that *reduces* our demand — and max()
would report those as 0 instead of the intended negative signal). So
estimated_uplift_pct here is the uplift value with the largest absolute
magnitude, sign preserved. That keeps the strongest single driver per
event and correctly reports competitor-promo events as negative instead
of flattening them to 0 or to their weakest positive component.

event_type values are also more granular now, matching market.events'
vocabulary instead of overloading "seasonal" for both back-to-school and
year-end holidays as the old EVENTS_2026 did:
  RELIGIEUX -> religious | SCOLAIRE -> academic | NATIONAL -> national
  SPORTIF -> sportif | COMMERCIAL -> commercial | CONCURRENTIEL -> competitive
inventory.events has no CHECK constraint on event_type (unlike
market.events), so this is a naming-consistency choice, not a schema
requirement — but keeping the vocabularies aligned makes cross-table
lookups (e.g. joining on event_name) far less error-prone.

Note on garbled accented characters (RentrÚe, FÛtes, AnnÚe, etc.) seen in
some psql sessions: that is NOT data corruption. It's the Windows console
codepage mismatch psql itself warns about at login ("Console code page
(850) differs from Windows code page (1252)"). The UTF-8 bytes stored in
Postgres are correct; only that terminal's rendering is wrong. Nothing to
fix here — `chcp 65001` before launching psql, or a GUI client like
pgAdmin/DBeaver, will display the accents correctly.

These two tables have no CSV source (they're small, hand-curated config,
not synthetic bulk data) — that's why they're seeded from constants here
instead of via a CSV loader like the other seed_*.py scripts.

Run: python db/seeds/seed_static_data.py
Re-run safe: existing rows in both tables are deleted first.
"""
import asyncio
import logging
import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _seed_common import get_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

OBJECTIVES = [
    {
        "objective_type": "minimize_cost", "label": "cost_savings", "priority": 1,
        "is_active": False,
        "description": "Minimize spending, accept moderate stockout risk "
                        "(service_level=0.75, safety_stock_factor=0.8)",
    },
    {
        "objective_type": "maximize_service_level", "label": "standard", "priority": 2,
        "is_active": True,
        "description": "Standard safety stock — default operating mode "
                        "(service_level=0.90, safety_stock_factor=1.0)",
    },
    {
        "objective_type": "maximize_service_level", "label": "market_growth", "priority": 3,
        "is_active": False,
        "description": "Proactive stocking for market presence and growth "
                        "(service_level=0.95, safety_stock_factor=1.3)",
    },
    {
        "objective_type": "maximize_service_level", "label": "high_demand", "priority": 4,
        "is_active": False,
        "description": "Maximum availability — activate during demand peaks "
                        "(service_level=0.98, safety_stock_factor=1.5)",
    },
]

# Mirrored 1:1 from market.events (seed_market_reference.py). Dates, scope,
# and affected_categories are copied straight across. estimated_uplift_pct
# is the signed uplift_* value with the largest absolute magnitude for that
# event (see docstring above for why not average/max).
EVENTS_HISTORICAL = [
    {"event_name": "Ramadan_2023", "event_type": "religious",
     "start_date": date(2023, 3, 22), "end_date": date(2023, 4, 20),
     "affected_categories": ["Forfait Mobile", "Recharge"],
     "estimated_uplift_pct": 25.0, "scope": "national"},
    {"event_name": "Eid_Al_Fitr_2023", "event_type": "religious",
     "start_date": date(2023, 4, 21), "end_date": date(2023, 4, 23),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge"],
     "estimated_uplift_pct": 35.0, "scope": "national"},
    {"event_name": "Eid_Al_Adha_2023", "event_type": "religious",
     "start_date": date(2023, 6, 27), "end_date": date(2023, 6, 29),
     "affected_categories": ["Forfait Mobile", "Recharge"],
     "estimated_uplift_pct": 20.0, "scope": "national"},
    {"event_name": "Summer_Sale_2023", "event_type": "commercial",
     "start_date": date(2023, 7, 1), "end_date": date(2023, 8, 31),
     "affected_categories": ["Terminal", "Accessoire"],
     "estimated_uplift_pct": 15.0, "scope": "national"},
    {"event_name": "Rentree_2023", "event_type": "academic",
     "start_date": date(2023, 9, 1), "end_date": date(2023, 9, 20),
     "affected_categories": ["Terminal", "Accessoire"],
     "estimated_uplift_pct": 30.0, "scope": "national"},
    {"event_name": "Year_End_2023", "event_type": "commercial",
     "start_date": date(2023, 12, 15), "end_date": date(2023, 12, 31),
     "affected_categories": ["Terminal", "Forfait Mobile", "Accessoire"],
     "estimated_uplift_pct": 20.0, "scope": "national"},
    {"event_name": "Fete_Independance_2024", "event_type": "national",
     "start_date": date(2024, 3, 20), "end_date": date(2024, 3, 20),
     "affected_categories": ["Forfait Mobile", "Recharge"],
     "estimated_uplift_pct": 10.0, "scope": "national"},
    {"event_name": "Ramadan 2024", "event_type": "religious",
     "start_date": date(2024, 3, 11), "end_date": date(2024, 4, 9),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 60.0, "scope": "national"},
    {"event_name": "Eid Al-Fitr 2024", "event_type": "religious",
     "start_date": date(2024, 4, 10), "end_date": date(2024, 4, 12),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 150.0, "scope": "national"},
    {"event_name": "Eid Al-Adha 2024", "event_type": "religious",
     "start_date": date(2024, 6, 16), "end_date": date(2024, 6, 18),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 80.0, "scope": "national"},
    {"event_name": "Rentree Scolaire 2024", "event_type": "academic",
     "start_date": date(2024, 9, 1), "end_date": date(2024, 9, 20),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 55.0, "scope": "national"},
    {"event_name": "Soldes Hiver 2024", "event_type": "commercial",
     "start_date": date(2024, 11, 25), "end_date": date(2025, 1, 5),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 65.0, "scope": "national"},
    {"event_name": "Noel 2024", "event_type": "commercial",
     "start_date": date(2024, 12, 24), "end_date": date(2024, 12, 31),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 50.0, "scope": "national"},
    {"event_name": "Yennayer 2025", "event_type": "national",
     "start_date": date(2025, 1, 12), "end_date": date(2025, 1, 14),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 25.0, "scope": "national"},
    {"event_name": "CAN 2025", "event_type": "sportif",
     "start_date": date(2025, 1, 21), "end_date": date(2025, 2, 18),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 35.0, "scope": "national"},
    {"event_name": "Ramadan 2025", "event_type": "religious",
     "start_date": date(2025, 3, 1), "end_date": date(2025, 3, 29),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 65.0, "scope": "national"},
    {"event_name": "Eid Al-Fitr 2025", "event_type": "religious",
     "start_date": date(2025, 3, 30), "end_date": date(2025, 4, 1),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 160.0, "scope": "national"},
    {"event_name": "Fete Independance 2025", "event_type": "national",
     "start_date": date(2025, 3, 20), "end_date": date(2025, 3, 20),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 15.0, "scope": "national"},
    {"event_name": "Eid Al-Adha 2025", "event_type": "religious",
     "start_date": date(2025, 6, 6), "end_date": date(2025, 6, 8),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 85.0, "scope": "national"},
    {"event_name": "Lancement 5G Ooredoo", "event_type": "competitive",
     "start_date": date(2025, 7, 1), "end_date": date(2025, 7, 31),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 55.0, "scope": "national"},
    {"event_name": "Rentree Scolaire 2025", "event_type": "academic",
     "start_date": date(2025, 9, 2), "end_date": date(2025, 9, 22),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 60.0, "scope": "national"},
    {"event_name": "Mawlid 2025", "event_type": "religious",
     "start_date": date(2025, 9, 4), "end_date": date(2025, 9, 6),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 35.0, "scope": "national"},
    {"event_name": "Soldes Hiver 2025", "event_type": "commercial",
     "start_date": date(2025, 11, 28), "end_date": date(2026, 1, 6),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 70.0, "scope": "national"},
    {"event_name": "Noel 2025", "event_type": "commercial",
     "start_date": date(2025, 12, 24), "end_date": date(2025, 12, 31),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 55.0, "scope": "national"},
    {"event_name": "Yennayer 2026", "event_type": "national",
     "start_date": date(2026, 1, 12), "end_date": date(2026, 1, 14),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 28.0, "scope": "national"},
    {"event_name": "Ramadan 2026", "event_type": "religious",
     "start_date": date(2026, 2, 17), "end_date": date(2026, 3, 18),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 70.0, "scope": "national"},
    {"event_name": "Eid Al-Fitr 2026", "event_type": "religious",
     "start_date": date(2026, 3, 20), "end_date": date(2026, 3, 22),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 170.0, "scope": "national"},
    {"event_name": "Fete Independance 2026", "event_type": "national",
     "start_date": date(2026, 3, 20), "end_date": date(2026, 3, 20),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 15.0, "scope": "national"},
    {"event_name": "Promo Orange Summer 2025", "event_type": "competitive",
     "start_date": date(2025, 7, 15), "end_date": date(2025, 8, 15),
     "affected_categories": ["Terminal", "Forfait Mobile", "Accessoire"],
     "estimated_uplift_pct": -12.0, "scope": "national"},
    {"event_name": "Promo TT Rentree 2025", "event_type": "competitive",
     "start_date": date(2025, 8, 25), "end_date": date(2025, 9, 15),
     "affected_categories": ["Terminal", "Forfait Mobile", "Accessoire"],
     "estimated_uplift_pct": -8.0, "scope": "national"},
    {"event_name": "Eid Al-Adha 2026", "event_type": "religious",
     "start_date": date(2026, 5, 27), "end_date": date(2026, 5, 29),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 88.0, "scope": "national"},
    {"event_name": "Soldes Ete 2025", "event_type": "commercial",
     "start_date": date(2025, 7, 1), "end_date": date(2025, 7, 31),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 30.0, "scope": "national"},
    {"event_name": "Soldes Ete 2026", "event_type": "commercial",
     "start_date": date(2026, 7, 1), "end_date": date(2026, 7, 31),
     "affected_categories": ["Terminal", "Forfait Mobile", "Recharge", "Accessoire"],
     "estimated_uplift_pct": 32.0, "scope": "national"},
]


async def seed_objectives(conn):
    await conn.execute("DELETE FROM inventory.business_objectives")
    for obj in OBJECTIVES:
        await conn.execute(
            """
            INSERT INTO inventory.business_objectives
                (objective_type, label, description, is_active, priority)
            VALUES ($1,$2,$3,$4,$5)
            """,
            obj["objective_type"], obj["label"], obj["description"],
            obj["is_active"], obj["priority"],
        )
    logger.info(f"  inventory.business_objectives: {len(OBJECTIVES)} rows")


async def seed_events(conn):
    await conn.execute("DELETE FROM inventory.events")
    for evt in EVENTS_HISTORICAL:
        await conn.execute(
            """
            INSERT INTO inventory.events
                (event_name, event_type, start_date, end_date,
                 affected_categories, estimated_uplift_pct, scope)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            """,
            evt["event_name"], evt["event_type"], evt["start_date"], evt["end_date"],
            ",".join(evt["affected_categories"]), evt["estimated_uplift_pct"],
            evt.get("scope", "national"),
        )
    logger.info(f"  inventory.events: {len(EVENTS_HISTORICAL)} rows  "
                "(sku/store_id/impact_pct/estimated_uplift left NULL/default — these are "
                "national/category-level events, not per-sku/store ones; estimated_uplift_pct "
                "is the largest-magnitude signed uplift across categories, mirrored from "
                "market.events — see module docstring)")


async def main():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await seed_objectives(conn)
        await seed_events(conn)
    await pool.close()
    logger.info("Done: inventory.business_objectives, inventory.events")


if __name__ == "__main__":
    asyncio.run(main())