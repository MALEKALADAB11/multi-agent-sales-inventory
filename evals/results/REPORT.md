# Rapport d'évaluation LLM — Système multi-agents Ooredoo

_Guardrail exécuté le 2026-07-16T21:14:01_

## 1. Guardrail Agent (jeu adversarial)

- **Accuracy statut** : 100.0% sur 30 cas
- **F1 macro** : 1.0
- **Taux de faux blocage** (réponse légitime bloquée/escaladée) : 0.0%

| Règle | Précision | Rappel | F1 |
|---|---|---|---|
| G1 | 1.0 | 1.0 | 1.0 |
| G2 | 1.0 | 1.0 | 1.0 |
| G3 | 1.0 | 1.0 | 1.0 |
| G4 | 1.0 | 1.0 | 1.0 |
| G5 | 1.0 | 1.0 | 1.0 |
| G6 | 1.0 | 1.0 | 1.0 |
| G7 | 1.0 | 1.0 | 1.0 |

## 2. RAG — Retrieval (golden set)

| Métrique | Valeur | Lecture |
|---|---|---|
| hit@3 | 0.0% | le bon domaine est dans le top-k |
| MRR | 0.0 | rang moyen du premier bon document |
| Token recall | 0.0% | le contenu attendu est retrouvé |
| Pureté | 100.0% | aucun contenu interdit remonté |
| Abstention | 100.0% | sait dire « rien de pertinent » (anti-hallucination) |

## 3. Coach — bout-en-bout (API réelle)

- **Taux de réponse** : 100.0% (20/20)
- **Latence** : p50 = 3433 ms, p95 = 15977 ms
- **Checks déterministes** (remise interdite, fuite de prompt, refus hors-sujet, français) : 88.5%
- **Taux d'usage du RAG** : 0.0%
- **Score juge global** : 3.44/5
- **Taux d'hallucination** : 60.0%

| Critère (0–5) | Moyenne |
|---|---|
| pertinence | 2.95 |
| ancrage | 2.7 |
| actionnabilite | 3.4 |
| langue | 4.15 |
| securite | 4.0 |

## 4. Benchmark comparatif des modèles

Protocole : 10 questions × 1 passage(s), prompt et contexte figés, juge LLM croisé (jamais le modèle évalué).

| Rang | Modèle | Score /5 | Hallucination | p50 | p95 |
|---|---|---|---|---|---|
| 1 | mistral/mistral-large-latest | 5.0 | 0.0% | 4247 ms | 5127 ms |
| 2 | mistral/mistral-small-latest | 4.98 | 0.0% | 1887 ms | 3153 ms |
| 3 | groq/openai/gpt-oss-120b | 4.84 | 0.0% | 1836 ms | 2527 ms |
| 4 | openrouter/nvidia/nemotron-3-super-120b-a12b:free | 4.67 | 33.3% | 2864 ms | 4961 ms |
| 5 | groq/llama-3.3-70b-versatile | 4.64 | 0.0% | 1506 ms | 1735 ms |
| 6 | openrouter/nvidia/nemotron-3-nano-30b-a3b:free | 4.58 | 10.0% | 2030 ms | 4611 ms |

Critères détaillés par modèle :

| Modèle | pertinence | ancrage | actionnabilite | langue | securite |
|---|---|---|---|---|---|
| mistral/mistral-large-latest | 5.0 | 5.0 | 5.0 | 5.0 | 5.0 |
| mistral/mistral-small-latest | 5.0 | 5.0 | 4.9 | 5.0 | 5.0 |
| groq/openai/gpt-oss-120b | 4.5 | 4.9 | 4.9 | 4.9 | 5.0 |
| openrouter/nvidia/nemotron-3-super-120b-a12b:free | 5.0 | 4.0 | 4.33 | 5.0 | 5.0 |
| groq/llama-3.3-70b-versatile | 4.33 | 5.0 | 4.11 | 4.78 | 5.0 |
| openrouter/nvidia/nemotron-3-nano-30b-a3b:free | 4.6 | 4.3 | 4.5 | 4.8 | 4.7 |

---
_Méthodologie : checks déterministes exécutés en local ; scores qualitatifs par LLM-as-judge (température 0, grille 0–5, JSON strict) avec exclusion du modèle évalué du rôle de juge. Retrieval mesuré par propriétés (domaine, tokens, abstention) plutôt que par doc_id figé, le corpus étant vivant._