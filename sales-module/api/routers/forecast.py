from fastapi import APIRouter
from datetime import datetime
from data.json_service import JsonDataService
from mcp_servers.timefm.tools import TimesFMTools

router   = APIRouter(prefix="/api/v1/forecast", tags=["forecast"])
_json:   JsonDataService = None
_timefm: TimesFMTools    = TimesFMTools(model_path="./models/timefm")


def set_json_svc(svc: JsonDataService):
    global _json
    _json = svc


@router.get("/eod/{store_id}")
async def get_eod_forecast(store_id: str):
    metrics  = _json.get_store_metrics()
    ca       = metrics["ca_today"]
    target   = metrics["ca_target"]
    gap      = round(((target - ca) / target * 100), 1) if target else 0

    forecast = await _timefm.forecast_eod(
        store_id      = store_id,
        ca_realized   = ca,
        sales_history = {},
        hour_current  = datetime.utcnow().hour
    )

    return {
        "store_id":    store_id,
        "ca_realized": ca,
        "ca_target":   target,
        "gap_pct":     gap,
        "eod":         forecast["eod"],
        "ci_low":      forecast["ci_low"],
        "ci_high":     forecast["ci_high"],
        "mape":        forecast["mape"],
        "model":       "TimesFM-v1.2",
        "updated_at":  datetime.utcnow().isoformat()
    }


@router.get("/hourly/{store_id}")
async def get_hourly_forecast(store_id: str, hours: int = 8):
    hourly = _json.get_hourly_ca()
    return {
        "hours": [
            {
                "hour":     h["hour"],
                "forecast": h["actual"] or h["target"],
                "ci_low":   round((h["actual"] or h["target"]) * 0.88, 2),
                "ci_high":  round((h["actual"] or h["target"]) * 1.12, 2)
            }
            for h in hourly
        ],
        "model": "JSON-mock"
    }


@router.get("/inventory/{store_id}")
async def get_inventory(store_id: str):
    return {"items": _json.get_inventory()}


@router.get("/alerts/{store_id}")
async def get_alerts(store_id: str):
    return {"alerts": _json.get_alerts()}