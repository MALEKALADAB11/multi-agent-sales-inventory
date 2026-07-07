"""
embed_coaching_scripts.py
══════════════════════════
Embed coaching scripts from coaching_scripts_ooredoo.csv into Milvus.

The existing seed_rag_milvus.py embeds 200+ scripts defined inline as JSON.
This script handles the CSV file (64 scripts) from the field (sales-module/data/).
Both share the same Milvus collection: coaching_scripts (dim=768).

Usage:
    python scripts/embed_coaching_scripts.py [--reset] [--dry-run]

Requirements:
    pip install pymilvus requests tqdm python-dotenv pandas
"""

import argparse
import csv
import os
import sys
import time
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", encoding="utf-8")

try:
    from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility
except ImportError:
    print("ERROR: pip install pymilvus", file=sys.stderr)
    sys.exit(1)

try:
    from tqdm import tqdm
except ImportError:
    tqdm = lambda x, **kw: x

# ── Config ────────────────────────────────────────────────────────────────────
MILVUS_URI   = os.getenv("MILVUS_URI", "http://localhost:19530")
OLLAMA_URL   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
COLLECTION   = "coaching_scripts"
EMBED_DIM    = 768
EMBED_MODEL  = "nomic-embed-text"
BATCH_SIZE   = 10

# Source des scripts : PostgreSQL (sales.coaching_scripts) — politique zéro-CSV.


def connect_milvus() -> None:
    host = MILVUS_URI.replace("http://", "").split(":")[0]
    port = int(MILVUS_URI.replace("http://", "").split(":")[1].rstrip("/"))
    connections.connect("default", host=host, port=port)
    print(f"Milvus connecté → {host}:{port}")


def ensure_collection() -> Collection:
    if utility.has_collection(COLLECTION):
        return Collection(COLLECTION)

    fields = [
        FieldSchema("id",        DataType.VARCHAR,       max_length=64,  is_primary=True, auto_id=False),
        FieldSchema("embedding", DataType.FLOAT_VECTOR,  dim=EMBED_DIM),
        FieldSchema("categorie", DataType.VARCHAR,       max_length=100),
        FieldSchema("situation", DataType.VARCHAR,       max_length=2000),
        FieldSchema("action",    DataType.VARCHAR,       max_length=2000),
        FieldSchema("produit",   DataType.VARCHAR,       max_length=500),
        FieldSchema("argument",  DataType.VARCHAR,       max_length=2000),
        FieldSchema("impact",    DataType.VARCHAR,       max_length=500),
        FieldSchema("heure_min", DataType.INT16),
        FieldSchema("heure_max", DataType.INT16),
        FieldSchema("source",    DataType.VARCHAR,       max_length=100),
        FieldSchema("full_text", DataType.VARCHAR,       max_length=4000),
    ]
    schema = CollectionSchema(fields, description="Ooredoo coaching scripts (CSV + JSON)")
    col = Collection(COLLECTION, schema)

    col.create_index("embedding", {
        "index_type": "IVF_FLAT",
        "metric_type": "COSINE",
        "params": {"nlist": 128},
    })
    col.load()
    print(f"Collection {COLLECTION!r} créée (dim={EMBED_DIM})")
    return col


def embed_text(text: str) -> list[float]:
    resp = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=30,
    )
    resp.raise_for_status()
    vec = resp.json().get("embedding", [])
    if not vec or len(vec) != EMBED_DIM:
        raise ValueError(f"Embedding invalide (len={len(vec)})")
    return vec


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
                WHERE actif
                ORDER BY id
            """)
            for row in cur.fetchall():
                scripts.append({
                    "id":        f"pg-{row['id']}",
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


def insert_batch(col: Collection, batch: list[dict]) -> int:
    ids, embeddings, cats, sits, acts, prods, args, imps, hmins, hmaxs, srcs, txts = (
        [], [], [], [], [], [], [], [], [], [], [], []
    )
    for s in batch:
        full_text = build_full_text(s)
        try:
            vec = embed_text(full_text)
        except Exception as e:
            print(f"  WARN embed échoué pour {s['id']}: {e}")
            continue
        ids.append(s["id"])
        embeddings.append(vec)
        cats.append(s["categorie"])
        sits.append(s["situation"])
        acts.append(s["action"])
        prods.append(s["produit"])
        args.append(s["argument"])
        imps.append(s["impact"])
        hmins.append(s["heure_min"])
        hmaxs.append(s["heure_max"])
        srcs.append(s["source"])
        txts.append(full_text)
        time.sleep(0.05)  # respect Ollama rate

    if not ids:
        return 0

    col.insert([ids, embeddings, cats, sits, acts, prods, args, imps, hmins, hmaxs, srcs, txts])
    return len(ids)


def main():
    parser = argparse.ArgumentParser(description="Embed coaching scripts PostgreSQL → Milvus")
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

    connect_milvus()

    if args.reset and utility.has_collection(COLLECTION):
        utility.drop_collection(COLLECTION)
        print(f"Collection {COLLECTION!r} supprimée")

    col = ensure_collection()

    inserted = 0
    for i in tqdm(range(0, len(scripts), BATCH_SIZE), desc="Embedding CSV scripts"):
        batch = scripts[i : i + BATCH_SIZE]
        n = insert_batch(col, batch)
        inserted += n

    col.flush()
    print(f"\n✅ {inserted} scripts CSV embeddés dans {COLLECTION!r}")
    print(f"   Total collection : {col.num_entities} vecteurs")
    print(f"   Run seed_rag_milvus.py --reset pour re-embedder les scripts JSON en plus")


if __name__ == "__main__":
    main()
