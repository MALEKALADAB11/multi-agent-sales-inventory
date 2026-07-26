"""
langfuse_sink.py — Pousse les résultats d'éval dans Langfuse.

Chaque cas devient une trace `eval-<suite>` (tags: eval, <suite>, <run_id>) ;
chaque critère du juge devient un score Langfuse (0-1, normalisé /5) attaché
à la trace. Les agrégats de suite deviennent une trace résumé unique.

Dans l'UI Langfuse : Traces → filtrer par tag `eval`, ou Scores → colonnes
eval/pertinence, eval/ancrage, ... comparables d'un run à l'autre.

Appelé automatiquement en fin de run_coach / run_models, ou à la main pour
(re)pousser les JSON déjà présents dans evals/results/ :

    python -m evals.langfuse_sink
"""

from __future__ import annotations

import json
import time
import uuid

from evals.common import RESULTS_DIR

from app.core.langfuse import get_langfuse

_TRUNC = 500


def _uid(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def _run_id(summary: dict) -> str:
    return (summary.get("run_at") or time.strftime("%Y-%m-%dT%H:%M:%S")).replace(":", "-")


def _push_case(lf, suite: str, run_id: str, *, case_id: str, inp, out,
               metadata: dict, scores: dict[str, float], comment: str = "") -> None:
    trace_id = _uid(f"eval-{suite}-")
    lf.trace(
        id=trace_id,
        name=f"eval-{suite}",
        user_id="eval-harness",
        session_id=f"eval-{suite}-{run_id}",
        tags=["eval", suite, run_id],
        input=inp,
        output=out,
        metadata=metadata,
    )
    for name, value in scores.items():
        if value is not None:
            lf.score(trace_id=trace_id, name=f"eval/{name}",
                     value=round(float(value), 3), comment=comment[:200])


def push_coach(summary: dict) -> int:
    lf = get_langfuse()
    if not lf:
        return 0
    run_id = _run_id(summary)
    n = 0
    for row in summary.get("details", []):
        judge = row.get("judge") or {}
        scores: dict[str, float] = {}
        comment = ""
        if "mean" in judge:
            # Langfuse attend des scores numériques homogènes : grille /5 → [0,1]
            scores = {c: judge[c] / 5 for c in
                      ("pertinence", "ancrage", "actionnabilite", "langue", "securite")
                      if c in judge}
            scores["judge_mean"] = judge["mean"] / 5
            scores["hallucination"] = 1.0 if judge.get("hallucination") else 0.0
            comment = judge.get("verdict", "")
        checks = row.get("checks") or {}
        if checks:
            scores["checks_pass"] = sum(checks.values()) / len(checks)
        _push_case(
            lf, "coach", run_id,
            case_id=row["id"],
            inp={"question": row["question"], "case_id": row["id"],
                 "category": row.get("category")},
            out={"reply": (row.get("reply") or "")[:_TRUNC],
                 "verdict": comment[:_TRUNC]},
            metadata={"latency_ms": row.get("latency_ms"), "mode": row.get("mode"),
                      "model": row.get("model"), "rag_used": row.get("rag_used"),
                      "judge_model": judge.get("judge_model"),
                      "checks": checks, "error": row.get("error") or None},
            scores=scores, comment=comment,
        )
        n += 1
    _push_summary(lf, "coach", run_id, {
        "success_rate": summary.get("success_rate"),
        "judge_global_mean_over5": summary.get("judge_global_mean"),
        "hallucination_rate": summary.get("hallucination_rate"),
        "checks_pass_rate": summary.get("checks_pass_rate"),
        "latency": summary.get("latency"),
    })
    lf.flush()
    return n


def push_models(summary: dict) -> int:
    lf = get_langfuse()
    if not lf:
        return 0
    run_id = _run_id(summary)
    n = 0
    for board_row in summary.get("details", []):
        model = board_row["model"]
        for ans in board_row.get("answers", []):
            judge = ans.get("judge") or {}
            scores = {}
            comment = judge.get("verdict", "")
            if "mean" in judge:
                scores = {c: judge[c] / 5 for c in
                          ("pertinence", "ancrage", "actionnabilite", "langue", "securite")
                          if c in judge}
                scores["judge_mean"] = judge["mean"] / 5
                scores["hallucination"] = 1.0 if judge.get("hallucination") else 0.0
            _push_case(
                lf, "models", run_id,
                case_id=ans["id"],
                inp={"question_id": ans["id"], "candidate_model": model},
                out={"answer": (ans.get("answer") or ans.get("error", ""))[:_TRUNC],
                     "verdict": comment[:_TRUNC]},
                metadata={"candidate_model": model,
                          "latency_ms": ans.get("latency_ms"),
                          "judge_model": judge.get("judge_model")},
                scores=scores, comment=comment,
            )
            n += 1
    _push_summary(lf, "models", run_id,
                  {"ranking": summary.get("ranking"),
                   "n_questions": summary.get("n_questions")})
    lf.flush()
    return n


def push_inventory_recommendations(summary: dict) -> int:
    """Miroir de `push_coach` : un cas = une trace `eval-inventory_recommendations`,
    chaque critère du juge inventaire (clarte/coherence/completude/actionabilite/
    richesse/ancrage) devient un score `eval/<critere>`, normalisé [0,1]."""
    lf = get_langfuse()
    if not lf:
        return 0
    run_id = _run_id(summary)
    n = 0
    for row in summary.get("details", []):
        judge = row.get("judge") or {}
        scores: dict[str, float] = {}
        comment = ""
        if "mean" in judge:
            scores = {c: judge[c] / 5 for c in
                      ("clarte", "coherence", "completude", "actionabilite", "richesse", "ancrage")
                      if c in judge}
            scores["judge_mean"] = judge["mean"] / 5
            comment = judge.get("verdict", "")
        _push_case(
            lf, "inventory_recommendations", run_id,
            case_id=row["id"],
            inp={"scenario": row["scenario"], "case_id": row["id"],
                 "category": row.get("category")},
            out={"recommendation_text": (row.get("recommendation_text") or "")[:_TRUNC],
                 "action": row.get("action"), "verdict": comment[:_TRUNC]},
            metadata={"action": row.get("action"), "reasoning_source": row.get("reasoning_source"),
                      "judge_model": judge.get("judge_model"), "error": row.get("error") or None},
            scores=scores, comment=comment,
        )
        n += 1
    _push_summary(lf, "inventory_recommendations", run_id, {
        "success_rate": summary.get("success_rate"),
        "judge_global_mean_over5": summary.get("judge_global_mean"),
        "fallback_comparison": summary.get("fallback_comparison"),
    })
    lf.flush()
    return n


def push_ragas(summary: dict) -> int:
    """Un cas RAGAS = une trace `eval-ragas` ; chaque métrique = un score
    `eval/ragas/<metrique>` (déjà normalisé [0,1] par RAGAS). Plus une trace résumé."""
    lf = get_langfuse()
    if not lf:
        return 0
    run_id = _run_id(summary)
    n = 0
    for row in summary.get("details", []):
        scores = {f"ragas/{name}": v for name, v in (row.get("scores") or {}).items()
                  if v is not None}
        _push_case(
            lf, "ragas", run_id,
            case_id=row["id"],
            inp={"question_id": row["id"], "domain": row.get("domain")},
            out={"answer": (row.get("answer") or "")[:_TRUNC],
                 "n_contexts": row.get("n_contexts"),
                 "rag_relevant": row.get("rag_relevant")},
            metadata={"domain": row.get("domain"), "n_contexts": row.get("n_contexts"),
                      "judge_model": summary.get("judge_model"),
                      "embed_model": summary.get("embed_model")},
            scores=scores,
        )
        n += 1
    _push_summary(lf, "ragas", run_id, {
        "judge_model": summary.get("judge_model"),
        "embed_model": summary.get("embed_model"),
        "n_cases": summary.get("n_cases"),
        **{name: val for name, val in (summary.get("means") or {}).items()},
    })
    lf.flush()
    return n


def push_aggregate(suite: str, summary: dict) -> int:
    """Suites sans cas LLM individuels (guardrail, rag) : une trace résumé."""
    lf = get_langfuse()
    if not lf:
        return 0
    run_id = _run_id(summary)
    _push_summary(lf, suite, run_id,
                  {k: v for k, v in summary.items()
                   if k not in ("details", "failures", "suite")})
    lf.flush()
    return 1


def _push_summary(lf, suite: str, run_id: str, metrics: dict) -> None:
    trace_id = _uid(f"eval-{suite}-summary-")
    lf.trace(
        id=trace_id,
        name=f"eval-{suite}-summary",
        user_id="eval-harness",
        session_id=f"eval-{suite}-{run_id}",
        tags=["eval", "summary", suite, run_id],
        input={"suite": suite, "run_id": run_id},
        output=metrics,
    )
    for name in ("accuracy", "hit_rate", "mrr", "abstention_rate",
                 "success_rate", "checks_pass_rate", "hallucination_rate",
                 "faithfulness", "answer_relevancy",
                 "llm_context_precision_without_reference", "context_recall"):
        value = metrics.get(name)
        if isinstance(value, (int, float)):
            lf.score(trace_id=trace_id, name=f"eval/{suite}/{name}", value=round(value, 3))
    status = metrics.get("status_report")
    if isinstance(status, dict) and isinstance(status.get("accuracy"), (int, float)):
        lf.score(trace_id=trace_id, name=f"eval/{suite}/accuracy",
                 value=round(status["accuracy"], 3))


_PUSHERS = {
    "coach_e2e":                 push_coach,
    "model_benchmark":           push_models,
    "guardrail":                 lambda s: push_aggregate("guardrail", s),
    "rag_retrieval":              lambda s: push_aggregate("rag", s),
    "ragas":                      push_ragas,
    "inventory_recommendations": push_inventory_recommendations,
}


def push_file(name: str) -> int:
    path = RESULTS_DIR / f"{name}.json"
    if not path.exists():
        return 0
    with open(path, encoding="utf-8") as f:
        summary = json.load(f)
    return _PUSHERS[name](summary)


def main():
    lf = get_langfuse()
    if not lf:
        print("Langfuse injoignable ou désactivé (LANGFUSE_HOST/LANGFUSE_ENABLED) — rien poussé.")
        return
    total = 0
    for name in _PUSHERS:
        n = push_file(name)
        if n:
            print(f"  {name:<18} → {n} trace(s)")
        total += n
    lf.flush()
    print(f"\n  {total} trace(s) poussée(s). UI : Traces → tag `eval`.")


if __name__ == "__main__":
    main()
