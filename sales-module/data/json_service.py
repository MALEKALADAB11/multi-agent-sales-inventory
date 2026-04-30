import json
import os
from datetime import datetime, date
from typing import Any

MOCK_DIR = os.path.join(os.path.dirname(__file__), "mock")


def load(filename: str) -> Any:
    path = os.path.join(MOCK_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class JsonDataService:

    def __init__(self):
        # ── Charger stores.json avec fallback store.json ──────
        try:
            raw = load("stores.json")
            # Supporter {"stores": [...]} ou [...] ou {...}
            if isinstance(raw, dict) and "stores" in raw:
                self._store = raw["stores"][0] if raw["stores"] else {}
            elif isinstance(raw, list):
                self._store = raw[0] if raw else {}
            else:
                self._store = raw
        except FileNotFoundError:
            raw = load("store.json")
            if isinstance(raw, list):
                self._store = raw[0] if raw else {}
            else:
                self._store = raw

        self._advisors     = load("advisors.json")
        self._transactions = load("transactions.json")
        self._targets      = load("targets.json")
        self._context      = load("context.json")
        self._inventory    = load("inventory.json")

    # ── Helpers clés compatibles ──────────────────────────
    def _store_id(self) -> str:
        return (
            self._store.get("store_id")
            or self._store.get("id")
            or "OOR_LAC_01"
        )

    def _store_name(self) -> str:
        return (
            self._store.get("name")
            or self._store.get("store_name")
            or "Lac 2"
        )

    # ── Store ─────────────────────────────────────────────
    def get_store(self) -> dict:
        return self._store

    # ── Advisors ──────────────────────────────────────────
    def get_advisors(self) -> list:
        return self._advisors

    def get_advisor(self, advisor_id: str) -> dict | None:
        return next(
            (a for a in self._advisors if a["id"] == advisor_id),
            None
        )

    # ── Targets ───────────────────────────────────────────
    def get_targets(self) -> list:
        return self._targets

    def get_target(self, advisor_id: str) -> dict | None:
        return next(
            (t for t in self._targets if t["advisor_id"] == advisor_id),
            None
        )

    # ── Transactions ──────────────────────────────────────
    def get_transactions_today(self) -> list:
        now  = datetime.now()
        hour = now.hour
        return [
            t for t in self._transactions
            if t["hour"] <= hour
        ]

    def get_ca_by_advisor(self) -> dict:
        txs = self.get_transactions_today()
        result: dict[str, float] = {}
        for t in txs:
            aid = t["advisor_id"]
            result[aid] = result.get(aid, 0.0) + float(t["amount"])
        return result

    def get_ca_total(self) -> float:
        return sum(self.get_ca_by_advisor().values())

    def get_units_by_sku(self) -> dict:
        txs = self.get_transactions_today()
        result: dict[str, int] = {}
        for t in txs:
            sku = t["sku"]
            result[sku] = result.get(sku, 0) + int(t.get("units", 1))
        return result

    def get_hourly_ca(self) -> list:
        """CA par heure pour le chart."""
        hourly: dict[int, float] = {}
        for t in self._transactions:
            h = t["hour"]
            hourly[h] = hourly.get(h, 0.0) + float(t["amount"])

        now    = datetime.now().hour
        result = []
        for h in range(9, 21):
            result.append({
                "hour":   f"{h}h",
                "actual": round(hourly.get(h, 0), 2) if h <= now else None,
                "target": round(self.get_ca_total() / max(now - 8, 1), 2)
            })
        return result

    # ── Context ───────────────────────────────────────────
    def get_context(self) -> dict:
        return self._context

    # ── Inventory ─────────────────────────────────────────
    def get_inventory(self) -> list:
        return self._inventory

    def get_inventory_item(self, sku: str) -> dict | None:
        return next(
            (i for i in self._inventory if i["sku"] == sku),
            None
        )

    def get_alerts(self) -> list:
        alerts = []
        for item in self._inventory:
            if item.get("risk_level") == "critical":
                alerts.append({
                    "id":      f"alert-{item['sku']}",
                    "sku":     item["sku"],
                    "type":    "stockout",
                    "urgency": "critical",
                    "message": f"{item['name']} — {item['stock']} units remaining",
                    "action":  item.get("recommendation", ""),
                    "time":    item.get("last_updated", "")
                })
            elif item.get("risk_level") == "high":
                alerts.append({
                    "id":      f"alert-{item['sku']}",
                    "sku":     item["sku"],
                    "type":    "stockout",
                    "urgency": "high",
                    "message": f"{item['name']} — demand peak expected",
                    "action":  item.get("recommendation", ""),
                    "time":    item.get("last_updated", "")
                })
        return alerts

    # ── Métriques boutique ────────────────────────────────
    def get_store_metrics(self) -> dict:
        ca_total   = self.get_ca_total()
        ca_target  = sum(t["ca_target"] for t in self._targets)
        attainment = round((ca_total / ca_target * 100), 1) if ca_target else 0
        ctx        = self._context

        return {
            # ── Clés compatibles stores.json ET store.json ──
            "store_id":       self._store_id(),
            "name":           self._store_name(),
            "ca_today":       round(ca_total, 2),
            "ca_target":      ca_target,
            "attainment_pct": attainment,
            "visitors_h":     ctx.get("traffic_h", 0),
            "agents_live":    4,
            "context": {
                "weather":      ctx.get("weather", ""),
                "weather_icon": ctx.get("weather_icon", ""),
                "event":        ctx.get("event", ""),
                "stock_alert":  ctx.get("stock_alert", "")
            },
            "updated_at": datetime.utcnow().isoformat()
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
                )
            })

        result.sort(key=lambda x: x["performance"], reverse=True)
        return result

    def add_transaction(self, tx: dict) -> None:
        self._transactions.append(tx)

    def reset_day(self) -> None:
        """Remet les transactions à zéro — début de journée."""
        self._transactions = []

    def get_stats(self) -> dict:
        """Stats actuelles pour debug."""
        ca_map = self.get_ca_by_advisor()
        return {
            "total_transactions": len(self.get_transactions_today()),
            "ca_total":           round(self.get_ca_total(), 2),
            "ca_by_advisor":      {k: round(v, 2) for k, v in ca_map.items()}
        }