import psycopg2, hashlib

def sha256(s): return hashlib.sha256(s.encode()).hexdigest()

conn = psycopg2.connect(
    host="localhost", port=5432,
    dbname="ooredoo_sales", user="postgres", password="admin"
)
conn.set_client_encoding("UTF8")
cur = conn.cursor()

# Voir les valeurs longues dans public.app_users
print("=== Valeurs dans public.app_users ===")
cur.execute("SELECT id, user_id, username, store_id, initials, color, advisor_id FROM public.app_users")
for r in cur.fetchall():
    print(f"  id={r[0]} user_id={repr(r[1])} username={r[2]} store_id={repr(r[3])} initials={repr(r[4])} color={repr(r[5])} advisor_id={repr(r[6])}")

conn.close()