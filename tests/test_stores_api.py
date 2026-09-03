"""
test_stores_api.py
==================
Garde-fou sur `GET /api/v1/stores`.

L'endpoint a longtemps répondu `200 {"stores": []}` alors que la base contenait
201 boutiques : sa requête visait `FROM boutiques` sans qualifier le schéma,
or le pool de `json_service._query()` ne pose aucun `search_path` et la base est
en `"$user", public`. Le `relation "boutiques" does not exist` qui en résultait
était avalé par le `except` de `_query()`, qui renvoie `[]` sur erreur — donc
aucune 500, aucun symptôme visible côté client, juste une liste vide.

Une liste vide est ici indiscernable d'une panne : ce test échoue si elle
revient vide, ce qui rattrape aussi bien une dé-qualification du schéma qu'une
colonne renommée (`a.actif`, qui n'a jamais existé sur `sales.agents`).

    python -m pytest tests/test_stores_api.py

Marqué `db` : sans Postgres joignable les tests sont SAUTÉS, pas en échec.
"""
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytestmark = pytest.mark.db

_CONNECT_TIMEOUT_S = 5


@pytest.fixture(scope="module")
def stores() -> list[dict]:
    """Résultat de l'endpoint, ou skip si la base n'est pas joignable."""
    import psycopg2

    from app.core.config import Config

    try:
        psycopg2.connect(
            host=Config.DB_HOST, port=Config.DB_PORT, dbname=Config.DB_NAME,
            user=Config.DB_USER, password=Config.DB_PASSWORD,
            connect_timeout=_CONNECT_TIMEOUT_S,
        ).close()
    except Exception as exc:  # pragma: no cover - dépend du poste
        pytest.skip(f"PostgreSQL injoignable : {exc}")

    from app.api.stores import list_stores

    return list_stores()["stores"]


def test_liste_non_vide(stores):
    """Le symptôme du bug : 200 avec zéro boutique."""
    assert stores, (
        "GET /api/v1/stores a renvoyé une liste vide. `_query()` transforme "
        "toute erreur SQL en [] : regarder logs/errors.log, c'est probablement "
        "une table non qualifiée ou une colonne inexistante."
    )


def test_colonnes_attendues(stores):
    """Le contrat que consomme le sélecteur de boutique du dashboard."""
    attendues = {
        "store_id", "store_name", "ville", "type_boutique",
        "nb_agents", "objectif_ca",
    }
    assert attendues <= set(stores[0])


def test_boutique_par_defaut_presente(stores):
    """DEFAULT_STORE_ID doit figurer dans la liste, sinon le dashboard ouvre
    une boutique absente de son propre sélecteur."""
    from app.core.config import DEFAULT_STORE_ID

    ids = {s["store_id"] for s in stores}
    assert DEFAULT_STORE_ID in ids, f"{DEFAULT_STORE_ID} absent de {len(ids)} boutiques"
