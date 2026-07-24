"""
Comparaison des variantes d'objectif du modèle global.

    python scripts/tune_sales_forecast.py --folds 3

Le WAPE seul ne suffit pas à choisir : `reg:absoluteerror` prédit la médiane
conditionnelle et sous-prévoit donc structurellement sur des ventes à
distribution asymétrique. Or l'Analyste calcule le gap et l'urgence à partir de
`forecast_eod` — une prévision 8 % basse gonfle l'urgence affichée au vendeur
tôt dans la journée, quand le déroulé intraday pèse encore peu.

On arbitre donc sur **WAPE et |biais| conjointement**.
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

from app.sales.forecasting.backtest import BacktestConfig, run_backtest  # noqa: E402
from app.sales.forecasting.data import load_panel  # noqa: E402
from app.sales.forecasting.global_model import TrainingConfig  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("tune")

VARIANTS = {
    "mae_brut":        TrainingConfig(objective="reg:absoluteerror", calibrate=False),
    "mae_calibre":     TrainingConfig(objective="reg:absoluteerror", calibrate=True),
    "quantile_055":    TrainingConfig(objective="reg:quantileerror", objective_quantile=0.55,
                                      calibrate=False),
    "quantile_060":    TrainingConfig(objective="reg:quantileerror", objective_quantile=0.60,
                                      calibrate=False),
    # Les deux leviers sont indépendants : le quantile décale la prédiction vers
    # le haut de la distribution, la calibration annule ce qu'il en reste.
    "quantile_055_cal": TrainingConfig(objective="reg:quantileerror",
                                       objective_quantile=0.55, calibrate=True),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--fold-days", type=int, default=28)
    ap.add_argument("--only", nargs="*", default=None,
                    help="ne mesurer que ces variantes")
    args = ap.parse_args()

    panel = asyncio.run(load_panel())
    cfg = BacktestConfig(n_folds=args.folds, fold_days=args.fold_days)

    variants = ({k: v for k, v in VARIANTS.items() if k in args.only}
                if args.only else VARIANTS)
    results = {}
    for name, tc in variants.items():
        logger.info("═" * 60)
        logger.info("VARIANTE %s", name)
        rep = run_backtest(panel, cfg, tc)
        m = rep["overall"]["global_xgb"]
        results[name] = {"wape": m["wape"], "mae": m["mae"], "bias": m["bias"],
                         "mase": m.get("mase"), "rmse": m["rmse"]}
        logger.info("  → WAPE %.2f%% | biais %+.2f%% | MASE %s",
                    m["wape"], m["bias"], m.get("mase"))

    baseline = rep["overall"]["holt_winters_seasonal7"]

    logger.info("═" * 60)
    logger.info("%-16s %8s %8s %8s", "variante", "WAPE", "biais", "MASE")
    for name, r in sorted(results.items(), key=lambda kv: kv[1]["wape"]):
        logger.info("%-16s %7.2f%% %+7.2f%% %8s", name, r["wape"], r["bias"], r["mase"])
    logger.info("%-16s %7.2f%% %+7.2f%% %8s  (référence)",
                "holt_winters", baseline["wape"], baseline["bias"], baseline.get("mase"))

    # Choix : parmi les variantes à moins de 1 point de WAPE de la meilleure,
    # retenir celle dont le biais absolu est le plus faible. Une demi-décimale
    # de WAPE ne vaut pas une prévision systématiquement fausse dans le même sens.
    best_wape = min(r["wape"] for r in results.values())
    eligibles = {k: v for k, v in results.items() if v["wape"] <= best_wape + 1.0}
    winner = min(eligibles, key=lambda k: abs(eligibles[k]["bias"]))
    logger.info("→ variante retenue : %s", winner)

    suffix = "_partiel" if args.only else ""
    out = ROOT / "evals" / "results" / f"sales_forecast_tuning{suffix}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"variants": results, "baseline": baseline, "winner": winner,
         "folds": args.folds, "fold_days": args.fold_days},
        indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Rapport : %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
