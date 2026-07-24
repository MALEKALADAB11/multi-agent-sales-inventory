"""
Prompts de l'Agent Analyste — Ooredoo Tunisie.

⚠️ PÉRIMÈTRE — à lire avant toute modification.

L'Analyste v4 ne demande **jamais** au LLM de calculer quoi que ce soit. Tous les
chiffres (prévision EOD, gap, ledger horaire, faisabilité, urgence) sortent de
`ts_engine.analyze_store()`, qui est déterministe et s'exécute en moins d'une
seconde. Le LLM n'a qu'un seul rôle, optionnel et borné dans le temps :
**reformuler en français le résumé déjà produit par le moteur**.

Ce module ne contient donc plus qu'un prompt de rédaction. Les anciens prompts
few-shot qui faisaient produire le JSON d'analyse par le LLM ont été supprimés
avec les nodes qui les utilisaient — ils décrivaient un moteur (TimesFM) et des
seuils d'urgence qui ne correspondaient plus au code.

Activation : `ANALYST_LLM_SUMMARY=1` (défaut `0`). Timeout `ANALYST_LLM_TIMEOUT`
(défaut 8 s). En cas d'échec, de dépassement ou de longueur aberrante, le résumé
statistique de `ts_engine._build_summary()` reprend la main.
"""

# ══════════════════════════════════════════════════════════════════════════════
# Seuils du moteur — documentation, PAS une source de vérité
# ══════════════════════════════════════════════════════════════════════════════
# Reproduits ici pour que le LLM emploie le bon vocabulaire. La seule source de
# vérité reste ts_engine.analyze_store(). Toute modification là-bas doit être
# répercutée ici.
#
#   Horaires boutique      : STORE_OPEN_HOUR=8 → STORE_CLOSE_HOUR=20
#   Heure « en retard »    : WATCH si déviation ≤ -20 % ou z ≤ -1.0
#                            ALERT si déviation ≤ -35 % ou z ≤ -2.0
#   Faisabilité            : ACHIEVED · CLOSED · ACHIEVABLE
#                            CHALLENGING (capacité ≥ 70 % du reste) · VERY_HARD
#   Urgence (max retenu)   : CRITICAL  gap > 40 %  ou (couverture < 70 % et ≤ 4 h)
#                            HIGH      gap > 25 %  ou (couverture < 85 % et ≤ 3 h)
#                                                  ou faisabilité VERY_HARD
#                            MEDIUM    gap > 10 %  ou tendance DECELERATING
#                                                  ou au moins une heure en retard
#                            LOW       sinon
# ══════════════════════════════════════════════════════════════════════════════

ANALYST_SUMMARY_SYSTEM_PROMPT = """\
Tu es analyste retail senior chez Ooredoo Tunisie.

On te fournit une analyse déjà calculée par un moteur statistique déterministe.
Ton rôle est UNIQUEMENT de la reformuler : tu ne recalcules rien, tu ne corriges
aucun chiffre, tu n'inventes aucune donnée absente.

RÈGLES STRICTES
1. Exactement 2 phrases, en français.
2. Reprendre les montants tels quels, en TND, sans les arrondir ni les modifier.
3. Ton factuel et orienté action — pas de superlatif, pas d'encouragement creux.
4. Nommer la contrainte la plus forte : l'heure en retard, la tendance, ou la
   faisabilité, selon ce qui domine la situation.
5. Aucune recommandation produit : c'est le rôle de l'agent Stratège.
6. Sortie en texte brut. Pas de JSON, pas de markdown, pas de préambule.
"""

ANALYST_SUMMARY_USER_PROMPT = """\
Boutique {store_id} à {analysis_hour}h.

CA réalisé      : {current_ca:.0f} / {daily_target:.0f} TND ({attainment_pct:.0f} % de l'objectif)
Prévision EOD   : {eod_forecast:.0f} TND (IC 80 % : {eod_ci_low:.0f}–{eod_ci_high:.0f}, WAPE {mape_backtest}%, moteur {model_engine})
Gap             : {gap_pct}% ({gap_eod:.0f} TND)
Tendance        : {trend_signal}
Heures en retard: {gap_hours}
Faisabilité     : {feasibility}
Heures restantes: {hours_remaining}

Rédige le résumé analyste.\
"""


def build_summary_prompt(analysis: dict) -> str:
    """Rend le user prompt à partir du dict retourné par `analyze_store()`."""
    gap_hours = ", ".join(
        f"{e['hour']}h {e['deviation_pct']:+.0f}%"
        for e in (analysis.get("hourly_gaps") or [])[:4]
    ) or "aucune"
    return ANALYST_SUMMARY_USER_PROMPT.format(
        store_id       = analysis.get("store_id", "?"),
        analysis_hour  = analysis.get("analysis_hour", "?"),
        current_ca     = float(analysis.get("current_ca", 0) or 0),
        daily_target   = float(analysis.get("daily_target", 0) or 0),
        attainment_pct = float(analysis.get("attainment_pct", 0) or 0),
        eod_forecast   = float(analysis.get("eod_forecast", 0) or 0),
        eod_ci_low     = float(analysis.get("eod_ci_low", 0) or 0),
        eod_ci_high    = float(analysis.get("eod_ci_high", 0) or 0),
        mape_backtest  = analysis.get("mape_backtest", "?"),
        model_engine   = analysis.get("model_engine", "?"),
        gap_pct        = analysis.get("gap_pct", 0),
        gap_eod        = float(analysis.get("gap_eod", 0) or 0),
        trend_signal   = analysis.get("trend_signal", "UNKNOWN"),
        gap_hours      = gap_hours,
        feasibility    = analysis.get("feasibility", "UNKNOWN"),
        hours_remaining= analysis.get("hours_remaining", "?"),
    )
