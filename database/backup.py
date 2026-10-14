import argparse
import gzip
import json
from pathlib import Path

from database import storage


def create_backup(destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    snapshot = storage.export_database_snapshot()
    with gzip.open(destination, "wt", encoding="utf-8") as backup_file:
        json.dump(snapshot, backup_file, ensure_ascii=False, separators=(",", ":"))
    return destination


def restore_backup(source):
    with gzip.open(source, "rt", encoding="utf-8") as backup_file:
        snapshot = json.load(backup_file)
    return storage.restore_database_snapshot(snapshot)


def main():
    parser = argparse.ArgumentParser(description="Sauvegarder ou restaurer la base Magic Trading.")
    commands = parser.add_subparsers(dest="command", required=True)
    create_command = commands.add_parser("create", help="Créer une sauvegarde .json.gz.")
    create_command.add_argument("path", help="Chemin du fichier de sauvegarde.")
    restore_command = commands.add_parser("restore", help="Restaurer une sauvegarde .json.gz.")
    restore_command.add_argument("path", help="Fichier de sauvegarde à restaurer.")
    restore_command.add_argument(
        "--yes", action="store_true", help="Confirmer le remplacement complet sans demander de saisie."
    )
    arguments = parser.parse_args()

    if arguments.command == "create":
        path = create_backup(arguments.path)
        print(f"Sauvegarde créée : {path}")
    else:
        if not arguments.yes and input(
            "La restauration remplacera toutes les tables. Tape RESTAURER pour confirmer : "
        ) != "RESTAURER":
            raise SystemExit("Restauration annulée.")
        counts = restore_backup(arguments.path)
        print("Restauration terminée : " + ", ".join(f"{table}={count}" for table, count in counts.items()))


if __name__ == "__main__":
    main()