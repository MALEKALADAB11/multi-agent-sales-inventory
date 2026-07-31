"""Bouchons de modules `app.*` pour les tests de câblage hors-ligne des bancs.

POURQUOI CE MODULE

Les deux tests de `tests/evals/` vérifient la mécanique des runners d'évaluation
sans DB, sans clé API et — c'est leur intérêt en CI — sans exiger la codebase
`app/`. Ils injectent donc des modules factices dans `sys.modules` avant que le
banc n'importe ses dépendances (les deux runners importent `app.*`
paresseusement, dans le corps de `run()`, pas au chargement du module).

Fait naïvement, c'est-à-dire :

    sys.modules.setdefault("app", types.ModuleType("app"))

ça casse tout le reste de la suite. Un `ModuleType("app")` nu n'a pas de
`__path__` : ce n'est pas un paquet. Une fois posé dans `sys.modules`, il
masque le vrai paquet `app/` pour tout le processus, et le moindre
`import app.<x>` exécuté ensuite échoue sur :

    ModuleNotFoundError: No module named 'app.core'; 'app' is not a package

Fichier par fichier on ne le voit jamais (le bouchon meurt avec le processus) ;
`pytest tests` en bloc s'arrêtait sur 8 erreurs de collecte, parce que
`tests/evals/` est collecté avant `tests/inventory/` et `tests/test_*.py`.

Ce module installe donc les bouchons sous trois règles :

  1. un paquet parent qui existe réellement est importé, jamais remplacé —
     seul le module feuille est bouchonné ;
  2. `install()` rend un `StubSet` : chaque fichier de test défait ses propres
     bouchons, sans toucher à ceux d'un autre fichier ;
  3. l'installation se fait dans `setUpModule()` et se défait dans
     `tearDownModule()`, jamais au chargement du module. pytest importe TOUS
     les fichiers de test avant d'en exécuter un seul : un bouchon posé à
     l'import resterait actif pendant l'import des fichiers suivants, qui
     lieraient alors le faux `llm_factory` au lieu du vrai.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from typing import Any, Dict, List, Tuple

_MISSING = object()


class StubSet:
    """Ensemble de bouchons installés, réversible par `restore()`."""

    def __init__(self) -> None:
        # Noms ajoutés à sys.modules par nos soins, dans l'ordre d'installation.
        self._created: List[str] = []
        # Noms qui existaient déjà et que nous avons écrasés → valeur d'origine.
        self._replaced: Dict[str, types.ModuleType] = {}
        # Attributs posés sur un parent : (parent, attribut, existait, ancienne valeur).
        self._attrs: List[Tuple[str, str, bool, Any]] = []

    # ── interne ──────────────────────────────────────────────────────────────

    def _set_parent_attr(self, parent_name: str, leaf: str, value: Any) -> None:
        parent = sys.modules.get(parent_name)
        if parent is None:
            return
        old = getattr(parent, leaf, _MISSING)
        self._attrs.append((parent_name, leaf, old is not _MISSING, old))
        setattr(parent, leaf, value)

    def _ensure_package(self, name: str) -> types.ModuleType:
        """Rend le paquet `name` : le vrai s'il existe, un bouchon sinon."""
        existing = sys.modules.get(name)
        if existing is not None:
            return existing

        parent_name, _, leaf = name.rpartition(".")
        if parent_name:
            self._ensure_package(parent_name)

        # find_spec lève si un parent est lui-même absent — c'est exactement le
        # cas « pas de codebase app/ », qui doit produire un bouchon, pas une
        # erreur.
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, AttributeError, ValueError):
            spec = None
        if spec is not None:
            return importlib.import_module(name)     # vrai paquet : intact

        pkg = types.ModuleType(name)
        pkg.__path__ = []       # sans ça : « 'app' is not a package » plus tard
        sys.modules[name] = pkg
        self._created.append(name)
        if parent_name:
            self._set_parent_attr(parent_name, leaf, pkg)
        return pkg

    # ── API ──────────────────────────────────────────────────────────────────

    def install(self, stubs: Dict[str, types.ModuleType]) -> "StubSet":
        """Installe les modules feuilles de `stubs` ({nom pointé: module})."""
        for full_name, module in stubs.items():
            parent_name, _, leaf = full_name.rpartition(".")
            if parent_name:
                self._ensure_package(parent_name)

            if full_name in sys.modules:
                self._replaced.setdefault(full_name, sys.modules[full_name])
            else:
                self._created.append(full_name)
            sys.modules[full_name] = module

            if parent_name:
                self._set_parent_attr(parent_name, leaf, module)
        return self

    def restore(self) -> None:
        """Défait `install()` : sys.modules et les parents reviennent à l'initial."""
        for parent_name, leaf, existed, old in reversed(self._attrs):
            parent = sys.modules.get(parent_name)
            if parent is None:
                continue
            if existed:
                setattr(parent, leaf, old)
            else:
                try:
                    delattr(parent, leaf)
                except AttributeError:
                    pass
        self._attrs.clear()

        for name in reversed(self._created):
            sys.modules.pop(name, None)
        self._created.clear()

        for name, module in self._replaced.items():
            sys.modules[name] = module
        self._replaced.clear()


def install(stubs: Dict[str, types.ModuleType]) -> StubSet:
    """Raccourci : installe `stubs` et rend le StubSet à restaurer."""
    return StubSet().install(stubs)
