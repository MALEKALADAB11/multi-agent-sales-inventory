"""
run_inventory_recommendations_live.py — LLM-as-judge sur des recommandations
RÉELLES, échantillonnées via create_orchestrator (pas de scénario synthétique).

Différence avec run_inventory_recommendations.py : ce script appelle le
pipeline complet (analysis + context + decision) sur de vrais SKU/store, donc
plusieurs appels LLM candidat par cas, en plus de l'appel juge. Échantillon
volontairement petit et espacé — cf. le run du 26/07 où le quota OpenRouter
gratuit a été épuisé après ~15 cas de l'éval synthétique seule.

    python -m evals.run_inventory_recommendations_live [--sample-size N] [--pace 2.0] [--no-judge]
"""
from __future__ import annotations

import argparse
import json
import time

from evals.common import save_results
from evals.judge import INVENTORY_CRITERIA, judge_inventory_answer

# Départ volontairement petit et hardcodé — passer à une requête DB dynamique
# seulement une fois cette version validée sur quelques SKU connus.
SAMPLE_PAIRS: list[tuple[str, str]] = [
    ("3025000", "I63"),
    ("3200004", "I63"),
    ("5020591", "I14"),
]


def build_context_string(result: dict) -> str:
    return "\n".join([
        "analysis_report: " + json.dumps(result.get("analysis_report", {}), ensure_ascii=False),
        "context_report: " + json.dumps(
            result.get("context_result", {}).get("context_report", {}), ensure_ascii=False),
        "adjusted_metrics: " + json.dumps(
            result.get("decision_result", {}).get("adjusted_metrics", {}), ensure_ascii=False),
    ])


def run(sample_pairs: list[tuple[str, str]], business_objective: str = "standard",
        pace: float = 2.0, use_judge: bool = True, judge_retries: int = 2) -> dict:
    from app.inventory.services.orchestrator import create_orchestrator
    orchestrator = create_orchestrator(use_llm=True)

    rows: list[dict] = []
    judged: list[dict] = []

    for i, (sku, store_id) in enumerate(sample_pairs):
        if i > 0:
            time.sleep(pace)  # espace les appels candidat — même souci de quota que le 26/07

        try:
            result = orchestrator.analyze_sku(sku, store_id, business_objective)
        except Exception as e:
            rows.append({"sku": sku, "store_id": store_id, "error": f"{type(e).__name__}: {e}"})
            print(f"  [ERR] {sku}@{store_id} — {e}")
            continue

        if result.get("error"):
            rows.append({"sku": sku, "store_id": store_id, "error": result["error"]})
            print(f"  [ERR] {sku}@{store_id} — {result['error']}")
            continue

        decision = result.get("decision_result", {}).get("decision", {})
        text = decision.get("recommendation_text", "")
        source = decision.get("reasoning_source", "unknown")

        row = {"sku": sku, "store_id": store_id, "recommendation_text": text,
               "action": decision.get("action"), "reasoning_source": source, "judge": None}

        # Ne juge que les vraies sorties LLM — un fallback rule-based dégradé
        # sous 429 ne doit pas être noté comme si c'était la qualité du LLM.
        if text and use_judge and source == "llm":
            j = judge_inventory_answer(
                scenario=f"SKU réel {sku} @ {store_id}",
                recommendation_text=text,
                context=build_context_string(result),
                max_retries=judge_retries,  # survit à un 429 isolé sans abandonner le cas
            )
            if j.ok:
                row["judge"] = {**j.scores, "mean": j.mean, "verdict": j.verdict,
                                "judge_model": j.judge_model}
                judged.append(row["judge"])
            else:
                row["judge"] = {"error": j.error}
                print(f"  [SKIP judge] {sku}@{store_id} — {j.error[:100]}")
        elif source != "llm":
            print(f"  [SKIP judge] {sku}@{store_id} — source={source} (pas une vraie sortie LLM)")

        status = "OK" if not row.get("error") else "ERR"
        score = f" juge={row['judge']['mean']:.1f}" if row.get("judge") and "mean" in row["judge"] else ""
        print(f"  [{status}] {sku}@{store_id:<15} action={row.get('action')} source={source}{score}")
        rows.append(row)

    n_ok = sum(1 for r in rows if not r.get("error"))
    n_judged = len(judged)
    mean_by_criterion = {
        c: round(sum(j[c] for j in judged) / len(judged), 2) if judged else None
        for c in INVENTORY_CRITERIA
    }

    summary = {
        "suite": "inventory_recommendations_live",
        "n_cases": len(sample_pairs),
        "n_success": n_ok,
        "n_judged": n_judged,
        "success_rate": round(n_ok / len(sample_pairs), 3) if sample_pairs else 0.0,
        "judge_scores_mean": mean_by_criterion,
        "judge_global_mean": round(sum(j["mean"] for j in judged) / len(judged), 2) if judged else None,
        "details": rows,
    }

    print("\n" + "=" * 62)
    print(f"  INVENTORY RECOMMENDATIONS (LIVE) — {n_ok}/{len(sample_pairs)} réussis, "
          f"{n_judged}/{n_ok} jugés")
    print("=" * 62)
    if not use_judge:
        print("  Juge désactivé (--no-judge) — vérification de câblage uniquement, "
              "0 jugé est attendu.")
    elif n_judged < n_ok:
        print(f"  ⚠ {n_ok - n_judged} cas réussis mais NON jugés (fallback rule-based côté "
              "candidat, ou juge indisponible) — exclus de la moyenne ci-dessous.")
    if judged:
        print("  juge (0-5)      " + "  ".join(f"{c}={mean_by_criterion[c]}" for c in INVENTORY_CRITERIA))
        print(f"  score global    {summary['judge_global_mean']}/5")
    elif use_judge:
        print("  Aucun cas jugé — voir les [SKIP judge] ci-dessus.")

    save_results("inventory_recommendations_live", summary)
    try:
        from evals.langfuse_sink import push_inventory_recommendations
        n = push_inventory_recommendations(summary, suite="inventory_recommendations_live")
        if n:
            print(f"  {n} traces poussées vers Langfuse (tag `eval`)")
    except Exception as e:
        print(f"  Langfuse non poussé : {e}")

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-size", type=int, default=len(SAMPLE_PAIRS))
    parser.add_argument("--pace", type=float, default=2.0,
                        help="secondes entre chaque SKU — évite le 429 vu le 26/07")
    parser.add_argument("--judge-retries", type=int, default=2)
    parser.add_argument("--no-judge", action="store_true")
    args = parser.parse_args()

    run(SAMPLE_PAIRS[:args.sample_size], pace=args.pace,
        use_judge=not args.no_judge, judge_retries=args.judge_retries)