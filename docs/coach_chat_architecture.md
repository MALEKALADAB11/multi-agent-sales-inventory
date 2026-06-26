# Coach Chat — Architecture & Prompts
> Version 8.0 · `coach_chat.py` · Mise à jour : 2026-06-22

---

## 1. Vue d'ensemble

```
Frontend Angular (port 4200)
    │  POST /api/v1/coach/chat
    │  { message, advisor_name, store_id, context{...} }
    ▼
FastAPI root main.py (port 8000)
    │  coach_rag_router → coach_chat.py
    ▼
┌──────────────────────────────────────────────────────────┐
│                     coach_chat()                         │
│                                                          │
│  1. _normalize_store()   store-lac2 → I63               │
│  2. _classify_intent()   → mode / domain / type         │
│  3. Context loaders      → DB psycopg2                   │
│  4. RAG conditionnel     → Milvus (Ollama embeddings)    │
│  5. Prompt builder       → selon mode                    │
│  6. _load_day_history()  → public.coach_interactions     │
│  7. _call_llm()          → OpenRouter gpt-oss-120b:free  │
│  8. save_interaction()   → public.coach_interactions     │
│  9. JSONResponse         → reply + metadata              │
└──────────────────────────────────────────────────────────┘
    │
    ├── Langfuse (port 3001, Docker) — traces observabilité
    └── PostgreSQL (port 5432)
         ├── sales.transactions
         ├── sales.produits
         ├── sales.vw_ca_par_boutique
         ├── inventory.stock_levels
         └── public.coach_interactions
```

---

## 2. Configuration

| Variable d'env | Valeur par défaut | Rôle |
|---|---|---|
| `OPENROUTER_API_KEY` | — | Clé API OpenRouter (obligatoire) |
| `OPENROUTER_MODEL` | `openai/gpt-oss-120b:free` | Modèle LLM |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Embeddings RAG |
| `POSTGRES_HOST` | `localhost` | DB |
| `POSTGRES_DB` | `ooredoo_sales` | DB |
| `POSTGRES_USER` | `postgres` | DB |
| `POSTGRES_PASSWORD` | `admin` | DB |

### Mapping store_id (frontend → DB)

Le frontend envoie des alias ; la DB stocke des codes internes.

```python
_STORE_MAP = {
    "store-lac2":    "I63",   # Boutique Lac 2
    "OOR_LAC_01":    "I63",
    "lac2":          "I63",
    "store-menzah":  "M01",
    "OOR_MENZAH_02": "M01",
    "store-sfax":    "S01",
    "OOR_SFAX_03":   "S01",
}
```

---

## 3. Classification d'intention

`_classify_intent(message)` retourne `{ mode, domain, type, confidence }`.

### Modes disponibles

| Mode | Domain | Déclencheur |
|---|---|---|
| `off_topic` | `none` | Sujets hors télécom/vente sans ancre télécom |
| `inventory` | `inventory` | Mots-clés stock (stock, rupture, disponible, inventaire…) |
| `coaching` | `sales` | Demande explicite d'outil de vente (score ≥ 2 keywords) |
| `conversation` | `sales` | Question générale, salutation, discussion |

### Types coaching reconnus

| Type | Keywords déclencheurs |
|---|---|
| `script` | script, comment vendre, que dire, pitch, argumentaire |
| `objection` | objection, trop cher, il refuse, pas besoin, concurrent |
| `closing` | closing, comment closer, finaliser, il hésite, indécis |
| `upsell` | upsell, cross-sell, bundle, vient d'acheter, complémentaire |
| `forfait` | convertir, migration forfait, passer en 5G, changer de forfait |
| `objectif` | atteindre objectif, combler le gap, rattraper, plan d'action |
| `meteo` | stratégie météo, il pleut que faire, adapter à la météo |

### Types inventory reconnus

| Type | Keywords |
|---|---|
| `stock` | stock, disponible, quantité, inventaire |
| `alerte` | rupture, alerte, critique, danger |
| `reorder` | réappro, commande, livraison, reorder |
| `rotation` | rotation, dormant, best-seller, top ventes |

### Algorithme de scoring

```
1. Compter off_topic_hits et telecom_hits
   → Si off_hits >= 1 ET telecom_hits == 0 → off_topic

2. Compter inv_hits (mots INVENTORY_SIGNALS)
   → Si inv_hits >= 1 → mode=inventory

3. Pour chaque type coaching, compter les keyword hits
   → best_coaching_score = max des scores

4. Compter conv_hits (mots CONVERSATION_SIGNALS)

5. Decision finale :
   - best_coaching_score >= 2                  → coaching
   - best_coaching_score == 1 ET conv_hits==0  → coaching (conf 0.70)
   - sinon                                     → conversation
```

---

## 4. Context Loaders (DB psycopg2)

> Toutes les requêtes utilisent `psycopg2` synchrone via `asyncio.run_in_executor`.
> Les données simulées sont datées juillet 2026 ; les requêtes utilisent `MAX(date_only)`
> plutôt que `CURRENT_DATE` pour éviter les retours vides.

### 4.1 Domaine Sales — `_load_sales_context(store_id, ctx)`

**Priorité des sources :**
1. `ctx` fourni par le frontend (WebSocket live)
2. Si `ca_today == 0` : fallback `sales.vw_ca_par_boutique ORDER BY date_only DESC LIMIT 1`

**Données chargées :**

| Champ | Source |
|---|---|
| `ca_today` | ctx.current_revenue ou vw_ca_par_boutique |
| `ca_target` | ctx.daily_target (défaut 1007 TND) |
| `gap_pct`, `gap_tnd` | calculé |
| `performance` | ca_today / ca_target × 100 |
| `nb_ventes` | ctx ou vw_ca_par_boutique |
| `urgency` | ctx ou MEDIUM |
| `weather` | ctx ou "Tunis" |
| `actions` | ctx.strategie_actions |
| `top_sellers` | sales.transactions — 6 produits, 7 derniers jours, ORDER BY ca DESC |
| `recent_transactions` | sales.transactions — 8 dernières lignes ORDER BY date DESC |

**Requête top_sellers :**
```sql
SELECT t.sku, COALESCE(p.nom, t.sku::text) AS nom,
       SUM(t.quantity) AS qty, SUM(t.lig_ttc) AS ca
FROM sales.transactions t
LEFT JOIN sales.produits p ON p.sku = t.sku
WHERE t.store_id = $1
  AND t.date_only >= (SELECT MAX(date_only) FROM sales.transactions WHERE store_id = $1)
                     - INTERVAL '7 days'
GROUP BY t.sku, p.nom
ORDER BY ca DESC LIMIT 6
```

**Requête recent_transactions :**
```sql
SELECT COALESCE(p.nom, t.sku::text) AS nom,
       t.quantity, t.lig_ttc, t.heure, t.date_only
FROM sales.transactions t
LEFT JOIN sales.produits p ON p.sku = t.sku
WHERE t.store_id = $1
ORDER BY t.date_only DESC, t.heure DESC LIMIT 8
```

---

### 4.2 Domaine Inventory — `_load_inventory_context(store_id)`

**Requête stats globales :**
```sql
SELECT COUNT(*) AS total,
    SUM(CASE WHEN qty <= 0  THEN 1 ELSE 0 END) AS ruptures,
    SUM(CASE WHEN qty BETWEEN 1 AND 5  THEN 1 ELSE 0 END) AS critiques,
    SUM(CASE WHEN qty BETWEEN 6 AND 15 THEN 1 ELSE 0 END) AS warnings,
    SUM(CASE WHEN qty > 15 THEN 1 ELSE 0 END) AS ok_count,
    AVG(qty)::NUMERIC(10,1) AS avg_stock
FROM inventory.stock_levels WHERE store_id = $1
-- qty = COALESCE(quantity_available, quantity, 0)
```

**Requête alertes (rupture + critique + warning) :**
```sql
SELECT sl.sku, COALESCE(p.nom, sl.sku::text), qty,
       CASE WHEN qty <= 0 THEN 'rupture'
            WHEN qty <= 5 THEN 'critical'
            ELSE 'warning' END AS risk_level
FROM inventory.stock_levels sl
LEFT JOIN sales.produits p ON p.sku = sl.sku
WHERE sl.store_id = $1 AND qty <= 15
ORDER BY qty ASC LIMIT 20
```

**Requête produits disponibles (stock > 15) :**
```sql
SELECT sl.sku, COALESCE(p.nom, sl.sku::text), qty
FROM inventory.stock_levels sl
LEFT JOIN sales.produits p ON p.sku = sl.sku
WHERE sl.store_id = $1 AND qty > 15
ORDER BY qty DESC LIMIT 15
```

**Requête top vendeurs :**
```sql
SELECT t.sku, COALESCE(p.nom, t.sku::text) AS nom,
       SUM(t.quantity) AS total_sold,
       COALESCE(sl.quantity_available, sl.quantity, 0) AS stock_restant
FROM sales.transactions t
LEFT JOIN sales.produits p ON p.sku = t.sku
LEFT JOIN inventory.stock_levels sl ON sl.sku = t.sku AND sl.store_id = t.store_id
WHERE t.store_id = $1
  AND t.date_only >= (SELECT MAX(date_only) FROM sales.transactions WHERE store_id = $1)
                     - INTERVAL '7 days'
GROUP BY t.sku, p.nom, sl.quantity_available, sl.quantity
ORDER BY total_sold DESC LIMIT 6
```

**Structure retournée :**

| Champ | Contenu |
|---|---|
| `total_skus` | Nombre total de SKUs |
| `ruptures` | Nombre a 0 unite |
| `critiques` | Nombre entre 1 et 5 |
| `warnings` | Nombre entre 6 et 15 |
| `ok_count` | Nombre > 15 |
| `avg_stock` | Stock moyen / SKU |
| `alert_items` | Liste [{sku, product_name, stock_qty, risk_level}] |
| `ok_items` | Liste [{sku, product_name, stock_qty}] — produits disponibles |
| `critical_items` | Sous-liste de alert_items (rupture + critique uniquement) |
| `top_sellers` | Liste [{sku, nom, total_sold, stock_restant}] |

---

## 5. RAG Pipeline

```
message + context
    │
    ▼
_search_rag(query, hour, top_k, min_score)
    │
    ├── Embed query : POST /api/embeddings Ollama nomic-embed-text (768D)
    │
    ├── Search Milvus collection="coaching_scripts"
    │   Fields: categorie, situation, action, produit, argument, impact,
    │           heure_min, heure_max, jour_semaine
    │
    ├── Score boosting :
    │   +0.15 si heure_min <= current_hour <= heure_max
    │   +0.05 si jour_semaine == weekday actuel
    │
    └── Filtre : score >= min_score → top_k resultats
```

**Activation du RAG :**
- Mode `coaching` → RAG systematique (top_k=3, min_score=0.35)
- Mode `conversation` → RAG uniquement si le message mentionne un produit vendable (top_k=2, min_score=0.45)
- Mode `inventory` → pas de RAG

---

## 6. Memoire — Historique du Jour

`_load_day_history(advisor_name, store_id)` charge depuis `public.coach_interactions` :

```sql
SELECT message, response
FROM public.coach_interactions
WHERE store_id = $1
  AND advisor_name = $2
  AND created_at >= CURRENT_DATE
ORDER BY created_at ASC
LIMIT 8
```

Retourne une liste de `{role: "user"|"assistant", content: str}` injectee dans les messages
OpenRouter avant la question courante. Le LLM a ainsi la memoire de toute la journee.

---

## 7. Prompts

### 7.1 Prompt — Mode CONVERSATION (sales)

> Question generale, salutation, discussion sans outil de vente explicite.
> `max_tokens=350`, `temperature=0.28`

```
Tu es le CoachAgent IA d'Ooredoo Tunisie — un coach de vente bienveillant et competent.

REGLE ABSOLUE : Reponds a la VRAIE question du conseiller. Ne genere PAS de pitch de vente
si on ne t'en demande pas un. Si le conseiller dit "bonjour", dis bonjour. S'il pose une question,
reponds-y. S'il demande un script de vente, genere-en un. Sois NATUREL et UTILE.

GUARDRAILS :
- FRANCAIS avec tutoiement — ton collegue bienveillant, pas un robot
- Si tu ne sais pas, dis-le. Ne fabrique JAMAIS de chiffres, promotions ou offres.
- Prix = UNIQUEMENT ceux du catalogue ci-dessous. Aucune invention.
- Max 120 mots sauf si on te demande un script detaille
- HORS-SUJET INTERDIT : tu ne reponds JAMAIS aux questions sans rapport avec la vente telecom,
  le stock, les produits Ooredoo ou les objectifs commerciaux.
- PAS de JSON, PAS de balises, PAS de "Based on", PAS de "Here is"

CONTEXTE REEL — {advisor_name} :
  CA : {ca_today} / {ca_target} TND ({perf}%)
  Gap : {gap_tnd} TND | {hours_left}h restantes | Urgence : {urgency}
  Meteo : {weather} | Ventes du jour : {nb_ventes}
  [Cause : {cause_racine}]
  [Actions Stratege :
    1. {action} -> {produit_cible}
    ...]

VENTES REELLES (7 derniers jours) :
  1. {nom} — {qty} vendus — {ca} TND
  ...

DERNIERES TRANSACTIONS :
  {heure}h — {nom} x{qty} — {ttc} TND
  ...

CATALOGUE OOREDOO (prix officiels — seuls prix autorises) :
  iPhone 16 Pro: 1 299 TND | Samsung A55 5G: 899 TND | Galaxy S25 Ultra: 1 599 TND
  Forfait 5G Max: 49 TND/mois | Flexi 25Go: 29 TND/mois | Unlimited: 69 TND/mois
  Box Fibre 1Go: 59 TND/mois | Assurance Premium: 9 TND/mois
  AirPods Pro 3: 279 TND | Apple Watch S10: 449 TND
  Bundle: iPhone 16 Pro + 5G Max + Assurance = 1 357 TND (54 TND/mois x24)

[Si RAG pertinent :
  Si la question est liee a un de ces scripts, utilise les arguments.
  Sinon, ignore-les et reponds naturellement.
  {rag_txt}
]

Tu es un VRAI coach — ecoute d'abord, conseille ensuite. Reponds a ce que le conseiller demande.
```

---

### 7.2 Prompt — Mode COACHING (sales)

> Demande explicite d'outil de vente (script, closing, objection…).
> `max_tokens=300` (urgence HIGH/CRITICAL) ou `400`, `temperature=0.18`

```
Tu es le CoachAgent IA d'Ooredoo — coach de vente expert, direct et actionnable.

{instruction_selon_type}
  script    → Genere un SCRIPT DE VENTE en 4-5 etapes numerotees.
               Accroche -> Valeur -> Prix -> Bundle -> Close force.
  objection → Donne la REPONSE A L'OBJECTION.
               Format : "Reponds : [reformulation + argument]  Close : [question fermante]"
  closing   → Donne une TECHNIQUE DE CLOSING precise.
               Urgence naturelle + question de decision.
  upsell    → Donne la technique d'UPSELL/CROSS-SELL.
               Timing "juste apres la signature" + produit + argument.
  forfait   → Donne le script de CONVERSION FORFAIT.
               Calcul comparatif + economie + activation immediate.
  objectif  → Donne un PLAN D'ACTION pour combler {gap} TND en {hl}h.
               Produits avec prix + timing.
  meteo     → Donne la STRATEGIE METEO adaptee a : {weather}.
               Produits adaptes + arguments.

GUARDRAILS :
- FRANCAIS avec tutoiement — direct et motivant
- Prix EXACTS du catalogue uniquement — JAMAIS d'invention
- Ne fabrique JAMAIS d'offres, promotions ou prix temporaires qui n'existent pas
- Vrais chiffres : {gap} TND de gap, {hl}h restantes, {perf}% atteint
- HORS-SUJET INTERDIT : reponds uniquement vente/stock/produits Ooredoo
- Commence DIRECTEMENT par l'action
- Termine par : "Vas-y !" / "Maintenant !" / "A toi !" / "Allez !"
- Max 130 mots
- PAS de JSON, PAS de "Here is", PAS de "Based on"

CONTEXTE — {advisor_name} :
  CA : {ca_today} / {ca_target} TND ({perf}%)
  Gap : {gap_tnd} TND | {hours_left}h restantes | Urgence : {urgency} | Ton : {tone}
  Meteo : {weather} | Ventes du jour : {nb_ventes}
  [Actions Stratege : ...]

VENTES REELLES (7 derniers jours) :
  1. {nom} — {qty} vendus — {ca} TND
  ...

DERNIERES TRANSACTIONS :
  {heure}h — {nom} x{qty} — {ttc} TND
  ...

CATALOGUE OOREDOO :
  iPhone 16 Pro: 1 299 TND | Samsung A55 5G: 899 TND | ...

[UTILISE CES ARGUMENTS (deja prouves sur le terrain) :
  {rag_txt}
]
```

**Ton selon urgency :**

| Urgency | Ton injecte dans le prompt |
|---|---|
| CRITICAL | CRITIQUE — 2-3 phrases MAX, action unique |
| HIGH | URGENT — court et percutant |
| MEDIUM | DYNAMIQUE — encourageant, 2 actions claires |
| LOW | POSITIF — feliciter, optimiser |

---

### 7.3 Prompt — Mode INVENTORY

> Toutes les questions sur le stock, les ruptures, les produits disponibles.
> `max_tokens=450`, `temperature=0.22`

```
Tu es le CoachAgent IA Ooredoo — expert gestion de stock pour boutiques Tunisie.

REGLE ABSOLUE : Reponds a la VRAIE question du conseiller.
- Si on demande "quels produits sont disponibles" -> liste les PRODUITS DISPONIBLES ci-dessous.
- Si on demande "quels produits sont en rupture"  -> liste les ALERTES.
- Utilise UNIQUEMENT les chiffres reels ci-dessous. Jamais d'invention.
- FRANCAIS avec tutoiement — ton professionnel, max 180 mots
- PAS de JSON ni balises

ETAT STOCK — Boutique {store_id} :
  Total SKUs : {total_skus}
  Ruptures   : {ruptures} | Critiques : {critiques}
  Warnings   : {warnings} | Disponibles (OK) : {ok_count}
  Stock moyen : {avg_stock} unites/SKU

PRODUITS DISPONIBLES (stock > 15 unites) :
  {nom} (SKU {sku}) — {stock_qty} unites
  ...   [jusqu'a 12 produits, tries par stock DESC]

ALERTES STOCK (rupture / critique / warning) :
  [RUPTURE]  {nom} (SKU {sku}) — 0 unites
  [CRITIQUE] {nom} (SKU {sku}) — {n} unites
  [WARNING]  {nom} (SKU {sku}) — {n} unites
  ...   [jusqu'a 15 items, tries par stock ASC]

TOP VENDEURS 7j (avec risque rupture) :
  {nom} — {total_sold} vendus/7j (vel. {vel}/j) | stock: {stock_restant} [⚠ STOCK BAS] | rupture J+{days}
  ...

REGLES :
  Rupture (0)    -> alerte manager + alternative client immediat
  Critique (1-5) -> commande urgente cette semaine
  Warning (6-15) -> planifier reappro
  Best-seller en critique = URGENCE ABSOLUE
```

---

## 8. Construction des messages OpenRouter

```json
[
  { "role": "system",    "content": "<prompt_selon_mode>" },

  // Historique du jour (max 8 echanges = 16 messages)
  { "role": "user",      "content": "<question_echange_1>" },
  { "role": "assistant", "content": "<reponse_echange_1>" },
  ...
  { "role": "user",      "content": "<question_echange_N>" },
  { "role": "assistant", "content": "<reponse_echange_N>" },

  // Question actuelle
  { "role": "user",      "content": "<message_actuel>" }
]
```

---

## 9. Flow complet d'une requete

```
1. POST /api/v1/coach/chat recu
   ↓
2. _normalize_store("store-lac2") → "I63"
   ↓
3. _classify_intent(message)
   ├── off_topic  → reponse guardrail immediate (pas de LLM)
   ├── inventory  → etape 4a
   └── sales      → etape 4b
   ↓
4a. Inventory :
    _load_inventory_context("I63")
    ├── stats (ruptures / critiques / warnings / ok)
    ├── alert_items (20 items <= 15 unites, ASC)
    ├── ok_items   (15 items >  15 unites, DESC)
    └── top_sellers (6 produits, 7j)
    → _build_inventory_prompt()

4b. Sales :
    _load_sales_context("I63", ctx)
    ├── ca_today, gap, performance depuis ctx ou vw_ca_par_boutique
    ├── top_sellers (6 produits, 7j, ORDER BY ca DESC)
    └── recent_transactions (8 dernieres lignes)
    → si mode=coaching  : _search_rag()
    → _build_coaching_prompt() ou _build_conversation_prompt()
   ↓
5. _load_day_history("Conseiller", "I63")
   → charge echanges du jour depuis public.coach_interactions
   ↓
6. _call_llm(system_prompt, message, history=day_history)
   → POST https://openrouter.ai/api/v1/chat/completions
   → modele : openai/gpt-oss-120b:free
   → timeout : 35s
   ↓
7. Fallback si LLM echoue :
   ├── Inventory : liste les critical_items
   ├── RAG dispo : premier script RAG
   └── Sinon     : texte statique avec % performance
   ↓
8. save_interaction() → public.coach_interactions
   trace_coach_chat() → Langfuse (si disponible)
   ↓
9. JSONResponse { reply, mode, domain, question_type, source,
                  model, confidence, rag_used, nb_rag_scripts,
                  latency_ms, sources, context_used,
                  rag_scripts, inventory_alerts }
```

---

## 10. Schema de reponse JSON

```json
{
  "reply":           "texte de la reponse coach",
  "mode":            "conversation | coaching | inventory | off_topic",
  "domain":          "sales | inventory | none",
  "question_type":   "general | script | objection | closing | stock | alerte...",
  "source":          "openrouter | openrouter+rag | openrouter+inventory | fallback",
  "model":           "openai/gpt-oss-120b:free",
  "confidence":      0.88,
  "rag_used":        true,
  "nb_rag_scripts":  2,
  "latency_ms":      1243,
  "sources": [
    { "id": "stock_levels", "label": "Stock levels",              "active": true },
    { "id": "top_sellers",  "label": "Top vendeurs",              "active": true },
    { "id": "history",      "label": "Historique jour (3 echanges)", "active": true }
  ],
  "context_used": {
    "advisor":    "Conseiller",
    "store_id":   "I63",
    "domain":     "inventory",
    "mode":       "inventory",
    "hour":       14,
    "total_skus": 113,
    "ruptures":   3
  },
  "rag_scripts": [
    { "categorie": "closing", "action": "Propose bundle", "score": 0.87 }
  ],
  "inventory_alerts": [
    { "sku": "5020961", "product": "SAMSUNG GALAXY S7 EDGE", "qty": 0, "level": "rupture" }
  ]
}
```

---

## 11. Guardrails

| ID | Regle | Implementation |
|---|---|---|
| G1 | Reponse en francais avec tutoiement | Dans tous les prompts |
| G2 | Prix uniquement depuis le catalogue officiel | Prompt + CATALOG_TXT |
| G3 | Jamais d'invention de chiffres ou d'offres | "Ne fabrique JAMAIS" dans chaque prompt |
| G4 | Hors-sujet bloque sans LLM | `_classify_intent` → off_topic → retour immediat |
| G5 | Max 120-180 mots selon mode | Prompt |
| G6 | Repondre a la vraie question (pas de pitch automatique) | "REGLE ABSOLUE" dans prompts conversation et inventory |
| G7 | Anti-hallucination stock | Section "UTILISE UNIQUEMENT les chiffres reels" |
| G8 | Donnees DB toujours via MAX(date_only) | Queries psycopg2 (pas CURRENT_DATE) |

---

## 12. Persistance des interactions

Table `public.coach_interactions` :

| Colonne | Type | Contenu |
|---|---|---|
| `id` | integer | PK auto |
| `advisor_name` | varchar | Nom du conseiller |
| `store_id` | varchar | I63, M01... |
| `message` | text | Question du conseiller |
| `response` | text | Reponse du coach |
| `gap_pct` | float | Gap objectif au moment de la reponse |
| `urgency` | varchar | LOW / MEDIUM / HIGH / CRITICAL |
| `rag_used` | boolean | RAG active ou non |
| `nb_rag_scripts` | integer | Nombre de scripts RAG utilises |
| `conseil_type` | varchar | sales/script, inventory/alerte... |
| `confidence` | float | Score de confiance (0-1) |
| `created_at` | timestamp | Horodatage |

---

## 13. Fichiers cles

```
backend/multi-agent-sales-inventory/
└── sales-module/
    ├── modules/coaching/agents/coach/
    │   ├── coach_chat.py     ← endpoint FastAPI + toute la logique
    │   └── tools.py          ← save_interaction()
    ├── data/
    │   └── rag_retriever.py  ← embeddings Ollama + recherche Milvus
    └── main.py               ← monte le router /api/v1/coach

frontend/PFE/src/app/
├── features/chat/
│   ├── chat.ts               ← composant Angular + ConversationStorageService
│   ├── chat.html             ← sidebar avec rename inline
│   └── chat.scss
└── core/services/
    └── conversation-storage.service.ts  ← localStorage par date
```
