"""
rag/evaluation/run_eval.py — Mesure la qualité du retrieval sur le golden set.

    python -m app.sales.data.rag.evaluation.run_eval
    python -m app.sales.data.rag.evaluation.run_eval --top-k 5 --verbose

Métriques :
  • hit@k          — le domaine attendu apparaît dans le top_k
  • MRR            — 1/rang du premier document du domaine attendu
  • token recall   — au moins un terme attendu figure dans un document retenu
  • purity         — aucun terme interdit ne figure dans les documents retenus
  • abstention     — sur les requêtes hors-sujet, relevant == False

À utiliser comme garde-fou avant de toucher aux poids du reranker : un gain sur
une requête se paie souvent sur une autre. Sans mesure, on optimise à l'aveugle.
"""

import argparse
import sys

from app.sales.data.rag.evaluation.golden_set import GOLDEN
from app.sales.data.rag.query import normalize
from app.sales.data.rag.retriever import retrieve
from app.sales.data.rag.settings import TOP_K


def _contains(docs, tokens: list[str]) -> bool:
    haystack = normalize(" ".join(f"{d.title} {d.text}" for d in docs))
    return any(normalize(t) in haystack for t in tokens)


def evaluate(top_k: int = TOP_K, store_id: str = "I63", hour: int = 15,
             verbose: bool = False) -> dict:
    stats = {
        "n_grounded": 0, "hits": 0, "mrr_sum": 0.0,
        "token_recall": 0, "purity_ok": 0, "purity_total": 0,
        "n_offtopic": 0, "abstentions": 0,
        "failures": [],
    }

    for case in GOLDEN:
        query = case["query"]
        result = retrieve(query, store_id=store_id, hour=hour, top_k=top_k)
        docs = result.docs

        # ── Requêtes hors-sujet : on attend une abstention ────────────────────
        if not case.get("expect_relevant", True):
            stats["n_offtopic"] += 1
            if not result.relevant:
                stats["abstentions"] += 1
            else:
                stats["failures"].append(
                    f"[abstention] '{query}' -> relevant=True "
                    f"(top={result.top_score:.2f}, {docs[0].title[:40] if docs else '-'})"
                )
            continue

        # ── Requêtes ancrées ─────────────────────────────────────────────────
        stats["n_grounded"] += 1

        expected = case.get("expect_domain")
        rank = next((i for i, d in enumerate(docs) if d.domain == expected), None)
        if rank is not None:
            stats["hits"] += 1
            stats["mrr_sum"] += 1.0 / (rank + 1)
        else:
            found = ", ".join(sorted({d.domain for d in docs})) or "aucun"
            stats["failures"].append(
                f"[hit@{top_k}] '{query}' -> domaine '{expected}' absent (obtenu : {found})"
            )

        if (tokens := case.get("expect_tokens")):
            if _contains(docs, tokens):
                stats["token_recall"] += 1
            else:
                stats["failures"].append(
                    f"[tokens] '{query}' -> aucun de {tokens} dans le top_k"
                )

        if (forbidden := case.get("forbid_tokens")):
            stats["purity_total"] += 1
            if not _contains(docs, forbidden):
                stats["purity_ok"] += 1
            else:
                stats["failures"].append(
                    f"[purete] '{query}' -> terme interdit present parmi {forbidden}"
                )

        if verbose:
            print(f"\nQ: {query}  [{result.mode}, {result.latency_ms:.0f}ms]")
            for d in docs:
                print(f"   {d.score:.3f} {d.domain:<20} cos={d.cosine:.3f} "
                      f"bm25={d.bm25:6.2f}  {d.title[:44]}")

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluation du retrieval RAG")
    parser.add_argument("--top-k", type=int, default=TOP_K)
    parser.add_argument("--store", default="I63")
    parser.add_argument("--hour", type=int, default=15)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    s = evaluate(top_k=args.top_k, store_id=args.store, hour=args.hour,
                 verbose=args.verbose)

    ng = max(1, s["n_grounded"])
    no = max(1, s["n_offtopic"])
    pt = max(1, s["purity_total"])

    print("\n" + "=" * 62)
    print(f"  GOLDEN SET  ({s['n_grounded']} ancrees + {s['n_offtopic']} hors-sujet, top_k={args.top_k})")
    print("=" * 62)
    print(f"  hit@{args.top_k}        {s['hits']}/{s['n_grounded']}   ({s['hits']/ng:.0%})")
    print(f"  MRR            {s['mrr_sum']/ng:.3f}")
    print(f"  token recall   {s['token_recall']}/{s['n_grounded']}   ({s['token_recall']/ng:.0%})")
    print(f"  purete         {s['purity_ok']}/{s['purity_total']}   ({s['purity_ok']/pt:.0%})")
    print(f"  abstention     {s['abstentions']}/{s['n_offtopic']}   ({s['abstentions']/no:.0%})")

    if s["failures"]:
        print("\n  Echecs :")
        for f in s["failures"]:
            print(f"    - {f}")
    else:
        print("\n  Aucun echec.")

    # Sortie non nulle si la qualite passe sous un seuil defendable.
    ok = (s["hits"] / ng >= 0.80
          and s["abstentions"] / no >= 0.66
          and s["purity_ok"] / pt >= 1.0)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
