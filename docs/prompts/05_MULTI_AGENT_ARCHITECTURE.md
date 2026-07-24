# Image 5 / 5 — Multi-Agent Architecture (I/O & orchestration)

**Objet** : les 7 agents, ce que chacun **consomme** et **produit**, et la façon
dont ils se passent le relais. C'est la vue la plus dense — elle mérite une page
entière du rapport.
**Format cible** : 2000 × 1400, paysage.
**Source** : [../ARCHITECTURE_MULTI_AGENTS.md](../ARCHITECTURE_MULTI_AGENTS.md)

---

## Prompt A — SVG exact ⭐ (recommandé)

````
Generate ONE self-contained SVG file (viewBox 0 0 2000 1400, no JavaScript, no
external assets, no external fonts). Embed all CSS in a <style> block using a
system geometric sans-serif stack; use a monospace stack for field names.
Output only the SVG code.

═══ DESIGN SYSTEM ═══
Background #0B1220. Primary text #F1F5F9, secondary #94A3B8.
Field names in 10px monospace #94A3B8.
  Input source     stroke #64748B  fill #1E293B
  Sales agent      stroke #E30613  fill #450A0A   (stroke-width 2px)
  Stock agent      stroke #FB7185  fill #4C0519   (stroke-width 2px)
  Guardrail        stroke #FACC15  fill #422006   (stroke-width 2.5px)
  HITL / human     stroke #A78BFA  fill #2E1065
  Output           stroke #14B8A6  fill #042F2E
  Tools            stroke #60A5FA  fill #172554
  ML engine        stroke #22D3EE  fill #083344
Shapes: agents = HEXAGON, large enough to hold three text zones.
Inputs and outputs = rounded rect (12px). Guardrail = DIAMOND.
Lines: handoff between agents = solid 2px with arrowhead, carrying the exact
field names being passed. Cross-domain link = DASHED. Feedback = dashed amber.
No 3D, no gradients inside shapes, no drop shadows.

═══ AGENT CARD ANATOMY (apply to all 7) ═══
Each agent hexagon contains three stacked zones separated by thin rules:
  TOP    — agent name, 14px bold
  MIDDLE — "◀ IN"  followed by its input field names, 9px monospace
  BOTTOM — "OUT ▶" followed by its output field names, 9px monospace
Below each hexagon, a small blue chip listing its tools or engine.

═══ TITLE (top-left) ═══
28px #F1F5F9: "Architecture Multi-Agents — Contrats d'E/S et orchestration"
14px #94A3B8: "7 agents · 4 modes d'orchestration · communication par état partagé"

═══ LAYOUT ═══
Two horizontal lanes, plus a shared control zone on the right.

╔═ LANE 1 (upper), header "DOMAINE VENTE — chaîne séquentielle" ═╗

  INPUT card: "pos_data"  fields: "ca_actuel · nb_transactions_today · avg_ticket"
    → arrow →
  HEXAGON "ANALYSTE"  (red)
     IN : store_id · pos_data · current_hour · analyst_memory
     OUT: gap_objectif · gap_amount · urgency_level · urgency_score
          forecast_eod · forecast_mape · coverage · attainment
          ts_analysis · trend_signal · hourly_gaps · feasibility
          analyst_summary · route_to="strategie"
     chip below (cyan): "ts_engine — Holt-Winters + WAPE (déterministe)"
     chip below (blue): "12 outils ReAct"
    → arrow labelled "gap_objectif · urgency_level · forecast_eod · ts_analysis" →
  HEXAGON "STRATÈGE"  (red)
     IN : gap_objectif · urgency_level · analyst_summary · signaux externes
     OUT: strategie · strategie_actions · focus_produits · cause_racine
          message_manager · context_heatmap · context_signals
          rag_used · nb_rag_scripts · critique_score · strategie_source
     chip below (blue): "RAG hybride — dense+BM25 → RRF → rerank → MMR"
     SMALL SELF-LOOP arrow on this hexagon labelled:
          "self_critique — révision si score < 0.80, max 2 cycles"
    → arrow labelled "strategie · strategie_actions · focus_produits · cause_racine" →
  HEXAGON "COACH"  (red)
     IN : user_message · strategie · focus_produits · inventory_snapshot
          historique conseiller
     OUT: conseil_final · coach_recommendation · scored_products
          rag_context · rag_query · nb_rag_scripts
     chip below (blue): "11 outils cross-domaine — score_product → rank_products"

  A second INPUT card feeds STRATÈGE from above:
     "signaux externes"  fields: "météo · événements datés · promos · offres actives"

╔═ LANE 2 (lower), header "DOMAINE STOCK — pipeline par SKU, workers parallèles" ═╗

  INPUT card: "sku · store_id"
    → it forks into TWO parallel arrows, drawn clearly side by side →

  HEXAGON "ANALYSIS"  (rose)
     IN : sku · store_id
     OUT: computed_metrics · forecast_source · risk_assessment
          seasonal_uplift · lead_time · lifecycle_stage
          risk_rationale · risk_override
     chip below (amber-ish note): "forecast_source ∈ {demand_sensing_db,
          live_ts_engine, fallback_flat} — champ d'audit"

  HEXAGON "CONTEXT"  (rose)
     IN : sku · store_id · category · signaux externes
     OUT: demand_uplift_pct · dominant_signal · confidence
          interpretation · reasoning_source
     chip below: "6 collecteurs parallèles — historique · promos · météo ·
          jours fériés · événements · offres marché"

  Draw a curly brace spanning ANALYSIS and CONTEXT with the caption:
     "strictement indépendants — ni l'un ni l'autre ne lit la sortie de l'autre"

  Both converge into:
  HEXAGON "DECISION"  (rose)
     IN : computed_metrics · risk · demand_uplift_pct · dominant_signal
          moq · moq_is_binding · lead_time · objective_conflict
     OUT: action ∈ {ORDER, EXPEDITE, MONITOR} · order_qty
          urgency ∈ {immediate, this_week, this_month} · confidence
          trade_offs · escalate_to_human · decision_rationale · reasoning_source
     SMALL DIAMOND attached before it labelled "constraints_check" with a
     bypass arrow labelled: "court-circuit — le LLM n'est pas appelé"

╔═ RIGHT ZONE, header "CONTRÔLE PARTAGÉ" ═╗

  Large amber DIAMOND "GUARDRAIL"
     IN : coach_recommendation · scored_products · stock_data · rag_used
          critique_score → confidence
     OUT: guardrail_status · guardrail_issues · guardrail_feedback
          requires_human_validation · guardrail_safe_fallback
  Attached amber panel listing the seven rules, 10px:
     G1 stock_available    → BLOCK
     G2 stockout_imminent
     G3 rag_source
     G4 business_rules     → BLOCK
     G5 network_eligibility
     G6 confidence         → REWRITE
     G7 budget
  Footer of the panel: "status = max(sévérité) — BLOCK(3) > ESCALATE(2) >
     REWRITE(1) > APPROVE(0)"

  FOUR outgoing edges from the diamond:
     "APPROVE"  → teal OUTPUT card "conseil_final · scored_products"
     "REWRITE"  → DASHED AMBER arrow curving BACK to the COACH hexagon,
                  labelled "guardrail_feedback — une passe supplémentaire"
     "ESCALATE" → violet OUTPUT card "file HITL"
     "BLOCK"    → amber OUTPUT card "safe_fallback — l'original n'est
                  JAMAIS envoyé"

  From DECISION: teal OUTPUT card "PO statut SUGGERE — Kanban"
  From DECISION: DASHED arrow to the violet "file HITL" card,
     labelled "escalate_to_human"

═══ TWO CROSS-DOMAIN BRIDGES (dashed, drawn from LANE 2 up to LANE 1) ═══
  ANALYSIS ⇢ COACH     : "inventory_snapshot · stock_data"
  ANALYSIS ⇢ GUARDRAIL : "stock_data — alimente les règles G1 / G2"
Annotate them with a single callout:
  "Seuls couplages horizontaux entre les deux domaines — tous deux vont du
   stock vers la vente."

═══ BOTTOM STRIP — "4 MODES D'ORCHESTRATION" (four small bordered cards) ═══
  A · Chaîne séquentielle   — "le successeur est désigné par route_to"
  B · Fan-out parallèle     — "4 branches, même superstep, reducers obligatoires"
  C · Pipeline par SKU      — "N workers, agents singleton, analysis ∥ context"
  D · Événementiel          — "AlertBus Redis — seule inversion de dépendance"

═══ QUALITY BAR ═══
Every handoff arrow MUST display the exact field names it carries — that is the
entire point of this diagram. No text may overlap a shape or a line. All labels
horizontal. Route connectors orthogonally with rounded corners. The two lanes
must read as clearly separated, and the two dashed bridges between them must be
immediately visible. Readable at 100% zoom and in A4 greyscale.
````

---

## Prompt B — Générateur d'images (slide)

```
A clean multi-agent system architecture diagram, flat vector style, technical
documentation aesthetic. Dark slate background (#0B1220), subtle dot grid.
Wide landscape, high resolution.

Two horizontal lanes separated by a thin divider, plus a control zone on the
right.

Upper lane: three large crimson red (#E30613) hexagons in a row, connected
left to right by thick solid arrows. Each hexagon is divided into three
horizontal text zones by thin rules. Small blue and cyan chips sit beneath
each hexagon. One hexagon has a small circular self-loop arrow.

Lower lane: one input card forking into two deep rose (#FB7185) hexagons drawn
side by side, spanned by a curly brace, both converging into a third rose
hexagon preceded by a small diamond.

Right zone: one large amber-yellow (#FACC15) diamond with a thick border,
accompanied by a tall bordered panel of small text lines. Four arrows leave
the diamond toward teal, violet and amber cards. One of them is a dashed amber
arrow curving back into the upper lane.

Two dashed arrows rise from the lower lane to the upper lane.

A bottom strip holds four small bordered cards in a row.

Style: minimal flat vector, no 3D, no isometric perspective, no gradients
inside shapes, thin strokes, soft outer glow on borders only, orthogonal
connector routing with rounded corners, crisp geometric sans-serif and
monospace labels in off-white. Looks like formal software architecture
documentation.
```

**Négatif :**
```
3D, isometric, photorealistic, clip art, stock photo, cartoon, robot face,
android, glowing brain, neural network illustration, circuit board, neon
cyberpunk, mind map, org chart, handwriting, blurry text, garbled text,
watermark, cluttered, tangled wires, drop shadows, gradients, skeuomorphism
```

---

## Variante — Diagrammes de séquence séparés

Si l'image ci-dessus est trop dense pour votre mise en page, découpez-la en
quatre diagrammes de séquence, un par mode d'orchestration. Les Mermaid prêts à
rendre sont dans [../ARCHITECTURE_MULTI_AGENTS.md](../ARCHITECTURE_MULTI_AGENTS.md) §4 :

| Mode | Diagramme |
|---|---|
| A | Chaîne séquentielle Analyste → Stratège → Coach |
| B | Fan-out parallèle du superviseur + routage guardrail à 4 sorties |
| C | Pipeline par SKU avec `analysis ∥ context → decision` |
| D | Boucle événementielle AlertBus |

---

## Vérification avant intégration

- [ ] **Chaque flèche de relais porte les noms de champs exacts** — c'est l'objet même du diagramme
- [ ] Chaque agent affiche bien ses trois zones : nom / ◀ IN / OUT ▶
- [ ] `ANALYSIS` et `CONTEXT` sont dessinés **côte à côte**, jamais en file — ils sont parallèles
- [ ] L'accolade « strictement indépendants » est présente
- [ ] Le `constraints_check` montre son **court-circuit** contournant le LLM
- [ ] Le chemin **REWRITE** revient bien au Coach, en pointillés ambre
- [ ] Les **deux ponts inter-domaines** vont du stock vers la vente, en pointillés
- [ ] `forecast_source` et `reasoning_source` apparaissent — ce sont les champs d'audit
- [ ] Aucune flèche n'atteint une sortie teal sans passer par le Guardrail
- [ ] Le bandeau des 4 modes d'orchestration est présent
