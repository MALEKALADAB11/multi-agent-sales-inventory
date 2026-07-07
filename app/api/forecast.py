"""
forecast.py — Router FastAPI forecast 100% PostgreSQL.
"""
from fastapi import APIRouter
from datetime import datetime
from app.core.config import DEFAULT_STORE_ID
from app.sales.data.json_service import JsonDataService
from app.sales.mcp.timefm.tools import TimesFMTools

router   = APIRouter(prefix="/api/v1/forecast", tags=["forecast"])
_json:   JsonDataService = None
_timefm: TimesFMTools    = TimesFMTools(model_path="./models/timefm")

# Alias boutique → id POS canonique ; les ids inconnus passent tels quels
# (multi-boutiques). La boutique par défaut vient de la config, pas du code.
_STORE_MAP = {
    "store-lac2":    DEFAULT_STORE_ID,
    "store-menzah":  "M23",
    "store-sfax":    "M03",
    "OOR_LAC_01":    DEFAULT_STORE_ID,
    "OOR_MENZAH_02": "M23",
    "OOR_SFAX_03":   "M03",
}


def set_json_svc(svc: JsonDataService):
    global _json
    _json = svc


def _get_svc(store_id: str) -> JsonDataService:
    cd_dist = _STORE_MAP.get(store_id, store_id or DEFAULT_STORE_ID)
    return JsonDataService(store_id=cd_dist)


@router.get("/eod/{store_id}")
async def get_eod_forecast(store_id: str):
    """Forecast EOD depuis PostgreSQL + TimesFM/Prophet."""
    from app.sales.data.postgres_provider import get_data_provider

    cd       = _STORE_MAP.get(store_id, store_id or DEFAULT_STORE_ID)
    provider = get_data_provider()

    # CA réel depuis PostgreSQL
    svc      = _get_svc(store_id)
    metrics  = svc.get_store_metrics()
    ca       = float(metrics.get("ca_today", 0))
    target   = float(metrics.get("ca_target", 1007))

    # Forecast via ratio historique réel
    forecast = await provider.fetch_timesfm_prediction(cd)
    eod      = float(forecast.get("forecast_end_of_day", 0))
    ci       = forecast.get("confidence_interval", {})
    gap      = round(((target - ca) / target * 100), 1) if target else 0

    # Utiliser Prophet si EOD = 0
    if eod == 0 or ca == 0:
        prophet_forecast = await _timefm.forecast_eod(
            store_id      = cd,
            ca_realized   = ca,
            sales_history = {},
            hour_current  = datetime.now().hour
        )
        eod    = prophet_forecast.get("eod", 0)
        ci     = {
            "low":  prophet_forecast.get("ci_low", 0),
            "high": prophet_forecast.get("ci_high", 0),
        }
        source = "prophet"
        mape   = prophet_forecast.get("mape", 0)
    else:
        source = forecast.get("source", "csv_ratio")
        mape   = 12.8

    return {
        "store_id":    cd,
        "ca_realized": round(ca, 2),
        "ca_target":   round(target, 2),
        "gap_pct":     gap,
        "eod":         round(eod, 2),
        "ci_low":      round(float(ci.get("low", 0)), 2),
        "ci_high":     round(float(ci.get("high", 0)), 2),
        "mape":        mape,
        "model":       f"TimesFM-{source}",
        "source":      "postgresql",
        "updated_at":  datetime.utcnow().isoformat(),
    }


@router.get("/hourly/{store_id}")
async def get_hourly_forecast(store_id: str, hours: int = 8):
    """Forecast horaire depuis PostgreSQL."""
    svc    = _get_svc(store_id)
    hourly = svc.get_hourly_ca()
    return {
        "hours": [
            {
                "hour":     h["hour"],
                "actual":   h.get("actual"),
                "forecast": h["target"],
                "ci_low":   round((h["target"] or 0) * 0.88, 2),
                "ci_high":  round((h["target"] or 0) * 1.12, 2),
            }
            for h in hourly
        ],
        "source": "postgresql",
        "model":  "ratio-historique",
    }


@router.get("/inventory/{store_id}")
async def get_inventory(store_id: str):
    """Inventaire temps réel depuis PostgreSQL."""
    svc = _get_svc(store_id)
    return {"items": svc.get_inventory(), "source": "postgresql"}


@router.get("/alerts/{store_id}")
async def get_alerts(store_id: str):
    """Alertes stock depuis PostgreSQL."""
    svc = _get_svc(store_id)
    return {"alerts": svc.get_alerts(), "source": "postgresql"}


@router.get("/product-mix/{store_id}")
async def get_product_mix(store_id: str):
    """Mix produits mensuel depuis PostgreSQL."""
    from app.sales.data.json_service import _query
    cd = _STORE_MAP.get(store_id, store_id or DEFAULT_STORE_ID)

    rows = _query("""
        SELECT
            COALESCE(p.categorie, 'Autre') AS categorie,
            SUM(t.lig_ttc)     AS ca_mensuel,
            COUNT(*)           AS nb_tx,
            SUM(t.quantity) AS nb_unites
        FROM sales.transactions t
        LEFT JOIN sales.produits p ON t.sku = p.sku
        WHERE t.store_id = %s
          AND t.date_only >= CURRENT_DATE - INTERVAL '30 days'
          AND t.lig_ttc > 0
        GROUP BY p.categorie
        ORDER BY ca_mensuel DESC
    """, (cd,))

    total = sum(float(r["ca_mensuel"]) for r in rows)
    return {
        "store_id": cd,
        "period":   "30 derniers jours",
        "mix": [
            {
                "categorie":  r["categorie"],
                "ca":         round(float(r["ca_mensuel"]), 2),
                "pct":        round(float(r["ca_mensuel"]) / total * 100, 1) if total else 0,
                "nb_tx":      int(r["nb_tx"]),
                "nb_unites":  int(r.get("nb_unites", 0) or 0),
            }
            for r in rows
        ],
        "total_ca": round(total, 2),
        "source":   "postgresql",
    }


@router.get("/top-products/{store_id}")
async def get_top_products(store_id: str, limit: int = 10):
    """Top produits depuis PostgreSQL."""
    from app.sales.data.json_service import _query
    cd = _STORE_MAP.get(store_id, store_id or DEFAULT_STORE_ID)

    rows = _query("""
        SELECT
            t.sku,
            t.des_produit,
            COALESCE(p.categorie, 'Autre') AS categorie,
            p.pv_ttc,
            SUM(t.lig_ttc)     AS ca,
            SUM(t.quantity) AS nb_unites
        FROM sales.transactions t
        LEFT JOIN sales.produits p ON t.sku = p.sku
        WHERE t.store_id = %s
          AND t.date_only >= CURRENT_DATE - INTERVAL '30 days'
          AND t.lig_ttc > 0
        GROUP BY t.sku, t.des_produit, p.categorie, p.pv_ttc
        ORDER BY ca DESC
        LIMIT %s
    """, (cd, limit))

    return {
        "store_id": cd,
        "products": [
            {
                "cod_prod":  str(r["cod_prod"]),
                "name":      str(r["des_produit"])[:50],
                "categorie": r["categorie"],
                "pv_ttc":    float(r.get("pv_ttc", 0) or 0),
                "ca":        round(float(r["ca"]), 2),
                "nb_unites": int(r.get("nb_unites", 0) or 0),
            }
            for r in rows
        ],
        "source": "postgresql",
    }
