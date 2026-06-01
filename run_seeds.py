"""
run_seeds.py — Lance les 3 seeds inventory dans l'ordre correct :
  1. seed_inventory.py     → inv.stores + inv.products + inv.promotions
  2. seed_static_data.py   → inv.business_objectives + inv.events
  3. init_stock_levels.py  → inv.stock_levels (avec calculs demand stats)

Pré-requis : schéma inv déjà créé (setup_inv_schema.py)
"""
import asyncio
import logging
import os
import sys
import json
from pathlib import Path

import pandas as pd
import asyncpg
from dotenv import load_dotenv
from datetime import date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Chemins ───────────────────────────────────────────────────────────────────
BASE      = Path(r"C:\Users\malek\Desktop\PFE-Backend")
INV_ROOT  = BASE / "inventory-module"
PROCESSED = INV_ROOT / "data" / "processed"
FORECAST  = INV_ROOT / "data" / "forecasts"

# Ajouter le path pour les imports
sys.path.insert(0, str(INV_ROOT))
sys.path.insert(0, str(BASE))

# Charger le .env racine
load_dotenv(BASE / ".env", override=True)

# ── DB Config (depuis .env racine) ────────────────────────────────────────────
DB_CONFIG = dict(
    host     = os.getenv("DB_HOST",     "localhost"),
    port     = int(os.getenv("DB_PORT", "5432")),
    database = os.getenv("DB_NAME",     "ooredoo_sales"),
    user     = os.getenv("DB_USER",     "postgres"),
    password = os.getenv("DB_PASSWORD", "admin"),
    min_size = 2,
    max_size = 10,
)

logger.info(f"DB: {DB_CONFIG['database']} @ {DB_CONFIG['host']}:{DB_CONFIG['port']}")

# ── Fichiers CSV ──────────────────────────────────────────────────────────────
PRODUCT_FILE    = PROCESSED / "product_master.csv"
SALES_FILE      = PROCESSED / "sales_history.csv"
STOCK_FILE      = PROCESSED / "stock_history.csv"
PROMOTIONS_FILE = PROCESSED / "promotions.csv"
FORECAST_FILE   = FORECAST  / "timesFM_future_forecast.csv"


# ══════════════════════════════════════════════════════════════
# SEED 1 — stores + products + promotions
# ══════════════════════════════════════════════════════════════

async def seed_stores(conn) -> int:
    """Union stores depuis sales_history + stock_history + public.boutiques."""
    stores = {}

    # Depuis CSVs
    for csv_path in [SALES_FILE, STOCK_FILE]:
        if not csv_path.exists():
            continue
        df = pd.read_csv(csv_path,
                         usecols=["store_id","store_name","region"],
                         dtype={"store_id": str}, low_memory=False)
        for _, r in df.drop_duplicates("store_id").dropna(subset=["store_id"]).iterrows():
            sid = str(r["store_id"]).strip()
            if sid and sid.lower() != "nan":
                stores[sid] = {
                    "store_name": str(r.get("store_name", sid)).strip(),
                    "region":     str(r["region"]).strip() if pd.notna(r.get("region")) else None,
                }

    inserted = 0
    for sid, info in stores.items():
        await conn.execute("""
            INSERT INTO inv.stores (store_id, store_name, region, active)
            VALUES ($1, $2, $3, TRUE)
            ON CONFLICT (store_id) DO UPDATE SET
                store_name = EXCLUDED.store_name,
                region     = COALESCE(EXCLUDED.region, inv.stores.region),
                updated_at = NOW()
        """, sid, info["store_name"], info["region"])
        inserted += 1

    # Complément depuis public.boutiques
    pg_stores = await conn.fetch("SELECT store_id, store_name, ville FROM public.boutiques")
    for r in pg_stores:
        sid = str(r["store_id"]).strip()
        if not sid:
            continue
        await conn.execute("""
            INSERT INTO inv.stores (store_id, store_name, region, active)
            VALUES ($1, $2, $3, TRUE)
            ON CONFLICT (store_id) DO NOTHING
        """, sid, str(r["store_name"] or sid), str(r["ville"] or "Tunisie"))
        inserted += 1

    logger.info(f"  ✓ inv.stores: {inserted} stores")
    return inserted


async def seed_products(conn) -> int:
    """Products depuis product_master.csv + public.produits."""
    inserted = 0

    if PRODUCT_FILE.exists():
        df = pd.read_csv(PRODUCT_FILE, dtype={"sku": str})
        for _, r in df.iterrows():
            sku = str(r.get("sku", "")).strip()
            if not sku or sku.lower() == "nan":
                continue

            def f(col): 
                v = r.get(col)
                return float(v) if pd.notna(v) else None
            def i(col):
                v = r.get(col)
                return int(float(v)) if pd.notna(v) else None

            stage = str(r.get("lifecycle_stage","")).strip().lower()
            if stage not in ("growth","mature","decline","discontinued"):
                stage = "mature"

            await conn.execute("""
                INSERT INTO inv.products
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
                sku, str(r.get("product_name", sku)),
                str(r["category"]) if pd.notna(r.get("category")) else None,
                f("unit_cost"), f("unit_price"),
                i("lead_time_days"), f("lead_time_std"),
                i("moq"), f("holding_cost_pct"),
                f("order_cost"), stage,
            )
            inserted += 1

    # Complément depuis public.produits
    pg_prods = await conn.fetch("""
        SELECT cod_prod, des_prod, categorie, pa_ttc, pv_ttc, actif
        FROM public.produits
    """)
    for p in pg_prods:
        sku = str(p["cod_prod"]).strip()
        if not sku:
            continue
        stage = "mature" if str(p["actif"]) == "O" else "discontinued"
        await conn.execute("""
            INSERT INTO inv.products
                (sku, product_name, category, unit_cost, unit_price, lifecycle_stage)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (sku) DO NOTHING
        """,
            sku,
            str(p["des_prod"] or sku)[:200],
            str(p["categorie"] or "Autre")[:100],
            float(p["pa_ttc"] or 0),
            float(p["pv_ttc"] or 0),
            stage,
        )
        inserted += 1

    logger.info(f"  ✓ inv.products: {inserted} produits")
    return inserted


async def seed_promotions(conn) -> int:
    """Promotions depuis promotions.csv."""
    if not PROMOTIONS_FILE.exists():
        logger.warning(f"  ⚠ {PROMOTIONS_FILE} non trouvé — skip")
        return 0

    valid_skus = {r["sku"] for r in await conn.fetch("SELECT sku FROM inv.products")}
    df = pd.read_csv(PROMOTIONS_FILE, parse_dates=["start_date","end_date"],
                     dtype={"promo_id": str, "sku": str})
    today = pd.Timestamp.now().date()
    inserted = 0

    for _, r in df.iterrows():
        promo_id = str(r.get("promo_id","")).strip()
        if not promo_id or promo_id.lower() == "nan":
            continue
        sku_raw = str(r.get("sku","")).strip()
        sku = sku_raw if (sku_raw and sku_raw.lower() != "nan" and sku_raw in valid_skus) else None
        scope_raw = str(r.get("scope","")).strip().lower()
        scope = scope_raw if scope_raw in ("national","regional","store") else "national"
        end_date  = r["end_date"].date()
        is_active = r["start_date"].date() <= today <= end_date

        await conn.execute("""
            INSERT INTO inv.promotions
                (promo_id, promo_name, promo_type, start_date, end_date,
                 sku, category, discount_pct, scope, is_active)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (promo_id) DO UPDATE SET
                promo_name   = EXCLUDED.promo_name,
                end_date     = EXCLUDED.end_date,
                discount_pct = EXCLUDED.discount_pct,
                is_active    = EXCLUDED.is_active
        """,
            promo_id,
            str(r.get("promo_name", promo_id)),
            str(r["promo_type"]) if pd.notna(r.get("promo_type")) else None,
            r["start_date"].date(), end_date, sku,
            str(r["category"]) if pd.notna(r.get("category")) else None,
            float(r["discount_pct"]) if pd.notna(r.get("discount_pct")) else None,
            scope, is_active,
        )
        inserted += 1

    logger.info(f"  ✓ inv.promotions: {inserted} promotions")
    return inserted


# ══════════════════════════════════════════════════════════════
# SEED 2 — business_objectives + events (static data)
# ══════════════════════════════════════════════════════════════

OBJECTIVES = [
    {
        "objective_type": "minimize_cost",
        "label": "cost_savings",
        "priority": 1,
        "target_value": 0.75,
        "is_active": False,
        "metadata": {"safety_stock_factor": 0.8, "service_level_target": 0.75,
                     "description": "Minimize spending, accept moderate stockout risk"},
    },
    {
        "objective_type": "maximize_service_level",
        "label": "standard",
        "priority": 2,
        "target_value": 0.90,
        "is_active": True,
        "metadata": {"safety_stock_factor": 1.0, "service_level_target": 0.90,
                     "description": "Standard safety stock — default operating mode"},
    },
    {
        "objective_type": "maximize_service_level",
        "label": "market_growth",
        "priority": 3,
        "target_value": 0.95,
        "is_active": False,
        "metadata": {"safety_stock_factor": 1.3, "service_level_target": 0.95,
                     "description": "Proactive stocking for market growth"},
    },
    {
        "objective_type": "maximize_service_level",
        "label": "high_demand",
        "priority": 4,
        "target_value": 0.98,
        "is_active": False,
        "metadata": {"safety_stock_factor": 1.5, "service_level_target": 0.98,
                     "description": "Maximum availability during demand peaks"},
    },
]

EVENTS_2026 = [
    {"event_name":"Ramadan 2026","event_type":"religious",
     "start_date":date(2026,2,19),"end_date":date(2026,3,19),
     "affected_categories":["Recharge","SIM / Ligne","Forfait Mobile"],"estimated_uplift_pct":25.0},
    {"event_name":"Aid El Fitr 2026","event_type":"religious",
     "start_date":date(2026,3,20),"end_date":date(2026,3,22),
     "affected_categories":["Terminal","Accessoire","Recharge"],"estimated_uplift_pct":40.0},
    {"event_name":"Aid El Adha 2026","event_type":"religious",
     "start_date":date(2026,5,27),"end_date":date(2026,5,29),
     "affected_categories":["Terminal","Accessoire","Recharge"],"estimated_uplift_pct":35.0},
    {"event_name":"Rentrée Scolaire 2026","event_type":"seasonal",
     "start_date":date(2026,9,1),"end_date":date(2026,9,20),
     "affected_categories":["Terminal","Forfait Data","Accessoire"],"estimated_uplift_pct":30.0},
    {"event_name":"Black Friday 2026","event_type":"commercial",
     "start_date":date(2026,11,24),"end_date":date(2026,12,2),
     "affected_categories":["Terminal","Accessoire","Box / Fibre"],"estimated_uplift_pct":50.0},
    {"event_name":"Fêtes Fin Année 2026","event_type":"seasonal",
     "start_date":date(2026,12,20),"end_date":date(2027,1,5),
     "affected_categories":["Terminal","Accessoire","Recharge"],"estimated_uplift_pct":20.0},
]


async def seed_static_data(conn) -> None:
    # Ajouter les colonnes label/metadata si manquantes
    await conn.execute("""
        ALTER TABLE inv.business_objectives
            ADD COLUMN IF NOT EXISTS label    TEXT,
            ADD COLUMN IF NOT EXISTS metadata JSONB
    """)

    # Vider et réinsérer
    await conn.execute("DELETE FROM inv.business_objectives")
    await conn.execute("DELETE FROM inv.events")

    for obj in OBJECTIVES:
        await conn.execute("""
            INSERT INTO inv.business_objectives
                (objective_type, priority, target_value, applies_to,
                 start_date, end_date, is_active, label, metadata)
            VALUES ($1,$2,$3,'all',$4,$5,$6,$7,$8)
        """,
            obj["objective_type"], obj["priority"], obj["target_value"],
            date(2025,1,1), date(2026,12,31),
            obj["is_active"], obj["label"],
            json.dumps(obj["metadata"]),
        )
        status = "✓ ACTIVE" if obj["is_active"] else "  inactive"
        logger.info(f"    {status}  [{obj['priority']}] {obj['label']}")

    for evt in EVENTS_2026:
        await conn.execute("""
            INSERT INTO inv.events
                (event_name, event_type, start_date, end_date,
                 affected_categories, estimated_uplift_pct, scope)
            VALUES ($1,$2,$3,$4,$5,$6,'national')
        """,
            evt["event_name"], evt["event_type"],
            evt["start_date"], evt["end_date"],
            json.dumps(evt["affected_categories"]),
            evt["estimated_uplift_pct"],
        )

    logger.info(f"  ✓ inv.business_objectives: {len(OBJECTIVES)}")
    logger.info(f"  ✓ inv.events: {len(EVENTS_2026)}")


# ══════════════════════════════════════════════════════════════
# SEED 3 — stock_levels depuis CSV (calculs demand stats)
# ══════════════════════════════════════════════════════════════

async def init_stock_levels(conn) -> int:
    """Initialise inv.stock_levels avec les calculs de la collègue."""
    logger.info("  Chargement sales_history.csv...")
    sales_df = pd.read_csv(SALES_FILE, dtype={"store_id":str,"sku":str}, low_memory=False)
    logger.info("  Chargement stock_history.csv...")
    stock_df = pd.read_csv(STOCK_FILE, dtype={"store_id":str,"sku":str}, low_memory=False)

    # Demand stats (90 derniers jours)
    sales_df["date"] = pd.to_datetime(sales_df["date"])
    cutoff = sales_df["date"].max() - pd.Timedelta(days=90)
    recent = sales_df[sales_df["date"] >= cutoff]
    stats = (
        recent.groupby(["sku","store_id"])["quantity_sold"]
        .agg(["sum","count"]).reset_index()
    )
    stats["avg_daily_demand"] = (stats["sum"] / stats["count"]).clip(lower=0.01)
    demand_map = {(r["sku"],r["store_id"]): r["avg_daily_demand"] for _,r in stats.iterrows()}

    # Latest stock par (sku, store_id)
    stock_df["date"] = pd.to_datetime(stock_df["date"])
    latest = (
        stock_df.sort_values("date")
        .groupby(["sku","store_id"])
        .last().reset_index()
    )
    stock_map = {(r["sku"],r["store_id"]): int(r["stock_level"]) for _,r in latest.iterrows()}

    # Infos produits
    products = await conn.fetch("SELECT sku, lead_time_days, moq FROM inv.products")
    product_info = {
        r["sku"]: {"lead_time_days": r["lead_time_days"] or 7, "moq": r["moq"] or 10}
        for r in products
    }
    valid_stores = {r["store_id"] for r in await conn.fetch("SELECT store_id FROM inv.stores")}

    count = skipped_sku = skipped_store = 0
    for (sku, store_id), stock_current in stock_map.items():
        if store_id not in valid_stores:
            skipped_store += 1
            continue
        if sku not in product_info:
            skipped_sku += 1
            continue

        info       = product_info[sku]
        lead_time  = info["lead_time_days"]
        moq        = info["moq"]
        avg_demand = demand_map.get((sku,store_id), 1.0)
        stock_min  = max(1, round(avg_demand * lead_time))
        stock_max  = max(moq * 3, round(avg_demand * (lead_time + 7)))
        remaining  = round(stock_current / avg_demand, 1) if avg_demand > 0 else None

        await conn.execute("""
            INSERT INTO inv.stock_levels
                (sku, store_id, stock_current, stock_in_transit,
                 stock_min, stock_max, remaining_days_of_stock, last_updated)
            VALUES ($1,$2,$3,0,$4,$5,$6,NOW())
            ON CONFLICT (sku, store_id) DO UPDATE SET
                stock_current           = EXCLUDED.stock_current,
                stock_min               = EXCLUDED.stock_min,
                stock_max               = EXCLUDED.stock_max,
                remaining_days_of_stock = EXCLUDED.remaining_days_of_stock,
                last_updated            = NOW()
        """, sku, store_id, stock_current, stock_min, stock_max, remaining)
        count += 1

    logger.info(
        f"  ✓ inv.stock_levels: {count} rows | "
        f"skipped_sku={skipped_sku} | skipped_store={skipped_store}"
    )
    return count


# ══════════════════════════════════════════════════════════════
# VÉRIFICATION FINALE
# ══════════════════════════════════════════════════════════════

async def verify(conn):
    logger.info("\n=== VÉRIFICATION FINALE ===")
    tables = ["stores","products","stock_levels","promotions","events",
              "business_objectives","demand_forecast","alerts",
              "recommendations","context_adjustments","agent_runs"]
    for t in tables:
        row = await conn.fetchrow(f"SELECT COUNT(*) AS n FROM inv.{t}")
        n = row["n"]
        s = "✓" if n > 0 else "○"
        logger.info(f"  {s} inv.{t}: {n:,}")


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

async def main():
    logger.info("╔══════════════════════════════════════════════════════╗")
    logger.info("║  RUN SEEDS — Inventory Schema inv                   ║")
    logger.info("╚══════════════════════════════════════════════════════╝")

    # Vérifier les CSV requis
    missing = [f for f in [SALES_FILE, STOCK_FILE, PRODUCT_FILE] if not f.exists()]
    if missing:
        for f in missing:
            logger.error(f"CSV manquant: {f}")
        sys.exit(1)

    pool = await asyncpg.create_pool(**DB_CONFIG)

    async with pool.acquire() as conn:
        # ── Seed 1 ─────────────────────────────────────────
        logger.info("\n=== SEED 1 — stores + products + promotions ===")
        await seed_stores(conn)
        await seed_products(conn)
        await seed_promotions(conn)

        # ── Seed 2 ─────────────────────────────────────────
        logger.info("\n=== SEED 2 — business_objectives + events ===")
        await seed_static_data(conn)

        # ── Seed 3 ─────────────────────────────────────────
        logger.info("\n=== SEED 3 — stock_levels (demand stats) ===")
        await init_stock_levels(conn)

        # ── Vérification ───────────────────────────────────
        await verify(conn)

    await pool.close()
    logger.info("\n✅ Tous les seeds terminés — relancez: uvicorn main:app --port 8000")


if __name__ == "__main__":
    asyncio.run(main())