# Image 2 / 4 — Logical Architecture

**Objet** : découpage des responsabilités en 5 couches, **sans aucune notion de
déploiement**. Répond à « comment le système est-il structuré ? ».
**Format cible** : 1800 × 1300, portrait-paysage.
**Source** : [../VUES_ARCHITECTURE.md](../VUES_ARCHITECTURE.md) §2

---

## Prompt A — SVG exact ⭐ (recommandé)

````
Generate ONE self-contained SVG file (viewBox 0 0 1800 1300, no JavaScript, no
external assets, no external fonts). Embed all CSS in a <style> block using a
system geometric sans-serif stack. Output only the SVG code.

═══ DESIGN SYSTEM ═══
Background #0B1220. Primary text #F1F5F9, secondary text #94A3B8.
  Presentation   stroke #14B8A6  fill #042F2E
  Application    stroke #94A3B8  fill #1E293B
  Orchestration  stroke #FB923C  fill #431407
  Guardrail      stroke #FACC15  fill #422006   (stroke-width 2.5px)
  HITL / human   stroke #A78BFA  fill #2E1065
  Sales business stroke #E30613  fill #450A0A
  Stock business stroke #FB7185  fill #4C0519
  Shared engines stroke #22D3EE  fill #083344
  Shared tools   stroke #60A5FA  fill #172554
  Persistence    stroke #38BDF8  fill #082F49
  LLM abstraction stroke #C084FC fill #3B0764
  Observability  stroke #9CA3AF  fill #1F2937
Shapes: all modules = rounded rect (12px radius). Layer bands = rounded rect,
1px stroke at 35% opacity, transparent fill.
Lines: layer-to-layer = solid 3px with large arrowhead. The single upward
event arrow = DASHED 2px in amber #FACC15.
No 3D, no gradients inside shapes, no drop shadows.

═══ TITLE (top-left) ═══
28px #F1F5F9: "Architecture Logique — Découpage des responsabilités"
14px #94A3B8: "Cinq couches · dépendance strictement descendante"

═══ LAYOUT — five horizontal bands, stacked top to bottom ═══
Each band carries, on its LEFT EDGE, a circled number and the layer name in
13px uppercase letter-spaced text. On its RIGHT EDGE, in 10px italic #94A3B8,
a "ne fait jamais" note.

BAND ① COUCHE PRÉSENTATION — teal. Three modules in a row:
  "Vues & pages" · "Services de communication" · "Gestion de session & rôles"
  Right note: "ne fait jamais : règle métier, calcul de KPI"

BAND ② COUCHE APPLICATION — grey. Five modules in a row:
  "Routage REST" · "Streaming SSE / WebSocket" · "Authentification & RBAC"
  "Limitation de débit" · "Journalisation & traçage"
  Right note: "ne fait jamais : raisonnement, orchestration d'agent"

BAND ③ COUCHE ORCHESTRATION — orange. Six modules in a row, but TWO of them
use a different palette to stand out:
  "Machine à états partagée" · "Routage conditionnel" · "Parallélisation & fusion"
  "Gate de sécurité"           ← AMBER palette, thickest stroke
  "Point de validation humaine" ← VIOLET palette
  "Résilience — disjoncteurs"
  Right note: "ne fait jamais : accès direct à la base"

BAND ④ COUCHE MÉTIER — the tallest band (~1.8×). It contains THREE labelled
sub-groups side by side, each with its own thin border and header:
  sub-group "Domaine VENTE" (red palette), three stacked modules:
      "Diagnostic de performance" · "Stratégie contextuelle" · "Conseil opérationnel"
  sub-group "Domaine STOCK" (rose palette), three stacked modules:
      "Diagnostic de couverture" · "Enrichissement contextuel" · "Décision de commande"
  sub-group "Services transverses", three stacked modules:
      "Prévision déterministe" (cyan) · "Recherche de connaissance" (blue)
      · "Scoring & classement produit" (blue)
  Right note: "ne fait jamais : connaître le transport HTTP/WS"

BAND ⑤ COUCHE PERSISTANCE & INTÉGRATION — sky blue. Five modules in a row:
  "Référentiel transactionnel" · "Référentiel vectoriel" · "Cache & bus
  d'événements" · "Abstraction fournisseurs LLM" (purple) · "Puits
  d'observabilité" (grey)
  Right note: "ne fait jamais : décision métier"

═══ EDGES ═══
Four THICK solid downward arrows on the LEFT side, one between each pair of
adjacent bands, each labelled on a #0B1220 pill:
  ① → ②  "contrat d'API"
  ② → ③  "invocation de cycle"
  ③ → ④  "délégation de raisonnement"
  ④ → ⑤  "accès aux données"

ONE dashed amber arrow on the RIGHT side, curving UPWARD from band ⑤ back to
band ③, labelled: "⚡ événement — seule remontée autorisée".
Draw a small amber annotation next to it: "inversion de dépendance : le
producteur d'alerte ignore qui la traitera".

═══ SIDE PANEL (bottom-right, bordered box, 11px) ═══
Header: "État partagé — RetailState"
Eight lines:
  identité       cycle_id · store_id · advisor_id · trigger_type
  entrées        pos_data · stock_data · context_data · user_message
  analyse        gap_pct · forecast_eod · urgency_level · feasibility
  stratégie      strategie_actions · focus_produits · cause_racine
  inventaire     inventory_decisions · critical_stock_alerts
  connaissance   rag_context · retrieved_scripts · recommended_offers
  sortie         coach_recommendation · scored_products
  contrôle       guardrail_status · requires_human_validation · hitl_*
Footer line in italic: "reducers sur agents_invoked · errors · metrics —
les nœuds retournent des deltas, jamais l'état complet"

═══ QUALITY BAR ═══
This diagram must contain NO port number, NO container name, NO file path —
it is a purely logical view. No text may overlap a shape or a line. All labels
horizontal. Band ④ must clearly read as the tallest and richest. Readable at
100% zoom and in A4 greyscale.
````

---

## Prompt B — Générateur d'images (slide)

```
A clean layered software architecture diagram, flat vector style, technical
documentation aesthetic. Dark slate background (#0B1220), subtle dot grid.
Landscape, high resolution.

Five horizontal bands stacked top to bottom, each a thin-bordered rounded
rectangle containing a row of small rounded module cards, with a circled
number on its left edge.

Band 1: three teal (#14B8A6) cards.
Band 2: five grey (#94A3B8) cards.
Band 3: six burnt-orange (#FB923C) cards, one of them amber-yellow (#FACC15)
with a noticeably thicker border, another violet (#A78BFA).
Band 4: the tallest band, containing three bordered sub-groups side by side —
the left one holding three crimson red (#E30613) cards, the middle one three
deep rose (#FB7185) cards, the right one three cards in cyan and blue.
Band 5: five sky-blue (#38BDF8) cards, one purple (#C084FC), one grey.

Four thick solid downward arrows on the left connect the bands in sequence.
ONE dashed amber arrow on the right curves upward from the bottom band back
to the third band.

Style: minimal flat vector, no 3D, no isometric perspective, no gradients
inside shapes, thin strokes, soft outer glow on borders only, abundant
negative space, crisp geometric sans-serif labels in off-white.
```

**Négatif :**
```
3D, isometric, photorealistic, clip art, stock photo, cartoon, robot face,
glowing brain, neural network illustration, circuit board, neon cyberpunk,
server rack, cloud icon, handwriting, blurry text, garbled text, watermark,
cluttered, drop shadows, gradients
```

---

## Vérification avant intégration

- [ ] **Aucun port, aucun conteneur, aucun chemin de fichier** — sinon c'est la vue physique
- [ ] Les 4 flèches descendantes sont **plus épaisses** que tout le reste
- [ ] L'unique flèche remontante est **en pointillés ambre** et clairement isolée
- [ ] La bande ④ est visiblement la plus haute
- [ ] Les deux domaines (Vente / Stock) sont **symétriques** — c'est le principe P1
- [ ] Le « Gate de sécurité » se distingue par son trait épais ambre
- [ ] Chaque bande porte sa note « ne fait jamais »
