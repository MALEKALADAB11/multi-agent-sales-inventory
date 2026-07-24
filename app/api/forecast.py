"""
forecast.py — Router FastAPI forecast 100% PostgreSQL.
"""
import asyncio
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
    """
    Prévision fin de journée — servie par le MÊME moteur que l'agent Analyste
    et le flux WebSocket (`ts_engine.analyze_store`), garde-fou d'invariants
    compris. Plus aucune valeur (MAPE, modèle) codée en dur : tout vient de
    l'analyse réelle.
    """
    from app.sales.coaching.agents.analyst.ts_engine import analyze_store

    cd = _STORE_MAP.get(store_id, store_id or DEFAULT_STORE_ID)
    a  = await analyze_store(cd)
    if a.get("error"):
        return {"store_id": cd, "error": a["error"], "source": "ts_engine"}

    return {
        "store_id":    cd,
        "ca_realized": a["current_ca"],
        "ca_target":   a["daily_target"],
        "gap_pct":     a["gap_pct"],
        "eod":         a["eod_forecast"],
        "ci_low":      a["eod_ci_low"],
        "ci_high":     a["eod_ci_high"],
        "mape":        a["mape_backtest"],
        "model":       a["model_engine"],
        "feasibility": a["feasibility"],
        "urgency":     a["urgency_level"],
        "source":      "ts_engine",
        "updated_at":  datetime.utcnow().isoformat(),
    }


@router.get("/hourly/{store_id}")
async def get_hourly_forecast(store_id: str, hours: int = 8):
    """
    Prévision horaire — trajectoire de `analyze_store`, garantie cohérente avec
    l'EOD : CA réalisé + Σ prévisions horaires = prévision de fin de journée.
    Chaque heure passée porte son réalisé, chaque heure à venir sa prévision.
    """
    from app.sales.coaching.agents.analyst.ts_engine import analyze_store

    cd = _STORE_MAP.get(store_id, store_id or DEFAULT_STORE_ID)
    a  = await analyze_store(cd)
    if a.get("error"):
        return {"store_id": cd, "hours": [], "error": a["error"], "source": "ts_engine"}

    # Heures écoulées (réalisé, depuis le ledger) puis heures à venir (prévues).
    rows = []
    for e in a.get("hourly_ledger", []):
        rows.append({
            "hour": e["hour"], "actual": e["actual"], "forecast": e["expected"],
            "status": e["status"], "ci_low": None, "ci_high": None,
        })
    for x in a.get("next_hours", []):
        band = x.get("std_ca", 0) or 0
        rows.append({
            "hour": x["hour"], "actual": None, "forecast": x["expected_ca"],
            "cumulative": x["cumulative_ca"],
            "ci_low": round(max(0.0, x["expected_ca"] - band), 2),
            "ci_high": round(x["expected_ca"] + band, 2),
        })

    return {
        "store_id":        cd,
        "hours":           rows,
        "target_hit_hour": a.get("target_hit_hour"),
        "eod_forecast":    a["eod_forecast"],
        "source":          "ts_engine",
        "model":           a["model_engine"],
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
