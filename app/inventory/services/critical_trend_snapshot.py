"""
critical_trend_snapshot.py
Tâche de fond : écrit une ligne dans inventory.critical_trend_history toutes
les heures, pour chaque magasin actif. Alimente le mini chart "Tendance du
risque (24-48h)" du dashboard inventory (% de SKUs en riskLevel=critical).

Design — pourquoi lire le cache analyze_store() plutôt que recalculer :
    Le "% critique" doit rester identique à celui affiché ailleurs sur le
    dashboard (KPI cards, donut), qui vient de riskLevel — un calcul basé
    sur le lead time fournisseur et la demande prévue, pas d'un seuil de
    stock fixe. Ce nombre n'existe qu'après un run du pipeline d'analyse.
    En mode normal on ne le recalcule pas ici : analyze_store(
    force_refresh=False, blocking=False) renvoie le payload déjà en cache
    (le reste du système — CronTrigger, sale triggers, WS — le garde
    chaud). Ce job ne fait donc jamais attendre le pipeline ; il se
    contente de lire ce qui existe déjà et de le persister pour
    l'historique.

    Si le cache n'est pas encore chaud pour un magasin (démarrage récent,
    totalSkus=0), le snapshot de ce cycle est simplement sauté — pas
    d'erreur, on retente au prochain cycle.

Mode "force activity" (préparation de démo) :
    Lire le cache toutes les 60 min ne sert à rien si le stock ne bouge pas
    réellement entre deux lectures — on obtiendrait N points identiques,
    une ligne plate. CRITICAL_TREND_FORCE_ACTIVITY=true active un mode où,
    avant chaque snapshot, le job :
      1. rejoue de VRAIES ventes historiques via
         StockSimulator.replay_sales_as_today() — ça décrémente le vrai
         stock en base, comme une vraie vente ;
      2. force un recalcul complet du pipeline (force_refresh=True) pour
         que riskLevel reflète ce nouveau stock.
    Résultat : une vraie tendance avec de vrais points, en quelques cycles
    au lieu de 48h.

    ⚠️ ATTENTION : ce mode consomme réellement du stock (rejoue des ventes)
    sur les magasins ciblés. À utiliser UNIQUEMENT sur un magasin de
    démo/test, jamais sur un magasin de production réel — et à désactiver
    (ou remettre CRITICAL_TREND_FORCE_ACTIVITY=false) une fois la démo
    passée. force_refresh=True est aussi plus coûteux (recalcul complet à
    chaque cycle) : gardez un intervalle raisonnable (10-15 min), pas 1-2
    min, pour ne pas marteler le pipeline.

Usage normal, prod (main.py, au démarrage, même pattern que
po_auto_confirm.auto_confirm_loop) :
    from app.inventory.services.critical_trend_snapshot import snapshot_loop
    asyncio.create_task(snapshot_loop(active_stores))

Usage préparation de démo (variables d'env, aucun changement de code) :
    CRITICAL_TREND_INTERVAL_MINUTES=15
    CRITICAL_TREND_FORCE_ACTIVITY=true
    CRITICAL_TREND_REPLAY_DAYS_BACK=1      # optionnel, défaut 1
    CRITICAL_TREND_REPLAY_SPEED=3.0        # optionnel, défaut 1.0 — accélère la conso de stock
"""
import asyncio
import logging
import os
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# Résolution horaire par défaut : suffisante pour une tendance 24-48h en
# régime de croisière, et cohérente avec le cycle de rafraîchissement du
# reste du pipeline (CronTrigger = 15 min, mais le payload critique ne
# bouge pas assez vite pour justifier plus fin qu'une heure sur ce chart).
# Override CRITICAL_TREND_INTERVAL_MINUTES=15 en veille de démo (voir
# CRITICAL_TREND_FORCE_ACTIVITY ci-dessous) — remettre à 60 (ou retirer la
# variable) une fois la démo passée.
SNAPSHOT_INTERVAL_MINUTES = int(os.getenv("CRITICAL_TREND_INTERVAL_MINUTES", "60"))

# Mode "force activity" — voir docstring du module. Off par défaut : sans
# cette variable, le comportement est identique à avant (lecture pure du
# cache, jamais de recalcul, jamais de vente rejouée).
FORCE_ACTIVITY = os.getenv("CRITICAL_TREND_FORCE_ACTIVITY", "false").lower() == "true"
REPLAY_DAYS_BACK = int(os.getenv("CRITICAL_TREND_REPLAY_DAYS_BACK", "1"))
REPLAY_SPEED = float(os.getenv("CRITICAL_TREND_REPLAY_SPEED", "1.0"))

# Décalage du tout premier snapshot : laisse le prewarm du cache inventory
# (voir main.py._prewarm_inventory) partir en premier, pour ne pas logger
# des cycles "cache pas encore chaud" à chaque redémarrage.
FIRST_SNAPSHOT_DELAY_SECONDS = 30


async def _build_simulator():
    """
    Construit un StockSimulator adossé à sa propre connexion InventoryRepo
    (async), indépendante du reste de l'app — uniquement utilisé en mode
    FORCE_ACTIVITY. Retourne None si la connexion échoue (le job continue
    alors en mode lecture seule, comme si FORCE_ACTIVITY était false).
    """
    try:
        from app.inventory.repositories.inventory_repo import InventoryRepo
        from app.inventory.stock_simulator import StockSimulator

        repo = InventoryRepo()
        await repo.connect()
        return StockSimulator(repo)
    except Exception as exc:
        logger.warning(
            "[CriticalTrend] FORCE_ACTIVITY demandé mais StockSimulator "
            "indisponible (%s) — repli sur lecture seule du cache.", exc,
        )
        return None


async def snapshot_loop(
    store_ids: Iterable[str],
    interval_minutes: int = SNAPSHOT_INTERVAL_MINUTES,
    force_activity: Optional[bool] = None,
) -> None:
    """Boucle infinie — à lancer une seule fois avec asyncio.create_task()."""
    store_ids = [s for s in store_ids if s]
    if not store_ids:
        logger.warning("[CriticalTrend] Aucun magasin à surveiller — job non démarré")
        return

    force_activity = FORCE_ACTIVITY if force_activity is None else force_activity

    from app.inventory.api.routes import analyze_store
    from app.inventory.repositories.inventory_repo import SyncInventoryRepo

    simulator = await _build_simulator() if force_activity else None
    if force_activity and simulator is not None:
        logger.warning(
            "[CriticalTrend] 🔴 FORCE_ACTIVITY actif — ce job va CONSOMMER DU "
            "VRAI STOCK sur %s en rejouant des ventes historiques, à chaque "
            "cycle de %d min. Désactiver après la démo "
            "(CRITICAL_TREND_FORCE_ACTIVITY=false).",
            ", ".join(store_ids), interval_minutes,
        )

    await asyncio.sleep(FIRST_SNAPSHOT_DELAY_SECONDS)
    loop = asyncio.get_event_loop()

    while True:
        for store_id in store_ids:
            try:
                if force_activity and simulator is not None:
                    applied = await simulator.replay_sales_as_today(
                        store_id, days_back=REPLAY_DAYS_BACK, speed=REPLAY_SPEED,
                    )
                    logger.info(
                        "[CriticalTrend] %s: %d SKU(s) mis à jour via replay "
                        "avant snapshot (mode démo)", store_id, applied,
                    )

                # force_refresh=True en mode démo : le stock vient de bouger
                # (replay ci-dessus), il faut un vrai recalcul pour que
                # riskLevel le reflète. En mode normal, force_refresh=False :
                # lecture pure du cache, jamais de recalcul déclenché par ce
                # job — voir docstring du module.
                payload = await loop.run_in_executor(
                    None,
                    lambda sid=store_id: analyze_store(
                        sid, "balanced", force_refresh=force_activity,
                        fast=True, page=1, page_size=0, blocking=False,
                    ),
                )
                summary = payload.get("summary") or {}
                total    = int(summary.get("totalSkus") or 0)
                critical = int(summary.get("criticalCount") or 0)
                high     = int(summary.get("highCount") or 0)

                if total <= 0:
                    logger.debug(
                        "[CriticalTrend] %s: cache pas encore chaud, snapshot sauté",
                        store_id,
                    )
                    continue

                critical_pct = round(critical / total * 100, 2)

                ok = SyncInventoryRepo.insert_critical_trend_snapshot(
                    store_id=store_id,
                    total_skus=total,
                    critical_count=critical,
                    high_count=high,
                    critical_pct=critical_pct,
                )
                if ok:
                    logger.info(
                        "[CriticalTrend] %s: snapshot enregistré — %d/%d critique (%.1f%%)",
                        store_id, critical, total, critical_pct,
                    )
                else:
                    logger.warning("[CriticalTrend] %s: échec écriture snapshot", store_id)

            except Exception as exc:
                logger.warning("[CriticalTrend] snapshot échoué pour %s: %s", store_id, exc)

        await asyncio.sleep(interval_minutes * 60)
