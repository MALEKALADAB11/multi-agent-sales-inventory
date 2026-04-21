"""
Time Series Model avec ARIMA
Prévisions EOD optimisées - WINDOWS COMPATIBLE
"""

from typing import Dict, Any
import pandas as pd
import numpy as np
from datetime import datetime
from statsmodels.tsa.arima.model import ARIMA
import warnings

warnings.filterwarnings('ignore')

class TimeSeriesPredictor:
    """Modèle de prévision série temporelle avec ARIMA"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.model_type = "arima"
        
    def predict_eod(self, df: pd.DataFrame, current_total: float) -> Dict[str, Any]:
        """Prédit les ventes jusqu'à la fin du jour avec ARIMA"""
        
        try:
            if len(df) < 3:
                return self._simple_extrapolation(current_total)
            
            return self._predict_arima(df, current_total)
            
        except Exception as e:
            print(f"⚠️ ARIMA error: {e}")
            return self._simple_extrapolation(current_total)
    
    def _predict_arima(self, df: pd.DataFrame, current_total: float) -> Dict[str, Any]:
        """Prévision avec ARIMA(1,0,1)"""
        
        try:
            # Entraîner ARIMA sur les montants horaires
            values = df['y'].values if isinstance(df['y'], pd.Series) else df['y']
            model = ARIMA(values, order=(1, 0, 1))
            results = model.fit()
            
            # Prédire les heures restantes du jour
            now = datetime.now()
            hours_remaining = 24 - now.hour
            
            if hours_remaining <= 0:
                hours_remaining = 1
            
            # Prévisions
            forecast = results.get_forecast(steps=hours_remaining)
            predicted_values = forecast.predicted_mean
            
            # Convertir en numpy si nécessaire
            if isinstance(predicted_values, pd.Series):
                predicted_values = predicted_values.values
            
            # Total prévu
            predicted_remaining = np.sum(predicted_values)
            predicted_total = current_total + predicted_remaining
            
            # Confiance basée sur nombre de points
            if len(df) >= 8:
                confidence = 0.85
            elif len(df) >= 5:
                confidence = 0.75
            else:
                confidence = 0.65
            
            return {
                "predicted_amount": float(max(0, predicted_total)),
                "confidence_low": float(max(0, predicted_total * 0.90)),
                "confidence_high": float(max(0, predicted_total * 1.10)),
                "confidence": confidence,
                "model": "ARIMA(1,0,1)",
                "status": "success"
            }
            
        except Exception as e:
            print(f"⚠️ ARIMA fit failed: {e}")
            return self._simple_extrapolation(current_total)
            
        except Exception as e:
            print(f"⚠️ ARIMA fit failed: {e}")
            return self._simple_extrapolation(current_total)
    
    def _simple_extrapolation(self, current_total: float) -> Dict[str, Any]:
        """Extrapolation simple si pas assez de données"""
        
        now = datetime.now()
        hours_elapsed = now.hour if now.hour > 0 else 1
        hours_remaining = 24 - now.hour
        
        # Projection linéaire
        average_per_hour = current_total / hours_elapsed
        predicted_total = current_total + (average_per_hour * hours_remaining)
        
        return {
            "predicted_amount": float(max(0, predicted_total)),
            "confidence_low": float(max(0, predicted_total * 0.85)),
            "confidence_high": float(max(0, predicted_total * 1.15)),
            "confidence": 0.70,
            "model": "SimpleExtrapolation",
            "status": "success"
        }