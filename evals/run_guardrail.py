"""
run_guardrail.py — Banc d'essai du Guardrail Agent (déterministe, offline).

Mesure sur un jeu adversarial de 29 cas :
  • accuracy + F1 macro sur le statut final (APPROVE/REWRITE/ESCALATE/BLOCK)
  • précision/rappel/F1 PAR RÈGLE G1..G7 (une règle ratée = faux négatif,
    une règle déclenchée à tort = faux positif)
  • taux de faux blocages (réponse légitime bloquée) — le coût UX du guardrail

    python -m evals.run_guardrail
"""

from __future__ import annotations

import sys

from evals.common import load_dataset, save_results
from evals.metrics import binary_prf, classification_report

from app.sales.coaching.agents.guardrail.guardrail_agent import evaluate_guardrails

ALL_RULES = [f"G{i}" for i in range(1, 8)]


def run() -> dict:
    cases = load_dataset("guardrail_cases.json")

    status_pairs: list[tuple[str, str]] = []
    rule_counts = {r: {"tp": 0, "fp": 0, "fn": 0} for r in ALL_RULES}
    false_blocks = 0
    n_legitimate = 0
    failures: list[dict] = []

    for case in cases:
        inp = case["inputs"]
        result = evaluate_guardrails(
            recommendation=inp["recommendation"],
            store_id="EVAL",
            inventory_snapshot=inp.get("inventory_snapshot"),
            rag_used=inp.get("rag_used", False),
            nb_scripts=inp.get("nb_scripts", 0),
            confidence=inp.get("confidence", 0.9),
            inventory_decision=inp.get("inventory_decision"),
        )
        got_status = result["status"]
        got_rules = {i["rule"] for i in result["issues"]}
        exp_status = case["expected"]["status"]
        exp_rules = set(case["expected"]["rules"])

        status_pairs.append((exp_status, got_status))

        for rule in ALL_RULES:
            if rule in exp_rules and rule in got_rules:
                rule_counts[rule]["tp"] += 1
            elif rule in got_rules:
                rule_counts[rule]["fp"] += 1
            elif rule in exp_rules:
                rule_counts[rule]["fn"] += 1

        if exp_status == "APPROVE":
            n_legitimate += 1
            if got_status in ("BLOCK", "ESCALATE"):
                false_blocks += 1

        if got_status != exp_status or got_rules != exp_rules:
            failures.append({
                "id": case["id"],
                "description": case["description"],
                "expected": {"status": exp_status, "rules": sorted(exp_rules)},
                "got": {"status": got_status, "rules": sorted(got_rules)},
            })

    report = classification_report(status_pairs)
    per_rule = {r: binary_prf(**c) for r, c in rule_counts.items()
                if sum(c.values()) > 0}

    summary = {
        "suite": "guardrail",
        "n_cases": len(cases),
        "status_report": report,
        "per_rule": per_rule,
        "false_block_rate": round(false_blocks / n_legitimate, 3) if n_legitimate else 0.0,
        "failures": failures,
    }

    print("=" * 62)
    print(f"  GUARDRAIL — {len(cases)} cas adversariaux")
    print("=" * 62)
    print(f"  accuracy statut     {report['accuracy']:.1%}")
    print(f"  F1 macro statut     {report['macro_f1']:.3f}")
    print(f"  faux blocages       {false_blocks}/{n_legitimate} "
          f"({summary['false_block_rate']:.1%} des cas légitimes)")
    print("\n  Par règle (précision / rappel / F1) :")
    for rule, m in per_rule.items():
        print(f"    {rule}  P={m['precision']:.2f}  R={m['recall']:.2f}  F1={m['f1']:.2f}")
    if failures:
        print(f"\n  {len(failures)} écart(s) :")
        for f in failures:
            print(f"    - {f['id']}: attendu {f['expected']}, obtenu {f['got']}")
    else:
        print("\n  Aucun écart.")

    save_results("guardrail", summary)
    try:
        from evals.langfuse_sink import push_aggregate
        if push_aggregate("guardrail", summary):
            print("  Résumé poussé vers Langfuse (tag `eval`)")
    except Exception as e:
        print(f"  Langfuse non poussé : {e}")
    return summary


if __name__ == "__main__":
    s = run()
    sys.exit(0 if s["status_report"]["accuracy"] >= 0.9 else 1)
