from fastapi import APIRouter, HTTPException
from data.json_service import JsonDataService
from shared_module.store_mapper import to_canonical_store_id
router   = APIRouter(prefix="/api/v1", tags=["stores"])
_json:   JsonDataService = None


def set_json_svc(svc: JsonDataService):
    global _json
    _json = svc


@router.get("/stores")
async def list_stores():
    return {"stores": [_json.get_store()]}


@router.get("/stores/{store_id}/metrics")
async def get_store_metrics(store_id: str):
    canonical_id = to_canonical_store_id(store_id)  # "lac2" → "STORE-001"
    return _json.get_store_metrics(canonical_id)


@router.get("/stores/{store_id}/advisors")
async def get_advisors(store_id: str):
    return {"advisors": _json.get_advisors_performance()}


@router.get("/stores/{store_id}/context")
async def get_context(store_id: str):
    return _json.get_context()


@router.post("/stores/{store_id}/simulate")
async def simulate(store_id: str):
    """Recharge les JSON — simule un refresh des données."""
    from data.json_service import JsonDataService
    global _json
    _json = JsonDataService()
    return {"status": "ok", "message": "Data reloaded"}
@router.get("/stores/{store_id}/stats")
async def get_stats(store_id: str):
    """Debug — voir les stats en temps réel."""
    return _json.get_stats()


@router.post("/stores/{store_id}/reset")
async def reset_day(store_id: str):
    """Remet les transactions à zéro."""
    _json.reset_day()
    return {"status": "ok", "message": "Transactions cleared"}


@router.post("/stores/{store_id}/simulate")
async def simulate(store_id: str):
    """Recharge les JSON depuis les fichiers."""
    from data.json_service import JsonDataService
    global _json
    _json = JsonDataService()
    return {"status": "ok", "message": "Data reloaded from JSON files"}