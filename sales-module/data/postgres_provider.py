"""
postgres_provider.py — v4.1
============================
Lit depuis les vues Ooredoo :
  - sales.vw_pos_enriched      → transactions enrichies
  - sales.vw_ca_par_boutique   → CA agrégé (inclut transactions_rt via UNION ALL)
  - sales.transactions_rt      → transactions temps réel du jour
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

import asyncpg
import numpy as np

logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host":     "localhost",
    "port":     5432,
    "database": "ooredoo_sales",
    "user":     "postgres",
    "password": "admin",
}

STORE_MAP = {
    "store-lac2": "I63",
    "I63":        "I63",
    "OOR_LAC_01": "I63",
    "lac2":       "I63",
}

DAILY_TARGETS = {
    "I63": 1007.0,
}

DEFAULT_TARGET = 1007.0

PATTERN_HORAIRE = {
    8: 1.20, 9: 3.27, 10: 7.35, 11: 10.19, 12: 14.34,
    13: 12.20, 14: 9.41, 15: 9.10, 16: 8.24, 17: 8.80,
    18: 7.72, 19: 5.10, 20: 2.44, 21: 0.90,
}


def normalize_store_id(store_id: str) -> str:
    return STORE_MAP.get(store_id, store_id)


async def get_pg_conn() -> asyncpg.Connection:
    return await asyncpg.connect(
        host=DB_CONFIG["host"], port=DB_CONFIG["port"],
        database=DB_CONFIG["database"],
        user=DB_CONFIG["user"], password=DB_CONFIG["password"],
        timeout=10, command_timeout=30,
    )


# ── fetch_pos_data ─────────────────────────────────────────────────────────────

async def fetch_pos_data(store_id: str) -> dict:
    sid = normalize_store_id(store_id)
    conn = await get_pg_conn()
    try:
        # vw_ca_par_boutique est maintenant une vue ordinaire qui inclut
        # transactions_rt via UNION ALL — une seule source de vérité
        last_date = await conn.fetchval(
            "SELECT MAX(date_only) FROM sales.vw_ca_par_boutique WHERE store_id = $1",
            sid
        )
        if not last_date:
            return _fallback_pos(sid)

        # Requête simple — vw_ca_par_boutique retourne déjà ca_total correct
        row = await conn.fetchrow("""
            SELECT store_id, store_name, ville, store_manager,
                   date_only        AS business_date,
                   nb_transactions,
                   ca_total         AS current_revenue,
                   avg_ticket
            FROM sales.vw_ca_par_boutique
            WHERE store_id = $1 AND date_only = $2
        """, sid, last_date)

        if not row:
            return _fallback_pos(sid)

        hourly_rows = await conn.fetch("""
            SELECT heure, SUM(montant_vente) AS ca_heure
            FROM sales.vw_pos_enriched
            WHERE store_id = $1 AND date_only = $2
            GROUP BY heure ORDER BY heure
        """, sid, last_date)

        hourly_ca       = {int(r["heure"]): float(r["ca_heure"]) for r in hourly_rows}
        daily_target    = DAILY_TARGETS.get(sid, DEFAULT_TARGET)
        current_revenue = float(row["current_revenue"] or 0)

        logger.info(
            f"[PG] POS {sid} | DATE={last_date} | "
            f"CA={current_revenue:.0f} TND | TX={row['nb_transactions']}"
        )

        return {
            "store_id":              sid,
            "store_name":            row["store_name"] or sid,
            "ville":                 row["ville"] or "",
            "store_manager":         row["store_manager"] or "",
            "business_date":         str(last_date),
            "daily_target":          daily_target,
            "daily_target_tnd":      daily_target,
            "current_revenue":       round(current_revenue, 2),
            "current_revenue_tnd":   round(current_revenue, 2),
            "nb_transactions_today": int(row["nb_transactions"] or 0),
            "avg_ticket":            round(float(row["avg_ticket"] or 0), 2),
            "hourly_ca":             hourly_ca,
            "current_hour":          datetime.now().hour,
            "snapshot_time":         datetime.now().strftime("%H:%M"),
            "closing_hour":          20,
            "data_status":           "available",
            "source":                "postgresql_enriched",
        }
    except Exception as e:
        logger.warning(f"[PG] fetch_pos_data error {sid}: {e}")
        return _fallback_pos(sid)
    finally:
        await conn.close()


def _fallback_pos(sid: str) -> dict:
    return {
        "store_id": sid, "store_name": sid,
        "business_date": str(date.today()),
        "daily_target": DAILY_TARGETS.get(sid, DEFAULT_TARGET),
        "daily_target_tnd": DAILY_TARGETS.get(sid, DEFAULT_TARGET),
        "current_revenue": 0, "current_revenue_tnd": 0,
        "nb_transactions_today": 0, "avg_ticket": 0,
        "hourly_ca": {}, "current_hour": datetime.now().hour,
        "snapshot_time": datetime.now().strftime("%H:%M"),
        "closing_hour": 20, "data_status": "unavailable", "source": "fallback",
    }


# ── fetch_pos_history ──────────────────────────────────────────────────────────

async def fetch_pos_history(store_id: str, limit: int = 200) -> list[dict]:
    sid = normalize_store_id(store_id)
    conn = await get_pg_conn()
    try:
        # Même logique : vw_ca_par_boutique inclut déjà RT → MAX retourne aujourd'hui
        last_date = await conn.fetchval(
            "SELECT MAX(date_only) FROM sales.vw_ca_par_boutique WHERE store_id = $1",
            sid
        )
        if not last_date:
            return []

        # vw_pos_enriched lit FROM sales.transactions (vue = history + RT)
        rows = await conn.fetch("""
            SELECT sale_id, date_vente, heure, agent_id, sku,
                   product_name, famille, groupe, gamme,
                   montant_vente AS revenue, montant_ht, qte,
                   forfait, type_abonnement,
                   stock_dispo, stock_risk, prix_catalogue, marge_valeur,
                   is_5g, is_4g, end_of_life
            FROM sales.vw_pos_enriched
            WHERE store_id = $1 AND date_only = $2
            ORDER BY date_vente DESC LIMIT $3
        """, sid, last_date, limit)

        now = datetime.now()
        history = []
        for r in rows:
            tx_dt = r["date_vente"]
            if tx_dt and hasattr(tx_dt, "hour"):
                fake = now.replace(hour=tx_dt.hour, minute=tx_dt.minute,
                                   second=0, microsecond=0)
                minutes_ago = max(0, int((now - fake).total_seconds() / 60))
            else:
                minutes_ago = 60

            history.append({
                "sale_id":          str(r["sale_id"] or ""),
                "time":             f"{int(r['heure'] or 0):02d}:00",
                "minutes_ago":      minutes_ago,
                "hour":             int(r["heure"] or 0),
                "agent_id":         str(r["agent_id"] or ""),
                "product_code":     str(r["sku"] or ""),
                "product_name":     str(r["product_name"] or ""),
                "product_category": str(r["famille"] or "Autre"),
                "famille":          str(r["famille"] or ""),
                "gamme":            str(r["gamme"] or ""),
                "revenue":          float(r["revenue"] or 0),
                "revenue_tnd":      float(r["revenue"] or 0),
                "quantity":         int(r["qte"] or 1),
                "forfait":          str(r["forfait"] or ""),
                "stock_dispo":      float(r["stock_dispo"]) if r["stock_dispo"] else None,
                "stock_risk":       str(r["stock_risk"] or "ok"),
                "prix_catalogue":   float(r["prix_catalogue"]) if r["prix_catalogue"] else None,
                "marge":            float(r["marge_valeur"]) if r["marge_valeur"] else None,
                "is_5g":            str(r["is_5g"] or "") == "O",
                "source":           "postgresql_enriched",
            })

        logger.info(f"[PG] History {sid} | DATE={last_date} | {len(history)} tx")
        return history
    except Exception as e:
        logger.warning(f"[PG] fetch_pos_history error {sid}: {e}")
        return []
    finally:
        await conn.close()


# ── fetch_timesfm_prediction ───────────────────────────────────────────────────

async def fetch_timesfm_prediction(store_id: str) -> dict:
    sid = normalize_store_id(store_id)
    conn = await get_pg_conn()
    try:
        last_date = await conn.fetchval(
            "SELECT MAX(date_only) FROM sales.vw_ca_par_boutique WHERE store_id = $1", sid
        )
        if not last_date:
            return _fallback_forecast(sid)

        ca_ref = float(await conn.fetchval(
            "SELECT ca_total FROM sales.vw_ca_par_boutique WHERE store_id=$1 AND date_only=$2",
            sid, last_date) or 0)

        hist_rows = await conn.fetch("""
            SELECT ca_total FROM sales.vw_ca_par_boutique
            WHERE store_id = $1 AND date_only < $2
              AND EXTRACT(DOW FROM date_only) = EXTRACT(DOW FROM $2::date)
            ORDER BY date_only DESC LIMIT 4
        """, sid, last_date)

        last7_rows = await conn.fetch("""
            SELECT ca_total FROM sales.vw_ca_par_boutique
            WHERE store_id=$1 AND date_only < $2 AND date_only >= $3
        """, sid, last_date, last_date - timedelta(days=7))

        last7_ca = [float(r["ca_total"]) for r in last7_rows if r["ca_total"]]
        avg_7d   = sum(last7_ca) / len(last7_ca) if last7_ca else ca_ref

        daily_target = DAILY_TARGETS.get(sid, DEFAULT_TARGET)
        current_hour = datetime.now().hour
        pct_done     = max(sum(PATTERN_HORAIRE.get(h, 0) for h in range(8, current_hour + 1)) / 100, 0.03)

        forecasts, weights = [], []

        if ca_ref > 0:
            forecasts.append(ca_ref / pct_done); weights.append(0.5)

        for i, row in enumerate(hist_rows):
            ca_sim = float(row["ca_total"] or 0)
            if ca_sim > 0:
                forecasts.append(ca_sim); weights.append(1.0 / (i + 1))

        if avg_7d > 0 and ca_ref > 0:
            expected = avg_7d * pct_done
            if expected > 0:
                forecasts.append(avg_7d * (ca_ref / expected)); weights.append(0.3)

        if forecasts:
            forecast_eod = sum(f * w for f, w in zip(forecasts, weights)) / sum(weights)
        else:
            forecast_eod = avg_7d or ca_ref

        forecast_eod = min(round(forecast_eod), int(daily_target * 1.5))
        forecast_eod = max(forecast_eod, round(ca_ref))

        std_7d    = float(np.std(last7_ca)) if len(last7_ca) >= 2 else forecast_eod * 0.10
        ci_spread = min(std_7d * 0.5, forecast_eod * 0.15)
        mape = round(min(float(np.mean([abs(f - ca_ref) / ca_ref * 100
                   for f in forecasts[:2]])), 30.0), 1) if len(forecasts) >= 2 and ca_ref > 0 else 14.3

        logger.info(
            f"[PG] Forecast {sid} | DATE={last_date} | CA={ca_ref:.0f} | "
            f"EOD={forecast_eod:.0f} | Obj={daily_target:.0f} | Sources={len(forecasts)}"
        )

        return {
            "forecast_end_of_day":     forecast_eod,
            "forecast_end_of_day_tnd": forecast_eod,
            "forecast_remaining":      max(0, forecast_eod - round(ca_ref)),
            "confidence_interval":     {
                "low":  max(round(ca_ref), forecast_eod - round(ci_spread)),
                "high": min(forecast_eod + round(ci_spread), int(daily_target * 1.5)),
            },
            "objectif":      daily_target,
            "business_date": str(last_date),
            "ca_current":    round(ca_ref, 2),
            "nb_sources":    len(forecasts),
            "avg_7d_ca":     round(avg_7d, 2),
            "pct_done":      round(pct_done * 100, 1),
            "mape":          mape,
            "model_version": "pg-multi-source-v4",
            "source":        "postgresql_forecast",
        }
    except Exception as e:
        logger.warning(f"[PG] fetch_timesfm_prediction error {sid}: {e}")
        return _fallback_forecast(sid)
    finally:
        await conn.close()


def _fallback_forecast(sid: str) -> dict:
    return {
        "forecast_end_of_day": 0, "forecast_end_of_day_tnd": 0,
        "forecast_remaining": 0, "confidence_interval": {"low": 0, "high": 0},
        "objectif": DAILY_TARGETS.get(sid, DEFAULT_TARGET),
        "mape": 99.0, "nb_sources": 0, "source": "fallback",
    }


# ── fetch_sellers ──────────────────────────────────────────────────────────────

async def fetch_sellers(store_id: str) -> list[dict]:
    sid = normalize_store_id(store_id)
    conn = await get_pg_conn()
    try:
        last_date = await conn.fetchval(
            "SELECT MAX(date_only) FROM sales.vw_ca_par_boutique WHERE store_id = $1", sid
        )
        if not last_date:
            return []

        rows = await conn.fetch("""
            SELECT agent_id,
                   COUNT(*)           AS nb_ventes,
                   SUM(montant_vente) AS ca_total,
                   AVG(montant_vente) AS avg_ticket
            FROM sales.vw_pos_enriched
            WHERE store_id = $1 AND date_only = $2
            GROUP BY agent_id ORDER BY SUM(montant_vente) DESC
        """, sid, last_date)

        daily_target = DAILY_TARGETS.get(sid, DEFAULT_TARGET)
        nb_sellers   = max(len(rows), 1)
        per_target   = round(daily_target / nb_sellers)

        return [{
            "agent_id":       str(r["agent_id"] or ""),
            "revenue_today":  round(float(r["ca_total"] or 0), 2),
            "nb_ventes":      int(r["nb_ventes"] or 0),
            "avg_ticket":     round(float(r["avg_ticket"] or 0), 2),
            "objectif":       per_target,
            "attainment_pct": round(float(r["ca_total"] or 0) / per_target * 100) if per_target else 0,
            "business_date":  str(last_date),
            "source":         "postgresql_enriched",
        } for r in rows]
    except Exception as e:
        logger.warning(f"[PG] fetch_sellers error {sid}: {e}")
        return []
    finally:
        await conn.close()


# ── fetch_stock_enriched ───────────────────────────────────────────────────────

async def fetch_stock_enriched(store_id: str, limit: int = 200) -> list[dict]:
    sid = normalize_store_id(store_id)
    conn = await get_pg_conn()
    try:
        rows = await conn.fetch("""
            SELECT store_id, store_name, sku, product_name,
                   famille, groupe, gamme,
                   prix_catalogue, prix_achat, marge_valeur, marge_pct,
                   stock_min, stock_max, is_5g, is_4g, end_of_life, lead_time,
                   stock_dispo, stock_disponible,
                   qte_vendue_cumul, qte_reservee,
                   velocity_ratio, stock_risk,
                   inv_stock_level, inv_available_qty
            FROM sales.vw_stock_enriched
            WHERE store_id = $1
            ORDER BY stock_risk DESC, stock_dispo ASC
            LIMIT $2
        """, sid, limit)
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[PG] fetch_stock_enriched error {sid}: {e}")
        return []
    finally:
        await conn.close()


# ── fetch_boutiques ────────────────────────────────────────────────────────────

async def fetch_boutiques() -> list[dict]:
    conn = await get_pg_conn()
    try:
        rows = await conn.fetch("""
            SELECT b."CD_DIST" AS store_id, b."NOM_DIST" AS store_name,
                   b."VILLE" AS ville, b."RESPONSABLE" AS responsable,
                   b."TEL" AS tel, b."EMAIL" AS email,
                   b."INTERNE_EXTERNE" AS type_boutique, b."CD_STATUT" AS statut,
                   COALESCE(c.nb_transactions, 0) AS nb_transactions,
                   COALESCE(c.ca_total, 0)        AS ca_total
            FROM sales.ooredoo_boutiques b
            LEFT JOIN (
                SELECT store_id,
                       SUM(nb_transactions) AS nb_transactions,
                       SUM(ca_total)        AS ca_total
                FROM sales.vw_ca_par_boutique GROUP BY store_id
            ) c ON b."CD_DIST" = c.store_id
            ORDER BY COALESCE(c.ca_total, 0) DESC
        """)
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[PG] fetch_boutiques error: {e}")
        return []
    finally:
        await conn.close()


# ── get_latest_business_date ───────────────────────────────────────────────────

async def get_latest_business_date(store_id: str) -> Optional[date]:
    sid = normalize_store_id(store_id)
    conn = await get_pg_conn()
    try:
        return await conn.fetchval(
            "SELECT MAX(date_only) FROM sales.vw_ca_par_boutique WHERE store_id = $1", sid
        )
    except Exception as e:
        logger.warning(f"[PG] get_latest_business_date error {sid}: {e}")
        return None
    finally:
        await conn.close()


# ── Singleton ──────────────────────────────────────────────────────────────────

class PostgresProvider:
    async def fetch_pos_data(self, store_id: str) -> dict:
        return await fetch_pos_data(store_id)
    async def fetch_pos_history(self, store_id: str) -> list:
        return await fetch_pos_history(store_id)
    async def fetch_timesfm_prediction(self, store_id: str) -> dict:
        return await fetch_timesfm_prediction(store_id)
    async def fetch_sellers(self, store_id: str) -> list:
        return await fetch_sellers(store_id)
    async def fetch_stock_enriched(self, store_id: str) -> list:
        return await fetch_stock_enriched(store_id)


_provider_instance: Optional[PostgresProvider] = None


def get_data_provider() -> PostgresProvider:
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = PostgresProvider()
    return _provider_instance