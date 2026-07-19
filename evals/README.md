# evals — Suite d'évaluation LLM

Quatre bancs d'essai indépendants + un rapport agrégé. Tout s'exécute depuis la
racine du repo, les clés API sont lues dans `.env`.

| Banc | Commande | Besoin | Mesure |
|---|---|---|---|
| Guardrail | `python -m evals.run_guardrail` | rien (offline, déterministe) | accuracy/F1 statut, P/R/F1 par règle G1–G7, taux de faux blocage |
| RAG retrieval | `python -m evals.run_rag` | Milvus ou fallback corpus | hit@k, MRR, token recall, pureté, **abstention** |
| Coach E2E | `python -m evals.run_coach` | serveur lancé (`uvicorn app.main:app`) + clés LLM pour le juge | taux de réponse, latence p50/p95, checks déterministes, juge 0–5, hallucination |
| Modèles | `python -m evals.run_models` | clés Mistral/Groq/OpenRouter | classement des modèles à prompt/contexte/juge constants |

Puis :

```bash
python -m evals.report     # → evals/results/REPORT.md
```

## Méthodologie

- **Déterministe d'abord** : tout ce qui peut être vérifié sans LLM l'est
  (patterns de remise interdite, fuite de prompt, statuts guardrail, retrieval).
- **LLM-as-judge** pour le qualitatif : grille 0–5 (pertinence, ancrage,
  actionnabilité, langue, sécurité), température 0, sortie JSON stricte.
  Le juge **n'est jamais le modèle évalué** (biais d'auto-préférence).
- **Ancrage mesurable** : le benchmark modèles utilise un contexte synthétique
  figé — tout chiffre absent du contexte est une hallucination par définition.
- **Golden set par propriétés** (domaine, tokens, abstention) et non par
  doc_id : le corpus RAG est vivant, les IDs ne sont pas stables.

## Voir les évaluations dans Langfuse

Chaque run pousse automatiquement ses résultats vers Langfuse (si joignable) :
chaque cas devient une trace `eval-coach` / `eval-models`, chaque critère du
juge un score `eval/pertinence`, `eval/ancrage`, … (normalisé 0–1), et chaque
suite une trace résumé `eval-<suite>-summary`.

Dans l'UI (http://localhost:3001, projet des clés `LANGFUSE_*` du `.env`) :
- **Traces** → filtrer par tag `eval` (le tag horodaté identifie le run)
- **Scores** → colonnes `eval/*` comparables d'un run à l'autre

Re-pousser des résultats déjà sur disque : `python -m evals.langfuse_sink`.
Prérequis : `docker compose up -d langfuse` (et `standalone` pour Milvus).

## RAGAS ?

La lib `ragas` n'est pas utilisée : mêmes mesures implémentées en natif —
`ancrage` = faithfulness, `pertinence` = answer relevance, golden set retrieval
= context recall/precision. Avantages : juge en français, providers du projet
(Mistral/Groq) au lieu d'OpenAI par défaut, zéro dépendance lourde, scores
poussés dans Langfuse. `pip install ragas` reste possible en complément si un
chiffre « RAGAS officiel » est exigé dans le rapport.

## Étendre les datasets

- `datasets/guardrail_cases.json` — cas adversariaux guardrail (`inputs` →
  `expected.status` + `expected.rules`)
- `datasets/coach_qa.json` — questions conseiller (`expected_behaviors` pour le
  juge, `checks` déterministes, `benchmark: true` pour inclure la question dans
  le benchmark modèles)
- Golden set retrieval : `app/sales/data/rag/evaluation/golden_set.py`

## Codes de sortie (CI)

Chaque runner sort non-zéro sous un seuil défendable (guardrail < 90% accuracy,
RAG hit@k < 80% ou abstention < 66%, coach < 90% de réponses) — branchable tel
quel dans GitHub Actions.
