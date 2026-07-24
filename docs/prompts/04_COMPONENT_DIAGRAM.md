# Image 4 / 4 — Component Diagram

**Objet** : diagramme de composants UML. Les briques et leurs **interfaces
fournies / requises**. Répond à « qui dépend de quoi, et par quel contrat ? ».
**Format cible** : 1900 × 1300, paysage.
**Source** : [../VUES_ARCHITECTURE.md](../VUES_ARCHITECTURE.md) §4

---

## Prompt A — SVG exact ⭐ (recommandé)

````
Generate ONE self-contained SVG file (viewBox 0 0 1900 1300, no JavaScript, no
external assets, no external fonts). Embed all CSS in a <style> block using a
system geometric sans-serif stack. Output only the SVG code.

═══ DESIGN SYSTEM ═══
Background #0B1220. Primary text #F1F5F9, secondary text #94A3B8.
  Client         stroke #14B8A6  fill #042F2E
  Exposure       stroke #94A3B8  fill #1E293B
  Orchestration  stroke #FB923C  fill #431407
  Guardrail      stroke #FACC15  fill #422006   (stroke-width 2.5px)
  HITL           stroke #A78BFA  fill #2E1065
  Sales business stroke #E30613  fill #450A0A
  Stock business stroke #FB7185  fill #4C0519
  ML engine      stroke #22D3EE  fill #083344
  Tools          stroke #60A5FA  fill #172554
  LLM abstraction stroke #C084FC fill #3B0764
  Infrastructure stroke #38BDF8  fill #082F49
  Observability  stroke #9CA3AF  fill #1F2937

═══ UML COMPONENT NOTATION (mandatory) ═══
Each component is a rounded rect (12px) containing, from top to bottom:
  • the «component» stereotype in 9px italic #94A3B8
  • the component name in 13px bold
  • a thin horizontal rule
  • a 10px descriptive sub-line
In the TOP-RIGHT corner of every component, draw the classic UML component
icon: a small rectangle with two smaller tabs protruding from its left side.

INTERFACES use ball-and-socket notation:
  • PROVIDED interface = a small filled circle on a short stalk (lollipop ○—)
    attached to the providing component, with the interface name in 10px
    monospace next to it.
  • REQUIRED interface = a half-circle socket on a short stalk (—C) attached
    to the requiring component.
  • The socket wraps around the ball where the two connect.
Where ball-and-socket would be too crowded, fall back to a plain solid arrow
from requirer to provider, labelled with the interface name in 10px monospace.

Lines: solid 1.5px. Optional/best-effort dependencies = dashed.
No 3D, no gradients inside shapes, no drop shadows.

═══ TITLE (top-left) ═══
28px #F1F5F9: "Diagramme de Composants — Interfaces fournies et requises"
14px #94A3B8: "17 composants · 18 interfaces · notation UML ball-and-socket"

═══ LAYOUT — five vertical packages, left to right ═══
Each package = a dashed-border enclosure with a «stereotype» label on top.

PACKAGE 1 «client»:
  WebApp — "Angular SPA"

PACKAGE 2 «couche exposition»:
  ApiGateway — "13 routers · JWT · RBAC · slowapi · CORS"
  StreamHub  — "4 WebSockets + SSE · broadcast + cache payload"

PACKAGE 3 «couche orchestration»:
  SupervisorGraph       — "fan-out ×4 · merge · routage guardrail"
  SalesCycleGraph       — "analyste → stratège → coach"
  InventoryOrchestrator — "N workers · agents singleton"
  GuardrailEngine       — "G1…G7 · sévérité max"        (AMBER, thickest)
  HitlService           — "file de validation"           (VIOLET)

PACKAGE 4 «couche métier» + «services partagés»:
  SalesAgents    — "Analyste · Stratège · Coach"          (RED)
  InventoryAgents— "Analysis · Context · Decision"        (ROSE)
  ForecastEngine — "Holt-Winters · MSTL+XGBoost · TimesFM" (CYAN)
  RagRetriever   — "dense+BM25 → RRF → rerank → MMR"      (BLUE)
  McpToolServer  — "7 outils inventaire"                  (BLUE)
  ProductScorer  — "score · rank cross-domaine"           (BLUE)
  LlmFactory     — "6 providers · rôles · repli"          (PURPLE)

PACKAGE 5 «couche infrastructure»:
  Repositories   — "inventory · supply · sales"
  AlertBus       — "4 canaux pub/sub"
  CircuitBreaker — "CLOSED / OPEN / HALF_OPEN"
  Tracer         — "Langfuse best-effort"                 (GREY)

═══ CONNECTIONS — requirer → provider, labelled with the interface ═══
WebApp → ApiGateway            : IRestApi
WebApp → StreamHub             : IRealtimeFeed
ApiGateway → SupervisorGraph   : ICycleRunner
ApiGateway → SalesCycleGraph   : ICycleRunner
ApiGateway → InventoryOrchestrator : IInventoryCycle
ApiGateway → HitlService       : IHitlQueue
SupervisorGraph → StreamHub    : IPushPayload
SupervisorGraph → SalesAgents  : IAgentInvoke
SupervisorGraph → InventoryOrchestrator : IInventoryCycle
SupervisorGraph → GuardrailEngine : IPolicyCheck
SupervisorGraph → HitlService  : IHumanReview
SalesCycleGraph → SalesAgents  : IAgentInvoke
InventoryOrchestrator → InventoryAgents : IAgentInvoke
SalesAgents → ForecastEngine   : IForecast
SalesAgents → RagRetriever     : IKnowledge
SalesAgents → ProductScorer    : IProductScore
InventoryAgents → McpToolServer: IStockTools
InventoryAgents → ForecastEngine : IForecast
ProductScorer → McpToolServer  : IStockTools
SalesAgents → LlmFactory       : ICompletion
InventoryAgents → LlmFactory   : ICompletion
GuardrailEngine ⇢ LlmFactory (DASHED) : ICompletion «optionnel»
ForecastEngine → Repositories  : IDataAccess
McpToolServer → Repositories   : IDataAccess
ProductScorer → Repositories   : IDataAccess
HitlService → Repositories     : IDataAccess
InventoryAgents → AlertBus     : IAlertPublish
AlertBus ⇢ SupervisorGraph (DASHED, drawn RIGHT-TO-LEFT, in amber #FACC15) : IAlertSubscribe
AlertBus → StreamHub           : IPushPayload
SalesAgents ⇢ CircuitBreaker (DASHED) : «protégé par»
LlmFactory ⇢ Tracer (DASHED)   : ITelemetry

═══ THREE CALLOUT ANNOTATIONS (small bordered notes with a leader line) ═══
On IAlertSubscribe, in amber:
  "Seule interface inversée — c'est ce qui rend le système événementiel
   et non seulement requête-réponse."
On IPolicyCheck, in amber:
  "Goulot obligatoire — aucun composant n'atteint IPushPayload sans être
   passé par GuardrailEngine. Propriété d'architecture, pas convention."
On IStockTools (the ProductScorer → McpToolServer link), in white:
  "Unique couplage horizontal entre les deux domaines — et il est explicite."

═══ LEGEND (bottom-left, 10px) ═══
"○—  interface fournie      —C  interface requise
 ——  dépendance             ---  dépendance optionnelle / best-effort"

═══ QUALITY BAR ═══
Every connection must be labelled with its interface name. No text may overlap
a shape or a line. All labels horizontal. Route the connectors with orthogonal
segments and rounded corners; avoid crossings wherever possible, and where a
crossing is unavoidable draw a small jump arc. Readable at 100% zoom and in A4
greyscale.
````

---

## Prompt B — Générateur d'images (slide)

```
A clean UML component diagram, flat vector style, technical documentation
aesthetic. Dark slate background (#0B1220), subtle dot grid. Landscape 16:10,
high resolution.

Five dashed-border vertical packages arranged left to right, each labelled at
the top. Package 1 holds one teal card. Package 2 holds two grey cards.
Package 3 holds five cards — three burnt-orange, one amber-yellow with a
thicker border, one violet. Package 4 holds seven cards — two crimson red, one
deep rose, one cyan, three blue, one purple. Package 5 holds four sky-blue
cards, one of them grey.

Every card carries a small UML component icon in its top-right corner: a tiny
rectangle with two small tabs protruding from its left edge.

Between the cards, thin connectors use ball-and-socket notation: small filled
circles on short stalks meeting matching half-circle sockets. Each connector
carries a tiny monospace label. A few connectors are dashed. One dashed amber
connector runs right-to-left against the general flow.

Three small bordered callout notes with thin leader lines annotate specific
connectors.

Style: minimal flat vector, no 3D, no isometric perspective, no gradients
inside shapes, thin 1.5px strokes, soft outer glow on borders only, orthogonal
connector routing with rounded corners, crisp geometric sans-serif labels in
off-white. Looks like a page from formal software architecture documentation.
```

**Négatif :**
```
3D, isometric, photorealistic, clip art, stock photo, cartoon, robot, glowing
brain, circuit board, neon cyberpunk, mind map, org chart, flowchart with
diamonds, handwriting, blurry text, garbled text, watermark, cluttered,
tangled wires, drop shadows, gradients
```

---

## Vérification avant intégration

- [ ] **Chaque connecteur porte le nom de son interface** — sinon ce n'est pas un diagramme de composants
- [ ] La notation **ball-and-socket** (○— / —C) est visible, au moins sur les liens principaux
- [ ] L'icône UML composant est présente sur **chaque** brique
- [ ] `IAlertSubscribe` va bien **à contre-courant** (droite → gauche) et est en ambre
- [ ] Aucun chemin n'atteint `IPushPayload` en contournant `GuardrailEngine`
- [ ] Les dépendances optionnelles (`ICompletion` du guardrail, `ITelemetry`, CircuitBreaker) sont en **pointillés**
- [ ] Les 3 annotations d'architecture sont présentes
- [ ] Les connecteurs sont routés orthogonalement, sans enchevêtrement
