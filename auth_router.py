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


# ── Setup tables ──────────────────────────────────────────────────────────────

def setup_auth_tables():
    conn = _get_conn()
    try:
        with conn.cursor() as cur:

            # Utiliser mogrify-safe strings — pas de %s dans CREATE TABLE
            cur.execute(
                "CREATE TABLE IF NOT EXISTS app_users ("
                "id SERIAL PRIMARY KEY, "
                "user_id VARCHAR(30) UNIQUE NOT NULL, "
                "username VARCHAR(50) UNIQUE NOT NULL, "
                "password_hash VARCHAR(64) NOT NULL, "
                "full_name VARCHAR(100), "
                "role VARCHAR(20) NOT NULL, "
                "store_id VARCHAR(20), "
                "store_name VARCHAR(100), "
                "initials VARCHAR(5), "
                "color VARCHAR(10), "
                "advisor_id VARCHAR(20), "
                "actif BOOLEAN DEFAULT TRUE, "
                "created_at TIMESTAMP DEFAULT NOW(), "
                "last_login TIMESTAMP)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_au_username ON app_users(username)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_au_store ON app_users(store_id)"
            )

            cur.execute(
                "CREATE TABLE IF NOT EXISTS app_sessions ("
                "id SERIAL PRIMARY KEY, "
                "token VARCHAR(64) UNIQUE NOT NULL, "
                "user_id VARCHAR(30) NOT NULL, "
                "expires_at TIMESTAMP NOT NULL, "
                "created_at TIMESTAMP DEFAULT NOW(), "
                "last_used TIMESTAMP DEFAULT NOW(), "
                "ip_address VARCHAR(45))"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_as_token ON app_sessions(token)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_as_user ON app_sessions(user_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_as_exp ON app_sessions(expires_at)"
            )

            # Insérer les utilisateurs si vide
            cur.execute("SELECT COUNT(*) FROM app_users")
            if cur.fetchone()[0] == 0:
                users = [
                    ('mgr-lac2',   'managerlac2',    'admin123', 'Manager Ghassen',    'manager', 'store-lac2',   'FR LAC2 TUNISIA MALL',    'MG', '#6C5CE7', None),
                    ('mgr-menzah', 'managermenzah',  'admin123', 'Manager Menzah',     'manager', 'store-menzah', 'Boutique Habib Bourguiba', 'MM', '#00B894', None),
                    ('mgr-sfax',   'managersfax',    'admin123', 'Manager Sfax',       'manager', 'store-sfax',   'Boutique Sfax I',          'MS', '#2D9CDB', None),
                    ('adv-zi', 'zouiTeninsaf',    'zi1234', 'Zouiten Insaf',    'vendeur', 'store-lac2', 'FR LAC2 TUNISIA MALL', 'ZI', '#6C5CE7', 'adv-zi'),
                    ('adv-mh', 'mansourhela',     'mh1234', 'Mansour Hela',     'vendeur', 'store-lac2', 'FR LAC2 TUNISIA MALL', 'MH', '#00B894', 'adv-mh'),
                    ('adv-bm', 'benammarmeriam',  'bm1234', 'Ben Ammar Meriam', 'vendeur', 'store-lac2', 'FR LAC2 TUNISIA MALL', 'BM', '#F9A825', 'adv-bm'),
                    ('adv-mk', 'mansourkhouloud', 'mk1234', 'Mansour Khouloud', 'vendeur', 'store-lac2', 'FR LAC2 TUNISIA MALL', 'MK', '#2D9CDB', 'adv-mk'),
                ]
                for uid, uname, pwd, fname, role, sid, sname, ini, col, adv in users:
                    cur.execute(
                        "INSERT INTO app_users"
                        "(user_id,username,password_hash,full_name,role,"
                        "store_id,store_name,initials,color,advisor_id)"
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (uid, uname, _hash(pwd), fname, role,
                         sid, sname, ini, col, adv)
                    )
                logger.info(f"[AUTH] {len(users)} utilisateurs créés ✅")

        conn.commit()
        logger.info("[AUTH] Tables authentification prêtes ✅")
    except Exception as e:
        conn.rollback()
        logger.error(f"[AUTH] Setup error: {e}")
        raise
    finally:
        conn.close()


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
