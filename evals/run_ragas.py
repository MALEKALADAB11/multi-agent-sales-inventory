"""
run_ragas.py — Évaluation RAGAS (officielle) de la chaîne RAG du projet.

Contrairement à run_rag.py (métriques retrieval maison sur le golden set), ce banc
utilise la vraie librairie `ragas` pour scorer la chaîne complète retriever +
génération ancrée, avec les modèles du projet (Mistral/Groq + embeddings Ollama
bge-m3), pas OpenAI.

Pour chaque requête du dataset :
  1. retrieval réel  — app.sales.data.rag.retriever.retrieve → contextes (texte plein)
  2. génération ancrée — réponse produite à partir de ces seuls contextes
  3. métriques RAGAS :
       • faithfulness                          — la réponse ne dit rien hors des contextes
       • answer_relevancy                      — la réponse traite bien la question (embeddings)
       • llm_context_precision_without_reference — les contextes remontés sont utiles
       • context_recall                        — les contextes couvrent la réponse de référence

Les scores partent dans Langfuse (traces `eval-ragas` + scores `eval/ragas/*`),
et les appels internes du juge RAGAS y sont tracés via le callback LangChain.

    python -m evals.run_ragas [--store I63] [--hour 15] [--top-k 3] [--no-embeddings] [--dataset ragas_qa.json]

Prérequis : une clé LLM (MISTRAL/GROQ/OPENROUTER), Milvus (ou fallback corpus) pour
le retrieval, et Ollama pour answer_relevancy (sinon cette métrique est sautée).
"""

from __future__ import annotations

import argparse
import math
import sys

from evals.common import load_dataset, load_providers, save_results, chat
from evals.ragas_provider import build_backends

_GEN_SYSTEM = (
    "Tu es le coach de vente des boutiques Ooredoo Tunisie. On te donne le CONTEXTE "
    "documentaire retrouvé pour une question de conseiller. Réponds UNIQUEMENT à partir "
    "de ce contexte, en français, de façon concise et actionnable. N'invente aucun "
    "chiffre, prix ou fait absent du contexte ; si l'information manque, dis-le clairement."
)


def _generator():
    """Provider/modèle de génération : le modèle rapide du premier provider dispo."""
    for name in ("mistral", "groq", "openrouter"):
        p = load_providers().get(name)
        if p and p.available:
            return p, p.models[-1]
    return None, None


def _grounded_answer(question: str, contexts: list[str]) -> str:
    provider, model = _generator()
    if not provider:
        return ""
    ctx_block = "\n\n".join(f"[C{i+1}] {c}" for i, c in enumerate(contexts)) or "(aucun contexte)"
    messages = [
        {"role": "system", "content": _GEN_SYSTEM},
        {"role": "user", "content": f"CONTEXTE :\n{ctx_block}\n\nQUESTION :\n{question}"},
    ]
    r = chat(provider, model, messages, temperature=0.2, max_tokens=500)
    return r.text if r.ok else ""


def _retrieve_contexts(question: str, store_id: str, hour: int, top_k: int):
    """Contextes texte-plein du vrai retriever du projet."""
    from app.sales.data.rag.retriever import retrieve
    result = retrieve(question, store_id=store_id, hour=hour, top_k=top_k)
    contexts = [f"{d.title}\n{d.text}".strip() for d in result.docs]
    return contexts, result


def _langfuse_callback(session_id: str):
    """Callback LangChain → Langfuse pour tracer les appels internes du juge RAGAS.
    None si Langfuse est injoignable (la sonde de app.core.langfuse évite les hangs)."""
    try:
        from app.core.langfuse import get_langfuse
        if not get_langfuse():
            return None
        import os
        from langfuse.callback import CallbackHandler
        return CallbackHandler(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-pfe-local"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-pfe-local"),
            host=os.getenv("LANGFUSE_HOST", "http://localhost:3001"),
            session_id=session_id,
            user_id="eval-harness",
            trace_name="eval-ragas-judge",
            tags=["eval", "ragas"],
        )
    except Exception:
        return None


def _mean(values: list) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float)) and not math.isnan(v)]
    return round(sum(nums) / len(nums), 3) if nums else None


def run(store_id: str = "I63", hour: int = 15, top_k: int = 3,
        use_embeddings: bool = True, max_workers: int = 2, dataset: str = "ragas_qa.json") -> dict:
    from ragas import EvaluationDataset, SingleTurnSample, evaluate
    from ragas.metrics import (
        Faithfulness,
        LLMContextPrecisionWithoutReference,
        LLMContextRecall,
        ResponseRelevancy,
    )

    backends = build_backends()
    if not backends.can_run:
        print(f"  RAGAS non exécutable : {backends.llm_error}")
        return {"suite": "ragas", "error": backends.llm_error}

    print("=" * 62)
    print(f"  RAGAS — chaîne RAG   juge={backends.judge_label}"
          f"   embeddings={backends.embed_label or '—'}")
    print("=" * 62)
    if not backends.embeddings:
        print(f"  answer_relevancy sauté : {backends.embed_error or 'embeddings désactivés'}")

    cases = load_dataset(dataset)
    samples: list = []
    meta: list[dict] = []
    for case in cases:
        contexts, result = _retrieve_contexts(case["question"], store_id, hour, top_k)
        answer = _grounded_answer(case["question"], contexts)
        samples.append(SingleTurnSample(
            user_input=case["question"],
            response=answer,
            retrieved_contexts=contexts or [""],
            reference=case.get("reference", ""),
        ))
        meta.append({"id": case["id"], "domain": case.get("domain"),
                     "n_contexts": len(contexts), "rag_relevant": result.relevant,
                     "answer": answer})
        print(f"  [{'OK' if answer else 'VIDE'}] {case['id']:<22} "
              f"contexts={len(contexts)} relevant={result.relevant}")

    # Un retriever muet produit des scores RAGAS de 0,0 parfaitement crédibles :
    # la chaîne « débranchée » est notée comme une chaîne « mauvaise ». Vécu le
    # 20/07 (Milvus éteint, 8 cas à 0,0 publiés). On refuse donc de mesurer
    # plutôt que de publier un chiffre qui décrit l'infrastructure, pas le RAG.
    with_ctx = sum(1 for m in meta if m["n_contexts"] > 0)
    coverage = round(with_ctx / len(cases), 3) if cases else 0.0
    if with_ctx == 0:
        print(f"\n  ABANDON : 0 contexte retrouvé sur {len(cases)} cas.")
        print("  Le retriever ne rend rien — Milvus est-il joignable "
              "(`docker compose ps standalone`) ? Sans contexte, RAGAS noterait "
              "l'infrastructure, pas la chaîne RAG.")
        aborted = {"suite": "ragas",
                   "error": "retrieval vide (0 contexte sur tous les cas)",
                   "judge_model": backends.judge_label,
                   "embed_model": backends.embed_label,
                   "n_cases": len(cases), "context_coverage": 0.0,
                   "details": [{**m, "scores": {}} for m in meta]}
        # Écrase les scores du run précédent : laisser un 0,0 périmé sur disque,
        # c'est le retrouver dans le rapport comme s'il avait été mesuré.
        save_results("ragas", aborted)
        return aborted
    if coverage < 1.0:
        print(f"\n  ATTENTION : {len(cases) - with_ctx}/{len(cases)} cas sans contexte — "
              "leurs scores mesurent l'absence de retrieval, pas la génération.")

    metrics = [Faithfulness(), LLMContextPrecisionWithoutReference(), LLMContextRecall()]
    if backends.embeddings and use_embeddings:
        metrics.insert(1, ResponseRelevancy())

    callback = _langfuse_callback(session_id=f"eval-ragas-{store_id}")
    print(f"\n  Évaluation RAGAS ({len(samples)} cas × {len(metrics)} métriques)…")

    # Concurrence bridée. Par défaut RAGAS lance 16 jobs de juge en parallèle,
    # ce que le quota Mistral ne suit pas : sur un premier run, 20 des 32 jobs
    # sont tombés en 429 ou timeout et les moyennes ne portaient plus que sur
    # une poignée de cas — un score calculé sur les survivants d'un rate-limit
    # ne mesure pas la qualité du RAG. Peu de workers, des reprises longues.
    from ragas.run_config import RunConfig
    run_config = RunConfig(
        max_workers = max_workers,
        timeout     = 180,
        max_retries = 10,
        max_wait    = 90,
    )
    result = evaluate(
        dataset=EvaluationDataset(samples=samples),
        metrics=metrics,
        llm=backends.llm,
        embeddings=backends.embeddings,
        callbacks=[callback] if callback else None,
        run_config=run_config,
    )
    df = result.to_pandas()

    metric_names = [m.name for m in metrics]
    rows: list[dict] = []
    for i, m in enumerate(meta):
        scores = {name: (float(df.iloc[i][name]) if name in df.columns
                         and not (isinstance(df.iloc[i][name], float) and math.isnan(df.iloc[i][name]))
                         else None)
                  for name in metric_names}
        rows.append({**m, "scores": scores})

    means = {name: _mean([r["scores"].get(name) for r in rows]) for name in metric_names}
    # Un job de juge qui tombe (429, timeout) laisse un NaN. La moyenne se
    # calcule alors sur les cas survivants : sans ce compteur, un score obtenu
    # sur 2 cas sur 8 se lit comme un résultat complet.
    scored = {name: sum(1 for r in rows if r["scores"].get(name) is not None)
              for name in metric_names}

    summary = {
        "suite": "ragas",
        "judge_model": backends.judge_label,
        "embed_model": backends.embed_label,
        "store_id": store_id,
        "top_k": top_k,
        "n_cases": len(cases),
        "context_coverage": coverage,
        "metrics": metric_names,
        "means": means,
        "scored_cases": scored,
        "details": rows,
    }

    print("\n" + "=" * 62)
    print(f"  RAGAS — {len(cases)} cas")
    print("=" * 62)
    for name in metric_names:
        val = means[name]
        n_ok = scored[name]
        flag = "" if n_ok == len(cases) else f"   ⚠ {n_ok}/{len(cases)} cas notés"
        print(f"  {name:<42} {val if val is not None else '—'}{flag}")
    if any(scored[n] < len(cases) for n in metric_names):
        print("\n  Des cas n'ont pas pu être notés (quota du juge) — relancer avec "
              "--max-workers 1 avant d'interpréter ces moyennes.")

    save_results("ragas", summary)
    try:
        from evals.langfuse_sink import push_ragas
        n = push_ragas(summary)
        if n:
            print(f"  {n} trace(s) poussée(s) vers Langfuse (tag `eval`)")
    except Exception as e:
        print(f"  Langfuse non poussé : {e}")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", default="I63")
    parser.add_argument("--hour", type=int, default=15)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--no-embeddings", action="store_true")
    parser.add_argument("--max-workers", type=int, default=2,
                        help="jobs de juge en parallèle (bas = moins de 429)")
    parser.add_argument("--dataset", default="ragas_qa.json",
                        help="fichier dataset à utiliser (ex: ragas_qa.json, ragas_qa_15.json)")
    args = parser.parse_args()

    s = run(store_id=args.store, hour=args.hour, top_k=args.top_k,
            use_embeddings=not args.no_embeddings, max_workers=args.max_workers, dataset=args.dataset)
    if s.get("error"):
        sys.exit(2)
    # Seuil défendable : faithfulness est la métrique anti-hallucination clé.
    faith = (s.get("means") or {}).get("faithfulness")
    sys.exit(0 if (faith is None or faith >= 0.7) else 1)
