"""
seed_3years_history.py
=======================
Génère 3 ans d'historique réaliste (Jan 2022 → Sep 2024) dans inventory.sales_history.
Utilise les patterns observés dans les données existantes (Oct 2024 → Jul 2026)
comme baseline, puis applique :
  - market.seasonal_patterns (facteurs mensuels × journaliers)
  - Événements historiques (Ramadan, Eid, Soldes, Rentrée) reconstruits par règle
  - Bruit gaussien (±18%) pour réalisme
  - Tendance croissance +2% mensuelle (marché en croissance)

Produit ~1.5-2M lignes supplémentaires pour les top SKU/store actifs.
"""

import os, sys, logging, random, math
from datetime import date, timedelta
from typing import Dict, List, Tuple

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB = {
    "host":     os.getenv("POSTGRES_HOST", "localhost"),
    "port":     int(os.getenv("POSTGRES_PORT", "5432")),
    "dbname":   os.getenv("POSTGRES_DB", "ooredoo_sales"),
    "user":     os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", "admin"),
}

# Période à générer
START_DATE = date(2022, 1, 1)
END_DATE   = date(2024, 9, 30)

# Facteurs saisonniers telco Tunisie (mois → facteur)
SEASONAL_MONTH = {
    1: 0.82,   # Hiver calme
    2: 0.85,
    3: 1.45,   # Ramadan (variable selon l'année)
    4: 1.75,   # Eid Al-Fitr (variable)
    5: 1.10,
    6: 1.25,   # Eid Al-Adha (variable)
    7: 1.30,   # Été
    8: 1.15,
    9: 1.35,   # Rentrée scolaire
    10: 0.90,
    11: 1.25,  # Black Friday / début soldes
    12: 1.50,  # Soldes hiver / fêtes
}

# Facteurs par jour de semaine (0=Lundi, 6=Dimanche)
SEASONAL_DOW = {0: 0.90, 1: 0.92, 2: 0.95, 3: 0.98, 4: 1.10, 5: 1.35, 6: 1.20}

# Uplift événements historiques connus (format: (debut_mois, debut_jour, duree, type, intensite, uplift_global))
HISTORICAL_EVENTS = [
    # Ramadan 2022
    (2022, 4, 2,  30, "RAMADAN",   "HIGH",    1.35),
    (2022, 5, 2,   3, "EID_FITR",  "EXTREME", 2.10),
    (2022, 7, 9,   3, "EID_ADHA",  "HIGH",    1.60),
    # Rentrée 2022
    (2022, 9, 1,  20, "RENTREE",   "HIGH",    1.45),
    # Soldes hiver 2022/23
    (2022, 11,25,  45, "SOLDES",   "HIGH",    1.55),
    # Ramadan 2023
    (2023, 3, 23,  29, "RAMADAN",  "HIGH",    1.40),
    (2023, 4, 21,   3, "EID_FITR", "EXTREME", 2.20),
    (2023, 6, 28,   3, "EID_ADHA", "HIGH",    1.65),
    # Rentrée 2023
    (2023, 9, 1,  20, "RENTREE",   "HIGH",    1.45),
    # CAN 2023
    (2023, 6, 24,  30, "CAN",      "MEDIUM",  1.15),
    # Soldes hiver 2023/24
    (2023, 11,24,  45, "SOLDES",   "HIGH",    1.60),
    # Ramadan 2024
    (2024, 3, 11,  30, "RAMADAN",  "HIGH",    1.45),
    (2024, 4, 10,   3, "EID_FITR", "EXTREME", 2.30),
    (2024, 6, 16,   3, "EID_ADHA", "HIGH",    1.55),
    # Rentrée 2024
    (2024, 9, 1,  20, "RENTREE",   "HIGH",    1.50),
]


def _build_event_map() -> Dict[date, Tuple[str, str, float]]:
    """Construit un dict date → (event_name, event_type, uplift_factor)."""
    event_map: Dict[date, Tuple[str, str, float]] = {}
    for (yr, mo, day, duration, etype, intensite, uplift) in HISTORICAL_EVENTS:
        try:
            start = date(yr, mo, day)
        except ValueError:
            continue
        for i in range(duration):
            d = start + timedelta(days=i)
            # Intensite decroissante sauf EID (pic sur 2-3 jours = flat)
            if intensite == "EXTREME":
                factor = uplift
            else:
                factor = uplift * (1.0 - 0.01 * i) if i < 7 else uplift * 0.85
            # Garde l'événement le plus fort si superposition
            if d not in event_map or event_map[d][2] < factor:
                names = {
                    "RAMADAN": "Ramadan",
                    "EID_FITR": "Eid Al-Fitr",
                    "EID_ADHA": "Eid Al-Adha",
                    "RENTREE": "Rentrée Scolaire",
                    "SOLDES": "Soldes Hiver",
                    "CAN": "CAN",
                }
                event_map[d] = (names.get(etype, etype), "RELIGIEUX" if "EID" in etype or etype == "RAMADAN" else "COMMERCIAL", factor)
    return event_map


def _get_season(d: date) -> str:
    m = d.month
    if m in (2, 3):
        return "RAMADAN"
    if m == 4:
        return "EID_PRINTEMPS"
    if m in (5,):
        return "PRINTEMPS"
    if m == 6:
        return "EID_ETE"
    if m in (7, 8):
        return "ETE"
    if m == 9:
        return "RENTREE"
    if m == 10:
        return "AUTOMNE"
    if m == 11:
        return "PRE_SOLDES"
    if m == 12:
        return "SOLDES_HIVER"
    return "HIVER"


def _get_top_sku_store_baselines(conn) -> List[dict]:
    """
    Récupère les top (store_id, sku, category) avec leur demande journalière
    baseline calculée depuis les données existantes (Oct 2024 → Jul 2026).
    Limite aux top 80 SKU les plus vendus × top 30 boutiques = max 2400 paires.
    """
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Top 80 SKU par volume de ventes
    cur.execute("""
        SELECT sku, category,
               AVG(quantity_sold) AS baseline_qty,
               AVG(revenue)       AS baseline_rev,
               AVG(unit_price)    AS avg_price,
               STDDEV(quantity_sold) AS std_qty
        FROM inventory.sales_history
        WHERE quantity_sold > 0
          AND record_date >= '2024-10-01'
        GROUP BY sku, category
        HAVING COUNT(*) >= 20
        ORDER BY SUM(quantity_sold) DESC
        LIMIT 80
    """)
    top_skus = {r["sku"]: dict(r) for r in cur.fetchall()}

    # Top 30 boutiques par CA
    cur.execute("""
        SELECT store_id
        FROM inventory.sales_history
        WHERE record_date >= '2024-10-01'
        GROUP BY store_id
        ORDER BY SUM(revenue) DESC
        LIMIT 30
    """)
    top_stores = [r["store_id"] for r in cur.fetchall()]

    # Baseline par (store, sku) — demande journalière moyenne observée
    cur.execute("""
        SELECT store_id, sku, category,
               AVG(quantity_sold) AS baseline_qty,
               AVG(revenue)       AS baseline_rev,
               AVG(unit_price)    AS avg_price,
               STDDEV(quantity_sold) AS std_qty
        FROM inventory.sales_history
        WHERE quantity_sold > 0
          AND record_date >= '2024-10-01'
          AND sku IN %(skus)s
          AND store_id IN %(stores)s
        GROUP BY store_id, sku, category
        HAVING COUNT(*) >= 10
        ORDER BY SUM(revenue) DESC
        LIMIT 2000
    """, {"skus": tuple(top_skus.keys()), "stores": tuple(top_stores)})
    baselines = [dict(r) for r in cur.fetchall()]

    cur.close()
    logger.info(f"[SEED] Baselines chargées : {len(baselines)} (store, sku) paires")
    return baselines


def _get_product_names(conn) -> Dict[int, str]:
    cur = conn.cursor()
    cur.execute("SELECT sku, nom FROM sales.produits")
    result = {r[0]: r[1] for r in cur.fetchall()}
    cur.close()
    return result


def seed_3years_history(dry_run: bool = False):
    conn = psycopg2.connect(**DB, connect_timeout=15)
    conn.autocommit = False

    try:
        event_map = _build_event_map()
        baselines = _get_top_sku_store_baselines(conn)
        product_names = _get_product_names(conn)

        logger.info(f"[SEED] Génération {START_DATE} → {END_DATE} | {len(baselines)} paires | {(END_DATE - START_DATE).days} jours")

        # Batch insert
        cur = conn.cursor()
        batch: List[tuple] = []
        BATCH_SIZE = 10_000
        total = 0

        # Calculer combien de mois séparent la période de backfill de la période de référence
        # On applique une décroissance vers le passé (déjà moins de ventes avant)
        ref_date = date(2024, 10, 1)

        all_dates = [START_DATE + timedelta(days=i) for i in range((END_DATE - START_DATE).days + 1)]

        for (store_sku_idx, baseline) in enumerate(baselines):
            store_id   = baseline["store_id"]
            sku        = baseline["sku"]
            category   = baseline["category"] or "AUTRE"
            base_qty   = float(baseline["baseline_qty"] or 1.0)
            base_rev   = float(baseline["baseline_rev"] or 0)
            avg_price  = float(baseline["avg_price"] or 0)
            std_qty    = float(baseline["std_qty"] or base_qty * 0.3)
            product_name = product_names.get(sku, str(sku))

            if base_qty < 0.05:
                continue

            for d in all_dates:
                # Facteur temporel : croissance +2%/mois vers le futur
                months_back = (ref_date.year - d.year) * 12 + (ref_date.month - d.month)
                growth_factor = max(0.55, 1.0 - months_back * 0.018)

                # Facteur saison mois × jour semaine
                month_f = SEASONAL_MONTH.get(d.month, 1.0)
                dow_f   = SEASONAL_DOW.get(d.weekday(), 1.0)

                # Facteur event
                event_name, event_type, event_uplift = None, None, 1.0
                if d in event_map:
                    event_name, event_type, event_uplift = event_map[d]

                # Uplift par catégorie pour les events
                cat_uplift = event_uplift
                if event_type in ("RELIGIEUX",) and category == "RECHARGE":
                    cat_uplift = event_uplift * 1.4
                elif event_type in ("RELIGIEUX",) and category == "TERMINAL":
                    cat_uplift = event_uplift * 1.1
                elif d.month == 9 and category in ("TERMINAL",):
                    cat_uplift = 1.3  # rentrée scolaire

                total_factor = growth_factor * month_f * dow_f * cat_uplift

                # Demande avec bruit gaussien
                mu = base_qty * total_factor
                sigma = max(0.1, std_qty * total_factor * 0.5)
                qty = max(0, round(random.gauss(mu, sigma)))

                if qty == 0 and random.random() > 0.7:
                    continue  # pas de ligne si 0 vendu (réaliste)
                if qty == 0:
                    continue

                revenue = round(qty * avg_price * (1 + random.gauss(0, 0.03)), 2)
                unit_p  = round(revenue / qty, 4) if qty > 0 else avg_price

                season       = _get_season(d)
                is_event     = event_name is not None
                is_weekend   = d.weekday() in (4, 5)  # vendredi/samedi en Tunisie

                uplift_factor = min(total_factor, 3.0)

                batch.append((
                    store_id, sku, product_name, category, d,
                    qty, revenue, unit_p,
                    False, None,                # is_promo, promo_type
                    event_name, event_type, season,
                    is_event,
                    "HIGH" if event_uplift > 1.4 else ("MEDIUM" if event_uplift > 1.1 else None),
                    uplift_factor,
                    is_weekend,
                    d.weekday(), d.isocalendar()[1], d.month, d.year
                ))

                if len(batch) >= BATCH_SIZE:
                    if not dry_run:
                        psycopg2.extras.execute_values(cur, """
                            INSERT INTO inventory.sales_history
                                (store_id, sku, product_name, category, record_date,
                                 quantity_sold, revenue, unit_price,
                                 is_promo, promo_type, event_name, event_type, season,
                                 is_event_day, event_intensite, uplift_factor,
                                 is_weekend, day_of_week, week_of_year, month_num, year_num)
                            VALUES %s
                            ON CONFLICT DO NOTHING
                        """, batch)
                    total += len(batch)
                    batch.clear()
                    if total % 100_000 == 0:
                        conn.commit()
                        logger.info(f"[SEED] {total:,} lignes insérées...")

        # Dernier batch
        if batch and not dry_run:
            psycopg2.extras.execute_values(cur, """
                INSERT INTO inventory.sales_history
                    (store_id, sku, product_name, category, record_date,
                     quantity_sold, revenue, unit_price,
                     is_promo, promo_type, event_name, event_type, season,
                     is_event_day, event_intensite, uplift_factor,
                     is_weekend, day_of_week, week_of_year, month_num, year_num)
                VALUES %s
                ON CONFLICT DO NOTHING
            """, batch)
        total += len(batch)
        conn.commit()

        cur2 = conn.cursor()
        cur2.execute("SELECT COUNT(*), MIN(record_date), MAX(record_date), COUNT(DISTINCT record_date) FROM inventory.sales_history")
        row = cur2.fetchone()
        cur2.close()

        logger.info(f"[SEED] DONE — Total: {total:,} lignes générées")
        logger.info(f"[DB]   Total sales_history: {row[0]:,} | {row[1]} → {row[2]} | {row[3]} jours uniques")

    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Ne pas insérer en base")
    args = parser.parse_args()
    seed_3years_history(dry_run=args.dry_run)
