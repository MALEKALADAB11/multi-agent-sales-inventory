# Image 1 / 4 — Global Architecture

**Objet** : vue de contexte. Le système comme une seule boîte, ses acteurs et ses
systèmes externes. C'est l'image d'ouverture du rapport.
**Format cible** : 1600 × 1000, paysage.
**Source** : [../VUES_ARCHITECTURE.md](../VUES_ARCHITECTURE.md) §1

---

## Prompt A — SVG exact ⭐ (recommandé)

À coller dans un LLM générateur de code. Rend un fichier vectoriel éditable.

````
Generate ONE self-contained SVG file (viewBox 0 0 1600 1000, no JavaScript, no
external assets, no external fonts). Embed all CSS in a <style> block using a
system geometric sans-serif stack. Output only the SVG code.

═══ DESIGN SYSTEM ═══
Background #0B1220. Primary text #F1F5F9, secondary text #94A3B8.
  Actors           stroke #64748B  fill #1E293B
  Core system      stroke #E30613  fill #450A0A   (stroke-width 3px)
  External systems stroke #38BDF8  fill #082F49
Shapes: actors = STADIUM (pill). Databases = CYLINDER. System = large rounded
rect (16px radius). Everything else = rounded rect (12px radius).
Lines: solid 2px with arrowhead for synchronous exchange; dashed 1.5px for
asynchronous/best-effort. Arrow labels in 11px #94A3B8 on a #0B1220 pill so
they never sit on top of a line.
No 3D, no gradients inside shapes, no drop shadows, no clip art.

═══ TITLE (top-left) ═══
28px #F1F5F9: "Architecture Globale — Vue de contexte"
14px #94A3B8: "Moteur Agentique Retail — Ooredoo Tunisie"

═══ LAYOUT — three zones, left to right ═══

LEFT ZONE, header "ACTEURS" — three stadium nodes stacked vertically:
  "Vendeur"                caption: "son magasin"
  "Manager magasin"        caption: "son magasin"
  "Superviseur régional"   caption: "multi-magasins"
Below them a small grey note: "cloisonnement RBAC au niveau store_id".

CENTER ZONE — ONE large rounded rectangle, the visual anchor, red stroke 3px,
title inside 20px bold: "MOTEUR AGENTIQUE RETAIL".
Inside it, three stacked capability blocks separated by thin horizontal rules:
  • "Coaching de vente temps réel"        sub: "analyse · stratégie · conseil"
  • "Optimisation d'approvisionnement"    sub: "diagnostic · contexte · décision PO"
  • "Garde-fou & validation humaine"      sub: "7 règles · escalade HITL"

RIGHT ZONE, header "SYSTÈMES EXTERNES" — six nodes stacked vertically:
  CYLINDER "PostgreSQL"      italic sub "ooredoo_sales"
  CYLINDER "Milvus"          italic sub "base vectorielle"
  CYLINDER "Redis"           italic sub "bus d'alertes"
  RECT     "Fournisseurs LLM" italic sub "Mistral · Groq · Ollama"
  RECT     "Langfuse"        italic sub "observabilité"
  RECT     "Sources marché"  italic sub "météo · événements"

═══ EDGES ═══
Actors → System (solid, arrow pointing right):
  Vendeur    : "pose une question · consulte ses objectifs"
  Manager    : "approuve un réappro · pilote son magasin"
  Superviseur: "supervise les agents · arbitre les escalades"
System → Actors (solid, arrow pointing left, drawn slightly below each):
  → Vendeur    : "conseil de vente contextualisé"
  → Manager    : "suggestion de commande + alerte"
  → Superviseur: "KPIs, traces, file HITL"
System ↔ External (bidirectional solid):
  ↔ PostgreSQL : "ventes · stocks · PO"
  ↔ Milvus     : "recherche hybride de scripts"
  ↔ Redis      : "pub/sub alertes critiques"
System → External (solid one-way):
  → Fournisseurs LLM : "raisonnement & rédaction"
System ⇢ External (DASHED):
  ⇢ Langfuse : "traces & coûts (best-effort)"
Sources marché ⇢ System (DASHED, arrow pointing left): "signaux contextuels"

═══ FOOTER CAPTION (bottom, 11px #94A3B8) ═══
"Monolithe FastAPI + LangGraph · PostgreSQL est la seule dépendance sans
mode dégradé"

═══ QUALITY BAR ═══
No text may overlap a shape or a line. All labels horizontal. Readable at 100%
zoom and when printed in A4 greyscale. The central system box must be visually
dominant. Generous whitespace between the three zones.
````

---

## Prompt B — Générateur d'images (slide)

```
A clean professional software context diagram, flat vector style, technical
documentation aesthetic. Dark slate background (#0B1220), subtle dot grid.
Landscape 16:10, high resolution.

Three vertical zones. LEFT: three small pill-shaped nodes in muted slate grey
(#64748B), stacked. CENTER: one large dominant rounded rectangle with a
glowing crimson red border (#E30613), containing three stacked text blocks
separated by thin horizontal rules — this is the visual anchor of the whole
image. RIGHT: six small nodes stacked vertically in sky blue (#38BDF8), three
of them drawn as database cylinders.

Thin solid arrows flow from the left pills into the central box and back out.
Thin solid arrows connect the central box to the right-hand nodes. Two of the
right-hand connections are dashed instead of solid.

Style: minimal flat vector, no 3D, no isometric perspective, no gradients
inside shapes, 1.5-2px thin strokes, soft outer glow on borders only, abundant
negative space, crisp geometric sans-serif labels in off-white. Looks like a
high-end engineering documentation page from Stripe or Vercel.
```

**Négatif :**
```
3D, isometric, photorealistic, clip art, stock photo, cartoon, robot face,
glowing brain, neural network illustration, circuit board, neon cyberpunk,
handwriting, blurry text, garbled text, watermark, cluttered, drop shadows,
gradients, skeuomorphism
```

---

## Vérification avant intégration

- [ ] La boîte centrale **domine visuellement** — c'est une vue de contexte, pas un schéma interne
- [ ] Aucun détail d'implémentation n'a fuité (pas de nom de node, pas de port)
- [ ] Les 3 acteurs ont bien un flux **aller ET retour**
- [ ] Langfuse et Sources marché sont en **pointillés** (asynchrone / best-effort)
- [ ] Le rouge Ooredoo n'est utilisé **que** pour la boîte système
- [ ] Lisible imprimé en A4 noir et blanc
