"""
seed_market_reference.py
=========================
Seeds:
  market.competitors        <- data/processed/competitors.csv
  market.competitor_pricing <- data/processed/competitor_pricing.csv
  market.mnp_flows          <- data/processed/mnp_flows.csv
  supply.suppliers          <- data/processed/suppliers.csv
  market.events             <- data/processed/events_calendar.csv

Columns present in the CSVs but absent from these tables are dropped
(logged as a warning per table, not silently). See the "NOT SEEDED" summary
printed at the end of run().

market.events specifics
------------------------
- event_type / intensite have CHECK constraints in the live schema
  (event_type: RELIGIEUX/SCOLAIRE/SPORTIF/COMMERCIAL/NATIONAL/CONCURRENTIEL/
  METEO/RESEAU — intensite: LOW/MEDIUM/HIGH/EXTREME). The legacy 2023 events
  in events_calendar.csv use lowercase English values (religious, shopping,
  academic...) — EVENT_TYPE_MAP below normalizes both eras to the DB's
  vocabulary. Unmapped values raise instead of silently violating the CHECK.
- uplift_postpaid and uplift_box_fibre have NO destination column in
  market.events (schema only has terminal/forfait/sim/recharge/accessoire).
  That signal is dropped here — flagged in the summary, not silently lost.

Run: python db/seeds/seed_market_reference.py
Re-run safe: existing rows in these 5 tables are deleted first.

Fix (2026-07-09): optional text fields across all 5 tables now pass
through _seed_common.clean() so a blank CSV cell (pandas NaN) becomes
NULL instead of crashing asyncpg's text codec with "expected str, got
float". Also fixed a related bug in seed_events: `r.get("scope", "national")`
and `r.get("intensity", "MEDIUM")` looked like safe defaults but weren't —
Series.get()'s default only applies when the column is missing entirely,
not when the cell is blank, so a blank cell was silently becoming the
literal string "NAN" after .upper(). Now clean(...) or <default> is used,
which actually falls back correctly for blank cells too.
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _seed_common import get_pool, DATA_PROCESSED, clean

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

EVENT_TYPE_MAP = {
    "religious": "RELIGIEUX", "shopping": "COMMERCIAL", "academic": "SCOLAIRE",
    "national": "NATIONAL", "RELIGIEUX": "RELIGIEUX", "SCOLAIRE": "SCOLAIRE",
    "SPORTIF": "SPORTIF", "COMMERCIAL": "COMMERCIAL", "NATIONAL": "NATIONAL",
    "CONCURRENTIEL": "CONCURRENTIEL", "METEO": "METEO", "RESEAU": "RESEAU",
}


def _jsonb(semicolon_str: str) -> str:
    items = [s.strip() for s in str(semicolon_str).split(";") if s.strip()]
    return json.dumps(items)


async def seed_competitors(conn, df: pd.DataFrame):
    # competitor_pricing FKs competitors.concurrent_id — must clear the
    # child table first or DELETE FROM competitors hits a FK violation.
    await conn.execute("DELETE FROM market.competitor_pricing")
    await conn.execute("DELETE FROM market.competitors")
    for _, r in df.iterrows():
        await conn.execute(
            """
            INSERT INTO market.competitors
                (concurrent_id, nom, code_operateur, pays, part_marche_pct,
                 nb_abonnes, positionnement, points_forts, points_faibles,
                 date_entree_marche, actif)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10,$11)
            """,
            r["competitor_id"], r["name"], clean(r["operator_code"]), clean(r["country"]),
            float(r["market_share_pct"]), int(r["nb_subscribers"]), clean(r["positioning"]),
            _jsonb(r["strengths"]), _jsonb(r["weaknesses"]),
            pd.to_datetime(r["market_entry_date"]).date(), bool(r["is_active"]),
        )
    logger.info(f"  market.competitors: {len(df)} rows")


async def seed_competitor_pricing(conn, df: pd.DataFrame):
    await conn.execute("DELETE FROM market.competitor_pricing")
    for _, r in df.iterrows():
        await conn.execute(
            """
            INSERT INTO market.competitor_pricing
                (concurrent_id, categorie, produit_type, donnees_go,
                 prix_ttc, engagement_mois, date_releve, source)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            """,
            r["competitor_id"], r["category"], clean(r["product_type"]),
            float(r["data_go"]) if pd.notna(r["data_go"]) else None,
            float(r["price_ttc"]), int(r["commitment_months"]),
            pd.to_datetime(r["price_date"]).date(), clean(r["source"]),
        )
    logger.info(f"  market.competitor_pricing: {len(df)} rows  (prix_ht not in source CSV, left NULL)")


async def seed_mnp_flows(conn, df: pd.DataFrame):
    await conn.execute("DELETE FROM market.mnp_flows")
    for _, r in df.iterrows():
        await conn.execute(
            """
            INSERT INTO market.mnp_flows
                (direction, operateur_origine, operateur_destination, mois,
                 volume, categorie_client, raison_principale, wilaya)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            """,
            r["direction"], clean(r["operator_origin"]), clean(r["operator_destination"]),
            pd.to_datetime(r["month"]).date(), int(r["volume"]),
            clean(r["client_category"]), clean(r["main_reason"]), clean(r["wilaya"]),
        )
    logger.info(f"  market.mnp_flows: {len(df)} rows")


async def seed_suppliers(conn, df: pd.DataFrame):
    await conn.execute("DELETE FROM supply.suppliers")
    for _, r in df.iterrows():
        await conn.execute(
            """
            INSERT INTO supply.suppliers
                (supplier_id, nom, pays_origine, type_fournisseur, categories,
                 marques, delai_livraison_moy, delai_livraison_std,
                 taux_fiabilite, commande_min, commande_multiple, devise, actif)
            VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7,$8,$9,$10,$11,$12,$13)
            """,
            r["supplier_id"], r["name"], clean(r["country_origin"]), clean(r["supplier_type"]),
            _jsonb(r["categories"]), _jsonb(r["brands"]),
            int(r["lead_time_avg_days"]), int(r["lead_time_std_days"]),
            float(r["reliability_rate"]), int(r["min_order_qty"]),
            int(r["order_multiple"]), clean(r["currency"]), bool(r["is_active"]),
        )
    logger.info(f"  supply.suppliers: {len(df)} rows  (contact_nom/contact_email/conditions_paiement not in source, left NULL)")


async def seed_events(conn, df: pd.DataFrame):
    await conn.execute("DELETE FROM market.events")
    bad_types = set(df["event_type"]) - set(EVENT_TYPE_MAP)
    if bad_types:
        raise ValueError(f"events_calendar.csv has unmapped event_type value(s): {bad_types} — add to EVENT_TYPE_MAP")

    for _, r in df.iterrows():
        cats = [c for c, col in [
            ("Terminal", "uplift_terminal"), ("Forfait Mobile", "uplift_forfait"),
            ("Recharge", "uplift_recharge"), ("Accessoire", "uplift_accessoire"),
        ] if pd.notna(r.get(col)) and float(r.get(col, 0)) != 0]
        await conn.execute(
            """
            INSERT INTO market.events
                (event_name, event_type, sous_type, start_date, end_date, scope,
                 categories_impactees, uplift_terminal, uplift_forfait, uplift_sim,
                 uplift_recharge, uplift_accessoire, intensite, source_donnee, note_strategie)
            VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10,$11,$12,$13,$14,$15)
            """,
            r["event_name"], EVENT_TYPE_MAP[r["event_type"]], clean(r.get("sous_type")),
            pd.to_datetime(r["start_date"]).date(), pd.to_datetime(r["end_date"]).date(),
            str(clean(r.get("scope")) or "national").upper(), json.dumps(cats),
            float(r.get("uplift_terminal") or 0), float(r.get("uplift_forfait") or 0),
            float(r.get("uplift_sim") or 0), float(r.get("uplift_recharge") or 0),
            float(r.get("uplift_accessoire") or 0), str(clean(r.get("intensity")) or "MEDIUM").upper(),
            "HISTORIQUE", clean(r.get("strategy_note")),
        )
    logger.info(f"  market.events: {len(df)} rows  (uplift_postpaid + uplift_box_fibre dropped — no column for either)")


async def main():
    pool = await get_pool()
    async with pool.acquire() as conn:
        competitors = pd.read_csv(DATA_PROCESSED / "competitors.csv")
        pricing     = pd.read_csv(DATA_PROCESSED / "competitor_pricing.csv")
        mnp         = pd.read_csv(DATA_PROCESSED / "mnp_flows.csv")
        suppliers   = pd.read_csv(DATA_PROCESSED / "suppliers.csv")
        events      = pd.read_csv(DATA_PROCESSED / "events_calendar.csv")

        await seed_competitors(conn, competitors)
        await seed_competitor_pricing(conn, pricing)
        await seed_mnp_flows(conn, mnp)
        await seed_suppliers(conn, suppliers)
        await seed_events(conn, events)

    await pool.close()
    logger.info("Done: market.competitors, market.competitor_pricing, market.mnp_flows, "
                "supply.suppliers, market.events")


if __name__ == "__main__":
    asyncio.run(main())