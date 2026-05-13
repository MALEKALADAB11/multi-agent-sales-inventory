"""
json_service.py — data layer for the sales module.

Real data sources (loaded at startup, fall back to mock JSON if unavailable):
  • Advisors / CA    — raw transaction CSV (AGENT_ID, AGENT_NAME, AGENT_SURNAME, LIG_TTC)
                       from  sales-module/data/transaction_vente_test_100500_fast.csv
                       or    shared_module/data/raw/transaction_vente.csv
  • Targets          — derived from the same CSV (30-day avg × 1.10)
  • Store            — boutique_actif from shared_module or mock/stores.json
  • Context/Inventory/Transactions — mock JSON (unchanged)

If no real data is found the service silently falls back to the existing
mock JSON files so nothing breaks during development without the CSV.
"""

import csv
import json
import logging
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MOCK_DIR = os.path.join(os.path.dirname(__file__), "mock")

# Target uplift over historical average
TARGET_UPLIFT = 1.10

# Default store when no real data is available
DEFAULT_STORE_ID = "I63"


# ── JSON mock helpers ─────────────────────────────────────────────────────────

def _load_mock(filename: str) -> Any:
    path = os.path.join(MOCK_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Real data loaders ─────────────────────────────────────────────────────────

def _find_transaction_csv() -> Path | None:
    candidates = [
        Path(__file__).parent / "transaction_vente_test_100500_fast.csv",
        Path(__file__).parent.parent.parent / "shared_module" / "data" / "raw" / "transaction_vente.csv",
        Path("shared_module") / "data" / "raw" / "transaction_vente.csv",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _load_real_advisors(store_id: str) -> tuple[list[dict], list[dict]]:
    """
    Read the raw transaction CSV and return (advisors, targets) for store_id.

    advisors: list of dicts compatible with mock advisors.json schema
    targets:  list of dicts compatible with mock targets.json schema

    Returns ([], []) if the CSV is unavailable.
    """
    path = _find_transaction_csv()
    if not path:
        return [], []

    agent_revenue: dict[str, float] = defaultdict(float)
    agent_names:   dict[str, tuple[str, str]] = {}   # id → (first, last)

    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader  = csv.DictReader(f)
            headers = reader.fieldnames or []

            if "AGENT_ID" not in headers:
                logger.warning("Transaction CSV has no AGENT_ID column — skipping")
                return [], []

            for row in reader:
                if (row.get("CODE_CENTRE") or "").strip() != store_id:
                    continue
                aid = (row.get("AGENT_ID") or "").strip()
                if not aid:
                    continue
                try:
                    amt = float(row.get("LIG_TTC") or row.get("MONTANT_PAIE") or 0)
                except ValueError:
                    amt = 0.0
                agent_revenue[aid] += amt
                if aid not in agent_names:
                    first = (row.get("AGENT_NAME")    or "").strip()
                    last  = (row.get("AGENT_SURNAME") or "").strip()
                    agent_names[aid] = (first, last)

    except Exception as e:
        logger.warning("Could not read real advisor data: %s", e)
        return [], []

    if not agent_revenue:
        logger.warning("No transactions found for store %s in %s", store_id, path.name)
        return [], []

    logger.info(
        "Loaded %d real advisors for %s from %s (total CA %.0f DT)",
        len(agent_revenue), store_id, path.name, sum(agent_revenue.values())
    )

    # Assign display colours round-robin
    COLOURS = ["#6C5CE7", "#00B894", "#F9A825", "#2D9CDB",
               "#E74C3C", "#A29BFE", "#FDCB6E", "#74B9FF"]

    # Sort by revenue descending
    sorted_agents = sorted(agent_revenue.items(), key=lambda x: -x[1])

    # Estimate daily average (CSV covers multiple days — divide by 30 as proxy)
    # A proper date-range calculation can replace this once DATE_VENTE parsing is added.
    DAYS_ESTIMATE = 30

    advisors = []
    targets  = []

    for i, (aid, total_rev) in enumerate(sorted_agents):
        first, last = agent_names.get(aid, ("", ""))
        name     = f"{first} {last}".strip() or aid
        initials = ((first[:1] + last[:1]) if first or last else aid[:2]).upper()
        daily_avg  = total_rev / DAYS_ESTIMATE
        ca_target  = round(daily_avg * TARGET_UPLIFT, 2)

        advisors.append({
            "id":           aid,
            "advisor_code": aid,
            "name":         name,
            "initials":     initials,
            "role":         _guess_role(i),
            "avatar_color": COLOURS[i % len(COLOURS)],
            "ca_target":    ca_target,
            "coach_score":  round(0.5 + (0.4 * total_rev / (sorted_agents[0][1] or 1)), 2),
        })
        targets.append({
            "advisor_id": aid,
            "ca_target":  ca_target,
        })

    return advisors, targets


def _guess_role(rank: int) -> str:
    """Assign a plausible role label based on revenue rank."""
    roles = [
        "Forfaits & Services",
        "Postpayé & Terminaux",
        "Smartphones & Data",
        "Recharge & Accessoires",
    ]
    return roles[rank % len(roles)]


# ── Service ───────────────────────────────────────────────────────────────────

class JsonDataService:

    def __init__(self, store_id: str = DEFAULT_STORE_ID):

        # ── Store ─────────────────────────────────────────────────────────────
        try:
            raw = _load_mock("stores.json")
            if isinstance(raw, dict) and "stores" in raw:
                self._store = raw["stores"][0] if raw["stores"] else {}
            elif isinstance(raw, list):
                self._store = raw[0] if raw else {}
            else:
                self._store = raw
        except FileNotFoundError:
            try:
                raw = _load_mock("store.json")
                self._store = raw[0] if isinstance(raw, list) and raw else raw
            except FileNotFoundError:
                self._store = {"id": store_id, "name": store_id}

        # Resolve the real store_id to use for CSV filtering
        self._resolved_store_id = (
            self._store.get("store_id")
            or self._store.get("id")
            or store_id
        )

        # ── Advisors & targets — prefer real CSV ──────────────────────────────
        real_advisors, real_targets = _load_real_advisors(self._resolved_store_id)

        if real_advisors:
            self._advisors = real_advisors
            self._targets  = real_targets
            self._using_real_advisors = True
        else:
            logger.warning(
                "Real advisor data unavailable — falling back to mock advisors.json"
            )
            self._advisors = _load_mock("advisors.json")
            self._targets  = _load_mock("targets.json")
            self._using_real_advisors = False

        # ── Transactions — start from mock, simulator appends in real time ────
        try:
            self._transactions = _load_mock("transactions.json")
        except FileNotFoundError:
            self._transactions = []

        # ── Context & inventory — always from mock ────────────────────────────
        try:
            self._context = _load_mock("context.json")
        except FileNotFoundError:
            self._context = {}

        try:
            self._inventory = _load_mock("inventory.json")
        except FileNotFoundError:
            self._inventory = []

        logger.info(
            "JsonDataService ready — store=%s | advisors=%s (%d) | mock_txs=%d",
            self._resolved_store_id,
            "REAL" if self._using_real_advisors else "MOCK",
            len(self._advisors),
            len(self._transactions),
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _store_id(self) -> str:
        return (
            self._store.get("store_id")
            or self._store.get("id")
            or DEFAULT_STORE_ID
        )

    def _store_name(self) -> str:
        return self._store.get("name") or self._store.get("store_name") or "Boutique"

    # ── Store ─────────────────────────────────────────────────────────────────

    def get_store(self) -> dict:
        return self._store

    # ── Advisors ──────────────────────────────────────────────────────────────

    def get_advisors(self) -> list:
        return self._advisors

    def get_advisor(self, advisor_id: str) -> dict | None:
        return next((a for a in self._advisors if a["id"] == advisor_id), None)

    # ── Targets ───────────────────────────────────────────────────────────────

    def get_targets(self) -> list:
        return self._targets

    def get_target(self, advisor_id: str) -> dict | None:
        return next((t for t in self._targets if t["advisor_id"] == advisor_id), None)

    # ── Transactions ──────────────────────────────────────────────────────────

    def get_transactions_today(self) -> list:
        hour = datetime.now().hour
        return [t for t in self._transactions if t["hour"] <= hour]

    def get_ca_by_advisor(self) -> dict:
        result: dict[str, float] = {}
        for t in self.get_transactions_today():
            aid = t["advisor_id"]
            result[aid] = result.get(aid, 0.0) + float(t["amount"])
        return result

    def get_ca_total(self) -> float:
        return sum(self.get_ca_by_advisor().values())

    def get_units_by_sku(self) -> dict:
        result: dict[str, int] = {}
        for t in self.get_transactions_today():
            sku = t["sku"]
            result[sku] = result.get(sku, 0) + int(t.get("units", 1))
        return result

    def get_hourly_ca(self) -> list:
        hourly: dict[int, float] = {}
        for t in self._transactions:
            h = t["hour"]
            hourly[h] = hourly.get(h, 0.0) + float(t["amount"])

        now    = datetime.now().hour
        target_h = round(self.get_ca_total() / max(now - 8, 1), 2)
        return [
            {
                "hour":   f"{h}h",
                "actual": round(hourly.get(h, 0), 2) if h <= now else None,
                "target": target_h,
            }
            for h in range(9, 21)
        ]

    # ── Context ───────────────────────────────────────────────────────────────

    def get_context(self) -> dict:
        return self._context

    # ── Inventory ─────────────────────────────────────────────────────────────

    def get_inventory(self) -> list:
        return self._inventory

    def get_inventory_item(self, sku: str) -> dict | None:
        return next((i for i in self._inventory if i["sku"] == sku), None)

    def get_alerts(self) -> list:
        alerts = []
        for item in self._inventory:
            level = item.get("risk_level", "")
            if level in ("critical", "high"):
                alerts.append({
                    "id":      f"alert-{item['sku']}",
                    "sku":     item["sku"],
                    "type":    "stockout",
                    "urgency": level,
                    "message": (
                        f"{item['name']} — {item['stock']} units remaining"
                        if level == "critical"
                        else f"{item['name']} — demand peak expected"
                    ),
                    "action": item.get("recommendation", ""),
                    "time":   item.get("last_updated", ""),
                })
        return alerts

    # ── Store metrics ─────────────────────────────────────────────────────────

    def get_store_metrics(self, store_id: str = None) -> dict:
        ca_total   = self.get_ca_total()
        ca_target  = sum(t["ca_target"] for t in self._targets)
        attainment = round((ca_total / ca_target * 100), 1) if ca_target else 0
        ctx        = self._context

        return {
            "store_id":       self._store_id(),
            "name":           self._store_name(),
            "ca_today":       round(ca_total, 2),
            "ca_target":      ca_target,
            "attainment_pct": attainment,
            "visitors_h":     ctx.get("traffic_h", 0),
            "agents_live":    len(self._advisors),
            "context": {
                "weather":      ctx.get("weather", ""),
                "weather_icon": ctx.get("weather_icon", ""),
                "event":        ctx.get("event", ""),
                "stock_alert":  ctx.get("stock_alert", ""),
            },
            "updated_at": datetime.utcnow().isoformat(),
        }

    def get_advisors_performance(self) -> list:
        ca_map = self.get_ca_by_advisor()
        result = []
        for adv in self._advisors:
            aid       = adv["id"]
            ca        = round(ca_map.get(aid, 0.0), 2)
            ca_target = adv.get("ca_target", 0)
            perf      = round((ca / ca_target * 100), 1) if ca_target else 0
            status    = "top" if perf >= 80 else "ok" if perf >= 50 else "urgent"

            result.append({
                "id":            aid,
                "advisor_code":  adv.get("advisor_code", aid),
                "name":          adv.get("name", ""),
                "initials":      adv.get("initials", ""),
                "role":          adv.get("role", ""),
                "avatar_color":  adv.get("avatar_color", "#2D9CDB"),
                "ca_realized":   ca,
                "ca_target":     ca_target,
                "performance":   perf,
                "coach_score":   adv.get("coach_score", 0.0),
                "status":        status,
                "prevision_eod": round(
                    ca * (20 / max(datetime.now().hour - 8, 1)), 2
                ),
            })

        result.sort(key=lambda x: x["performance"], reverse=True)
        return result

    # ── Mutations ─────────────────────────────────────────────────────────────

    def add_transaction(self, tx: dict) -> None:
        self._transactions.append(tx)

    def reset_day(self) -> None:
        self._transactions = []

    # ── Debug ─────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        ca_map = self.get_ca_by_advisor()
        return {
            "total_transactions":  len(self.get_transactions_today()),
            "ca_total":            round(self.get_ca_total(), 2),
            "ca_by_advisor":       {k: round(v, 2) for k, v in ca_map.items()},
            "using_real_advisors": self._using_real_advisors,
        }