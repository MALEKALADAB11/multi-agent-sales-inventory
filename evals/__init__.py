"""
evals — Suite d'évaluation LLM du système multi-agents Ooredoo.

4 bancs d'essai indépendants :
  run_guardrail  — précision/rappel/F1 du Guardrail Agent (déterministe, offline)
  run_rag        — Recall@k, MRR, nDCG du pipeline RAG (Milvus ou fallback corpus)
  run_coach      — qualité des réponses /api/v1/coach/chat (LLM-as-judge + checks)
  run_models     — benchmark comparatif des providers (Mistral/Groq/OpenRouter)

  report         — agrège evals/results/*.json en un rapport Markdown

Usage :
  python -m evals.run_guardrail
  python -m evals.run_rag
  python -m evals.run_coach --base-url http://localhost:8000
  python -m evals.run_models
  python -m evals.report
"""
