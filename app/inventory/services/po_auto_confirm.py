"""
po_auto_confirm.py — Auto-confirmation des bons de commande SOUMIS.

Boucle de fond démarrée dans main.py (startup) : toutes les
PO_AUTO_CONFIRM_CHECK_MINUTES, tout BC resté en SOUMIS plus de
PO_AUTO_CONFIRM_HOURS sans action humaine passe automatiquement en CONFIRME
(confirmed_auto=TRUE + note d'audit — voir
SyncPurchaseOrderRepo.auto_confirm_stale_soumis).

Chaque confirmation est diffusée sur le WS du Kanban (po_status_changed) pour
que la carte se déplace en direct, exactement comme un drag humain.

Le travail DB est du psycopg2 synchrone → délégué à un thread via
asyncio.to_thread pour ne jamais bloquer la boucle uvicorn.
"""
from __future__ import annotations

import asyncio
import logging
import os

from app.inventory.repositories.supply_repo import SyncPurchaseOrderRepo
from app.inventory.services import po_ws_bus

logger = logging.getLogger(__name__)

AUTO_CONFIRM_HOURS = int(os.getenv("PO_AUTO_CONFIRM_HOURS", "24"))
CHECK_INTERVAL_SECONDS = int(os.getenv("PO_AUTO_CONFIRM_CHECK_MINUTES", "15")) * 60


async def run_auto_confirm_once(max_age_hours: int = AUTO_CONFIRM_HOURS) -> list[dict]:
    """Un passage : confirme les BC éligibles et notifie le board. Jamais raise."""
    try:
        confirmed = await asyncio.to_thread(
            SyncPurchaseOrderRepo.auto_confirm_stale_soumis, max_age_hours,
        )
    except Exception as exc:
        logger.warning("[po_auto_confirm] passage échoué: %s", exc)
        return []

    for po in confirmed:
        logger.info(
            "[po_auto_confirm] BC %s (sku=%s, store=%s) SOUMIS -> CONFIRME auto après %dh",
            po.get("po_id"), po.get("sku"), po.get("store_id"), max_age_hours,
        )
        try:
            await po_ws_bus.broadcast_po_status(
                store_id=str(po["store_id"]),
                po_id=str(po["po_id"]),
                sku=po["sku"],
                old_statut="SOUMIS",
                new_statut="CONFIRME",
            )
        except Exception as exc:
            logger.debug("[po_auto_confirm] broadcast WS raté pour %s: %s", po.get("po_id"), exc)
    return confirmed


async def auto_confirm_loop() -> None:
    """Boucle infinie — à lancer via asyncio.create_task au startup."""
    logger.info(
        "[po_auto_confirm] démarré: SOUMIS -> CONFIRME après %dh, vérification toutes les %d min",
        AUTO_CONFIRM_HOURS, CHECK_INTERVAL_SECONDS // 60,
    )
    while True:
        await run_auto_confirm_once()
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
