from fastapi import APIRouter
from app.sales.orchestration.graph import CycleOrchestrator
from app.sales.orchestration.trigger import CronTrigger

router        = APIRouter(prefix="/api/v1/cycle", tags=["cycle"])
_orchestrator: CycleOrchestrator = None
_trigger:      CronTrigger       = None


def set_orchestrator(orc: CycleOrchestrator, trg: CronTrigger):
    global _orchestrator, _trigger
    _orchestrator = orc
    _trigger      = trg


@router.post("/trigger")
async def trigger_cycle(store_id: str | None = None):
    """
    Déclenche un cycle manuellement sur la boutique demandée.

    `store_id` est désormais transmis à l'orchestrateur — l'ancienne version
    l'acceptait mais lançait toujours la boutique par défaut.
    """
    result = await _trigger.fire_now("manual", store_id=store_id)
    return {
        "store_id":       store_id or getattr(_trigger, "store_id", None),
        "cycle_id":       result.get("cycle_id"),
        "niveau_urgence": result.get("niveau_urgence"),
        "ecart_objectif": result.get("ecart_objectif"),
        "forecast_eod":   result.get("forecast_eod"),
        "duration_ms":    (result.get("metrics") or {}).get("total_ms"),
    }


@router.get("/status")
async def get_status():
    """État du dernier cycle."""
    last = _trigger.last_result
    if not last:
        return {"status": "no_cycle_yet"}
    return {
        "cycle_id":       last.get("cycle_id"),
        "niveau_urgence": last.get("niveau_urgence"),
        "ecart_objectif": last.get("ecart_objectif"),
        "forecast_eod":   last.get("forecast_eod"),
        "forecast_mape":  last.get("forecast_mape"),
        "pos_data":       last.get("pos_data"),
        "errors":         last.get("errors"),
        "metrics":        last.get("metrics")
    }
