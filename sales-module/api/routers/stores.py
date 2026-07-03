"""
stores.py — Router FastAPI 100% PostgreSQL.
Toutes les données viennent de la base de données en temps réel.
"""
from fastapi import APIRouter, HTTPException
from data.json_service import JsonDataService

router = APIRouter(prefix="/api/v1", tags=["stores"])
_json: JsonDataService = None

_STORE_MAP = {
    "store-lac2":    "I63",
    "store-menzah":  "M23",
    "store-sfax":    "M03",
    "OOR_LAC_01":    "I63",
    "OOR_MENZAH_02": "M23",
    "OOR_SFAX_03":   "M03",
    "I63": "I63", "M23": "M23", "M03": "M03",
}


def set_json_svc(svc: JsonDataService):
    global _json
    _json = svc


def _get_svc(store_id: str) -> JsonDataService:
    """Retourne un JsonDataService configuré pour le bon store."""
    cd_dist = _STORE_MAP.get(store_id, "I63")
    return JsonDataService(store_id=cd_dist)


@router.get("/stores")
async def list_stores():
    """Liste toutes les boutiques depuis PostgreSQL."""
    from data.json_service import _query
    rows = _query("""
        SELECT b.store_id, b.store_name, b.ville, b.type_boutique,
               COUNT(DISTINCT a.agent_id) AS nb_agents,
               COALESCE(o.objectif_ca, 0) AS objectif_ca
        FROM boutiques b
        LEFT JOIN agents a ON a.store_id = b.store_id AND a.actif = true
        LEFT JOIN objectifs o ON o.store_id = b.store_id
            AND o.agent_id IS NULL
            AND o.date_objectif = CURRENT_DATE
        GROUP BY b.store_id, b.store_name, b.ville, b.type_boutique, o.objectif_ca
        ORDER BY b.store_id
    """)
    return {"stores": rows}


@router.get("/stores/{store_id}/metrics")
async def get_store_metrics(store_id: str):
    """Métriques temps réel depuis PostgreSQL."""
    svc = _get_svc(store_id)
    return svc.get_store_metrics()


@router.get("/stores/{store_id}/advisors")
async def get_advisors(store_id: str):
    """Performance agents depuis PostgreSQL."""
    svc = _get_svc(store_id)
    return {"advisors": svc.get_advisors_performance()}


@router.get("/stores/{store_id}/context")
async def get_context(store_id: str):
    """Contexte boutique."""
    svc = _get_svc(store_id)
    return svc.get_context()


@router.get("/stores/{store_id}/transactions")
async def get_transactions(store_id: str, limit: int = 50):
    """Dernières transactions du jour depuis PostgreSQL."""
    svc = _get_svc(store_id)
    return {"transactions": svc.get_transactions_today()[:limit]}


@router.get("/stores/{store_id}/product-mix")
async def get_product_mix(store_id: str):
    """Mix produits réel depuis PostgreSQL."""
    from data.json_service import _query, _STORE_MAP
    cd = _STORE_MAP.get(store_id, "I63")
    rows = _query("""
        SELECT
            p.categorie,
            SUM(t.lig_ttc)     AS ca,
            COUNT(*)           AS nb_tx,
            SUM(t.quantity) AS nb_unites
        FROM sales.transactions t
        LEFT JOIN sales.produits p ON t.sku = p.sku
        WHERE t.store_id = %s
          AND t.date_only = CURRENT_DATE
          AND t.lig_ttc > 0
        GROUP BY p.categorie
        ORDER BY ca DESC
    """, (cd,))
    total = sum(float(r.get("ca", 0)) for r in rows)
    mix = [
        {
            "product":    r["categorie"] or "Autre",
            "revenue":    round(float(r["ca"]), 2),
            "nb_tx":      int(r["nb_tx"]),
            "nb_unites":  int(r.get("nb_unites", 0) or 0),
            "attainment": round(float(r["ca"]) / total * 100, 1) if total else 0,
            "stock_level": "OK",
        }
        for r in rows
    ]
    return {"product_mix": mix, "total_ca": round(total, 2)}


@router.get("/stores/{store_id}/live-analysis")
async def get_live_analysis(store_id: str):
    """
    Analyse en temps réel complète depuis PostgreSQL.
    Utilisé par le dashboard Angular pour la section Product Mix et advisors.
    """
    from data.postgres_provider import get_data_provider
    import asyncio

    provider = get_data_provider()
    cd = _STORE_MAP.get(store_id, "I63")

    pos_data, pos_history, forecast = await asyncio.gather(
        provider.fetch_pos_data(cd),
        provider.fetch_pos_history(cd),
        provider.fetch_timesfm_prediction(cd),
        return_exceptions=True
    )

    if isinstance(pos_data, Exception):
        raise HTTPException(status_code=500, detail=str(pos_data))

    svc      = _get_svc(store_id)
    advisors = svc.get_advisors_performance()
    mix_data = svc.get_hourly_ca()

    ca_today = float(pos_data.get("current_revenue", 0))
    objectif = float(pos_data.get("daily_target", 1007))
    eod      = float(forecast.get("forecast_end_of_day", 0)) if not isinstance(forecast, Exception) else 0

    # Product mix depuis PostgreSQL
    from data.json_service import _query
    mix_rows = _query("""
        SELECT
            COALESCE(p.categorie, 'Autre') AS product,
            SUM(t.lig_ttc)     AS revenue,
            COUNT(*)           AS nb_tx,
            SUM(t.quantity) AS nb_unites
        FROM sales.transactions t
        LEFT JOIN sales.produits p ON t.sku = p.sku
        WHERE t.store_id = %s
          AND t.date_only = CURRENT_DATE
          AND t.lig_ttc > 0
        GROUP BY p.categorie
        ORDER BY revenue DESC
    """, (cd,))

    total_ca = sum(float(r["revenue"]) for r in mix_rows)
    product_mix = [
        {
            "product":    r["product"],
            "revenue":    round(float(r["revenue"]), 2),
            "attainment": round(float(r["revenue"]) / total_ca * 100, 1) if total_ca else 0,
            "nb_unites":  int(r.get("nb_unites", 0) or 0),
            "stock_level": "OK",
        }
        for r in mix_rows
    ]

    # Fallback product mix depuis PostgreSQL si pas de données aujourd'hui
    if not product_mix:
        raw_mix = await provider.fetch_product_mix(cd)
        product_mix = [
            {
                "product":    m["category"],
                "revenue":    m["ca"],
                "attainment": m["pct"],
                "nb_unites":  0,
                "stock_level": "OK",
            }
            for m in raw_mix
        ]

    # Prévision horaire — CA réel par heure (mix_data) au lieu de 0 constant
    hourly_perf = []
    from datetime import datetime
    now_h = datetime.now().hour
    hourly_actual = {
        int(row["hour"].rstrip("h")): row.get("actual")
        for row in (mix_data or [])
        if row.get("actual") is not None
    }
    for h in range(9, 21):
        hourly_perf.append({
            "hour":     f"{h}h",
            "revenue":  hourly_actual.get(h, 0),
            "target":   round(objectif / 12, 2),
            "forecast": round(eod / 12, 2),
            "risk":     h <= now_h and ca_today < objectif * 0.5,
        })

    return {
        "store_id":           cd,
        "ca_today":           ca_today,
        "ca_target":          objectif,
        "attainment":         round(ca_today / objectif * 100, 1) if objectif else 0,
        "forecast_eod":       eod,
        "ecart_objectif":     round((objectif - ca_today) / objectif * 100, 1) if objectif else 0,
        "advisors":           [
            {
                "id":         a["id"],
                "name":       a["name"],
                "initials":   a["initials"],
                "revenue":    a["ca_realized"],
                "target":     a["ca_target"],
                "attainment": a["performance"],
                "nb_ventes":  a.get("nb_ventes", 0),
                "rank":       i + 1,
            }
            for i, a in enumerate(advisors)
        ],
        "product_mix":        product_mix,
        "hourly_performance": hourly_perf,
        "risk_hours":         [],
        "context_signals":    [],
        "updated_at":         datetime.now().isoformat(),
        "source":             "postgresql",
    }


@router.get("/stores/{store_id}/stats")
async def get_stats(store_id: str):
    """Stats debug depuis PostgreSQL."""
    svc = _get_svc(store_id)
    return svc.get_stats()


@router.post("/stores/{store_id}/reset")
async def reset_day(store_id: str):
    return {"status": "ok", "message": "PostgreSQL — pas de reset manuel"}


@router.post("/stores/{store_id}/simulate")
async def simulate(store_id: str):
    return {"status": "ok", "message": "PostgreSQL — données live"}
