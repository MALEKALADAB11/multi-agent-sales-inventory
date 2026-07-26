"""
test_judge_fix_only.py — teste UNIQUEMENT le prompt du juge, sans passer par
le DecisionAgent ni le dataset complet. 4 appels juge au total.

Réutilise les 2 textes déjà vus dans ton --dry-run d'hier (donc on sait déjà
qu'ils sont valides / réellement générés par le LLM) :
  - critical_stockout-01 : texte riche, pour tester richesse (vs un texte
    "à trous" fabriqué à la main) et ancrage (vs une version avec un chiffre
    corrompu)

Lancer depuis la racine du repo :
    python test_judge_fix_only.py
"""
from evals.judge import judge_inventory_answer

SCENARIO = (
    "Risque CRITICAL, 2 jours de stock restants, délai de livraison standard "
    "de 10 jours — rupture garantie avant l'arrivée d'une commande normale. "
    "Doit produire EXPEDITE, pas ORDER."
)

CONTEXT = (
    "baseline_report: {\"risk_assessment\": {\"level\": \"CRITICAL\"}, "
    "\"metrics\": {\"days_of_stock_remaining\": 2, \"formula_order_qty\": 45}, "
    "\"stock\": {\"lead_time_avg_days\": 10}}"
)

RICH_TEXT = (
    "Expeditez une commande de 45 unités aujourd'hui — le stock tombera à zéro "
    "dans 2 jours alors que le délai de livraison moyen est de 10 jours. À ce "
    "rythme, une commande standard arriverait trop tard, laissant le rayon vide "
    "pendant au moins 8 jours."
)

# Texte "à trous" fabriqué à la main — doit recevoir richesse=1
TEMPLATED_TEXT = "Action recommandée : EXPEDITE. Stock : 2 jours. Délai : 10 jours."

# Même texte riche, mais avec 45 -> 99 (chiffre absent du scénario) — doit
# recevoir ancrage=1
CORRUPTED_TEXT = RICH_TEXT.replace("45", "99")


def show(label, j):
    print(f"\n--- {label} ---")
    if not j.ok:
        print(f"  ERREUR juge : {j.error}")
        return
    print(f"  juge_model = {j.judge_model}")
    print(f"  richesse={j.scores.get('richesse')}  ancrage={j.scores.get('ancrage')}")
    print(f"  verdict: {j.verdict}")


if __name__ == "__main__":
    print("Test 1/2 — richesse : texte riche vs texte à trous")
    j_rich = judge_inventory_answer(SCENARIO, RICH_TEXT, context=CONTEXT)
    j_template = judge_inventory_answer(SCENARIO, TEMPLATED_TEXT, context=CONTEXT)
    show("riche", j_rich)
    show("templaté", j_template)
    if j_rich.ok and j_template.ok:
        ok = j_rich.scores.get("richesse", 0) > j_template.scores.get("richesse", 5)
        print(f"\n  {'[OK]' if ok else '[ATTENTION]'} richesse discrimine : "
              f"{j_rich.scores.get('richesse')} vs {j_template.scores.get('richesse')}")

    print("\n\nTest 2/2 — ancrage : chiffre correct vs chiffre corrompu (45 -> 99)")
    j_orig = judge_inventory_answer(SCENARIO, RICH_TEXT, context=CONTEXT)
    j_corrupt = judge_inventory_answer(SCENARIO, CORRUPTED_TEXT, context=CONTEXT)
    show("original (45)", j_orig)
    show("corrompu (99)", j_corrupt)
    if j_orig.ok and j_corrupt.ok:
        ok = j_corrupt.scores.get("ancrage", 5) <= 1 and j_orig.scores.get("ancrage", 0) >= 4
        print(f"\n  {'[OK]' if ok else '[ATTENTION]'} ancrage discrimine : "
              f"{j_orig.scores.get('ancrage')} vs {j_corrupt.scores.get('ancrage')}")