"""
APP2 - Sales Analyzer Agent
Objectif: Prédire les tendances, identifier les écarts, ajuster les objectifs en temps réel

Fonctionnalités:
1. Recoit flux POS en direct
2. Calcule urgency_score
3. Appelle TimeslPM -> prévision EOD
4. Détecte écart: HAUTE/FAIBLE
"""

from langchain.chat_models import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END
from typing import Dict, Any, List
import json
from datetime import datetime, timedelta
from .detection_engine import DetectionEngine
from .tools import get_analyzer_tools
from .prompts import ANALYZER_SYSTEM_PROMPT

class App2Analyzer:
    """Agent Analyste pour détection & analyse des tendances POS"""
    
    def __init__(self, config: Dict[str, Any]):
        self.llm = ChatOpenAI(
            model=config.get("model", "gpt-4"),
            temperature=config.get("temperature", 0.7),
            api_key=config.get("openai_api_key")
        )
        self.config = config
        self.detection_engine = DetectionEngine(config)
        self.tools = get_analyzer_tools()
        
        # Stockage des données pour l'analyse
        self.daily_transactions = []
        self.daily_forecast = None
        self.daily_target = 0
        
    def receive_pos_stream(self, transaction: Dict) -> Dict[str, Any]:
        """
        1️⃣ RECOIT FLUX POS EN DIRECT
        Réceptionne les transactions POS en temps réel
        """
        
        # Valider la transaction
        if not self._validate_transaction(transaction):
            return {
                "status": "error",
                "message": "Invalid transaction format",
                "transaction_id": transaction.get("id", "unknown")
            }
        
        # Ajouter à la liste des transactions du jour
        self.daily_transactions.append(transaction)
        
        return {
            "status": "success",
            "transaction_id": transaction.get("id"),
            "total_transactions_today": len(self.daily_transactions),
            "timestamp": datetime.now().isoformat()
        }
    
    def calculate_urgency_score(self) -> Dict[str, Any]:
        """
        2️⃣ CALCULE URGENCY_SCORE
        Évalue l'urgence basée sur les écarts et la tendance
        """
        
        if not self.daily_transactions or self.daily_forecast is None:
            return {
                "urgency_score": 0,
                "status": "insufficient_data"
            }
        
        # Calculer le total actuel
        current_total = sum(t.get("amount", 0) for t in self.daily_transactions)
        
        # Calculer l'écart
        forecast_total = self.daily_forecast.get("forecast_amount", 0)
        gap = current_total - forecast_total
        gap_percentage = (gap / forecast_total * 100) if forecast_total > 0 else 0
        
        # Calculer le score d'urgence (0-100)
        # Plus l'écart est grand, plus le score est élevé
        urgency_score = min(100, abs(gap_percentage) * 1.5)
        
        return {
            "urgency_score": urgency_score,
            "current_total": current_total,
            "forecast_total": forecast_total,
            "gap": gap,
            "gap_percentage": gap_percentage,
            "timestamp": datetime.now().isoformat()
        }
    
    def call_timesl_pm(self) -> Dict[str, Any]:
        """
        3️⃣ APPELLE TimeslPM -> PRÉVISION EOD
        Appelle le module de prévision pour estimer les ventes End-Of-Day
        """
        
        if not self.daily_transactions:
            return {
                "status": "error",
                "message": "No transactions available for forecast"
            }
        
        # Analyser les transactions actuelles
        current_amount = sum(t.get("amount", 0) for t in self.daily_transactions)
        current_time = datetime.now()
        time_of_day_percentage = (current_time.hour / 24) * 100
        
        # Projeter jusqu'à la fin du jour (EOD)
        projection_factor = 24 / (current_time.hour + 1) if current_time.hour > 0 else 1
        
        # Prévision EOD
        forecast_eod = current_amount * projection_factor
        
        # Obtenir la cible du jour
        target = self.daily_target
        
        # Vérifier si on atteindra la cible
        will_reach_target = forecast_eod >= target
        achievement_percentage = (forecast_eod / target * 100) if target > 0 else 0
        
        forecast = {
            "forecast_amount": forecast_eod,
            "target": target,
            "will_reach_target": will_reach_target,
            "achievement_percentage": achievement_percentage,
            "current_amount": current_amount,
            "current_time": current_time.isoformat(),
            "time_of_day_percentage": time_of_day_percentage,
            "projection_factor": projection_factor,
            "timestamp": datetime.now().isoformat()
        }
        
        self.daily_forecast = forecast
        
        return forecast
    
    def detect_gap(self) -> Dict[str, Any]:
        """
        4️⃣ DÉTECTE ÉCART: HAUTE/FAIBLE
        Identifie les écarts entre prévisions et objectifs
        """
        
        if self.daily_forecast is None:
            return {
                "status": "error",
                "message": "No forecast available"
            }
        
        forecast_amount = self.daily_forecast.get("forecast_amount", 0)
        target = self.daily_forecast.get("target", 0)
        
        # Calculer l'écart
        gap = forecast_amount - target
        gap_percentage = (gap / target * 100) if target > 0 else 0
        
        # Déterminer le niveau d'écart
        if abs(gap_percentage) <= 5:
            gap_level = "STABLE"
        elif gap_percentage > 5:
            gap_level = "HAUTEUR"  # Dépassement positif
        else:
            gap_level = "FAIBLE"   # Déficit
        
        # Déterminer l'action à prendre
        action = self._determine_action(gap_level, gap_percentage)
        
        return {
            "gap": gap,
            "gap_percentage": gap_percentage,
            "gap_level": gap_level,  # HAUTEUR, STABLE, FAIBLE
            "action": action,
            "forecast_amount": forecast_amount,
            "target": target,
            "timestamp": datetime.now().isoformat()
        }
    
    def adjust_objectives_realtime(self) -> Dict[str, Any]:
        """
        AJUSTE LES OBJECTIFS EN TEMPS RÉEL
        Modifie les objectifs basés sur les tendances actuelles
        """
        
        gap_analysis = self.detect_gap()
        gap_level = gap_analysis.get("gap_level")
        gap_percentage = gap_analysis.get("gap_percentage")
        
        # Calculer l'ajustement
        if gap_level == "FAIBLE" and gap_percentage < -20:
            # Si on est très en retard, augmenter les efforts
            adjustment = 1.2  # +20%
            strategy = "AUGMENTER_EFFORTS"
        elif gap_level == "HAUTEUR" and gap_percentage > 20:
            # Si on dépasse largement, relâcher un peu
            adjustment = 0.95  # -5%
            strategy = "OPTIMISER"
        else:
            # Sinon, maintenir le cap
            adjustment = 1.0
            strategy = "MAINTENIR"
        
        # Nouvel objectif
        new_target = self.daily_target * adjustment
        
        return {
            "status": "success",
            "previous_target": self.daily_target,
            "new_target": new_target,
            "adjustment_factor": adjustment,
            "adjustment_percentage": (adjustment - 1) * 100,
            "strategy": strategy,
            "gap_level": gap_level,
            "recommendation": self._get_recommendation(strategy),
            "timestamp": datetime.now().isoformat()
        }
    
    def full_analysis(self, daily_target: float) -> Dict[str, Any]:
        """
        ANALYSE COMPLÈTE
        Exécute l'analyse complète: détection -> prévision -> ajustement
        """
        
        self.daily_target = daily_target
        
        # Étape 1: Calculer urgency score
        urgency = self.calculate_urgency_score()
        
        # Étape 2: Appeler prévision EOD
        forecast = self.call_timesl_pm()
        
        # Étape 3: Détecter écarts
        gap_analysis = self.detect_gap()
        
        # Étape 4: Ajuster objectifs
        adjustment = self.adjust_objectives_realtime()
        
        return {
            "analysis_timestamp": datetime.now().isoformat(),
            "total_transactions": len(self.daily_transactions),
            "urgency_score": urgency.get("urgency_score", 0),
            "forecast": forecast,
            "gap_analysis": gap_analysis,
            "objective_adjustment": adjustment,
            "summary": self._generate_summary(urgency, forecast, gap_analysis, adjustment)
        }
    
    def _determine_action(self, gap_level: str, gap_percentage: float) -> str:
        """Détermine l'action à prendre basée sur l'écart"""
        
        if gap_level == "HAUTEUR":
            return "MAINTENIR_RYTHME"
        elif gap_level == "STABLE":
            return "CONTINUER_NORMAL"
        else:  # FAIBLE
            if gap_percentage < -30:
                return "INTERVENTION_URGENTE"
            else:
                return "AUGMENTER_VENTES"
    
    def _get_recommendation(self, strategy: str) -> str:
        """Obtient la recommandation basée sur la stratégie"""
        
        recommendations = {
            "AUGMENTER_EFFORTS": "Activer les promotions, augmenter la visibilité produits, former l'équipe ventes",
            "OPTIMISER": "Vérifier la rentabilité, ne pas surcharger les stocks",
            "MAINTENIR": "Continuer le rythme actuel, surveiller les variations"
        }
        
        return recommendations.get(strategy, "Analyse continue requise")
    
    def _generate_summary(self, urgency: Dict, forecast: Dict, gap: Dict, adjustment: Dict) -> str:
        """Génère un résumé de l'analyse"""
        
        gap_level = gap.get("gap_level", "UNKNOWN")
        achievement = forecast.get("achievement_percentage", 0)
        
        summary = f"""
        RÉSUMÉ ANALYSTE APP2:
        - Niveau d'écart: {gap_level}
        - Taux d'atteinte: {achievement:.1f}%
        - Score d'urgence: {urgency.get('urgency_score', 0):.1f}/100
        - Stratégie: {adjustment.get('strategy', 'UNKNOWN')}
        - Recommandation: {adjustment.get('recommendation', 'N/A')}
        """
        
        return summary.strip()
    
    def _validate_transaction(self, transaction: Dict) -> bool:
        """Valide le format de la transaction"""
        required_fields = ["id", "amount", "timestamp", "product_id"]
        return all(field in transaction for field in required_fields)
    
    def build_workflow(self):
        """Construit le workflow LangGraph pour l'agent"""
        
        workflow = StateGraph(dict)
        
        # Nœuds du workflow
        workflow.add_node("receive_pos", self._receive_pos_node)
        workflow.add_node("calculate_urgency", self._calculate_urgency_node)
        workflow.add_node("forecast_eod", self._forecast_eod_node)
        workflow.add_node("detect_gap", self._detect_gap_node)
        workflow.add_node("adjust_objectives", self._adjust_objectives_node)
        workflow.add_node("report", self._report_node)
        
        # Arêtes du workflow
        workflow.set_entry_point("receive_pos")
        workflow.add_edge("receive_pos", "calculate_urgency")
        workflow.add_edge("calculate_urgency", "forecast_eod")
        workflow.add_edge("forecast_eod", "detect_gap")
        workflow.add_edge("detect_gap", "adjust_objectives")
        workflow.add_edge("adjust_objectives", "report")
        workflow.add_edge("report", END)
        
        return workflow.compile()
    
    def _receive_pos_node(self, state: Dict) -> Dict:
        """Nœud de réception POS"""
        transaction = state.get("transaction")
        result = self.receive_pos_stream(transaction)
        state["pos_result"] = result
        return state
    
    def _calculate_urgency_node(self, state: Dict) -> Dict:
        """Nœud de calcul d'urgence"""
        urgency = self.calculate_urgency_score()
        state["urgency"] = urgency
        return state
    
    def _forecast_eod_node(self, state: Dict) -> Dict:
        """Nœud de prévision EOD"""
        forecast = self.call_timesl_pm()
        state["forecast"] = forecast
        return state
    
    def _detect_gap_node(self, state: Dict) -> Dict:
        """Nœud de détection d'écart"""
        gap = self.detect_gap()
        state["gap"] = gap
        return state
    
    def _adjust_objectives_node(self, state: Dict) -> Dict:
        """Nœud d'ajustement des objectifs"""
        adjustment = self.adjust_objectives_realtime()
        state["adjustment"] = adjustment
        return state
    
    def _report_node(self, state: Dict) -> Dict:
        """Nœud de rapport final"""
        state["report_generated"] = True
        return state