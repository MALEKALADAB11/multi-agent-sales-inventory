# Image 3 / 4 — Physical Architecture

**Objet** : vue de déploiement. Processus, conteneurs, ports, protocoles.
Répond à « où tourne quoi ? ». C'est la vue qui prouve que le système existe
réellement.
**Format cible** : 1800 × 1200, paysage.
**Source** : [../VUES_ARCHITECTURE.md](../VUES_ARCHITECTURE.md) §3

---

## Prompt A — SVG exact ⭐ (recommandé)

````
Generate ONE self-contained SVG file (viewBox 0 0 1800 1200, no JavaScript, no
external assets, no external fonts). Embed all CSS in a <style> block using a
system geometric sans-serif stack. Output only the SVG code.

═══ DESIGN SYSTEM ═══
Background #0B1220. Primary text #F1F5F9, secondary text #94A3B8.
  Browser / SPA      stroke #14B8A6  fill #042F2E
  Native process     stroke #FB923C  fill #431407  (stroke-width 2px)
  Child process      stroke #60A5FA  fill #172554
  LLM runtime        stroke #C084FC  fill #3B0764
  Database           stroke #38BDF8  fill #082F49  (stroke-width 2px)
  Docker container   stroke #38BDF8  fill #0C4A6E
  Remote cloud       stroke #C084FC  fill #3B0764
Shapes: databases = CYLINDER. Everything else = rounded rect (12px radius).
Enclosures (host, docker network, cloud) = large rounded rect, dashed 1.5px
border at 45% opacity, transparent fill, label in the top-left corner.
Lines: solid 2px with arrowhead. Every edge MUST carry a protocol+port label
in 10px monospace on a #0B1220 pill, e.g. "TCP :5432", "gRPC :19530".
Best-effort links (Langfuse) = dashed.
No 3D, no isometric, no server-rack icons, no gradients, no drop shadows.

═══ TITLE (top-left) ═══
28px #F1F5F9: "Architecture Physique — Vue de déploiement"
14px #94A3B8: "Déploiement mono-hôte · 6 conteneurs Docker · 5 processus natifs"

═══ LAYOUT — three enclosures, top to bottom ═══

ENCLOSURE 1 (top, narrow), label "💻 POSTE CLIENT — Navigateur":
  One teal card: "Angular SPA" sub-label "bundle statique · localStorage".

ENCLOSURE 2 (middle, large), label "🖥️ HÔTE — Windows 10 / serveur unique":
  It contains TWO sub-enclosures side by side.

  SUB-ENCLOSURE 2A, label "Processus natifs":
    Orange card, LARGEST of the group, drawn as the hub:
      "uvicorn — main:app"
      sub: "FastAPI + LangGraph"
      badge: "🔌 :8000"
    Orange card: "ng serve"        sub "dev server Angular"  badge ":4200"
    Blue card:   "mcp_server.py"   sub "inventory-advisor"   badge "📡 stdio — sans port"
    Purple card: "Ollama"          sub "runtime LLM local"   badge ":11434"
    CYLINDER:    "PostgreSQL"      sub "ooredoo_sales"       badge ":5432"

  SUB-ENCLOSURE 2B, label "🐳 Docker — réseau 'milvus'":
    Container card: "milvus-standalone" sub "v2.5.14"      badge ":19530 gRPC · :9091"
    Container card: "milvus-etcd"       sub "v3.5.5"       badge "🔒 :2379 interne"
    Container card: "milvus-minio"      sub "object store" badge "🔒 :9000 · :9001"
    Container card: "retail-redis"      sub "redis:7-alpine" badge ":6379"
    Container card: "langfuse"          sub "v2"           badge ":3001 → 3000"
    CYLINDER:       "langfuse-db"       sub "postgres:15"  badge "🔒 non publié"

ENCLOSURE 3 (bottom, narrow), label "☁️ FOURNISSEURS LLM DISTANTS":
  Three purple cards in a row: "Mistral La Plateforme" · "OpenRouter" · "Groq".

═══ EDGES — every one labelled with protocol and port ═══
Angular SPA → uvicorn      : "HTTP/JSON :8000"
Angular SPA → uvicorn      : "WebSocket ×4 :8000"
Angular SPA → uvicorn      : "SSE :8000"
uvicorn → PostgreSQL       : "asyncpg / psycopg2 · TCP :5432"
uvicorn → milvus-standalone: "gRPC :19530"
uvicorn → retail-redis     : "RESP :6379"
uvicorn ⇢ langfuse (DASHED): "HTTP :3001 — best-effort"
uvicorn → Ollama           : "HTTP :11434"
uvicorn → the three cloud providers : "HTTPS"
uvicorn → mcp_server.py    : "spawn · stdio"
mcp_server.py → PostgreSQL : "TCP :5432"
milvus-standalone → milvus-etcd and milvus-minio : "interne"
langfuse → langfuse-db     : "interne"

Draw "uvicorn — main:app" as the visual hub: most edges originate from it.

═══ ANNOTATION PANEL (right side, bordered, 11px) ═══
Header: "⚠️ Contraintes physiques observées"
  ① Résolution IPv6 Windows — `localhost` résout en ::1 avant 127.0.0.1.
    Ollama et Milvus n'écoutent qu'en IPv4 : 2227 ms via localhost contre
    87 ms via 127.0.0.1. Les URL sont réécrites systématiquement.
  ② Garde-fou de schéma au boot — l'application refuse de démarrer si la
    base n'est pas à la révision Alembic attendue. Aucune table n'est jamais
    créée au runtime.
  ③ Redis sans persistance — --save "" · --appendonly no · 256 Mo allkeys-lru.
    C'est un bus et un cache, jamais une source de vérité.

═══ FOOTER PANEL (bottom-left, bordered, 11px) ═══
Header: "Modes dégradés"
  Milvus KO   → RAG bascule sur corpus fichier
  Redis KO    → perte du bus ; les cycles cron continuent
  Langfuse KO → SDK muselé ; aucun impact agent
  Groq quota  → rotation de clés, puis repli Ollama local
  LLM KO      → nœuds use_llm=False → heuristiques déterministes
  PostgreSQL KO → ARRÊT — seule dépendance sans repli   ← in red #E30613

═══ QUALITY BAR ═══
Every single edge must display its protocol and port. No text may overlap a
shape or a line. All labels horizontal. The uvicorn process must be visually
identifiable as the hub. Readable at 100% zoom and in A4 greyscale.
````

---

## Prompt B — Générateur d'images (slide)

```
A clean software deployment diagram, flat vector style, technical
documentation aesthetic. Dark slate background (#0B1220), subtle dot grid.
Landscape 16:10, high resolution.

Three dashed-border enclosure boxes stacked vertically, each labelled in its
top-left corner.

Top enclosure (narrow): one teal (#14B8A6) card.

Middle enclosure (large): two side-by-side dashed sub-boxes. The left sub-box
holds five nodes — one noticeably larger burnt-orange (#FB923C) card acting as
the hub, one smaller orange card, one blue (#60A5FA) card, one purple
(#C084FC) card, and one sky-blue database cylinder. The right sub-box holds
six sky-blue (#38BDF8) container cards arranged in a grid, one of them drawn
as a cylinder.

Bottom enclosure (narrow): three purple cards in a row.

Thin solid arrows radiate from the large orange hub card to almost every other
node, each carrying a small dark label pill. One arrow is dashed. Two side
panels with thin borders sit at the right and bottom-left, filled with small
monospace text lines.

Style: minimal flat vector, no 3D, no isometric perspective, no server rack
illustrations, no cloud clip art, no gradients inside shapes, thin strokes,
soft outer glow on borders only, crisp geometric sans-serif and monospace
labels in off-white.
```

**Négatif :**
```
3D, isometric, photorealistic, server rack photo, data center photo, cloud
clip art, cartoon, robot, circuit board, neon cyberpunk, handwriting, blurry
text, garbled text, watermark, cluttered, drop shadows, gradients,
skeuomorphism
```

---

## Vérification avant intégration

- [ ] **Chaque flèche porte un protocole et un port** — sans exception
- [ ] `uvicorn :8000` se lit comme le **hub** central
- [ ] Les ports internes (etcd, MinIO, langfuse-db) sont marqués 🔒 non publiés
- [ ] `mcp_server.py` est bien **sans port** (stdio) — c'est un processus enfant
- [ ] La distinction processus natif / conteneur Docker est visuellement nette
- [ ] Les 3 contraintes physiques et les modes dégradés sont présents
- [ ] « PostgreSQL KO → ARRÊT » ressort en rouge
- [ ] Aucun nom d'agent ni de node LangGraph — ce n'est pas la vue logique
