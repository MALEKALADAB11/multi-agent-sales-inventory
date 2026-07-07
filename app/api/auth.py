"""
auth_router.py — Authentification PostgreSQL pour AI Sales Coach Ooredoo.
"""
import os
import logging
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from pathlib import Path
from dotenv import load_dotenv
# Load .env from project root
load_dotenv(Path(__file__).resolve().parent / ".env", encoding="utf-8")

logger   = logging.getLogger(__name__)
router   = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)

_DB_CONFIG = {
    "host":     os.getenv("POSTGRES_HOST", "localhost"),
    "port":     int(os.getenv("POSTGRES_PORT", 5432)),
    "dbname":   os.getenv("POSTGRES_DB", "ooredoo_sales"),
    "user":     os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", "admin"),
}


def _get_conn():
    os.environ['PGCLIENTENCODING'] = 'UTF8'
    conn = psycopg2.connect(**_DB_CONFIG)
    conn.set_client_encoding('UTF8')
    return conn


def _hash(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()


def _new_token() -> str:
    return secrets.token_hex(32)


# Les tables app_users/app_sessions sont créées par les migrations Alembic
# (db/migrations, baseline 0001) ; les comptes de démo sont insérés par
# db/seeds/seed_app_users.py. Plus aucun DDL au runtime.


# ── Session ───────────────────────────────────────────────────────────────────

def _create_session(user_id: str, hours: int = 24) -> str:
    tok = _new_token()
    exp = datetime.now() + timedelta(hours=hours)
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO app_sessions(token,user_id,expires_at) VALUES(%s,%s,%s)",
            (tok, user_id, exp)
        )
    conn.commit()
    conn.close()
    return tok


def _get_user_from_token(token: str) -> Optional[dict]:
    if not token:
        return None
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT u.user_id,u.username,u.full_name,u.role,"
                "u.store_id,u.store_name,u.initials,u.color,u.advisor_id "
                "FROM app_sessions s "
                "JOIN app_users u ON u.user_id=s.user_id "
                "WHERE s.token=%s AND s.expires_at>NOW() AND u.actif=TRUE",
                (token,)
            )
            row = cur.fetchone()
            if not row:
                return None
            cur.execute(
                "UPDATE app_sessions SET last_used=NOW() WHERE token=%s",
                (token,)
            )
        conn.commit()
        return dict(row)
    except Exception as e:
        logger.warning(f"[AUTH] Token error: {e}")
        return None
    finally:
        conn.close()


async def validate_store_access(
    token: Optional[str],
    requested_store_id: str,
    *,
    raise_on_missing_token: bool = False,
) -> Optional[dict]:
    """
    RBAC store-level check (S6.3).

    Rules:
      - No token         → allowed (backwards compat) unless raise_on_missing_token=True
      - role=manager     → allowed on any store
      - role=vendeur     → allowed only on their own store_id
      - mismatch         → 403

    Returns the user dict if allowed, None if no token and not required.
    """
    if not token:
        if raise_on_missing_token:
            raise HTTPException(status_code=401, detail="Token requis")
        return None

    import asyncio
    loop = asyncio.get_event_loop()
    user = await loop.run_in_executor(None, _get_user_from_token, token)
    if not user:
        raise HTTPException(status_code=401, detail="Token invalide ou expiré")

    if user["role"] == "manager":
        return user  # manager can access any store

    # Normalize: "I63" ↔ "store-lac2" style ids may differ — compare both raw and by suffix
    def _norm(s: str) -> str:
        return s.lower().replace("-", "").replace("_", "").replace("store", "")

    if _norm(user["store_id"]) != _norm(requested_store_id):
        raise HTTPException(
            status_code=403,
            detail=f"Accès refusé — votre compte est lié à '{user['store_id']}', "
                   f"pas à '{requested_store_id}'",
        )
    return user


def _fmt(row: dict) -> dict:
    return {
        "id":        row["user_id"],
        "username":  row["username"],
        "name":      row["full_name"],
        "role":      row["role"],
        "storeId":   row["store_id"],
        "storeName": row["store_name"],
        "initials":  row["initials"],
        "color":     row["color"],
        "advisorId": row.get("advisor_id"),
    }


# ── Models ────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/login")
async def login(body: LoginRequest):
    u = body.username.strip()
    p = body.password.strip()
    if not u or not p:
        raise HTTPException(400, "Champs requis")

    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM app_users "
                "WHERE LOWER(username)=LOWER(%s) AND password_hash=%s AND actif=TRUE",
                (u, _hash(p))
            )
            user = cur.fetchone()
            if not user:
                raise HTTPException(401, "Identifiant ou mot de passe incorrect.")
            cur.execute(
                "UPDATE app_users SET last_login=NOW() WHERE user_id=%s",
                (user["user_id"],)
            )
        conn.commit()
    finally:
        conn.close()

    hours = 24 if user["role"] == "manager" else 12
    tok   = _create_session(user["user_id"], hours=hours)
    logger.info(f"[AUTH] ✅ {user['full_name']} ({user['role']}) connecté")

    return JSONResponse({"token": tok, "user": _fmt(dict(user)), "expires": hours})


@router.get("/me")
async def me(creds: HTTPAuthorizationCredentials = Depends(security)):
    token = creds.credentials if creds else None
    user  = _get_user_from_token(token)
    if not user:
        raise HTTPException(401, "Session expirée ou invalide.")
    return JSONResponse({"user": _fmt(user)})


@router.post("/logout")
async def logout(creds: HTTPAuthorizationCredentials = Depends(security)):
    token = creds.credentials if creds else None
    if token:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM app_sessions WHERE token=%s", (token,))
        conn.commit()
        conn.close()
    return JSONResponse({"status": "logged_out"})


@router.get("/users")
async def list_users(creds: HTTPAuthorizationCredentials = Depends(security)):
    token = creds.credentials if creds else None
    user  = _get_user_from_token(token)
    if not user or user["role"] != "manager":
        raise HTTPException(403, "Accès manager requis.")
    conn = _get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT user_id,username,full_name,role,store_id,store_name,"
            "initials,color,advisor_id,actif,last_login "
            "FROM app_users WHERE store_id=%s ORDER BY role,full_name",
            (user["store_id"],)
        )
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return JSONResponse({"users": rows})


@router.post("/users/{user_id}/password")
async def change_password(
    user_id: str, body: dict,
    creds: HTTPAuthorizationCredentials = Depends(security),
):
    token = creds.credentials if creds else None
    req   = _get_user_from_token(token)
    if not req:
        raise HTTPException(401, "Non authentifié.")
    if req["role"] != "manager" and req["user_id"] != user_id:
        raise HTTPException(403, "Permission refusée.")
    pwd = body.get("password", "").strip()
    if len(pwd) < 4:
        raise HTTPException(400, "Mot de passe trop court.")
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE app_users SET password_hash=%s WHERE user_id=%s",
            (_hash(pwd), user_id)
        )
    conn.commit()
    conn.close()
    return JSONResponse({"status": "updated"})


@router.get("/sessions/clean")
async def clean_sessions():
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM app_sessions WHERE expires_at<NOW()")
        n = cur.rowcount
    conn.commit()
    conn.close()
    return JSONResponse({"deleted_sessions": n})
