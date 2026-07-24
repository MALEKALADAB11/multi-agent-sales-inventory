# Choix des modèles & évaluation — système multi-agents Ooredoo

Trois questions, trois sections : **quels modèles et pourquoi**, **comment on le
prouve** (benchmark), **ce que RAGAS mesure** que le reste ne mesure pas.

---

## 1. Les modèles du système

Le système n'utilise pas « un LLM ». Il utilise **cinq familles de modèles**,
choisies par contrainte, pas par préférence.

### 1.1 Vue d'ensemble

| Brique | Modèle | Où | Pourquoi celui-là |
|---|---|---|---|
| Coach (rédaction) | `openai/gpt-oss-120b` (Groq) → `nemotron-3-nano-30b` (OpenRouter) → `mistral-small/large` | `app/sales/coaching/agents/coach/coach_chat.py` | le contexte est déjà construit côté serveur : le modèle **rédige**, il ne raisonne pas. Priorité à la latence et au quota |
| Stratège (raisonnement cross-domaine) | `nemotron-3-nano-30b` puis rotation OpenRouter, Mistral en secours | `agents/stratege/nodes.py` | 256k de contexte pour absorber ventes + stock + événements + RAG dans un seul prompt |
| Analyste (séries temporelles) | **aucun LLM sur le chemin critique** — Holt-Winters / MSTL / XGBoost ; `llama3.2` local seulement pour la mise en mots | `agents/analyst/`, `demand_sensing/` | une prévision se calcule, elle ne se génère pas. Le LLM ne touche jamais aux chiffres |
| Guardrail (sécurité) | **aucun LLM** — 7 règles déterministes G1–G7 | `agents/guardrail/guardrail_agent.py` | un garde-fou probabiliste n'est pas un garde-fou. 100 % d'accuracy sur 30 cas adversariaux, reproductible |
| Embeddings RAG | `bge-m3` (Ollama, local, 1024 dim) | `app/sales/data/rag/settings.py` | multilingue et symétrique ; nomic-embed-text est anglophone |
| Juge d'évaluation | `mistral-large` + `gpt-oss-120b` (panel) | `evals/judge.py` | jamais le modèle évalué (auto-préférence) |

### 1.2 Les trois décisions qui structurent tout

**a) Le LLM ne calcule rien.** Prévisions, taux d'atteinte, points de rupture,
suggestions de commande : tout est calculé en SQL/statsforecast/XGBoost et
**injecté** dans le prompt. Le LLM reçoit des chiffres et les met en phrases.
C'est ce qui rend l'hallucination *mesurable* : tout chiffre absent du contexte
injecté est, par construction, inventé (cf. §2.3).

**b) Rotation multi-providers plutôt qu'un modèle unique.** La chaîne de
production est :

```
Groq (rotation multi-clés × 2 modèles)   ← primaire : ~1,5 s, quota généreux
  ↓ 429/503
OpenRouter (nemotron nano → super → gpt-oss → llama)  ← gratuit
  ↓ 429/503
Mistral (large → small → nemo)           ← quota indépendant, qualité haute
  ↓ indisponible
Ollama local (llama3.2)                  ← dégradé mais souverain
  ↓
fallback intent (réponse construite sans LLM)
```

Motif : le projet tourne sur des **quotas gratuits**. Un seul provider = un seul
429 entre le conseiller et sa réponse. Le benchmark (§2) mesure d'ailleurs la
disponibilité comme un critère de premier rang, pas comme un détail d'exploitation.

**c) Réglage anti-raisonnement.** `nemotron-3-nano` est un modèle de raisonnement :
laissé libre, il consomme tout le budget de tokens en chaîne de pensée anglaise
qui finit dans `content` (mesuré : 61 s, zéro réponse exploitable). D'où
`reasoning: {enabled: false}` — mesures sur la même requête : 6,0 s sans réglage,
6,9 s avec `exclude`, **2,3 s désactivé**. Le coach n'a rien à raisonner.

### 1.3 Embeddings : bge-m3 vs nomic-embed-text

Choix mesuré, pas subi. Sur la requête « combien coûte l'iPhone 16 Pro », écart
de cosinus entre la bonne fiche produit et une coque iPhone 6 :

| Modèle | Écart | Conséquence |
|---|---|---|
| `nomic-embed-text` | **−0,053** | classe la coque **devant** la fiche |
| `bge-m3` | **+0,296** | classe correctement |

nomic est entraîné majoritairement sur de l'anglais et exige des préfixes de
tâche asymétriques ; bge-m3 est multilingue et symétrique. Le corpus est en
français, souvent en textes courts : le choix se décide tout seul.

---

## 2. Le benchmark — `python -m evals.run_models`

### 2.1 Le protocole

Variables contrôlées : **même prompt système, même contexte figé, mêmes
questions, même panel de juges**. Seul le modèle change. Le contexte est
synthétique et figé (CA 640/1 007 TND, iPhone 16 à 3 899 TND, Samsung A25 en
rupture, promo −15 % coques, 3 scripts terrain) — ce qui rend l'ancrage
vérifiable mécaniquement.

### 2.2 Sept garde-fous, sept biais

Un benchmark LLM naïf (« une question, une réponse, une note ») est faux de sept
manières. Chacune est traitée :

| Garde-fou | Biais évité | Ce que ça a changé ici |
|---|---|---|
| **Retries + backoff** sur 429/5xx | *biais de survie* | au run précédent, `mistral-large` avait **7 erreurs sur 10** et finissait **1er à 5,00/5** — une moyenne calculée sur 3 réponses |
| **Comparaison appariée** | moyennes sur des sous-ensembles différents | le classement ne compte que les questions auxquelles **tous** les modèles ont répondu |
| **Répétitions** (`--repeat`) | mesure sur un tirage unique | donne l'écart-type intra-modèle = stabilité |
| **Panel de juges distincts** | auto-préférence + biais d'un juge unique | le désaccord moyen entre juges est publié : un écart inférieur à ce désaccord ne conclut rien |
| **Bootstrap IC 95 %** | classer sur 0,2 point de différence | les modèles aux IC chevauchants sont déclarés **ex æquo** |
| **Contrôles déterministes** | tout confier au juge LLM | remise interdite, produit en rupture, fuite de prompt, langue, concision — sans LLM ; l'ancrage numérique est publié **hors score** (§2.3) |
| **Score composite** | choisir sur la seule qualité | qualité 50 % / fiabilité 20 % / latence 15 % / coût 15 %, poids explicites et discutables |

### 2.3 L'ancrage numérique — le contrôle central

Le contexte étant figé, l'ensemble des chiffres légitimes est **calculable à
l'avance** : ceux du contexte, plus ceux qu'on en dérive par les seules
opérations qu'un coach fait réellement — reste à faire (`a − b`), application
d'un pourcentage (`a × b / 100`), taux d'atteinte (`a / b × 100`).

- « il te manque **367** TND » (1 007 − 640) → ancré ✅
- « la coque passe à **33,15** TND » (39 × 0,85) → ancré ✅
- « propose le Redmi à **2 499** TND » → **signalé** ❌ (aucune dérivation possible)

**Pourquoi c'est un signal et non une faute.** La clôture s'arrête volontairement
à **une** opération. En autorisant les enchaînements, l'ensemble des valeurs
« légitimes » passe de 805 à ~2 400 sur ce contexte : assez dense pour que
2 499 TND, 99 TND et 119 TND — trois prix purement inventés — deviennent tous
« ancrés ». Le contrôle ne mesurerait plus rien. Le prix de cette rigueur est
symétrique : un calcul en deux temps légitime (« 367 TND à faire ÷ 5 h = 73 TND/h »)
est signalé à tort.

Aucun réglage ne sépare les deux cas — l'information n'est pas dans le nombre.
D'où la décision de protocole : **l'ancrage numérique est publié hors score**,
avec la liste des chiffres en cause, comme signal à relire. Le score, lui, ne
retient que les règles réellement décidables (remise non autorisée, produit en
rupture, fuite de prompt, langue, concision). Le qualitatif est tranché par le
critère `ancrage` du juge — instrument différent, erreurs non corrélées.

Sur le run du 21/07, ce signal a correctement pointé quatre prix de fibre
inventés (49, 79, 99, 119 TND — le contexte ne contient aucun prix fibre) sur la
question `fibre-01`, et le juge LLM a flaggé indépendamment la même réponse
(« hallucine le prix de la fibre »). Deux instruments qui convergent.

### 2.4 Résultats du 21/07/2026

10 questions × 2 passages × panel de 2 juges, 3 réessais — 6 modèles, ~360 appels.

| Rang | Modèle | Composite | Qualité /5 | IC 95 % | Dispo | Checks | Halluc. | p50 | $/1k rép. |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `mistral-small-latest` | **0,953** | 4,88 | 4,79–4,97 | 100 % | 100 % | 5 % | 1 775 ms | 0,078 |
| 2 | `llama-3.3-70b` (Groq) | 0,861 | 4,79 | 4,65–4,91 | 100 % | 100 % | 0 % | 1 357 ms | 0,370 |
| 3 | `gpt-oss-120b` (Groq) | 0,842 | 4,91 | 4,78–5,00 | 100 % | 100 % | 0 % | 1 765 ms | 0,327 |
| 4 | `mistral-large-latest` | 0,733 | 4,92 | 4,80–4,99 | 100 % | 98 % | 5 % | 4 545 ms | 1,951 |
| 5–6 | `nemotron-3-nano` / `-super` (OpenRouter) | 0,150 | — | — | **0 %** | — | — | — | — |

**Le résultat qui compte est le groupe d'ex æquo.** Les quatre modèles
disponibles ont des IC 95 % **tous chevauchants** (4,79 → 4,92) : sur ce banc,
**leur qualité est statistiquement indiscernable**. Conclusion défendable en
soutenance : ne pas choisir sur la note de qualité — elle ne discrimine pas —
mais sur les axes qui, eux, discriminent. `mistral-small` gagne parce qu'il est
**2,5× plus rapide** et **25× moins cher** que `mistral-large` pour une qualité
que la mesure ne distingue pas.

Trois observations secondaires :

- **Le désaccord entre juges** (0,03 à 0,19 point) est du même ordre que les
  écarts de qualité entre modèles. C'est la preuve chiffrée qu'un classement
  au dixième de point n'aurait aucun sens ici.
- **Les retries ont fait leur travail** : `mistral-large` passe de **7 erreurs
  sur 10** (run du 16/07, sans retry) à **0 sur 20**. Son ancienne 1ʳᵉ place à
  5,00/5 était un artefact.
- **OpenRouter est à 0 % de disponibilité** : quota `free-models-per-day`
  épuisé. Mesure réelle, pas incident de banc — un modèle gratuit indisponible
  reste indisponible en production. D'où sa place en fond de classement malgré
  un coût nul, et la nécessité de la chaîne de repli multi-providers (§1.2).

> Défaut de protocole corrigé au passage : avec deux modèles à 0 réponse,
> l'intersection des questions communes devenait vide et supprimait la
> comparaison de qualité **de tous les autres**. Les modèles sans aucune réponse
> sont désormais exclus de l'appariement (et restent classés, leur
> disponibilité nulle parlant d'elle-même). `--reaggregate` rejoue l'agrégation
> sur les réponses déjà collectées, sans redépenser un appel.

### 2.5 Ce que le benchmark ne dit pas

- 10 questions : un IC large est une information, pas un défaut à masquer.
- Contexte synthétique : mesure la **fidélité au contexte**, pas la qualité du
  contexte réel (c'est le rôle de `run_coach` en bout-en-bout et de RAGAS).
- Tarifs : indicatifs (`_PRICES` dans `run_models.py`), à réviser avec les grilles.

---

## 3. RAGAS — `python -m evals.run_ragas`

### 3.1 Ce que c'est

RAGAS (*Retrieval-Augmented Generation Assessment*) est la librairie de référence
pour évaluer une chaîne RAG **sans jeu de données annoté à la main**. Son principe :
un LLM juge décompose la réponse en affirmations élémentaires et vérifie chacune
contre les contextes retrouvés. On n'écrit pas 200 paires question/réponse — on
écrit une question et une réponse de référence, et le juge fait le reste.

### 3.2 Les quatre métriques utilisées

| Métrique | Question posée | Ce qu'un score bas révèle |
|---|---|---|
| `faithfulness` | chaque affirmation de la réponse est-elle soutenue par les contextes ? | le **générateur** hallucine |
| `answer_relevancy` | la réponse traite-t-elle la question posée ? | le générateur répond à côté (nécessite les embeddings) |
| `llm_context_precision_without_reference` | les contextes remontés sont-ils utiles ? | le **retriever** ramène du bruit |
| `context_recall` | les contextes couvrent-ils la réponse de référence ? | le retriever **rate** de l'information |

Lecture croisée : `faithfulness` bas + `context_precision` haut → problème de
génération. `context_recall` bas → problème d'indexation/retrieval. C'est ce
découpage qui rend la métrique actionnable : elle désigne le coupable.

### 3.3 Comment il est branché ici

RAGAS suppose OpenAI par défaut. Le projet n'utilise pas OpenAI, donc
`evals/ragas_provider.py` lui injecte :

- **juge** : Mistral > Groq > OpenRouter, via les wrappers LangChain (mêmes clés
  `.env` que le coach) ;
- **embeddings** : Ollama `bge-m3`, **exactement le modèle du RAG** — sinon
  `answer_relevancy` mesurerait la pertinence dans un espace vectoriel différent
  de celui du retriever, ce qui n'a pas de sens.

Le banc évalue la chaîne **complète** : retrieval réel (`retriever.retrieve`),
puis génération ancrée sur ces seuls contextes, puis scoring. Seuil CI :
`faithfulness ≥ 0,7`.

### 3.4 RAGAS ne remplace pas `run_rag`

Les métriques RAGAS **présupposent un contexte pertinent**. Elles ne savent pas
évaluer une requête hors-sujet où le bon comportement est de **s'abstenir**.
`run_rag` (hit@k, MRR, pureté, **taux d'abstention**) teste le retriever seul,
sans LLM, y compris sur les requêtes qui ne doivent rien remonter. Les deux sont
complémentaires : `run_rag` pour le retriever, `run_ragas` pour retriever +
génération.

### 3.5 Piège vécu : un run RAGAS à 0,0 n'est pas un mauvais RAG

Le run du 20/07 affichait `faithfulness = 0.0`, `context_precision = 0.0`,
`context_recall = 0.0` sur les 8 cas. Diagnostic : `n_contexts = 0` partout —
Milvus n'était pas joignable, le retriever ne rendait **aucun** contexte, le
générateur répondait « je ne peux pas répondre sans contexte », et RAGAS notait
consciencieusement 0 une chaîne débranchée. `embed_model` était vide, donc
`answer_relevancy` n'était même pas calculée.

Leçon à retenir pour la soutenance : **toujours lire `n_contexts` avant de lire
les scores**. Un 0,0 RAGAS mesure aussi souvent l'infrastructure que le modèle.

Le banc ne peut plus produire cette erreur : si aucun cas ne remonte de contexte,
il **abandonne** avec un diagnostic explicite au lieu de publier des zéros, et
écrase le résultat périmé sur disque (sinon le rapport agrégé continuerait
d'afficher un run invalide comme s'il avait été mesuré). La couverture
(`context_coverage`) figure désormais dans le résumé, et un run partiellement
couvert affiche un avertissement.

> État au 21/07 : Milvus est de nouveau tombé (Docker bloqué sur cette machine),
> le banc RAGAS abandonne donc proprement. À rejouer une fois la stack relancée —
> c'est la seule mesure de la suite qui reste à produire.

---

## 4. La suite d'évaluation complète

| Banc | Ce qu'il isole | Sans LLM ? |
|---|---|---|
| `run_guardrail` | 7 règles de sécurité sur 30 cas adversariaux | ✅ totalement déterministe |
| `run_rag` | retriever seul, y compris l'abstention | ✅ |
| `run_ragas` | retriever + génération ancrée | ❌ juge LLM |
| `run_models` | le modèle, à contexte constant | mixte (checks + panel de juges) |
| `run_coach` | la chaîne réelle via l'API HTTP | mixte |
| `report` | agrégation → `evals/results/REPORT.md` | — |

Chaque run pousse ses traces et scores dans **Langfuse** (tag `eval`), ce qui
permet de comparer deux runs à des semaines d'écart et de voir, pour RAGAS, le
raisonnement du juge métrique par métrique.
