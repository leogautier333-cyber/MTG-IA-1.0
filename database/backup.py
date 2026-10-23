import argparse
import gzip
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from database import storage


class BackupEncryptionError(RuntimeError):
    """Raised when the backup encryption key is missing or invalid."""


def _fernet():
    key = os.getenv("BACKUP_ENCRYPTION_KEY")
    if not key:
        raise BackupEncryptionError(
            "BACKUP_ENCRYPTION_KEY is required to create or restore encrypted backups. "
            "Store the Fernet key in GitHub Actions secrets and keep a secure offline copy."
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        raise BackupEncryptionError(
            "BACKUP_ENCRYPTION_KEY is invalid; expected a URL-safe Fernet key."
        ) from None


def validate_key():
    _fernet()
    return True


def create_backup_bytes(snapshot=None):
    snapshot = snapshot or storage.export_database_snapshot()
    serialized = json.dumps(
        snapshot, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    compressed = gzip.compress(serialized, mtime=0)
    return _fernet().encrypt(compressed)


def restore_backup_bytes(encrypted_bytes, allow_plaintext_legacy=False):
    if allow_plaintext_legacy and encrypted_bytes.startswith(b"\x1f\x8b"):
        compressed = encrypted_bytes
    else:
        try:
            compressed = _fernet().decrypt(encrypted_bytes)
        except InvalidToken:
            raise BackupEncryptionError(
                "Backup authentication failed; the key is incorrect or the archive was modified."
            ) from None
    try:
        snapshot = json.loads(gzip.decompress(compressed).decode("utf-8"))
    except (OSError, EOFError, UnicodeDecodeError, json.JSONDecodeError):
        raise BackupEncryptionError("Backup is not a valid compressed database snapshot.") from None
    return storage.restore_database_snapshot(snapshot)


def create_backup(destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(create_backup_bytes())
    return destination


def restore_backup(source, allow_plaintext_legacy=False):
    return restore_backup_bytes(
        Path(source).read_bytes(), allow_plaintext_legacy=allow_plaintext_legacy
    )


def main():
    parser = argparse.ArgumentParser(description="Sauvegarder ou restaurer la base Magic Trading.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-key", help="Valider BACKUP_ENCRYPTION_KEY sans afficher sa valeur.")
    create_command = commands.add_parser("create", help="Créer une sauvegarde chiffrée .json.gz.enc.")
    create_command.add_argument("path", help="Chemin du fichier de sauvegarde.")
    restore_command = commands.add_parser("restore", help="Restaurer une sauvegarde .json.gz.enc.")
    restore_command.add_argument("path", help="Fichier de sauvegarde à restaurer.")
    restore_command.add_argument(
        "--yes", action="store_true", help="Confirmer le remplacement complet sans demander de saisie."
    )
    restore_command.add_argument(
        "--allow-plaintext-legacy", action="store_true",
        help="Autoriser explicitement la restauration d’une ancienne sauvegarde gzip non chiffrée.",
    )
    arguments = parser.parse_args()

    if arguments.command == "validate-key":
        validate_key()
        print("Backup encryption key is valid.")
    elif arguments.command == "create":
        path = create_backup(arguments.path)
        print(f"Encrypted backup created: {path}")
    else:
        if not arguments.yes and input(
            "La restauration remplacera toutes les tables. Tape RESTAURER pour confirmer : "
        ) != "RESTAURER":
            raise SystemExit("Restauration annulée.")
        counts = restore_backup(
            arguments.path, allow_plaintext_legacy=arguments.allow_plaintext_legacy
        )
        print("Restauration terminée : " + ", ".join(f"{table}={count}" for table, count in counts.items()))


if __name__ == "__main__":
    main()