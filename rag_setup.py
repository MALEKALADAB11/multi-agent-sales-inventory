"""
rag_setup_complet.py — Setup complet RAG Ooredoo
=================================================
Étapes :
  1. Crée la table coaching_scripts dans PostgreSQL
  2. Charge le CSV (coaching_scripts_ooredoo.csv)
  3. Génère les embeddings via Ollama (nomic-embed-text)
  4. Stocke dans Milvus Docker

Lancer UNE SEULE FOIS :
    python rag_setup_complet.py

Prérequis :
    pip install pymilvus requests psycopg2-binary pandas
    ollama pull nomic-embed-text
    docker compose up -d
"""

import os
import sys
import csv
import time
import logging
import requests
import psycopg2
from psycopg2.extras import RealDictCursor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────
DB_CONFIG = {
    "host": "localhost", "port": 5432,
    "dbname": "ooredoo_sales",
    "user": "postgres", "password": "admin",
}
MILVUS_URI  = "http://localhost:19530"
COLLECTION  = "coaching_scripts"
EMBED_MODEL = "nomic-embed-text"
OLLAMA_URL  = "http://localhost:11434"
EMBED_DIM   = 768
CSV_FILE    = "sales-module\\data\\coaching_scripts_ooredoo.csv"


# ══════════════════════════════════════════════════════════════
# ÉTAPE 1 — PostgreSQL : Créer la table et charger le CSV
# ══════════════════════════════════════════════════════

def setup_postgres():
    logger.info("=== ÉTAPE 1 : PostgreSQL ===")
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_client_encoding("UTF8")

    with conn.cursor() as cur:
        # Créer la table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS coaching_scripts (
                id              SERIAL PRIMARY KEY,
                categorie       VARCHAR(100),
                situation       TEXT,
                action          TEXT,
                produit_cible   VARCHAR(200),
                argument_vente  TEXT,
                impact_observe  TEXT,
                heure_min       INT DEFAULT 9,
                heure_max       INT DEFAULT 20,
                jour_semaine    INT DEFAULT -1,
                store_id        VARCHAR(20) DEFAULT 'ALL',
                source          VARCHAR(50) DEFAULT 'manual',
                embedded        BOOLEAN DEFAULT FALSE,
                created_at      TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_cs_categorie ON coaching_scripts(categorie)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_cs_store ON coaching_scripts(store_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_cs_embedded ON coaching_scripts(embedded)"
        )

        # Vérifier si déjà peuplé
        cur.execute("SELECT COUNT(*) FROM coaching_scripts")
        count = cur.fetchone()[0]
        logger.info(f"Scripts existants dans PostgreSQL : {count}")

        # Charger le CSV
        if not os.path.exists(CSV_FILE):
            logger.error(f"CSV non trouvé : {CSV_FILE}")
            sys.exit(1)

        inserted = 0
        skipped  = 0
        with open(CSV_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Vérifier doublon sur (categorie + situation)
                cur.execute(
                    "SELECT id FROM coaching_scripts WHERE categorie=%s AND situation=%s LIMIT 1",
                    (row["categorie"].strip(), row["situation"].strip()),
                )
                if cur.fetchone():
                    skipped += 1
                    continue

                cur.execute("""
                    INSERT INTO coaching_scripts
                        (categorie, situation, action, produit_cible,
                         argument_vente, impact_observe,
                         heure_min, heure_max, jour_semaine, store_id, source)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    row["categorie"].strip(),
                    row["situation"].strip(),
                    row["action"].strip(),
                    row["produit_cible"].strip(),
                    row["argument_vente"].strip(),
                    row["impact_observe"].strip(),
                    int(row.get("heure_min", 9)),
                    int(row.get("heure_max", 20)),
                    int(row.get("jour_semaine", -1)),
                    row.get("store_id", "ALL").strip(),
                    row.get("source", "csv").strip(),
                ))
                inserted += 1

    conn.commit()
    conn.close()
    logger.info(f"PostgreSQL OK : {inserted} insérés, {skipped} doublons ignorés")
    return inserted


# ══════════════════════════════════════════════════════════════
# ÉTAPE 2 — Ollama : Tester les embeddings
# ══════════════════════════════════════════════════════════════

def embed_text(text: str) -> list | None:
    """Génère un embedding via Ollama nomic-embed-text."""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text[:1500]},
            timeout=30,
        )
        resp.raise_for_status()
        emb = resp.json().get("embedding", [])
        if not emb:
            return None
        # Normaliser à EMBED_DIM=768
        if len(emb) < EMBED_DIM:
            emb = emb + [0.0] * (EMBED_DIM - len(emb))
        elif len(emb) > EMBED_DIM:
            emb = emb[:EMBED_DIM]
        return emb
    except Exception as e:
        logger.error(f"Embedding échoué : {e}")
        return None


def test_ollama():
    logger.info("=== ÉTAPE 2 : Test Ollama embeddings ===")
    emb = embed_text("test Ooredoo coaching vente")
    if emb:
        logger.info(f"Ollama OK : dimension={len(emb)}")
        return True
    else:
        logger.error("Ollama non disponible — lancez : ollama serve")
        return False


# ══════════════════════════════════════════════════════════════
# ÉTAPE 3 — Milvus : Créer la collection
# ══════════════════════════════════════════════════════════════

def setup_milvus():
    logger.info("=== ÉTAPE 3 : Milvus collection ===")
    try:
        from pymilvus import MilvusClient, DataType
    except ImportError:
        logger.error("pymilvus non installé : pip install pymilvus")
        sys.exit(1)

    client = MilvusClient(uri=MILVUS_URI)

    # Supprimer la collection si elle existe (reset propre)
    if client.has_collection(COLLECTION):
        logger.info(f"Collection '{COLLECTION}' existe — suppression pour reset...")
        client.drop_collection(COLLECTION)

    # Créer le schéma
    schema = client.create_schema(
        auto_id=True,
        enable_dynamic_field=True,
    )
    schema.add_field("id",          DataType.INT64,   is_primary=True, auto_id=True)
    schema.add_field("vector",      DataType.FLOAT_VECTOR, dim=EMBED_DIM)
    schema.add_field("pg_id",       DataType.INT64)
    schema.add_field("categorie",   DataType.VARCHAR, max_length=200)
    schema.add_field("situation",   DataType.VARCHAR, max_length=1000)
    schema.add_field("action",      DataType.VARCHAR, max_length=500)
    schema.add_field("produit",     DataType.VARCHAR, max_length=300)
    schema.add_field("argument",    DataType.VARCHAR, max_length=1000)
    schema.add_field("impact",      DataType.VARCHAR, max_length=300)
    schema.add_field("heure_min",   DataType.INT64)
    schema.add_field("heure_max",   DataType.INT64)
    schema.add_field("jour_semaine",DataType.INT64)
    schema.add_field("store_id",    DataType.VARCHAR, max_length=50)

    # Paramètres d'index
    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name  = "vector",
        index_type  = "IVF_FLAT",
        metric_type = "COSINE",
        params      = {"nlist": 128},
    )

    # Créer la collection
    client.create_collection(
        collection_name = COLLECTION,
        schema          = schema,
        index_params    = index_params,
    )
    logger.info(f"Collection '{COLLECTION}' créée avec succès (dim={EMBED_DIM})")
    return client


# ══════════════════════════════════════════════════════════════
# ÉTAPE 4 — Embeddings + insertion Milvus
# ══════════════════════════════════════════════════════════════

def embed_and_insert(client):
    logger.info("=== ÉTAPE 4 : Génération embeddings + insertion Milvus ===")

    # Récupérer les scripts depuis PostgreSQL
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_client_encoding("UTF8")
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT id, categorie, situation, action,
                   produit_cible, argument_vente, impact_observe,
                   heure_min, heure_max, jour_semaine, store_id
            FROM coaching_scripts
            ORDER BY id
        """)
        scripts = [dict(r) for r in cur.fetchall()]
    conn.close()

    logger.info(f"{len(scripts)} scripts à embedder...")

    batch      = []
    pg_ids_ok  = []
    errors     = 0
    total      = len(scripts)

    for i, s in enumerate(scripts, 1):
        # Texte à embedder : combinaison situation + action + produit + argument
        text = (
            f"Situation: {s['situation']} "
            f"Action: {s['action']} "
            f"Produit: {s['produit_cible']} "
            f"Argument: {s['argument_vente']}"
        )

        emb = embed_text(text)
        if emb is None:
            logger.warning(f"  [{i}/{total}] ÉCHEC embedding id={s['id']}")
            errors += 1
            continue

        batch.append({
            "vector":      emb,
            "pg_id":       int(s["id"]),
            "categorie":   (s["categorie"]       or "")[:200],
            "situation":   (s["situation"]        or "")[:1000],
            "action":      (s["action"]           or "")[:500],
            "produit":     (s["produit_cible"]    or "")[:300],
            "argument":    (s["argument_vente"]   or "")[:1000],
            "impact":      (s["impact_observe"]   or "")[:300],
            "heure_min":   int(s["heure_min"]     or 9),
            "heure_max":   int(s["heure_max"]     or 20),
            "jour_semaine":int(s["jour_semaine"]  or -1),
            "store_id":    (s["store_id"]         or "ALL")[:50],
        })
        pg_ids_ok.append(s["id"])

        logger.info(f"  [{i}/{total}] ✓ {s['categorie'][:40]}")

        # Insérer par batch de 10
        if len(batch) >= 10:
            client.insert(collection_name=COLLECTION, data=batch)
            logger.info(f"  → Batch inséré ({len(batch)} vecteurs)")
            batch = []
            time.sleep(0.5)  # éviter surcharge Ollama

    # Dernier batch
    if batch:
        client.insert(collection_name=COLLECTION, data=batch)
        logger.info(f"  → Dernier batch inséré ({len(batch)} vecteurs)")

    # Marquer comme embedded dans PostgreSQL
    if pg_ids_ok:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.set_client_encoding("UTF8")
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE coaching_scripts SET embedded=TRUE WHERE id = ANY(%s)",
                (pg_ids_ok,)
            )
        conn.commit()
        conn.close()

    logger.info(f"Embeddings terminés : {len(pg_ids_ok)} OK, {errors} erreurs")
    return len(pg_ids_ok)


# ══════════════════════════════════════════════════════════════
# ÉTAPE 5 — Vérification finale
# ══════════════════════════════════════════════════════════════

def verify(client):
    logger.info("=== ÉTAPE 5 : Vérification ===")

    # Stats Milvus
    stats = client.get_collection_stats(COLLECTION)
    row_count = stats.get("row_count", "?")
    logger.info(f"Milvus '{COLLECTION}' : {row_count} vecteurs")

    # Test de recherche
    test_query = "gap critique objectif vente forfait urgent"
    emb = embed_text(test_query)
    if emb:
        results = client.search(
            collection_name = COLLECTION,
            data            = [emb],
            limit           = 3,
            output_fields   = ["categorie", "action", "produit", "score"],
        )
        logger.info(f"Test recherche : '{test_query}'")
        for r in results[0]:
            logger.info(
                f"  → [{r['entity']['categorie']}] "
                f"score={r['distance']:.3f} | "
                f"{r['entity']['action'][:60]}..."
            )
    else:
        logger.warning("Test recherche ignoré — Ollama non disponible")

    # Stats PostgreSQL
    conn = psycopg2.connect(**DB_CONFIG)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM coaching_scripts")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM coaching_scripts WHERE embedded=TRUE")
        embedded = cur.fetchone()[0]
        cur.execute("SELECT categorie, COUNT(*) FROM coaching_scripts GROUP BY categorie ORDER BY COUNT(*) DESC LIMIT 5")
        top_cats = cur.fetchall()
    conn.close()

    logger.info(f"PostgreSQL coaching_scripts : {total} total, {embedded} embeddés")
    logger.info("Top 5 catégories :")
    for cat, cnt in top_cats:
        logger.info(f"  {cat:40s} : {cnt}")


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info("╔══════════════════════════════════════════════╗")
    logger.info("║   RAG SETUP — Ooredoo AI Sales Coach         ║")
    logger.info("╚══════════════════════════════════════════════╝")

    # 1. PostgreSQL
    nb_inserted = setup_postgres()

    # 2. Ollama
    if not test_ollama():
        logger.error("Arrêt : Ollama requis pour les embeddings")
        sys.exit(1)

    # 3. Milvus
    client = setup_milvus()

    # 4. Embeddings + insertion
    nb_ok = embed_and_insert(client)

    # 5. Vérification
    verify(client)

    logger.info("╔══════════════════════════════════════════════╗")
    logger.info(f"║  TERMINÉ : {nb_ok} scripts dans Milvus           ║")
    logger.info("╚══════════════════════════════════════════════╝")