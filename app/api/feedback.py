"""
feedback.py — Router de la boucle de feedback humain.
======================================================
POST /api/v1/feedback           : le conseiller déclare suivi/ignoré une incitation,
                                  ou une UI enregistre un rejet explicite.
GET  /api/v1/feedback/stats     : agrégats (taux de suivi, approbation HITL,
                                  adoption PO) pour le dashboard et les KPIs.

Endpoints sync (def) — FastAPI les exécute dans le threadpool, le service
utilise psycopg2 une-connexion-par-appel comme les repos inventory.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.feedback_service import get_feedback_stats, record_feedback

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])


class FeedbackPayload(BaseModel):
    store_id: str
    source: str = Field(pattern="^(incitation|hitl|po|reco)$")
    decision: str = Field(pattern="^(followed|ignored|approved|rejected)$")
    ref_id: Optional[str] = None
    sku: Optional[int] = None
    action_type: Optional[str] = None
    reason: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None


@router.post("")
def post_feedback(body: FeedbackPayload):
    fb_id = record_feedback(
        store_id=body.store_id,
        source=body.source,
        decision=body.decision,
        ref_id=body.ref_id,
        sku=body.sku,
        action_type=body.action_type,
        reason=body.reason,
        payload=body.payload,
    )
    if fb_id is None:
        raise HTTPException(status_code=500, detail="Enregistrement feedback impossible")
    return {"id": fb_id, "status": "recorded"}


@router.get("/stats")
def feedback_stats(store_id: Optional[str] = None, days: int = 14):
    return get_feedback_stats(store_id=store_id, days=days)