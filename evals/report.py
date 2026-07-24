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

    rg = _load("ragas")
    if rg and not rg.get("error"):
        means = rg.get("means", {})
        _labels = {
            "faithfulness": "Faithfulness — la réponse ne dit rien hors des contextes",
            "answer_relevancy": "Answer relevancy — la réponse traite bien la question",
            "llm_context_precision_without_reference": "Context precision — contextes remontés utiles",
            "context_recall": "Context recall — contextes couvrant la référence",
        }
        lines += [
            "## 2bis. RAG — RAGAS (métriques officielles)", "",
            f"Chaîne retriever + génération ancrée, juge `{rg.get('judge_model', '?')}`, "
            f"embeddings `{rg.get('embed_model') or '—'}`, sur {rg.get('n_cases', '?')} cas.", "",
            "| Métrique RAGAS | Score /1 | Lecture |", "|---|---|---|",
        ]
        for name in rg.get("metrics", []):
            val = means.get(name)
            lines.append(f"| {name} | {val if val is not None else '—'} | {_labels.get(name, '')} |")
        lines.append("")

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
        w = m.get("weights", {})
        lines += [
            "## 4. Benchmark comparatif des modèles", "",
            f"Protocole : {m['n_questions']} questions × {m.get('repeat', 1)} passage(s), "
            f"prompt et contexte figés, panel de {m.get('n_judges', 1)} juge(s) LLM "
            "distincts (jamais le modèle évalué), "
            f"{m.get('retries', 0)} réessais sur 429/5xx.", "",
            f"Qualité mesurée sur les **{m.get('n_paired_questions', '?')}/"
            f"{m['n_questions']} questions communes** à tous les modèles "
            "(comparaison appariée — sinon un modèle rate-limité serait noté sur "
            "un sous-ensemble plus favorable).", "",
            "| Rang | Modèle | Composite | Qualité /5 | IC 95% | Dispo | Checks | Halluc. | p50 | $/1k rép. |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for i, r in enumerate(m["ranking"], 1):
            ci = r.get("ci95") or {}
            ci_txt = (f"{ci.get('low')}–{ci.get('high')}"
                      if ci.get("low") is not None else "—")
            def _v(x, suffix=""):
                return f"{x}{suffix}" if x is not None else "—"
            lines.append(
                f"| {i} | {r['model']} | {_v(r.get('composite'))} | {_v(r['global_mean'])} | "
                f"{ci_txt} | {_pct(r.get('availability'))} | "
                f"{_pct(r.get('checks_pass_rate'))} | {_pct(r['hallucination_rate'])} | "
                f"{_v(r['p50_ms'], ' ms')} | {_v(r.get('cost_usd_per_1k_answers'))} |")
        if w:
            lines += ["", "Score composite = "
                      + " + ".join(f"{v:.0%} {k}" for k, v in w.items())
                      + " (qualité pondérée par les contrôles déterministes)."]
        groups = m.get("tie_groups") or []
        if any(len(g) > 1 for g in groups):
            lines += ["", "**Écarts non concluants** (IC 95% chevauchants — "
                      "à traiter comme ex æquo) :"]
            lines += [f"- {' ≈ '.join(g)}" for g in groups if len(g) > 1]
        lines += ["", "Critères du juge (0–5), contrôles déterministes et signaux :", "",
                  "| Modèle | " + " | ".join(CRITERIA)
                  + " | remise | rupture | concision | chiffres à relire | stabilité σ | désaccord juges |",
                  "|---|" + "---|" * (len(CRITERIA) + 6)]
        for r in m["ranking"]:
            ch = r.get("checks", {})
            lines.append(
                "| " + r["model"] + " | "
                + " | ".join(str(r["scores"].get(crit) if r["scores"].get(crit) is not None
                                 else "—") for crit in CRITERIA)
                + f" | {_pct(ch.get('pas_de_remise'))} | {_pct(ch.get('rupture_respectee'))}"
                + f" | {_pct(ch.get('format_concis'))} | {_pct(r.get('ungrounded_flag_rate'))}"
                + f" | {r.get('stability_std', '—')}"
                + f" | {r.get('judge_disagreement', '—')} |")
        lines += ["",
                  "_« Chiffres à relire » n'est pas une faute : le contrôle d'ancrage "
                  "signale tout nombre non dérivable en une opération du contexte figé — "
                  "il attrape un prix inventé comme un calcul légitime en deux temps "
                  "(367 ÷ 5 h). Il est publié hors score ; le critère `ancrage` du juge "
                  "tranche le qualitatif._", ""]

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
