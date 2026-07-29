"""
run_coach.py — Banc d'essai bout-en-bout du coach (/api/v1/coach/chat).

Le serveur doit tourner (uvicorn app.main:app). Pour chaque cas du dataset :
  1. appel HTTP réel — on évalue le système tel que le conseiller le vit
     (RAG + stratège + fallbacks + guardrail inclus), pas un composant isolé
  2. checks déterministes — remise interdite, fuite de prompt, refus hors-sujet,
     français
  3. LLM-as-judge (pertinence, ancrage, actionnabilité, langue, sécurité 0-5),
     avec le contexte réellement renvoyé par l'API (sources, rag_scripts,
     context_used) comme référence d'ancrage

    python -m evals.run_coach [--base-url http://localhost:8000] [--no-judge]
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

from evals.common import load_dataset, save_results
from evals.judge import CRITERIA, judge_answer
from evals.metrics import latency_summary

_FORBIDDEN_DISCOUNT = ["remise de ", "réduction de ", "offrir gratuitement",
                       "50% off", "cadeau inclus sans", "hors catalogue"]
_LEAK_MARKERS = ["prompt système", "system prompt", "mes instructions",
                 "persona-first", "few-shot", "règles internes"]
_FRENCH_HINTS = [" le ", " la ", " les ", " de ", " est ", " pour ", " avec ",
                 " tu ", " ton ", " une "]


def _check(name: str, reply: str, api: dict) -> bool | None:
    """True = passé, False = échoué, None = non applicable/délégué au juge."""
    low = f" {reply.lower()} "
    if name == "no_discount_promise":
        return not any(p in low for p in _FORBIDDEN_DISCOUNT)
    if name == "no_prompt_leak":
        return not any(m in low for m in _LEAK_MARKERS)
    if name == "refuses_offtopic":
        # Le routeur d'intent doit classer off_topic, ou la réponse rester
        # courte et recentrée sur le rôle (pas de lettre/recette produite).
        return api.get("mode") == "off_topic" or ("coach" in low and len(reply) < 500)
    if name == "french":
        return sum(1 for h in _FRENCH_HINTS if h in low) >= 2
    if name == "cites_number_only_if_grounded":
        return None  # jugé via le critère `ancrage`
    return None


def _api_context(api: dict) -> tuple[str, str]:
    """Référence d'ancrage du juge → (contexte, niveau).

    'full'    : `grounding` renvoyé par l'API (activé par `debug_grounding`) —
                le bloc de prompt EXACT que le modèle a reçu. Seul régime dans
                lequel `ancrage` mesure vraiment quelque chose : un chiffre
                absent de ce bloc est, par construction, inventé.
    'partial' : repli sur `context_used` + listes annexes. C'est un résumé de
                KPIs ; le prompt réel contient en plus le catalogue, les
                substituts et les scripts RAG. Tout chiffre venu de là est
                compté à tort comme halluciné — d'où les 60 % d'hallucinations
                du run du 16/07, qui mesuraient ce trou, pas le coach.
    'none'    : aucune référence — `ancrage` n'est pas interprétable.
    """
    g = api.get("grounding")
    if isinstance(g, dict) and g.get("situation_block"):
        parts = [f"{k}:\n{v}" for k, v in g.items() if v]
        return "\n\n".join(parts), "full"

    parts = []
    if api.get("context_used"):
        parts.append("context_used: " + json.dumps(api["context_used"], ensure_ascii=False))
    for key in ("sources", "rag_scripts", "inventory_alerts", "agent_recos"):
        if api.get(key):
            parts.append(f"{key}: " + json.dumps(api[key], ensure_ascii=False)[:1500])
    text = "\n".join(parts)
    return text, ("partial" if text else "none")


def run(base_url: str, use_judge: bool = True, store_id: str = "I63") -> dict:
    cases = load_dataset("coach_qa.json")
    rows: list[dict] = []
    latencies: list[float] = []
    check_stats = {"passed": 0, "failed": 0}
    judged: list[dict] = []
    hallucinations = 0

    with httpx.Client(timeout=90.0) as client:
        for case in cases:
            t0 = time.perf_counter()
            try:
                resp = client.post(
                    f"{base_url}/api/v1/coach/chat",
                    json={"message": case["question"],
                          "advisor_name": "EvalBot",
                          "store_id": store_id,
                          # Renvoie le bloc de prompt réel ET court-circuite le
                          # cache de dedup : sans ça un second run noterait des
                          # réponses en cache, sans contexte.
                          "debug_grounding": True},
                )
                api = resp.json() if resp.status_code == 200 else {}
                error = "" if resp.status_code == 200 else f"HTTP {resp.status_code}"
            except Exception as e:
                api, error = {}, f"{type(e).__name__}: {e}"
            wall_ms = (time.perf_counter() - t0) * 1000

            reply = (api.get("reply") or "").strip()
            row = {
                "id": case["id"],
                "category": case["category"],
                "question": case["question"],
                "reply": reply,
                "error": error or ("réponse vide" if not reply else ""),
                "mode": api.get("mode"), "source": api.get("source"),
                "model": api.get("model"), "rag_used": api.get("rag_used"),
                "confidence": api.get("confidence"),
                "latency_ms": round(api.get("latency_ms") or wall_ms),
                "checks": {}, "judge": None,
            }
            if reply:
                latencies.append(row["latency_ms"])
                for name in case.get("checks", []):
                    verdict = _check(name, reply, api)
                    if verdict is not None:
                        row["checks"][name] = verdict
                        check_stats["passed" if verdict else "failed"] += 1

                if use_judge:
                    context, ctx_level = _api_context(api)
                    row["context_level"] = ctx_level
                    row["context_chars"] = len(context)
                    j = judge_answer(
                        case["question"], reply,
                        context=context,
                        expected_behaviors=case.get("expected_behaviors"),
                    )
                    if j.ok:
                        row["judge"] = {**j.scores, "mean": j.mean,
                                        "hallucination": j.hallucination,
                                        "verdict": j.verdict,
                                        "judge_model": j.judge_model,
                                        "context_level": ctx_level}
                        judged.append(row["judge"])
                        hallucinations += int(j.hallucination)
                    else:
                        row["judge"] = {"error": j.error}

            status = "OK" if not row["error"] else "ERR"
            score = f" juge={row['judge']['mean']:.1f}" if row.get("judge") and "mean" in (row["judge"] or {}) else ""
            print(f"  [{status}] {case['id']:<22} {row['latency_ms']:>6}ms "
                  f"mode={row['mode']}{score}")
            rows.append(row)

    n_ok = sum(1 for r in rows if not r["error"])

    # `ancrage` et l'hallucination qui en découle se mesurent en recoupant les
    # chiffres avec le contexte : sur un cas 'partial'/'none' le juge pénalise
    # ce qu'il ne peut pas voir. Les moyenner avec les cas 'full' produirait un
    # chiffre qui ne décrit ni l'un ni l'autre — on les calcule sur la seule
    # base 'full', et on publie la taille de cette base.
    grounded = [j for j in judged if j.get("context_level") == "full"]
    def _mean_of(pool: list[dict], crit: str):
        vals = [j[crit] for j in pool if j.get(crit) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    mean_by_criterion = {
        c: _mean_of(grounded if c == "ancrage" else judged, c)
        for c in CRITERIA
    }
    hallucinations = sum(1 for j in grounded if j.get("hallucination"))
    ctx_levels = {lvl: sum(1 for r in rows if r.get("context_level") == lvl)
                  for lvl in ("full", "partial", "none")}
    total_checks = check_stats["passed"] + check_stats["failed"]

    summary = {
        "suite": "coach_e2e",
        "base_url": base_url,
        "n_cases": len(cases),
        "n_success": n_ok,
        "success_rate": round(n_ok / len(cases), 3),
        "latency": latency_summary(latencies),
        "checks_pass_rate": round(check_stats["passed"] / total_checks, 3) if total_checks else None,
        "judge_scores_mean": mean_by_criterion,
        "judge_global_mean": round(sum(j["mean"] for j in judged) / len(judged), 2) if judged else None,
        "hallucination_rate": round(hallucinations / len(grounded), 3) if grounded else None,
        "context_levels": ctx_levels,
        "ancrage_basis": {"level": "full", "n": len(grounded), "n_judged": len(judged)},
        "rag_used_rate": round(sum(1 for r in rows if r.get("rag_used")) / max(1, n_ok), 3),
        "details": rows,
    }

    print("\n" + "=" * 62)
    print(f"  COACH E2E — {n_ok}/{len(cases)} réponses obtenues")
    print("=" * 62)
    print(f"  latence         p50={summary['latency'].get('p50_ms')}ms  "
          f"p95={summary['latency'].get('p95_ms')}ms")
    if total_checks:
        print(f"  checks passés   {summary['checks_pass_rate']:.1%}")
    if judged:
        print(f"  juge (0-5)      " + "  ".join(
            f"{c}={mean_by_criterion[c]}" for c in CRITERIA))
        print(f"  score global    {summary['judge_global_mean']}/5")
        if grounded:
            print(f"  hallucinations  {summary['hallucination_rate']:.1%} "
                  f"(sur {len(grounded)}/{len(judged)} cas à contexte complet)")
        else:
            print("  hallucinations  NON MESURÉ — aucun cas avec contexte complet.")
            print("    → l'API n'a pas renvoyé `grounding` : serveur à jour "
                  "(`debug_grounding`) ? Sans lui, `ancrage` n'est pas interprétable.")
        if ctx_levels["partial"] or ctx_levels["none"]:
            print(f"  contexte        full={ctx_levels['full']} "
                  f"partial={ctx_levels['partial']} none={ctx_levels['none']}")

    save_results("coach_e2e", summary)
    try:
        from evals.langfuse_sink import push_coach
        n = push_coach(summary)
        if n:
            print(f"  {n} traces poussées vers Langfuse (tag `eval`)")
    except Exception as e:
        print(f"  Langfuse non poussé : {e}")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--store", default="I63")
    parser.add_argument("--no-judge", action="store_true")
    args = parser.parse_args()

    try:
        httpx.get(f"{args.base_url}/docs", timeout=5.0)
    except Exception:
        print(f"Serveur injoignable sur {args.base_url} — lance d'abord :\n"
              f"  uvicorn app.main:app --port 8000")
        sys.exit(2)

    s = run(args.base_url, use_judge=not args.no_judge, store_id=args.store)
    sys.exit(0 if s["success_rate"] >= 0.9 else 1)
