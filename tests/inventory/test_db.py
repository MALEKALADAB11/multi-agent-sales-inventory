"""
test_db.py
==========
Vérifie le socle asynchrone d'InventoryRepo contre la vraie base : le pool
asyncpg s'ouvre, les lectures de référence répondent, et leurs lignes ont la
forme attendue par le reste du code.

    python -m pytest tests/inventory/test_db.py
    python tests/inventory/test_db.py      # smoke verbeux, hors pytest

Ce fichier contenait auparavant une unique coroutine `async def test()` qui ne
faisait qu'imprimer, sans aucune assertion — et que pytest-asyncio en mode
`strict` sautait silencieusement (voir asyncio_mode dans pytest.ini). Elle n'a
donc jamais rien vérifié en CI. Les assertions ci-dessous portent sur ce dont
le reste du code dépend réellement : la présence des clés, pas les volumes.

Marqué `db` : sans Postgres joignable, les tests sont SAUTÉS, pas en échec —
un poste sans base ne doit pas faire échouer la suite.
"""
import asyncio
import sys
from pathlib import Path

import pytest

# Sous pytest, tests/conftest.py met déjà la racine du repo sur sys.path. Lancé
# directement (`python tests/inventory/test_db.py`), Python n'ajoute que
# tests/inventory/ — sans ce bootstrap, `app` reste introuvable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.inventory.repositories.inventory_repo import InventoryRepo  # noqa: E402

pytestmark = pytest.mark.db


# Borne le temps perdu quand il n'y a pas de base : asyncpg attend 60 s par
# défaut, soit ~84 s pour les 4 tests sur un poste sans Postgres. Le premier
# échec est mémorisé pour que les suivants soient sautés sans réessayer.
_CONNECT_TIMEOUT_S = 5
_connect_failure: str | None = None


@pytest.fixture
async def repo():
    """Pool asyncpg ouvert pour un test, refermé ensuite. Skip si DB absente."""
    global _connect_failure
    if _connect_failure:
        pytest.skip(_connect_failure)

    r = InventoryRepo()
    try:
        await asyncio.wait_for(r.connect(), timeout=_CONNECT_TIMEOUT_S)
    except Exception as exc:            # asyncpg/OSError/TimeoutError selon la panne
        detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        _connect_failure = f"Postgres injoignable — tests DB sautés ({detail})"
        pytest.skip(_connect_failure)
    try:
        yield r
    finally:
        await r.close()


async def test_le_pool_est_ouvert_et_repond(repo):
    assert repo.pool is not None
    async with repo.pool.acquire() as conn:
        assert await conn.fetchval("SELECT 1") == 1


async def test_products_expose_les_colonnes_attendues(repo):
    products = await repo.get_all_products()
    assert isinstance(products, list)
    if not products:
        pytest.skip("inventory.products vide — lancer db/seeds/run_all_seeds.py")
    # Les clés dont dépendent l'agent d'analyse et _to_inventory_item.
    for key in ("sku", "product_name", "lead_time_days", "moq"):
        assert key in products[0], f"colonne manquante dans inventory.products : {key}"


async def test_stores_ne_renvoie_que_des_boutiques_actives(repo):
    stores = await repo.get_all_stores()
    assert isinstance(stores, list)
    if not stores:
        pytest.skip("inventory.stores vide — lancer db/seeds/run_all_seeds.py")
    for key in ("store_id", "store_name"):
        assert key in stores[0], f"colonne manquante dans inventory.stores : {key}"
    # get_all_stores filtre sur active = TRUE : le vérifier ici évite qu'un
    # changement de requête ne remette des boutiques fermées dans le dashboard.
    assert all(s.get("active", True) for s in stores)


async def test_les_files_pending_repondent_sans_erreur(repo):
    """Alertes, recommandations et promotions : ces lectures alimentent le
    dashboard et le bus d'alertes. On vérifie le contrat (liste de dicts),
    pas le contenu — il dépend de l'état courant de la base."""
    alerts = await repo.get_pending_alerts()
    recs = await repo.get_pending_recommendations()
    promos = await repo.get_active_promotions()

    for name, rows in (("alerts", alerts), ("recommendations", recs),
                       ("promotions", promos)):
        assert isinstance(rows, list), f"{name} : liste attendue, reçu {type(rows)}"
        assert all(isinstance(r, dict) for r in rows), f"{name} : lignes non converties en dict"


# ── Smoke verbeux hors pytest (usage historique du fichier) ──────────────────

async def _smoke():
    r = InventoryRepo()
    await r.connect()
    print("\n── Products ─────────────────────────────")
    print(f"  Count: {len(await r.get_all_products())}")
    print("\n── Stores ───────────────────────────────")
    print(f"  Count: {len(await r.get_all_stores())}")
    print("\n── Pending alerts ───────────────────────")
    print(f"  Count: {len(await r.get_pending_alerts())}")
    print("\n── Pending recommendations ──────────────")
    print(f"  Count: {len(await r.get_pending_recommendations())}")
    print("\n── Active promotions (today) ─────────────")
    print(f"  Count: {len(await r.get_active_promotions())}")
    await r.close()
    print("\n✓ All OK\n")


if __name__ == "__main__":
    asyncio.run(_smoke())
