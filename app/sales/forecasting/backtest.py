"""
Harnais de backtest rolling-origin — comparaison honnête des prévisionnistes.

PROTOCOLE
─────────
Fenêtre glissante à origine expansive. Les `n_folds × fold_days` derniers jours
servent de test ; chaque pli n'est prédit qu'avec des données strictement
antérieures à son début. Tous les modèles produisent des prévisions à **un jour**
(h=1) sur exactement les mêmes couples (boutique, date) — c'est l'usage réel de
l'Analyste, qui prédit le CA du jour à partir de la veille.

MODÈLES COMPARÉS
────────────────
  seasonal_naive          y_{t-7} — le seul baseline qui compte vraiment sur du
                          retail hebdomadaire ; battre la moyenne ne prouve rien
  mean_7 / mean_28        moyennes mobiles
  holt_winters_seasonal7  le moteur de production (`ts_engine`), paramètres
                          re-cherchés au début de chaque pli
  global_xgb              le modèle global, ré-entraîné au début de chaque pli

Sur Holt-Winters, la suite `fitted` de la récursion EST la prévision à un jour :
l'état à l'instant t n'utilise que y_0..y_{t-1}. Seuls les paramètres (α, β, γ)
sont estimés sur la partie entraînement du pli, jamais sur le test.

MÉTRIQUES
─────────
  WAPE  Σ|e| / Σy      métrique de référence du projet, robuste aux petits jours
  MAE, RMSE            en TND
  MASE  MAE / MAE_naive-saisonnier   < 1 signifie « mieux que la naïve »
  biais  Σe / Σy       une prévision systématiquement basse ronge la confiance
                       du vendeur autant qu'une prévision imprécise
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .features import FEATURE_COLUMNS, SCALE_COLUMN, TARGET_COLUMN, build_feature_frame
from .global_model import GlobalSalesForecaster, TrainingConfig

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Métriques
# ══════════════════════════════════════════════════════════════════════════════

def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray,
                    naive_mae: float | None = None) -> dict:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    y_true, y_pred = y_true[mask], y_pred[mask]
    if len(y_true) == 0:
        return {"n": 0}

    err = y_pred - y_true
    total = float(np.sum(np.abs(y_true)))
    mae = float(np.mean(np.abs(err)))

    out = {
        "n":     int(len(y_true)),
        "wape":  round(float(np.sum(np.abs(err)) / total * 100), 2) if total > 0 else None,
        "mae":   round(mae, 2),
        "rmse":  round(float(np.sqrt(np.mean(err ** 2))), 2),
        "bias":  round(float(np.sum(err) / total * 100), 2) if total > 0 else None,
    }
    if naive_mae and naive_mae > 0:
        out["mase"] = round(mae / naive_mae, 3)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Prévisionnistes statistiques — vectorisés sur le panel
# ══════════════════════════════════════════════════════════════════════════════

def _holt_winters_one_step(y: np.ndarray, n_train: int, period: int = 7) -> np.ndarray:
    """
    Prévisions à un jour de Holt-Winters additif sur toute la série.

    Les paramètres sont cherchés sur `y[:n_train]` uniquement ; la récursion
    parcourt ensuite la série entière et l'on renvoie `fitted`, dont l'élément t
    n'a vu que y_0..y_{t-1}.
    """
    from app.sales.coaching.agents.analyst.ts_engine import _hw_fit_forecast, _hw_gridsearch

    train = y[:n_train]
    if len(train) < 3 * period:
        return np.full(len(y), np.nan)
    params = _hw_gridsearch(train, period=period)
    fitted, _ = _hw_fit_forecast(y, period, 1,
                                 params["alpha"], params["beta"], params["gamma"])
    return np.maximum(0.0, fitted)


def _statistical_predictions(panel: pd.DataFrame, train_end: pd.Timestamp) -> pd.DataFrame:
    """Colonnes de prévision à un jour pour tous les baselines statistiques."""
    frames = []
    for store_id, grp in panel.groupby("store_id", sort=True):
        grp = grp.sort_values("date_only")
        idx = pd.DatetimeIndex(grp["date_only"])
        full = pd.date_range(idx.min(), idx.max(), freq="D")
        y = pd.Series(grp["ca_total"].to_numpy(dtype=float), index=idx).reindex(full).fillna(0.0)

        prev = y.shift(1)
        block = pd.DataFrame({
            "store_id":       store_id,
            "date":           full,
            "y":              y.to_numpy(),
            "seasonal_naive": y.shift(7).to_numpy(),
            "mean_7":         prev.rolling(7,  min_periods=3).mean().to_numpy(),
            "mean_28":        prev.rolling(28, min_periods=7).mean().to_numpy(),
        })

        n_train = int((full <= train_end).sum())
        block["holt_winters_seasonal7"] = _holt_winters_one_step(y.to_numpy(), n_train)
        frames.append(block)

    return pd.concat(frames, ignore_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# Rolling-origin
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class BacktestConfig:
    n_folds: int = 6
    fold_days: int = 28
    valid_days: int = 28          # queue de l'entraînement réservée à l'early stopping
    horizons: tuple[int, ...] = (1,)


def run_backtest(panel: pd.DataFrame,
                 config: BacktestConfig | None = None,
                 train_config: TrainingConfig | None = None) -> dict:
    """
    Exécute le backtest complet et retourne un rapport sérialisable.

    Le frame de features est construit une seule fois sur tout le panel : c'est
    licite car chaque ligne n'utilise que le passé de sa propre date. Le
    découpage temporel des plis suffit à garantir l'absence de fuite.
    """
    cfg = config or BacktestConfig()
    t_start = time.time()

    logger.info("[BACKTEST] construction des features…")
    feats = build_feature_frame(panel, horizons=cfg.horizons)
    if feats.empty:
        raise RuntimeError("frame de features vide — panel insuffisant")

    all_dates = np.sort(feats["date"].unique())
    test_span = cfg.n_folds * cfg.fold_days
    if len(all_dates) <= test_span + 120:
        raise RuntimeError(
            f"historique trop court : {len(all_dates)} jours pour {test_span} jours de test")

    fold_bounds = []
    for k in range(cfg.n_folds):
        end_i = len(all_dates) - k * cfg.fold_days
        start_i = end_i - cfg.fold_days
        fold_bounds.append((pd.Timestamp(all_dates[start_i]), pd.Timestamp(all_dates[end_i - 1])))
    fold_bounds.reverse()

    logger.info("[BACKTEST] %d plis de %d jours — test du %s au %s",
                cfg.n_folds, cfg.fold_days,
                fold_bounds[0][0].date(), fold_bounds[-1][1].date())

    predictions: list[pd.DataFrame] = []
    fold_reports: list[dict] = []

    for i, (fold_start, fold_end) in enumerate(fold_bounds, 1):
        t0 = time.time()
        train_end = fold_start - pd.Timedelta(days=1)

        train_mask = feats["date"] <= train_end
        train_all = feats[train_mask]
        valid_cut = train_end - pd.Timedelta(days=cfg.valid_days)
        train_df = train_all[train_all["date"] <= valid_cut]
        valid_df = train_all[train_all["date"] > valid_cut]

        test_df = feats[(feats["date"] >= fold_start) & (feats["date"] <= fold_end)].copy()

        model = GlobalSalesForecaster().train(train_df, valid_df, train_config)
        test_df["global_xgb"] = model.predict_frame(test_df)

        stats = _statistical_predictions(panel, train_end)
        test_df = test_df.merge(
            stats.drop(columns=["y"]), on=["store_id", "date"], how="left")
        test_df["fold"] = i
        predictions.append(test_df)

        naive_mae = float(np.nanmean(np.abs(
            test_df["seasonal_naive"].to_numpy(dtype=float) - test_df["y"].to_numpy(dtype=float))))
        fold_reports.append({
            "fold": i,
            "train_end": str(train_end.date()),
            "test_start": str(fold_start.date()),
            "test_end": str(fold_end.date()),
            "train_rows": len(train_df),
            "test_rows": len(test_df),
            "best_iteration": model.metadata.best_iteration,
            "models": {
                m: compute_metrics(test_df["y"], test_df[m], naive_mae)
                for m in MODEL_COLUMNS if m in test_df
            },
        })
        logger.info("[BACKTEST] pli %d/%d — %s→%s | WAPE global_xgb %.2f%% vs HW %.2f%% | %.0fs",
                    i, cfg.n_folds, fold_start.date(), fold_end.date(),
                    fold_reports[-1]["models"]["global_xgb"]["wape"] or -1,
                    fold_reports[-1]["models"]["holt_winters_seasonal7"]["wape"] or -1,
                    time.time() - t0)

    preds = pd.concat(predictions, ignore_index=True)

    naive_mae_all = float(np.nanmean(np.abs(
        preds["seasonal_naive"].to_numpy(dtype=float) - preds["y"].to_numpy(dtype=float))))
    overall = {m: compute_metrics(preds["y"], preds[m], naive_mae_all)
               for m in MODEL_COLUMNS if m in preds}

    per_store = _per_store_table(preds)
    best = min((m for m in overall if overall[m].get("wape") is not None),
               key=lambda m: overall[m]["wape"])

    return {
        "config": {"n_folds": cfg.n_folds, "fold_days": cfg.fold_days,
                   "valid_days": cfg.valid_days, "horizons": list(cfg.horizons)},
        "panel": {"rows": int(len(panel)), "stores": int(panel["store_id"].nunique()),
                  "start": str(panel["date_only"].min().date()),
                  "end": str(panel["date_only"].max().date())},
        "test_window": {"start": str(fold_bounds[0][0].date()),
                        "end": str(fold_bounds[-1][1].date()),
                        "rows": int(len(preds))},
        "overall": overall,
        "best_model": best,
        "folds": fold_reports,
        "per_store": per_store,
        "runtime_s": round(time.time() - t_start, 1),
    }


MODEL_COLUMNS = [
    "seasonal_naive", "mean_7", "mean_28",
    "holt_winters_seasonal7", "global_xgb",
]


def _per_store_table(preds: pd.DataFrame) -> list[dict]:
    """WAPE par boutique — pour repérer les boutiques où le global perd."""
    rows = []
    for store_id, grp in preds.groupby("store_id", sort=True):
        y = grp["y"].to_numpy(dtype=float)
        total = float(np.sum(np.abs(y)))
        if total <= 0:
            continue
        entry = {"store_id": store_id, "n": int(len(grp)), "ca_moyen": round(float(np.mean(y)), 1)}
        for m in MODEL_COLUMNS:
            if m in grp:
                p = grp[m].to_numpy(dtype=float)
                mask = np.isfinite(p)
                entry[m] = (round(float(np.sum(np.abs(p[mask] - y[mask])) / total * 100), 2)
                            if mask.any() else None)
        rows.append(entry)
    return rows
