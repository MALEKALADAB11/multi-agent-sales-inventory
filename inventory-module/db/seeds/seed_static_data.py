"""
seed_static_data.py
Inserts static configuration data that doesn't come from CSVs:
  - inventory.business_objectives  ← derived from BUSINESS_OBJECTIVE_SETTINGS in config/settings.py
  - inventory.events               ← known recurring events for Tunisia

The four objective modes (user-facing labels):
  cost_savings   → minimize spending, accept moderate stockout risk
  standard       → default operating mode, balanced safety
  market_growth  → proactive stocking for growth / new launches
  high_demand    → maximum availability during known demand peaks

Run from inventory-module/:
    python db/seeds/seed_static_data.py

Re-run safe: existing rows are deleted and re-inserted cleanly.

Migration required before first run (if label/metadata columns don't exist):
    ALTER TABLE inventory.business_objectives
        ADD COLUMN IF NOT EXISTS label    TEXT,
        ADD COLUMN IF NOT EXISTS metadata JSONB;
"""
import asyncio
import logging
import os
import json
from pathlib import Path
from datetime import date

import asyncpg
from dotenv import load_dotenv
MODULE_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(MODULE_ROOT.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


# ── Business objectives ───────────────────────────────────────────────────────
# Label naming is intentionally user-facing (marketing team selects these).
#
# Mapping to config/settings.py → BUSINESS_OBJECTIVE_SETTINGS:
#   cost_savings  ← "cost"
#   standard      ← "balanced"
#   market_growth ← "competitive"
#   high_demand   ← "service_level"
#
# Only `standard` is active by default. Agents switch the active mode
# based on context (upcoming events, business directives, season).

OBJECTIVES = [
    # ── cost_savings ─────────────────────────────────────────────────────
    # "We're clearing stock / tight budget this quarter"
    {
        "objective_type": "minimize_cost",
        "label":          "cost_savings",
        "priority":       1,
        "target_value":   0.75,
        "applies_to":     "all",
        "category":       None,
        "sku":            None,
        "start_date":     date(2025, 1, 1),
        "end_date":       date(2025, 12, 31),
        "is_active":      False,
        "metadata": {
            "safety_stock_factor":  0.8,
            "service_level_target": 0.75,
            "description": "Minimize spending, accept moderate stockout risk",
        },
    },

    # ── standard ─────────────────────────────────────────────────────────
    # "Normal operations, nothing special" — DEFAULT active mode
    {
        "objective_type": "maximize_service_level",
        "label":          "standard",
        "priority":       2,
        "target_value":   0.90,
        "applies_to":     "all",
        "category":       None,
        "sku":            None,
        "start_date":     date(2025, 1, 1),
        "end_date":       date(2025, 12, 31),
        "is_active":      True,
        "metadata": {
            "safety_stock_factor":  1.0,
            "service_level_target": 0.90,
            "description": "Standard safety stock — default operating mode",
        },
    },

    # ── market_growth ─────────────────────────────────────────────────────
    # "We're pushing into new stores / launching products"
    {
        "objective_type": "maximize_service_level",
        "label":          "market_growth",
        "priority":       3,
        "target_value":   0.95,
        "applies_to":     "all",
        "category":       None,
        "sku":            None,
        "start_date":     date(2025, 1, 1),
        "end_date":       date(2025, 12, 31),
        "is_active":      False,
        "metadata": {
            "safety_stock_factor":  1.3,
            "service_level_target": 0.95,
            "description": "Proactive stocking for market presence and growth",
        },
    },

    # ── high_demand ───────────────────────────────────────────────────────
    # "Ramadan is coming / Black Friday campaign starts"
    {
        "objective_type": "maximize_service_level",
        "label":          "high_demand",
        "priority":       4,
        "target_value":   0.98,
        "applies_to":     "all",
        "category":       None,
        "sku":            None,
        "start_date":     date(2025, 1, 1),
        "end_date":       date(2025, 12, 31),
        "is_active":      False,
        "metadata": {
            "safety_stock_factor":  1.5,
            "service_level_target": 0.98,
            "description": "Maximum availability — activate during demand peaks",
        },
    },
]


# ── Demand events ─────────────────────────────────────────────────────────────
# estimated_uplift_pct: expected demand increase as a percentage (30 = +30%)
# affected_categories:  must match category values in product_master.csv

EVENTS_2026 = [
    {
        "event_name":           "Ramadan 2026",
        "event_type":           "religious",
        "start_date":           date(2026, 2, 19),
        "end_date":             date(2026, 3, 19),
        "affected_categories":  ["Recharge", "SIM / Ligne", "Forfait Mobile"],
        "estimated_uplift_pct": 25.0,
        "scope":                "national",
    },
    {
        "event_name":           "Aid El Fitr 2026",
        "event_type":           "religious",
        "start_date":           date(2026, 3, 20),
        "end_date":             date(2026, 3, 22),
        "affected_categories":  ["Terminal", "Accessoire", "Recharge"],
        "estimated_uplift_pct": 40.0,
        "scope":                "national",
    },
    {
        "event_name":           "Aid El Adha 2026",
        "event_type":           "religious",
        "start_date":           date(2026, 5, 27),
        "end_date":             date(2026, 5, 29),
        "affected_categories":  ["Terminal", "Accessoire", "Recharge"],
        "estimated_uplift_pct": 35.0,
        "scope":                "national",
    },
    {
        "event_name":           "Rentrée Scolaire 2026",
        "event_type":           "seasonal",
        "start_date":           date(2026, 9, 1),
        "end_date":             date(2026, 9, 20),
        "affected_categories":  ["Terminal", "Forfait Data", "Accessoire"],
        "estimated_uplift_pct": 30.0,
        "scope":                "national",
    },
    {
        "event_name":           "Black Friday / Promotions Fin Novembre 2026",
        "event_type":           "commercial",
        "start_date":           date(2026, 11, 24),
        "end_date":             date(2026, 12, 2),
        "affected_categories":  ["Terminal", "Accessoire", "Box / Fibre"],
        "estimated_uplift_pct": 50.0,
        "scope":                "national",
    },
    {
        "event_name":           "Fêtes de Fin d'Année 2026",
        "event_type":           "seasonal",
        "start_date":           date(2026, 12, 20),
        "end_date":             date(2027, 1, 5),
        "affected_categories":  ["Terminal", "Accessoire", "Recharge"],
        "estimated_uplift_pct": 20.0,
        "scope":                "national",
    },
]


async def get_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host     = os.getenv("DOCKER_DB_HOST",     "localhost"),
        port     = int(os.getenv("DOCKER_DB_PORT", "5432")),
        database = os.getenv("DOCKER_DB_NAME",     "asc_db"),
        user     = os.getenv("DOCKER_DB_USER",     "asc_user"),
        password = os.getenv("DOCKER_DB_PASSWORD", "asc_password"),
        min_size = 2,
        max_size = 5,
    )


async def seed_objectives(conn: asyncpg.Connection) -> None:
    for obj in OBJECTIVES:
        await conn.execute(
            """
            INSERT INTO inventory.business_objectives
                (objective_type, priority, target_value, applies_to,
                 category, sku, start_date, end_date, is_active, label, metadata)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            """,
            obj["objective_type"],
            obj["priority"],
            obj.get("target_value"),
            obj.get("applies_to", "all"),
            obj.get("category"),
            obj.get("sku"),
            obj.get("start_date"),
            obj.get("end_date"),
            obj.get("is_active", False),
            obj.get("label"),
            json.dumps(obj.get("metadata", {})),
        )
        status = "✓ ACTIVE" if obj["is_active"] else "  inactive"
        logger.info(
            f"    {status}  [{obj['priority']}] {obj['label']:14s} "
            f"→ service_level={obj.get('target_value')}  "
            f"safety_factor={obj['metadata']['safety_stock_factor']}"
        )

    logger.info(f"  Business objectives: {len(OBJECTIVES)} rows")


async def seed_events(conn: asyncpg.Connection) -> None:
    for evt in EVENTS_2026:
        await conn.execute(
            """
            INSERT INTO inventory.events
                (event_name, event_type, start_date, end_date,
                 affected_categories, estimated_uplift_pct, scope)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            """,
            evt["event_name"],
            evt.get("event_type"),
            evt["start_date"],
            evt["end_date"],
            json.dumps(evt.get("affected_categories", [])),
            evt.get("estimated_uplift_pct"),
            evt.get("scope", "national"),
        )
    logger.info(f"  Events: {len(EVENTS_2026)} rows")


async def main():
    logger.info("=" * 55)
    logger.info("Seeding static data: objectives + events")
    logger.info("=" * 55)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM inventory.business_objectives")
        await conn.execute("DELETE FROM inventory.events")

        logger.info("Seeding business objectives...")
        await seed_objectives(conn)

        logger.info("Seeding events...")
        await seed_events(conn)

    await pool.close()
    logger.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
