"""
backfill_critical_trend_demo.py
Script à lancer UNE FOIS avant une démo pour remplir immédiatement le chart
"Tendance du risque (24-48h)", sans attendre que le job horaire
(critical_trend_snapshot.snapshot_loop) accumule 48h de vraies données.

Ce que fait le script :
    1. Lit le % critique ACTUEL et RÉEL du magasin (via le cache
       analyze_store(), même source que le job de prod — donc le dernier
       point du chart == la vraie valeur affichée ailleurs sur le dashboard).
    2. Génère ~48 points horaires dans le passé qui MÈNENT à cette valeur
       réelle, avec une légère variation aléatoire, pour que le chart ait
       une forme crédible pour la démo.
    3. Insère ces points avec un snapshot_time explicite dans le passé
       (voir insert_critical_trend_snapshot(..., snapshot_time=...)).

Important — ce n'est PAS une source de données pour la prod :
    Les points backfillés sont synthétiques (sauf le dernier, qui est réel).
    Une fois le service tourné en continu quelques jours, la vraie
    accumulation du job horaire remplace naturellement ces points passés
    dès qu'ils sortent de la fenêtre `hours_back` demandée par le frontend
    (48h par défaut) — donc rien à nettoyer après coup, le seed "expire"
    de lui-même.

Usage :
    python -m app.inventory.scripts.backfill_critical_trend_demo --store I63
    python -m app.inventory.scripts.backfill_critical_trend_demo --store I63 --hours 48 --points-per-hour 1
"""
import argparse
import logging
import random
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_critical_trend_demo")


def _get_current_real_pct(store_id: str) -> tuple[int, int, int]:
    """
    Renvoie (total_skus, critical_count, high_count) à partir du cache
    analyze_store() — la même source que le job de prod. Si le cache n'est
    pas chaud, force un run bloquant (acceptable ici : script one-off lancé
    à la main, pas dans une boucle temps réel).
    """
    from app.inventory.api.routes import analyze_store

    payload = analyze_store(
        store_id, "balanced", force_refresh=False,
        fast=False, page=1, page_size=0, blocking=True,
    )
    summary = payload.get("summary") or {}
    total = int(summary.get("totalSkus") or 0)

    if total <= 0:
        logger.warning(
            "Cache vide pour %s après un run bloquant — vérifie que le "
            "pipeline tourne pour ce magasin.", store_id,
        )
        return 0, 0, 0

    return total, int(summary.get("criticalCount") or 0), int(summary.get("highCount") or 0)


def backfill(store_id: str, hours: int, points_per_hour: int, seed: int | None) -> None:
    from app.inventory.repositories.inventory_repo import SyncInventoryRepo

    if seed is not None:
        random.seed(seed)

    total, real_critical, real_high = _get_current_real_pct(store_id)
    if total <= 0:
        logger.error("Abandon : impossible de lire un % critique réel pour %s", store_id)
        return

    real_pct = round(real_critical / total * 100, 2)
    logger.info(
        "Valeur réelle actuelle pour %s : %d/%d critique (%.1f%%) — le chart y mènera",
        store_id, real_critical, total, real_pct,
    )

    step_minutes = 60 // max(points_per_hour, 1)
    n_points = hours * points_per_hour
    now = datetime.utcnow()

    # Point de départ synthétique : un peu plus bas que la valeur actuelle,
    # pour dessiner une tendance à la hausse crédible (cf. maquette fournie).
    # Si vous préférez une tendance à la baisse pour votre démo, inversez
    # simplement start_pct et real_pct ci-dessous.
    start_pct = max(5.0, real_pct - random.uniform(15, 25))

    inserted = 0
    for i in range(n_points, -1, -1):
        t = now - timedelta(minutes=i * step_minutes)
        progress = 1 - (i / n_points) if n_points else 1
        # Interpolation linéaire start → real, + bruit pour ne pas avoir
        # une ligne parfaitement droite.
        base_pct = start_pct + (real_pct - start_pct) * progress
        noisy_pct = max(0.0, min(100.0, base_pct + random.uniform(-2.5, 2.5)))

        # Le tout dernier point DOIT être la vraie valeur, pas une valeur
        # bruitée — c'est ce que le reste du dashboard affiche.
        if i == 0:
            noisy_pct = real_pct
            critical_count, high_count = real_critical, real_high
        else:
            critical_count = round(noisy_pct / 100 * total)
            high_count = real_high  # approximation raisonnable pour du synthétique

        ok = SyncInventoryRepo.insert_critical_trend_snapshot(
            store_id=store_id,
            total_skus=total,
            critical_count=critical_count,
            high_count=high_count,
            critical_pct=round(noisy_pct, 2),
            snapshot_time=t,
        )
        inserted += 1 if ok else 0

    logger.info(
        "✅ %d/%d points insérés pour %s (%dh d'historique, %d pts/h). "
        "Le chart devrait maintenant s'afficher immédiatement.",
        inserted, n_points + 1, store_id, hours, points_per_hour,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, help="store_id, ex: I63")
    parser.add_argument("--hours", type=int, default=48, help="fenêtre d'historique à générer")
    parser.add_argument(
        "--points-per-hour", type=int, default=1,
        help="1 = un point/heure (comme le job de prod), 4 = un point/15min pour un chart plus lisse",
    )
    parser.add_argument("--seed", type=int, default=None, help="graine aléatoire, pour reproductibilité")
    args = parser.parse_args()

    backfill(args.store, args.hours, args.points_per_hour, args.seed)
