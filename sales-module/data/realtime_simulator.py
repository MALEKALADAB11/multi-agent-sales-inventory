"""
realtime_simulator.py — v2.0 PostgreSQL
=========================================
Charge les advisors et SKUs depuis PostgreSQL au lieu des CSV.
Plus aucune dépendance aux fichiers CSV à runtime.
"""

import asyncio
import logging
import random
from datetime import datetime
from typing import Callable, Optional

import asyncpg

logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host": "localhost", "port": 5432,
    "database": "ooredoo_sales",
    "user": "postgres", "password": "admin",
}

SKU_REFRESH_DELAY = 999999  # désactiver phase 2 inventory

class RealtimeSimulator:
    """
    Simule des transactions POS en temps réel.
    Charge advisors et SKUs depuis PostgreSQL (store I63).
    """

    def __init__(self, store_id: str = "I63", interval: int = 15):
        self.store_id   = store_id
        self.interval   = interval
        self.on_sale:   Optional[Callable] = None
        self._running   = False
        self._task:     Optional[asyncio.Task] = None
        self._advisors  = []
        self._skus      = []
        self._tx_count  = 0

    async def _load_from_postgres(self):
        """Charger advisors et SKUs depuis PostgreSQL."""
        try:
            conn = await asyncpg.connect(**DB_CONFIG, timeout=10)
            try:
                # Charger les advisors réels de I63 depuis transactions_history
                advisor_rows = await conn.fetch("""
                    SELECT 
                        agent_id,
                        COUNT(*) AS nb_tx,
                        ROUND(AVG(lig_ttc), 2) AS avg_ticket,
                        ROUND(SUM(lig_ttc), 2) AS ca_total
                    FROM sales.transactions_history
                    WHERE store_id = $1
                      AND agent_id IS NOT NULL
                      AND agent_id != ''
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

                # Charger les SKUs réels vendus à I63
                sku_rows = await conn.fetch("""
                    SELECT 
                        cod_prod AS sku,
                        des_produit,
                        ROUND(AVG(lig_ttc), 2) AS avg_price,
                        COUNT(*) AS nb_ventes
                    FROM sales.transactions_history
                    WHERE store_id = $1
                      AND cod_prod IS NOT NULL
                      AND lig_ttc > 0
                    GROUP BY cod_prod, des_produit
                    ORDER BY nb_ventes DESC
                    LIMIT 20
                """, self.store_id)

                self._skus = [
                    {
                        "sku":   str(r["sku"]),
                        "name":  str(r["des_produit"] or r["sku"]),
                        "price": float(r["avg_price"] or 25),
                    }
                    for r in sku_rows
                ] if sku_rows else []

                logger.info(
                    f"[SIM PG] Chargé {len(self._advisors)} advisors "
                    f"et {len(self._skus)} SKUs depuis PostgreSQL pour {self.store_id}"
                )

            finally:
                await conn.close()

        except Exception as e:
            logger.warning(f"[SIM PG] Erreur chargement: {e} — fallback hardcodé")
            # Fallback minimal si PostgreSQL indisponible
            self._advisors = [
                {"id": "7296", "avg_ticket": 45, "ca_total": 0},
                {"id": "7451", "avg_ticket": 35, "ca_total": 0},
                {"id": "8987", "avg_ticket": 55, "ca_total": 0},
                {"id": "8988", "avg_ticket": 40, "ca_total": 0},
            ]
            self._skus = [
                {"sku": "8811364", "name": "Forfait Flexi 25 Go", "price": 25},
                {"sku": "8811001", "name": "Paiement Facture Postpayé", "price": 45},
                {"sku": "8812148", "name": "Forfait MIFI PRE 80 Go", "price": 109},
                {"sku": "5021240", "name": "Xiaomi Redmi Note 15 8/256", "price": 899},
                {"sku": "8811365", "name": "Forfait Flexi 55 Go", "price": 55},
                {"sku": "8811546", "name": "Forfait 8 Go", "price": 18},
                {"sku": "8811458", "name": "Forfait 30 Go", "price": 30},
                {"sku": "5021214", "name": "Redmi 15C 8/256", "price": 549},
            ]

    async def _simulate_loop(self):
        """Boucle principale de simulation."""
        # Charger depuis PostgreSQL au démarrage
        await self._load_from_postgres()

        logger.info(
            f"RealtimeSimulator started — {len(self._skus)} SKUs | "
            f"{len(self._advisors)} advisors | every {self.interval}s"
        )

        while self._running:
            await asyncio.sleep(self.interval)
            if not self._running:
                break

            try:
                await self._generate_transaction()
            except Exception as e:
                logger.debug(f"[SIM] TX error: {e}")

    async def _generate_transaction(self):
        """Générer et persister une transaction simulée."""
        if not self._advisors or not self._skus:
            return

        advisor = random.choice(self._advisors)
        sku     = random.choice(self._skus)

        # Varier le prix autour de la moyenne (±20%)
        price = round(sku["price"] * random.uniform(0.8, 1.2), 2)
        price = max(price, 1.0)

        self._tx_count += 1
        now = datetime.now()

        logger.info(
            f"{now.strftime('%H:%M:%S')} | TX — advisor={advisor['id']} "
            f" sku={sku['sku']} ({sku['name']})  {price:.0f} DT  (total={self._tx_count})"
        )

        # Insérer dans sales.transactions_rt
        try:
            import asyncpg as _apg
            import uuid
            conn = await _apg.connect(**DB_CONFIG, timeout=3)
            try:
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
                    advisor["id"],
                    sku["sku"],
                    sku["name"],
                    round(price, 2),
                    round(price / 1.19, 2),
                    round(price * 0.19 / 1.19, 2),
                    1,
                )
                logger.info(f"[RT TX] ✅ {sku['sku']}@{self.store_id} {price:.0f} TND")
            finally:
                await conn.close()
        except Exception as e:
            logger.warning(f"[RT TX] INSERT err: {e}")

        # Appeler le callback on_sale si défini
        if self.on_sale:
            try:
                self.on_sale(self.store_id, sku["sku"], 1)
            except Exception as e:
                logger.debug(f"[SIM] on_sale callback: {e}")

    def start(self):
        """Démarrer le simulateur."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._simulate_loop())
        logger.info(f"[SIM] Démarré pour {self.store_id}")

    def stop(self):
        """Arrêter le simulateur."""
        self._running = False
        if self._task:
            self._task.cancel()

    def get_tx_count(self) -> int:
        return self._tx_count

    def get_advisors(self) -> list:
        return self._advisors

    def get_skus(self) -> list:
        return self._skus