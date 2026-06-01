"""
test_db_integration.py
======================
Verifies the full DB integration end-to-end without running the HTTP server.

Run from inventory-module/:
    python test_db_integration.py

What it checks:
  1. DB connection works
  2. SyncInventoryRepo reads (product, stock_level, active objective)
  3. analysis_agent.run() for one SKU — writes agent_run + stock_level + alert
  4. Reads back what was written and prints it

Pass/fail is printed clearly. No data is deleted after the test.
"""

import sys
import os
import json
from pathlib import Path

from src.agents.analysis.agent import create_analysis_agent

# Make sure imports resolve from inventory-module/
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# ── Colours for terminal output ───────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

def ok(msg):   print(f"  {GREEN}✓ {msg}{RESET}")
def fail(msg): print(f"  {RED}✗ {msg}{RESET}")
def info(msg): print(f"  {YELLOW}→ {msg}{RESET}")


# =============================================================================
# 1. DB connection
# =============================================================================
print("\n── 1. DB Connection ─────────────────────────────────────────────")
try:
    
    import psycopg2
    conn = psycopg2.connect(
        host    = os.getenv("DOCKER_DB_HOST"),
        port    = int(os.getenv("DOCKER_DB_PORT", "5432")),
        dbname  = os.getenv("DOCKER_DB_NAME"),
        user    = os.getenv("DOCKER_DB_USER"),
        password= os.getenv("DOCKER_DB_PASSWORD"),
    )
    conn.close()
    ok("Connected to DB")
except Exception as e:
    fail(f"DB connection failed: {e}")
    print("\nCannot continue — fix DB connection first.")
    sys.exit(1)


# =============================================================================
# 2. SyncInventoryRepo reads
# =============================================================================
print("\n── 2. SyncInventoryRepo reads ───────────────────────────────────")
from db.repositories.inventory_repo import SyncInventoryRepo

# Pick the first available SKU from inv.products
import psycopg2, psycopg2.extras, os
conn = psycopg2.connect(
        host    = os.getenv("DOCKER_DB_HOST"),
        port    = int(os.getenv("DOCKER_DB_PORT", "5432")),
        dbname  = os.getenv("DOCKER_DB_NAME"),
        user    = os.getenv("DOCKER_DB_USER"),
        password= os.getenv("DOCKER_DB_PASSWORD"),
)
with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute("""
        SELECT sl.sku, sl.store_id
        FROM inv.stock_levels sl
        JOIN inv.products p ON p.sku = sl.sku
        LIMIT 1
    """)
    row = cur.fetchone()
conn.close()

if not row:
    fail("No rows in inv.stock_levels — did you run init_stock_levels.py?")
    sys.exit(1)

TEST_SKU      = row["sku"]
TEST_STORE_ID = row["store_id"]
info(f"Using SKU={TEST_SKU}  store={TEST_STORE_ID}")

product = SyncInventoryRepo.get_product(TEST_SKU)
if product:
    ok(f"get_product: {product.get('product_name')}  lead_time={product.get('lead_time_days')}d  moq={product.get('moq')}")
else:
    fail("get_product returned None")

stock = SyncInventoryRepo.get_stock_level(TEST_SKU, TEST_STORE_ID)
if stock:
    ok(f"get_stock_level: current={stock.get('stock_current')}  min={stock.get('stock_min')}  days={stock.get('remaining_days_of_stock')}")
else:
    fail("get_stock_level returned None")

objective = SyncInventoryRepo.get_active_objective()
if objective:
    ok(f"get_active_objective: label={objective.get('label')}  target={objective.get('target_value')}")
else:
    fail("get_active_objective returned None — did you run seed_static_data.py?")


# =============================================================================
# 3. agent_run lifecycle (start + complete)
# =============================================================================
print("\n── 3. agent_run write ───────────────────────────────────────────")
run_id = SyncInventoryRepo.start_agent_run("analysis_agent", TEST_STORE_ID)
if run_id:
    ok(f"start_agent_run: id={run_id}")
else:
    fail("start_agent_run returned None — check inv.agent_runs table exists")

SyncInventoryRepo.complete_agent_run(
    run_id=run_id, status="completed",
    items_processed=1, alerts_generated=0
)
if run_id:
    ok("complete_agent_run: updated")

# Verify in DB
conn = psycopg2.connect(
        host    = os.getenv("DOCKER_DB_HOST"),
        port    = int(os.getenv("DOCKER_DB_PORT", "5432")),
        dbname  = os.getenv("DOCKER_DB_NAME"),
        user    = os.getenv("DOCKER_DB_USER"),
        password= os.getenv("DOCKER_DB_PASSWORD"),
)
with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute("SELECT * FROM inv.agent_runs WHERE id = %s", (run_id,))
    db_run = dict(cur.fetchone() or {})
conn.close()

if db_run.get("status") == "completed":
    ok(f"Verified in DB: status={db_run['status']}  completed_at={db_run.get('completed_at')}")
else:
    fail(f"agent_run row not as expected: {db_run}")


# =============================================================================
# 4. stock_level upsert
# =============================================================================
print("\n── 4. stock_level upsert ────────────────────────────────────────")
original_stock = stock.get("stock_current", 0) if stock else 0
test_value     = original_stock + 999  # something obviously different

SyncInventoryRepo.upsert_stock_level_sync(
    TEST_SKU, TEST_STORE_ID,
    stock_current=test_value,
    remaining_days_of_stock=42.0,
)
readback = SyncInventoryRepo.get_stock_level(TEST_SKU, TEST_STORE_ID)
if readback and readback.get("stock_current") == test_value:
    ok(f"upsert_stock_level_sync: wrote {test_value}, read back {readback['stock_current']}") 
    # Restore original value
    SyncInventoryRepo.upsert_stock_level_sync(
        TEST_SKU, TEST_STORE_ID,
        stock_current=original_stock,
        remaining_days_of_stock=stock.get("remaining_days_of_stock"),
    )
    ok(f"Restored original stock_current={original_stock}")
else:
    fail(f"upsert did not persist — got {readback}")


# =============================================================================
# 5. alert insert + dedup
# =============================================================================
print("\n── 5. alert insert + dedup ──────────────────────────────────────")

# Clean up any leftover test alert first
conn = psycopg2.connect(
        host    = os.getenv("DOCKER_DB_HOST"),
        port    = int(os.getenv("DOCKER_DB_PORT", "5432")),
        dbname  = os.getenv("DOCKER_DB_NAME"),
        user    = os.getenv("DOCKER_DB_USER"),
        password= os.getenv("DOCKER_DB_PASSWORD"),
)
with conn.cursor() as cur:
    cur.execute(
        "DELETE FROM inv.alerts WHERE sku=%s AND store_id=%s AND recommended_action LIKE '%%TEST%%'",
        (TEST_SKU, TEST_STORE_ID)
    )
    conn.commit()
conn.close()

inserted = SyncInventoryRepo.insert_alert_if_new(
    sku=TEST_SKU, store_id=TEST_STORE_ID,
    alert_type="stockout_risk", severity="high",
    recommended_action="TEST — plan replenishment",
)
if inserted:
    ok("insert_alert_if_new: inserted")
else:
    fail("insert_alert_if_new: returned False on first insert")

# Second call — should be deduped
inserted2 = SyncInventoryRepo.insert_alert_if_new(
    sku=TEST_SKU, store_id=TEST_STORE_ID,
    alert_type="stockout_risk", severity="high",
    recommended_action="TEST — plan replenishment (duplicate)",
)
if not inserted2:
    ok("Dedup works: second insert correctly skipped")
else:
    fail("Dedup failed: second insert was not skipped")


# =============================================================================
# 6. Full agent run (optional — skipped if LLM not available)
# =============================================================================
print("\n── 6. Full analysis_agent.run() ─────────────────────────────────")
info("Running analysis_agent for one SKU — this may take a few seconds...")
try:

    agent = create_analysis_agent()
    result = agent.run(
        sku=TEST_SKU,
        store_id=TEST_STORE_ID,
        agent_run_id=run_id,
    )
    if "error" in result:
        fail(f"agent.run() returned error: {result['error']}")
    else:
        report = result.get("analysis_report", {})
        risk   = report.get("risk_assessment", {}).get("level", "?")
        obj    = result.get("business_objective", "?")
        days   = report.get("metrics", {}).get("days_of_stock_remaining", "?")
        ok(f"agent.run() succeeded")
        info(f"  objective       : {obj}")
        info(f"  risk_level      : {risk}")
        info(f"  days_of_stock   : {days}")
        info(f"  reasoning_source: {report.get('reasoning_source', '?')}")
except Exception as e:
    fail(f"agent.run() raised: {e}")


# =============================================================================
# Summary
# =============================================================================
print("\n── Done ─────────────────────────────────────────────────────────")
print("Check your DB with:")
print(f"  SELECT * FROM inv.agent_runs ORDER BY created_at DESC LIMIT 5;")
print(f"  SELECT * FROM inv.alerts WHERE sku = '{TEST_SKU}';")
print(f"  SELECT stock_current, remaining_days_of_stock FROM inv.stock_levels WHERE sku = '{TEST_SKU}' AND store_id = '{TEST_STORE_ID}';")
