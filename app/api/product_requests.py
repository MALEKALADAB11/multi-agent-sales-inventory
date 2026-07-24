"""
product_requests.py — Demandes de réapprovisionnement des conseillers.

Boucle terrain → manager (HITL) :
  - Le conseiller (role=vendeur) crée une demande sur un produit signalé en
    rupture / stock critique sur sa page advisor.
  - Le manager voit toutes les demandes de sa boutique dans son espace de
    supervision et les approuve/rejette (avec note optionnelle).
  - Une demande approuvée peut être reliée ensuite à un BC Kanban (po_id).

RBAC :
  - POST   : tout utilisateur authentifié (vendeur ou manager).
  - GET    : manager → toutes les demandes de la boutique ;
             vendeur → uniquement les siennes.
  - PATCH  : manager uniquement.
"""
import logging
import secrets
from datetime import datetime
from typing import Optional

from psycopg2.extras import RealDictCursor
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from app.api.auth import _get_conn, _get_user_from_token

logger   = logging.getLogger(__name__)
router   = APIRouter(prefix="/api/v1/product-requests", tags=["product-requests"])
security = HTTPBearer(auto_error=False)


def _require_user(creds: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    token = creds.credentials if creds else None
    user  = _get_user_from_token(token) if token else None
    if not user:
        raise HTTPException(401, "Authentification requise.")
    return user


def _require_manager(user: dict = Depends(_require_user)) -> dict:
    if user["role"] != "manager":
        raise HTTPException(403, "Accès manager requis.")
    return user


def _row_out(r: dict) -> dict:
    out = dict(r)
    for k in ("created_at", "updated_at", "decided_at"):
        if isinstance(out.get(k), datetime):
            out[k] = out[k].isoformat()
    return out


# ── Models ────────────────────────────────────────────────────────────────────

class CreateRequestBody(BaseModel):
    product_name: str = Field(..., min_length=2, max_length=150)
    sku:          Optional[str] = Field(None, max_length=40)
    quantity:     int = Field(..., gt=0, le=10_000)
    reason:       Optional[str] = Field(None, max_length=2000)
    urgency:      str = Field("NORMALE", pattern="^(RUPTURE|CRITIQUE|NORMALE)$")


class DecideRequestBody(BaseModel):
    decision:     str = Field(..., pattern="^(APPROUVEE|REJETEE)$")
    manager_note: Optional[str] = Field(None, max_length=2000)
    po_id:        Optional[str] = Field(None, max_length=40)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("")
async def create_request(body: CreateRequestBody, user: dict = Depends(_require_user)):
    # Seul le terrain émet des demandes — le manager supervise et décide,
    # il ne se fait pas de demandes à lui-même.
    if user["role"] == "manager":
        raise HTTPException(403, "Les demandes sont émises par les conseillers ; "
                                 "le manager les approuve depuis la page Demandes.")
    req_id = f"REQ-{secrets.token_hex(6).upper()}"
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO product_requests
                    (request_id, store_id, sku, product_name, quantity,
                     reason, urgency, requested_by, advisor_name)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING *
                """,
                (req_id, user["store_id"], body.sku, body.product_name,
                 body.quantity, body.reason, body.urgency,
                 user["user_id"], user["full_name"]),
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    logger.info("[REQ] %s — %s x%d (%s) par %s",
                req_id, body.product_name, body.quantity, body.urgency, user["full_name"])
    return {"request": _row_out(row)}


@router.get("")
async def list_requests(
    statut: Optional[str] = None,
    user: dict = Depends(_require_user),
):
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            where  = ["store_id = %s"]
            params: list = [user["store_id"]]
            if user["role"] != "manager":
                where.append("requested_by = %s")
                params.append(user["user_id"])
            if statut:
                where.append("statut = %s")
                params.append(statut)
            cur.execute(
                f"SELECT * FROM product_requests WHERE {' AND '.join(where)} "
                "ORDER BY created_at DESC LIMIT 200",
                params,
            )
            rows = [_row_out(r) for r in cur.fetchall()]
    finally:
        conn.close()

    pending = sum(1 for r in rows if r["statut"] == "EN_ATTENTE")
    return {"requests": rows, "pending": pending}


@router.patch("/{request_id}")
async def decide_request(
    request_id: str,
    body: DecideRequestBody,
    user: dict = Depends(_require_manager),
):
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM product_requests WHERE request_id=%s AND store_id=%s",
                (request_id, user["store_id"]),
            )
            existing = cur.fetchone()
            if not existing:
                raise HTTPException(404, f"Demande '{request_id}' introuvable.")
            if existing["statut"] != "EN_ATTENTE":
                raise HTTPException(409, f"Demande déjà traitée ({existing['statut']}).")
            cur.execute(
                """
                UPDATE product_requests
                SET statut=%s, manager_note=%s, po_id=COALESCE(%s, po_id),
                    decided_by=%s, decided_at=NOW(), updated_at=NOW()
                WHERE request_id=%s
                RETURNING *
                """,
                (body.decision, body.manager_note, body.po_id,
                 user["user_id"], request_id),
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    logger.info("[REQ] %s → %s par %s", request_id, body.decision, user["full_name"])
    return {"request": _row_out(row)}
