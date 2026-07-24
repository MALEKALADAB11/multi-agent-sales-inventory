# Prompts de génération — Image d'architecture globale

Compagnon de [ARCHITECTURE_GLOBALE_VISUELLE.md](ARCHITECTURE_GLOBALE_VISUELLE.md).
Tous les prompts ci-dessous sont dérivés **du code source**, pas d'une
description approximative : noms de nodes, règles de guardrail, schémas SQL et
versions d'images sont exacts.

---

## ⚠️ Choisir le bon outil avant de copier un prompt

| Objectif | Outil | Variante |
|---|---|---|
| Schéma **exact** pour le rapport / la soutenance | LLM générateur de code → SVG | **V1** ⭐ |
| Vue **détaillée** du graphe superviseur seul | LLM → SVG | **V2** |
| Image **stylée** pour une slide d'ouverture | Générateur d'images | **V3** |
| **Couverture** du mémoire | Générateur d'images | **V4** |
| Planche **agent par agent**, fond blanc, imprimable | LLM → SVG | **V5** ⭐ |
| Version stylée fond blanc de la V5 | Générateur d'images | **V6** |

> Les modèles de diffusion (Midjourney, DALL·E, Flux, Imagen) **ne savent pas
> écrire du texte dense**. Un schéma d'architecture est à 70 % du texte.
> N'utilisez V3/V4 que pour de l'illustratif ; pour un livrable technique,
> passez par V1 ou par le Mermaid du §13 du document d'architecture.

---

## V1 ⭐ — Schéma d'architecture complet en SVG

À coller dans Claude / GPT / tout LLM générateur de code. Rend un fichier
vectoriel exact, éditable et imprimable.

````
Generate ONE self-contained SVG file (viewBox 0 0 2000 1400, no external
assets, no JavaScript, no external fonts). Embed all styling in a <style>
block using a system geometric sans-serif stack. Output only the SVG code.

═══ DESIGN SYSTEM (mandatory) ═══
Background: #0B1220.
Primary text #F1F5F9, secondary text #94A3B8.
Semantic colors — stroke / fill pairs:
  Actors            #64748B / #1E293B
  Frontend          #14B8A6 / #042F2E
  API Gateway       #94A3B8 / #1E293B
  Orchestration     #FB923C / #431407
  Sales agents      #E30613 / #450A0A
  Inventory agents  #FB7185 / #4C0519
  Guardrail         #FACC15 / #422006   (thickest stroke: 2.5px)
  HITL / human      #A78BFA / #2E1065
  Tools & RAG       #60A5FA / #172554
  ML engines        #22D3EE / #083344
  LLM providers     #C084FC / #3B0764
  Data & infra      #38BDF8 / #082F49
  Observability     #9CA3AF / #1F2937
Shape grammar: agents = HEXAGON. Databases = CYLINDER. Decisions = DIAMOND.
Entry/exit = STADIUM. Everything else = rounded rect (12px radius).
Line grammar: main flow = solid 2.5px with arrowhead. Secondary = solid 1.5px.
Agent binding = dotted 1.5px. Feedback loop = dashed curve, #FACC15 at 60%
opacity, with a numbered filled circle badge.
No 3D, no gradients on shapes, no drop shadows.

═══ TITLE BLOCK (top-left) ═══
Title 30px #F1F5F9: "Moteur Agentique Retail — Architecture Globale"
Subtitle 15px #94A3B8: "Coaching de vente temps réel × Optimisation
d'approvisionnement — Ooredoo Tunisie"

═══ EIGHT HORIZONTAL LAYER BANDS, top to bottom ═══
Each band: rounded rect, 1px stroke at 35% opacity, transparent fill,
left-aligned layer code + name in 13px uppercase letter-spaced text.

C0 · ACTEURS — 3 pill nodes: "Vendeur", "Manager magasin",
"Superviseur régional". Small caption right: "RBAC store-level".

C1 · FRONTEND — Angular 21 Signals — 7 cards in one row: Dashboard,
Coach Chat, Inventaire, Kanban Réappro, Demandes, Monitoring, Évaluation.
Right edge: two small badges "SSE /chat/stream" and "WebSocket ×4".

C2 · API GATEWAY — FastAPI v4.0.0 — one wide bar containing 4 chips:
"13 routers REST", "SSE stream", "WebSocket ×4", "JWT · RBAC · slowapi".

C3 · ORCHESTRATION — LangGraph — TALLEST BAND (~2.5× the others).
Draw this directed graph left to right:
  • stadium node "initialize_state"
  • it FANS OUT to 4 nodes stacked vertically: "sales_branch",
    "knowledge_branch", "context_branch", "inventory_branch".
    Draw a curly brace spanning the 4 with the caption
    "même superstep LangGraph — reducers obligatoires".
  • all 4 converge into a wide node "merge_outputs" carrying the small
    italic subtitle "operator.add · _merge_dict"
  • then "coach_agent"
  • then a large amber DIAMOND "guardrail_agent" with subtitle "G1…G7"
  • the diamond has FOUR labeled outgoing edges:
      → "BLOCK (G1/G4)"  to node "safe_fallback"      (amber)
      → "ESCALATE"       to node "human_validation"   (violet)
      → "APPROVE"        straight to "notify_frontend"
      → dashed edge labeled "REWRITE" looping BACK to "coach_agent"
  • safe_fallback and human_validation both rejoin "notify_frontend"
  • then stadium node "save_memory" → "END"

C4 · AGENTS — 7 hexagons in one row, split by a vertical dashed divider.
  Left group, header "SALES", stroke #E30613:
    "Analyste" (sub: 7 nodes · ts_analyst)
    "Stratège" (sub: 6 nodes · self_critique)
    "Coach"    (sub: 6 nodes · invoke_stratege)
  Right group, header "INVENTORY", stroke #FB7185:
    "Analysis" (sub: fetch→compute→reason)
    "Context"  (sub: fetch_signals→interpret)
    "Decision" (sub: constraints_check ◇ decide)
  Under the inventory group, a caption: "analysis ∥ context → decision
  (agents singleton, workers parallèles)".

C5 · OUTILS & CONNAISSANCE — 4 cards, stroke #60A5FA:
  "MCP Server — 7 outils"
  "RAG hybride — dense + BM25 → RRF → rerank → MMR"
  "ReAct tools — 12 outils analyste"
  "Cross-domain — 11 fonctions"

C6 · MOTEURS ML (hors chemin LLM) — 3 cards, stroke #22D3EE:
  "Holt-Winters + WAPE 4,4%"
  "Demand Sensing MSTL→XGBoost · WAPE 9,8%"
  "TimesFM"

C7 · LLM FACTORY — 4 chips, stroke #C084FC:
  "Mistral (primaire)", "OpenRouter (fast/smart)", "Groq (+rotation clés)",
  "Ollama (local)".

C8 · DONNÉES & INFRASTRUCTURE — 4 cylinders:
  "PostgreSQL — 5 schémas · Alembic 0001→0012"   stroke #38BDF8
  "Milvus 2.5.14 + etcd + MinIO"                 stroke #38BDF8
  "Redis 7 — Alert Bus · 4 canaux"               stroke #38BDF8
  "Langfuse v2 — traces & coûts"                 stroke #9CA3AF

═══ CROSS-LAYER EDGES ═══
Solid downward arrows between adjacent bands.
Dotted arrows: sales_branch → Analyste & Stratège; coach_agent → Coach;
inventory_branch → Analysis & Context; Analysis+Context → Decision.
Dotted arrow: LLM Factory → Langfuse, labeled "traces".

═══ THREE FEEDBACK LOOPS (dashed, right margin, numbered badges) ═══
① from Redis cylinder UP to "initialize_state",
   label "AlertBus — cycle événementiel"
② from "human_validation" ACROSS to the "Kanban Réappro" card,
   label "HITL — PO SUGGERE → RECU"
③ from "notify_frontend" DOWN to PostgreSQL,
   label "feedback humain — agent_feedback"

═══ LEGEND (bottom-left, 11px) ═══
"— trait plein : flux synchrone   ··· pointillé : rattachement agent
--- tireté : boucle de rétroaction   ◇ décision   ⬡ agent   ⬢ base de données"

═══ QUALITY BAR ═══
No text may overlap any shape or edge. All labels horizontal. Readable at
100% zoom. Generous whitespace between bands. This must look like a
production engineering diagram from Stripe, Vercel or Temporal docs.
````

---

## V2 — Zoom sur le graphe superviseur (diagramme secondaire)

Utile en slide dédiée : le graphe LangGraph seul, en grand.

````
Generate a self-contained SVG (viewBox 0 0 1600 900, no JS, no external
assets) rendering a LangGraph state machine diagram. Background #0B1220,
text #F1F5F9. Output only SVG code.

Title 26px: "SupervisorAgent — graphe LangGraph sur RetailState"
Subtitle 14px #94A3B8: "app/sales/orchestration/supervisor_agent.py"

Layout: left to right.

1. Stadium node "initialize_state" (stroke #FB923C, fill #431407).
2. FAN-OUT: four rounded rect nodes stacked vertically, same orange style:
   "sales_branch", "knowledge_branch", "context_branch", "inventory_branch".
   Draw a large curly brace to their left spanning all four, with rotated
   caption "fan-out parallèle — même superstep".
3. All four converge into "merge_outputs" (wider node). Under it, a small
   grey annotation box: "reducers RetailState — agents_invoked: operator.add
   · errors: operator.add · metrics: _merge_dict — les nodes retournent des
   deltas, jamais l'état complet".
4. "coach_agent".
5. Large DIAMOND "guardrail_agent", stroke #FACC15 2.5px, fill #422006.
   To its right, a small amber panel listing the seven rules, 11px:
     G1 stock_available      → BLOCK
     G2 stockout_imminent
     G3 rag_source
     G4 business_rules       → BLOCK
     G5 network_eligibility
     G6 confidence           → REWRITE
     G7 budget
   with a footer line: "status = max(sévérité) · BLOCK(3) > ESCALATE(2) >
   REWRITE(1) > APPROVE(0)".
6. Four outgoing edges from the diamond, each labeled:
   "BLOCK"    → "safe_fallback" (amber)
   "ESCALATE" → "human_validation" (violet #A78BFA / #2E1065)
   "APPROVE"  → straight to "notify_frontend"
   "REWRITE"  → DASHED edge curving back to "coach_agent"
7. safe_fallback and human_validation rejoin "notify_frontend".
8. "notify_frontend" → stadium "save_memory" → small circle "END".

Annotate the human_validation node with a small violet caption:
"requires_human_validation = status ∈ {ESCALATE, BLOCK}".

Style: flat vector, 1.5px strokes (2.5px on the diamond), 12px rounded
corners, no 3D, no shadows, no gradients on shapes. Nothing may overlap.
````

---

## V3 — Visuel de slide (générateur d'images)

Pour une slide d'ouverture. Exactitude sacrifiée au profit du style.

```
A clean, professional software architecture diagram poster, flat vector
style, technical documentation aesthetic. Dark slate background (#0B1220)
with a very subtle dot grid. Landscape 16:9, high resolution.

Eight horizontal layer bands stacked top to bottom, each a thin-bordered
rounded rectangle with a small label on its left edge.

Top band: three small human silhouette icons in muted slate grey.
Second band: seven small teal (#14B8A6) rounded cards in a row.
Third band: one wide dark grey bar with four pill-shaped chips inside.

Fourth band — the visual centerpiece, twice as tall as the others: a
directed graph in burnt orange (#FB923C). One entry node fans out into four
parallel nodes stacked vertically, which all converge back into a single
node, then flow into a large amber-yellow (#FACC15) DIAMOND. From the
diamond, three arrows diverge — one to an amber node, one to a violet
(#A78BFA) node, one going straight ahead — and all three reconverge into a
final node.

Fifth band: seven hexagonal nodes in a row. Three glowing Ooredoo red
(#E30613) on the left, three deep rose (#FB7185) on the right, separated by
a thin vertical dashed divider.

Sixth band: four blue (#60A5FA) cards and three cyan (#22D3EE) cards.
Seventh band: four purple (#C084FC) chips in a row.
Bottom band: four sky-blue (#38BDF8) cylinder database icons.

Solid thin glowing arrows flow downward between adjacent bands. Three
curved dashed amber arrows loop from the bottom back up along the right
margin, each carrying a small numbered circle badge.

Style: minimal flat vector, no 3D, no isometric perspective, no gradients
inside shapes, 1.5px thin strokes, soft outer glow on node borders only,
generous whitespace, crisp geometric sans-serif labels in off-white.
Looks like a high-end engineering documentation page. Extremely clean.
```

**Prompt négatif :**
```
3D, isometric, photorealistic, clip art, stock photo, cartoon mascot,
robot face, glowing brain, neural network illustration, circuit board
texture, neon cyberpunk, handwriting, blurry text, garbled text, lorem
ipsum, watermark, cluttered, busy, drop shadows, skeuomorphism, gradients
```

---

## V4 — Couverture de mémoire (conceptuel)

```
An elegant conceptual illustration of a real-time multi-agent AI system for
retail operations. Wide 16:9, deep navy background (#0B1220) with subtle
atmospheric depth.

Center: a luminous orchestration core — a rounded hexagon glowing burnt
orange — emitting four beams of light that fan outward to four smaller
nodes, then reconverge into a single beam that passes through a glowing
amber diamond-shaped gate before continuing upward.

Left half: warm Ooredoo-red hexagonal nodes representing sales intelligence,
linked by fine luminous lines to a minimal line chart and a chat bubble.

Right half: deep rose hexagonal nodes representing inventory intelligence,
linked to a minimal warehouse shelf outline and a kanban board outline.

Bottom: four glowing sky-blue database cylinders in a row, thin vertical
light beams rising from them toward the center.

Top: a soft teal glow representing the user interface, with three faint
human silhouettes.

Three thin dashed golden arcs sweep from the bottom right, around the whole
composition, and back — suggesting continuous feedback loops.

Style: premium flat vector infographic, thin luminous strokes, restrained
palette (#E30613 red, #FB923C orange, #FACC15 amber, #22D3EE cyan,
#38BDF8 sky blue, #14B8A6 teal on deep navy), abundant negative space,
calm and symmetric, editorial tech-magazine quality. Minimal or no text.
No 3D, no photorealism, no robots, no brains, no humanoid AI figures.
```

---

## V5 ⭐ — Planche « agent par agent », fond blanc

Une seule planche paysage montrant les **9 agents** avec leur graphe interne
exact. Conçue pour l'impression A3 / la double page du mémoire. À coller dans
un LLM générateur de code (Claude, GPT), pas dans un générateur d'images.

````
Generate ONE self-contained SVG file (viewBox 0 0 2400 1700, no external
assets, no JavaScript, no external fonts, no <image> tags). Embed all styling
in a single <style> block using a system geometric sans-serif stack
(Inter, "Segoe UI", system-ui, sans-serif). Output only the SVG code, nothing
else.

═══ DESIGN SYSTEM — LIGHT / PRINT (mandatory) ═══
Page background: pure #FFFFFF. No dark mode, no gradients, no drop shadows,
no 3D, no glow.
Text: primary #0F172A, secondary #64748B, monospace-ish code labels #334155.
Section band background #F8FAFC with 1px #E2E8F0 border, 14px radius.

Semantic palette — stroke / fill pairs (all fills are pale tints, all strokes
are dark enough to survive a black-and-white print):
  Sales agents        #E30613 / #FEF2F2
  Inventory agents    #BE123C / #FFF1F2
  Orchestration       #C2410C / #FFF7ED
  Guardrail           #B45309 / #FFFBEB     (stroke 3px — thickest on page)
  HITL / human        #6D28D9 / #F5F3FF
  Deterministic node  #475569 / #F1F5F9     (no LLM involved)
  LLM node            #7E22CE / #FAF5FF
  Tools / RAG         #1D4ED8 / #EFF6FF
  Data & persistence  #0369A1 / #F0F9FF
  Observability       #6B7280 / #F9FAFB

Shape grammar:
  agent container = rounded rect, 16px radius, 2px stroke
  internal graph node = small rounded rect, 8px radius, 1.5px stroke
  conditional router = DIAMOND
  database / table = CYLINDER
  entry & exit = STADIUM
Line grammar:
  main flow          solid 2px + arrowhead
  parallel fan-out   solid 2px, split from a single junction dot
  feedback loop      dashed 2px, curved, colour #B45309
  side effect        dotted 1.5px

LLM BADGE — top-right corner of every agent container, an 18px circle:
  filled #7E22CE with white "L"  → this agent calls an LLM
  outlined #475569, empty        → fully deterministic, no LLM
Each agent container also shows, bottom-right in 11px #64748B, its source
path (e.g. "agents/analyst/agent.py").

═══ TITLE BLOCK (top-left, above everything) ═══
Title 34px #0F172A: "Architecture multi-agents — fonctionnement agent par agent"
Subtitle 16px #64748B: "Moteur agentique retail Ooredoo Tunisie — LangGraph ·
9 agents · 3 couches"

═══ LAYOUT: three stacked section bands ═══

──────── BAND 1 — « DOMAINE SALES · coaching temps réel » ────────
Header on the left edge, 14px uppercase letter-spaced, colour #E30613.
Three agent containers side by side, equal width. Inside each, draw the
internal node chain HORIZONTALLY as small connected nodes.

  [1] "Agent ANALYSTE"  — LLM badge: OUTLINED (deterministic)
      Role line, 13px italic: "Où en est le magasin, et où finira-t-il ?"
      7 nodes in a chain:
        receive_pos → validate_data → load_memory → ts_analyst
        → compare_with_memory → build_strategy_query → save_memory → END
      Render "ts_analyst" LARGER and highlighted (2.5px stroke, fill #F1F5F9)
      with the caption underneath, 11px:
        "moteur séries temporelles déterministe — Holt-Winters saisonnier
         + profil intraday + gap horaire · < 1 s · zéro LLM"
      Output chip row at the bottom of the container, 11px pale blue chips:
        forecast_eod · gap_pct · attainment · urgency_level · hourly_gaps

  [2] "Agent STRATÈGE" — LLM badge: FILLED
      Role line: "Pourquoi cet écart, et quoi faire ?"
      Small tag next to the title, 11px #C2410C: "pattern Reflexion"
      6 nodes in a chain:
        fetch_context → rag_search → analyze_context → generate_strategy
        → build_output → self_critique → END
      Colour "rag_search" with the Tools/RAG palette.
      Draw a short dashed self-loop arrow above "self_critique" back onto
      "generate_strategy", labelled 11px "auto-évaluation → critique_score".
      Output chips: cause_racine · strategie_actions · focus_produits
                    · critique_score

  [3] "Agent COACH" — LLM badge: FILLED
      Role line: "Que dire à CE vendeur, maintenant ?"
      6 nodes in a chain:
        load_context → rag_search → load_advisor_history
        → invoke_stratege_for_coach → generate_conseil → save_conseil → END
      Highlight "invoke_stratege_for_coach" and draw a dotted arrow from it
      UP to the Stratège container above/left, labelled 11px:
        "cache + retry + timeout borné"
      Output chips: conseil_final · confidence · rag_used

  Below the three containers, a thin full-width orchestrator strip
  (Orchestration palette) titled "CycleOrchestrator — orchestration/graph.py":
    stadium "analyste" → DIAMOND "router" → "stratege" → DIAMOND "router"
    → "coach"  and a second edge from that diamond to a stadium "END",
    labelled "si pos_data.coach_message est vide".
  At the right end of the strip, two small dotted side-effect callouts:
    "AlertBus — si urgence ∈ {CRITICAL, HIGH}"
    "enrichissement RAG Milvus — asyncio.to_thread"

──────── BAND 2 — « DOMAINE INVENTORY · approvisionnement » ────────
Header colour #BE123C. This band is NOT three parallel cards — it must show
convergence. Layout: two containers stacked on the LEFT, converging by two
arrows into one container on the RIGHT.

  [4] "Inventory ANALYSIS AGENT" — LLM badge: FILLED, with the small
      annotation 11px "LLM = évaluateur, pas narrateur (tier fast)"
      Role: "Quel est le vrai état du stock de ce SKU ?"
      3 nodes: fetch → compute → reason
      Captions under the nodes, 10px:
        fetch   "DB-first · fallback CSV · préchargé en batch"
        compute "Python pur — EOQ · safety stock · reorder point ·
                 risque 2 couches"
        reason  "détecte les conflits cross-dimensionnels"
      Output chip: analysis_report (baseline)

  [5] "Inventory CONTEXT AGENT" — LLM badge: FILLED
      Role: "De combien la demande va-t-elle bouger, et pourquoi ?"
      2 nodes: fetch_signals → interpret
      Captions:
        fetch_signals "promos · météo · fériés · événements +
                       uplifts RÉELS observés dans sales_history · cache 1 h"
        interpret     "LLM calibré par l'historique · fallback rule-based"
      Output chip, rendered BIGGER than the others: "demand_uplift_pct (7 j)"

  Between the two left containers, a vertical curly brace with the rotated
  caption 12px #64748B: "exécution parallèle — ThreadPoolExecutor(2)".
  Two arrows leave them and merge at a junction dot before entering [6].

  [6] "Inventory DECISION AGENT" — LLM badge: FILLED, annotation
      "tier smart — décision critique"
      Role: "Je commande ? Combien ? Quand ?"
      Above the node chain, a pale pre-processing box:
        "_compute_adjusted_metrics() — uplift appliqué à la demande
         journalière → EOQ / safety stock / reorder point recalculés"
      Graph with a branch:
        DIAMOND "constraints_check"
          ├─ edge labelled "blocage dur (MOQ · budget · capacité)" → stadium END
          └─ edge → node "decide" → stadium END
      Output chips: action ∈ {ORDER · HOLD · MONITOR · EXPEDITE}
                    · order_qty · urgency · escalate_to_human
      Two dotted side-effect arrows leaving the container to the right, in
      order, numbered ① and ②:
        ① to a CYLINDER "inventory.recommendations"
           caption "ORDER / EXPEDITE uniquement"
        ② to a rounded rect "Kanban réappro — PO statut SUGGERE"
           caption "push WebSocket temps réel · porte HITL"
      Add a small grey note under the container, 11px:
        "dégradation gracieuse : context absent → uplift = 0 (baseline)"

  Right edge of the band, a small grey note box:
    "Orchestrateur inventory — analyze_batch parallélise sur tous les SKUs.
     Graphes LangGraph compilés UNE fois par process (_compiled_graphs)."

──────── BAND 3 — « COUCHE CROSS-DOMAINE » ────────
Header colour #B45309. Three zones left to right.

  [7] "SUPERVISOR AGENT" — LLM badge: OUTLINED (orchestration only)
      Draw the master graph left to right, Orchestration palette:
        stadium "initialize_state"
          → fans out to FOUR nodes stacked vertically:
              sales_branch · knowledge_branch · context_branch
              · inventory_branch
            with a curly brace and the caption 11px:
              "même superstep — chaque node retourne SON DELTA, jamais
               l'état complet (sinon InvalidUpdateError)"
          → all four converge into "merge_outputs"
          → "coach_agent"
          → the guardrail diamond in zone [8]

  [8] "GUARDRAIL AGENT" — LLM badge: OUTLINED, with a bold 12px caption
      "100 % déterministe — aucun LLM"
      A large amber DIAMOND labelled "guardrail_agent  G1…G7", stroke 3px.
      To its right a compact 7-row table, 11px, columns Règle / Contrôle /
      Sévérité, header row filled #FFFBEB:
        G1  stock à zéro sur le produit poussé          BLOCK
        G2  rupture imminente (< 3 j) hors écoulement   REWRITE
        G3  argumentaire sans source RAG + conf. < 0,7  REWRITE
        G4  remise / offre non autorisée détectée       BLOCK
        G5  5G / Fibre sans vérification d'éligibilité  REWRITE
        G6  confiance < 0,65                            ESCALATE
        G7  commande > 100 000 DT                       ESCALATE
      Render BLOCK cells in #B45309 bold, ESCALATE in #6D28D9 bold.
      Footer line under the table, 11px:
        "statut = max(sévérité) · BLOCK > ESCALATE > REWRITE > APPROVE
         · toute violation divise final_confidence par 2"
      FOUR labelled edges leave the diamond:
        "APPROVE"  → straight to "notify_frontend"
        "ESCALATE" → "human_validation" (HITL violet) → "notify_frontend"
        "BLOCK"    → "safe_fallback" (amber) → "notify_frontend"
        "REWRITE"  → DASHED curve looping BACK to "coach_agent" in zone [7],
                     labelled "1 itération max"
      Then "notify_frontend" → stadium "save_memory" → stadium "END".

  [9] "COACH AGENT CROSS-DOMAINE" — LLM badge: FILLED, annotation
      "synthèse LLM + fallback rule-based"
      Small header note 11px: "distinct du Coach conversationnel [3]"
      Draw it as a funnel: three labelled input arrows on the left —
        "branche INVENTORY — stock_report · context_report ·
         inventory_decisions"
        "branche SALES — sales_report · strategy_actions · focus_produits"
        "branche KNOWLEDGE — rag_scripts · market_signals"
      converging into the container, with four output chips on the right:
        produit_a_pousser · produit_a_eviter · conseil_personnalise
        · justification_metier
      Caption under the outputs, 11px italic:
        "le SKU où la demande ventes ET la disponibilité stock s'alignent"

═══ FOOTER STRIP (full width, bottom, on #F8FAFC) ═══
Left: legend, 11px —
  "⬤ L  appelle un LLM      ○  déterministe
   ── flux principal   ··· effet de bord   --- boucle de rétroaction
   ◇ routeur conditionnel   ⬢ table PostgreSQL"
Right, 15px #0F172A, italic, given visual weight:
  "Le déterministe porte les chiffres · le LLM porte le jugement ·
   les règles dures portent la sécurité — rien n'atteint l'utilisateur
   sans passer par le Guardrail."

═══ QUALITY BAR ═══
No text may overlap any shape, arrow or another label. All labels horizontal
except the two explicitly rotated brace captions. Generous whitespace between
the three bands and between containers. Every arrow must have a visible
arrowhead and terminate exactly on a shape edge, never inside it. The result
must read as a production engineering diagram from Stripe, Linear or Temporal
documentation — printed on white paper.
````

---

## V6 — Version stylée fond blanc (générateur d'images)

Pour une slide. **Le texte sera illisible ou inventé** — ne l'utiliser que si
les libellés ne comptent pas.

```
A clean professional software architecture poster on a pure white background,
flat vector style, technical documentation aesthetic, landscape 16:9, high
resolution, generous white space.

Three horizontal section bands stacked top to bottom, each a very light grey
(#F8FAFC) rounded rectangle with a thin #E2E8F0 border and a small colour-coded
label on its left edge.

Top band: three equal rounded-rectangle cards outlined in crimson red
(#E30613) with pale pink fill. Inside each card, a horizontal chain of six or
seven small connected rounded nodes joined by thin arrows. In the first card,
one node in the middle of the chain is noticeably larger and outlined in dark
slate grey. Below the three cards, a thin full-width orange (#C2410C) strip
containing three nodes and two small diamonds connected left to right.

Middle band: two rounded cards outlined in deep rose (#BE123C) stacked on the
left, joined by a curly brace, their two arrows merging at a junction dot into
a single wider card on the right. Two dotted arrows leave the right card
toward a small blue cylinder icon and a small kanban-board rectangle.

Bottom band: on the left, one entry node fanning out into four small nodes
stacked vertically that reconverge into a single node. In the centre, a large
amber-yellow (#B45309) DIAMOND with a thick outline — the visual anchor of the
whole poster — with four arrows radiating from it: one straight ahead, one to
a violet (#6D28D9) node, one to an amber node, and one dashed arrow curving
backwards in a wide loop. On the right, three input arrows converging into a
single card that emits four small chips.

Style: minimal flat vector, thin 1.5 to 2 pixel strokes, pale tinted fills,
16 pixel rounded corners, crisp geometric sans-serif labels in near-black,
no 3D, no isometric perspective, no shadows, no gradients, no glow. Looks like
a printed page from high-end engineering documentation.
```

**Prompt négatif :**
```
dark background, black background, 3D, isometric, photorealistic, clip art,
stock photo, cartoon mascot, robot face, glowing brain, neural network
illustration, circuit board texture, neon, cyberpunk, handwriting, blurry
text, garbled text, lorem ipsum, watermark, cluttered, busy, drop shadows,
skeuomorphism, gradients, saturated colors
```

---

## Modèle de vérification avant livraison

Cocher avant d'intégrer l'image au rapport :

- [ ] Les **4 branches parallèles** sont visuellement parallèles (pas en file)
- [ ] Le **guardrail est un goulot unique** — aucune flèche ne le contourne
- [ ] Le chemin **REWRITE** revient bien au `coach_agent`
- [ ] `human_validation` est **dans** le flux, pas en marge (principe P4)
- [ ] Les **moteurs ML sont une couche distincte** des agents (principe P2)
- [ ] Les **3 boucles** ①②③ sont présentes et numérotées
- [ ] Chaque service externe montre son **chemin de repli** (principe P5)
- [ ] Aucun texte ne chevauche une forme ou une flèche
- [ ] Le rouge Ooredoo est **réservé aux agents Sales**, nulle part ailleurs
- [ ] Lisible imprimé en A4 noir et blanc (contraste porté par la forme, pas
      uniquement par la couleur)

### Spécifique à la V5 (planche agent par agent)

- [ ] Les **9 agents** sont présents et numérotés [1] à [9]
- [ ] `ts_analyst` est visuellement **plus gros** que les autres nodes de
      l'Analyste — c'est le point de design de la refonte v4
- [ ] L'Analyste porte un badge LLM **vide**, le Guardrail aussi
- [ ] Analysis et Context sont **empilés et joints par une accolade**, pas
      alignés en file avec Decision
- [ ] Le losange `constraints_check` a bien **deux** sorties dont une vers END
- [ ] Le chemin **REWRITE** boucle du Guardrail vers `coach_agent` du
      Superviseur, pas vers le Coach conversationnel [3]
- [ ] Le Coach cross-domaine [9] est explicitement marqué « distinct du Coach
      conversationnel »
- [ ] Le fond est **blanc pur**, aucun aplat sombre
