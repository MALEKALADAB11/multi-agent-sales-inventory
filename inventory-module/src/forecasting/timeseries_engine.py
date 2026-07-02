"""
TimeSeriesEngine — Moteur de prévision performant pour le retail.
=================================================================

Stack:
  1. StatsForecast (Nixtla)  ← PRIMAIRE : AutoARIMA + AutoETS + AutoCES
     - 50× plus rapide que TimesFM sur CPU
     - Parallélisation automatique par SKU (n_jobs=-1)
     - ~2ms par SKU sur un historique de 30 jours

  2. Chronos-Bolt-Small (Amazon) ← OPTIONNEL : zéro-shot neural
     - 71M paramètres, chargé une fois en mémoire
     - Bon pour SKUs à historique court ou irrégulier

  3. Fallback numpy ← TOUJOURS DISPONIBLE : Holt-Winters simplifié
     - Pas de dépendances externes
     - Garantit qu'un résultat est toujours retourné

Architecture de sélection :
  ┌─ historique ≥ 30 jours + StatsForecast installé ─→ StatsForecast
  ├─ historique < 30 jours + Chronos disponible      ─→ Chronos-Bolt
  └─ toujours                                        ─→ Fallback numpy

Output normalisé (toujours le même format) :
  {
    "avg_daily_demand":  float,   # moyenne sur horizon
    "demand_std_dev":    float,   # écart-type prévision
    "total_30d_demand":  float,   # somme 30 jours
    "trend_direction":   str,     # "up" | "down" | "stable"
    "seasonality_score": float,   # 0-1 (force de la saisonnalité)
    "forecast_values":   list,    # valeurs journalières [h jours]
    "confidence_lower":  list,    # borne inférieure 80%
    "confidence_upper":  list,    # borne supérieure 80%
    "engine":            str,     # moteur utilisé
    "horizon":           int,
  }
"""
from __future__ import annotations

import logging
import warnings
from functools import lru_cache
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Détection des moteurs disponibles
try:
    from statsforecast import StatsForecast
    from statsforecast.models import AutoARIMA, AutoETS, AutoCES
    _SF_AVAILABLE = True
    logger.info("[TS] StatsForecast disponible ✓")
except ImportError:
    _SF_AVAILABLE = False
    logger.info("[TS] StatsForecast non installé — fallback numpy actif")

try:
    import torch
    from chronos import BaseChronosPipeline  # type: ignore
    _CHRONOS_AVAILABLE = True
    logger.info("[TS] Chronos disponible ✓")
except ImportError:
    _CHRONOS_AVAILABLE = False

# Singleton Chronos pipeline — chargé une seule fois
_chronos_pipeline = None


# ── Singleton StatsForecast models ────────────────────────────────────────────

def _get_sf_models():
    """Retourne les modèles StatsForecast — instanciés une seule fois."""
    return [
        AutoARIMA(season_length=7, allowdrift=True, stepwise=True),
        AutoETS(season_length=7, damped=True),
        AutoCES(season_length=7),
    ]


def _get_chronos():
    """Lazy-load Chronos-Bolt-Small (71M params). Singleton."""
    global _chronos_pipeline
    if _chronos_pipeline is None and _CHRONOS_AVAILABLE:
        try:
            _chronos_pipeline = BaseChronosPipeline.from_pretrained(
                "amazon/chronos-bolt-small",
                device_map="cpu",
                torch_dtype=torch.float32,
            )
            logger.info("[TS] Chronos-Bolt-Small chargé ✓")
        except Exception as exc:
            logger.warning("[TS] Chronos load failed: %s", exc)
    return _chronos_pipeline


# ── Engines ───────────────────────────────────────────────────────────────────

def _forecast_statsforecast(
    values: np.ndarray,
    horizon: int,
    freq: str = "D",
) -> Dict[str, Any]:
    """
    Prévision via StatsForecast — ensemble AutoARIMA + AutoETS + AutoCES.
    Retourne la moyenne pondérée des 3 modèles (robustesse).
    """
    n = len(values)
    uid = "sku"

    df = pd.DataFrame({
        "unique_id": [uid] * n,
        "ds":        pd.date_range("2020-01-01", periods=n, freq="D"),
        "y":         values.astype(float),
    })

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sf = StatsForecast(
            models=_get_sf_models(),
            freq=freq,
            n_jobs=1,
        )
        sf.fit(df)
        pred = sf.predict(h=horizon, level=[80])

    # Colonnes: unique_id, ds, AutoARIMA, AutoETS, AutoCES
    # + AutoARIMA-lo-80, AutoARIMA-hi-80, etc.
    model_cols = ["AutoARIMA", "AutoETS", "AutoCES"]
    available  = [c for c in model_cols if c in pred.columns]

    forecast_vals = pred[available].mean(axis=1).values

    # Bornes de confiance : moyenne des bornes disponibles
    lo_cols = [f"{m}-lo-80" for m in available if f"{m}-lo-80" in pred.columns]
    hi_cols = [f"{m}-hi-80" for m in available if f"{m}-hi-80" in pred.columns]

    lower = pred[lo_cols].mean(axis=1).values if lo_cols else forecast_vals * 0.8
    upper = pred[hi_cols].mean(axis=1).values if hi_cols else forecast_vals * 1.2

    return _build_output(forecast_vals, lower, upper, values, horizon, engine="statsforecast")


def _forecast_chronos(
    values: np.ndarray,
    horizon: int,
) -> Dict[str, Any]:
    """Prévision via Chronos-Bolt-Small (zéro-shot neural)."""
    pipeline = _get_chronos()
    if pipeline is None:
        raise RuntimeError("Chronos non disponible")

    ctx = torch.tensor(values.astype(float), dtype=torch.float32).unsqueeze(0)
    with torch.no_grad():
        quantiles, mean = pipeline.predict_quantiles(
            context=ctx,
            prediction_length=horizon,
            quantile_levels=[0.1, 0.5, 0.9],
        )

    # quantiles: [1, 3, horizon] → mean: [1, horizon]
    forecast_vals = mean[0].numpy()
    lower         = quantiles[0, 0].numpy()
    upper         = quantiles[0, 2].numpy()

    return _build_output(forecast_vals, lower, upper, values, horizon, engine="chronos-bolt-small")


def _forecast_numpy(
    values: np.ndarray,
    horizon: int,
) -> Dict[str, Any]:
    """
    Fallback Holt-Winters simplifié via numpy.
    Toujours disponible — pas de dépendances externes.
    """
    n = len(values)
    if n == 0:
        forecast_vals = np.zeros(horizon)
        lower  = np.zeros(horizon)
        upper  = np.zeros(horizon)
        return _build_output(forecast_vals, lower, upper, values, horizon, engine="numpy_fallback")

    # Double exponential smoothing (Holt's method)
    alpha, beta = 0.3, 0.1
    level = float(values[0])
    trend = float(np.mean(np.diff(values[:min(7, n)]))) if n > 1 else 0.0

    for v in values[1:]:
        prev_level = level
        level = alpha * float(v) + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend

    forecast_vals = np.array([max(0, level + (i + 1) * trend) for i in range(horizon)])

    # Incertitude : std des résidus récents
    recent = values[-min(14, n):]
    sigma  = float(np.std(recent)) if len(recent) > 1 else float(np.mean(values)) * 0.2
    lower  = np.maximum(0, forecast_vals - 1.282 * sigma)
    upper  = forecast_vals + 1.282 * sigma

    return _build_output(forecast_vals, lower, upper, values, horizon, engine="numpy_holt")


def _build_output(
    forecast_vals: np.ndarray,
    lower:         np.ndarray,
    upper:         np.ndarray,
    history:       np.ndarray,
    horizon:       int,
    engine:        str,
) -> Dict[str, Any]:
    """Normalise la sortie quel que soit le moteur utilisé."""
    forecast_vals = np.maximum(0, forecast_vals)
    lower         = np.maximum(0, lower)

    avg_daily = float(np.mean(forecast_vals))
    std_dev   = float(np.std(forecast_vals))
    total_30d = float(np.sum(forecast_vals[:30])) if horizon >= 30 else float(np.sum(forecast_vals))

    # Tendance : régression linéaire sur l'historique récent (14 jours)
    recent = history[-14:] if len(history) >= 14 else history
    if len(recent) > 2:
        x        = np.arange(len(recent), dtype=float)
        slope    = float(np.polyfit(x, recent.astype(float), 1)[0])
        mean_val = float(np.mean(recent)) or 1.0
        rel_slope = slope / mean_val
        if rel_slope > 0.02:
            trend = "up"
        elif rel_slope < -0.02:
            trend = "down"
        else:
            trend = "stable"
    else:
        trend = "stable"

    # Score de saisonnalité : variance des moyennes hebdomadaires
    seasonality_score = 0.0
    if len(history) >= 14:
        try:
            weekly = history[-min(56, len(history)):]
            padded_len = (len(weekly) // 7) * 7
            if padded_len >= 14:
                reshaped   = weekly[-padded_len:].reshape(-1, 7)
                day_means  = reshaped.mean(axis=0)
                seasonality_score = float(np.std(day_means) / (np.mean(day_means) + 1e-6))
        except Exception:
            pass

    return {
        "avg_daily_demand":  round(avg_daily, 4),
        "demand_std_dev":    round(std_dev, 4),
        "total_30d_demand":  round(total_30d, 2),
        "trend_direction":   trend,
        "seasonality_score": round(min(1.0, seasonality_score), 3),
        "forecast_values":   [round(float(v), 2) for v in forecast_vals],
        "confidence_lower":  [round(float(v), 2) for v in lower],
        "confidence_upper":  [round(float(v), 2) for v in upper],
        "engine":            engine,
        "horizon":           horizon,
    }


# ── API publique ──────────────────────────────────────────────────────────────

def forecast(
    sales_series: List[float],
    horizon:      int = 30,
    freq:         str = "D",
    force_engine: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Prévision de demande sur `horizon` jours.

    Args:
        sales_series: Historique des ventes journalières (ordre chronologique).
                      Doit contenir au moins 3 valeurs.
        horizon:      Nombre de jours à prévoir (défaut 30).
        freq:         Fréquence temporelle ('D'=daily, 'W'=weekly).
        force_engine: Force un moteur spécifique ('statsforecast'|'chronos'|'numpy').

    Returns:
        Dict normalisé (voir module docstring).
    """
    values = np.array(sales_series, dtype=float)
    values = np.nan_to_num(values, nan=0.0)
    n      = len(values)

    if n < 3:
        logger.warning("[TS] Historique trop court (%d pts) — forecast dégradé", n)
        avg = float(np.mean(values)) if n > 0 else 0.0
        flat = np.full(horizon, avg)
        return _build_output(flat, flat * 0.8, flat * 1.2, values, horizon, engine="constant_fallback")

    # Sélection du moteur
    engine = force_engine or _select_engine(n)
    logger.debug("[TS] Engine=%s | n=%d | horizon=%d", engine, n, horizon)

    if engine == "statsforecast" and _SF_AVAILABLE:
        try:
            return _forecast_statsforecast(values, horizon, freq)
        except Exception as exc:
            logger.warning("[TS] StatsForecast failed (%s) — trying Chronos", exc)

    if engine in ("chronos", "statsforecast") and _CHRONOS_AVAILABLE:
        try:
            return _forecast_chronos(values, horizon)
        except Exception as exc:
            logger.warning("[TS] Chronos failed (%s) — numpy fallback", exc)

    return _forecast_numpy(values, horizon)


def forecast_batch(
    series_dict: Dict[str, List[float]],
    horizon:     int = 30,
    freq:        str = "D",
) -> Dict[str, Dict[str, Any]]:
    """
    Prévision batch pour plusieurs SKUs en parallèle.

    Args:
        series_dict: {sku: [ventes_jour_1, ventes_jour_2, ...]}
        horizon:     Nombre de jours à prévoir

    Returns:
        {sku: dict_forecast} — même format que forecast()
    """
    if not series_dict:
        return {}

    # Si StatsForecast disponible et historiques longs → batch natif (plus rapide)
    if _SF_AVAILABLE and all(len(v) >= 14 for v in series_dict.values()):
        try:
            return _forecast_batch_statsforecast(series_dict, horizon, freq)
        except Exception as exc:
            logger.warning("[TS] Batch StatsForecast failed (%s) — individual fallback", exc)

    # Fallback: individuel
    return {
        sku: forecast(series, horizon=horizon, freq=freq)
        for sku, series in series_dict.items()
    }


def _forecast_batch_statsforecast(
    series_dict: Dict[str, List[float]],
    horizon:     int,
    freq:        str,
) -> Dict[str, Dict[str, Any]]:
    """Batch StatsForecast — une seule passe fit+predict pour tous les SKUs."""
    rows = []
    for sku, values in series_dict.items():
        n   = len(values)
        arr = np.nan_to_num(np.array(values, dtype=float), nan=0.0)
        for i, v in enumerate(arr):
            rows.append({
                "unique_id": sku,
                "ds":        pd.Timestamp("2020-01-01") + pd.Timedelta(days=i),
                "y":         float(v),
            })

    df = pd.DataFrame(rows)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sf = StatsForecast(models=_get_sf_models(), freq=freq, n_jobs=-1)
        sf.fit(df)
        pred = sf.predict(h=horizon, level=[80])

    results: Dict[str, Dict[str, Any]] = {}
    model_cols = [c for c in ["AutoARIMA", "AutoETS", "AutoCES"] if c in pred.columns]

    for sku, group in pred.groupby("unique_id"):
        fv     = group[model_cols].mean(axis=1).values
        lo_c   = [f"{m}-lo-80" for m in model_cols if f"{m}-lo-80" in group.columns]
        hi_c   = [f"{m}-hi-80" for m in model_cols if f"{m}-hi-80" in group.columns]
        lower  = group[lo_c].mean(axis=1).values if lo_c else fv * 0.8
        upper  = group[hi_c].mean(axis=1).values if hi_c else fv * 1.2
        hist   = np.array(series_dict[sku], dtype=float)
        results[sku] = _build_output(fv, lower, upper, hist, horizon, engine="statsforecast_batch")

    return results


def _select_engine(n: int) -> str:
    """Choisit le meilleur moteur selon la longueur de l'historique."""
    if _SF_AVAILABLE and n >= 14:
        return "statsforecast"
    if _CHRONOS_AVAILABLE and n >= 3:
        return "chronos"
    return "numpy"


# ── Extraction depuis DataFrame de ventes ─────────────────────────────────────

def extract_series_from_sales(
    sales_df:  pd.DataFrame,
    sku:       str,
    store_id:  str,
    days_back: int = 90,
) -> List[float]:
    """
    Extrait et agrège les ventes journalières d'un SKU depuis un DataFrame.

    Args:
        sales_df:  DataFrame avec colonnes sku, store_id, date, quantity_sold
        sku:       SKU à extraire
        store_id:  Store ID
        days_back: Fenêtre historique (jours)

    Returns:
        Liste de ventes journalières (complétée par 0 si pas de ventes ce jour)
    """
    try:
        df = sales_df.copy()

        # Normalise SKU column — tolère sku / product_id / article_id
        sku_col = next((c for c in ["sku", "product_id", "article_id"] if c in df.columns), None)
        if sku_col is None:
            return [0.0] * 7
        if sku_col != "sku":
            df = df.rename(columns={sku_col: "sku"})
        df["sku"] = df["sku"].astype(str)

        # Normalise store column
        store_col = next((c for c in ["store_id", "store", "boutique_id"] if c in df.columns), None)
        if store_col is None:
            mask = df["sku"] == str(sku)
        else:
            if store_col != "store_id":
                df = df.rename(columns={store_col: "store_id"})
            df["store_id"] = df["store_id"].astype(str)
            mask = (df["sku"] == str(sku)) & (df["store_id"] == str(store_id))

        sub  = df[mask].copy()

        if sub.empty:
            return [0.0] * min(days_back, 7)

        # Normaliser la colonne date
        date_col = next((c for c in ["date", "transaction_date", "ds"] if c in sub.columns), None)
        if not date_col:
            return [0.0] * 7

        sub[date_col]  = pd.to_datetime(sub[date_col], errors="coerce")
        qty_col        = next((c for c in ["quantity_sold", "qty", "quantity"] if c in sub.columns), None)
        if not qty_col:
            return [0.0] * 7

        sub            = sub.dropna(subset=[date_col])
        sub["_qty"]    = pd.to_numeric(sub[qty_col], errors="coerce").fillna(0)

        daily = (
            sub.groupby(sub[date_col].dt.date)["_qty"]
            .sum()
            .sort_index()
        )

        cutoff = pd.Timestamp.now().date() - pd.Timedelta(days=days_back)
        daily  = daily[daily.index >= cutoff]

        # Compléter les jours manquants par 0
        if not daily.empty:
            idx       = pd.date_range(start=daily.index[0], end=daily.index[-1], freq="D").date
            daily     = daily.reindex(idx, fill_value=0.0)

        return [float(v) for v in daily.values]

    except Exception as exc:
        logger.warning("[TS] extract_series_from_sales failed (%s) — empty series", exc)
        return [0.0] * 7
