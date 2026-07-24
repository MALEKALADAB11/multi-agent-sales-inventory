"""
Ingénierie des features pour le modèle global de CA journalier.

PRINCIPE DIRECTEUR — cible normalisée
────────────────────────────────────
Les boutiques vont de ~60 TND/jour (S31) à ~3 100 TND/jour (M15), soit un facteur
50. Un modèle global entraîné sur le CA brut consacrerait toute sa capacité aux
grosses boutiques et l'erreur absolue des petites disparaîtrait dans le bruit.

On apprend donc un **ratio** : `y_t / scale_t`, où `scale_t` est la médiane
mobile sur 28 jours **des seules valeurs observables** à la date de prévision.
Toutes les features de niveau sont divisées par ce même `scale`. Le modèle
apprend « ce jour vaut 1,3 fois le régime récent de sa boutique » — une
grandeur comparable d'une boutique à l'autre. La prévision est ensuite
redimensionnée : `ŷ_t = ratiô × scale_t`.

ABSENCE DE FUITE
────────────────
Pour un horizon `h`, la dernière observation utilisable est `y_{t-h}`. Tous les
lags, moyennes mobiles et l'échelle sont calculés par `shift(h)` ou plus, jamais
sur `y_t`. Le backtest rolling-origin recoupe cette garantie en ne montrant au
modèle que le passé strict de chaque pli.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ── Horizons servis par le modèle ────────────────────────────────────────────
# h=1 : le CA d'aujourd'hui (usage principal de l'Analyste)
# h=2 : le CA de demain (champ `tomorrow_forecast`)
HORIZONS = (1, 2)

SCALE_WINDOW = 28          # médiane mobile servant d'échelle
SCALE_MIN_PERIODS = 14     # en deçà, la boutique est jugée trop jeune
MIN_HISTORY_DAYS = 35      # historique minimal pour produire une ligne exploitable

FEATURE_COLUMNS = [
    # Horizon
    "horizon",
    # Lags récents, normalisés
    "lag_1", "lag_2", "lag_3", "lag_4", "lag_5", "lag_6", "lag_7",
    # Lags alignés sur le jour de semaine, normalisés
    "dow_lag_1", "dow_lag_2", "dow_lag_3", "dow_lag_4",
    "dow_mean_4", "dow_std_4",
    # Moyennes mobiles, normalisées
    "roll_mean_7", "roll_mean_14", "roll_mean_28", "roll_mean_56",
    "roll_std_7", "roll_std_28",
    "roll_min_7", "roll_max_7",
    # Dynamique
    "trend_7_28", "trend_14_56", "momentum_1_7",
    # Calendrier
    "dow", "day_of_month", "month", "week_of_year", "is_weekend",
    "is_month_start", "is_month_end",
    # Fourier — saisonnalité hebdomadaire puis annuelle
    "fourier_w_sin1", "fourier_w_cos1", "fourier_w_sin2", "fourier_w_cos2",
    "fourier_y_sin1", "fourier_y_cos1", "fourier_y_sin2", "fourier_y_cos2",
    # Régime de la boutique
    "log_scale", "maturity_days",
]

# Identité de la boutique, en catégoriel natif XGBoost. Sépare l'effet
# « boutique » de l'effet « régime de CA » : deux boutiques au même CA moyen
# n'ont pas le même profil hebdomadaire.
CATEGORICAL_COLUMNS = ["store_cat"]
ALL_FEATURES = FEATURE_COLUMNS + CATEGORICAL_COLUMNS

TARGET_COLUMN = "ratio"
SCALE_COLUMN = "scale"


# ══════════════════════════════════════════════════════════════════════════════
# Panel → frame d'entraînement
# ══════════════════════════════════════════════════════════════════════════════

def _calendar_features(idx: pd.DatetimeIndex) -> pd.DataFrame:
    """Features de date — identiques à l'entraînement et à l'inférence."""
    doy = idx.dayofyear.to_numpy(dtype=float)
    dow = idx.dayofweek.to_numpy(dtype=float)
    return pd.DataFrame(
        {
            "dow":            dow,
            "day_of_month":   idx.day.to_numpy(dtype=float),
            "month":          idx.month.to_numpy(dtype=float),
            "week_of_year":   idx.isocalendar().week.to_numpy(dtype=float),
            "is_weekend":     (dow >= 5).astype(float),
            "is_month_start": idx.is_month_start.astype(float),
            "is_month_end":   idx.is_month_end.astype(float),
            "fourier_w_sin1": np.sin(2 * np.pi * dow / 7),
            "fourier_w_cos1": np.cos(2 * np.pi * dow / 7),
            "fourier_w_sin2": np.sin(4 * np.pi * dow / 7),
            "fourier_w_cos2": np.cos(4 * np.pi * dow / 7),
            "fourier_y_sin1": np.sin(2 * np.pi * doy / 365.25),
            "fourier_y_cos1": np.cos(2 * np.pi * doy / 365.25),
            "fourier_y_sin2": np.sin(4 * np.pi * doy / 365.25),
            "fourier_y_cos2": np.cos(4 * np.pi * doy / 365.25),
        },
        index=idx,
    )


def _store_features(y: pd.Series, horizon: int) -> pd.DataFrame:
    """
    Features d'une boutique pour un horizon donné.

    `y` est indexé par date, en fréquence journalière continue. Toute la
    protection contre la fuite tient dans le `shift(horizon)` initial : `obs`
    ne contient que ce qui est connu à la date de prévision.
    """
    obs = y.shift(horizon)

    scale = obs.rolling(SCALE_WINDOW, min_periods=SCALE_MIN_PERIODS).median()
    # Une médiane nulle (boutique à l'arrêt) rendrait le ratio infini.
    scale = scale.where(scale > 1.0)

    out = pd.DataFrame(index=y.index)
    out["horizon"] = float(horizon)

    for k in range(1, 8):
        out[f"lag_{k}"] = y.shift(horizon + k - 1) / scale

    for k in range(1, 5):
        out[f"dow_lag_{k}"] = y.shift(7 * k) / scale
    dow_lags = out[[f"dow_lag_{k}" for k in range(1, 5)]]
    out["dow_mean_4"] = dow_lags.mean(axis=1)
    out["dow_std_4"]  = dow_lags.std(axis=1)

    for w in (7, 14, 28, 56):
        out[f"roll_mean_{w}"] = obs.rolling(w, min_periods=max(3, w // 3)).mean() / scale
    for w in (7, 28):
        out[f"roll_std_{w}"] = obs.rolling(w, min_periods=max(3, w // 3)).std() / scale
    out["roll_min_7"] = obs.rolling(7, min_periods=3).min() / scale
    out["roll_max_7"] = obs.rolling(7, min_periods=3).max() / scale

    out["trend_7_28"]   = out["roll_mean_7"]  / out["roll_mean_28"].replace(0, np.nan)
    out["trend_14_56"]  = out["roll_mean_14"] / out["roll_mean_56"].replace(0, np.nan)
    out["momentum_1_7"] = out["lag_1"]        / out["roll_mean_7"].replace(0, np.nan)

    out["log_scale"] = np.log1p(scale)
    out["maturity_days"] = np.arange(len(y), dtype=float)

    out[SCALE_COLUMN] = scale
    return out


def build_feature_frame(
    panel: pd.DataFrame,
    horizons: tuple[int, ...] = HORIZONS,
    observed_only: bool = True,
) -> pd.DataFrame:
    """
    Construit le frame d'entraînement à partir du panel journalier.

    `panel` : colonnes `store_id`, `date_only`, `ca_total`, et optionnellement
    `observed` (False pour une date reconstruite par réindexation).

    Retourne un frame indexé en entier avec `store_id`, `date`, `horizon`,
    `y`, `scale`, `ratio` et toutes les colonnes de `ALL_FEATURES`.
    """
    frames: list[pd.DataFrame] = []

    for store_id, grp in panel.groupby("store_id", sort=True):
        grp = grp.sort_values("date_only")
        idx = pd.DatetimeIndex(pd.to_datetime(grp["date_only"]))
        # Fréquence journalière continue : les lags doivent être des décalages
        # calendaires, pas des décalages de ligne.
        full_idx = pd.date_range(idx.min(), idx.max(), freq="D")
        y = pd.Series(grp["ca_total"].to_numpy(dtype=float), index=idx).reindex(full_idx)
        observed = y.notna()
        if "observed" in grp.columns:
            flag = pd.Series(grp["observed"].to_numpy(dtype=bool), index=idx).reindex(full_idx)
            observed = observed & flag.fillna(False)
        y = y.fillna(0.0)

        if len(y) < MIN_HISTORY_DAYS:
            continue

        cal = _calendar_features(full_idx)
        for h in horizons:
            feats = _store_features(y, h)
            block = pd.concat([feats, cal], axis=1)
            block["store_id"] = store_id
            block["date"] = full_idx
            block["y"] = y.to_numpy()
            block["observed"] = observed.to_numpy()
            frames.append(block)

    if not frames:
        return pd.DataFrame(
            columns=["store_id", "date", "y", SCALE_COLUMN, TARGET_COLUMN] + ALL_FEATURES)

    df = pd.concat(frames, ignore_index=True)
    df[TARGET_COLUMN] = df["y"] / df[SCALE_COLUMN]

    df = df[df[SCALE_COLUMN].notna() & np.isfinite(df[TARGET_COLUMN])]
    if observed_only:
        df = df[df["observed"]]
    # Une journée à 8× le régime récent est un artefact de saisie, pas un signal
    # apprenable : on écarte la queue extrême pour ne pas déformer l'arbre.
    df = df[df[TARGET_COLUMN].between(0.0, 8.0)]

    df[FEATURE_COLUMNS] = df[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)
    df["store_cat"] = df["store_id"].astype("category")
    return df.reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
# Inférence — une seule ligne, depuis la série brute
# ══════════════════════════════════════════════════════════════════════════════

def build_inference_row(
    values: list[float],
    last_date,
    horizon: int = 1,
    store_id: str | None = None,
    store_categories: list[str] | None = None,
) -> tuple[pd.DataFrame | None, float]:
    """
    Construit la ligne de features pour prédire `last_date + horizon` jours.

    `values` : CA journaliers consécutifs se terminant à `last_date` (inclus),
    tels que produits par `ts_engine._fetch_daily_series`.

    Retourne `(frame_1_ligne, scale)`, ou `(None, 0.0)` si l'historique est trop
    court — l'appelant doit alors se rabattre sur le moteur statistique.
    """
    if values is None or len(values) < MIN_HISTORY_DAYS:
        return None, 0.0

    last_ts = pd.Timestamp(last_date)
    idx = pd.date_range(end=last_ts, periods=len(values), freq="D")
    y = pd.Series(np.asarray(values, dtype=float), index=idx)

    # La cible se situe `horizon` jours après la dernière observation : on
    # prolonge l'index pour que shift(horizon) y ramène la bonne fenêtre.
    target_ts = last_ts + pd.Timedelta(days=horizon)
    extended = pd.date_range(end=target_ts, periods=len(values) + horizon, freq="D")
    y = y.reindex(extended).fillna(0.0)

    feats = _store_features(y, horizon)
    cal = _calendar_features(extended)
    row = pd.concat([feats, cal], axis=1).loc[[target_ts]]

    scale = float(row[SCALE_COLUMN].iloc[0]) if pd.notna(row[SCALE_COLUMN].iloc[0]) else 0.0
    if scale <= 1.0:
        return None, 0.0

    out = row[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)
    # Le catégoriel doit porter le MÊME jeu de catégories qu'à l'entraînement,
    # sinon les codes internes ne correspondent plus. Une boutique inconnue
    # devient NaN, que XGBoost traite comme une modalité manquante.
    if store_categories:
        cats = pd.CategoricalDtype(categories=store_categories)
        out["store_cat"] = pd.Series([store_id], index=out.index, dtype=cats)
    else:
        out["store_cat"] = pd.Series([None], index=out.index, dtype="category")
    return out, scale
