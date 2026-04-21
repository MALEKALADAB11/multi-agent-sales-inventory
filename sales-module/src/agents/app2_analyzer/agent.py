"""
APP2 - Sales Analyzer Agent
Utilise une série temporelle (ARIMA) pour prédire les tendances

Fonctionnalités:
1. Reçoit flux POS en temps réel
2. Calcule l'écart objectif
3. Appelle modèle série temporelle → prévision EOD
4. Détecte l'urgence: HAUTE/MOYENNE/FAIBLE
5. Génère actions recommandées
6. Enregistre métriques pour MLflow
"""

from typing import Dict, Any, List
from datetime import datetime, timedelta
import pandas as pd
import json
from .detection_engine import DetectionEngine
from .time_series_model import TimeSeriesPredictor

class App2Analyzer:
    """Agent Analyste avec modèle série temporelle ARIMA"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.detection_engine = DetectionEngine(config)
        self.time_series_model = TimeSeriesPredictor(config)
        
        # Données du jour
        self.daily_transactions = []
        self.daily_target = 0
        self.current_total = 0
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.analysis_history = []
        
    def receive_pos_stream(self, transaction: Dict) -> Dict[str, Any]:
        """
        1️⃣ REÇOIT FLUX POS EN TEMPS RÉEL
        Valide et enregistre chaque transaction
        """
        
        if not self._validate_transaction(transaction):
            return {
                "status": "error",
                "message": "Invalid transaction format",
                "transaction_id": transaction.get("id", "unknown"),
                "timestamp": datetime.now().isoformat()
            }
        
        # Ajouter à la liste
        self.daily_transactions.append(transaction)
        self.current_total += transaction.get("amount", 0)
        
        return {
            "status": "success",
            "transaction_id": transaction.get("id"),
            "amount": transaction.get("amount"),
            "total_received": self.current_total,
            "transaction_count": len(self.daily_transactions),
            "average_transaction": self.current_total / len(self.daily_transactions),
            "timestamp": datetime.now().isoformat()
        }
    
    def calculate_objective_gap(self) -> Dict[str, Any]:
        """
        2️⃣ CALCULE L'ÉCART OBJECTIF
        Compare le total actuel vs l'objectif
        """
        
        if self.daily_target == 0:
            return {
                "status": "error",
                "message": "Daily target not set",
                "timestamp": datetime.now().isoformat()
            }
        
        # Écart absolu et pourcentage
        gap = self.current_total - self.daily_target
        gap_percentage = (gap / self.daily_target * 100) if self.daily_target > 0 else 0
        
        # Temps écoulé du jour
        now = datetime.now()
        hours_elapsed = now.hour + now.minute / 60
        hours_remaining = 24 - hours_elapsed
        
        # Progression attendue vs réelle
        expected_progress = (hours_elapsed / 24) * self.daily_target
        actual_progress = (self.current_total / self.daily_target) * 100 if self.daily_target > 0 else 0
        
        return {
            "status": "success",
            "current_total": self.current_total,
            "daily_target": self.daily_target,
            "gap": gap,
            "gap_percentage": gap_percentage,
            "gap_absolute_percentage": (gap / self.daily_target * 100) if self.daily_target > 0 else 0,
            "hours_elapsed": hours_elapsed,
            "hours_remaining": hours_remaining,
            "expected_progress_amount": expected_progress,
            "actual_progress_percentage": actual_progress,
            "progress_variance": actual_progress - (hours_elapsed / 24 * 100),
            "transactions_count": len(self.daily_transactions),
            "avg_transaction_value": self.current_total / len(self.daily_transactions) if self.daily_transactions else 0,
            "timestamp": datetime.now().isoformat()
        }
    
    def predict_eod_timeseries(self) -> Dict[str, Any]:
        """
        3️⃣ APPELLE MODÈLE SÉRIE TEMPORELLE → PRÉVISION EOD
        Utilise ARIMA pour prévoir les ventes de fin de jour
        """
        
        if not self.daily_transactions:
            return {
                "status": "error",
                "message": "No transactions available",
                "timestamp": datetime.now().isoformat()
            }
        
        # Préparer les données pour le modèle
        df = self._prepare_timeseries_data()
        
        # Appeler le modèle de prévision
        forecast = self.time_series_model.predict_eod(df, self.current_total)
        
        # Calculer les métriques supplémentaires
        predicted_remaining = forecast.get("predicted_amount", 0) - self.current_total
        
        return {
            "status": "success",
            "current_amount": self.current_total,
            "predicted_eod": forecast.get("predicted_amount"),
            "predicted_remaining": max(0, predicted_remaining),
            "confidence_interval_low": forecast.get("confidence_low"),
            "confidence_interval_high": forecast.get("confidence_high"),
            "confidence_level": forecast.get("confidence"),
            "confidence_percentage": f"{forecast.get('confidence', 0)*100:.0f}%",
            "model_used": forecast.get("model"),
            "timestamp": datetime.now().isoformat()
        }
    
    def detect_urgency(self) -> Dict[str, Any]:
        """
        4️⃣ DÉTECTE L'URGENCE: HAUTE/MOYENNE/FAIBLE
        Basé sur l'écart, la prévision et le temps restant
        """
        
        # Obtenir les analyses
        gap_analysis = self.calculate_objective_gap()
        eod_forecast = self.predict_eod_timeseries()
        
        if gap_analysis.get("status") == "error" or eod_forecast.get("status") == "error":
            return {
                "status": "error",
                "message": "Cannot calculate urgency",
                "timestamp": datetime.now().isoformat()
            }
        
        gap_percentage = gap_analysis.get("gap_percentage", 0)
        predicted_eod = eod_forecast.get("predicted_eod", 0)
        target = self.daily_target
        hours_remaining = gap_analysis.get("hours_remaining", 0)
        
        # Calculer le taux d'atteinte prévu
        expected_achievement = (predicted_eod / target * 100) if target > 0 else 0
        
        # Déterminer le niveau d'urgence
        urgency_level = self._determine_urgency_level(
            gap_percentage,
            expected_achievement,
            hours_remaining
        )
        
        # Score d'urgence (0-100)
        urgency_score = self._calculate_urgency_score(
            gap_percentage,
            expected_achievement,
            hours_remaining
        )
        
        # Actions recommandées détaillées
        actions = self._generate_detailed_actions(urgency_level, gap_analysis, eod_forecast)
        
        return {
            "status": "success",
            "urgency_level": urgency_level,
            "urgency_score": urgency_score,
            "urgency_percentage": f"{urgency_score:.0f}%",
            "gap_percentage": gap_percentage,
            "expected_achievement": expected_achievement,
            "predicted_eod": predicted_eod,
            "target": target,
            "action_required": urgency_level in ["HAUTE", "MOYENNE"],
            "recommendation": self._get_recommendation(urgency_level),
            "detailed_actions": actions,
            "timestamp": datetime.now().isoformat()
        }
    
    def full_analysis(self, daily_target: float) -> Dict[str, Any]:
        """
        ANALYSE COMPLÈTE
        Exécute toutes les étapes et génère un rapport détaillé
        """
        
        self.daily_target = daily_target
        
        # Étape 1: Écart objectif
        gap = self.calculate_objective_gap()
        
        # Étape 2: Prévision EOD
        forecast = self.predict_eod_timeseries()
        
        # Étape 3: Détection urgence
        urgency = self.detect_urgency()
        
        # Enregistrer dans l'historique
        analysis_result = {
            "timestamp": datetime.now().isoformat(),
            "gap": gap,
            "forecast": forecast,
            "urgency": urgency
        }
        self.analysis_history.append(analysis_result)
        
        return {
            "session_id": self.session_id,
            "analysis_timestamp": datetime.now().isoformat(),
            "transactions_received": len(self.daily_transactions),
            "objective_gap": gap,
            "eod_forecast": forecast,
            "urgency_detection": urgency,
            "summary": self._generate_summary(gap, forecast, urgency),
            "json_export": self._generate_json_export(gap, forecast, urgency)
        }
    
    def _prepare_timeseries_data(self) -> pd.DataFrame:
        """Prépare les données pour le modèle série temporelle"""
        
        if not self.daily_transactions:
            return pd.DataFrame()
        
        # Créer des données horaires agrégées
        data = []
        for tx in self.daily_transactions:
            data.append({
                "timestamp": pd.to_datetime(tx.get("timestamp")),
                "amount": tx.get("amount", 0)
            })
        
        df = pd.DataFrame(data)
        
        # Agréger par heure
        if not df.empty:
            df['hour'] = df['timestamp'].dt.floor('H')
            df_hourly = df.groupby('hour')['amount'].sum().reset_index()
            df_hourly.columns = ['ds', 'y']
            return df_hourly
        
        return df
    
    def _determine_urgency_level(self, gap_pct: float, achievement: float, hours_remaining: float) -> str:
        """Détermine le niveau d'urgence"""
        
        # Si on va dépasser largement la cible
        if achievement >= 110:
            return "FAIBLE"
        
        # Si on va atteindre la cible
        if achievement >= 95:
            return "FAIBLE"
        
        # Si on est en retard mais rattrapage possible
        if achievement >= 80 and hours_remaining > 4:
            return "MOYENNE"
        
        # Si on est très en retard
        if achievement < 80:
            return "HAUTE"
        
        return "MOYENNE"
    
    def _calculate_urgency_score(self, gap_pct: float, achievement: float, hours_remaining: float) -> float:
        """Calcule un score d'urgence (0-100)"""
        
        score = 0
        
        # Facteur 1: Écart vs cible (0-40 points)
        if achievement < 50:
            score += 40
        elif achievement < 80:
            score += 30
        elif achievement < 95:
            score += 15
        
        # Facteur 2: Temps restant (0-40 points)
        if hours_remaining < 2:
            score += 40
        elif hours_remaining < 4:
            score += 25
        elif hours_remaining < 8:
            score += 10
        
        # Facteur 3: Tendance (0-20 points)
        if gap_pct < -30:
            score += 20
        elif gap_pct < -10:
            score += 10
        
        return min(100, score)
    
    def _get_recommendation(self, urgency_level: str) -> str:
        """Retourne une recommandation basée sur l'urgence"""
        
        recommendations = {
            "HAUTE": "🔴 INTERVENTION URGENTE - Activer les promotions, augmenter la visibilité, renforcer l'équipe ventes",
            "MOYENNE": "🟡 SURVEILLANCE - Monitorer les ventes, préparer actions de secours",
            "FAIBLE": "🟢 NORMAL - Continuer le rythme actuel, surveillance passive"
        }
        
        return recommendations.get(urgency_level, "Analyse continue requise")
    
    def _generate_detailed_actions(self, urgency_level: str, gap: Dict, forecast: Dict) -> List[str]:
        """Génère des actions détaillées basées sur l'urgence"""
        
        actions = []
        
        if urgency_level == "HAUTE":
            actions = [
                "🔴 Activer TOUTES les promotions flash",
                "🔴 Augmenter la visibilité des best-sellers",
                "🔴 Renforcer l'équipe de ventes/service client",
                "🔴 Envoyer notifications push aux clients VIP",
                "🔴 Activiser les remises urgentes",
                f"🔴 Objectif: Atteindre {forecast.get('predicted_eod', 0):.2f}€ d'ici EOD"
            ]
        
        elif urgency_level == "MOYENNE":
            actions = [
                "🟡 Monitorer les KPIs en temps réel",
                "🟡 Préparer les actions de secours",
                "🟡 Augmenter légèrement la visibilité",
                "🟡 Envoyer emails aux clients inactifs",
                f"🟡 Réduction prévisionnelle nécessaire: {gap.get('gap', 0):.2f}€"
            ]
        
        else:  # FAIBLE
            actions = [
                "🟢 Maintenir le rythme actuel",
                "🟢 Surveillance passive des KPIs",
                "🟢 Préparer rapport d'EOD",
                f"🟢 Objectif prévisionnellement atteint: {forecast.get('predicted_eod', 0):.2f}€"
            ]
        
        return actions
    
    def _generate_summary(self, gap: Dict, forecast: Dict, urgency: Dict) -> str:
        """Génère un résumé détaillé de l'analyse"""
        
        summary = f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                    📊 RÉSUMÉ ANALYSTE APP2                                   ║
╚══════════════════════════════════════════════════════════════════════════════╝

📈 ÉTAT ACTUEL:
   • Montant reçu:        {gap.get('current_total', 0):>10.2f}€
   • Objectif journalier:  {gap.get('daily_target', 0):>10.2f}€
   • Écart:                {gap.get('gap', 0):>10.2f}€ ({gap.get('gap_percentage', 0):>6.1f}%)
   • Transactions:         {gap.get('transactions_count', 0):>10} | Avg: {gap.get('avg_transaction_value', 0):.2f}€

⏱️  PROGRESSION TEMPORELLE:
   • Heures écoulées:      {gap.get('hours_elapsed', 0):>10.1f}h
   • Heures restantes:     {gap.get('hours_remaining', 0):>10.1f}h
   • Progression réelle:   {gap.get('actual_progress_percentage', 0):>9.1f}%
   • Progression attendue: {(gap.get('hours_elapsed', 0)/24*100):>9.1f}%
   • Variance:             {gap.get('progress_variance', 0):>10.1f}%

🔮 PRÉVISION EOD:
   • Montant prévu:        {forecast.get('predicted_eod', 0):>10.2f}€
   • Montant restant:      {forecast.get('predicted_remaining', 0):>10.2f}€
   • Confiance:            {forecast.get('confidence_percentage', '0%'):>10}
   • Intervalle bas:       {forecast.get('confidence_interval_low', 0):>10.2f}€
   • Intervalle haut:      {forecast.get('confidence_interval_high', 0):>10.2f}€
   • Modèle utilisé:       {forecast.get('model_used', 'N/A'):>10}

🚨 URGENCE & ACTIONS:
   • Niveau:               {urgency.get('urgency_level', 'N/A'):>10}
   • Score:                {urgency.get('urgency_percentage', '0%'):>10}
   • Action requise:       {'OUI' if urgency.get('action_required') else 'NON':>10}
   • Taux d'atteinte:      {urgency.get('expected_achievement', 0):>9.1f}%

💡 RECOMMANDATION:
   {urgency.get('recommendation', 'N/A')}

╚══════════════════════════════════════════════════════════════════════════════╝
        """
        
        return summary.strip()
    
    def _generate_json_export(self, gap: Dict, forecast: Dict, urgency: Dict) -> str:
        """Génère un export JSON pour intégration externe"""
        
        export_data = {
            "session_id": self.session_id,
            "timestamp": datetime.now().isoformat(),
            "gap_analysis": gap,
            "forecast": forecast,
            "urgency": urgency
        }
        
        return json.dumps(export_data, indent=2, ensure_ascii=False, default=str)
    
    def _validate_transaction(self, transaction: Dict) -> bool:
        """Valide une transaction"""
        required = ["id", "amount", "timestamp", "product_id"]
        return all(field in transaction for field in required)