"""
po_ws_bus.py — In-process WebSocket registry for the Purchase Order Kanban.

Shared between the API layer (src/api/supply_routes.py, owns the WS lifecycle:
accept/register/unregister) and the agent layer (src/agents/decision/agent.py,
publishes po_suggested events) so the decision agent never has to import the
API layer directly.

Sync/async bridge (broadcast_po_suggested_sync) mirrors the pattern already
used by src/services/redis_alert_bus.py::dispatch_alerts_sync — the decision
agent runs synchronously (inside a ThreadPoolExecutor worker), while the WS
send calls are async.
"""
from __future__ import annotations

import asyncio
import datetime
import decimal
import logging
import time
import uuid
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# store_id -> list of connected WebSocket clients for the PO board.
connections: Dict[str, List[Any]] = {}

# The event loop the WebSocket objects are bound to (uvicorn's server loop).
# A Starlette WebSocket can only be written from the loop that accepted it, so
# a worker thread must hand the coroutine back to that loop rather than spin up
# its own with asyncio.run(). Captured on the first WS accept (see
# supply_routes.po_board_ws) and, defensively, at app startup.
_server_loop: "asyncio.AbstractEventLoop | None" = None


def set_server_loop(loop: "asyncio.AbstractEventLoop") -> None:
    """Registers the loop that owns the WebSocket connections. Idempotent."""
    global _server_loop
    _server_loop = loop


def _json_safe(obj: Any) -> Any:
    """
    Raw psycopg2 RealDictCursor rows carry Decimal/UUID/date objects that
    Starlette's WebSocket.send_json (plain stdlib json under the hood) can't
    serialize — unlike REST responses, there's no jsonable_encoder in the way.
    """
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    return obj


async def broadcast_po_status(
    store_id: str, po_id: str, sku: Any, old_statut: str, new_statut: str,
) -> None:
    await _broadcast(store_id, {
        "type":       "po_status_changed",
        "store_id":   store_id,
        "po_id":      po_id,
        "sku":        sku,
        "old_statut": old_statut,
        "new_statut": new_statut,
        "timestamp":  time.time(),
    })


async def broadcast_po_suggested(po: Dict[str, Any]) -> None:
    """Notifies the board that the agent just created a SUGGERE purchase order."""
    store_id = po.get("store_id")
    if not store_id:
        return
    await _broadcast(store_id, {
        "type":      "po_suggested",
        "store_id":  store_id,
        "po":        _json_safe(po),
        "timestamp": time.time(),
    })


def broadcast_po_suggested_sync(po: Dict[str, Any]) -> None:
    """
    Sync wrapper for callers running outside an async context — the decision
    agent's run() is plain sync code executed in a ThreadPoolExecutor worker.

    The coroutine is handed to the loop that owns the WebSockets rather than run
    on a throwaway loop built by asyncio.run(): a Starlette WebSocket belongs to
    the loop that accepted it, and creating/tearing down one loop per broadcast
    is needless churn on the Windows Proactor loop.

    Never raises, never blocks — a failed or slow broadcast must not stall the
    decision pipeline; the PO row is already committed either way.
    """
    try:
        # Already on an event loop (e.g. called from async route code).
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        if running is not None:
            running.create_task(broadcast_po_suggested(po))
            return

        loop = _server_loop
        if loop is None or loop.is_closed():
            # No board client has ever connected, so nobody is listening.
            logger.debug("[po_ws_bus] no server loop registered — skipping broadcast")
            return

        future = asyncio.run_coroutine_threadsafe(broadcast_po_suggested(po), loop)

        def _log_failure(fut: Any) -> None:
            exc = fut.exception()
            if exc is not None:
                logger.warning("[po_ws_bus] broadcast to board failed: %r", exc)

        # Fire-and-forget: waiting here would block the agent worker on an event
        # loop that may itself be busy, and the pipeline must never depend on a
        # board client being responsive.
        future.add_done_callback(_log_failure)
    except Exception as exc:
        logger.warning("[po_ws_bus] broadcast_po_suggested_sync failed: %s", exc)


async def _broadcast(store_id: str, message: Dict[str, Any]) -> None:
    conns = connections.get(store_id, [])
    dead = []
    for ws in conns:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        conns.remove(ws)
