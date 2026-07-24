"""
Prévision du CA journalier par boutique — modèle global entraîné.

Complète `analyst/ts_engine.py`, qui ajuste un modèle statistique par boutique à
chaque appel. Ici le modèle est **appris hors ligne sur les 154 boutiques à la
fois**, puis chargé en inférence : il capte des régularités transverses (effet
jour de semaine, saisonnalité annuelle, dynamique de rattrapage) qu'une série
isolée de 120 jours ne suffit pas à estimer.

Le moteur statistique reste le repli : si le modèle n'est pas sur disque ou si
une feature manque, `ts_engine` retombe sur AutoETS puis Holt-Winters.
"""

from .features import FEATURE_COLUMNS, build_feature_frame, build_inference_row
from .global_model import GlobalSalesForecaster

__all__ = [
    "FEATURE_COLUMNS",
    "build_feature_frame",
    "build_inference_row",
    "GlobalSalesForecaster",
]
