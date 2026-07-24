"""
export_db.py — Export de la base pour synchroniser l'équipe sur les mêmes données.

Produit deux fichiers dans backups/ :

  ooredoo_sales_data_<date>.sql      données seules (COPY), à charger sur un
                                      schéma déjà créé par `alembic upgrade head`
  ooredoo_sales_full_<date>.sql      schéma + données, autonome (option --full)

Le schéma N'EST PAS exporté par défaut : db/migrations/versions/0001..0012 est
déjà la source de vérité versionnée dans git (cf. refonte monolithe 2026-07-07).
Exporter le DDL en parallèle créerait une seconde définition du schéma qui
divergerait dès la migration suivante. Le coéquipier fait donc :

    alembic upgrade head          -> schéma (git)
    python scripts/import_db.py   -> données (ce dump)

Tables exclues : traces d'exécution regénérées localement (agent_logs,
agent_runs, sessions, RAG queries, simulateur temps réel). Elles pèsent ~430 Mo
sur 1,3 Go et n'ont aucune valeur métier partagée. alembic_version est exclue
aussi : c'est `alembic upgrade head` qui la renseigne côté coéquipier, la
réinsérer créerait une ligne en double.

Usage :
    python scripts/export_db.py            # données seules + gzip
    python scripts/export_db.py --full     # + dump autonome schéma+données
    python scripts/export_db.py --no-gzip
"""
import argparse
import gzip
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.config import Config  # noqa: E402

BACKUP_DIR = ROOT / "backups"

# Traces d'exécution : chaque poste les regénère en tournant l'app.
EXCLUDED_TABLES = [
    "public.agent_logs",
    "public.agent_cycles",
    "public.agent_errors",
    "public.agent_sessions",
    "public.agent_memory",
    "public.app_sessions",
    "public.rag_queries",
    "inventory.agent_runs",
    "sales.transactions_rt",
    "public.alembic_version",
]


def find_pg_dump() -> str:
    """
    pg_dump doit être >= au serveur (17.10 ici), sinon il refuse de tourner.
    Le PATH Windows ne contient pas forcément le bin PostgreSQL : on retombe
    sur l'installation la plus récente trouvée sous Program Files.
    """
    found = shutil.which("pg_dump")
    if found:
        return found

    candidates = []
    for base in (r"C:\Program Files\PostgreSQL", r"C:\Program Files (x86)\PostgreSQL"):
        base_path = Path(base)
        if not base_path.exists():
            continue
        for version_dir in base_path.iterdir():
            exe = version_dir / "bin" / "pg_dump.exe"
            if exe.exists():
                try:
                    candidates.append((int(version_dir.name), exe))
                except ValueError:
                    continue
    if not candidates:
        sys.exit(
            "pg_dump introuvable. Ajoute <PostgreSQL>/bin au PATH "
            "ou installe les client tools."
        )
    return str(max(candidates)[1])


def run_dump(pg_dump: str, out: Path, extra_args: list[str]) -> None:
    cmd = [
        pg_dump,
        "-h", Config.DB_HOST,
        "-p", str(Config.DB_PORT),
        "-U", Config.DB_USER,
        "-d", Config.DB_NAME,
        "--no-owner",          # le coéquipier n'a pas forcément le rôle 'postgres'
        "--no-privileges",     # idem pour les GRANT
        "--file", str(out),
        *extra_args,
    ]
    env = {**os.environ, "PGPASSWORD": Config.DB_PASSWORD}
    print(f"  $ pg_dump ... --file {out.name}")
    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        sys.exit(f"pg_dump a échoué (code {result.returncode})")


def gzip_file(path: Path) -> Path:
    target = path.with_suffix(path.suffix + ".gz")
    with open(path, "rb") as src, gzip.open(target, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst, length=8 * 1024 * 1024)
    return target


def human(path: Path) -> str:
    size = path.stat().st_size / 1024 / 1024
    return f"{size:,.1f} Mo"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true",
                        help="ajoute un dump autonome schéma+données")
    parser.add_argument("--no-gzip", action="store_true",
                        help="garde uniquement le .sql non compressé")
    args = parser.parse_args()

    BACKUP_DIR.mkdir(exist_ok=True)
    pg_dump = find_pg_dump()
    stamp = date.today().isoformat()

    print(f"Base    : {Config.DB_NAME} @ {Config.DB_HOST}:{Config.DB_PORT}")
    print(f"pg_dump : {pg_dump}")
    print(f"Exclues : {len(EXCLUDED_TABLES)} tables de traces\n")

    exclude_args = []
    for table in EXCLUDED_TABLES:
        exclude_args += ["--exclude-table-data", table]

    produced = []

    data_sql = BACKUP_DIR / f"ooredoo_sales_data_{stamp}.sql"
    print("-- données seules --")
    run_dump(pg_dump, data_sql, [
        "--data-only",
        # Les COPY sont émis dans l'ordre alphabétique des tables, pas dans
        # l'ordre des FK : sans ça, inventory.sales_history se charge avant
        # sales.produits et viole produits_sku_fkey.
        "--disable-triggers",
        *exclude_args,
    ])
    produced.append(data_sql)

    if args.full:
        full_sql = BACKUP_DIR / f"ooredoo_sales_full_{stamp}.sql"
        print("\n-- schéma + données --")
        run_dump(pg_dump, full_sql, ["--clean", "--if-exists", *exclude_args])
        produced.append(full_sql)

    print("\n-- résultat --")
    for path in produced:
        print(f"  {path.name:<45} {human(path)}")
        if not args.no_gzip:
            gz = gzip_file(path)
            print(f"  {gz.name:<45} {human(gz)}  (à partager)")

    print(
        "\nCôté coéquipier :\n"
        "  1. createdb -U postgres ooredoo_sales\n"
        "  2. alembic upgrade head\n"
        "  3. python scripts/import_db.py backups/<fichier>.sql.gz"
    )


if __name__ == "__main__":
    main()
