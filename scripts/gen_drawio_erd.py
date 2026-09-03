"""Genere un diagramme de classes (ERD) draw.io a partir de la base PostgreSQL vivante.

Source de verite : information_schema (colonnes) + pg_constraint (PK/FK/UNIQUE).
Aucune documentation externe n'est lue : tout vient de la base et, pour les
relations non contraintes, de la liste LOGICAL_FKS ci-dessous relevee dans le code.

Usage :
    python scripts/gen_drawio_erd.py [--out docs/architecture/db_class_diagram.drawio]
"""
from __future__ import annotations

import argparse
import os
from collections import defaultdict
from xml.sax.saxutils import escape

import psycopg2

DSN = os.getenv("DATABASE_URL", "postgresql://postgres:admin@localhost:5432/ooredoo_sales")

# --------------------------------------------------------------------------- #
# Relations reelles dans le code mais SANS contrainte FK en base.
# (child_schema, child_table, child_col, parent_schema, parent_table, parent_col)
# --------------------------------------------------------------------------- #
LOGICAL_FKS = [
    ("public", "agent_kpi_daily", "agent_id", "sales", "agents", "agent_id"),
    ("public", "agent_kpi_daily", "store_id", "sales", "boutiques", "store_id"),
    ("public", "store_kpi_daily", "store_id", "sales", "boutiques", "store_id"),
    ("public", "weekly_kpi_summary", "agent_id", "sales", "agents", "agent_id"),
    ("public", "weekly_kpi_summary", "store_id", "sales", "boutiques", "store_id"),
    ("public", "telco_targets_monthly", "agent_id", "sales", "agents", "agent_id"),
    ("public", "telco_targets_monthly", "store_id", "sales", "boutiques", "store_id"),
    ("public", "app_users", "store_id", "sales", "boutiques", "store_id"),
    ("public", "app_sessions", "user_id", "public", "app_users", "user_id"),
    ("public", "coach_interactions", "store_id", "sales", "boutiques", "store_id"),
    ("public", "hitl_reviews", "store_id", "sales", "boutiques", "store_id"),
    ("public", "hitl_reviews", "cycle_id", "public", "agent_cycles", "cycle_id"),
    ("public", "agent_logs", "cycle_id", "public", "agent_cycles", "cycle_id"),
    ("public", "agent_errors", "cycle_id", "public", "agent_cycles", "cycle_id"),
    ("public", "agent_memory", "cycle_id", "public", "agent_cycles", "cycle_id"),
    ("public", "rag_feedback", "cycle_id", "public", "agent_cycles", "cycle_id"),
    ("public", "rag_feedback_metrics", "cycle_id", "public", "agent_cycles", "cycle_id"),
    ("public", "rag_queries", "cycle_id", "public", "agent_cycles", "cycle_id"),
    ("public", "agent_feedback", "sku", "sales", "produits", "sku"),
    ("public", "agent_feedback", "store_id", "sales", "boutiques", "store_id"),
    ("public", "recommendation_scores", "ref_id", "inventory", "recommendations", "id"),
    ("public", "product_requests", "store_id", "sales", "boutiques", "store_id"),
    ("public", "product_requests", "po_id", "supply", "purchase_orders", "po_id"),
    ("coaching", "coaching_events", "cycle_id", "public", "agent_cycles", "cycle_id"),
    ("sales", "objectifs", "agent_id", "sales", "agents", "agent_id"),
    ("sales", "coaching_scripts", "store_id", "sales", "boutiques", "store_id"),
    ("inventory", "agent_runs", "store_id", "sales", "boutiques", "store_id"),
    ("inventory", "agent_runs", "sku", "sales", "produits", "sku"),
    ("inventory", "forecast_accuracy", "sku", "sales", "produits", "sku"),
    ("inventory", "forecast_accuracy", "store_id", "sales", "boutiques", "store_id"),
    ("inventory", "critical_trend_history", "store_id", "sales", "boutiques", "store_id"),
    ("inventory", "product_associations", "sku1", "sales", "produits", "sku"),
    ("inventory", "product_associations", "sku2", "sales", "produits", "sku"),
    ("inventory", "product_associations", "store_id", "sales", "boutiques", "store_id"),
    ("supply", "serial_numbers", "sku", "sales", "produits", "sku"),
    ("supply", "serial_numbers", "store_id", "sales", "boutiques", "store_id"),
    ("supply", "serial_numbers", "sale_id", "sales", "transactions_rt", "sale_id"),
    ("market", "mnp_flows", "operateur_origine", "market", "competitors", "concurrent_id"),
    ("market", "mnp_flows", "operateur_destination", "market", "competitors", "concurrent_id"),
]

SCHEMA_STYLE = {
    "sales":     ("#dae8fc", "#6c8ebf"),
    "inventory": ("#d5e8d4", "#82b366"),
    "supply":    ("#ffe6cc", "#d79b00"),
    "market":    ("#e1d5e7", "#9673a6"),
    "customer":  ("#fff2cc", "#d6b656"),
    "coaching":  ("#f8cecc", "#b85450"),
    "public":    ("#f5f5f5", "#666666"),
}

SKIP_TABLES = {("public", "alembic_version")}

# Tables techniques / fonctionnelles : traces d'execution des agents, logs,
# telemetrie RAG, evaluation LLM, authentification. Exclues par defaut du
# diagramme metier (--all pour les reintegrer).
TECHNICAL_TABLES = {
    ("public", "agent_cycles"),
    ("public", "agent_logs"),
    ("public", "agent_errors"),
    ("public", "agent_memory"),
    ("public", "agent_sessions"),
    ("public", "agent_feedback"),
    ("public", "coach_interactions"),
    ("public", "hitl_reviews"),
    ("public", "rag_queries"),
    ("public", "rag_feedback"),
    ("public", "rag_feedback_metrics"),
    ("public", "recommendation_scores"),
    ("public", "app_users"),
    ("public", "app_sessions"),
    ("inventory", "agent_runs"),
    ("inventory", "business_objectives"),
    ("inventory", "critical_trend_history"),
}

CORE = [("sales", "boutiques"), ("sales", "produits"), ("sales", "agents")]

PAGES = [
    ("1 - Vue globale metier", None, True),
    ("2 - Ventes & Coaching", [
        ("sales", "boutiques"), ("sales", "agents"), ("sales", "produits"),
        ("sales", "transactions"), ("sales", "transactions_rt"),
        ("sales", "coaching_scripts"), ("coaching", "coaching_events"),
    ], False),
    ("3 - Inventaire & Prevision", [
        ("sales", "produits"), ("sales", "boutiques"),
        ("inventory", "product_master"), ("inventory", "stock_levels"),
        ("inventory", "stock_history"), ("inventory", "sales_history"),
        ("inventory", "demand_forecast"), ("inventory", "forecast_accuracy"),
        ("inventory", "context_adjustments"), ("inventory", "events"),
        ("inventory", "promotions"), ("inventory", "product_associations"),
        ("inventory", "alerts"), ("inventory", "recommendations"),
    ], False),
    ("4 - Supply Chain", [
        ("sales", "produits"), ("sales", "boutiques"),
        ("supply", "suppliers"), ("supply", "supplier_products"),
        ("supply", "purchase_orders"), ("supply", "reorder_params"),
        ("supply", "stock_movements"), ("supply", "serial_numbers"),
        ("supply", "transfers"), ("inventory", "recommendations"),
        ("public", "product_requests"),
    ], False),
    ("5 - Marche & Client", [
        ("market", "competitors"), ("market", "competitor_pricing"),
        ("market", "events"), ("market", "mnp_flows"),
        ("market", "seasonal_patterns"),
        ("customer", "segments"), ("customer", "nps_csat"),
        ("sales", "agents"), ("sales", "boutiques"),
    ], False),
    ("6 - Objectifs & Performance", [
        ("sales", "boutiques"), ("sales", "agents"), ("sales", "objectifs"),
        ("public", "telco_targets_monthly"), ("public", "agent_kpi_daily"),
        ("public", "store_kpi_daily"), ("public", "weekly_kpi_summary"),
        ("customer", "nps_csat"),
    ], False),
]

HDR = 26
ROW = 20
WIDTH = 270
GAP_Y = 40
GAP_X = 60
MAX_COL_H = 1500


def short_type(dt, clen, prec, scale):
    m = {
        "character varying": f"varchar({clen})" if clen else "varchar",
        "character": f"char({clen})" if clen else "char",
        "timestamp without time zone": "timestamp",
        "timestamp with time zone": "timestamptz",
        "double precision": "float8",
        "integer": "int",
        "boolean": "bool",
        "smallint": "int2",
        "bigint": "int8",
    }
    if dt in m:
        return m[dt]
    if dt == "numeric":
        return f"numeric({prec},{scale})" if prec else "numeric"
    return dt


def fetch_model(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT c.table_schema, c.table_name, c.column_name, c.data_type,
               c.character_maximum_length, c.numeric_precision, c.numeric_scale,
               c.is_nullable
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema=c.table_schema AND t.table_name=c.table_name
         AND t.table_type='BASE TABLE'
        WHERE c.table_schema NOT IN ('pg_catalog','information_schema')
        ORDER BY c.table_schema, c.table_name, c.ordinal_position
    """)
    cols = defaultdict(list)
    for s, t, col, dt, cl, p, sc, nul in cur.fetchall():
        cols[(s, t)].append({"name": col, "type": short_type(dt, cl, p, sc),
                             "null": nul == "YES"})

    cur.execute("""
        SELECT n.nspname, rel.relname, con.contype, pg_get_constraintdef(con.oid),
               con.conkey, rel.oid,
               fn.nspname, frel.relname
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = rel.relnamespace
        LEFT JOIN pg_class frel ON frel.oid = con.confrelid
        LEFT JOIN pg_namespace fn ON fn.oid = frel.relnamespace
        WHERE n.nspname NOT IN ('pg_catalog','information_schema')
    """)
    pks, uks, fks = defaultdict(set), defaultdict(set), []
    for s, t, ctype, cdef, _conkey, _oid, fs, ft in cur.fetchall():
        inner = cdef[cdef.find("(") + 1: cdef.find(")")]
        local = [c.strip().strip('"') for c in inner.split(",")]
        if ctype == "p":
            pks[(s, t)].update(local)
        elif ctype == "u":
            uks[(s, t)].update(local)
        elif ctype == "f":
            tail = cdef[cdef.rfind("(") + 1: cdef.rfind(")")]
            remote = [c.strip().strip('"') for c in tail.split(",")]
            for lc, rc in zip(local, remote):
                fks.append((s, t, lc, fs, ft, rc))
    return cols, pks, uks, fks


def cell(cid, value, style, parent, x=None, y=None, w=None, h=None,
         vertex=True, source=None, target=None, connectable=None):
    geo_attrs = " ".join(
        f'{k}="{v}"' for k, v in
        (("x", x), ("y", y), ("width", w), ("height", h)) if v is not None)
    kind = 'vertex="1"' if vertex else 'edge="1"'
    if source:
        kind += f' source="{source}"'
    if target:
        kind += f' target="{target}"'
    conn = f' connectable="{connectable}"' if connectable is not None else ""
    return (f'<mxCell id="{cid}" value="{escape(value)}" style="{style}" '
            f'parent="{parent}" {kind}{conn}>'
            f'<mxGeometry {geo_attrs} as="geometry"/></mxCell>')


def build_page(name, tables, compact, cols, pks, uks, all_fks, pid):
    out, row_id = [], {}
    by_schema = defaultdict(list)
    for key in tables:
        by_schema[key[0]].append(key)

    x, y, col_h, idx = 40, 80, 0, 0
    placed = {}
    order = [k for s in sorted(by_schema) for k in sorted(by_schema[s])]

    for key in order:
        s, t = key
        attrs = cols[key]
        if compact:
            keep = pks[key] | {f[2] for f in all_fks if (f[0], f[1]) == key}
            attrs = [a for a in attrs if a["name"] in keep] or attrs[:1]
        h = HDR + ROW * len(attrs)
        if col_h and col_h + h + GAP_Y > MAX_COL_H:
            x += WIDTH + GAP_X
            y, col_h = 80, 0
        placed[key] = (x, y, h)
        fill, stroke = SCHEMA_STYLE.get(s, ("#ffffff", "#000000"))
        tid = f"{pid}_t{idx}"
        idx += 1
        out.append(cell(
            tid, f"{s}.{t}",
            f"shape=table;startSize={HDR};container=1;collapsible=1;"
            f"childLayout=tableLayout;fixedRows=1;rowLines=0;fontStyle=1;"
            f"align=center;resizeLast=1;html=1;fillColor={fill};"
            f"strokeColor={stroke};fontSize=12;",
            "1", x, y, WIDTH, h))

        fk_cols = {f[2] for f in all_fks if (f[0], f[1]) == key}
        for ri, a in enumerate(attrs):
            rid = f"{tid}_r{ri}"
            out.append(cell(
                rid, "",
                "shape=tableRow;horizontal=0;startSize=0;swimlaneHead=0;"
                "swimlaneBody=0;fillColor=none;collapsible=0;dropTarget=0;"
                "points=[[0,0.5],[1,0.5]];portConstraint=eastwest;"
                "top=0;left=0;right=0;bottom=0;",
                tid, y=HDR + ri * ROW, w=WIDTH, h=ROW))
            marks = []
            if a["name"] in pks[key]:
                marks.append("PK")
            if a["name"] in fk_cols:
                marks.append("FK")
            if a["name"] in uks[key] and a["name"] not in pks[key]:
                marks.append("U")
            prefix = ("<b>" + ",".join(marks) + "</b> ") if marks else ""
            nn = "" if a["null"] else " *"
            label = f'{prefix}{a["name"]} : {a["type"]}{nn}'
            out.append(
                f'<mxCell id="{rid}_c" value="{escape(label)}" '
                f'style="shape=partialRectangle;connectable=0;fillColor=none;'
                f'top=0;left=0;bottom=0;right=0;align=left;spacingLeft=6;'
                f'overflow=hidden;fontSize=11;html=1;" parent="{rid}" '
                f'vertex="1" connectable="0">'
                f'<mxGeometry width="{WIDTH}" height="{ROW}" as="geometry"/></mxCell>')
            row_id[(key, a["name"])] = rid
        y += h + GAP_Y
        col_h += h + GAP_Y

    eidx = 0
    seen = set()
    for (s, t, lc, fs, ft, rc, logical) in all_fks:
        child, parent = (s, t), (fs, ft)
        if child not in placed or parent not in placed:
            continue
        src = row_id.get((child, lc))
        dst = row_id.get((parent, rc)) or f"{pid}_t{order.index(parent)}"
        if not src:
            continue
        sig = (src, dst, lc)
        if sig in seen:
            continue
        seen.add(sig)
        style = ("edgeStyle=entityRelationEdgeStyle;rounded=0;html=1;"
                 "endArrow=ERone;startArrow=ERmany;exitX=0;exitY=0.5;"
                 "entryX=1;entryY=0.5;fontSize=10;")
        style += ("dashed=1;strokeColor=#B85450;" if logical
                  else "strokeColor=#4D4D4D;")
        out.append(cell(f"{pid}_e{eidx}", lc, style, "1",
                        vertex=False, source=src, target=dst))
        eidx += 1

    legend = ("<b>Legende</b><br>trait plein = FK contrainte en base<br>"
              "pointille rouge = relation logique (code, sans FK)<br>"
              "PK cle primaire &#8226; FK cle etrangere &#8226; U unique &#8226; * NOT NULL"
              + ("<br><i>Vue globale : seules les colonnes cles sont affichees</i>"
                 if compact else ""))
    out.append(f'<mxCell id="{pid}_leg" value="{escape(legend)}" '
               f'style="text;html=1;align=left;verticalAlign=top;fontSize=12;'
               f'fillColor=#ffffff;strokeColor=#999999;spacing=6;" parent="1" '
               f'vertex="1"><mxGeometry x="40" y="10" width="520" height="60" '
               f'as="geometry"/></mxCell>')
    out.append(f'<mxCell id="{pid}_ttl" value="{escape(name)} — ooredoo_sales" '
               f'style="text;html=1;fontSize=20;fontStyle=1;align=left;" '
               f'parent="1" vertex="1"><mxGeometry x="600" y="10" width="600" '
               f'height="40" as="geometry"/></mxCell>')
    return "".join(out)


def write_markdown(path, cols, pks, uks, fks, tables):
    by_schema = defaultdict(list)
    for k in tables:
        by_schema[k[0]].append(k)
    lines = [
        "# Dictionnaire de donnees — base `ooredoo_sales`",
        "",
        "Genere par `scripts/gen_drawio_erd.py` depuis PostgreSQL "
        "(information_schema + pg_constraint). Ne pas editer a la main.",
        "",
        f"{len(tables)} tables / {len(by_schema)} schemas / {len(fks)} relations.",
        "",
    ]
    for s in sorted(by_schema):
        lines += [f"## Schema `{s}`", ""]
        for key in sorted(by_schema[s]):
            out_fk = {f[2]: f"{f[3]}.{f[4]}.{f[5]}" + (" *(logique)*" if f[6] else "")
                      for f in fks if (f[0], f[1]) == key}
            lines += [f"### `{s}.{key[1]}`", "",
                      "| Colonne | Type | Null | Cle | Reference |",
                      "|---|---|---|---|---|"]
            for a in cols[key]:
                marks = []
                if a["name"] in pks[key]:
                    marks.append("PK")
                if a["name"] in out_fk:
                    marks.append("FK")
                if a["name"] in uks[key] and a["name"] not in pks[key]:
                    marks.append("U")
                lines.append(
                    f'| `{a["name"]}` | {a["type"]} | '
                    f'{"oui" if a["null"] else "non"} | {",".join(marks) or ""} | '
                    f'{out_fk.get(a["name"], "")} |')
            lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def write_csv(path, cols, pks, uks, fks, tables):
    """Variante compacte : spec d'import draw.io (Arrange > Insert > Advanced > CSV).

    Colonnes cles uniquement, pour un fichier collable tel quel.
    """
    head = [
        "# label: %name%",
        "# style: shape=rectangle;rounded=0;html=1;whiteSpace=wrap;align=left;"
        "verticalAlign=top;spacing=6;fillColor=%fill%;strokeColor=%stroke%;",
        "# namespace: erd-",
        '# connect: {"from":"refs","to":"id","invert":true,"style":'
        '"edgeStyle=entityRelationEdgeStyle;html=1;rounded=0;'
        'endArrow=ERone;startArrow=ERmany;strokeColor=#4D4D4D;"}',
        "# width: auto",
        "# height: auto",
        "# nodespacing: 45",
        "# levelspacing: 90",
        "# edgespacing: 40",
        "# layout: organic",
        "## ---------------------------------------------------------------",
        "id,name,fill,stroke,refs",
    ]
    rows = []
    for key in sorted(tables):
        s, t = key
        out_fk = {f[2]: (f[3], f[4]) for f in fks if (f[0], f[1]) == key}
        keep = pks[key] | set(out_fk) | uks[key]
        attrs = [a for a in cols[key] if a["name"] in keep]
        body = []
        for a in attrs:
            marks = []
            if a["name"] in pks[key]:
                marks.append("PK")
            if a["name"] in out_fk:
                marks.append("FK")
            if a["name"] in uks[key] and a["name"] not in pks[key]:
                marks.append("U")
            body.append(f'{"".join(m + " " for m in marks)}{a["name"]} : {a["type"]}')
        others = len(cols[key]) - len(attrs)
        if others:
            body.append(f"... +{others} attributs")
        label = f"<b>{s}.{t}</b><hr size=1>" + "<br>".join(body)
        fill, stroke = SCHEMA_STYLE.get(s, ("#ffffff", "#000000"))
        parents = sorted({f"{p[0]}.{p[1]}" for p in out_fk.values()
                          if p != key and (p[0], p[1]) in tables})
        rows.append(f'{s}.{t},"{label}",{fill},{stroke},"{",".join(parents)}"')
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(head + rows) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/architecture/db_class_diagram.drawio")
    ap.add_argument("--md", default="docs/architecture/db_schema.md")
    ap.add_argument("--csv", default="docs/architecture/db_class_diagram.csv")
    ap.add_argument("--all", action="store_true",
                    help="inclure aussi les tables techniques (agents, logs, RAG, auth)")
    args = ap.parse_args()

    with psycopg2.connect(DSN) as conn:
        cols, pks, uks, real_fks = fetch_model(conn)

    excluded = SKIP_TABLES if args.all else SKIP_TABLES | TECHNICAL_TABLES
    all_tables = [k for k in cols if k not in excluded]
    known = set(all_tables)
    fks = [(*f, False) for f in real_fks
           if (f[0], f[1]) in known and (f[3], f[4]) in known]
    fks += [(*f, True) for f in LOGICAL_FKS
            if (f[0], f[1]) in known and (f[3], f[4]) in known]

    parts = ['<mxfile host="app.diagrams.net" type="device">']
    for i, (name, subset, compact) in enumerate(PAGES):
        tables = all_tables if subset is None else [k for k in subset if k in known]
        body = build_page(name, tables, compact, cols, pks, uks, fks, f"p{i}")
        parts.append(
            f'<diagram id="page{i}" name="{escape(name)}">'
            f'<mxGraphModel dx="1400" dy="900" grid="0" gridSize="10" guides="1" '
            f'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
            f'pageWidth="1654" pageHeight="1169" math="0" shadow="0">'
            f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{body}</root>'
            f'</mxGraphModel></diagram>')
    parts.append("</mxfile>")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    if args.md:
        os.makedirs(os.path.dirname(args.md) or ".", exist_ok=True)
        write_markdown(args.md, cols, pks, uks, fks, all_tables)
        print(f"OK -> {args.md}")
    if args.csv:
        os.makedirs(os.path.dirname(args.csv) or ".", exist_ok=True)
        write_csv(args.csv, cols, pks, uks, fks, set(all_tables))
        print(f"OK -> {args.csv}")
    print(f"OK -> {args.out}  ({len(all_tables)} tables, {len(fks)} relations)")


if __name__ == "__main__":
    main()
