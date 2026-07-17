"""Trained correction model for the demand sensing layer."""
from typing import Optional
import xgboost as xgb
import numpy as np
from .sensing_features import FEATURE_COLUMNS


class SensingModel:
    def __init__(self, booster: Optional[xgb.Booster] = None):
        self.booster = booster

    def train(self, training_df, n_estimators=300, num_leaves=31, learning_rate=0.05):
        X = training_df[FEATURE_COLUMNS]
        y = training_df["actual_demand"]
        model = xgb.XGBRegressor(
            n_estimators=n_estimators, max_leaves=num_leaves, learning_rate=learning_rate
        )
        model.fit(X, y)
        self.booster = model.get_booster()
        return self

    def predict(self, features: dict, baseline_demand: float) -> float:
        X = np.array([[features[c] for c in FEATURE_COLUMNS]])
        dmatrix = xgb.DMatrix(X, feature_names=FEATURE_COLUMNS)
        pred = float(self.booster.predict(dmatrix)[0])
        return max(0.0, min(pred, baseline_demand * 3 if baseline_demand > 0 else pred))

    def save(self, path: str):
        self.booster.save_model(path)

    @classmethod
    def load(cls, path: str) -> "SensingModel":
        booster = xgb.Booster()
        booster.load_model(path)
        return cls(booster=booster)