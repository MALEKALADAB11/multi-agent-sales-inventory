"""
postgres_provider.py — Données Ooredoo réelles depuis PostgreSQL.
Remplace mock_provider.py — même interface, vraies données.

Installation :
    pip install psycopg2-binary asyncpg --break-system-packages

Utilisation :
    Remplacer dans nodes.py :
        from data.mock_provider import get_data_provider
    Par :
        from data.postgres_provider import get_data_provider
"""

import logging
import asyncio
from datetime import datetime, date, timedelta
from functools import lru_cache
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

# ── Configuration PostgreSQL ──────────────────────────────────────────────────
DB_CONFIG = {
    "host":     "localhost",
    "port":     5432,
    "database": "ooredoo_sales",
    "user":     "postgres",
    "password": "postgres",   # ← adapter selon votre config
}

# ── Mapping familles produits → catégories UI ─────────────────────────────────
# COD_FAM depuis product.xls → label frontend
FAMILLE_MAP = {
    11: "Recharge",
    12: "Recharge",
    13: "Recharge",
    20: "SIM / Ligne",
    21: "SIM / Ligne",
    30: "Terminal",
    40: "Accessoire",
    41: "Accessoire",
    44: "Accessoire",
    47: "Accessoire",
    48: "Accessoire",
    10: "Forfait Mobile",
    15: "Forfait Mobile",
    52: "Forfait Data",
    60: "Box / Fibre",
    70: "Services",
    80: "Postpayé",
    90: "Postpayé",
    92: "Postpayé",
}

# Pattern horaire réel calculé depuis transactions.csv (toutes boutiques)
PATTERN_HORAIRE = {
    8: 3.27, 9: 7.35, 10: 10.19, 11: 14.34, 12: 12.20,
    13: 9.41, 14: 9.10, 15: 8.24, 16: 8.80, 17: 7.72,
    18: 5.10, 19: 2.44, 20: 0.90,
}


# ── Pool de connexions (singleton) ───────────────────────────────────────────

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            host     = DB_CONFIG["host"],
            port     = DB_CONFIG["port"],
            database = DB_CONFIG["database"],
            user     = DB_CONFIG["user"],
            password = DB_CONFIG["password"],
            min_size = 2,
            max_size = 10,
        )
        logger.info("[POSTGRES] Pool connexions créé")
    return _pool


# ── Provider principal ────────────────────────────────────────────────────────

class PostgresDataProvider:
    """
    Remplace MockDataProvider — même interface async.
    Lit les vraies données depuis PostgreSQL.
    """

    # ── fetch_pos_data ────────────────────────────────────────────────────────
    async def fetch_pos_data(self, store_id: str) -> dict:
        """
        CA temps réel de la journée pour une boutique.
        Lit les transactions du jour depuis PostgreSQL.
        """
        pool = await get_pool()
        today = date.today()

        async with pool.acquire() as conn:
            # CA et transactions du jour
            row = await conn.fetchrow("""
                SELECT
                    COALESCE(SUM(lig_ttc), 0)   AS ca_today,
                    COUNT(*)                     AS nb_transactions,
                    AVG(lig_ttc)                 AS avg_ticket
                FROM transactions
                WHERE store_id = $1
                  AND date_only = $2
                  AND lig_ttc  > 0
            """, store_id, today)

            # Objectif journalier
            obj_row = await conn.fetchrow("""
                SELECT objectif_ca
                FROM objectifs
                WHERE store_id = $1
                  AND date_objectif = $2
            """, store_id, today)

            # Infos boutique
            store_row = await conn.fetchrow("""
                SELECT store_name, ville, type_boutique
                FROM boutiques
                WHERE store_id = $1
            """, store_id)

            # CA par heure aujourd'hui
            hourly_rows = await conn.fetch("""
                SELECT heure, SUM(lig_ttc) AS ca_heure
                FROM transactions
                WHERE store_id = $1
                  AND date_only = $2
                GROUP BY heure
                ORDER BY heure
            """, store_id, today)

        ca_today   = float(row["ca_today"] or 0)
        nb_tx      = int(row["nb_transactions"] or 0)
        avg_ticket = float(row["avg_ticket"] or 0)
        objectif   = float(obj_row["objectif_ca"]) if obj_row else self._estimate_objectif(store_id, ca_today)

        hourly_ca = {r["heure"]: float(r["ca_heure"]) for r in hourly_rows}

        logger.info(
            f"[POSTGRES] POS {store_id} | CA={ca_today:,.0f} TND "
            f"| TX={nb_tx} | Obj={objectif:,.0f}"
        )

        return {
            "store_id":              store_id,
            "store_name":            store_row["store_name"] if store_row else store_id,
            "ville":                 store_row["ville"] if store_row else "",
            "daily_target":          objectif,
            "daily_target_tnd":      objectif,
            "current_revenue":       ca_today,
            "current_revenue_tnd":   ca_today,
            "nb_transactions_today": nb_tx,
            "avg_ticket":            round(avg_ticket, 2),
            "hourly_ca":             hourly_ca,
            "current_hour":          datetime.now().hour,
            "snapshot_time":         datetime.now().strftime("%H:%M"),
            "closing_hour":          20,
            "source":                "postgresql",
        }

    # ── fetch_pos_history ─────────────────────────────────────────────────────
    async def fetch_pos_history(self, store_id: str) -> list[dict]:
        """
        Transactions de la journée courante pour l'agent analyste.
        """
        pool = await get_pool()
        today = date.today()
        now = datetime.now()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    t.sale_id,
                    t.date_vente,
                    t.agent_id,
                    a.agent_name,
                    a.agent_surname,
                    t.cod_prod,
                    t.des_produit,
                    t.lig_ttc,
                    t.qte_produit,
                    t.heure,
                    p.cod_famille
                FROM transactions t
                LEFT JOIN agents   a ON t.agent_id = a.agent_id
                LEFT JOIN produits p ON t.cod_prod  = p.cod_prod
                WHERE t.store_id = $1
                  AND t.date_only = $2
                  AND t.lig_ttc  > 0
                ORDER BY t.date_vente DESC
                LIMIT 200
            """, store_id, today)

        history = []
        for r in rows:
            tx_time = r["date_vente"]
            minutes_ago = max(0, int((now - tx_time).total_seconds() / 60))
            famille = r["cod_famille"]
            categorie = FAMILLE_MAP.get(famille, "Autre")

            history.append({
                "sale_id":         r["sale_id"],
                "time":            tx_time.strftime("%H:%M"),
                "transaction_time": tx_time,
                "minutes_ago":     minutes_ago,
                "agent_id":        r["agent_id"],
                "agent_name":      f"{r['agent_name']} {r['agent_surname']}" if r["agent_name"] else "Inconnu",
                "product_code":    r["cod_prod"],
                "product_name":    r["des_produit"],
                "product_category": categorie,
                "revenue_tnd":     float(r["lig_ttc"]),
                "revenue":         float(r["lig_ttc"]),
                "quantity":        int(r["qte_produit"] or 1),
            })

        return history

    # ── fetch_timesfm_prediction ──────────────────────────────────────────────
    async def fetch_timesfm_prediction(self, store_id: str, **kwargs) -> dict:
        """
        Forecast EOD basé sur :
          P1 (60%) — même jour N-1 (si disponible)
          P2 (25%) — même semaine N-1
          P3 (15%) — moyenne même mois N-1
          + pattern horaire réel si historique insuffisant
        """
        pool = await get_pool()
        today = date.today()
        now = datetime.now()
        current_hour = now.hour
        hours_elapsed = max(1, current_hour - 8)
        hours_remaining = max(0, 20 - current_hour)

        async with pool.acquire() as conn:
            # CA actuel aujourd'hui
            ca_row = await conn.fetchrow("""
                SELECT COALESCE(SUM(lig_ttc), 0) AS ca_today
                FROM transactions
                WHERE store_id = $1 AND date_only = $2
            """, store_id, today)
            ca_today = float(ca_row["ca_today"])

            # Objectif
            obj_row = await conn.fetchrow("""
                SELECT objectif_ca FROM objectifs
                WHERE store_id = $1 AND date_objectif = $2
            """, store_id, today)
            objectif = float(obj_row["objectif_ca"]) if obj_row else self._estimate_objectif(store_id, ca_today)

            # ── P1 : même jour exact N-1 ──────────────────────────────────
            same_day_n1 = today - timedelta(days=365)
            p1_row = await conn.fetchrow("""
                SELECT
                    SUM(CASE WHEN heure <= $3 THEN lig_ttc ELSE 0 END) AS ca_at_hour,
                    SUM(lig_ttc)                                         AS ca_eod
                FROM transactions
                WHERE store_id = $1 AND date_only = $2
            """, store_id, same_day_n1, current_hour)

            # ── P2 : même semaine N-1 ─────────────────────────────────────
            week_ago = today - timedelta(days=7)
            p2_row = await conn.fetchrow("""
                SELECT
                    SUM(CASE WHEN heure <= $3 THEN lig_ttc ELSE 0 END) AS ca_at_hour,
                    SUM(lig_ttc)                                         AS ca_eod
                FROM transactions
                WHERE store_id = $1 AND date_only = $2
            """, store_id, week_ago, current_hour)

            # ── P3 : moyenne même mois dernière année ─────────────────────
            p3_row = await conn.fetchrow("""
                SELECT
                    AVG(ca_at_hour) AS ca_at_hour_avg,
                    AVG(ca_eod)     AS ca_eod_avg
                FROM (
                    SELECT
                        date_only,
                        SUM(CASE WHEN heure <= $3 THEN lig_ttc ELSE 0 END) AS ca_at_hour,
                        SUM(lig_ttc)                                         AS ca_eod
                    FROM transactions
                    WHERE store_id  = $1
                      AND EXTRACT(MONTH FROM date_only) = $2
                      AND date_only < $4
                    GROUP BY date_only
                ) sub
            """, store_id, today.month, current_hour, today)

            # ── Ratio historique depuis table pré-calculée ─────────────────
            ratio_row = await conn.fetchrow("""
                SELECT ratio_eod, ca_eod_moyen
                FROM ratios_historiques
                WHERE store_id = $1
                  AND jour_semaine = $2
                  AND heure = $3
            """, store_id, today.weekday(), current_hour)

        # ── Calculer le forecast pondéré ──────────────────────────────────────
        forecasts = []
        weights = []

        # P1 — même jour N-1
        p1_available = (
            p1_row and
            p1_row["ca_at_hour"] and
            float(p1_row["ca_at_hour"]) > 0
        )
        if p1_available:
            ratio_p1 = float(p1_row["ca_eod"]) / float(p1_row["ca_at_hour"])
            f_p1 = ca_today * ratio_p1
            forecasts.append(f_p1)
            weights.append(0.60)
            logger.info(f"[FORECAST] P1 (N-1) ratio={ratio_p1:.3f} → {f_p1:,.0f} TND")

        # P2 — même semaine N-1
        p2_available = (
            p2_row and
            p2_row["ca_at_hour"] and
            float(p2_row["ca_at_hour"]) > 0
        )
        if p2_available:
            ratio_p2 = float(p2_row["ca_eod"]) / float(p2_row["ca_at_hour"])
            f_p2 = ca_today * ratio_p2
            forecasts.append(f_p2)
            weights.append(0.25 if p1_available else 0.60)
            logger.info(f"[FORECAST] P2 (semaine) ratio={ratio_p2:.3f} → {f_p2:,.0f} TND")

        # P3 — moyenne même mois N-1
        p3_available = (
            p3_row and
            p3_row["ca_at_hour_avg"] and
            float(p3_row["ca_at_hour_avg"]) > 0
        )
        if p3_available:
            ratio_p3 = float(p3_row["ca_eod_avg"]) / float(p3_row["ca_at_hour_avg"])
            f_p3 = ca_today * ratio_p3
            forecasts.append(f_p3)
            weights.append(0.15 if p1_available else (0.40 if p2_available else 1.0))
            logger.info(f"[FORECAST] P3 (mois avg) ratio={ratio_p3:.3f} → {f_p3:,.0f} TND")

        # Fallback — ratio depuis table pré-calculée ou pattern horaire
        if not forecasts:
            if ratio_row and ratio_row["ratio_eod"]:
                ratio_fallback = float(ratio_row["ratio_eod"])
                f_fallback = ca_today * ratio_fallback
                logger.info(f"[FORECAST] Fallback ratio_table={ratio_fallback:.3f} → {f_fallback:,.0f}")
            else:
                # Pattern horaire pur
                pct_done = sum(
                    PATTERN_HORAIRE.get(h, 0)
                    for h in range(8, current_hour + 1)
                ) / 100
                pct_done = max(pct_done, 0.05)
                f_fallback = ca_today / pct_done
                logger.info(f"[FORECAST] Fallback pattern {pct_done:.1%} → {f_fallback:,.0f}")
            forecasts = [f_fallback]
            weights = [1.0]

        # Moyenne pondérée
        total_weight = sum(weights)
        forecast_eod = sum(f * w for f, w in zip(forecasts, weights)) / total_weight

        # Cap à 120% de l'objectif
        max_allowed = objectif * 1.20
        forecast_eod = min(round(forecast_eod), int(max_allowed))

        # Intervalles de confiance
        ci_spread = round(forecast_eod * 0.10)
        forecast_remaining = max(0, forecast_eod - round(ca_today))

        # Prévision horaire (reste de journée)
        total_pct_remaining = sum(
            PATTERN_HORAIRE.get(h, 0)
            for h in range(current_hour + 1, 21)
        ) / 100
        hourly_forecast = []
        for h in range(current_hour + 1, 21):
            pct = PATTERN_HORAIRE.get(h, 0) / 100
            if total_pct_remaining > 0:
                share = pct / total_pct_remaining
                hourly_forecast.append({
                    "hour": h,
                    "predicted": round(forecast_remaining * share)
                })

        logger.info(
            f"[FORECAST] {store_id} | CA={ca_today:,.0f} | "
            f"EOD={forecast_eod:,.0f} | Obj={objectif:,.0f} | "
            f"Sources={len(forecasts)}"
        )

        return {
            "forecast_end_of_day":     forecast_eod,
            "forecast_end_of_day_tnd": forecast_eod,
            "forecast_remaining":      forecast_remaining,
            "forecast_remaining_tnd":  forecast_remaining,
            "forecast_hourly":         [h["predicted"] for h in hourly_forecast],
            "hourly_forecast":         hourly_forecast,
            "confidence_interval": {
                "low":  max(round(ca_today), forecast_eod - ci_spread),
                "high": min(forecast_eod + ci_spread, int(max_allowed)),
            },
            "objectif":       objectif,
            "nb_sources":     len(forecasts),
            "p1_available":   p1_available,
            "p2_available":   p2_available,
            "p3_available":   p3_available,
            "model_version":  "timesfm-postgres-v1",
            "source":         "postgresql",
        }

    # ── fetch_sellers (advisors) ──────────────────────────────────────────────
    async def fetch_sellers(self, store_id: str) -> list[dict]:
        """
        Performance des agents de la boutique aujourd'hui.
        """
        pool = await get_pool()
        today = date.today()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    t.agent_id,
                    a.agent_name,
                    a.agent_surname,
                    COUNT(*)        AS nb_ventes,
                    SUM(t.lig_ttc)  AS ca_today,
                    AVG(t.lig_ttc)  AS panier_moyen,
                    o.objectif_ca   AS objectif_agent
                FROM transactions t
                LEFT JOIN agents   a ON t.agent_id = a.agent_id
                LEFT JOIN objectifs o ON (
                    o.store_id = t.store_id
                    AND o.date_objectif = $2
                    AND o.agent_id = t.agent_id
                )
                WHERE t.store_id = $1
                  AND t.date_only = $2
                  AND t.lig_ttc  > 0
                GROUP BY t.agent_id, a.agent_name, a.agent_surname, o.objectif_ca
                ORDER BY ca_today DESC
            """, store_id, today)

        sellers = []
        for r in rows:
            ca = float(r["ca_today"] or 0)
            obj = float(r["objectif_agent"]) if r["objectif_agent"] else ca * 1.2
            sellers.append({
                "agent_id":      r["agent_id"],
                "name":          f"{r['agent_name']} {r['agent_surname']}" if r["agent_name"] else r["agent_id"],
                "revenue_today": round(ca, 2),
                "nb_ventes":     int(r["nb_ventes"]),
                "panier_moyen":  round(float(r["panier_moyen"] or 0), 2),
                "objectif":      round(obj, 2),
                "attainment_pct": round((ca / obj * 100) if obj > 0 else 0, 1),
            })

        return sellers

    # ── fetch_product_mix ─────────────────────────────────────────────────────
    async def fetch_product_mix(self, store_id: str) -> list[dict]:
        """
        Mix produits du jour par catégorie.
        """
        pool = await get_pool()
        today = date.today()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    p.cod_famille,
                    SUM(t.lig_ttc)  AS ca_categorie,
                    COUNT(*)        AS nb_tx
                FROM transactions t
                LEFT JOIN produits p ON t.cod_prod = p.cod_prod
                WHERE t.store_id = $1
                  AND t.date_only = $2
                  AND t.lig_ttc  > 0
                GROUP BY p.cod_famille
                ORDER BY ca_categorie DESC
            """, store_id, today)

        total_ca = sum(float(r["ca_categorie"]) for r in rows)
        mix = []
        for r in rows:
            ca = float(r["ca_categorie"])
            famille = r["cod_famille"]
            mix.append({
                "category":  FAMILLE_MAP.get(famille, f"Famille {famille}"),
                "ca":        round(ca, 2),
                "pct":       round(ca / total_ca * 100, 1) if total_ca > 0 else 0,
                "nb_tx":     int(r["nb_tx"]),
            })

        return mix

    # ── fetch_top_products ────────────────────────────────────────────────────
    async def fetch_top_products(self, store_id: str, limit: int = 10) -> list[dict]:
        """
        Top produits vendus aujourd'hui.
        """
        pool = await get_pool()
        today = date.today()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    t.cod_prod,
                    t.des_produit,
                    p.cod_famille,
                    p.pv_ttc,
                    SUM(t.lig_ttc)  AS ca,
                    SUM(t.qte_produit) AS qte
                FROM transactions t
                LEFT JOIN produits p ON t.cod_prod = p.cod_prod
                WHERE t.store_id = $1
                  AND t.date_only = $2
                  AND t.lig_ttc  > 0
                GROUP BY t.cod_prod, t.des_produit, p.cod_famille, p.pv_ttc
                ORDER BY ca DESC
                LIMIT $3
            """, store_id, today, limit)

        return [
            {
                "cod_prod":   r["cod_prod"],
                "name":       r["des_produit"],
                "category":   FAMILLE_MAP.get(r["cod_famille"], "Autre"),
                "pv_ttc":     float(r["pv_ttc"] or 0),
                "ca":         round(float(r["ca"]), 2),
                "quantity":   int(r["qte"]),
            }
            for r in rows
        ]

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _estimate_objectif(self, store_id: str, ca_today: float) -> float:
        """Objectif estimé si pas en base : CA moyen × 1.10."""
        # Valeurs calculées depuis transaction_vente_test_100500_fast.csv
        objectifs_defaut = {
            "M10": 3457.0,
            "M23": 2047.0,
            "M06": 2046.0,
            "S47": 1946.0,
            "I86": 1863.0,
        }
        return objectifs_defaut.get(store_id, max(ca_today * 1.20, 1500.0))

    async def fetch_pos_context(self, store_id: str) -> dict | None:
        return None

    def list_stores(self) -> list[str]:
        return ["M10", "M23", "M06", "S47", "I86"]


# ── Singleton ─────────────────────────────────────────────────────────────────
_provider: Optional[PostgresDataProvider] = None


def get_data_provider() -> PostgresDataProvider:
    global _provider
    if _provider is None:
        _provider = PostgresDataProvider()
    return _provider