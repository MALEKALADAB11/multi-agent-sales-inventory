# evals — Suite d'évaluation LLM

Quatre bancs d'essai indépendants + un rapport agrégé. Tout s'exécute depuis la
racine du repo, les clés API sont lues dans `.env`.

| Banc | Commande | Besoin | Mesure |
|---|---|---|---|
| Guardrail | `python -m evals.run_guardrail` | rien (offline, déterministe) | accuracy/F1 statut, P/R/F1 par règle G1–G7, taux de faux blocage |
| RAG retrieval | `python -m evals.run_rag` | Milvus ou fallback corpus | hit@k, MRR, token recall, pureté, **abstention** |
| RAGAS | `python -m evals.run_ragas` | clé LLM + Milvus + Ollama (embeddings) | faithfulness, answer relevancy, context precision, context recall |
| Coach E2E | `python -m evals.run_coach` | serveur lancé (`uvicorn app.main:app`) + clés LLM pour le juge | taux de réponse, latence p50/p95, checks déterministes, juge 0–5, hallucination |
| Modèles | `python -m evals.run_models` | clés Mistral/Groq/OpenRouter | classement des modèles à prompt/contexte/juge constants |

### Benchmark modèles — protocole

`python -m evals.run_models [--repeat 2] [--judges 2] [--retries 3] [--pace 0.4]`

Sept garde-fous, chacun contre un biais précis :

| Garde-fou | Biais évité |
|---|---|
| Retries backoff sur 429/5xx | **survie** : un modèle rate-limité rend 3/10 réponses et gagne sur ses 3 questions les plus faciles |
| Comparaison appariée (questions communes à tous) | moyennes calculées sur des sous-ensembles différents |
| `--repeat` passages | performance mesurée sur un tirage unique ; on obtient aussi la stabilité (σ) |
| Panel de `--judges` juges distincts, jamais le candidat | auto-préférence + biais d'un juge unique ; leur désaccord moyen est reporté |
| Bootstrap IC 95% + groupes d'ex æquo | un écart de 0,2 point sur 10 questions présenté comme un classement |
| Contrôles déterministes (`evals/checks.py`) | tout confier au juge LLM ; remise, rupture, fuite, langue, concision se décident sans LLM |
| Score composite (qualité 50 / fiabilité 20 / latence 15 / coût 15) | choisir un modèle sur la seule note de qualité |

L'**ancrage numérique** exploite le fait que le contexte est figé : l'ensemble
des chiffres autorisés (ceux du contexte + ceux dérivables en **une** opération —
reste à faire, application d'un pourcentage, taux d'atteinte, mise en rythme) est
calculable à l'avance. Il est publié **hors score**, comme signal à relire :
en autorisant les enchaînements d'opérations, l'ensemble passe de 805 à ~2 400
valeurs et absorbe n'importe quelle invention ; en s'y refusant, un calcul en
deux temps légitime (367 ÷ 5 h) est signalé à tort. Aucun réglage ne sépare les
deux — c'est le critère `ancrage` du juge qui tranche le qualitatif.

`--reaggregate` rejoue classement, IC, composite **et contrôles déterministes**
sur les réponses déjà stockées : corriger une règle ne coûte pas 360 appels LLM.

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
chaque cas devient une trace `eval-coach` / `eval-models` / `eval-ragas`, chaque
critère du juge un score `eval/pertinence`, `eval/ragas/faithfulness`, …
(normalisé 0–1), et chaque suite une trace résumé `eval-<suite>-summary`.

Pour RAGAS, en plus des scores finaux, les appels internes du juge (les prompts
de faithfulness, context recall, …) sont tracés en direct via le callback
LangChain de Langfuse — on voit le raisonnement métrique par métrique, pas
seulement le chiffre agrégé.

Dans l'UI (http://localhost:3001, projet des clés `LANGFUSE_*` du `.env`) :
- **Traces** → filtrer par tag `eval` (le tag horodaté identifie le run) ;
  tag `ragas` pour ne voir que ce banc
- **Scores** → colonnes `eval/*` et `eval/ragas/*` comparables d'un run à l'autre

Re-pousser des résultats déjà sur disque : `python -m evals.langfuse_sink`.
Prérequis : `docker compose up -d langfuse` (et `standalone` pour Milvus).

## RAGAS (officiel)

La librairie `ragas` est intégrée (`python -m evals.run_ragas`) et branchée sur
les modèles du projet, pas sur OpenAI :

- **Juge** : Mistral > Groq > OpenRouter (via les wrappers LangChain), clés lues
  dans `.env` — voir `evals/ragas_provider.py`.
- **Embeddings** : Ollama `bge-m3`, exactement le modèle du RAG
  (`app/sales/data/rag/settings.py`), pour que RAGAS mesure la pertinence dans le
  même espace vectoriel que le retriever.

Le banc évalue la **chaîne RAG complète** : pour chaque requête de
`datasets/ragas_qa.json`, il fait un retrieval réel (`retriever.retrieve`),
génère une réponse ancrée sur ces seuls contextes, puis calcule les quatre
métriques RAGAS :

| Métrique RAGAS | Ce qu'elle mesure |
|---|---|
| `faithfulness` | la réponse n'affirme rien en dehors des contextes (anti-hallucination) |
| `answer_relevancy` | la réponse traite bien la question posée (nécessite les embeddings) |
| `llm_context_precision_without_reference` | les contextes remontés sont réellement utiles |
| `context_recall` | les contextes couvrent la réponse de référence (`reference` du dataset) |

Sans Ollama joignable, `answer_relevancy` est sautée ; les trois autres ne
demandent que le LLM. Seuil CI : `faithfulness ≥ 0.7`.

**Garde-fou retrieval vide** : si aucun cas ne remonte de contexte (Milvus
éteint), le banc **abandonne** au lieu de publier des `0.0`. Un retriever muet
produit des scores RAGAS nuls parfaitement crédibles — la chaîne débranchée est
notée comme une chaîne mauvaise. Vécu le 20/07 : 8 cas à 0,0 publiés alors que
`n_contexts = 0` partout. La couverture (`context_coverage`) est désormais dans
le résumé, et un run partiel affiche un avertissement.

> **Contrainte de dépendances** : les deps langchain de `ragas` ne sont pas
> bornées en amont. Les pins `langchain==0.3.14` / `langchain-core==0.3.29` /
> `langgraph==0.2.56` de `requirements.txt` doivent rester : sans eux, `ragas`
> tire langchain 1.x et casse l'orchestration LangGraph du projet.

### RAGAS vs. métriques maison

Le banc `run_rag` (métriques retrieval maison : hit@k, MRR, abstention…) reste :
il teste le retrieval **seul**, sans LLM, y compris les requêtes hors-sujet où le
RAG doit s'abstenir — un angle que RAGAS ne couvre pas (ses métriques présupposent
un contexte pertinent). Les deux sont complémentaires : `run_rag` pour le
retriever, `run_ragas` pour la chaîne retriever + génération.

## Étendre les datasets

- `datasets/guardrail_cases.json` — cas adversariaux guardrail (`inputs` →
  `expected.status` + `expected.rules`)
- `datasets/coach_qa.json` — questions conseiller (`expected_behaviors` pour le
  juge, `checks` déterministes, `benchmark: true` pour inclure la question dans
  le benchmark modèles)
- Golden set retrieval : `app/sales/data/rag/evaluation/golden_set.py`
- `datasets/ragas_qa.json` — requêtes RAGAS (`question` + `reference`, la réponse
  idéale servant au `context_recall`)

## Codes de sortie (CI)

Chaque runner sort non-zéro sous un seuil défendable (guardrail < 90% accuracy,
RAG hit@k < 80% ou abstention < 66%, coach < 90% de réponses) — branchable tel
quel dans GitHub Actions.
