"""
generate_multistore_data.py
════════════════════════════
Génère des données synthétiques réalistes pour enrichir la base Ooredoo Tunisia.

PROBLÈME : Les données réelles couvrent 1 boutique (I63/M10) avec 2-3 agents.
SOLUTION  : Générer des données pour 4 boutiques supplémentaires + 12 conseillers
            diversifiés + 90 jours de stock history pour chaque SKU.

Schéma réel ciblé (vérifié sur la DB) :
  - sales.boutiques    : store_id, store_name, address, ville, region, active,
                         capacite_conseillers, date_ouverture
  - sales.agents       : agent_id, agent_name, store_id, role, performance_level,
                         specialisation, coach_score, avatar_color
  - sales.transactions : transaction_date, date_only, heure, store_id, agent_id,
                         sku(INTEGER), quantity, prix_unitaire, lig_ht, lig_ttc, marge
  - inventory.stock_history : sku(INTEGER), store_id, record_date, stock_level,
                               quantity_sold, is_stockout, days_of_stock
  - agent_kpi_daily    : agent_id, store_id, kpi_date, ca_realise, ...

Usage :
  python scripts/generate_multistore_data.py [--days 90] [--dry-run]

Prérequis :
  pip install psycopg2-binary numpy python-dotenv
"""

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB = dict(
    host=os.getenv("POSTGRES_HOST", "localhost"),
    port=int(os.getenv("POSTGRES_PORT", 5432)),
    dbname=os.getenv("POSTGRES_DB", "ooredoo_sales"),
    user=os.getenv("POSTGRES_USER", "postgres"),
    password=os.getenv("POSTGRES_PASSWORD", "root"),
)

# ── Boutiques cibles (4 nouvelles — I63/M10 a déjà des données réelles) ───────
BOUTIQUES = [
    # (store_id, store_name, ville, region, n_advisors, volume_factor)
    ("M22", "Ooredoo Sfax Medina",        "Sfax",     "Centre",      3, 0.85),
    ("M15", "Ooredoo Sousse Khézama",     "Sousse",   "Sahel",       3, 0.92),
    ("M18", "Ooredoo Monastir Centre",    "Monastir", "Sahel",       2, 0.78),
    ("I14", "Ooredoo Ariana Ennasr",      "Ariana",   "Grand Tunis", 4, 1.05),
]

BOUTIQUE_META = {
    "M22": (36.8664, 10.3254, "Boulevard du 7 Novembre, Sfax",    "OFR", "2006-03-15"),
    "M15": (35.8256, 10.6369, "Avenue Hedi Chaker, Khézama",      "OFR", "2007-09-01"),
    "M18": (35.7643, 10.8113, "Avenue Habib Bourguiba, Monastir", "OFR", "2008-04-20"),
    "I14": (36.8944, 10.1789, "Centre Commercial Ennasr 2",       "OFI", "2012-11-05"),
}

DAILY_TARGETS = {"M22": 850.0, "M15": 920.0, "M18": 780.0, "I14": 1050.0}

# ── Conseillers avec profils de performance ────────────────────────────────────
# (advisor_id, name, store_id, skill, performance_factor, specialite)
ADVISORS = [
    (4001, "TRABELSI Anis",        "M22", "senior",    1.15, "terminal"),
    (4002, "HAMMAMI Sonia",        "M22", "confirmed",  0.92, "forfait"),
    (4003, "BEN ROMDHANE Yassine", "M22", "junior",     0.68, "recharge"),
    (4011, "GARGOURI Mohamed",     "M15", "senior",    1.20, "premium"),
    (4012, "KACHOURI Rim",         "M15", "confirmed",  0.88, "forfait"),
    (4013, "SOUILAH Bilel",        "M15", "junior",     0.72, "sim"),
    (4021, "MELLOULI Fatma",       "M18", "confirmed",  0.95, "terminal"),
    (4022, "HADHRI Tarek",         "M18", "junior",     0.65, "recharge"),
    (4031, "JEBALI Nadia",         "I14", "senior",    1.18, "forfait"),
    (4032, "CHAKROUN Walid",       "I14", "confirmed",  0.85, "terminal"),
    (4033, "BACCOUCHE Sara",       "I14", "confirmed",  0.90, "premium"),
    (4034, "MARZOUKI Rami",        "I14", "junior",     0.62, "sim"),
]

AVATAR_COLORS = ["#E53935", "#1E88E5", "#43A047", "#FB8C00", "#8E24AA",
                 "#00ACC1", "#F4511E", "#6D4C41", "#3949AB", "#039BE5",
                 "#558B2F", "#00838F"]

HOURLY_PATTERN = {
    8: 0.012, 9: 0.033, 10: 0.074, 11: 0.102, 12: 0.143,
    13: 0.122, 14: 0.094, 15: 0.091, 16: 0.082, 17: 0.088,
    18: 0.077, 19: 0.051, 20: 0.024,
}
DOW_FACTOR  = {0: 0.88, 1: 0.92, 2: 0.95, 3: 0.98, 4: 1.05, 5: 1.35, 6: 1.20}
SEASONAL    = {1: 0.88, 2: 0.90, 3: 1.40, 4: 1.70, 5: 1.10,
               6: 1.25, 7: 1.15, 8: 1.00, 9: 1.35, 10: 1.05, 11: 1.20, 12: 1.35}
EVENTS = [("2026-06-06", "2026-06-08", 1.45), ("2026-06-15", "2026-08-15", 1.20)]

# Demande journalière approx par catégorie produit
DEMAND_DAILY = {"50": 1.2, "88": 8.5, "80": 1.5, "20": 12.0, "30": 25.0, "70": 2.5}
STOCK_INIT   = {"50": (5,25), "88": (999,999), "80": (10,30), "20": (50,200), "30": (100,500), "70": (10,50)}
REORDER_PT   = {"50": 5, "88": 999, "80": 5, "20": 20, "30": 30, "70": 8}


def get_conn():
    os.environ["PGCLIENTENCODING"] = "UTF8"
    c = psycopg2.connect(**DB)
    c.set_client_encoding("UTF8")
    return c


def event_factor(d: date) -> float:
    ds = d.strftime("%Y-%m-%d")
    for s, e, u in EVENTS:
        if s <= ds <= e:
            return u
    return 1.0


def load_products(conn) -> dict[str, list]:
    """Charge les SKUs réels depuis inventory.products, groupés par catégorie."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT sku, product_name, category, unit_price
            FROM inventory.products
            WHERE active = TRUE AND unit_price > 0
            ORDER BY unit_price DESC
        """)
        rows = cur.fetchall()

    by_cat: dict[str, list] = {}
    for sku, name, cat, price in rows:
        by_cat.setdefault(cat, []).append((int(sku), str(name), float(price)))

    fallback = [(8811044, "Forfait", 26.5)]
    products_by_spec = {
        "terminal": by_cat.get("50", fallback),
        "forfait":  by_cat.get("88", fallback),
        "sim":      by_cat.get("20", fallback),
        "recharge": by_cat.get("30", fallback),
        "premium":  [p for p in by_cat.get("50", []) if p[2] > 500] or by_cat.get("50", fallback)[:5],
        "default":  by_cat.get("88", fallback)[:10],
    }
    total = sum(len(v) for v in by_cat.values())
    log.info(f"  {total} SKUs actifs chargés depuis inventory.products")
    return products_by_spec


def pick_product(spec: str, products: dict, rng) -> tuple:
    pool = products.get(spec, products["default"])
    if not pool:
        return (8811044, "Forfait", 26.5)
    prices = np.array([p[2] for p in pool], dtype=float)
    weights = 1.0 / (prices + 1.0)
    weights /= weights.sum()
    return pool[rng.choice(len(pool), p=weights)]


# ══════════════════════════════════════════════════════════════════════════════
# 1. BOUTIQUES
# ══════════════════════════════════════════════════════════════════════════════

def seed_boutiques(conn, dry_run: bool = False):
    log.info("→ Seed boutiques...")
    rows = []
    for store_id, store_name, ville, region, n_adv, _ in BOUTIQUES:
        lat, lon, adresse, _, date_ouv = BOUTIQUE_META.get(store_id, (0, 0, "", "OFR", "2010-01-01"))
        rows.append((store_id, store_name, adresse, ville, region, True, n_adv, date_ouv, lat, lon))

    if not dry_run:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO sales.boutiques
                    (store_id, store_name, address, ville, region,
                     active, capacite_conseillers, date_ouverture,
                     latitude, longitude)
                VALUES %s
                ON CONFLICT (store_id) DO UPDATE SET
                    store_name=EXCLUDED.store_name, ville=EXCLUDED.ville, active=TRUE,
                    latitude=EXCLUDED.latitude, longitude=EXCLUDED.longitude
            """, rows)
        conn.commit()
    log.info(f"  {len(rows)} boutiques insérées")


# ══════════════════════════════════════════════════════════════════════════════
# 2. CONSEILLERS
# ══════════════════════════════════════════════════════════════════════════════

def seed_advisors(conn, dry_run: bool = False):
    log.info("→ Seed conseillers...")
    rows = []
    for i, (adv_id, name, store_id, skill, perf, spec) in enumerate(ADVISORS):
        role = ("Conseiller Senior" if skill == "senior" else
                "Conseiller Confirmé" if skill == "confirmed" else "Conseiller")
        lo, hi = {"senior": (85,98), "confirmed": (65,84), "junior": (35,64)}[skill]
        score = round(max(lo, min(hi, lo + (hi-lo) * (perf-0.6) / 0.7)), 1)
        rows.append((adv_id, name, store_id, role, skill, spec, score, AVATAR_COLORS[i % len(AVATAR_COLORS)]))

    if not dry_run:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO sales.agents
                    (agent_id, agent_name, store_id, role,
                     performance_level, specialisation, coach_score, avatar_color)
                VALUES %s
                ON CONFLICT (agent_id) DO UPDATE SET
                    coach_score=EXCLUDED.coach_score,
                    performance_level=EXCLUDED.performance_level
            """, rows)
        conn.commit()
    log.info(f"  {len(rows)} conseillers insérés")


# ══════════════════════════════════════════════════════════════════════════════
# 3. TRANSACTIONS SYNTHÉTIQUES
# ══════════════════════════════════════════════════════════════════════════════

def generate_transactions(conn, products: dict, n_days: int = 90, dry_run: bool = False):
    log.info(f"→ Génération transactions ({n_days} jours × 4 boutiques)...")

    end_date   = date.today()
    start_date = end_date - timedelta(days=n_days)

    store_advs: dict[str, list] = {}
    for a in ADVISORS:
        store_advs.setdefault(a[2], []).append(a)

    all_txns = []
    rng = np.random.default_rng(42)

    for store_id, _, _, _, _, vol in BOUTIQUES:
        advs = store_advs.get(store_id, [])
        if not advs:
            continue
        target = DAILY_TARGETS[store_id]
        current = start_date

        while current <= end_date:
            dow = current.weekday()
            sf = SEASONAL.get(current.month, 1.0) * DOW_FACTOR[dow] * event_factor(current) * vol
            ca_day = max(target * 0.3, target * sf * rng.normal(1.0, 0.12))

            for hour, pct in HOURLY_PATTERN.items():
                ca_hour = ca_day * pct
                n_tx = max(1, int(ca_hour / 55 + rng.normal(0, 0.5)))
                for _ in range(n_tx):
                    w = np.array([a[4] for a in advs]); w /= w.sum()
                    adv = advs[rng.choice(len(advs), p=w)]
                    adv_id, _, _, _, _, spec = adv

                    sku, _, prix = pick_product(spec, products, rng)
                    prix_final = float(prix) * rng.uniform(0.95, 1.02)
                    lig_ht  = round(prix_final / 1.19, 4)
                    lig_ttc = round(prix_final, 2)
                    marge   = round(lig_ht * 0.20, 4)

                    tx_dt = datetime.combine(current, datetime.min.time()).replace(
                        hour=hour, minute=int(rng.uniform(0, 59)), second=int(rng.uniform(0, 59)))

                    all_txns.append((
                        tx_dt, tx_dt.date(), hour,
                        store_id, adv_id, sku,
                        1, lig_ttc, lig_ht, lig_ttc, marge,
                    ))
            current += timedelta(days=1)

    log.info(f"  {len(all_txns)} transactions générées")
    if dry_run or not all_txns:
        return

    with conn.cursor() as cur:
        for i in range(0, len(all_txns), 1000):
            execute_values(cur, """
                INSERT INTO sales.transactions
                    (transaction_date, date_only, heure, store_id, agent_id, sku,
                     quantity, prix_unitaire, lig_ht, lig_ttc, marge)
                VALUES %s
            """, all_txns[i:i+1000])
            if i % 20000 == 0 and i > 0:
                conn.commit()
                log.info(f"  {i} / {len(all_txns)} insérées")
    conn.commit()
    log.info("  Transactions insérées ✅")


# ══════════════════════════════════════════════════════════════════════════════
# 4. STOCK HISTORY
# ══════════════════════════════════════════════════════════════════════════════

def generate_stock_history(conn, n_days: int = 90, dry_run: bool = False):
    log.info(f"→ Stock history ({n_days} jours)...")

    with conn.cursor() as cur:
        cur.execute("SELECT sku, category FROM inventory.products WHERE active=TRUE LIMIT 500")
        products_list = cur.fetchall()

    if not products_list:
        log.warning("  Aucun produit actif — skip")
        return

    end_date = date.today(); start_date = end_date - timedelta(days=n_days)
    rng = np.random.default_rng(123)

    stock_state: dict = {}
    for sku, cat in products_list:
        for store_id, *_ in BOUTIQUES:
            lo, hi = STOCK_INIT.get(cat, (10, 50))
            stock_state[(sku, store_id)] = 9999 if cat == "88" else int(rng.uniform(lo, hi))

    rows = []
    current = start_date
    while current <= end_date:
        sf = SEASONAL.get(current.month, 1.0) * DOW_FACTOR[current.weekday()] * event_factor(current)
        for store_id, _, _, _, _, vol in BOUTIQUES:
            for sku, cat in products_list:
                key = (sku, store_id)
                stock = stock_state.get(key, 0)
                demand = DEMAND_DAILY.get(cat, 2.0) * sf * vol * rng.normal(1.0, 0.20)
                demand = max(0, demand)

                if cat == "88":
                    stock_after = 9999; sold = round(demand); stockout = False
                else:
                    sold = min(stock, round(demand)); sold = max(0, sold)
                    stock_after = max(0, stock - sold); stockout = stock_after == 0
                    if stock_after <= REORDER_PT.get(cat, 5) and rng.random() > 0.6:
                        lo, hi = STOCK_INIT.get(cat, (10, 50))
                        stock_after += int(rng.uniform(max(1, lo//2), hi))

                days_stock = min(round(stock_after / max(demand, 0.1), 1), 90.0)
                rows.append((int(sku), store_id, current, stock_after, stockout))
                stock_state[key] = stock_after
        current += timedelta(days=1)

    log.info(f"  {len(rows)} lignes stock history")
    if dry_run or not rows:
        return

    with conn.cursor() as cur:
        # La table inventory.stock_history est creee par les migrations Alembic
        # (db/migrations, baseline 0001) - ce script ne fait que du DML.
        for i in range(0, len(rows), 2000):
            execute_values(cur, """
                INSERT INTO inventory.stock_history
                    (sku, store_id, record_date, stock_level, is_stockout)
                VALUES %s
            """, rows[i:i+2000])
            if i % 40000 == 0 and i > 0:
                conn.commit(); log.info(f"  {i} / {len(rows)} stock history insérés")
    conn.commit()
    log.info("  Stock history inséré ✅")


# ══════════════════════════════════════════════════════════════════════════════
# 5. STOCK LEVELS COURANTS
# ══════════════════════════════════════════════════════════════════════════════

def seed_stock_levels(conn, dry_run: bool = False):
    log.info("→ Stock levels courants...")

    with conn.cursor() as cur:
        cur.execute("SELECT sku, category, unit_price FROM inventory.products WHERE active=TRUE LIMIT 500")
        prods = cur.fetchall()

    rng = np.random.default_rng(777)
    rows = []
    for store_id, *_ in BOUTIQUES:
        for sku, cat, price in prods:
            lo, hi = STOCK_INIT.get(cat, (10, 50))
            stock = 9999 if cat == "88" else int(rng.uniform(lo * 0.5, hi))
            demand = DEMAND_DAILY.get(cat, 2.0)
            days = min(round(stock / max(demand, 0.1), 1), 60.0)
            rows.append((int(sku), store_id, stock, 0, days))

    if not dry_run and rows:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO inventory.stock_levels
                    (sku, store_id, quantity, quantity_reserved,
                     remaining_days_of_stock)
                VALUES %s
                ON CONFLICT (sku, store_id) DO UPDATE SET
                    quantity=EXCLUDED.quantity,
                    quantity_reserved=EXCLUDED.quantity_reserved,
                    remaining_days_of_stock=EXCLUDED.remaining_days_of_stock,
                    last_updated=NOW()
            """, rows)
        conn.commit()
    log.info(f"  {len(rows)} niveaux de stock initialisés ✅")


# ══════════════════════════════════════════════════════════════════════════════
# 6. KPI DAILY
# ══════════════════════════════════════════════════════════════════════════════

def compute_kpi_daily(conn, dry_run: bool = False):
    log.info("→ Calcul KPI daily...")
    if dry_run:
        log.info("  DRY RUN — skip"); return

    store_ids = [b[0] for b in BOUTIQUES]
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO agent_kpi_daily
                (agent_id, store_id, kpi_date, ca_realise, nb_transactions,
                 nb_clients_uniques, panier_moyen, ca_terminaux, ca_forfaits, ca_recharges)
            SELECT
                t.agent_id, t.store_id, t.date_only,
                COALESCE(SUM(t.lig_ttc), 0),
                COUNT(*),
                COUNT(DISTINCT t.heure),
                ROUND(AVG(t.lig_ttc)::NUMERIC, 2),
                COALESCE(SUM(t.lig_ttc) FILTER (WHERE p.category='50'), 0),
                COALESCE(SUM(t.lig_ttc) FILTER (WHERE p.category IN ('88','80')), 0),
                COALESCE(SUM(t.lig_ttc) FILTER (WHERE p.category='30'), 0)
            FROM sales.transactions t
            LEFT JOIN inventory.products p ON p.sku = t.sku
            WHERE t.store_id = ANY(%s)
              AND t.date_only >= CURRENT_DATE - INTERVAL '90 days'
              AND t.lig_ttc > 0
            GROUP BY t.agent_id, t.store_id, t.date_only
            ON CONFLICT (agent_id, kpi_date) DO UPDATE SET
                ca_realise=EXCLUDED.ca_realise, nb_transactions=EXCLUDED.nb_transactions
        """, (store_ids,))

        for store_id, *_ in BOUTIQUES:
            target = DAILY_TARGETS.get(store_id, 900.0)
            cur.execute("""
                UPDATE agent_kpi_daily SET
                    ca_cible = %s / NULLIF(
                        (SELECT COUNT(DISTINCT agent_id) FROM agent_kpi_daily
                         WHERE store_id=%s AND kpi_date=agent_kpi_daily.kpi_date), 0),
                    urgency_level = CASE
                        WHEN gap_ca_pct < -40 THEN 'CRITIQUE'
                        WHEN gap_ca_pct < -20 THEN 'ELEVE'
                        WHEN gap_ca_pct < -10 THEN 'MODERE'
                        ELSE 'OK'
                    END
                WHERE store_id=%s AND ca_cible IS NULL
            """, (target, store_id, store_id))
    conn.commit()
    log.info("  KPI daily calculés ✅")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days",              type=int, default=90)
    parser.add_argument("--dry-run",           action="store_true")
    parser.add_argument("--skip-transactions", action="store_true")
    parser.add_argument("--skip-stock",        action="store_true")
    args = parser.parse_args()

    log.info("═" * 60)
    log.info("GÉNÉRATION DONNÉES MULTI-BOUTIQUES OOREDOO")
    log.info(f"  Boutiques : {[b[0] for b in BOUTIQUES]}")
    log.info(f"  Agents    : {len(ADVISORS)}")
    log.info(f"  Période   : {args.days} jours")
    log.info(f"  Mode      : {'DRY RUN' if args.dry_run else 'ÉCRITURE DB'}")
    log.info("═" * 60)

    conn = get_conn()
    try:
        products = load_products(conn)
        seed_boutiques(conn, args.dry_run)
        seed_advisors(conn, args.dry_run)
        if not args.skip_transactions:
            generate_transactions(conn, products, args.days, args.dry_run)
        seed_stock_levels(conn, args.dry_run)
        if not args.skip_stock:
            generate_stock_history(conn, args.days, args.dry_run)
        compute_kpi_daily(conn, args.dry_run)

        log.info("═" * 60)
        log.info("✅ TERMINÉ — 4 boutiques, 12 agents, transactions + stock insérés")
    except Exception as e:
        log.error(f"ERREUR : {e}", exc_info=True)
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()