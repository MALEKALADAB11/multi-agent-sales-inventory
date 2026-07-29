"""Configuration pytest — la racine du repo est le seul chemin nécessaire."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Deux scripts de diagnostic, pas des modules pytest : tout leur corps
# s'exécute à l'import, ils exigent une DB Postgres réellement peuplée et ils
# rendent leur verdict via le code de sortie du processus. Le préfixe `test_`
# les fait ramasser par pytest, avec deux conséquences :
#   - test_inventory_pipeline.py : `sys.exit(1)` au niveau module → la session
#     entière s'arrêtait sur INTERNALERROR ;
#   - test_db_integration.py     : il ÉCRIT en base (agent_runs, stock_levels,
#     alerts) dès l'import, donc pendant la simple collecte.
# Tous deux restent lançables à la main :
#     python tests/test_inventory_pipeline.py
#     python tests/inventory/test_db_integration.py
collect_ignore = [
    "test_inventory_pipeline.py",
    "inventory/test_db_integration.py",
]
