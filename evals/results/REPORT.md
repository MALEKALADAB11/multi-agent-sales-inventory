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
| hit@3 | 100.0% | le bon domaine est dans le top-k |
| MRR | 0.955 | rang moyen du premier bon document |
| Token recall | 100.0% | le contenu attendu est retrouvé |
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

## 3bis. DecisionAgent inventaire — recommendation_text (LLM-as-judge)

- **Cas évalués** : 15/15 (100.0%)
- **Score juge global** : 4.13/5

| Critère (0–5) | Moyenne |
|---|---|
| clarte | 4.47 |
| coherence | 4.4 |
| completude | 4.27 |
| actionabilite | 4.47 |
| richesse | 4.07 |
| ancrage | 4.13 |

**Ancre `richesse`** (LLM vs fallback rule-based) :
- `critical_stockout-02` — LLM=5 vs rule-based=5
- `hold_overstock-01` — LLM=5 vs rule-based=4

**Sanity checks du juge** :

## 4. Benchmark comparatif des modèles

Protocole : 10 questions × 2 passage(s), prompt et contexte figés, panel de 2 juge(s) LLM distincts (jamais le modèle évalué), 3 réessais sur 429/5xx.

Qualité mesurée sur les **10/10 questions communes** à tous les modèles (comparaison appariée — sinon un modèle rate-limité serait noté sur un sous-ensemble plus favorable).

| Rang | Modèle | Composite | Qualité /5 | IC 95% | Dispo | Checks | Halluc. | p50 | $/1k rép. |
|---|---|---|---|---|---|---|---|---|---|
| 1 | mistral/mistral-small-latest | 0.953 | 4.88 | 4.79–4.965 | 100.0% | 100.0% | 5.0% | 1775 ms | 0.078 |
| 2 | groq/llama-3.3-70b-versatile | 0.861 | 4.79 | 4.653–4.911 | 100.0% | 100.0% | 0.0% | 1357 ms | 0.37 |
| 3 | groq/openai/gpt-oss-120b | 0.842 | 4.91 | 4.78–4.995 | 100.0% | 100.0% | 0.0% | 1765 ms | 0.327 |
| 4 | mistral/mistral-large-latest | 0.733 | 4.92 | 4.795–4.99 | 100.0% | 98.0% | 5.0% | 4545 ms | 1.951 |
| 5 | openrouter/nvidia/nemotron-3-nano-30b-a3b:free | 0.15 | — | — | 0.0% | — | — | — | — |
| 6 | openrouter/nvidia/nemotron-3-super-120b-a12b:free | 0.15 | — | — | 0.0% | — | — | — | — |

Score composite = 50% qualite + 20% fiabilite + 15% latence + 15% cout (qualité pondérée par les contrôles déterministes).

**Écarts non concluants** (IC 95% chevauchants — à traiter comme ex æquo) :
- mistral/mistral-large-latest ≈ groq/openai/gpt-oss-120b ≈ mistral/mistral-small-latest ≈ groq/llama-3.3-70b-versatile

Critères du juge (0–5), contrôles déterministes et signaux :

| Modèle | pertinence | ancrage | actionnabilite | langue | securite | remise | rupture | concision | chiffres à relire | stabilité σ | désaccord juges |
|---|---|---|---|---|---|---|---|---|---|---|---|
| mistral/mistral-small-latest | 4.88 | 4.8 | 4.83 | 4.95 | 4.97 | 100.0% | 100.0% | 100.0% | 5.0% | 0.216 | 0.19 |
| groq/llama-3.3-70b-versatile | 4.68 | 4.84 | 4.55 | 4.89 | 5.0 | 100.0% | 100.0% | 100.0% | 5.0% | 0.303 | 0.025 |
| groq/openai/gpt-oss-120b | 4.83 | 4.88 | 4.97 | 4.97 | 4.9 | 100.0% | 100.0% | 100.0% | 5.0% | 0.273 | 0.105 |
| mistral/mistral-large-latest | 4.85 | 4.83 | 4.95 | 4.97 | 4.97 | 100.0% | 100.0% | 90.0% | 10.0% | 0.237 | 0.091 |
| openrouter/nvidia/nemotron-3-nano-30b-a3b:free | — | — | — | — | — | — | — | — | — | None | None |
| openrouter/nvidia/nemotron-3-super-120b-a12b:free | — | — | — | — | — | — | — | — | — | None | None |

_« Chiffres à relire » n'est pas une faute : le contrôle d'ancrage signale tout nombre non dérivable en une opération du contexte figé — il attrape un prix inventé comme un calcul légitime en deux temps (367 ÷ 5 h). Il est publié hors score ; le critère `ancrage` du juge tranche le qualitatif._

---
_Méthodologie : checks déterministes exécutés en local ; scores qualitatifs par LLM-as-judge (température 0, grille 0–5, JSON strict) avec exclusion du modèle évalué du rôle de juge. Retrieval mesuré par propriétés (domaine, tokens, abstention) plutôt que par doc_id figé, le corpus étant vivant._