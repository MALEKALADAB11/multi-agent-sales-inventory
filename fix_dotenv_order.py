"""
fix_dotenv_order.py — Corrige le chargement des .env dans main.py.
Le problème : load_dotenv avec override=True sur les sous-modules
efface DB_HOST/DB_PORT définis dans le .env racine.
Fix : charger le .env racine EN DERNIER avec override=True.
"""

import os

BASE = r"C:\Users\malek\Desktop\PFE-Backend"
MAIN = os.path.join(BASE, "main.py")

with open(MAIN, "r", encoding="utf-8") as f:
    content = f.read()

# Fix 1 : remplacer le chargement des sous-modules par override=False
# pour ne pas écraser les variables déjà définies
OLD = 'for env_dir in ("inventory-module", "sales-module"):\n    load_dotenv(os.path.join(BASE_DIR, env_dir, ".env"), override=True)'
NEW = '''# Charger d'abord les sous-modules SANS override (ne pas écraser)
for env_dir in ("inventory-module", "sales-module"):
    load_dotenv(os.path.join(BASE_DIR, env_dir, ".env"), override=False)
# Charger le .env racine EN DERNIER avec override (source de vérité)
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)'''

if OLD in content:
    content = content.replace(OLD, NEW)
    print("✓ Ordre chargement .env corrigé")
else:
    # Essayer une variante
    OLD2 = "for env_dir in (\"inventory-module\", \"sales-module\"):\n    load_dotenv(os.path.join(BASE_DIR, env_dir, \".env\"), override=True)"
    if OLD2 in content:
        content = content.replace(OLD2, NEW)
        print("✓ Ordre chargement .env corrigé (variante)")
    else:
        print("⚠ Pattern non trouvé — cherche manuellement...")
        # Chercher la ligne
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "load_dotenv" in line and "override=True" in line and "env_dir" in line:
                print(f"  Ligne {i+1}: {line}")

with open(MAIN, "w", encoding="utf-8") as f:
    f.write(content)
print("✓ main.py sauvegardé")

# Vérifier le résultat
with open(MAIN, "r", encoding="utf-8") as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if "load_dotenv" in line or ".env" in line.lower():
        if i < 50:  # seulement les premières lignes
            print(f"  L{i+1}: {line.rstrip()}")

print("\n✅ Fix terminé — relancez: uvicorn main:app --port 8000")