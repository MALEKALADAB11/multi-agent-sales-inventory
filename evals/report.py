"""
report.py — Agrège evals/results/*.json en un rapport Markdown unique.

    python -m evals.report        → evals/results/REPORT.md
"""

from __future__ import annotations

import json
from pathlib import Path

from evals.common import RESULTS_DIR
from evals.judge import CRITERIA


def _load(name: str) -> dict | None:
    path = RESULTS_DIR / f"{name}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _pct(x) -> str:
    return f"{x:.1%}" if isinstance(x, (int, float)) else "—"


def build() -> str:
    lines = ["# Rapport d'évaluation LLM — Système multi-agents Ooredoo", ""]

    g = _load("guardrail")
    if g:
        r = g["status_report"]
        lines += [
            f"_Guardrail exécuté le {g.get('run_at', '?')}_", "",
            "## 1. Guardrail Agent (jeu adversarial)", "",
            f"- **Accuracy statut** : {_pct(r['accuracy'])} sur {g['n_cases']} cas",
            f"- **F1 macro** : {r['macro_f1']}",
            f"- **Taux de faux blocage** (réponse légitime bloquée/escaladée) : {_pct(g['false_block_rate'])}",
            "", "| Règle | Précision | Rappel | F1 |", "|---|---|---|---|",
        ]
        for rule, m in g["per_rule"].items():
            lines.append(f"| {rule} | {m['precision']} | {m['recall']} | {m['f1']} |")
        if g["failures"]:
            lines += ["", f"**Écarts ({len(g['failures'])})** :"]
            lines += [f"- `{f['id']}` — attendu {f['expected']}, obtenu {f['got']}"
                      for f in g["failures"]]
        lines.append("")

    rag = _load("rag_retrieval")
    if rag:
        lines += [
            "## 2. RAG — Retrieval (golden set)", "",
            "| Métrique | Valeur | Lecture |", "|---|---|---|",
            f"| hit@{rag['top_k']} | {_pct(rag['hit_rate'])} | le bon domaine est dans le top-k |",
            f"| MRR | {rag['mrr']} | rang moyen du premier bon document |",
            f"| Token recall | {_pct(rag['token_recall'])} | le contenu attendu est retrouvé |",
            f"| Pureté | {_pct(rag['purity'])} | aucun contenu interdit remonté |",
            f"| Abstention | {_pct(rag['abstention_rate'])} | sait dire « rien de pertinent » (anti-hallucination) |",
            "",
        ]

    c = _load("coach_e2e")
    if c:
        lat = c.get("latency", {})
        lines += [
            "## 3. Coach — bout-en-bout (API réelle)", "",
            f"- **Taux de réponse** : {_pct(c['success_rate'])} ({c['n_success']}/{c['n_cases']})",
            f"- **Latence** : p50 = {lat.get('p50_ms')} ms, p95 = {lat.get('p95_ms')} ms",
            f"- **Checks déterministes** (remise interdite, fuite de prompt, refus hors-sujet, français) : {_pct(c.get('checks_pass_rate'))}",
            f"- **Taux d'usage du RAG** : {_pct(c.get('rag_used_rate'))}",
        ]
        if c.get("judge_global_mean") is not None:
            lines += [
                f"- **Score juge global** : {c['judge_global_mean']}/5",
                f"- **Taux d'hallucination** : {_pct(c.get('hallucination_rate'))}",
                "", "| Critère (0–5) | Moyenne |", "|---|---|",
            ]
            for crit in CRITERIA:
                lines.append(f"| {crit} | {c['judge_scores_mean'].get(crit)} |")
        lines.append("")

    m = _load("model_benchmark")
    if m:
        lines += [
            "## 4. Benchmark comparatif des modèles", "",
            f"Protocole : {m['n_questions']} questions × {m['repeat']} passage(s), "
            "prompt et contexte figés, juge LLM croisé (jamais le modèle évalué).", "",
            "| Rang | Modèle | Score /5 | Hallucination | p50 | p95 |",
            "|---|---|---|---|---|---|",
        ]
        for i, r in enumerate(m["ranking"], 1):
            lines.append(
                f"| {i} | {r['model']} | {r['global_mean']} | "
                f"{_pct(r['hallucination_rate'])} | {r['p50_ms']} ms | {r['p95_ms']} ms |")
        lines += ["", "Critères détaillés par modèle :", "",
                  "| Modèle | " + " | ".join(CRITERIA) + " |",
                  "|---|" + "---|" * len(CRITERIA)]
        for r in m["ranking"]:
            lines.append("| " + r["model"] + " | "
                         + " | ".join(str(r["scores"].get(crit, "—")) for crit in CRITERIA) + " |")
        lines.append("")

    lines += [
        "---",
        "_Méthodologie : checks déterministes exécutés en local ; scores qualitatifs "
        "par LLM-as-judge (température 0, grille 0–5, JSON strict) avec exclusion "
        "du modèle évalué du rôle de juge. Retrieval mesuré par propriétés "
        "(domaine, tokens, abstention) plutôt que par doc_id figé, le corpus étant vivant._",
    ]
    return "\n".join(lines)


def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report = build()
    out = RESULTS_DIR / "REPORT.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n  Rapport → {out}")


if __name__ == "__main__":
    main()
