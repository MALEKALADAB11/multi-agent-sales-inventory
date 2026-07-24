"""
run_rag.py — Banc d'essai retrieval, adossé au golden set existant du RAG.

Réutilise app/sales/data/rag/evaluation (hit@k, MRR, token recall, pureté,
abstention) et normalise la sortie dans evals/results/ pour le rapport global.

L'abstention est la métrique critique : un retriever qui ne sait pas dire
« rien de pertinent » fabrique des hallucinations en aval du coach.

    python -m evals.run_rag [--top-k 5] [--store I63]
"""

from __future__ import annotations

import argparse
import sys

from evals.common import save_results

from app.sales.data.rag.evaluation.run_eval import evaluate
from app.sales.data.rag.settings import TOP_K


def run(top_k: int = TOP_K, store_id: str = "I63", hour: int = 15) -> dict:
    s = evaluate(top_k=top_k, store_id=store_id, hour=hour)

    ng = max(1, s["n_grounded"])
    no = max(1, s["n_offtopic"])
    pt = max(1, s["purity_total"])

    summary = {
        "suite": "rag_retrieval",
        "top_k": top_k,
        "store_id": store_id,
        "n_grounded": s["n_grounded"],
        "n_offtopic": s["n_offtopic"],
        "hit_rate":        round(s["hits"] / ng, 3),
        "mrr":             round(s["mrr_sum"] / ng, 3),
        "token_recall":    round(s["token_recall"] / ng, 3),
        "purity":          round(s["purity_ok"] / pt, 3),
        "abstention_rate": round(s["abstentions"] / no, 3),
        "failures": s["failures"],
    }

    print("=" * 62)
    print(f"  RAG RETRIEVAL — {s['n_grounded']} ancrées + {s['n_offtopic']} hors-sujet (top_k={top_k})")
    print("=" * 62)
    print(f"  hit@{top_k}          {summary['hit_rate']:.1%}")
    print(f"  MRR            {summary['mrr']:.3f}")
    print(f"  token recall   {summary['token_recall']:.1%}")
    print(f"  pureté         {summary['purity']:.1%}")
    print(f"  abstention     {summary['abstention_rate']:.1%}")
    if s["failures"]:
        print(f"\n  {len(s['failures'])} échec(s) :")
        for f in s["failures"]:
            print(f"    - {f}")

    save_results("rag_retrieval", summary)
    try:
        from evals.langfuse_sink import push_aggregate
        if push_aggregate("rag", summary):
            print("  Résumé poussé vers Langfuse (tag `eval`)")
    except Exception as e:
        print(f"  Langfuse non poussé : {e}")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=TOP_K)
    parser.add_argument("--store", default="I63")
    parser.add_argument("--hour", type=int, default=15)
    args = parser.parse_args()
    summary = run(top_k=args.top_k, store_id=args.store, hour=args.hour)
    ok = summary["hit_rate"] >= 0.8 and summary["abstention_rate"] >= 0.66
    sys.exit(0 if ok else 1)
