"""
run_models.py — Benchmark comparatif des LLM candidats au rôle de coach.

Protocole à variables contrôlées : même prompt système, même contexte
synthétique figé (CA, objectif, stocks, scripts RAG), mêmes 10 questions,
même juge. Seul le modèle change — le classement mesure donc le modèle,
pas la chance du contexte.

Le juge exclut le modèle qu'il évalue (biais d'auto-préférence).

Modèles couverts (si clés .env présentes) : Mistral (large/small),
Groq (gpt-oss-120b, llama-3.3-70b), OpenRouter (nemotron nano/super).

    python -m evals.run_models [--repeat 1]
"""

from __future__ import annotations

import argparse
import sys

from evals.common import chat, load_dataset, load_providers, save_results
from evals.judge import CRITERIA, judge_answer
from evals.metrics import latency_summary

# Contexte synthétique FIGÉ — chaque chiffre cité par un modèle doit venir d'ici.
# C'est ce qui rend le critère `ancrage` mesurable : tout autre chiffre est inventé.
_CONTEXT = """DONNÉES BOUTIQUE (Ooredoo Lac 2, aujourd'hui 15h) :
- CA du jour : 640 TND / objectif 1 007 TND (63%). Il reste 5 heures de vente.
- Stocks : iPhone 16 128Go = 7 unités (prix 3 899 TND TTC) ; Samsung A25 = 0 unité (RUPTURE) ;
  Recharge 10DT = 210 unités ; Coque universelle = 34 unités (prix 39 TND).
- Alerte : Samsung A25 en rupture — ne pas proposer.
- Promo officielle catalogue : -15% sur les coques, affichée en boutique.

SCRIPTS TERRAIN ÉPROUVÉS :
[S1] Objection prix — Reformuler en coût par jour ("moins de 4 TND/jour sur 36 mois"),
     valoriser la reprise de l'ancien téléphone. Impact : +18% de closing.
[S2] Bundle accessoire — Proposer la coque au moment du passage en caisse, pas avant.
     Impact : +9% de panier moyen.
[S3] Client "je vais réfléchir" — Proposer de mettre l'article de côté 24h et prendre
     le numéro. Impact : 40% de retours effectifs."""

_SYSTEM = f"""Tu es le coach commercial des conseillers en boutique Ooredoo Tunisie.
Tu réponds en français, en 3 à 6 phrases, de façon concrète et actionnable.

RÈGLES STRICTES :
- Chaque chiffre (prix, stock, CA) doit provenir des données ci-dessous. Si une
  information manque, dis-le — n'invente jamais.
- Jamais de remise ni d'offre hors promo officielle du catalogue.
- Ne propose jamais un produit en rupture.
- Hors vente/stock/télécom : refuse poliment et recentre.

{_CONTEXT}"""


def run(repeat: int = 1) -> dict:
    cases = [c for c in load_dataset("coach_qa.json") if c.get("benchmark")]
    providers = load_providers()

    candidates = [(p, m) for p in providers.values() if p.available for m in p.models]
    if not candidates:
        print("Aucun provider configuré (clés API absentes du .env).")
        sys.exit(2)

    print(f"  {len(cases)} questions × {len(candidates)} modèles × {repeat} passage(s)\n")

    board: list[dict] = []
    for provider, model in candidates:
        tag = f"{provider.name}/{model}"
        answers, latencies, errors = [], [], 0
        scores_acc = {c: [] for c in CRITERIA}
        hallucinations, judged_n = 0, 0
        tokens_out = 0

        for case in cases:
            for _ in range(repeat):
                res = chat(provider, model,
                           [{"role": "system", "content": _SYSTEM},
                            {"role": "user", "content": case["question"]}],
                           temperature=0.3, max_tokens=500)
                if not res.ok:
                    errors += 1
                    answers.append({"id": case["id"], "error": res.error})
                    continue
                latencies.append(res.latency_ms)
                tokens_out += res.output_tokens

                j = judge_answer(case["question"], res.text,
                                 context=_CONTEXT,
                                 expected_behaviors=case.get("expected_behaviors"),
                                 avoid_model=model)
                entry = {"id": case["id"], "latency_ms": round(res.latency_ms),
                         "answer": res.text}
                if j.ok:
                    judged_n += 1
                    hallucinations += int(j.hallucination)
                    for c in CRITERIA:
                        scores_acc[c].append(j.scores[c])
                    entry["judge"] = {**j.scores, "mean": j.mean,
                                      "hallucination": j.hallucination,
                                      "verdict": j.verdict,
                                      "judge_model": j.judge_model}
                else:
                    entry["judge"] = {"error": j.error}
                answers.append(entry)

        means = {c: round(sum(v) / len(v), 2) if v else None
                 for c, v in scores_acc.items()}
        global_mean = (round(sum(m for m in means.values() if m is not None)
                             / len([m for m in means.values() if m is not None]), 2)
                       if judged_n else None)
        row = {
            "model": tag,
            "n_answers": len(cases) * repeat - errors,
            "errors": errors,
            "latency": latency_summary(latencies),
            "output_tokens_total": tokens_out,
            "scores": means,
            "global_mean": global_mean,
            "hallucination_rate": round(hallucinations / judged_n, 3) if judged_n else None,
            "answers": answers,
        }
        board.append(row)
        lat = row["latency"].get("p50_ms", "-")
        print(f"  {tag:<55} score={global_mean}  p50={lat}ms  "
              f"halluc={row['hallucination_rate']}  err={errors}")

    ranking = sorted(
        (r for r in board if r["global_mean"] is not None),
        key=lambda r: (-r["global_mean"], r["hallucination_rate"] or 0,
                       r["latency"].get("p50_ms", 1e9)),
    )

    print("\n" + "=" * 62)
    print("  CLASSEMENT (score juge ↓, hallucination ↑, latence p50 ↑)")
    print("=" * 62)
    for i, r in enumerate(ranking, 1):
        print(f"  {i}. {r['model']:<52} {r['global_mean']}/5  "
              f"halluc={r['hallucination_rate']:.0%}  p50={r['latency'].get('p50_ms')}ms")

    summary = {
        "suite": "model_benchmark",
        "n_questions": len(cases),
        "repeat": repeat,
        "ranking": [{"model": r["model"], "global_mean": r["global_mean"],
                     "hallucination_rate": r["hallucination_rate"],
                     "p50_ms": r["latency"].get("p50_ms"),
                     "p95_ms": r["latency"].get("p95_ms"),
                     "scores": r["scores"], "errors": r["errors"]}
                    for r in ranking],
        "details": board,
    }
    save_results("model_benchmark", summary)
    try:
        from evals.langfuse_sink import push_models
        n = push_models(summary)
        if n:
            print(f"  {n} traces poussées vers Langfuse (tag `eval`)")
    except Exception as e:
        print(f"  Langfuse non poussé : {e}")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1,
                        help="Passages par question (>1 pour mesurer la variance)")
    args = parser.parse_args()
    run(repeat=args.repeat)
