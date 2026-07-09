"""
Supply API Routes — Purchase Order Kanban board
================================================
Bounded context separate from src/api/routes.py (inventory): drives the
supply.purchase_orders lifecycle (BROUILLON -> SOUMIS -> CONFIRME -> EXPEDIE
-> RECU_PARTIEL/RECU, side states ANNULE/LITIGE).

Mounted standalone in main.py — own prefix, own WS registry, no coupling to
the inventory router's cache/WS machinery.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.inventory.repositories.supply_repo import (
    SyncPurchaseOrderRepo,
    PurchaseOrderTransitionError,
    ALLOWED_TRANSITIONS,
)
from app.inventory.services import po_ws_bus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/supply", tags=["supply"])


def _decimal_safe(obj: Any) -> Any:
    import decimal
    if isinstance(obj, dict):
        return {k: _decimal_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimal_safe(v) for v in obj]
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    return obj


async def _require_store_access(request: Request, store_id: str) -> None:
    """Mirrors the coach_chat.py RBAC call-site pattern (auth_router.validate_store_access)."""
    from app.api.auth import validate_store_access

    bearer = request.headers.get("Authorization", "")
    token = bearer[7:] if bearer.startswith("Bearer ") else None
    await validate_store_access(token, store_id)


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket registry — dedicated to PO board traffic, decoupled from the
# inventory router's _active_ws_connections (avoids mixing stock_delta noise).
# Registry + broadcast helpers live in src/services/po_ws_bus.py so the
# decision agent can publish po_suggested events without importing this
# API module.
# ─────────────────────────────────────────────────────────────────────────────

_broadcast_po_status = po_ws_bus.broadcast_po_status


@router.websocket("/ws/{store_id}")
async def po_board_ws(websocket: WebSocket, store_id: str) -> None:
    await websocket.accept()
    # The decision agent broadcasts from a worker thread; it needs a handle on
    # the loop these sockets are bound to. This is the earliest point where we
    # are guaranteed to be running on it.
    po_ws_bus.set_server_loop(asyncio.get_running_loop())
    conns = po_ws_bus.connections.setdefault(store_id, [])
    conns.append(websocket)
    logger.info("[supply/ws] connected store=%s (total=%d)", store_id, len(conns))
    try:
        while True:
            # Keep-alive: client pings every 15s (purchase-board-socket.service.ts).
            # We don't need the payload, just detect disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        conns = po_ws_bus.connections.get(store_id, [])
        if websocket in conns:
            conns.remove(websocket)
        logger.info("[supply/ws] disconnected store=%s (remaining=%d)", store_id, len(conns))


def _record_po_feedback(
    po: Dict[str, Any],
    po_id: str,
    *,
    decision: str,
    decided_by: str,
    reason: Optional[str] = None,
) -> None:
    """
    Journalise la décision humaine sur un PO suggéré dans public.agent_feedback.
    Le feedback_service agrège ces lignes et les réinjecte dans les prompts du
    DecisionAgent/Stratège (boucle d'apprentissage). Jamais bloquant : la
    transition du PO est déjà commitée quoi qu'il arrive ici.
    """
    try:
        from app.core.feedback_service import record_feedback
        record_feedback(
            store_id=str(po.get("store_id") or "unknown"),
            source="po",
            decision=decision,                       # 'approved' | 'rejected'
            ref_id=str(po_id),
            sku=int(po["sku"]) if po.get("sku") is not None else None,
            action_type="purchase_order",
            reason=reason,
            payload={
                "decided_by": decided_by,
                "quantite":   po.get("quantite_commandee"),
                "urgency":    po.get("urgency"),
                "statut":     po.get("statut"),
            },
        )
    except Exception as exc:
        logger.debug("Feedback PO %s non enregistré: %s", po_id, exc)


# ─────────────────────────────────────────────────────────────────────────────
# REST endpoints
# ─────────────────────────────────────────────────────────────────────────────

class CreatePurchaseOrderRequest(BaseModel):
    recommendation_id: str
    supplier_id: Optional[str] = None
    priorite: str = Field(default="NORMAL")


class UpdatePurchaseOrderStatusRequest(BaseModel):
    statut: str
    quantite_recue: Optional[int] = None


class ApprovePurchaseOrderRequest(BaseModel):
    decided_by: str


class RejectPurchaseOrderRequest(BaseModel):
    decided_by: str
    reason: Optional[str] = None


@router.get("/purchase-orders/{store_id}")
async def list_purchase_orders(
    request: Request,
    store_id: str,
    statut: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    await _require_store_access(request, store_id)
    try:
        orders = SyncPurchaseOrderRepo.list_purchase_orders(store_id, statut)
        return _decimal_safe({
            "store_id":         store_id,
            "purchase_orders":  orders,
            "count":            len(orders),
            "filter":           statut or "all",
        })
    except Exception as exc:
        logger.error("Failed to list purchase orders for %s: %s", store_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/purchase-orders/detail/{po_id}")
async def get_purchase_order(request: Request, po_id: str) -> Dict[str, Any]:
    po = SyncPurchaseOrderRepo.get_purchase_order_by_id(po_id)
    if not po:
        raise HTTPException(status_code=404, detail=f"Purchase order '{po_id}' not found.")
    await _require_store_access(request, po["store_id"])
    return _decimal_safe(po)


@router.post("/purchase-orders")
async def create_purchase_order(
    request: Request,
    req: CreatePurchaseOrderRequest,
) -> Dict[str, Any]:
    store_id = SyncPurchaseOrderRepo.get_recommendation_store_id(req.recommendation_id)
    if not store_id:
        raise HTTPException(
            status_code=404,
            detail=f"Recommendation '{req.recommendation_id}' not found.",
        )
    await _require_store_access(request, store_id)

    try:
        po = SyncPurchaseOrderRepo.create_from_recommendation(
            recommendation_id=req.recommendation_id,
            supplier_id=req.supplier_id,
            priorite=req.priorite,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    if not po:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Could not create purchase order from recommendation "
                f"'{req.recommendation_id}' — it must exist, reference a known "
                f"product, be in 'approved' status, and have a usable quantity."
            ),
        )

    # Kanban temps réel : sans ce broadcast, la nouvelle carte BROUILLON
    # n'apparaît qu'au prochain rechargement de page. po_suggested est le
    # seul event que le store Angular sait upserter (po_status_changed ne
    # fait que déplacer une carte déjà connue).
    await po_ws_bus.broadcast_po_suggested(po)

    logger.info("Purchase order %s created (BROUILLON) from recommendation %s",
                po["po_id"], req.recommendation_id)
    return _decimal_safe(po)


class SuggestPurchaseOrderRequest(BaseModel):
    recommendation_id: str = Field(..., description="inventory.recommendations.id")


@router.post("/purchase-orders/suggest")
async def suggest_purchase_order(
    request: Request,
    req: SuggestPurchaseOrderRequest,
) -> Dict[str, Any]:
    """
    Puts a SUGGERE card on the Kanban for a recommendation the agent has not
    yet turned into one — the manual counterpart of the decision agent's
    auto-suggestion, used by the "créer un ticket" action on a red product in
    the inventory page.

    Idempotent by design: the one-PO-per-recommendation rule is what makes it
    safe to click twice. A second call returns the existing card (with
    already_exists=true) instead of a 409, because from the operator's point of
    view "the ticket is on the board" is the same successful outcome either way.
    Unlike POST /purchase-orders it does NOT require the recommendation to be
    approved — SUGGERE is precisely the pre-approval state, and the human gate
    stays on the board's approve/reject buttons.
    """
    store_id = SyncPurchaseOrderRepo.get_recommendation_store_id(req.recommendation_id)
    if not store_id:
        raise HTTPException(
            status_code=404,
            detail=f"Recommendation '{req.recommendation_id}' not found.",
        )
    await _require_store_access(request, store_id)

    existing = SyncPurchaseOrderRepo.get_purchase_order_by_recommendation(req.recommendation_id)
    if existing:
        return _decimal_safe({**existing, "already_exists": True})

    po = SyncPurchaseOrderRepo.create_suggestion_from_recommendation(
        recommendation_id=req.recommendation_id,
    )
    if not po:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Could not suggest a purchase order for recommendation "
                f"'{req.recommendation_id}' — it must reference a known product "
                f"and carry a usable quantity."
            ),
        )

    await po_ws_bus.broadcast_po_suggested(po)
    logger.info("Purchase order %s suggested (SUGGERE) from recommendation %s",
                po["po_id"], req.recommendation_id)
    return _decimal_safe({**po, "already_exists": False})


@router.patch("/purchase-orders/{po_id}")
async def update_purchase_order_status(
    request: Request,
    po_id: str,
    req: UpdatePurchaseOrderStatusRequest,
) -> Dict[str, Any]:
    valid_statuses = set(ALLOWED_TRANSITIONS.keys())
    if req.statut not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid statut '{req.statut}'. Must be one of: {sorted(valid_statuses)}",
        )

    existing = SyncPurchaseOrderRepo.get_purchase_order_by_id(po_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Purchase order '{po_id}' not found.")
    await _require_store_access(request, existing["store_id"])

    old_statut = existing["statut"]

    try:
        updated = SyncPurchaseOrderRepo.update_status(po_id, req.statut, quantite_recue=req.quantite_recue)
    except PurchaseOrderTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not updated:
        raise HTTPException(status_code=404, detail=f"Purchase order '{po_id}' not found.")

    await _broadcast_po_status(
        store_id=updated["store_id"],
        po_id=po_id,
        sku=updated["sku"],
        old_statut=old_statut,
        new_statut=req.statut,
    )

    logger.info("Purchase order %s: %s -> %s", po_id, old_statut, req.statut)
    return {
        "po_id":   po_id,
        "statut":  req.statut,
        "updated": True,
    }


@router.post("/purchase-orders/{po_id}/approve")
async def approve_purchase_order(
    request: Request,
    po_id: str,
    req: ApprovePurchaseOrderRequest,
) -> Dict[str, Any]:
    """
    Approves an agent-suggested purchase order (statut SUGGERE): flips the
    underlying recommendation to 'approved' and the PO to BROUILLON in one
    transaction. This is the human gate — nothing downstream (SOUMIS and
    beyond) can happen without going through here first.
    """
    existing = SyncPurchaseOrderRepo.get_purchase_order_by_id(po_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Purchase order '{po_id}' not found.")
    await _require_store_access(request, existing["store_id"])

    try:
        updated = SyncPurchaseOrderRepo.approve_suggestion(po_id, decided_by=req.decided_by)
    except PurchaseOrderTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not updated:
        raise HTTPException(status_code=404, detail=f"Purchase order '{po_id}' not found.")

    await _broadcast_po_status(
        store_id=updated["store_id"], po_id=po_id, sku=updated["sku"],
        old_statut="SUGGERE", new_statut="BROUILLON",
    )
    _record_po_feedback(updated, po_id, decision="approved", decided_by=req.decided_by)
    logger.info("Purchase order %s approved by %s (SUGGERE -> BROUILLON)", po_id, req.decided_by)
    return _decimal_safe(updated)


@router.post("/purchase-orders/{po_id}/reject")
async def reject_purchase_order(
    request: Request,
    po_id: str,
    req: RejectPurchaseOrderRequest,
) -> Dict[str, Any]:
    """Rejects an agent-suggested purchase order: recommendation -> rejected, PO -> ANNULE."""
    existing = SyncPurchaseOrderRepo.get_purchase_order_by_id(po_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Purchase order '{po_id}' not found.")
    await _require_store_access(request, existing["store_id"])

    try:
        updated = SyncPurchaseOrderRepo.reject_suggestion(po_id, decided_by=req.decided_by)
    except PurchaseOrderTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not updated:
        raise HTTPException(status_code=404, detail=f"Purchase order '{po_id}' not found.")

    await _broadcast_po_status(
        store_id=updated["store_id"], po_id=po_id, sku=updated["sku"],
        old_statut="SUGGERE", new_statut="ANNULE",
    )
    _record_po_feedback(updated, po_id, decision="rejected",
                        decided_by=req.decided_by, reason=req.reason)
    logger.info("Purchase order %s rejected by %s (SUGGERE -> ANNULE)", po_id, req.decided_by)
    return _decimal_safe(updated)
