"""
realtime_simulator.py — simulateur de ventes multi-boutiques, 100 % dynamique.

RIEN N'EST CODÉ EN DUR
──────────────────────
Ni boutique, ni SKU, ni prix, ni conseiller, ni profil horaire. Tout est
découvert en base au démarrage puis rafraîchi :

  boutiques      `sales.boutiques` (actives), classées par CA récent
  conseillers    `agent_id` réellement observés dans l'historique de LA boutique
  catalogue      SKU en stock positif pour LA boutique, prix médian observé
  objectif       `sales.objectifs`, à défaut la médiane des 28 derniers jours
  profil horaire part du CA par heure, calculée sur le même jour de semaine

Si la base est injoignable, le simulateur **n'émet rien**. Inventer des produits
et des prix plausibles donnerait une démo qui marche et des chiffres faux — le
silence est le seul repli honnête.

RESPECT DU STOCK
────────────────
Un SKU à quantité nulle n'est jamais vendu : la vente est convertie en signal de
rupture. La décrémentation elle-même reste au trigger `trg_sync_stock_on_sale`,
qui est la source de vérité — le simulateur ne touche jamais `stock_levels`.

RYTHME
──────
Le débit suit la courbe d'objectif de chaque boutique, mais ne tombe jamais à
zéro : une boutique en avance continue de vendre au ralenti. L'ancienne porte
binaire coupait tout flux dès l'objectif dépassé, et le tableau de bord restait
figé sur « En attente de la première vente » le reste de la journée.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Optional

import asyncpg

from app.core.config import Config

logger = logging.getLogger(__name__)

_pg_pool: Optional[asyncpg.Pool] = None


async def _get_sim_pool() -> asyncpg.Pool:
    global _pg_pool
    if _pg_pool is None or _pg_pool._closed:
        _pg_pool = await asyncpg.create_pool(
            Config.dsn_async(), min_size=1, max_size=5, command_timeout=15, ssl=False)
    return _pg_pool


# ── Réglages, tous surchargeables par environnement ──────────────────────────
SIM_MAX_STORES     = int(os.getenv("SIM_MAX_STORES", "5"))
SIM_TICK_SECONDS   = int(os.getenv("SIM_TICK_SECONDS", "15"))
SIM_REFRESH_MIN    = int(os.getenv("SIM_REFRESH_MINUTES", "30"))
SIM_HISTORY_WEEKS  = int(os.getenv("SIM_HISTORY_WEEKS", "8"))
# Débit résiduel quand la boutique est en avance : une boutique qui a dépassé
# son objectif n'arrête pas de vendre.
SIM_AHEAD_RATE     = float(os.getenv("SIM_AHEAD_RATE", "0.12"))
SIM_OPEN_HOUR      = int(os.getenv("STORE_OPEN_HOUR", "8"))
SIM_CLOSE_HOUR     = int(os.getenv("STORE_CLOSE_HOUR", "20"))


@dataclass
class StoreContext:
    """Tout ce qu'il faut pour simuler une boutique — entièrement issu de la base."""
    store_id: str
    advisors: list[int] = field(default_factory=list)
    catalogue: list[dict] = field(default_factory=list)   # sku, name, price
    daily_target: float = 0.0
    hourly_share: dict[int, float] = field(default_factory=dict)
    loaded_at: Optional[datetime] = None

    def is_usable(self) -> bool:
        return bool(self.advisors and self.catalogue and self.daily_target > 0)


class RealtimeSimulator:
    """Simulateur de ventes temps réel sur N boutiques découvertes dynamiquement."""

    def __init__(self, store_ids: Optional[list[str]] = None,
                 interval: int = SIM_TICK_SECONDS,
                 max_stores: int = SIM_MAX_STORES):
        # `store_ids=None` → découverte en base. Aucune valeur par défaut codée.
        self._requested_stores = list(store_ids) if store_ids else None
        self.interval   = max(1, interval)
        self.max_stores = max(1, max_stores)

        self.on_sale:    Optional[Callable] = None
        self.on_stockout: Optional[Callable] = None

        self._contexts: dict[str, StoreContext] = {}
        self._running   = False
        self._task:     Optional[asyncio.Task] = None
        self._tx_count  = 0
        self._skipped_stockout = 0

    # ── Découverte ───────────────────────────────────────────────────────────

    async def _discover_stores(self, conn) -> list[str]:
        if self._requested_stores:
            return self._requested_stores[: self.max_stores]

        rows = await conn.fetch("""
            SELECT b.store_id
            FROM sales.boutiques b
            JOIN (
                SELECT store_id, SUM(lig_ttc) ca
                FROM sales.transactions
                WHERE date_only >= CURRENT_DATE - 30
                GROUP BY store_id
            ) t ON t.store_id = b.store_id
            WHERE b.active
            ORDER BY t.ca DESC
            LIMIT $1
        """, self.max_stores)
        stores = [r["store_id"] for r in rows]

        # Le magasin affiché par le dashboard doit TOUJOURS produire des ventes,
        # même s'il n'est pas dans le top CA : sinon le bandeau reste figé sur
        # « En attente de la première vente » et le stock ne bouge jamais. On
        # garantit donc sa présence en tête. Surchargeable via SIM_STORE_IDS.
        pinned = [s.strip() for s in os.getenv(
            "SIM_STORE_IDS", Config.DEFAULT_STORE_ID).split(",") if s.strip()]
        for sid in reversed(pinned):
            if sid and sid not in stores:
                stores.insert(0, sid)
        return stores

    # ── Contexte d'une boutique ──────────────────────────────────────────────

    async def _load_context(self, conn, store_id: str) -> StoreContext:
        ctx = StoreContext(store_id=store_id, loaded_at=datetime.now())

        ctx.advisors = [int(r["agent_id"]) for r in await conn.fetch("""
            SELECT agent_id, COUNT(*) n
            FROM sales.transactions
            WHERE store_id = $1 AND agent_id IS NOT NULL
              AND date_only >= CURRENT_DATE - ($2::int * 7)
            GROUP BY agent_id ORDER BY n DESC LIMIT 12
        """, store_id, SIM_HISTORY_WEEKS)]

        # Catalogue : uniquement ce que la boutique a réellement en stock.
        #
        # Le prix retenu est, par ordre de préférence, la MÉDIANE des prix
        # effectivement pratiqués dans CETTE boutique, puis le tarif catalogue.
        # Un produit sans aucun prix connu est écarté : lui en inventer un
        # fausserait le CA simulé, donc l'objectif, donc le gap, donc l'urgence.
        ctx.catalogue = [
            {"sku": int(r["sku"]), "name": r["name"], "price": float(r["price"]),
             "stock": int(r["stock"])}
            for r in await conn.fetch("""
                SELECT s.sku,
                       s.quantity_available AS stock,
                       -- NULLIF imbriqués : écarte les libellés vides ET ceux
                       -- réduits à un point, présents dans le référentiel.
                       COALESCE(NULLIF(NULLIF(TRIM(pr.nom), ''), '.'),
                                NULLIF(NULLIF(TRIM(ip.product_name), ''), '.'),
                                'SKU ' || s.sku)                            AS name,
                       COALESCE(obs.prix, pr.prix_ttc, ip.unit_price)      AS price
                FROM inventory.stock_levels s
                LEFT JOIN sales.produits     pr ON pr.sku = s.sku
                LEFT JOIN inventory.products ip ON ip.sku = s.sku
                LEFT JOIN (
                    SELECT sku, PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY lig_ttc) AS prix
                    FROM sales.transactions
                    WHERE store_id = $1 AND lig_ttc > 0
                      AND date_only >= CURRENT_DATE - ($2::int * 7)
                    GROUP BY sku
                ) obs ON obs.sku = s.sku
                WHERE s.store_id = $1
                  AND s.quantity_available > 0
                  AND COALESCE(obs.prix, pr.prix_ttc, ip.unit_price) > 0
                ORDER BY s.quantity_available DESC
                LIMIT 60
            """, store_id, SIM_HISTORY_WEEKS)
        ]

        ctx.daily_target = float(await conn.fetchval("""
            SELECT COALESCE(
                (SELECT objectif_ca FROM sales.objectifs
                 WHERE store_id = $1 ORDER BY ABS(date_objectif - CURRENT_DATE) LIMIT 1),
                (SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ca_total)
                 FROM sales.vw_ca_par_boutique
                 WHERE store_id = $1 AND date_only >= CURRENT_DATE - 28),
                0)
        """, store_id) or 0.0)

        ctx.hourly_share = await self._load_hourly_share(conn, store_id)
        return ctx

    async def _load_hourly_share(self, conn, store_id: str) -> dict[int, float]:
        """Part du CA journalier par heure, sur le même jour de semaine."""
        rows = await conn.fetch("""
            SELECT heure, SUM(lig_ttc) ca
            FROM sales.transactions
            WHERE store_id = $1
              AND date_only >= CURRENT_DATE - ($2::int * 7)
              AND EXTRACT(DOW FROM date_only) = EXTRACT(DOW FROM CURRENT_DATE)
              AND heure BETWEEN $3 AND $4
            GROUP BY heure
        """, store_id, SIM_HISTORY_WEEKS, SIM_OPEN_HOUR, SIM_CLOSE_HOUR)

        by_hour = {int(r["heure"]): float(r["ca"] or 0) for r in rows}
        total = sum(by_hour.values())
        if total <= 0:
            # Aucun historique pour ce jour de semaine : répartition uniforme.
            hours = range(SIM_OPEN_HOUR, SIM_CLOSE_HOUR + 1)
            n = len(list(hours))
            return {h: 1.0 / n for h in range(SIM_OPEN_HOUR, SIM_CLOSE_HOUR + 1)}
        return {h: v / total for h, v in by_hour.items()}

    async def _refresh_contexts(self) -> None:
        try:
            pool = await _get_sim_pool()
            async with pool.acquire() as conn:
                stores = await self._discover_stores(conn)
                if not stores:
                    logger.warning("[SIM] aucune boutique active trouvée — rien à simuler")
                    return
                fresh: dict[str, StoreContext] = {}
                for sid in stores:
                    ctx = await self._load_context(conn, sid)
                    if ctx.is_usable():
                        fresh[sid] = ctx
                    else:
                        logger.info("[SIM] %s ignorée — %d conseillers, %d SKU, objectif %.0f",
                                    sid, len(ctx.advisors), len(ctx.catalogue), ctx.daily_target)
                self._contexts = fresh
                logger.info("[SIM] %d boutique(s) simulée(s) : %s",
                            len(fresh), ", ".join(fresh) or "aucune")
        except Exception as e:
            logger.warning("[SIM] chargement du contexte impossible (%s) — aucune émission", e)

    # ── Rythme ───────────────────────────────────────────────────────────────

    def _expected_ca_now(self, ctx: StoreContext) -> float:
        """CA cumulé attendu à cet instant, selon le profil horaire de CETTE boutique."""
        now = datetime.now()
        pct = sum(v for h, v in ctx.hourly_share.items() if h < now.hour)
        pct += ctx.hourly_share.get(now.hour, 0.0) * (now.minute / 60.0)
        return ctx.daily_target * pct

    async def _ca_today(self, conn, store_id: str) -> float:
        """CA des heures écoulées uniquement — même convention que l'Analyste."""
        val = await conn.fetchval("""
            SELECT COALESCE(SUM(lig_ttc), 0) FROM (
                SELECT heure, lig_ttc FROM sales.transactions
                WHERE store_id = $1 AND date_only = $2
                UNION ALL
                SELECT heure, lig_ttc FROM sales.transactions_rt
                WHERE store_id = $1 AND date_only = $2
            ) t WHERE heure <= $3
        """, store_id, date.today(), datetime.now().hour)
        return float(val or 0.0)

    def _should_emit(self, ctx: StoreContext, ca_today: float) -> bool:
        # Boutique fermée → aucune vente, quel que soit le retard sur l'objectif.
        # Source unique de vérité partagée avec le dashboard (config.is_store_open).
        # On passe le `now` du module (patchable par les tests) plutôt que de
        # laisser is_store_open lire sa propre horloge.
        from app.core.config import is_store_open
        if not is_store_open(datetime.now()):
            return False

        gap = self._expected_ca_now(ctx) - ca_today
        if gap > 0:
            # En retard : probabilité croissante avec le retard.
            return random.random() < min(1.0, gap / 30.0)
        # En avance : débit résiduel, jamais nul.
        return random.random() < SIM_AHEAD_RATE

    # ── Émission ─────────────────────────────────────────────────────────────

    async def _emit_sale(self, conn, ctx: StoreContext) -> None:
        advisor = random.choice(ctx.advisors)
        # Pondération par le stock : un SKU abondant se vend plus souvent qu'un
        # SKU en fin de série, ce qui reproduit la réalité du rayon.
        weights = [max(1, item["stock"]) for item in ctx.catalogue]
        item = random.choices(ctx.catalogue, weights=weights, k=1)[0]

        stock = await conn.fetchval("""
            SELECT quantity_available FROM inventory.stock_levels
            WHERE store_id = $1 AND sku = $2
        """, ctx.store_id, item["sku"])

        if stock is None or int(stock) <= 0:
            # Rupture : pas de vente, mais un signal. Émettre la vente quand même
            # créerait du stock négatif et fausserait toute la chaîne d'appro.
            self._skipped_stockout += 1
            item["stock"] = 0
            if self.on_stockout:
                try:
                    self.on_stockout(ctx.store_id, item["sku"], item["name"])
                except Exception as e:
                    logger.debug("[SIM] on_stockout: %s", e)
            return

        item["stock"] = int(stock)
        price = round(max(1.0, item["price"] * random.uniform(0.85, 1.15)), 2)
        now = datetime.now()

        await conn.execute("""
            INSERT INTO sales.transactions_rt
                (sale_id, date_vente, date_only, heure, store_id, agent_id,
                 cod_prod, des_produit, lig_ttc, lig_ht, lig_tva, qte_produit)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            ON CONFLICT (sale_id) DO NOTHING
        """, str(uuid.uuid4()), now, now.date(), now.hour, ctx.store_id, advisor,
             item["sku"], item["name"], price,
             round(price / 1.19, 2), round(price * 0.19 / 1.19, 2), 1)

        self._tx_count += 1
        logger.info("[SIM] %s | %s (%s) %.2f TND | conseiller %s | stock %d→%d",
                    ctx.store_id, item["sku"], item["name"][:28], price,
                    advisor, int(stock), int(stock) - 1)

        if self.on_sale:
            try:
                self.on_sale(ctx.store_id, item["sku"], 1,
                             product_name=item["name"], amount=price)
            except Exception as e:
                logger.debug("[SIM] on_sale: %s", e)

    # ── Boucle ───────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        await self._refresh_contexts()
        last_refresh = datetime.now()

        while self._running:
            await asyncio.sleep(self.interval)
            if not self._running:
                break

            if (datetime.now() - last_refresh).total_seconds() > SIM_REFRESH_MIN * 60:
                await self._refresh_contexts()
                last_refresh = datetime.now()

            if not self._contexts:
                continue

            try:
                pool = await _get_sim_pool()
                async with pool.acquire() as conn:
                    for ctx in list(self._contexts.values()):
                        try:
                            ca = await self._ca_today(conn, ctx.store_id)
                            if self._should_emit(ctx, ca):
                                await self._emit_sale(conn, ctx)
                        except Exception as e:
                            logger.debug("[SIM] %s : %s", ctx.store_id, e)
            except Exception as e:
                logger.warning("[SIM] tick impossible (%s)", e)

    # ── Cycle de vie et introspection ────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("[SIM] démarré — tick %ds, jusqu'à %d boutiques",
                    self.interval, self.max_stores)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    def get_tx_count(self) -> int:
        return self._tx_count

    def snapshot(self) -> dict:
        return {
            "running": self._running,
            "tick_seconds": self.interval,
            "transactions": self._tx_count,
            "stockout_skips": self._skipped_stockout,
            "stores": {
                sid: {"advisors": len(c.advisors), "catalogue": len(c.catalogue),
                      "daily_target": round(c.daily_target, 2),
                      "loaded_at": c.loaded_at.isoformat() if c.loaded_at else None}
                for sid, c in self._contexts.items()
            },
        }
