"""
seed_reference_data.py
========================
Seeds:
  sales.produits           <- data/processed/products.csv  (French, detailed)
  inventory.product_master <- data/processed/products.csv  (English subset, near 1:1)
  sales.boutiques          <- data/processed/stores.csv
  sales.agents             <- data/processed/agents.csv
  supply.supplier_products <- data/processed/products.csv  (supplier_id/lead_time/moq/unit_cost slice)

Columns from products.csv with NO destination anywhere in the schema
(dropped, not guessed): avg_daily_velocity, store_count, stock_min_default,
stock_max_default, forfait_type. These are per-store/derived metrics — the
closest table, inventory.stock_levels, is keyed per (store_id, sku), not
per-product, so a single "default" value doesn't fit that grain either.
Flag for your teammate rather than force a mapping.

Best-guess mappings (verify against real data before trusting reports):
  products.csv:subcategory   -> sales.produits.famille
  products.csv:product_line  -> sales.produits.famille_libelle
  products.csv:flag_4g/5g    -> passed through as-is
  products.csv:category      -> sales.produits.flag_terminal/flag_forfait/
                                 flag_sim/flag_recharge, derived by keyword
                                 match (see _category_flags below) since the
                                 CSV has one category column, not 4 booleans.

Run: python db/seeds/seed_reference_data.py
Re-run safe: existing rows in these 4 tables are deleted first (child-first
order: supplier_products before produits, since it FKs to produits.sku).

Fix (2026-07-09): a blank cell in stores.csv's `responsable` column was
being read by pandas as float NaN, not None/empty-string. asyncpg's text
codec rejects a raw float for a text column ("expected str, got float"),
which crashed seed_boutiques on insert. All optional text fields pulled
from a row via [] or .get() (with or without a default — Series.get()'s
default does NOT catch NaN, only a missing column) are now passed through
_seed_common.clean() first, which converts NaN -> None.

Fix (2026-07-09, #2): sales.boutiques.type_boutique is varchar(5) in the
live schema — narrower than stores.csv:store_type. Inserting a longer
value raised asyncpg.exceptions.StringDataRightTruncationError. Now passed
through _seed_common.clean_trunc(value, 5, ...), which truncates to fit
and logs a warning per row where truncation actually cuts data, instead of
crashing or silently losing characters.
"""
import asyncio
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _seed_common import get_pool, DATA_PROCESSED, clean, clean_trunc

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def _category_flags(category: str) -> dict:
    c = str(category).lower()
    return {
        "flag_terminal": "terminal" in c,
        "flag_forfait":  "forfait" in c or "postpaid" in c,
        "flag_sim":      "sim" in c,
        "flag_recharge": "recharge" in c,
    }


async def seed_produits(conn, df: pd.DataFrame):
    await conn.execute("DELETE FROM sales.produits")
    for _, r in df.iterrows():
        flags = _category_flags(r["category"])
        await conn.execute(
            """
            INSERT INTO sales.produits
                (sku, nom, categorie, famille, marge_pct, marque, pa_ht,
                 famille_libelle, flag_4g, flag_5g, flag_terminal, flag_forfait,
                 flag_sim, flag_recharge, serialisable, lead_time_days,
                 lead_time_std, moq, holding_cost_pct, order_cost, lifecycle_stage)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21)
            """,
            int(r["sku"]), r["product_name"], r["category"], clean(r.get("subcategory")),
            float(r["margin_pct"]) if pd.notna(r.get("margin_pct")) else None,
            clean(r.get("brand")), float(r["unit_cost"]) if pd.notna(r.get("unit_cost")) else None,
            clean(r.get("product_line")), bool(r.get("flag_4g", False)), bool(r.get("flag_5g", False)),
            flags["flag_terminal"], flags["flag_forfait"], flags["flag_sim"], flags["flag_recharge"],
            bool(r.get("is_serialized", False)),
            int(r["lead_time_days"]) if pd.notna(r.get("lead_time_days")) else 14,
            int(r["lead_time_std"]) if pd.notna(r.get("lead_time_std")) else 3,
            int(r["moq"]) if pd.notna(r.get("moq")) else 1,
            float(r["holding_cost_pct"]) if pd.notna(r.get("holding_cost_pct")) else 0.2,
            float(r["order_cost"]) if pd.notna(r.get("order_cost")) else 50,
            clean(r.get("lifecycle_stage")) or "mature",
        )
    logger.info(f"  sales.produits: {len(df)} rows  (prix_ht/prix_ttc left NULL — CSV has unit_price only, "
                "confirm which prix_* column that should fill before relying on margin reports)")


async def seed_product_master(conn, df: pd.DataFrame):
    await conn.execute("DELETE FROM inventory.product_master")
    for _, r in df.iterrows():
        await conn.execute(
            """
            INSERT INTO inventory.product_master
                (sku, product_name, category, unit_cost, unit_price, lead_time_days,
                 lead_time_std, moq, holding_cost_pct, order_cost, lifecycle_stage)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            """,
            int(r["sku"]), r["product_name"], r["category"],
            float(r["unit_cost"]) if pd.notna(r.get("unit_cost")) else None,
            float(r["unit_price"]) if pd.notna(r.get("unit_price")) else None,
            int(r["lead_time_days"]) if pd.notna(r.get("lead_time_days")) else None,
            int(r["lead_time_std"]) if pd.notna(r.get("lead_time_std")) else None,
            int(r["moq"]) if pd.notna(r.get("moq")) else 1,
            float(r["holding_cost_pct"]) if pd.notna(r.get("holding_cost_pct")) else None,
            float(r["order_cost"]) if pd.notna(r.get("order_cost")) else None,
            clean(r.get("lifecycle_stage")),
        )
    logger.info(f"  inventory.product_master: {len(df)} rows  (clean 1:1 match)")


async def seed_supplier_products(conn, df: pd.DataFrame):
    await conn.execute("DELETE FROM supply.supplier_products")
    rows = df.dropna(subset=["supplier_id"])
    for _, r in rows.iterrows():
        await conn.execute(
            """
            INSERT INTO supply.supplier_products
                (supplier_id, sku, lead_time_days, moq, unit_cost, is_preferred)
            VALUES ($1,$2,$3,$4,$5,TRUE)
            ON CONFLICT (supplier_id, sku) DO NOTHING
            """,
            r["supplier_id"], int(r["sku"]),
            int(r["lead_time_days"]) if pd.notna(r.get("lead_time_days")) else None,
            int(r["moq"]) if pd.notna(r.get("moq")) else None,
            float(r["unit_cost"]) if pd.notna(r.get("unit_cost")) else None,
        )
    skipped = len(df) - len(rows)
    logger.info(f"  supply.supplier_products: {len(rows)} rows"
                + (f"  ({skipped} skipped — no supplier_id)" if skipped else ""))


async def seed_boutiques(conn, df: pd.DataFrame):
    await conn.execute("DELETE FROM sales.boutiques")
    for _, r in df.iterrows():
        await conn.execute(
            """
            INSERT INTO sales.boutiques
                (store_id, store_name, ville, region, manager_name, active,
                 type_boutique, canal, email_store, is_officielle)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            """,
            r["store_id"], r["store_name"], clean(r.get("ville")), clean(r.get("region")),
            clean(r.get("responsable")), bool(r.get("is_active", True)),
            clean_trunc(r.get("store_type"), 5, field="type_boutique"),
            clean(r.get("channel")) or "PHYSIQUE",
            clean(r.get("email")), bool(r.get("is_official", False)),
        )
    logger.info(f"  sales.boutiques: {len(df)} rows  (address/phone/wilaya/zone_commerciale/"
                "latitude/longitude/capacite_conseillers/date_ouverture not in source, left default/NULL)")


async def seed_agents(conn, df: pd.DataFrame):
    await conn.execute("DELETE FROM sales.agents")
    skipped = []
    for _, r in df.iterrows():
        try:
            agent_id = int(r["agent_id"])
        except (ValueError, TypeError):
            skipped.append((r["agent_id"], r.get("store_id"), r.get("agent_name")))
            continue
        full_name = f"{r.get('agent_name','')} {r.get('agent_surname','')}".strip()
        await conn.execute(
            """
            INSERT INTO sales.agents
                (agent_id, agent_name, store_id, performance_level, date_embauche,
                 niveau_certification, quota_mensuel_ca, quota_activations,
                 quota_postpaye, coach_score, anciennete_mois)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            """,
            agent_id, full_name, r["store_id"], clean(r.get("performance_level")),
            pd.to_datetime(r["hire_date"]).date() if pd.notna(r.get("hire_date")) else None,
            int(r["certification_level"]) if pd.notna(r.get("certification_level")) else 1,
            float(r["monthly_ca_quota"]) if pd.notna(r.get("monthly_ca_quota")) else None,
            int(r["activation_quota"]) if pd.notna(r.get("activation_quota")) else 60,
            int(r["postpaid_quota"]) if pd.notna(r.get("postpaid_quota")) else 10,
            float(r["coach_score"]) if pd.notna(r.get("coach_score")) else 0.0,
            int(r["tenure_months"]) if pd.notna(r.get("tenure_months")) else 12,
        )
    for aid, store, name in skipped:
        logger.warning(
            f"  \u26a0 sales.agents: skipped agent_id={aid!r} (store={store}, name={name!r}) "
            f"— non-integer id, sales.agents.agent_id is integer NOT NULL. "
            f"Row not inserted; nothing downstream in these seed scripts references this id."
        )
    logger.info(f"  sales.agents: {len(df) - len(skipped)} rows"
                + (f"  ({len(skipped)} skipped — non-integer agent_id, see warnings above)" if skipped else "")
                + "  (avg_monthly_revenue/total_revenue_3y/rank_in_store dropped — "
                "no column; role/phone/email/specialisation/avatar_color left NULL)")


async def main():
    pool = await get_pool()
    async with pool.acquire() as conn:
        products = pd.read_csv(DATA_PROCESSED / "products.csv")
        stores   = pd.read_csv(DATA_PROCESSED / "stores.csv")
        agents   = pd.read_csv(DATA_PROCESSED / "agents.csv")

        # Boutiques before agents (agent_id has no FK in baseline, but keep
        # store-then-agent order in case one gets added later).
        await seed_boutiques(conn, stores)
        # Produits before product_master/supplier_products (both FK sku).
        await seed_produits(conn, products)
        await seed_product_master(conn, products)
        await seed_supplier_products(conn, products)
        await seed_agents(conn, agents)

    await pool.close()
    logger.info("Done: sales.produits, inventory.product_master, sales.boutiques, "
                "sales.agents, supply.supplier_products")


if __name__ == "__main__":
    asyncio.run(main())
