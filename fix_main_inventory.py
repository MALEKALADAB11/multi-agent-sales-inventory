"""
fix_main_inventory.py — Corrige 3 problèmes dans main.py et inventory-module :
  1. _live_stock non défini → ajouter initialisation globale
  2. SyncInventoryRepo port 5433 → 5432
  3. STORE-001 → I63 dans inventory settings
"""

import os
import re

BASE = r"C:\Users\malek\Desktop\PFE-Backend"


def fix_file(path: str, replacements: list[tuple[str, str]], label: str):
    full = os.path.join(BASE, path)
    if not os.path.exists(full):
        print(f"  ⚠ Fichier non trouvé: {full}")
        return

    with open(full, "r", encoding="utf-8") as f:
        content = f.read()

    original = content
    for old, new in replacements:
        if old in content:
            content = content.replace(old, new)
            print(f"  ✓ {label}: remplacement effectué")
        else:
            print(f"  ⚠ {label}: pattern non trouvé — '{old[:60]}'")

    if content != original:
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  💾 {full} sauvegardé")
    else:
        print(f"  = {label}: aucun changement")


# ════════════════════════════════════════════════════════════
# 1. main.py — Ajouter _live_stock: Dict[str, int] = {}
#    juste après la déclaration de _active_stores
# ════════════════════════════════════════════════════════════
fix_file("main.py", [
    (
        "_active_stores: set[str] = set()",
        "_active_stores: set[str] = set()\n_live_stock: Dict[str, int] = {}  # stock live en mémoire"
    ),
], "main.py _live_stock")

# ════════════════════════════════════════════════════════════
# 2. inventory-module/db/repositories/inventory_repo.py
#    port 5433 → 5432
# ════════════════════════════════════════════════════════════
for repo_path in [
    r"inventory-module\db\repositories\inventory_repo.py",
    r"inventory-module\db\stock_simulator.py",
    r"inventory-module\config\settings.py",
    r"inventory-module\.env",
]:
    fix_file(repo_path, [
        ("5433", "5432"),
        ("asc_db",       "ooredoo_sales"),
        ("asc_user",     "postgres"),
        ("asc_password", "admin"),
        ("DOCKER_DB_HOST", "DB_HOST"),
        ("DOCKER_DB_PORT", "DB_PORT"),
        ("DOCKER_DB_NAME", "DB_NAME"),
        ("DOCKER_DB_USER", "DB_USER"),
        ("DOCKER_DB_PASSWORD", "DB_PASSWORD"),
    ], f"inventory {repo_path}")

# ════════════════════════════════════════════════════════════
# 3. Corriger STORE-001 → I63 dans inventory tools
# ════════════════════════════════════════════════════════════
for tool_path in [
    r"inventory-module\src\tools\internal\stock_tools.py",
    r"inventory-module\db\stock_simulator.py",
]:
    fix_file(tool_path, [
        ('"STORE-001"', '"I63"'),
        ("'STORE-001'", "'I63'"),
        ("STORE-001",   "I63"),
    ], f"STORE-001→I63 {tool_path}")

# ════════════════════════════════════════════════════════════
# 4. Corriger le schema inventory dans inventory_repo.py
#    utiliser inventory.stock_levels au lieu de inv.stock_levels
# ════════════════════════════════════════════════════════════
for repo_path in [
    r"inventory-module\db\repositories\inventory_repo.py",
    r"inventory-module\db\stock_simulator.py",
]:
    fix_file(repo_path, [
        ("inv.stock_levels",       "inventory.stock_levels"),
        ("inv.stock_movements",    "inventory.stock_movements"),
        ("inv.stock_alerts",       "inventory.stock_alerts"),
        ('"inv"',                  '"inventory"'),
        ("schema = 'inv'",         "schema = 'inventory'"),
        ("schema='inv'",           "schema='inventory'"),
        ("SET search_path TO inv", "SET search_path TO inventory"),
    ], f"schema inv→inventory {repo_path}")

print("\n✅ Fix terminé — relancez: uvicorn main:app --port 8000")