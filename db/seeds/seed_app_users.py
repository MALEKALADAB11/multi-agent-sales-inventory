"""
Seed des comptes applicatifs de démo (public.app_users).

Idempotent : ON CONFLICT (user_id) DO NOTHING — ne touche jamais un compte
existant. Anciennement dans auth_router.setup_auth_tables (DDL runtime,
supprimé au profit des migrations Alembic).

Usage : python db/seeds/seed_app_users.py
"""

import hashlib
import os
import sys

import psycopg2
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
load_dotenv()

_USERS = [
    # (user_id, username, password, full_name, role, store_id, store_name, initials, color, advisor_id)
    ("mgr-lac2",   "managerlac2",    "admin123", "Manager Ghassen",  "manager", "store-lac2",   "FR LAC2 TUNISIA MALL",     "MG", "#6C5CE7", None),
    ("mgr-menzah", "managermenzah",  "admin123", "Manager Menzah",   "manager", "store-menzah", "Boutique Habib Bourguiba", "MM", "#00B894", None),
    ("mgr-sfax",   "managersfax",    "admin123", "Manager Sfax",     "manager", "store-sfax",   "Boutique Sfax I",          "MS", "#2D9CDB", None),
    ("adv-zi", "zouiTeninsaf",    "zi1234", "Zouiten Insaf",    "vendeur", "store-lac2", "FR LAC2 TUNISIA MALL", "ZI", "#6C5CE7", "adv-zi"),
    ("adv-mh", "mansourhela",     "mh1234", "Mansour Hela",     "vendeur", "store-lac2", "FR LAC2 TUNISIA MALL", "MH", "#00B894", "adv-mh"),
    ("adv-bm", "benammarmeriam",  "bm1234", "Ben Ammar Meriam", "vendeur", "store-lac2", "FR LAC2 TUNISIA MALL", "BM", "#F9A825", "adv-bm"),
    ("adv-mk", "mansourkhouloud", "mk1234", "Mansour Khouloud", "vendeur", "store-lac2", "FR LAC2 TUNISIA MALL", "MK", "#2D9CDB", "adv-mk"),
]


def _hash(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()


def main() -> None:
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "ooredoo_sales"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )
    try:
        with conn.cursor() as cur:
            n = 0
            for uid, uname, pwd, fname, role, sid, sname, ini, col, adv in _USERS:
                cur.execute(
                    """
                    INSERT INTO app_users
                        (user_id, username, password_hash, full_name, role,
                         store_id, store_name, initials, color, advisor_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    (uid, uname, _hash(pwd), fname, role, sid, sname, ini, col, adv),
                )
                n += cur.rowcount
        conn.commit()
        print(f"app_users : +{n} comptes (sur {len(_USERS)} définis)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
