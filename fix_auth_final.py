import psycopg2, hashlib

def sha256(s): return hashlib.sha256(s.encode()).hexdigest()

STORE_MAP = {
    "store-lac2":   "I63",
    "store-menzah": "M23",
    "store-sfax":   "M03",
}

conn = psycopg2.connect(
    host="localhost", port=5432,
    dbname="ooredoo_sales", user="postgres", password="admin"
)
conn.set_client_encoding("UTF8")
cur = conn.cursor()

# Recréer tables proprement
cur.execute("DROP TABLE IF EXISTS sales.app_sessions CASCADE")
cur.execute("DROP TABLE IF EXISTS sales.app_users CASCADE")
cur.execute("""
    CREATE TABLE sales.app_users (
        id            SERIAL PRIMARY KEY,
        user_id       VARCHAR(50),
        username      VARCHAR(50) UNIQUE NOT NULL,
        password_hash VARCHAR(64) NOT NULL,
        full_name     VARCHAR(100),
        role          VARCHAR(20) DEFAULT 'vendeur',
        store_id      VARCHAR(10) DEFAULT 'I63',
        store_name    VARCHAR(100),
        initials      VARCHAR(20),
        color         VARCHAR(30),
        advisor_id    VARCHAR(30),
        actif         BOOLEAN DEFAULT TRUE,
        last_login    TIMESTAMP,
        created_at    TIMESTAMP DEFAULT NOW()
    )
""")
cur.execute("""
    CREATE TABLE sales.app_sessions (
        id         SERIAL PRIMARY KEY,
        token      VARCHAR(64) UNIQUE NOT NULL,
        user_id    VARCHAR(50),
        expires_at TIMESTAMP NOT NULL,
        last_used  TIMESTAMP DEFAULT NOW(),
        ip_address VARCHAR(50),
        created_at TIMESTAMP DEFAULT NOW()
    )
""")
cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token ON sales.app_sessions(token)")
conn.commit()
print("Tables recrées OK")

# Migrer public.app_users avec mapping store_id
cur.execute("SELECT user_id, username, password_hash, full_name, role, store_id, initials, color, advisor_id, actif, last_login, created_at FROM public.app_users")
rows = cur.fetchall()
for row in rows:
    user_id, username, pwd, full_name, role, store_id, initials, color, advisor_id, actif, last_login, created_at = row
    store_id_mapped = STORE_MAP.get(store_id, store_id[:10] if store_id else "I63")
    cur.execute("""
        INSERT INTO sales.app_users
            (user_id, username, password_hash, full_name, role,
             store_id, initials, color, advisor_id, actif, last_login, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (username) DO NOTHING
    """, (user_id, username, pwd, full_name, role,
          store_id_mapped, initials, color, advisor_id, actif, last_login, created_at))

conn.commit()
cur.execute("SELECT COUNT(*) FROM sales.app_users")
print(f"Migres depuis public: {cur.fetchone()[0]}")

# Ajouter les 6 utilisateurs PFE
users = [
    ("managerlac2",    sha256("admin123"), "Manager Ghassen",    "manager", "I63", "FR LAC2 Tunisia Mall", "MG", "#E40612"),
    ("zouiTeninsaf",   sha256("zi1234"),   "Zouiten Insaf",      "vendeur", "I63", "FR LAC2 Tunisia Mall", "ZI", "#00B894"),
    ("mansourHela",    sha256("mh1234"),   "Mansour Hela",       "vendeur", "I63", "FR LAC2 Tunisia Mall", "MH", "#0984E3"),
    ("benammarMeriam", sha256("bm1234"),   "Ben Ammar Meriam",   "vendeur", "I63", "FR LAC2 Tunisia Mall", "BM", "#6C5CE7"),
    ("mansourKhouloud",sha256("mk1234"),   "Mansour Khouloud",   "vendeur", "I63", "FR LAC2 Tunisia Mall", "MK", "#FDCB6E"),
    ("admin",          sha256("admin123"), "Administrateur",     "admin",   "I63", "Siege",               "AD", "#2D3436"),
]
for username, pwd, full_name, role, store_id, store_name, initials, color in users:
    cur.execute("""
        INSERT INTO sales.app_users
            (username, password_hash, full_name, role, store_id, store_name, initials, color, actif)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,TRUE)
        ON CONFLICT (username) DO UPDATE SET
            password_hash = EXCLUDED.password_hash,
            store_name    = EXCLUDED.store_name,
            initials      = EXCLUDED.initials,
            color         = EXCLUDED.color
    """, (username, pwd, full_name, role, store_id, store_name, initials, color))
conn.commit()

# Migrer sessions
cur.execute("SELECT token, user_id, expires_at, last_used, created_at FROM public.app_sessions")
for row in cur.fetchall():
    token, user_id, expires_at, last_used, created_at = row
    cur.execute("""
        INSERT INTO sales.app_sessions (token, user_id, expires_at, last_used, created_at)
        VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING
    """, (token, user_id, expires_at, last_used, created_at))
conn.commit()

# Résultat final
cur.execute("SELECT id, username, role, store_id, initials FROM sales.app_users ORDER BY id")
print("\n=== sales.app_users ===")
for r in cur.fetchall():
    print(f"  {r[0]:3d} | {r[1]:20s} | {r[2]:10s} | {r[3]:5s} | {r[4]}")

cur.execute("SELECT COUNT(*) FROM sales.app_sessions")
print(f"\nsales.app_sessions: {cur.fetchone()[0]}")

conn.close()
print("\n17/17 OK!")