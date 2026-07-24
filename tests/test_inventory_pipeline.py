"""
test_inventory_pipeline.py
===========================
Stand-alone backend tests that verify the inventory pipeline is working
WITHOUT starting the HTTP server. Run with:
    python scripts/test_inventory_pipeline.py
"""
import sys
import time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

print("=" * 60)
print("INVENTORY PIPELINE BACKEND TESTS")
print("=" * 60)

# ── Setup ──────────────────────────────────────────────────────────────────
import os
from pathlib import Path

# Add project root to sys.path for app imports
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# Load .env
_env = Path(__file__).resolve().parent.parent / ".env"
if _env.exists():
    from dotenv import load_dotenv
    load_dotenv(_env, override=False)
    print(f"[OK] Loaded .env from {_env}")

DEFAULT_STORE_ID = os.getenv("DEFAULT_STORE_ID", "I14")
print(f"[OK] DEFAULT_STORE_ID = {DEFAULT_STORE_ID}\n")

PASS = 0
FAIL = 0
RESULTS = []

def ok(name, detail=""):
    global PASS
    PASS += 1
    RESULTS.append(("OK", name, detail))
    print(f"  [OK]  {name}" + (f" — {detail}" if detail else ""))

def fail(name, detail=""):
    global FAIL
    FAIL += 1
    RESULTS.append(("FAIL", name, detail))
    print(f"  [FAIL] {name} — {detail}")


# ══════════════════════════════════════════════════════════════════
# TEST 1: DB connectivity & I14 data presence
# ══════════════════════════════════════════════════════════════════
print("\n[1/6] Database data checks")
try:
    import psycopg2
    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "ooredoo_sales"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "root"),
    )
    cur = conn.cursor()

    checks = [
        (f"inventory.stock_levels   store={DEFAULT_STORE_ID}", f"SELECT COUNT(*) FROM inventory.stock_levels WHERE store_id='{DEFAULT_STORE_ID}'", 1),
        (f"inventory.stock_history  store={DEFAULT_STORE_ID}", f"SELECT COUNT(*) FROM inventory.stock_history WHERE store_id='{DEFAULT_STORE_ID}'", 1),
        (f"inventory.sales_history  store={DEFAULT_STORE_ID}", f"SELECT COUNT(*) FROM inventory.sales_history WHERE store_id='{DEFAULT_STORE_ID}'", 1),
        (f"inventory.demand_forecast store={DEFAULT_STORE_ID}", f"SELECT COUNT(*) FROM inventory.demand_forecast WHERE store_id='{DEFAULT_STORE_ID}'", 1),
        ("inventory.products (global)", "SELECT COUNT(*) FROM inventory.products", 1),
        ("inventory.recommendations  store=" + DEFAULT_STORE_ID, f"SELECT COUNT(*) FROM inventory.recommendations WHERE store_id='{DEFAULT_STORE_ID}'", 0),
    ]

    for label, sql, min_count in checks:
        try:
            cur.execute(sql)
            n = cur.fetchone()[0]
            if n >= min_count:
                ok(label, f"count={n}")
            else:
                fail(label, f"count={n}, expected >= {min_count}")
        except Exception as e:
            conn.rollback()
            fail(label, str(e))

    conn.close()
except Exception as e:
    fail("DB connection", str(e))


# ══════════════════════════════════════════════════════════════════
# TEST 2: DataCache loads from Postgres
# ══════════════════════════════════════════════════════════════════
print("\n[2/6] _DataCache loads (stock, sales, product, forecast)")
try:
    from app.inventory.tools.internal.stock_tools import _DataCache

    t0 = time.time()
    stock_df = _DataCache.stock()
    t_stock = time.time() - t0
    if not stock_df.empty and "sku" in stock_df.columns:
        ok("DataCache.stock()", f"{len(stock_df)} rows in {t_stock:.2f}s")
    else:
        fail("DataCache.stock()", "empty or missing 'sku' column")

    t0 = time.time()
    sales_df = _DataCache.sales()
    t_sales = time.time() - t0
    if not sales_df.empty and "sku" in sales_df.columns:
        ok("DataCache.sales()", f"{len(sales_df)} rows in {t_sales:.2f}s")
    else:
        fail("DataCache.sales()", "empty or missing 'sku' column")

    t0 = time.time()
    prod_df = _DataCache.product()
    t_prod = time.time() - t0
    if not prod_df.empty:
        ok("DataCache.product()", f"{len(prod_df)} rows in {t_prod:.2f}s")
    else:
        fail("DataCache.product()", "empty")

    t0 = time.time()
    fc_df = _DataCache.forecast()
    t_fc = time.time() - t0
    ok("DataCache.forecast()", f"{len(fc_df)} rows in {t_fc:.2f}s")

except Exception as e:
    fail("DataCache imports", str(e))


# ══════════════════════════════════════════════════════════════════
# TEST 3: SKU resolution for DEFAULT_STORE_ID
# ══════════════════════════════════════════════════════════════════
print(f"\n[3/6] SKU resolution for store {DEFAULT_STORE_ID}")
skus = []
try:
    from app.inventory.api.routes import _resolve_skus_for_store
    t0 = time.time()
    skus = _resolve_skus_for_store(DEFAULT_STORE_ID)
    elapsed = time.time() - t0
    if skus:
        ok("_resolve_skus_for_store", f"{len(skus)} SKUs resolved in {elapsed:.2f}s")
        print(f"     Sample SKUs: {skus[:5]}")
    else:
        fail("_resolve_skus_for_store", f"0 SKUs — store {DEFAULT_STORE_ID} has no stock or sales data")
except Exception as e:
    fail("_resolve_skus_for_store", str(e))


# ══════════════════════════════════════════════════════════════════
# TEST 4: Single-SKU pipeline timing (fast orchestrator)
# ══════════════════════════════════════════════════════════════════
print(f"\n[4/6] Single-SKU pipeline timing (fast=True orchestrator)")
if skus:
    test_sku = skus[0]
    try:
        from app.inventory.api.routes import get_orchestrator_fast
        orch = get_orchestrator_fast()

        t0 = time.time()
        results = orch.analyze_batch([test_sku], DEFAULT_STORE_ID, "balanced")
        elapsed = time.time() - t0

        if results and len(results) > 0:
            r = results[0]
            if "error" in r:
                fail(f"Pipeline SKU {test_sku}", f"pipeline error: {r['error']}")
            else:
                analysis = r.get("analysis_report", {})
                decision = r.get("decision_result", {})
                risk = analysis.get("risk_assessment", {}).get("level", "UNKNOWN")
                action = decision.get("decision", {}).get("action", "UNKNOWN") if isinstance(decision, dict) else "UNKNOWN"
                ok(f"Single-SKU pipeline {test_sku}", f"{elapsed:.2f}s | risk={risk} | action={action}")
        else:
            fail(f"Pipeline SKU {test_sku}", "empty result list")
    except Exception as e:
        fail(f"Pipeline SKU {test_sku}", str(e))
else:
    fail("Single-SKU pipeline", "skipped — no SKUs resolved")


# ══════════════════════════════════════════════════════════════════
# TEST 5: Mini-batch (5 SKUs) timing
# ══════════════════════════════════════════════════════════════════
print(f"\n[5/6] Mini-batch timing (5 SKUs, fast=True)")
if skus:
    batch = skus[:5]
    try:
        from app.inventory.api.routes import get_orchestrator_fast
        orch = get_orchestrator_fast()

        t0 = time.time()
        results = orch.analyze_batch(batch, DEFAULT_STORE_ID, "balanced")
        elapsed = time.time() - t0

        errors = sum(1 for r in results if "error" in r)
        actions = [r.get("decision_result", {}).get("decision", {}).get("action", "?")
                   if isinstance(r.get("decision_result"), dict) else "?"
                   for r in results]

        if errors == len(batch):
            fail("Mini-batch 5 SKUs", f"all {errors} SKUs errored")
        else:
            ok("Mini-batch 5 SKUs",
               f"{elapsed:.2f}s ({elapsed/5:.2f}s/SKU avg) | {errors} errors | actions={actions}")

        # Project to full store
        ms_per_sku = elapsed / 5
        print(f"     Projected full store ({len(skus)} SKUs): {ms_per_sku * len(skus):.0f}s")

    except Exception as e:
        fail("Mini-batch 5 SKUs", str(e))
else:
    fail("Mini-batch 5 SKUs", "skipped — no SKUs resolved")


# ══════════════════════════════════════════════════════════════════
# TEST 6: Inventory recommendations schema
# ══════════════════════════════════════════════════════════════════
print(f"\n[6/6] Recommendations structure from pipeline result")
if skus:
    try:
        from app.inventory.api.routes import get_orchestrator_fast, _to_inventory_item
        orch = get_orchestrator_fast()
        results = orch.analyze_batch([skus[0]], DEFAULT_STORE_ID, "balanced")
        if results:
            r = results[0]
            item = _to_inventory_item(r, preloaded_stock=None, product_lookup=None)
            required_keys = ["sku", "stock", "daysOfStock", "riskLevel", "recommendation"]
            missing = [k for k in required_keys if k not in item]
            if missing:
                fail("_to_inventory_item structure", f"missing keys: {missing}")
            else:
                ok("_to_inventory_item structure",
                   f"sku={item.get('sku')} risk={item.get('riskLevel')} "
                   f"recommendation={item.get('recommendation')} "
                   f"stock={item.get('stock')}")
    except Exception as e:
        fail("Recommendations structure", str(e))
else:
    fail("Recommendations structure", "skipped")


# ══════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print(f"RESULTS: {PASS} OK, {FAIL} FAIL")
print("=" * 60)
if FAIL:
    for status, name, detail in RESULTS:
        if status == "FAIL":
            print(f"  FAIL  {name} — {detail}")
    sys.exit(1)
else:
    print("All tests passed!")
    sys.exit(0)