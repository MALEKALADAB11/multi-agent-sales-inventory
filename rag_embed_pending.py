"""
rag_embed_pending.py — Embedde les scripts PostgreSQL non encore embeddés dans Milvus.
Utile quand Milvus n'était pas disponible lors de l'insertion PostgreSQL.

Usage:
    python rag_embed_pending.py
"""

import time, logging, requests, psycopg2
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


def get_conn():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_client_encoding("UTF8")
    return conn


def embed_text(text: str):
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": "nomic-embed-text", "prompt": text[:1200]},
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


if __name__ == "__main__":
    logger.info("╔══════════════════════════════════════════════════╗")
    logger.info("║  RAG EMBED PENDING — Ooredoo AI Sales Coach      ║")
    logger.info("╚══════════════════════════════════════════════════╝")

    # ── Vérifier Milvus ───────────────────────────────────────────────────
    try:
        from pymilvus import MilvusClient
        client = MilvusClient(uri=MILVUS_URI)
        stats  = client.get_collection_stats(COLLECTION)
        existing = int(stats.get("row_count", 0))
        logger.info(f"Milvus OK — {existing} vecteurs existants dans '{COLLECTION}'")
    except Exception as e:
        logger.error(f"Milvus non disponible: {e}")
        logger.error("Lancez d'abord: docker compose up -d")
        exit(1)

    # ── Vérifier Ollama ───────────────────────────────────────────────────
    test_emb = embed_text("test Ooredoo coaching")
    if test_emb is None:
        logger.error("Ollama non disponible. Lancez: ollama serve")
        exit(1)
    logger.info(f"Ollama OK — dimension embedding: {len(test_emb)}")

    # ── Récupérer les scripts non embeddés ────────────────────────────────
    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT id, categorie, situation, action,
                   produit_cible, argument_vente, impact_observe,
                   heure_min, heure_max, jour_semaine, store_id
            FROM coaching_scripts
            WHERE embedded = FALSE
            ORDER BY id
        """)
        scripts = [dict(r) for r in cur.fetchall()]
    conn.close()

    total = len(scripts)
    logger.info(f"{total} scripts non embeddés à traiter")

    if total == 0:
        logger.info("Tous les scripts sont déjà embeddés dans Milvus !")
        exit(0)

    # ── Embeddings + insertion Milvus ─────────────────────────────────────
    batch, ok_ids, errors = [], [], 0

    for i, s in enumerate(scripts, 1):
        text = (
            f"Situation: {s['situation']} "
            f"Action: {s['action']} "
            f"Produit: {s['produit_cible']} "
            f"Argument: {s['argument_vente']}"
        )
        emb = embed_text(text)
        if emb is None:
            logger.warning(f"[{i}/{total}] ÉCHEC id={s['id']} — {s['categorie'][:40]}")
            errors += 1
            continue

        batch.append({
            "vector":      emb,
            "pg_id":       int(s["id"]),
            "categorie":   (s["categorie"]     or "")[:200],
            "situation":   (s["situation"]     or "")[:1000],
            "action":      (s["action"]        or "")[:500],
            "produit":     (s["produit_cible"] or "")[:300],
            "argument":    (s["argument_vente"]or "")[:1000],
            "impact":      (s["impact_observe"]or "")[:300],
            "heure_min":   int(s["heure_min"]   or 9),
            "heure_max":   int(s["heure_max"]   or 20),
            "jour_semaine":int(s["jour_semaine"] or -1),
            "store_id":    (s["store_id"]       or "ALL")[:50],
        })
        ok_ids.append(s["id"])
        logger.info(f"  [{i}/{total}] ✓ {s['categorie'][:50]}")

        # Insérer par batch de 10
        if len(batch) >= 10:
            client.insert(collection_name=COLLECTION, data=batch)
            logger.info(f"  → Batch {len(batch)} vecteurs insérés dans Milvus")
            batch = []
            time.sleep(0.3)

    # Dernier batch
    if batch:
        client.insert(collection_name=COLLECTION, data=batch)
        logger.info(f"  → Dernier batch {len(batch)} vecteurs insérés")

    # ── Marquer comme embeddés dans PostgreSQL ────────────────────────────
    if ok_ids:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE coaching_scripts SET embedded=TRUE WHERE id=ANY(%s)",
                (ok_ids,)
            )
        conn.commit()
        conn.close()

    # ── Vérification finale ───────────────────────────────────────────────
    stats_final = client.get_collection_stats(COLLECTION)
    total_milvus = int(stats_final.get("row_count", 0))

    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM coaching_scripts")
        total_pg = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM coaching_scripts WHERE embedded=TRUE")
        embedded_pg = cur.fetchone()[0]
    conn.close()

    logger.info("─" * 50)
    logger.info(f"PostgreSQL : {total_pg} scripts | {embedded_pg} embeddés")
    logger.info(f"Milvus     : {total_milvus} vecteurs")
    logger.info(f"Résultat   : {len(ok_ids)} embeddés | {errors} erreurs")

    # Test recherche final
    queries = [
        "gap critique urgent closing bundle terminal",
        "pluie météo accessoires résistants eau",
        "objection prix trop cher iphone recadrage",
        "upsell assurance premium après vente terminal",
    ]
    logger.info("\nTests de recherche RAG:")
    for q in queries:
        emb = embed_text(q)
        if emb:
            res = client.search(
                collection_name=COLLECTION, data=[emb],
                limit=2, output_fields=["categorie","action"],
            )
            logger.info(f"\n  Requête: '{q[:45]}'")
            for r in res[0]:
                logger.info(
                    f"    → [{r['entity']['categorie']}] "
                    f"score={r['distance']:.3f} | "
                    f"{r['entity']['action'][:55]}..."
                )

    logger.info("\n╔══════════════════════════════════════════════════╗")
    logger.info(f"║  TERMINÉ: {total_milvus} vecteurs dans Milvus         ║")
    logger.info("╚══════════════════════════════════════════════════╝")
