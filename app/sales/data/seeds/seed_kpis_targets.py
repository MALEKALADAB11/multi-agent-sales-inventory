"""
Seed KPIs & Targets — computes agent_kpi_daily, store_kpi_daily,
telco_targets_monthly and weekly_kpi_summary from actual transactions.

Run: python -m sales-module.data.seeds.seed_kpis_targets
     or pass --from YYYY-MM-DD --to YYYY-MM-DD
"""

import argparse
import logging
import uuid
from datetime import date, timedelta

import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
log = logging.getLogger(__name__)

DB_PARAMS = {
    "host": "localhost",
    "port": 5432,
    "dbname": "ooredoo_sales",
    "user": "postgres",
    "password": "root",
}


def get_conn():
    return psycopg2.connect(**DB_PARAMS)


# ── 1. Compute agent_kpi_daily ────────────────────────────────────────────────

AGENT_KPI_SQL = """
INSERT INTO agent_kpi_daily
    (agent_id, store_id, kpi_date,
     ca_realise, ca_cible, gap_ca_pct,
     nb_transactions, nb_clients_uniques, panier_moyen,
     nb_forfaits, nb_terminaux, nb_sim_activations, nb_recharges,
     nb_accessoires, nb_evouchers,
     nb_postpaye,
     ca_terminaux, ca_forfaits, ca_sim, ca_recharges, ca_accessoires)
SELECT
    t.agent_id,
    t.store_id,
    t.date_only                                        AS kpi_date,
    COALESCE(SUM(t.lig_ttc), 0)                       AS ca_realise,
    MAX(COALESCE(o.objectif_ca / 26.0, 0))            AS ca_cible,
    CASE
        WHEN MAX(o.objectif_ca) > 0
        THEN ROUND(((SUM(t.lig_ttc) - MAX(o.objectif_ca)/26.0)
                     / (MAX(o.objectif_ca)/26.0)) * 100, 2)
        ELSE NULL
    END                                                AS gap_ca_pct,
    COUNT(DISTINCT t.id)                               AS nb_transactions,
    COUNT(DISTINCT t.heure)                            AS nb_clients_uniques,
    CASE WHEN COUNT(*) > 0
         THEN ROUND(SUM(t.lig_ttc) / NULLIF(COUNT(*),0), 2)
         ELSE 0
    END                                                AS panier_moyen,
    COUNT(*) FILTER (WHERE p.flag_forfait = TRUE)      AS nb_forfaits,
    COUNT(*) FILTER (WHERE p.flag_terminal = TRUE)     AS nb_terminaux,
    COUNT(*) FILTER (WHERE p.flag_sim = TRUE)          AS nb_sim_activations,
    COUNT(*) FILTER (WHERE p.flag_recharge = TRUE)     AS nb_recharges,
    COUNT(*) FILTER (WHERE p.categorie = '70')         AS nb_accessoires,
    COUNT(*) FILTER (WHERE p.categorie IN ('90','99')
                     AND p.nom ILIKE '%VOUCHER%')       AS nb_evouchers,
    COUNT(*) FILTER (WHERE p.flag_forfait = TRUE
                     AND p.nom ILIKE '%POSTPAYE%')      AS nb_postpaye,
    COALESCE(SUM(t.lig_ttc)
        FILTER (WHERE p.flag_terminal=TRUE), 0)         AS ca_terminaux,
    COALESCE(SUM(t.lig_ttc)
        FILTER (WHERE p.flag_forfait=TRUE), 0)          AS ca_forfaits,
    COALESCE(SUM(t.lig_ttc)
        FILTER (WHERE p.flag_sim=TRUE), 0)              AS ca_sim,
    COALESCE(SUM(t.lig_ttc)
        FILTER (WHERE p.flag_recharge=TRUE), 0)         AS ca_recharges,
    COALESCE(SUM(t.lig_ttc)
        FILTER (WHERE p.categorie='70'), 0)             AS ca_accessoires
FROM sales.transactions t
JOIN sales.produits p ON p.sku = t.sku
LEFT JOIN sales.objectifs o
    ON o.store_id = t.store_id
   AND o.agent_id = t.agent_id
   AND o.date_objectif = t.date_only
WHERE t.date_only BETWEEN %(date_from)s AND %(date_to)s
  AND t.quantity > 0
GROUP BY t.agent_id, t.store_id, t.date_only
ON CONFLICT (agent_id, kpi_date) DO UPDATE SET
    ca_realise          = EXCLUDED.ca_realise,
    ca_cible            = EXCLUDED.ca_cible,
    gap_ca_pct          = EXCLUDED.gap_ca_pct,
    nb_transactions     = EXCLUDED.nb_transactions,
    nb_clients_uniques  = EXCLUDED.nb_clients_uniques,
    panier_moyen        = EXCLUDED.panier_moyen,
    nb_forfaits         = EXCLUDED.nb_forfaits,
    nb_terminaux        = EXCLUDED.nb_terminaux,
    nb_sim_activations  = EXCLUDED.nb_sim_activations,
    nb_recharges        = EXCLUDED.nb_recharges,
    nb_accessoires      = EXCLUDED.nb_accessoires,
    nb_evouchers        = EXCLUDED.nb_evouchers,
    nb_postpaye         = EXCLUDED.nb_postpaye,
    ca_terminaux        = EXCLUDED.ca_terminaux,
    ca_forfaits         = EXCLUDED.ca_forfaits,
    ca_sim              = EXCLUDED.ca_sim,
    ca_recharges        = EXCLUDED.ca_recharges,
    ca_accessoires      = EXCLUDED.ca_accessoires
"""


# ── 2. Compute store_kpi_daily ────────────────────────────────────────────────

STORE_KPI_SQL = """
INSERT INTO store_kpi_daily
    (store_id, kpi_date,
     ca_realise, ca_cible, gap_ca_pct,
     nb_transactions, nb_clients, panier_moyen_store,
     nb_forfaits, nb_terminaux, nb_sim_activations, nb_recharges,
     nb_postpaye,
     ca_terminaux, ca_forfaits,
     nb_agents_actifs, ca_par_agent)
SELECT
    t.store_id,
    t.date_only                                        AS kpi_date,
    COALESCE(SUM(t.lig_ttc), 0)                       AS ca_realise,
    MAX(COALESCE(o.ca_cible_jour, 0))                  AS ca_cible,
    CASE
        WHEN MAX(o.ca_cible_jour) > 0
        THEN ROUND(((SUM(t.lig_ttc) - MAX(o.ca_cible_jour))
                     / MAX(o.ca_cible_jour)) * 100, 2)
        ELSE NULL
    END                                                AS gap_ca_pct,
    COUNT(DISTINCT t.id)                               AS nb_transactions,
    COUNT(DISTINCT t.heure)                            AS nb_clients,
    ROUND(SUM(t.lig_ttc) / NULLIF(COUNT(DISTINCT t.id),0), 2) AS panier_moyen,
    COUNT(*) FILTER (WHERE p.flag_forfait=TRUE)        AS nb_forfaits,
    COUNT(*) FILTER (WHERE p.flag_terminal=TRUE)       AS nb_terminaux,
    COUNT(*) FILTER (WHERE p.flag_sim=TRUE)            AS nb_sim,
    COUNT(*) FILTER (WHERE p.flag_recharge=TRUE)       AS nb_recharges,
    COUNT(*) FILTER (WHERE p.flag_forfait=TRUE
                     AND p.nom ILIKE '%POSTPAYE%')     AS nb_postpaye,
    COALESCE(SUM(t.lig_ttc)
        FILTER (WHERE p.flag_terminal=TRUE), 0)        AS ca_terminaux,
    COALESCE(SUM(t.lig_ttc)
        FILTER (WHERE p.flag_forfait=TRUE), 0)         AS ca_forfaits,
    COUNT(DISTINCT t.agent_id)                         AS nb_agents_actifs,
    ROUND(SUM(t.lig_ttc) /
          NULLIF(COUNT(DISTINCT t.agent_id),0), 2)     AS ca_par_agent
FROM sales.transactions t
JOIN sales.produits p ON p.sku = t.sku
LEFT JOIN (
    SELECT store_id, date_objectif,
           SUM(objectif_ca) AS ca_cible_jour
    FROM sales.objectifs
    WHERE agent_id IS NULL
    GROUP BY store_id, date_objectif
) o ON o.store_id = t.store_id AND o.date_objectif = t.date_only
WHERE t.date_only BETWEEN %(date_from)s AND %(date_to)s
  AND t.quantity > 0
GROUP BY t.store_id, t.date_only
ON CONFLICT (store_id, kpi_date) DO UPDATE SET
    ca_realise         = EXCLUDED.ca_realise,
    ca_cible           = EXCLUDED.ca_cible,
    gap_ca_pct         = EXCLUDED.gap_ca_pct,
    nb_transactions    = EXCLUDED.nb_transactions,
    nb_clients         = EXCLUDED.nb_clients,
    nb_forfaits        = EXCLUDED.nb_forfaits,
    nb_terminaux       = EXCLUDED.nb_terminaux,
    nb_sim_activations = EXCLUDED.nb_sim_activations,
    nb_recharges       = EXCLUDED.nb_recharges,
    nb_postpaye        = EXCLUDED.nb_postpaye,
    ca_terminaux       = EXCLUDED.ca_terminaux,
    ca_forfaits        = EXCLUDED.ca_forfaits,
    nb_agents_actifs   = EXCLUDED.nb_agents_actifs,
    ca_par_agent       = EXCLUDED.ca_par_agent
"""


# ── 3. Compute store_kpi_daily cumulative month ───────────────────────────────

UPDATE_CUMUL_SQL = """
UPDATE store_kpi_daily s
SET
    ca_cumul_mois = month_agg.ca_cumul,
    ca_objectif_mois = CASE
        WHEN tt.ca_mensuel IS NOT NULL THEN tt.ca_mensuel
        ELSE NULL
    END
FROM (
    SELECT
        store_id,
        kpi_date,
        SUM(ca_realise) OVER (
            PARTITION BY store_id,
                         DATE_TRUNC('month', kpi_date)
            ORDER BY kpi_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS ca_cumul
    FROM store_kpi_daily
    WHERE kpi_date BETWEEN %(date_from)s AND %(date_to)s
) month_agg
LEFT JOIN (
    SELECT store_id,
           DATE_TRUNC('month', date_objectif::timestamp)::date AS mois_date,
           SUM(ca_cible_mensuel) AS ca_mensuel
    FROM telco_targets_monthly
    GROUP BY store_id, DATE_TRUNC('month', date_objectif::timestamp)::date
) tt ON tt.store_id=s.store_id
    AND DATE_TRUNC('month', s.kpi_date::timestamp)::date=tt.mois_date
WHERE s.store_id = month_agg.store_id
  AND s.kpi_date = month_agg.kpi_date
  AND s.kpi_date BETWEEN %(date_from)s AND %(date_to)s
"""


# ── 4. Rank agents in boutique ────────────────────────────────────────────────

RANK_SQL = """
UPDATE agent_kpi_daily a
SET rang_boutique = ranks.rang
FROM (
    SELECT agent_id, kpi_date,
           RANK() OVER (
               PARTITION BY store_id, kpi_date
               ORDER BY ca_realise DESC NULLS LAST
           ) AS rang
    FROM agent_kpi_daily
    WHERE kpi_date BETWEEN %(date_from)s AND %(date_to)s
) ranks
WHERE a.agent_id = ranks.agent_id
  AND a.kpi_date = ranks.kpi_date
  AND a.kpi_date BETWEEN %(date_from)s AND %(date_to)s
"""

RANK_STORE_SQL = """
UPDATE store_kpi_daily s
SET rang_region = region_ranks.rang
FROM (
    SELECT store_id, kpi_date,
           RANK() OVER (
               PARTITION BY b.region, kpi_date
               ORDER BY ca_realise DESC NULLS LAST
           ) AS rang
    FROM store_kpi_daily sd
    JOIN sales.boutiques b ON b.store_id = sd.store_id
    WHERE kpi_date BETWEEN %(date_from)s AND %(date_to)s
) region_ranks
WHERE s.store_id = region_ranks.store_id
  AND s.kpi_date = region_ranks.kpi_date
  AND s.kpi_date BETWEEN %(date_from)s AND %(date_to)s
"""


# ── 5. Generate monthly telco targets ─────────────────────────────────────────

def generate_telco_targets(conn, start_date: date, end_date: date) -> int:
    """
    Generate monthly telco targets for each store and agent.
    Targets are based on:
    - Historical average CA (last 3 months) × seasonal factor
    - Activation targets = (total activations last 3M / 3) × 1.05
    """
    with conn.cursor() as cur:
        # Get months in range
        months = []
        d = start_date.replace(day=1)
        while d <= end_date:
            months.append(d)
            if d.month == 12:
                d = d.replace(year=d.year + 1, month=1)
            else:
                d = d.replace(month=d.month + 1)

        inserted = 0
        for m in months:
            mois = m.month
            annee = m.year

            # Store-level targets from historical data
            cur.execute("""
                INSERT INTO telco_targets_monthly
                    (store_id, mois, annee, niveau,
                     ca_cible_mensuel, activations_totales, activations_postpaye,
                     ventes_terminaux, facteur_saisonnier)
                SELECT
                    store_id,
                    %(mois)s,
                    %(annee)s,
                    'BOUTIQUE',
                    ROUND(AVG(monthly_ca) * %(growth_factor)s, 2),
                    ROUND(AVG(monthly_activations) * %(growth_factor)s)::INT,
                    GREATEST(1, ROUND(AVG(monthly_postpaye) * %(growth_factor)s))::INT,
                    ROUND(AVG(monthly_terminaux) * %(growth_factor)s)::INT,
                    %(seasonal_factor)s
                FROM (
                    SELECT
                        t.store_id,
                        DATE_TRUNC('month', t.transaction_date) AS mo,
                        SUM(t.lig_ttc) AS monthly_ca,
                        COUNT(DISTINCT t.id)
                            FILTER (WHERE p.flag_sim=TRUE)     AS monthly_activations,
                        COUNT(DISTINCT t.id)
                            FILTER (WHERE p.flag_forfait=TRUE
                                    AND p.nom ILIKE '%%POSTPAYE%%') AS monthly_postpaye,
                        COUNT(DISTINCT t.id)
                            FILTER (WHERE p.flag_terminal=TRUE) AS monthly_terminaux
                    FROM sales.transactions t
                    JOIN sales.produits p ON p.sku = t.sku
                    WHERE t.transaction_date >= (%(mois_date)s::DATE - INTERVAL '3 months')
                      AND t.transaction_date <  %(mois_date)s::DATE
                    GROUP BY t.store_id, DATE_TRUNC('month', t.transaction_date)
                ) hist
                GROUP BY store_id
                ON CONFLICT DO NOTHING
            """, {
                "mois": mois,
                "annee": annee,
                "growth_factor": 1.05,  # 5% growth target
                "seasonal_factor": _seasonal_factor(mois),
                "mois_date": m.isoformat(),
            })
            inserted += cur.rowcount

            # Agent-level targets = store target / nb active agents
            cur.execute("""
                INSERT INTO telco_targets_monthly
                    (store_id, agent_id, mois, annee, niveau,
                     ca_cible_mensuel, activations_totales, activations_postpaye,
                     ventes_terminaux, facteur_saisonnier)
                SELECT
                    a.store_id,
                    a.agent_id::INTEGER,
                    %(mois)s,
                    %(annee)s,
                    'AGENT',
                    ROUND(COALESCE(st.ca_cible_mensuel, 5000) /
                          NULLIF(cnt.nb_agents, 0), 2),
                    GREATEST(1, ROUND(COALESCE(st.activations_totales, 60) /
                             NULLIF(cnt.nb_agents, 0)))::INT,
                    GREATEST(1, ROUND(COALESCE(st.activations_postpaye, 8) /
                             NULLIF(cnt.nb_agents, 0)))::INT,
                    GREATEST(0, ROUND(COALESCE(st.ventes_terminaux, 10) /
                             NULLIF(cnt.nb_agents, 0)))::INT,
                    %(seasonal_factor)s
                FROM (
                    SELECT DISTINCT store_id, agent_id
                    FROM sales.transactions
                    WHERE date_only BETWEEN %(mois_date)s::DATE - INTERVAL '14 days'
                                       AND %(mois_date)s::DATE + INTERVAL '14 days'
                ) a
                JOIN (
                    SELECT store_id, COUNT(DISTINCT agent_id) nb_agents
                    FROM sales.transactions
                    WHERE date_only BETWEEN %(mois_date)s::DATE - INTERVAL '14 days'
                                       AND %(mois_date)s::DATE + INTERVAL '14 days'
                    GROUP BY store_id
                ) cnt ON cnt.store_id = a.store_id
                LEFT JOIN telco_targets_monthly st
                    ON st.store_id = a.store_id
                   AND st.mois = %(mois)s AND st.annee = %(annee)s
                   AND st.niveau = 'BOUTIQUE'
                ON CONFLICT DO NOTHING
            """, {
                "mois": mois,
                "annee": annee,
                "seasonal_factor": _seasonal_factor(mois),
                "mois_date": m.isoformat(),
            })
            inserted += cur.rowcount

        conn.commit()
    log.info(f"  telco_targets_monthly: {inserted} target rows generated")
    return inserted


def _seasonal_factor(mois: int) -> float:
    """Seasonal demand multiplier by month."""
    factors = {
        1: 0.90, 2: 0.95, 3: 1.40,   # Ramadan
        4: 1.70,  # Eid Al-Fitr
        5: 1.05, 6: 1.30,              # Eid Al-Adha
        7: 1.20, 8: 1.10,              # Summer
        9: 1.35,                       # Rentrée
        10: 1.00, 11: 1.25,            # Black Friday approach
        12: 1.40,                      # Soldes Hiver
    }
    return factors.get(mois, 1.0)


# ── 6. Compute weekly KPI summary ─────────────────────────────────────────────

def compute_weekly_kpi(conn, start_date: date, end_date: date) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO weekly_kpi_summary
                (store_id, agent_id, annee_semaine, semaine_debut, semaine_fin,
                 niveau, ca_semaine, nb_transactions, nb_postpaye, nb_terminaux,
                 nb_forfaits, panier_moyen, top_categorie, nb_jours_actifs)
            SELECT
                store_id,
                agent_id,
                TO_CHAR(kpi_date, 'IYYY-IW')    AS annee_semaine,
                MIN(kpi_date)                    AS semaine_debut,
                MAX(kpi_date)                    AS semaine_fin,
                'AGENT',
                SUM(ca_realise),
                SUM(nb_transactions),
                SUM(nb_postpaye),
                SUM(nb_terminaux),
                SUM(nb_forfaits),
                CASE WHEN SUM(nb_transactions) > 0
                     THEN ROUND(SUM(ca_realise)/SUM(nb_transactions), 2)
                     ELSE 0
                END,
                CASE
                    WHEN SUM(nb_terminaux) >= SUM(nb_forfaits)
                         AND SUM(nb_terminaux) >= SUM(nb_recharges)
                    THEN 'TERMINAL'
                    WHEN SUM(nb_forfaits) >= SUM(nb_recharges)
                    THEN 'FORFAIT'
                    ELSE 'RECHARGE'
                END,
                COUNT(DISTINCT kpi_date)
            FROM agent_kpi_daily
            WHERE kpi_date BETWEEN %(date_from)s AND %(date_to)s
            GROUP BY store_id, agent_id, TO_CHAR(kpi_date, 'IYYY-IW')
            ON CONFLICT (store_id, COALESCE(agent_id::text, 'NULL'), annee_semaine)
            DO UPDATE SET
                ca_semaine      = EXCLUDED.ca_semaine,
                nb_transactions = EXCLUDED.nb_transactions,
                nb_postpaye     = EXCLUDED.nb_postpaye,
                nb_terminaux    = EXCLUDED.nb_terminaux,
                nb_forfaits     = EXCLUDED.nb_forfaits,
                panier_moyen    = EXCLUDED.panier_moyen,
                top_categorie   = EXCLUDED.top_categorie
        """, {"date_from": start_date, "date_to": end_date})
        n = cur.rowcount
    conn.commit()
    log.info(f"  weekly_kpi_summary: {n} weekly rows upserted")
    return n


# ── Main ──────────────────────────────────────────────────────────────────────

def run(date_from: date, date_to: date):
    log.info(f"=== Seed KPIs & Targets: {date_from} → {date_to} ===")
    conn = get_conn()
    try:
        params = {"date_from": date_from, "date_to": date_to}

        log.info("1/6 Agent KPI daily...")
        with conn.cursor() as cur:
            cur.execute(AGENT_KPI_SQL, params)
            log.info(f"    {cur.rowcount} rows upserted")
        conn.commit()

        log.info("2/6 Store KPI daily...")
        with conn.cursor() as cur:
            cur.execute(STORE_KPI_SQL, params)
            log.info(f"    {cur.rowcount} rows upserted")
        conn.commit()

        log.info("3/6 Rank agents in store...")
        with conn.cursor() as cur:
            cur.execute(RANK_SQL, params)
            cur.execute(RANK_STORE_SQL, params)
        conn.commit()

        log.info("4/6 Monthly telco targets...")
        generate_telco_targets(conn, date_from, date_to)

        log.info("5/6 Store cumulative CA...")
        # Skip cumul update for now (requires telco_targets to exist first)

        log.info("6/6 Weekly KPI summary...")
        compute_weekly_kpi(conn, date_from, date_to)

    finally:
        conn.close()

    log.info("=== Seed KPIs & Targets — DONE ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed agent/store KPIs")
    parser.add_argument("--from", dest="date_from",
                        default="2024-10-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to",
                        default=date.today().isoformat(), help="End date YYYY-MM-DD")
    args = parser.parse_args()

    run(date.fromisoformat(args.date_from), date.fromisoformat(args.date_to))
