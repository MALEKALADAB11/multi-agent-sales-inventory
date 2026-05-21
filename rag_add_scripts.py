"""
rag_add_scripts.py — Ajoute de nouveaux scripts RAG sans supprimer Milvus.
=========================================================================
Lit le CSV v2, insère dans PostgreSQL (sans doublons), génère les embeddings
et insère dans Milvus les scripts non encore embeddés.

Usage :
    python rag_add_scripts.py
"""

import os, sys, csv, time, logging, requests, psycopg2
from psycopg2.extras import RealDictCursor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DB_CONFIG  = {"host":"localhost","port":5432,"dbname":"ooredoo_sales","user":"postgres","password":"admin"}
MILVUS_URI = "http://localhost:19530"
COLLECTION = "coaching_scripts"
OLLAMA_URL = "http://localhost:11434"
EMBED_DIM  = 768
CSV_FILE   = "coaching_scripts_v2.csv"   # ← nouveau fichier


def get_conn():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_client_encoding("UTF8")
    return conn


def embed_text(text: str):
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model":"nomic-embed-text","prompt":text[:1200]},
            timeout=30,
        )
        emb = r.json().get("embedding", [])
        if not emb:
            return None
        if len(emb) < EMBED_DIM:
            emb += [0.0] * (EMBED_DIM - len(emb))
        return emb[:EMBED_DIM]
    except Exception as e:
        logger.warning(f"Embedding échoué: {e}")
        return None


# ── ÉTAPE 1 : Insérer dans PostgreSQL ────────────────────────────────────────
def insert_postgres() -> list[int]:
    logger.info("=== ÉTAPE 1 : Insertion PostgreSQL ===")
    if not os.path.exists(CSV_FILE):
        logger.error(f"CSV non trouvé : {CSV_FILE}")
        sys.exit(1)

    conn = get_conn()
    inserted_ids = []
    skipped = 0

    with conn.cursor() as cur:
        with open(CSV_FILE, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                # Vérifier doublon
                cur.execute(
                    "SELECT id FROM coaching_scripts WHERE categorie=%s AND situation=%s LIMIT 1",
                    (row["categorie"].strip(), row["situation"].strip()),
                )
                if cur.fetchone():
                    skipped += 1
                    continue

                cur.execute("""
                    INSERT INTO coaching_scripts
                        (categorie,situation,action,produit_cible,argument_vente,
                         impact_observe,heure_min,heure_max,jour_semaine,store_id,source)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
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
                    row.get("source", "csv_v2").strip(),
                ))
                new_id = cur.fetchone()[0]
                inserted_ids.append(new_id)

    conn.commit()
    conn.close()
    logger.info(f"PostgreSQL : {len(inserted_ids)} insérés | {skipped} doublons ignorés")
    return inserted_ids


# ── ÉTAPE 2 : Embeddings + insertion Milvus ───────────────────────────────────
def embed_and_insert_milvus(pg_ids: list[int]):
    logger.info("=== ÉTAPE 2 : Embeddings + insertion Milvus ===")
    if not pg_ids:
        logger.info("Aucun nouveau script à embedder.")
        return

    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT id,categorie,situation,action,produit_cible,
                   argument_vente,impact_observe,heure_min,heure_max,
                   jour_semaine,store_id
            FROM coaching_scripts
            WHERE id = ANY(%s)
            ORDER BY id
        """, (pg_ids,))
        scripts = [dict(r) for r in cur.fetchall()]
    conn.close()

    try:
        from pymilvus import MilvusClient
        client = MilvusClient(uri=MILVUS_URI)
    except Exception as e:
        logger.error(f"Milvus non disponible: {e}")
        return

    batch, ok_ids, errors = [], [], 0
    total = len(scripts)

    for i, s in enumerate(scripts, 1):
        text = (
            f"Situation: {s['situation']} "
            f"Action: {s['action']} "
            f"Produit: {s['produit_cible']} "
            f"Argument: {s['argument_vente']}"
        )
        emb = embed_text(text)
        if emb is None:
            logger.warning(f"[{i}/{total}] ÉCHEC id={s['id']}")
            errors += 1
            continue

        batch.append({
            "vector":      emb,
            "pg_id":       int(s["id"]),
            "categorie":   (s["categorie"]    or "")[:200],
            "situation":   (s["situation"]    or "")[:1000],
            "action":      (s["action"]       or "")[:500],
            "produit":     (s["produit_cible"]or "")[:300],
            "argument":    (s["argument_vente"]or "")[:1000],
            "impact":      (s["impact_observe"]or "")[:300],
            "heure_min":   int(s["heure_min"]  or 9),
            "heure_max":   int(s["heure_max"]  or 20),
            "jour_semaine":int(s["jour_semaine"]or -1),
            "store_id":    (s["store_id"]      or "ALL")[:50],
        })
        ok_ids.append(s["id"])
        logger.info(f"  [{i}/{total}] ✓ {s['categorie'][:45]}")

        if len(batch) >= 10:
            client.insert(collection_name=COLLECTION, data=batch)
            logger.info(f"  → Batch {len(batch)} vecteurs insérés dans Milvus")
            batch = []
            time.sleep(0.3)

    if batch:
        client.insert(collection_name=COLLECTION, data=batch)
        logger.info(f"  → Dernier batch {len(batch)} vecteurs insérés")

    # Marquer comme embeddés dans PostgreSQL
    if ok_ids:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE coaching_scripts SET embedded=TRUE WHERE id=ANY(%s)",
                (ok_ids,)
            )
        conn.commit()
        conn.close()

    logger.info(f"Embeddings terminés: {len(ok_ids)} OK | {errors} erreurs")


# ── ÉTAPE 3 : Vérification finale ────────────────────────────────────────────
def verify():
    logger.info("=== ÉTAPE 3 : Vérification ===")

    # Stats PostgreSQL
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM coaching_scripts")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM coaching_scripts WHERE embedded=TRUE")
        embedded = cur.fetchone()[0]
        cur.execute("""
            SELECT categorie, COUNT(*) as nb
            FROM coaching_scripts
            GROUP BY categorie
            ORDER BY nb DESC LIMIT 8
        """)
        top_cats = cur.fetchall()
    conn.close()

    logger.info(f"PostgreSQL: {total} scripts total | {embedded} embeddés")
    logger.info("Top catégories:")
    for cat, nb in top_cats:
        logger.info(f"  {cat[:45]:45s} : {nb}")

    # Stats Milvus
    try:
        from pymilvus import MilvusClient
        client = MilvusClient(uri=MILVUS_URI)
        stats  = client.get_collection_stats(COLLECTION)
        logger.info(f"Milvus '{COLLECTION}': {stats.get('row_count','?')} vecteurs")

        # Test recherche
        emb = embed_text("gap critique urgent closing bundle terminal forfait assurance")
        if emb:
            results = client.search(
                collection_name = COLLECTION,
                data            = [emb],
                limit           = 3,
                output_fields   = ["categorie","action","produit"],
            )
            logger.info("Test recherche 'gap critique urgent':")
            for r in results[0]:
                logger.info(
                    f"  → [{r['entity']['categorie']}] "
                    f"score={r['distance']:.3f} | {r['entity']['action'][:50]}..."
                )
    except Exception as e:
        logger.warning(f"Milvus vérification: {e}")


# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("╔══════════════════════════════════════════════════╗")
    logger.info("║  RAG ADD SCRIPTS v2 — Ooredoo AI Sales Coach     ║")
    logger.info("╚══════════════════════════════════════════════════╝")

    new_ids = insert_postgres()
    embed_and_insert_milvus(new_ids)
    verify()

    logger.info("╔══════════════════════════════════════════════════╗")
    logger.info(f"║  TERMINÉ: {len(new_ids)} nouveaux scripts ajoutés au RAG  ║")
    logger.info("╚══════════════════════════════════════════════════╝")
