"""
embed_coaching_scripts.py
══════════════════════════
Embed coaching scripts from PostgreSQL into the unified RAG collection.

The companion seed_rag_milvus.py embeds a small inline corpus. This script
loads the complete active corpus from PostgreSQL.
The scripts are stored in the `sales_script` domain of `retail_knowledge`.

Usage:
    python scripts/embed_coaching_scripts.py [--reset] [--dry-run]

Requirements:
    pip install pymilvus tqdm python-dotenv
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", encoding="utf-8")

try:
    from tqdm import tqdm
except ImportError:
    tqdm = lambda x, **kw: x

# ── Config ────────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sales.data.rag import store
from app.sales.data.rag.documents import Document
from app.sales.data.rag.settings import COLLECTION, DOMAIN_SALES_SCRIPT, MILVUS_URI

BATCH_SIZE = 20

# Source des scripts : PostgreSQL (sales.coaching_scripts) — politique zéro-CSV.


def load_from_postgres() -> list[dict]:
    """Charge les scripts actifs depuis sales.coaching_scripts (source de vérité)."""
    import psycopg2
    from psycopg2.extras import RealDictCursor
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "ooredoo_sales"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )
    scripts = []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, categorie, situation, action, produit_cible,
                       argument_vente, impact_observe, heure_min, heure_max, source
                FROM sales.coaching_scripts
                ORDER BY id
            """)
            for row in cur.fetchall():
                scripts.append({
                    "id":        f"pg-sales-{row['id']}",
                    "categorie": (row["categorie"] or "")[:100],
                    "situation": (row["situation"] or "")[:2000],
                    "action":    (row["action"] or "")[:2000],
                    "produit":   (row["produit_cible"] or "")[:500],
                    "argument":  (row["argument_vente"] or "")[:2000],
                    "impact":    (row["impact_observe"] or "")[:500],
                    "heure_min": int(row["heure_min"] or 8),
                    "heure_max": int(row["heure_max"] or 20),
                    "source":    (row["source"] or "pg")[:100],
                })
    finally:
        conn.close()
    return scripts


def build_full_text(s: dict) -> str:
    parts = [
        f"Situation: {s['situation']}",
        f"Action: {s['action']}",
        f"Produit: {s['produit']}",
        f"Argument: {s['argument']}",
        f"Impact: {s['impact']}",
        f"Catégorie: {s['categorie']}",
    ]
    return " | ".join(p for p in parts if p.split(": ", 1)[1])[:4000]


def to_document(script: dict) -> Document:
    full_text = build_full_text(script)
    return Document(
        doc_id=script["id"],
        domain=DOMAIN_SALES_SCRIPT,
        title=script["situation"][:512],
        text=full_text,
        doc_type=script["categorie"],
        categorie=script["categorie"],
        produit=script["produit"],
        heure_min=script["heure_min"],
        heure_max=script["heure_max"],
        payload={
            "situation": script["situation"], "action": script["action"],
            "argument": script["argument"], "impact": script["impact"],
            "source": script["source"],
        },
    )


def main():
    parser = argparse.ArgumentParser(description="Embed PostgreSQL coaching scripts into the unified RAG")
    parser.add_argument("--reset",   action="store_true", help="Supprimer et recréer la collection")
    parser.add_argument("--dry-run", action="store_true", help="Charger depuis PostgreSQL sans écrire dans Milvus")
    args = parser.parse_args()

    scripts = load_from_postgres()
    print(f"PostgreSQL : {len(scripts)} scripts actifs chargés (sales.coaching_scripts)")

    if args.dry_run:
        print(f"DRY RUN — Aperçu des 3 premiers scripts :")
        for s in scripts[:3]:
            print(f"  [{s['categorie']}] {s['situation'][:80]}...")
        return

    client = store.get_client(recreate=args.reset)
    if client is None:
        sys.exit("Milvus collection unavailable. Check Docker/MILVUS_URI.")

    documents = [to_document(script) for script in scripts]
    inserted = 0
    for i in tqdm(range(0, len(scripts), BATCH_SIZE), desc="Embedding PostgreSQL scripts"):
        inserted += store.upsert(documents[i : i + BATCH_SIZE], batch_size=BATCH_SIZE)

    print(f"\n✅ {inserted} scripts CSV embeddés dans {COLLECTION!r}")
    stats = store.stats()
    print(f"   Stats : {stats}")
    if inserted != len(documents) or not stats.get("available"):
        sys.exit("Seed incomplet: vérifiez Milvus et Ollama, puis relancez.")


if __name__ == "__main__":
    main()
