# Évaluation du système multi-agents — LLM-as-judge et bancs d'essai

> Document de référence de la partie *évaluation*. Couvre le juge LLM (conception,
> prompts, validation), les sept bancs d'essai, le juge branché en production, et
> la lecture critique des résultats.
>
> Code : [`evals/`](../evals/) · [`app/core/quality_service.py`](../app/core/quality_service.py)
> · migrations [`0014`](../db/migrations/versions/0014_recommendation_scores.py) et
> [`0016`](../db/migrations/versions/0016_judge_context_snapshot.py)

---

## 1. Le problème à résoudre

Le système produit deux familles de texte que rien de déterministe ne sait noter :

| Sortie | Produite par | Persistée dans |
|---|---|---|
| `recommendation_text` — pourquoi commander 45 unités aujourd'hui | DecisionAgent inventaire | `inventory.recommendations` |
| réponse du coach / `strategie_summary` du Stratège | Coach v11, Agent Stratège | réponse HTTP, `public.hitl_reviews` |

On peut vérifier par une règle qu'une réponse ne promet pas de remise interdite.
On ne peut pas vérifier par une règle qu'elle est *utile à un conseiller*. D'où
un juge LLM — avec la difficulté que cela ajoute : **l'instrument de mesure est
lui-même un modèle de langage, donc bruité et biaisé.** Tout ce document
découle de cette contrainte.

### Trois principes structurants

1. **Déterministe d'abord.** Tout ce qui est décidable par une règle l'est sans
   LLM ([`evals/checks.py`](../evals/checks.py)). Le juge ne garde que ce qui
   demande un jugement : ton, utilité, cohérence du raisonnement.
2. **Le juge n'est jamais le candidat.** Un LLM surnote systématiquement ses
   propres sorties (biais d'auto-préférence) — `avoid_model` exclut le modèle
   évalué du rôle de juge.
3. **Un chiffre non mesurable ne se publie pas.** Une moyenne calculée sur une
   base invalide n'est pas « approximative », elle est trompeuse. Le harnais
   préfère afficher `NON VÉRIFIÉ` qu'un nombre.

---

## 2. Cartographie

```
                        ┌──────────────────── evals/ (hors ligne, batch) ────────────────────┐
                        │                                                                     │
  datasets/*.json ─────►│  run_guardrail   run_rag   run_ragas   run_coach   run_models      │
                        │  run_inventory_recommendations[_live]                              │
                        │            │                    │                                   │
                        │            ▼                    ▼                                   │
                        │      checks.py            judge.py ── common.py (httpx direct)      │
                        │      (0 LLM)              (grilles 0-5)                             │
                        │            └──────► results/*.json ──► report.py ──► REPORT.md      │
                        └────────────────────────────────┬────────────────────────────────────┘
                                                         │ même juge, mêmes grilles
                        ┌────────────────────────────────▼────────────────────────────────────┐
                        │  quality_service.py (en production, hors chemin critique)           │
                        │  inventory.recommendations ─┐                                        │
                        │                             ├─► judge ─► recommendation_scores       │
                        │  public.hitl_reviews ───────┘                    │                   │
                        │                                                  ▼                   │
                        │                              GET /api/v1/monitoring/quality/judge    │
                        │                                        → page Supervision Métier     │
                        └─────────────────────────────────────────────────────────────────────┘
```

| Banc | Commande | Dépendances | Mesure |
|---|---|---|---|
| Guardrail | `python -m evals.run_guardrail` | aucune | accuracy/F1 statut, P/R/F1 par règle G1–G7, faux blocage |
| RAG retrieval | `python -m evals.run_rag` | Milvus ou corpus de repli | hit@k, MRR, token recall, pureté, abstention |
| RAGAS | `python -m evals.run_ragas` | clé LLM + Milvus + Ollama | faithfulness, answer relevancy, context precision/recall |
| Coach E2E | `python -m evals.run_coach` | serveur lancé + clé LLM | taux de réponse, latence, checks, juge 0–5, hallucination |
| Inventory Recs | `python -m evals.run_inventory_recommendations` | clé LLM | 6 critères sur 15 scénarios figés |
| Inventory Recs live | `python -m evals.run_inventory_recommendations_live` | DB + orchestrateur + clé LLM | mêmes 6 critères, sur des SKU réels |
| Benchmark modèles | `python -m evals.run_models` | clés Mistral/Groq/OpenRouter | classement à prompt/contexte/juge constants |
| **Juge live** | `POST /api/v1/monitoring/quality/judge/run` | DB + clé LLM | notation continue des recos réelles |

Agrégation : `python -m evals.report` → [`evals/results/REPORT.md`](../evals/results/REPORT.md).

---

## 3. Le juge LLM — [`evals/judge.py`](../evals/judge.py)

### 3.1 Deux grilles

| Grille | Critères (0–5) | Cible |
|---|---|---|
| **coach** ([`judge.py:23`](../evals/judge.py#L23)) | `pertinence`, `ancrage`, `actionnabilite`, `langue`, `securite` | réponses coach, `strategie_summary` |
| **inventory** ([`judge.py:29`](../evals/judge.py#L29)) | `clarte`, `coherence`, `completude`, `actionabilite`, `richesse`, `ancrage` | `recommendation_text` |

`ancrage` est commun aux deux : *chaque chiffre cité provient-il des données
fournies ?* C'est le critère anti-hallucination, et le seul dont la note dépend
de ce que le juge a reçu comme contexte — voir §4.

### 3.2 Mécanique d'un appel

```
judge_answer / judge_inventory_answer
  │
  ├─ _judge_candidates(avoid_model)     ordre : mistral-large → groq gpt-oss-120b
  │                                             → groq llama-3.3-70b → openrouter
  │                                     filtre : clé présente ET ≠ modèle évalué
  ├─ pour chaque candidat :
  │     chat(temperature=0.0, response_format=json_object, max_tokens=400|700)
  │     ├─ échec HTTP        → candidat suivant
  │     ├─ JSON illisible    → candidat suivant
  │     └─ critère manquant  → candidat suivant   ← validation stricte, pas de note partielle
  └─ Judgment(scores, hallucination, verdict, judge_model)  ou  Judgment(error=…)
```

Points de conception, chacun contre un mode de panne observé :

- **Température 0** — la même réponse doit donner la même note d'un run à l'autre,
  sinon aucune comparaison entre versions n'est possible.
- **`response_format: json_object`** + `extract_json` tolérant aux fences
  ([`common.py:248`](../evals/common.py#L248)) — un juge qui préface son JSON de
  prose casse le parsing.
- **Validation stricte des critères** ([`judge.py:294-297`](../evals/judge.py#L294-L297)) —
  un JSON à qui il manque `richesse` est rejeté et le candidat suivant est
  essayé. Accepter la note partielle produirait une moyenne calculée sur un
  nombre de critères variable selon la ligne.
- **`max_tokens` = 700 pour l'inventaire vs 400 pour le coach** — la liste
  `chiffres_verifies` (§3.3) précède les scores ; tronquée à 400, elle casse le JSON.
- **Ne lève jamais.** Un juge indisponible rend un `Judgment` avec `error` ;
  aucun banc ne plante, aucun cycle de production n'est bloqué.

### 3.3 Les deux mécanismes non triviaux du prompt inventaire

Le prompt système ([`judge.py:195-250`](../evals/judge.py#L195-L250)) fait plus
que décrire les critères — il contraint la procédure du juge.

**a) `richesse` : un contre-exemple, pas une définition.** Demander « la
recommandation est-elle riche ? » fait noter la fluidité grammaticale. Le prompt
oppose donc deux textes concrets :

```
richesse = 1 :  "Action recommandée : ORDER. Stock : 5 jours. Délai : 10 jours."
richesse = 5 :  "Commander 45 unités aujourd'hui — le stock tombe à zéro dans
                 5 jours alors que le délai de livraison moyen est de 10 jours,
                 ce qui laisserait le rayon vide pendant au moins 5 jours."
```

et énonce le discriminant : *« La différence n'est pas la longueur : c'est que le
second relie les chiffres entre eux, le premier les énumère côte à côte. »* Un
texte fluide mais sans lien de conséquence doit être noté ≤ 2.

**b) `ancrage` : une chaîne de vérification imposée par le schéma de sortie.**
Le JSON attendu commence par un champ que le juge doit remplir *avant* de noter :

```json
{"chiffres_verifies": [{"chiffre": "45", "trouve_dans_scenario": true},
                       {"chiffre": "60", "trouve_dans_scenario": false}],
 "clarte": n, "coherence": n, ..., "ancrage": n, "verdict": "..."}
```

L'ordre des clés force le relevé chiffre par chiffre avant la note, et la règle
est absolue : **un seul chiffre non retrouvé ⇒ `ancrage ≤ 1`**, quelle que soit
la qualité de la prose. Le code en tire l'hallucination :

```python
hallucination = scores.get("ancrage", 5) <= 1      # judge.py:300
```

### 3.4 Panel de juges et désaccord

Un juge unique est un point de mesure unique : son biais de style traverse tout
le classement sans que rien ne le signale.

- `judge_panel(..., n_judges=2, judge_fn=…)` ([`judge.py:146`](../evals/judge.py#L146))
  fait noter la même réponse par *n* juges **distincts** (`skip_models` accumule
  les juges déjà utilisés). Le paramètre `judge_fn` permet d'appliquer le panel
  aux deux grilles ; sans lui il était réservé au coach.
- `panel_disagreement(panel)` = écart absolu moyen entre juges sur la note
  globale. **Un désaccord élevé signale un score peu fiable, pas un mauvais
  modèle** — c'est une mesure sur l'instrument, pas sur le candidat.
- `judge_roster()` liste les juges *configurés* (clé présente dans `.env`), sans
  appel réseau. Sert à savoir **avant de dépenser du quota** si un contrôle a un
  sens : panel et déterminisme croisé exigent ≥ 2 juges.

---

## 4. Le point central : `context_level`

### 4.1 Le défaut

`ancrage` se note en retrouvant chaque chiffre du texte dans le contexte fourni.
**Ce que le juge reçoit comme contexte détermine donc ce que ce critère mesure
réellement.** Deux endroits envoyaient au juge un contexte plus pauvre que celui
dont l'agent disposait :

| Chaîne | Ce que l'agent voyait | Ce que le juge recevait |
|---|---|---|
| Juge live inventaire | `baseline_report` + `context_report` + `adjusted_metrics` | 4 colonnes scalaires (`action`, `urgency`, `order_qty`, `confidence`) |
| Coach E2E | bloc de situation complet + catalogue + substituts + scripts RAG | `context_used`, un résumé de KPIs |

Conséquence mécanique : tout chiffre correct venu de la partie invisible était
compté comme inventé. Le run coach du 16/07 affichait **60 % d'hallucinations et
`ancrage` = 2,7** — des verdicts du type *« le prix de l'Assurance Premium
(9 TND/mois) n'est pas ancré dans le CONTEXTE fourni »* alors que ce prix venait
du catalogue réellement injecté dans le prompt. Le chiffre mesurait le trou du
harnais, pas le coach.

### 4.2 La correction

**Côté inventaire** — migration [`0016`](../db/migrations/versions/0016_judge_context_snapshot.py) :

- `inventory.recommendations.context_snapshot` (jsonb) — les trois rapports
  amont, figés avec la ligne au moment de la décision. Écrit par
  `DecisionAgent._build_context_snapshot()`
  ([`agent.py`](../app/inventory/agents/decision/agent.py)), persisté par
  `SyncInventoryRepo.save_recommendation(context_snapshot=…)`.
- Les clés reprennent **exactement** celles de `build_context_string()` du banc
  hors-ligne ([`run_inventory_recommendations.py:85`](../evals/run_inventory_recommendations.py#L85)) :
  c'est ce qui rend une note obtenue en production comparable à une note du banc.

**Côté coach** — champ `grounding` dans la réponse de `POST /api/v1/coach/chat`,
activé par `debug_grounding: true` dans le corps de requête
([`coach_chat.py`](../app/sales/coaching/agents/coach/coach_chat.py)). Il renvoie
le bloc de prompt *exact* (situation, catalogue, RAG, substituts, sorties
d'agents). Le flag court-circuite aussi le cache de dedup — sans quoi un second
run noterait des réponses en cache, sans contexte et sans passer par le modèle.

> Opt-in délibéré : quelques Ko de prompt n'ont rien à faire dans le payload du
> frontend en usage normal.

### 4.3 Le marquage

Chaque score porte désormais son régime de contexte :

| `context_level` | Signification | `ancrage` |
|---|---|---|
| `full` | le juge a vu le contexte réel de la décision | pleinement mesuré |
| `partial` | contexte reconstruit depuis les colonnes / résumé KPI | **non interprétable** |
| `none` | aucune référence (coach uniquement) | **non interprétable** |

Aujourd'hui `partial` couvre : les recommandations antérieures à la migration
0016 (le contexte n'a pas été conservé, il ne le sera jamais) et **tout le
domaine vente** — `hitl_reviews` stocke le résumé du Stratège, pas la météo, le
stock, les promos et les documents RAG qui l'ont produit.

**Règle d'agrégation** (`_domain_summary`,
[`quality_service.py`](../app/core/quality_service.py)) : `ancrage` et le taux
d'hallucination qui en découle ne sont calculés que sur les scores `full`, et la
taille de cette base est publiée (`ancrage_basis`). Les autres critères portent
sur le texte seul et restent calculés sur la totalité.

> Mélanger les deux ne rendrait pas la moyenne « un peu pessimiste » : elle
> serait fausse dans une proportion inconnue et non récupérable a posteriori.

### 4.4 Vérification

Recommandation de test écrite avec snapshot, puis notée par le juge live :

```
scores  : clarte 4 · coherence 5 · completude 5 · actionabilite 5 · richesse 5 · ancrage 5
verdict : « Recommandation claire, cohérente et bien ancrée dans les données du
           scénario, avec une justification précise des chiffres cités
           (45 unités, 5 jours de stock, 10 jours de délai). »
context_level : full
```

Les trois chiffres cités par le verdict proviennent du snapshot. Avant la
correction, le juge n'aurait vu que `{"action":"reorder","urgency":"CRITICAL",
"order_qty":45,"confidence":0.8}` : « 5 jours de stock » et « 10 jours de délai »
auraient été invérifiables ⇒ `ancrage ≤ 1` ⇒ hallucination signalée à tort.

---

## 5. Valider le juge avant de croire ses notes

Un juge qui rend des notes n'est pas un juge qui mesure. Trois contrôles
séparés, dans [`run_inventory_recommendations.py`](../evals/run_inventory_recommendations.py) :

| Contrôle | Protocole | Ce qu'il attrape |
|---|---|---|
| **`--dry-run N`** | imprime le texte complet + le JSON complet du juge pour N cas | bugs de câblage (mauvaise clé de state, contexte vide) qu'un score bas ferait passer pour un défaut du candidat |
| **`--sanity-check` / richesse** | juge le texte LLM *et* le texte du fallback rule-based templaté | un juge qui ne distingue pas une analyse d'un texte à trous |
| **`--sanity-check` / ancrage** | `_corrupt_one_number` remplace un chiffre par `n×3+17`, on rejuge | un juge qui ne recoupe pas réellement les chiffres |
| **`--determinism-check`** | rejoue le même texte 2× (température déjà à 0) | prompt de juge sous-spécifié ; écart attendu ≤ ±1 point |

Le contrôle de déterminisme rapporte `same_judge_each_run` : un écart peut venir
d'une vraie instabilité **ou** d'une rotation vers un autre juge si le premier
était indisponible. Les deux causes ne se corrigent pas de la même façon.

### 5.1 Un contrôle qui n'a pas tourné doit être aussi visible qu'un contrôle échoué

Ces contrôles rendaient `None` quand ils ne s'exécutaient pas, et `report.py`
imprimait alors **une section vide** — indiscernable d'un « rien à signaler ».
Le run du 26/07 a publié `sanity_checks: {richesse: null, ancrage: null}` et un
rapport où le titre « Sanity checks du juge » n'était suivi de rien, alors que
la seule garantie que le juge discrimine avait disparu sans bruit.

Vocabulaire explicite désormais : `OK` / `ECHEC` / `NON_VERIFIE` **avec sa
raison**, toujours présent dans le JSON même quand le contrôle n'a pas été
demandé, et rendu par `report.py` :

```markdown
**Validation du juge** — sans ces trois contrôles au vert, les moyennes ci-dessus
ne sont pas interprétables :
- ⬜ richesse discrimine (LLM vs fallback templaté) — **NON VÉRIFIÉ** (non demandé…)
- ⚠️ ancrage discrimine (chiffre corrompu) — original=5 vs corrompu=5
- ✅ déterminisme (même texte rejoué) — écart max par critère {…}
- ⚠️ juges configurés : 1 (`mistral/mistral-large-latest`)
  ⚠️ Un seul juge : ni panel ni déterminisme croisé — son biais propre traverse
     tous les scores de cette section.
```

### 5.2 Fraîcheur des runs

Les bancs se lancent séparément (l'un demande le serveur, l'autre Milvus, l'autre
du quota). `REPORT.md` juxtaposait un guardrail du 16/07 et un banc inventaire du
26/07 sans le dire. Un bandeau daté ouvre maintenant le rapport, avec marquage
au-delà de `STALE_AFTER_DAYS = 14`.

---

## 6. Ce que le juge ne fait pas — [`evals/checks.py`](../evals/checks.py)

Tout ce qui est décidable mécaniquement l'est sans LLM, et sert de garde-fou aux
notes du juge :

| Contrôle | Règle |
|---|---|
| `pas_de_remise` | gestes commerciaux interdits ; un pourcentage cité n'est une faute que s'il ne correspond à **aucune promo du contexte** |
| `pas_de_fuite` | marqueurs de prompt système, « en tant que modèle de langage » |
| `rupture_respectee` | ne pousse pas un produit en rupture |
| `francais` / fuite anglaise | marqueurs de chaîne de pensée anglaise ayant fui |
| `format_concis` | longueur |
| **ancrage numérique** | tout nombre non présent dans le contexte figé ni dérivable en **une** opération |

Le dernier est publié **hors score**, comme signal à relire. En autorisant les
enchaînements d'opérations, l'ensemble des valeurs légitimes passe de ~805 à
~2 400 et absorbe n'importe quelle invention ; en s'y refusant, un calcul en deux
temps légitime (367 ÷ 5 h) est signalé à tort. Aucun réglage ne sépare les deux —
c'est le critère `ancrage` du juge qui tranche le qualitatif.

---

## 7. Le juge en production — [`app/core/quality_service.py`](../app/core/quality_service.py)

Le juge ne vit pas qu'en batch : il note les recommandations réelles au fil de
l'eau.

```
inventory.recommendations ─┐  (+ context_snapshot)
                           ├─► LEFT JOIN anti-doublon ─► evals.judge ─► recommendation_scores
public.hitl_reviews ───────┘                                                  │
                                                                              ▼
                              GET  /api/v1/monitoring/quality/judge     (agrégats)
                              POST /api/v1/monitoring/quality/judge/run (BackgroundTasks)
```

Propriétés :

- **Hors chemin critique, toujours.** Déclenché par endpoint en `BackgroundTasks`
  ([`monitoring.py:1022`](../app/api/monitoring.py#L1022)) — zéro latence ajoutée
  aux cycles agents.
- **Idempotent.** `UNIQUE (domain, ref_id, judge_model)` + `ON CONFLICT DO NOTHING` ;
  la sélection ne prend que les lignes sans score (`LEFT JOIN … WHERE s.id IS NULL`).
- **Jamais bloquant.** Toute erreur DB/LLM est journalisée et rend un récap vide.
- **Import tardif de `evals`** — le package dépend de `httpx`/`dotenv` ; on
  n'alourdit pas le démarrage de l'API et on reste robuste s'il est absent.
- **Table générique** `public.recommendation_scores` : `domain` + `ref_id` (uuid
  en texte) couvrent les deux domaines sans FK vers un schéma unique.

`get_judge_summary()` alimente la page **Supervision Métier** : moyenne /5 et %,
note par critère, taux d'hallucination, 5 pires verdicts, répartition
`context_levels` et base de calcul `ancrage_basis`.

---

## 8. RAGAS — la chaîne RAG

[`evals/run_ragas.py`](../evals/run_ragas.py) utilise la librairie officielle
`ragas`, branchée sur les modèles du projet (pas OpenAI) : juge
Mistral > Groq > OpenRouter, embeddings Ollama `bge-m3` — **le modèle exact du
retriever**, pour mesurer la pertinence dans le même espace vectoriel.

| Métrique | Mesure | Dernier run |
|---|---|---|
| `faithfulness` | la réponse n'affirme rien hors des contextes | 0,719 |
| `answer_relevancy` | la réponse traite la question (embeddings) | 0,677 |
| `llm_context_precision_without_reference` | les contextes remontés sont utiles | 0,806 |
| `context_recall` | les contextes couvrent la réponse de référence | **0,167** |

Deux garde-fous vécus :

- **Concurrence bridée à 2 workers.** Par défaut RAGAS lance 16 jobs de juge en
  parallèle, que le quota Mistral ne suit pas : sur un premier run, 20 des 32
  jobs sont tombés en 429/timeout et les moyennes ne portaient plus que sur les
  survivants du rate-limit. `scored_cases` compte désormais les cas réellement
  notés par métrique.
- **Abandon si retrieval vide.** Un retriever muet produit des scores RAGAS de
  0,0 parfaitement crédibles — la chaîne débranchée est notée comme une chaîne
  mauvaise. Vécu le 20/07 (Milvus éteint, 8 cas à 0,0 publiés). Le banc refuse
  désormais de mesurer et écrase le résultat précédent
  ([`run_ragas.py:141-161`](../evals/run_ragas.py#L141-L161)).

> `context_recall` à 0,167 est le point ouvert : il compare les contextes
> remontés à la réponse de référence du dataset. Soit les `reference` de
> `ragas_qa.json` sont plus détaillées que ce que le corpus contient, soit le
> retrieval rate réellement. À trancher en relisant cas par cas avant d'agir.

RAGAS ne remplace pas `run_rag` : ce dernier teste le retrieval **seul**, y
compris les requêtes hors-sujet où le RAG doit s'abstenir — un angle que RAGAS
ne couvre pas, ses métriques présupposant un contexte pertinent.

---

## 9. Benchmark comparatif des modèles

`python -m evals.run_models [--repeat 2] [--judges 2] [--retries 3] [--pace 0.4]`

Sept garde-fous, chacun contre un biais précis :

| Garde-fou | Biais évité |
|---|---|
| Retries backoff sur 429/5xx | **survie** : un modèle rate-limité rend 3/10 réponses et gagne sur ses 3 questions les plus faciles |
| Comparaison appariée (questions communes à tous) | moyennes calculées sur des sous-ensembles différents |
| `--repeat` passages | performance mesurée sur un tirage unique ; donne aussi la stabilité σ |
| Panel de `--judges` juges distincts, jamais le candidat | auto-préférence + biais d'un juge unique ; leur désaccord est reporté |
| Bootstrap IC 95 % + groupes d'ex æquo | un écart de 0,2 point sur 10 questions présenté comme un classement |
| Contrôles déterministes | tout confier au juge |
| Composite (qualité 50 / fiabilité 20 / latence 15 / coût 15) | choisir un modèle sur la seule note de qualité |

`--reaggregate` rejoue classement, IC, composite **et** contrôles déterministes
sur les réponses déjà stockées : corriger une règle ne coûte pas 360 appels LLM.

Résultat du 21/07 : les quatre modèles disponibles ont des IC 95 % chevauchants
— **ex æquo**, l'arbitrage se fait sur latence et coût, pas sur la qualité.

---

## 10. Observabilité — Langfuse

Chaque run pousse ses résultats (si Langfuse est joignable) : un cas → une trace
`eval-coach` / `eval-models` / `eval-ragas`, un critère → un score `eval/<critère>`
normalisé 0–1, une suite → une trace résumé `eval-<suite>-summary`.

Pour RAGAS, les appels **internes** du juge (prompts de faithfulness, context
recall…) sont tracés via le callback LangChain : on voit le raisonnement métrique
par métrique, pas seulement le chiffre agrégé.

UI : `http://localhost:3001`, filtrer les traces par tag `eval`. Re-pousser des
résultats déjà sur disque : `python -m evals.langfuse_sink`.

---

## 11. Lire un rapport sans se tromper

Avant de conclure quoi que ce soit d'un chiffre de `REPORT.md` :

1. **Quel âge a le run ?** Bandeau en tête. Au-delà de 14 jours, la section
   décrit une version antérieure du système.
2. **Le juge a-t-il été validé ?** Bloc « Validation du juge ». Trois `NON VÉRIFIÉ`
   ⇒ les moyennes de la section ne sont pas interprétables.
3. **Combien de juges distincts ?** Un seul ⇒ pas de panel, pas de déterminisme
   croisé, biais non mesuré.
4. **Sur quelle base l'ancrage est-il calculé ?** `ancrage_basis` / `context_levels`.
   `full = 0` ⇒ le critère et le taux d'hallucination ne veulent rien dire.
5. **Les IC se chevauchent-ils ?** (benchmark) ⇒ traiter comme ex æquo.
6. **Combien de cas ont réellement été notés ?** (`scored_cases` RAGAS) — une
   moyenne sur 2 cas survivants d'un rate-limit se lit comme un résultat complet.

---

## 12. Limites connues

| Limite | Effet | Statut |
|---|---|---|
| Domaine **vente** sans snapshot de contexte | `ancrage` non interprétable côté sales | ouvert — miroir de 0016 sur `hitl_reviews` |
| 765 recommandations antérieures à 0016 | notables, mais en `partial` définitif | assumé, non rattrapable |
| `judge_roster()` = configuration, pas disponibilité | un juge listé peut répondre 429 | assumé, documenté |
| `context_recall` RAGAS à 0,167 | qualité du RAG mal cernée | ouvert — relecture cas par cas |
| Ancrage numérique déterministe | ne sépare pas invention et calcul en deux temps | assumé, publié hors score |
| Échantillon live figé dans `SAMPLE_PAIRS` | couverture réelle limitée | ouvert — sélection dynamique des SKU actifs |
| `run_inventory_recommendations_live` sans seuil CI | pas de gate | **délibéré** — ses résultats varient avec les données du jour |

---

## 13. Runbook

```bash
# 1. Valider le juge AVANT de faire confiance à ses scores
python -m evals.run_inventory_recommendations --dry-run 2
python -m evals.run_inventory_recommendations --sanity-check --determinism-check

# 2. Bancs sans dépendance externe
python -m evals.run_guardrail
python -m evals.run_rag

# 3. Bancs nécessitant l'infra
docker compose up -d standalone langfuse          # Milvus + Langfuse
uvicorn app.main:app --port 8000                  # requis par run_coach
python -m evals.run_coach
python -m evals.run_ragas --max-workers 2

# 4. Comparaison de modèles (coûteux — ~360 appels)
python -m evals.run_models --repeat 2 --judges 2 --retries 3
python -m evals.run_models --reaggregate          # rejoue les agrégats sans appel LLM

# 5. Rapport agrégé
python -m evals.report                            # → evals/results/REPORT.md

# 6. Juge en production
curl -X POST "localhost:8000/api/v1/monitoring/quality/judge/run?limit=20"
curl      "localhost:8000/api/v1/monitoring/quality/judge?days=30"
```

**Codes de sortie CI** : guardrail < 90 % accuracy, RAG hit@k < 80 % ou abstention
< 66 %, coach < 90 % de réponses, RAGAS `faithfulness` < 0,7.
`run_inventory_recommendations_live` n'a volontairement pas de seuil.

---

## 14. Étendre les datasets

| Fichier | Contenu |
|---|---|
| `datasets/guardrail_cases.json` | cas adversariaux (`inputs` → `expected.status` + `expected.rules`) |
| `datasets/coach_qa.json` | questions conseiller (`expected_behaviors`, `checks`, `benchmark: true`) |
| `datasets/ragas_qa.json` | `question` + `reference` (réponse idéale, sert au `context_recall`) |
| `datasets/inventory_recommendations.json` | scénarios figés (`baseline_report` + `context_report` + `adjusted_metrics`) ; `compare_fallback: true` marque les ancres `richesse` |
| `app/sales/data/rag/evaluation/golden_set.py` | golden set retrieval, **par propriétés** (domaine, tokens, abstention) et non par doc_id — le corpus est vivant, les IDs ne sont pas stables |

> **Contrainte de dépendances** : les deps langchain de `ragas` ne sont pas
> bornées en amont. Les pins `langchain==0.3.14` / `langchain-core==0.3.29` /
> `langgraph==0.2.56` de `requirements.txt` doivent rester : sans eux, `ragas`
> tire langchain 1.x et casse l'orchestration LangGraph du projet.
