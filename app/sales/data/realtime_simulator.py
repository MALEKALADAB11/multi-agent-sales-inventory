"""
realtime_simulator.py — v2.1 PostgreSQL + Inventory API SKU Refresh
=====================================================================
Loads advisors and SKUs from PostgreSQL at startup.
Phase 2: after SKU_REFRESH_DELAY seconds, fetches live SKUs from the
inventory API (GET /api/inventory/skus?store_id=<store>) and rebuilds
the product pool — guaranteeing every on_sale call references a SKU
that exists in the live inventory snapshot → stock_delta carries a real
new_stock value → UI updates correctly.
Retries every SKU_REFRESH_RETRY seconds until the inventory pipeline is ready.
"""

import asyncio
import logging
import random
import urllib.request
import urllib.error
import json as _json
import uuid
from datetime import datetime
from typing import Callable, Optional

import asyncpg
from app.core.config import DEFAULT_STORE_ID
from app.sales.data.postgres_provider import PATTERN_HORAIRE, DEFAULT_TARGET

logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host": "localhost", "port": 5432,
    "database": "ooredoo_sales",
    "user": "postgres", "password": "admin",
}

_pg_pool: Optional[asyncpg.Pool] = None


async def _get_sim_pool() -> asyncpg.Pool:
    global _pg_pool
    if _pg_pool is None or _pg_pool._closed:
        _pg_pool = await asyncpg.create_pool(
            **DB_CONFIG, min_size=1, max_size=5, command_timeout=15, ssl=False,
        )
    return _pg_pool

INVENTORY_API_BASE = "http://localhost:8000/api/inventory"
SKU_REFRESH_DELAY  = 30    # seconds after start before first inventory API fetch
SKU_REFRESH_RETRY  = 30    # retry interval if API not ready yet

# ── Fallback SKUs — used if PostgreSQL AND inventory API are both unavailable ──
FALLBACK_SKUS = [
    {"sku": "8811364", "name": "Forfait Flexi 25 Go",        "price": 25},
    {"sku": "8811001", "name": "Paiement Facture Postpayé",  "price": 45},
    {"sku": "8812148", "name": "Forfait MIFI PRE 80 Go",     "price": 109},
    {"sku": "5021240", "name": "Xiaomi Redmi Note 15 8/256", "price": 899},
    {"sku": "8811365", "name": "Forfait Flexi 55 Go",        "price": 55},
    {"sku": "8811546", "name": "Forfait 8 Go",               "price": 18},
    {"sku": "8811458", "name": "Forfait 30 Go",              "price": 30},
    {"sku": "5021214", "name": "Redmi 15C 8/256",            "price": 549},
]

FALLBACK_ADVISORS = [
    {"id": "7296", "avg_ticket": 45, "ca_total": 0},
    {"id": "7451", "avg_ticket": 35, "ca_total": 0},
    {"id": "8987", "avg_ticket": 55, "ca_total": 0},
    {"id": "8988", "avg_ticket": 40, "ca_total": 0},
]


class RealtimeSimulator:
    """
    Simulates real-time POS transactions.

    Phase 1 (startup): loads advisors + SKUs from PostgreSQL.
                       Falls back to FALLBACK_* constants if PG is down.
    Phase 2 (≥30 s):   fetches live SKUs from the inventory API and
                       rebuilds the product pool so every on_sale() call
                       references a SKU present in the live inventory
                       snapshot. Retries every SKU_REFRESH_RETRY seconds
                       until the pipeline is ready, then refreshes hourly.
    """

    def __init__(self, store_id: str = DEFAULT_STORE_ID, interval: int = 15):
        self.store_id  = store_id
        self.interval  = interval
        self.on_sale:  Optional[Callable] = None
        self._running  = False
        self._task:    Optional[asyncio.Task] = None
        self._sku_task: Optional[asyncio.Task] = None
        self._advisors: list[dict] = []
        self._skus:     list[dict] = []
        self._tx_count  = 0
        self._skus_from_api = False  # True once phase-2 inventory API kicks in
        self._daily_target: float = DEFAULT_TARGET  # objectif CA du jour (sales.objectifs)

    # ── PostgreSQL loader ─────────────────────────────────────────────────────

    async def _load_from_postgres(self):
        """Load advisors and SKUs from PostgreSQL (phase 1)."""
        try:
            pool = await _get_sim_pool()
            conn = await pool.acquire()
            try:
                advisor_rows = await conn.fetch("""
                    SELECT
                        agent_id,
                        COUNT(*) AS nb_tx,
                        ROUND(AVG(lig_ttc), 2) AS avg_ticket,
                        ROUND(SUM(lig_ttc), 2) AS ca_total
                    FROM sales.transactions
                    WHERE store_id = $1
                      AND agent_id IS NOT NULL
                    GROUP BY agent_id
                    ORDER BY nb_tx DESC
                    LIMIT 10
                """, self.store_id)

                self._advisors = [
                    {
                        "id":         str(r["agent_id"]),
                        "avg_ticket": float(r["avg_ticket"] or 25),
                        "ca_total":   float(r["ca_total"] or 0),
                    }
                    for r in advisor_rows
                ] if advisor_rows else []

                sku_rows = await conn.fetch("""
                    SELECT
                        t.sku,
                        p.nom AS des_produit,
                        ROUND(AVG(t.lig_ttc), 2) AS avg_price,
                        COUNT(*) AS nb_ventes
                    FROM sales.transactions t
                    JOIN sales.produits p ON p.sku = t.sku AND p.nom IS NOT NULL
                    WHERE t.store_id = $1
                      AND t.sku IS NOT NULL
                      AND t.lig_ttc > 0
                    GROUP BY t.sku, p.nom
                    ORDER BY nb_ventes DESC
                    LIMIT 20
                """, self.store_id)

                self._skus = [
                    {
                        "sku":   str(r["sku"]),
                        "name":  str(r["des_produit"]),
                        "price": float(r["avg_price"] or 25),
                    }
                    for r in sku_rows
                ] if sku_rows else []

                target = await conn.fetchval("""
                    SELECT objectif_ca FROM sales.objectifs
                    WHERE store_id = $1 AND agent_id IS NULL AND date_objectif = $2
                """, self.store_id, datetime.now().date())
                if target:
                    self._daily_target = float(target)

                logger.info(
                    "[SIM PG] Loaded %d advisors and %d SKUs from PostgreSQL for %s "
                    "(objectif jour = %.0f DT)",
                    len(self._advisors), len(self._skus), self.store_id,
                    self._daily_target,
                )

            finally:
                await pool.release(conn)

        except Exception as e:
            logger.warning("[SIM PG] Load error: %s — using fallback data", e)
            self._advisors = list(FALLBACK_ADVISORS)
            self._skus     = list(FALLBACK_SKUS)

    # ── Inventory API SKU refresh (phase 2) ───────────────────────────────────

    async def _refresh_skus_loop(self):
        """
        Wait SKU_REFRESH_DELAY seconds, then fetch live SKUs from the inventory
        API. Retry every SKU_REFRESH_RETRY seconds until a non-empty list is
        returned (inventory pipeline may still be running on first attempt).
        Once we have a live list, refresh hourly to pick up stock changes.
        """
        await asyncio.sleep(SKU_REFRESH_DELAY)

        while self._running:
            try:
                skus = await asyncio.get_event_loop().run_in_executor(
                    None, self._fetch_skus_sync
                )
                if skus:
                    # Merge inventory SKU list with PG price data.
                    # L'API inventory ne renvoie que des SKUs : les noms sont
                    # résolus depuis sales.produits — jamais le SKU en libellé
                    # (sinon des_produit se corrompt en base, cf. migration 0003).
                    pg_price = {s["sku"]: s["price"] for s in self._skus}
                    pg_name  = {s["sku"]: s["name"] for s in self._skus}
                    names    = await self._resolve_product_names(
                        [s for s in skus if s not in pg_name]
                    )
                    pg_name.update(names)
                    self._skus = [
                        {
                            "sku":   sku,
                            "name":  pg_name.get(sku),
                            "price": pg_price.get(sku, 25.0),
                        }
                        for sku in skus
                        if pg_name.get(sku)  # SKU sans nom produit = exclu
                    ]
                    self._skus_from_api = True
                    logger.info(
                        "✅ SKU pool updated from inventory API — %d SKUs for %s (phase 2)",
                        len(self._skus), self.store_id,
                    )
                    await asyncio.sleep(3600)   # refresh hourly
                    continue

                logger.info(
                    "[SIM] Inventory API returned 0 SKUs — pipeline may still be running, "
                    "retrying in %ds", SKU_REFRESH_RETRY,
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("[SIM] SKU refresh error: %s", e)

            await asyncio.sleep(SKU_REFRESH_RETRY)

    async def _resolve_product_names(self, skus: list[str]) -> dict[str, str]:
        """Résout les noms produits depuis sales.produits pour des SKUs inconnus."""
        if not skus:
            return {}
        try:
            numeric = [int(s) for s in skus if str(s).isdigit()]
            if not numeric:
                return {}
            pool = await _get_sim_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT sku, nom FROM sales.produits WHERE sku = ANY($1)",
                    numeric,
                )
            return {str(r["sku"]): r["nom"] for r in rows if r["nom"]}
        except Exception as e:
            logger.warning("[SIM] résolution noms produits: %s", e)
            return {}

    def _fetch_skus_sync(self) -> list[str]:
        """Synchronous HTTP GET to /api/inventory/skus?store_id=<store>."""
        url = f"{INVENTORY_API_BASE}/skus?store_id={self.store_id}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = _json.loads(resp.read().decode())
                skus = body.get("skus", [])
                logger.debug("Inventory API returned %d SKUs for %s", len(skus), self.store_id)
                return skus
        except urllib.error.URLError as e:
            logger.warning("[SIM] Inventory API unreachable (%s) — will retry", e.reason)
            return []
        except Exception as e:
            logger.warning("[SIM] SKU fetch failed: %s", e)
            return []

    # ── Transaction loop ──────────────────────────────────────────────────────

    async def _simulate_loop(self):
        """Main simulation loop."""
        await self._load_from_postgres()

        logger.info(
            "RealtimeSimulator started — %d SKUs (phase 1, %s) | %d advisors | every %ds",
            len(self._skus),
            "PG" if self._skus else "fallback",
            len(self._advisors),
            self.interval,
        )

        while self._running:
            await asyncio.sleep(self.interval)
            if not self._running:
                break
            try:
                if await self._budget_allows_sale():
                    await self._generate_transaction()
            except Exception as e:
                logger.debug("[SIM] TX error: %s", e)

    # ── Budget pacing ─────────────────────────────────────────────────────────

    def _expected_ca_now(self) -> float:
        """
        CA cumulé attendu à cet instant : objectif journalier × pattern horaire
        réel du magasin, avec interpolation linéaire dans l'heure courante.
        """
        now = datetime.now()
        pct = sum(PATTERN_HORAIRE.get(h, 0) for h in range(0, now.hour))
        pct += PATTERN_HORAIRE.get(now.hour, 0) * (now.minute / 60)
        return self._daily_target * pct / 100

    async def _budget_allows_sale(self) -> bool:
        """
        Régule le rythme : une vente n'est émise que si le CA du jour
        (batch + temps réel) est en retard sur la courbe d'objectif.
        Sans cette porte, le simulateur écrasait l'objectif journalier
        (~1 000 DT) en moins d'une heure → attainment à 690 %.
        """
        try:
            pool = await _get_sim_pool()
            async with pool.acquire() as conn:
                # Heures écoulées uniquement : le batch synthétique sème des
                # ventes sur 24h, même règle que fetch_pos_data.
                ca_today = await conn.fetchval("""
                    SELECT COALESCE(SUM(lig_ttc), 0) FROM (
                        SELECT heure, lig_ttc FROM sales.transactions
                        WHERE store_id = $1 AND date_only = $2
                        UNION ALL
                        SELECT heure, lig_ttc FROM sales.transactions_rt
                        WHERE store_id = $1 AND date_only = $2
                    ) t WHERE heure <= $3
                """, self.store_id, datetime.now().date(), datetime.now().hour)
        except Exception as e:
            logger.debug("[SIM] budget check failed (%s) — sale skipped", e)
            return False

        gap = self._expected_ca_now() - float(ca_today or 0)
        # Petite tolérance stochastique pour ne pas coller exactement à la courbe
        return gap > 0 and random.random() < min(1.0, gap / 30)

    async def _generate_transaction(self):
        """Generate and persist one simulated transaction."""
        if not self._advisors or not self._skus:
            return

        advisor = random.choice(self._advisors)

        # Ticket cohérent avec l'échelle du magasin : on privilégie les SKUs
        # dont le prix reste dans le budget restant de l'heure (les terminaux
        # à 900 DT ne partent pas toutes les 15 minutes dans une boutique
        # qui fait ~1 000 DT/jour).
        affordable = [s for s in self._skus if s["price"] <= max(60.0, self._daily_target * 0.05)]
        sku = random.choice(affordable if affordable else self._skus)

        # Vary price ±20% around the mean
        price = round(sku["price"] * random.uniform(0.8, 1.2), 2)
        price = max(price, 1.0)

        self._tx_count += 1
        now = datetime.now()

        logger.info(
            "%s | TX [%s] — advisor=%s  sku=%s (%s)  %.0f DT  (total=%d)",
            now.strftime("%H:%M:%S"),
            "API" if self._skus_from_api else "PG/fallback",
            advisor["id"], sku["sku"], sku["name"],
            price, self._tx_count,
        )

        # Persist to sales.transactions_rt
        try:
            pool = await _get_sim_pool()
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO sales.transactions_rt
                        (sale_id, date_vente, date_only, heure,
                         store_id, agent_id, cod_prod, des_produit,
                         lig_ttc, lig_ht, lig_tva, qte_produit)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                    ON CONFLICT (sale_id) DO NOTHING
                """,
                    str(uuid.uuid4()),
                    now, now.date(), now.hour,
                    self.store_id,
                    int(advisor["id"]),
                    int(sku["sku"]),
                    sku["name"],
                    round(price, 2),
                    round(price / 1.19, 2),
                    round(price * 0.19 / 1.19, 2),
                    1,
                )
            logger.info("[RT TX] ✅ %s@%s %.0f TND", sku["sku"], self.store_id, price)
        except Exception as e:
            logger.warning("[RT TX] INSERT err: %s — %s", type(e).__name__, e)

        # Fire inventory callback
        if self.on_sale:
            try:
                self.on_sale(
                    self.store_id, sku["sku"], 1,
                    product_name=sku["name"], amount=price,
                )
            except Exception as e:
                logger.debug("[SIM] on_sale callback: %s", e)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running  = True
        self._task     = asyncio.create_task(self._simulate_loop())
        self._sku_task = asyncio.create_task(self._refresh_skus_loop())
        logger.info("[SIM] Started for %s", self.store_id)

    def stop(self):
        self._running = False
        for t in (self._task, self._sku_task):
            if t:
                t.cancel()
        logger.info("[SIM] Stopped")

    # ── Accessors ─────────────────────────────────────────────────────────────

    def get_tx_count(self) -> int:
        return self._tx_count

    def get_advisors(self) -> list:
        return self._advisors

    def get_skus(self) -> list:
        return self._skus
