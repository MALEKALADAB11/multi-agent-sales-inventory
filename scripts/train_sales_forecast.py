"""
Entraînement du modèle global de prévision du CA journalier.

    python scripts/train_sales_forecast.py                # backtest + entraînement final
    python scripts/train_sales_forecast.py --backtest-only
    python scripts/train_sales_forecast.py --folds 4 --fold-days 21

Produit :
    app/sales/models/forecast/sales_daily_v1.ubj          booster principal
    app/sales/models/forecast/sales_daily_v1.q0.1.ubj     borne basse IC 80 %
    app/sales/models/forecast/sales_daily_v1.q0.9.ubj     borne haute IC 80 %
    app/sales/models/forecast/sales_daily_v1.meta.json    features, fenêtre, métriques
    evals/results/sales_forecast_backtest.json            rapport complet
    evals/results/SALES_FORECAST_REPORT.md                rapport lisible

Le modèle final n'est écrit **que s'il bat le moteur statistique en WAPE** sur la
fenêtre de test. Un modèle qui perd n'a rien à faire en production, et l'écrire
malgré tout rendrait le repli silencieusement nuisible.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from app.sales.forecasting.backtest import BacktestConfig, run_backtest  # noqa: E402
from app.sales.forecasting.data import load_panel  # noqa: E402
from app.sales.forecasting.features import build_feature_frame  # noqa: E402
from app.sales.forecasting.global_model import (  # noqa: E402
    DEFAULT_MODEL_DIR, GlobalSalesForecaster, TrainingConfig,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("train_sales_forecast")

RESULTS_DIR = ROOT / "evals" / "results"
BASELINE_MODEL = "holt_winters_seasonal7"


def _markdown_report(report: dict) -> str:
    overall = report["overall"]
    order = sorted((m for m in overall if overall[m].get("wape") is not None),
                   key=lambda m: overall[m]["wape"])

    lines = [
        "# Prévision du CA journalier — rapport de backtest",
        "",
        f"Panel : **{report['panel']['stores']} boutiques**, "
        f"{report['panel']['rows']:,} jours-boutique, "
        f"{report['panel']['start']} → {report['panel']['end']}.",
        "",
        f"Protocole : rolling-origin à origine expansive, "
        f"**{report['config']['n_folds']} plis × {report['config']['fold_days']} jours**, "
        f"prévision à un jour (h=1). Fenêtre de test : "
        f"{report['test_window']['start']} → {report['test_window']['end']} "
        f"({report['test_window']['rows']:,} prévisions par modèle).",
        "",
        "## Résultats agrégés",
        "",
        "| Modèle | WAPE % | MAE TND | RMSE TND | MASE | Biais % |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for m in order:
        r = overall[m]
        mark = " ⭐" if m == report["best_model"] else ""
        lines.append(
            f"| `{m}`{mark} | **{r['wape']}** | {r['mae']} | {r['rmse']} | "
            f"{r.get('mase', '—')} | {r.get('bias', '—')} |")

    base = overall.get(BASELINE_MODEL, {}).get("wape")
    best = overall[report["best_model"]]["wape"]
    if base and best:
        gain = (base - best) / base * 100
        lines += [
            "",
            f"> Le meilleur modèle est **`{report['best_model']}`** à **{best} % de WAPE**, "
            f"soit **{gain:+.1f} %** d'erreur relative en moins que le moteur statistique "
            f"de production (`{BASELINE_MODEL}`, {base} %).",
        ]

    lines += ["", "## Détail par pli", "",
              "| Pli | Entraîné jusqu'au | Test | Lignes test | " +
              " | ".join(f"WAPE {m}" for m in order) + " |",
              "|---|---|---|---:|" + "---:|" * len(order)]
    for f in report["folds"]:
        cells = " | ".join(str(f["models"].get(m, {}).get("wape", "—")) for m in order)
        lines.append(
            f"| {f['fold']} | {f['train_end']} | {f['test_start']} → {f['test_end']} | "
            f"{f['test_rows']:,} | {cells} |")

    per_store = pd.DataFrame(report["per_store"])
    if not per_store.empty and "global_xgb" in per_store and BASELINE_MODEL in per_store:
        per_store["delta"] = per_store["global_xgb"] - per_store[BASELINE_MODEL]
        wins = int((per_store["delta"] < 0).sum())
        lines += [
            "", "## Couverture par boutique", "",
            f"Le modèle global bat le moteur statistique sur **{wins} boutiques "
            f"sur {len(per_store)}** ({wins / len(per_store) * 100:.0f} %).", "",
            "Les cinq boutiques où il perd le plus — à surveiller, le repli "
            "statistique reste disponible pour elles :", "",
            "| Boutique | CA moyen | WAPE global | WAPE Holt-Winters | Écart |",
            "|---|---:|---:|---:|---:|",
        ]
        for _, r in per_store.nlargest(5, "delta").iterrows():
            lines.append(
                f"| `{r['store_id']}` | {r['ca_moyen']} | {r['global_xgb']} | "
                f"{r[BASELINE_MODEL]} | {r['delta']:+.2f} |")

    lines += ["", f"*Backtest exécuté en {report['runtime_s']} s.*", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folds", type=int, default=6)
    ap.add_argument("--fold-days", type=int, default=28)
    ap.add_argument("--min-days", type=int, default=120,
                    help="historique minimal pour retenir une boutique")
    ap.add_argument("--backtest-only", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="écrire le modèle même s'il ne bat pas le moteur statistique")
    args = ap.parse_args()

    logger.info("Chargement du panel…")
    panel = asyncio.run(load_panel(min_days=args.min_days))
    if panel.empty:
        logger.error("panel vide — vérifier sales.vw_ca_par_boutique")
        return 1

    report = run_backtest(panel, BacktestConfig(n_folds=args.folds, fold_days=args.fold_days))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "sales_forecast_backtest.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (RESULTS_DIR / "SALES_FORECAST_REPORT.md").write_text(
        _markdown_report(report), encoding="utf-8")

    overall = report["overall"]
    logger.info("═" * 66)
    for m in sorted((k for k in overall if overall[k].get("wape") is not None),
                    key=lambda k: overall[k]["wape"]):
        logger.info("  %-24s WAPE %6.2f%%   MAE %8.2f   MASE %s",
                    m, overall[m]["wape"], overall[m]["mae"], overall[m].get("mase", "—"))
    logger.info("═" * 66)

    best = report["best_model"]
    base_wape = overall.get(BASELINE_MODEL, {}).get("wape")
    xgb_wape = overall.get("global_xgb", {}).get("wape")

    if args.backtest_only:
        logger.info("--backtest-only : aucun modèle écrit.")
        return 0

    if not args.force and (xgb_wape is None or (base_wape and xgb_wape >= base_wape)):
        logger.warning(
            "Le modèle global (%.2f%%) ne bat pas %s (%.2f%%) — rien n'est écrit. "
            "Le moteur statistique reste en production. Forcer avec --force.",
            xgb_wape or -1, BASELINE_MODEL, base_wape or -1)
        return 2

    logger.info("Entraînement final sur l'intégralité du panel…")
    feats = build_feature_frame(panel)
    valid_cut = feats["date"].max() - pd.Timedelta(days=28)
    model = GlobalSalesForecaster().train(
        feats[feats["date"] <= valid_cut], feats[feats["date"] > valid_cut], TrainingConfig())
    # Les catégories DOIVENT venir du frame réellement entraîné, pas du panel :
    # `build_feature_frame` écarte les boutiques trop jeunes, et un décalage
    # d'une seule catégorie renumérote les codes internes de XGBoost — les
    # prévisions resteraient plausibles tout en étant attribuées aux mauvaises
    # boutiques.
    model.metadata.store_categories = list(feats["store_cat"].cat.categories)
    model.metadata.metrics = {
        "backtest_wape": xgb_wape,
        "baseline_wape": base_wape,
        "baseline_model": BASELINE_MODEL,
        "test_window": report["test_window"],
        "best_model": best,
    }
    out = model.save(DEFAULT_MODEL_DIR)

    logger.info("Modèle écrit : %s", out)
    logger.info("Rapport      : %s", RESULTS_DIR / "SALES_FORECAST_REPORT.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
