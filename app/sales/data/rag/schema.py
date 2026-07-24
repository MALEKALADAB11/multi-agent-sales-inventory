"""
rag/schema.py — Schéma de la collection `retail_knowledge`.

Collection unique, deux index sur le même champ `text` :
  • `dense`  : FLOAT_VECTOR(768) HNSW/COSINE  → similarité sémantique
  • `sparse` : SPARSE_FLOAT_VECTOR, rempli côté serveur par la fonction BM25 de
               Milvus 2.5 → correspondance lexicale exacte (SKU, "iPhone 16",
               "MOQ", noms propres — là où le dense se noie).

Le sparse n'est jamais fourni à l'insertion : la Function BM25 le dérive de `text`.
`domain` est clé de partition : Milvus route physiquement les lignes par domaine,
donc un filtre `domain == "product"` ne scanne pas les playbooks.
"""

import logging

from pymilvus import DataType, Function, FunctionType

from app.sales.data.rag.settings import COLLECTION, EMBED_DIM

logger = logging.getLogger(__name__)

# Stopwords FR — le filtre `stop` de Milvus n'embarque pas de liste française.
_FRENCH_STOPWORDS = [
    "le", "la", "les", "un", "une", "des", "de", "du", "au", "aux", "et", "ou",
    "en", "sur", "pour", "avec", "sans", "dans", "que", "qui", "quoi", "est",
    "sont", "mon", "ma", "mes", "ton", "ta", "tes", "son", "sa", "ses", "ce",
    "cet", "cette", "ces", "il", "elle", "je", "tu", "on", "nous", "vous",
    "ils", "elles", "pas", "plus", "moins", "tres", "comment", "quel", "quelle",
    "a", "y", "d", "l", "s", "n", "c", "j", "m", "t", "se", "ne", "par", "plus",
]

# asciifolding : "réappro" et "reappro" doivent tomber sur le même token.
# stemmer french : "ruptures"/"rupture", "vendre"/"vendu".
FRENCH_ANALYZER = {
    "tokenizer": "standard",
    "filter": [
        "lowercase",
        "asciifolding",
        {"type": "stop", "stop_words": _FRENCH_STOPWORDS},
        {"type": "stemmer", "language": "french"},
    ],
}

STANDARD_ANALYZER = {"type": "standard"}


def build_schema(client, analyzer_params: dict):
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)

    schema.add_field("doc_id", DataType.VARCHAR, is_primary=True, max_length=128)

    # Champ analysé : source du BM25 ET texte embeddé côté client.
    schema.add_field(
        "text", DataType.VARCHAR, max_length=8000,
        enable_analyzer=True, analyzer_params=analyzer_params,
    )
    schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
    schema.add_field("dense", DataType.FLOAT_VECTOR, dim=EMBED_DIM)

    schema.add_field("domain", DataType.VARCHAR, max_length=32, is_partition_key=True)
    schema.add_field("doc_type",  DataType.VARCHAR, max_length=64)
    schema.add_field("title",     DataType.VARCHAR, max_length=512)
    schema.add_field("categorie", DataType.VARCHAR, max_length=100)
    schema.add_field("produit",   DataType.VARCHAR, max_length=200)
    schema.add_field("sku",       DataType.VARCHAR, max_length=64)
    schema.add_field("store_id",  DataType.VARCHAR, max_length=20)

    schema.add_field("heure_min",    DataType.INT64)
    schema.add_field("heure_max",    DataType.INT64)
    schema.add_field("jour_semaine", DataType.INT64)
    schema.add_field("updated_at",   DataType.INT64)
    schema.add_field("payload",      DataType.JSON)

    schema.add_function(Function(
        name="text_bm25",
        function_type=FunctionType.BM25,
        input_field_names=["text"],
        output_field_names=["sparse"],
    ))
    return schema


def build_index_params(client):
    idx = client.prepare_index_params()
    idx.add_index(
        field_name="dense",
        index_type="HNSW",
        metric_type="COSINE",
        params={"M": 16, "efConstruction": 200},
    )
    idx.add_index(
        field_name="sparse",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="BM25",
        params={"inverted_index_algo": "DAAT_MAXSCORE"},
    )
    return idx


def ensure_collection(client, recreate: bool = False) -> bool:
    """Crée `retail_knowledge` si absente. Retourne True si la collection est prête."""
    try:
        if recreate and client.has_collection(COLLECTION):
            client.drop_collection(COLLECTION)
            logger.info("[RAG] Collection '%s' supprimée (recreate)", COLLECTION)

        if client.has_collection(COLLECTION):
            return True

        # L'analyseur français peut être refusé par des builds Milvus sans le
        # stemmer FR : on retombe sur l'analyseur standard plutôt que de perdre
        # tout le BM25.
        for analyzer, label in ((FRENCH_ANALYZER, "french"), (STANDARD_ANALYZER, "standard")):
            try:
                client.create_collection(
                    COLLECTION,
                    schema=build_schema(client, analyzer),
                    index_params=build_index_params(client),
                )
                logger.info("[RAG] Collection '%s' créée (analyzer=%s)", COLLECTION, label)
                return True
            except Exception as e:
                logger.warning("[RAG] création avec analyzer=%s échouée: %.140s", label, str(e))

        return False
    except Exception as e:
        logger.warning("[RAG] ensure_collection: %.140s", str(e))
        return False
