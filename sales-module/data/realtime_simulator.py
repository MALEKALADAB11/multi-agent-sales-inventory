"""
realtime_simulator.py — injects one POS transaction every N seconds.

SKU selection:
  Phase 1 (startup)   — HARDCODED_SKUS while waiting for inventory pipeline
  Phase 2 (≥30s in)   — fetches GET /api/inventory/skus?store_id=<store>
                         and rebuilds product pool from ONLY those SKUs,
                         enriched with names/prices from product_master.csv.
                         Retries every 30 s until it gets a non-empty list.

This guarantees every on_sale call references a SKU that exists in the live
inventory snapshot → backend returns a real new_stock → stock_delta patches
the item → quadrant blinks → KPI counts update.

Advisor selection:
  Tries to load real AGENT_IDs from the raw transaction CSV.
  Falls back to HARDCODED_ADVISORS.
"""

import asyncio
import csv
import logging
import random
import urllib.request
import urllib.error
import json as _json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

INVENTORY_API_BASE = "http://localhost:8000/api/inventory"
SKU_REFRESH_DELAY  = 30       # seconds after start before first API fetch
SKU_REFRESH_RETRY  = 30       # retry interval if API not ready yet


# ── File helpers ──────────────────────────────────────────────────────────────

def _candidate_paths(relative: str) -> list[Path]:
    base = Path(__file__).parent
    return [
        base.parent.parent / "inventory-module" / "data" / "processed" / relative,
        base.parent.parent / "shared_module"    / "data" / "processed" / relative,
        Path("inventory-module") / "data" / "processed" / relative,
    ]


# ── Fallback SKUs — confirmed present in I63 snapshot from previous logs ──────
HARDCODED_SKUS = [
    {"sku": "8811001", "name": "Paiement Facture Postpayé",  "category": "Postpayé",       "amount": 45.0,  "weight": 0.18},
    {"sku": "8811364", "name": "Forfait Flexi 25 Go",        "category": "Forfait Mobile", "amount": 25.0,  "weight": 0.15},
    {"sku": "8811458", "name": "Forfait 30 Go",              "category": "Forfait Mobile", "amount": 30.0,  "weight": 0.08},
    {"sku": "8811546", "name": "Forfait 8 Go",               "category": "Forfait Mobile", "amount": 18.0,  "weight": 0.09},
    {"sku": "8811365", "name": "Forfait Flexi 55 Go",        "category": "Forfait Mobile", "amount": 55.0,  "weight": 0.06},
    {"sku": "8812148", "name": "Forfait MIFI PRE 80 Go",     "category": "Box / Fibre",    "amount": 109.0, "weight": 0.04},
    {"sku": "5021240", "name": "Xiaomi Redmi Note 15 8/256", "category": "Terminal",       "amount": 899.0, "weight": 0.04},
    {"sku": "5021214", "name": "Redmi 15C 8/256",            "category": "Terminal",       "amount": 549.0, "weight": 0.035},
]

HARDCODED_ADVISORS = [
    {"id": "adv-kb", "weight": 0.35},
    {"id": "adv-sm", "weight": 0.28},
    {"id": "adv-at", "weight": 0.22},
    {"id": "adv-lk", "weight": 0.15},
]


# ── Product-master lookup ─────────────────────────────────────────────────────

def _load_product_master() -> dict[str, dict]:
    """Returns {sku: {name, category, amount}} from product_master.csv."""
    for path in _candidate_paths("product_master.csv"):
        if not path.exists():
            continue
        result: dict[str, dict] = {}
        try:
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    sku = (row.get("sku") or "").strip()
                    if not sku:
                        continue
                    try:
                        price = float(row.get("unit_price") or 0)
                    except ValueError:
                        price = 0.0
                    result[sku] = {
                        "name":     (row.get("product_name") or sku).strip(),
                        "category": (row.get("category")     or "Unknown").strip(),
                        "amount":   price,
                    }
            logger.debug("product_master: %d SKUs", len(result))
            return result
        except Exception as e:
            logger.warning("product_master read error: %s", e)
    return {}


def _weight_for_category(category: str) -> float:
    cat = category.lower()
    if   "forfait" in cat or "mobile" in cat:              return 0.08
    elif "postpay" in cat or "facture" in cat:             return 0.10
    elif "recharge" in cat:                                return 0.06
    elif "sim" in cat or "ligne" in cat:                   return 0.04
    elif "terminal" in cat or "portable" in cat:           return 0.03
    elif "box" in cat or "fibre" in cat or "routeur" in cat: return 0.015
    else:                                                  return 0.01


def _build_products_from_skus(
    skus: list[str],
    master: dict[str, dict],
) -> list[dict]:
    """Turn a list of SKUs + master info into a weighted product list."""
    products = []
    for sku in skus:
        info     = master.get(sku, {})
        name     = info.get("name",     sku)
        category = info.get("category", "Unknown")
        amount   = info.get("amount",   0.0)
        products.append({
            "sku": sku, "name": name, "category": category,
            "amount": amount,
            "weight": _weight_for_category(category),
        })
    if products:
        total = sum(p["weight"] for p in products) or 1.0
        for p in products:
            p["weight"] /= total
    return products


# ── Advisor loader ────────────────────────────────────────────────────────────

def _load_advisors(store_id: str) -> list[dict]:
    raw_paths = [
        Path(__file__).parent / "transaction_vente_test_100500_fast.csv",
        Path(__file__).parent.parent.parent / "shared_module" / "data" / "raw" / "transaction_vente.csv",
    ]
    agent_revenue: dict[str, float] = defaultdict(float)
    agent_names:   dict[str, str]   = {}

    for path in raw_paths:
        if not path.exists():
            continue
        try:
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                if "AGENT_ID" not in (reader.fieldnames or []):
                    continue
                for row in reader:
                    if (row.get("CODE_CENTRE") or "").strip() != store_id:
                        continue
                    aid = (row.get("AGENT_ID") or "").strip()
                    if not aid:
                        continue
                    try:
                        amt = float(row.get("LIG_TTC") or 0)
                    except ValueError:
                        amt = 0.0
                    agent_revenue[aid] += amt
                    if aid not in agent_names:
                        first = (row.get("AGENT_NAME")    or "").strip()
                        last  = (row.get("AGENT_SURNAME") or "").strip()
                        agent_names[aid] = f"{first} {last}".strip() or aid
            if agent_revenue:
                logger.info("Loaded %d real advisors from %s", len(agent_revenue), path.name)
                break
        except Exception as e:
            logger.warning("Advisor load error: %s", e)

    if not agent_revenue:
        return []

    total = sum(agent_revenue.values()) or 1.0
    return [
        {"id": aid, "name": agent_names.get(aid, aid), "weight": rev / total}
        for aid, rev in sorted(agent_revenue.items(), key=lambda x: -x[1])
    ][:10]


# ── Simulator ─────────────────────────────────────────────────────────────────

class RealtimeSimulator:
    """
    Phase 1: fires transactions using HARDCODED_SKUS (safe baseline).
    Phase 2: after SKU_REFRESH_DELAY seconds, queries the inventory API for
             the exact list of active SKUs for the store, then rebuilds the
             product pool. Retries until the pipeline is ready.

    This guarantees every on_sale(store_id, sku, 1) call hits a SKU that
    exists in the live inventory snapshot, so stock_delta always carries a
    real new_stock value and the UI updates correctly.
    """

    def __init__(self, json_svc, interval_seconds: int = 15, store_id: str = "I63"):
        self.json_svc  = json_svc
        self.interval  = interval_seconds
        self.running   = False
        self.task      = None
        self.sku_task  = None
        self.total_injected = 0
        self._store_id = store_id

        # Pre-load product master once — cheap, avoids repeated CSV reads
        self._master = _load_product_master()

        # Start with hardcoded safe SKUs
        _total = sum(p["weight"] for p in HARDCODED_SKUS)
        self._products = [{**p, "weight": p["weight"] / _total} for p in HARDCODED_SKUS]
        self._weights  = [p["weight"] for p in self._products]
        self._skus_from_api = False

        # Advisors
        advisors = _load_advisors(store_id)
        if not advisors:
            logger.warning("Real advisors unavailable — using synthetic IDs")
            advisors = HARDCODED_ADVISORS
        self._advisors        = advisors
        self._advisor_weights = [a["weight"] for a in advisors]

        # Wired in main.py → InventoryDataCache.apply_sale
        self.on_sale = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        if not self.running:
            self.running  = True
            self.task     = asyncio.create_task(self._loop())
            self.sku_task = asyncio.create_task(self._refresh_skus_loop())
            logger.info(
                "RealtimeSimulator started — %d SKUs (phase 1) | %d advisors | every %ds",
                len(self._products), len(self._advisors), self.interval,
            )

    def stop(self):
        self.running = False
        for t in (self.task, self.sku_task):
            if t:
                t.cancel()
        logger.info("RealtimeSimulator stopped")

    # ── Transaction loop ──────────────────────────────────────────────────────

    async def _loop(self):
        while self.running:
            try:
                await asyncio.sleep(self.interval)
                self._inject_transaction()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Simulator error: %s", e)

    def _inject_transaction(self):
        adv     = random.choices(self._advisors, weights=self._advisor_weights)[0]
        product = random.choices(self._products, weights=self._weights)[0]

        tx = {
            "advisor_id":   adv["id"],
            "sku":          product["sku"],
            "product_name": product["name"],
            "category":     product["category"],
            "amount":       product["amount"],
            "units":        1,
            "hour":         datetime.now().hour,
        }

        self.json_svc.add_transaction(tx)
        self.total_injected += 1

        logger.info(
            "%s | TX [%s] — advisor=%s  sku=%s (%s)  %.0f DT  (total=%d)",
            datetime.now().strftime("%H:%M:%S"),
            "API" if self._skus_from_api else "fallback",
            adv["id"], product["sku"], product["name"],
            product["amount"], self.total_injected,
        )

        if self.on_sale:
            try:
                store_id = self.json_svc.get_store().get("id", self._store_id)
                self.on_sale(store_id, product["sku"], tx["units"])
            except Exception as e:
                logger.warning("on_sale callback error: %s", e)

    # ── SKU refresh loop ──────────────────────────────────────────────────────

    async def _refresh_skus_loop(self):
        """
        Wait SKU_REFRESH_DELAY seconds, then fetch live SKUs from the inventory
        API. Retry every SKU_REFRESH_RETRY seconds until a non-empty list is
        returned (inventory pipeline may still be running on first attempt).
        Once we have a live list, refresh once per hour to pick up stock changes.
        """
        await asyncio.sleep(SKU_REFRESH_DELAY)

        while self.running:
            try:
                skus = await asyncio.get_event_loop().run_in_executor(
                    None, self._fetch_skus_sync
                )
                if skus:
                    products = _build_products_from_skus(skus, self._master)
                    if products:
                        self._products        = products
                        self._weights         = [p["weight"] for p in products]
                        self._skus_from_api   = True
                        logger.info(
                            "✅ SKU pool updated from API — %d SKUs for %s (phase 2)",
                            len(products), self._store_id,
                        )
                        # Refresh hourly to stay in sync
                        await asyncio.sleep(3600)
                        continue

                logger.info(
                    "Inventory API returned 0 SKUs — pipeline may still be running, "
                    "retrying in %ds", SKU_REFRESH_RETRY
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("SKU refresh error: %s", e)

            await asyncio.sleep(SKU_REFRESH_RETRY)

    def _fetch_skus_sync(self) -> list[str]:
        """Synchronous HTTP GET to /api/inventory/skus?store_id=<store>."""
        url = f"{INVENTORY_API_BASE}/skus?store_id={self._store_id}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = _json.loads(resp.read().decode())
                skus = body.get("skus", [])
                logger.debug("Inventory API returned %d SKUs for %s", len(skus), self._store_id)
                return skus
        except urllib.error.URLError as e:
            logger.warning("Inventory API unreachable (%s) — will retry", e.reason)
            return []
        except Exception as e:
            logger.warning("SKU fetch failed: %s", e)
            return []