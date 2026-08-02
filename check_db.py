import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(
    host=os.getenv('POSTGRES_HOST'),
    port=os.getenv('POSTGRES_PORT'),
    dbname=os.getenv('POSTGRES_DB'),
    user=os.getenv('POSTGRES_USER'),
    password=os.getenv('POSTGRES_PASSWORD')
)

cur = conn.cursor()

# Check if table exists
cur.execute("""
    SELECT table_name 
    FROM information_schema.tables 
    WHERE table_schema='inventory' AND table_name='critical_trend_history'
""")
table_exists = cur.fetchone() is not None
print(f"Table inventory.critical_trend_history exists: {table_exists}")

if table_exists:
    # Check if there's data
    cur.execute("SELECT COUNT(*) FROM inventory.critical_trend_history")
    count = cur.fetchone()[0]
    print(f"Number of rows in critical_trend_history: {count}")
    
    if count > 0:
        cur.execute("SELECT store_id, snapshot_time, critical_pct FROM inventory.critical_trend_history ORDER BY snapshot_time DESC LIMIT 5")
        rows = cur.fetchall()
        print("Recent snapshots:")
        for row in rows:
            print(f"  {row[0]}: {row[1]} -> {row[2]}%")
else:
    print("ERROR: Table does not exist - migration 0017 not applied!")

conn.close()
