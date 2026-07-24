"""
Modèle global de prévision du CA journalier — XGBoost sur cible normalisée.

Un seul booster sert les 154 boutiques. L'objectif d'entraînement est
`reg:absoluteerror` : minimiser l'erreur absolue sur le ratio revient à
minimiser le WAPE une fois remis à l'échelle, donc la métrique optimisée est
celle qu'on publie. Un objectif quadratique tirerait les prévisions vers le haut
sur les journées exceptionnelles.

Intervalle de prédiction : deux boosters quantiles (10 % / 90 %) entraînés sur
la même matrice, ce qui donne un IC 80 % conditionné aux features plutôt qu'une
bande constante dérivée du WAPE global.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import xgboost as xgb

from .features import (
    ALL_FEATURES, CATEGORICAL_COLUMNS, FEATURE_COLUMNS,
    SCALE_COLUMN, TARGET_COLUMN, build_inference_row,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "sales_daily_v1"
DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[2] / "sales" / "models" / "forecast"


@dataclass
class TrainingConfig:
    # L'early stopping tranche : sur le panel complet, le meilleur tour se situe
    # au-delà de 1 200, donc plafonner plus bas amputerait le modèle.
    n_estimators: int = 2000
    learning_rate: float = 0.04
    max_depth: int = 7
    min_child_weight: float = 12.0
    subsample: float = 0.85
    colsample_bytree: float = 0.75
    reg_lambda: float = 2.0
    reg_alpha: float = 0.5
    early_stopping_rounds: int = 60
    quantile_alphas: tuple[float, float] = (0.1, 0.9)
    seed: int = 42
    # `reg:absoluteerror` optimise le WAPE mais prédit la médiane
    # conditionnelle : sur des ventes à distribution asymétrique, la médiane est
    # sous la moyenne et le modèle sous-prévoit structurellement. Un quantile
    # légèrement au-dessus de 0,5, ou la calibration ci-dessous, corrige ce biais.
    objective: str = "reg:absoluteerror"
    objective_quantile: float = 0.5
    calibrate: bool = True


@dataclass
class ModelMetadata:
    version: str = MODEL_VERSION
    features: list[str] = field(default_factory=lambda: list(ALL_FEATURES))
    store_categories: list[str] = field(default_factory=list)
    trained_at: str = ""
    train_rows: int = 0
    train_stores: int = 0
    train_start: str = ""
    train_end: str = ""
    best_iteration: int = 0
    calibration: float = 1.0
    config: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)


class GlobalSalesForecaster:
    """Modèle global chargeable en inférence. Ne lève jamais côté prédiction."""

    def __init__(self, booster: Optional[xgb.Booster] = None,
                 quantile_boosters: Optional[dict[str, xgb.Booster]] = None,
                 metadata: Optional[ModelMetadata] = None):
        self.booster = booster
        self.quantile_boosters = quantile_boosters or {}
        self.metadata = metadata or ModelMetadata()

    # ── Entraînement ─────────────────────────────────────────────────────────

    def train(self, train_df: pd.DataFrame, valid_df: Optional[pd.DataFrame] = None,
              config: Optional[TrainingConfig] = None) -> "GlobalSalesForecaster":
        cfg = config or TrainingConfig()

        X = train_df[ALL_FEATURES]
        y = train_df[TARGET_COLUMN]
        # Pondérer par l'échelle aligne l'optimisation sur le WAPE en TND :
        # sans cela, une erreur de 0,1 sur une boutique à 60 TND pèserait autant
        # qu'une erreur de 0,1 sur une boutique à 3 000 TND.
        w = train_df[SCALE_COLUMN].to_numpy(dtype=float)

        fit_kwargs: dict = {}
        eval_set = None
        if valid_df is not None and len(valid_df):
            eval_set = [(valid_df[ALL_FEATURES], valid_df[TARGET_COLUMN])]
            fit_kwargs["sample_weight_eval_set"] = [valid_df[SCALE_COLUMN].to_numpy(dtype=float)]

        obj_kwargs: dict = {"objective": cfg.objective}
        if cfg.objective == "reg:quantileerror":
            obj_kwargs["quantile_alpha"] = cfg.objective_quantile

        model = xgb.XGBRegressor(
            **obj_kwargs,
            n_estimators=cfg.n_estimators,
            learning_rate=cfg.learning_rate,
            max_depth=cfg.max_depth,
            min_child_weight=cfg.min_child_weight,
            subsample=cfg.subsample,
            colsample_bytree=cfg.colsample_bytree,
            reg_lambda=cfg.reg_lambda,
            reg_alpha=cfg.reg_alpha,
            random_state=cfg.seed,
            n_jobs=-1,
            eval_metric="mae",
            enable_categorical=True,
            tree_method="hist",
            early_stopping_rounds=cfg.early_stopping_rounds if eval_set else None,
        )
        model.fit(X, y, sample_weight=w, eval_set=eval_set, verbose=False, **fit_kwargs)

        self.booster = model.get_booster()
        best_iter = int(getattr(model, "best_iteration", cfg.n_estimators - 1) or 0)

        # Bornes de l'intervalle : mêmes features, objectif pinball.
        n_q = max(200, best_iter + 1)
        self.quantile_boosters = {}
        for alpha in cfg.quantile_alphas:
            q = xgb.XGBRegressor(
                objective="reg:quantileerror", quantile_alpha=alpha,
                n_estimators=n_q, learning_rate=cfg.learning_rate,
                max_depth=cfg.max_depth, min_child_weight=cfg.min_child_weight,
                subsample=cfg.subsample, colsample_bytree=cfg.colsample_bytree,
                random_state=cfg.seed, n_jobs=-1,
                enable_categorical=True, tree_method="hist",
            )
            q.fit(X, y, sample_weight=w, verbose=False)
            self.quantile_boosters[f"q{alpha}"] = q.get_booster()

        # Calibration : le facteur qui annule le biais agrégé en TND sur la
        # validation. Mesuré hors échantillon d'apprentissage, donc honnête.
        calibration = 1.0
        if cfg.calibrate and valid_df is not None and len(valid_df):
            sc = valid_df[SCALE_COLUMN].to_numpy(dtype=float)
            actual = float(np.sum(valid_df[TARGET_COLUMN].to_numpy(dtype=float) * sc))
            pred = float(np.sum(self.predict_ratio(valid_df) * sc))
            if pred > 0:
                calibration = float(np.clip(actual / pred, 0.8, 1.25))
                logger.info("[GLOBAL-FC] calibration = %.4f", calibration)

        self.metadata = ModelMetadata(
            trained_at=pd.Timestamp.utcnow().isoformat(timespec="seconds"),
            train_rows=len(train_df),
            train_stores=int(train_df["store_id"].nunique()) if "store_id" in train_df else 0,
            train_start=str(train_df["date"].min().date()) if "date" in train_df else "",
            train_end=str(train_df["date"].max().date()) if "date" in train_df else "",
            best_iteration=best_iter,
            calibration=calibration,
            config=asdict(cfg),
        )
        logger.info("[GLOBAL-FC] entraîné — %d lignes, %d boutiques, best_iter=%d",
                    self.metadata.train_rows, self.metadata.train_stores, best_iter)
        return self

    # ── Prédiction ───────────────────────────────────────────────────────────

    def predict_ratio(self, X: pd.DataFrame) -> np.ndarray:
        dm = xgb.DMatrix(X[ALL_FEATURES], feature_names=list(ALL_FEATURES),
                         enable_categorical=True)
        return np.maximum(0.0, self.booster.predict(dm))

    def predict_frame(self, df: pd.DataFrame) -> np.ndarray:
        """Prévision en TND pour un frame déjà featurisé (contient `scale`)."""
        return (self.predict_ratio(df) * df[SCALE_COLUMN].to_numpy(dtype=float)
                * self.metadata.calibration)

    def forecast(self, values: list[float], last_date, horizon: int = 1,
                 store_id: Optional[str] = None) -> Optional[dict]:
        """
        Prévision pour une boutique depuis sa série brute.

        Retourne `None` si l'historique est insuffisant ou si le modèle n'est pas
        chargé — l'appelant se rabat alors sur le moteur statistique.
        """
        if self.booster is None:
            return None
        try:
            row, scale = build_inference_row(
                values, last_date, horizon=horizon, store_id=store_id,
                store_categories=self.metadata.store_categories or None,
            )
            if row is None:
                return None
            ratio = float(self.predict_ratio(row)[0]) * self.metadata.calibration
            point = round(max(0.0, ratio * scale), 2)

            lo = hi = None
            if self.quantile_boosters:
                dm = xgb.DMatrix(row[ALL_FEATURES], feature_names=list(ALL_FEATURES),
                                 enable_categorical=True)
                qs = {k: float(b.predict(dm)[0]) * scale
                      for k, b in self.quantile_boosters.items()}
                lo = round(max(0.0, min(qs.values())), 2)
                hi = round(max(qs.values()), 2)
                # Le point doit rester dans son propre intervalle.
                lo, hi = min(lo, point), max(hi, point)

            return {"forecast": point, "ci_low": lo, "ci_high": hi,
                    "ratio": round(ratio, 4), "scale": round(scale, 2),
                    "engine": f"global_xgb_{self.metadata.version}"}
        except Exception as e:
            logger.warning("[GLOBAL-FC] prédiction impossible (%s) — repli statistique", e)
            return None

    # ── Persistance ──────────────────────────────────────────────────────────

    def save(self, model_dir: Path | str = DEFAULT_MODEL_DIR) -> Path:
        model_dir = Path(model_dir)
        model_dir.mkdir(parents=True, exist_ok=True)
        self.booster.save_model(str(model_dir / f"{self.metadata.version}.ubj"))
        for name, b in self.quantile_boosters.items():
            b.save_model(str(model_dir / f"{self.metadata.version}.{name}.ubj"))
        meta_path = model_dir / f"{self.metadata.version}.meta.json"
        meta_path.write_text(json.dumps(asdict(self.metadata), indent=2, ensure_ascii=False),
                             encoding="utf-8")
        logger.info("[GLOBAL-FC] modèle écrit dans %s", model_dir)
        return model_dir

    @classmethod
    def load(cls, model_dir: Path | str = DEFAULT_MODEL_DIR,
             version: str = MODEL_VERSION) -> Optional["GlobalSalesForecaster"]:
        model_dir = Path(model_dir)
        main = model_dir / f"{version}.ubj"
        if not main.exists():
            return None
        try:
            booster = xgb.Booster()
            booster.load_model(str(main))

            quantiles: dict[str, xgb.Booster] = {}
            for path in model_dir.glob(f"{version}.q*.ubj"):
                b = xgb.Booster()
                b.load_model(str(path))
                quantiles[path.stem.split(".")[-1]] = b

            meta = ModelMetadata(version=version)
            meta_path = model_dir / f"{version}.meta.json"
            if meta_path.exists():
                meta = ModelMetadata(**json.loads(meta_path.read_text(encoding="utf-8")))
            return cls(booster=booster, quantile_boosters=quantiles, metadata=meta)
        except Exception as e:
            logger.warning("[GLOBAL-FC] chargement impossible (%s) — moteur statistique seul", e)
            return None


# ── Singleton d'inférence ────────────────────────────────────────────────────

_CACHED: Optional[GlobalSalesForecaster] = None
_LOAD_ATTEMPTED = False


def get_global_forecaster() -> Optional[GlobalSalesForecaster]:
    """Charge le modèle une fois par process. `None` si absent : repli attendu."""
    global _CACHED, _LOAD_ATTEMPTED
    if not _LOAD_ATTEMPTED:
        _LOAD_ATTEMPTED = True
        _CACHED = GlobalSalesForecaster.load()
        if _CACHED is None:
            logger.info("[GLOBAL-FC] aucun modèle entraîné sur disque — moteur statistique seul")
    return _CACHED
