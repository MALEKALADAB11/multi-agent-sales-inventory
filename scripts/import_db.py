"""
import_db.py — Charge un dump produit par scripts/export_db.py.

Prérequis (dump de données seules, le cas normal) :

    createdb -U postgres ooredoo_sales      # base vide
    alembic upgrade head                     # schéma 0001 -> 0012
    python scripts/import_db.py backups/ooredoo_sales_data_<date>.sql.gz

Accepte .sql et .sql.gz. Le .gz est décompressé en flux dans stdin de psql —
pas de fichier intermédiaire de plusieurs centaines de Mo sur le disque.

--force : vide d'abord les tables présentes dans le dump. Sans ça, réimporter
sur une base déjà peuplée fait échouer chaque COPY en violation de clé
primaire. Les tables de traces (exclues du dump) ne sont jamais touchées.
"""
import argparse
import gzip
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.config import Config  # noqa: E402
from scripts.export_db import EXCLUDED_TABLES  # noqa: E402


def find_psql() -> str:
    found = shutil.which("psql")
    if found:
        return found

    candidates = []
    for base in (r"C:\Program Files\PostgreSQL", r"C:\Program Files (x86)\PostgreSQL"):
        base_path = Path(base)
        if not base_path.exists():
            continue
        for version_dir in base_path.iterdir():
            exe = version_dir / "bin" / "psql.exe"
            if exe.exists():
                try:
                    candidates.append((int(version_dir.name), exe))
                except ValueError:
                    continue
    if not candidates:
        sys.exit("psql introuvable. Ajoute <PostgreSQL>/bin au PATH.")
    return str(max(candidates)[1])


def psql_env() -> dict:
    return {**os.environ, "PGPASSWORD": Config.DB_PASSWORD}


def psql_cmd(psql: str, *extra: str) -> list[str]:
    return [
        psql,
        "-h", Config.DB_HOST,
        "-p", str(Config.DB_PORT),
        "-U", Config.DB_USER,
        "-d", Config.DB_NAME,
        "-v", "ON_ERROR_STOP=1",
        *extra,
    ]


def truncate_target_tables(psql: str) -> None:
    """
    TRUNCATE ... CASCADE sur tout sauf les tables de traces et alembic_version.
    CASCADE est sûr ici : les seules tables que le dump ne recouvre pas sont
    justement celles qu'on exclut, et elles sont regénérées à l'exécution.
    """
    excluded = ", ".join(f"'{t}'" for t in EXCLUDED_TABLES)
    sql = f"""
    DO $$
    DECLARE targets text;
    BEGIN
      SELECT string_agg(format('%I.%I', schemaname, relname), ', ')
        INTO targets
        FROM pg_stat_user_tables
       WHERE schemaname || '.' || relname NOT IN ({excluded});
      IF targets IS NOT NULL THEN
        EXECUTE 'TRUNCATE TABLE ' || targets || ' RESTART IDENTITY CASCADE';
      END IF;
    END $$;
    """
    print("  vidage des tables cibles (TRUNCATE ... CASCADE)")
    result = subprocess.run(psql_cmd(psql, "-c", sql), env=psql_env())
    if result.returncode != 0:
        sys.exit("le vidage a échoué")


def load(psql: str, dump: Path) -> None:
    cmd = psql_cmd(psql)
    env = psql_env()
    print(f"  chargement de {dump.name} ...")

    if dump.suffix == ".gz":
        with gzip.open(dump, "rb") as src:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, env=env)
            shutil.copyfileobj(src, proc.stdin, length=8 * 1024 * 1024)
            proc.stdin.close()
            code = proc.wait()
    else:
        code = subprocess.run(cmd + ["-f", str(dump)], env=env).returncode

    if code != 0:
        sys.exit(f"psql a échoué (code {code})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", type=Path, help="chemin du .sql ou .sql.gz")
    parser.add_argument("--force", action="store_true",
                        help="vide les tables avant de charger")
    args = parser.parse_args()

    dump = args.dump if args.dump.is_absolute() else ROOT / args.dump
    if not dump.exists():
        sys.exit(f"fichier introuvable : {dump}")

    psql = find_psql()
    print(f"Base : {Config.DB_NAME} @ {Config.DB_HOST}:{Config.DB_PORT}")

    if args.force:
        truncate_target_tables(psql)

    load(psql, dump)
    print("\nImport terminé. Vérification :")
    subprocess.run(psql_cmd(psql, "-c", """
        SELECT 'sales.transactions' AS t, count(*) FROM sales.transactions
        UNION ALL SELECT 'inventory.sales_history', count(*) FROM inventory.sales_history
        UNION ALL SELECT 'inventory.stock_history', count(*) FROM inventory.stock_history
        UNION ALL SELECT 'sales.produits', count(*) FROM sales.produits;
    """), env=psql_env())


if __name__ == "__main__":
    main()
