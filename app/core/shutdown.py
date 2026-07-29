"""
Coopération avec l'arrêt du processus
=====================================

POURQUOI CE MODULE

Au démarrage, `main.py` lance le préchauffage inventory en tâche de fond
(`analyze_store` → `InventoryOrchestrator.analyze_batch`). Ce run froid dure
plusieurs minutes. Si uvicorn s'arrête (Ctrl-C, `--reload`, redéploiement)
pendant ce temps, le thread du pool continue de tourner alors que CPython a
déjà commencé sa séquence de sortie.

`concurrent.futures.thread` enregistre `_python_exit` via
`threading._register_atexit` : ce hook positionne un drapeau global AVANT de
joindre les threads workers. Tout `ThreadPoolExecutor.submit()` appelé après
lève alors :

    RuntimeError: cannot schedule new futures after interpreter shutdown

C'est exactement l'erreur observée en boucle sur chaque SKU : le pipeline
crée un pool imbriqué par SKU, et tous échouaient d'un coup à l'arrêt.

USAGE

    from app.core.shutdown import is_shutting_down, is_shutdown_error

    if is_shutting_down():
        return                      # ne rien démarrer de nouveau
    try:
        fut = pool.submit(fn)
    except RuntimeError as exc:
        if not is_shutdown_error(exc):
            raise
        fn()                        # repli séquentiel dans le thread courant

`request_shutdown()` permet à `lifespan`/`shutdown_event` de signaler l'arrêt
tôt, avant même la sortie de l'interpréteur, pour que les batchs en vol
s'arrêtent proprement au lieu d'être coupés au milieu.
"""

import threading
import atexit

_shutdown_flag = threading.Event()


def request_shutdown() -> None:
    """Signale l'arrêt : les travaux de fond doivent s'arrêter dès que possible."""
    _shutdown_flag.set()


def is_shutting_down() -> bool:
    """True si l'arrêt du processus a été demandé ou a déjà commencé."""
    return _shutdown_flag.is_set()


def is_shutdown_error(exc: BaseException) -> bool:
    """
    True si `exc` est le RuntimeError levé par concurrent.futures quand on
    soumet une tâche à un pool fermé ou pendant la sortie de l'interpréteur.
    """
    if not isinstance(exc, RuntimeError):
        return False
    msg = str(exc).lower()
    return "cannot schedule new futures" in msg or "shutdown" in msg


# Le hook threading s'exécute AVANT que les threads non-daemon soient joints —
# c'est le même mécanisme que concurrent.futures, donc le drapeau est déjà
# positionné quand un worker tente une nouvelle soumission.
try:
    threading._register_atexit(request_shutdown)   # CPython >= 3.9
except AttributeError:                              # pragma: no cover
    atexit.register(request_shutdown)
