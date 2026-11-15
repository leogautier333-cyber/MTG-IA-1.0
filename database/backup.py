import argparse
import base64
import gzip
import io
import json
import os
import tempfile
import zlib
from pathlib import Path

import ijson
from cryptography.fernet import Fernet, InvalidToken
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from database import snapshot


class BackupEncryptionError(RuntimeError):
    """Raised when the backup encryption key is missing or invalid."""


STREAM_HEADER = b"MTGSQL2"
NONCE_SIZE = 12
TAG_SIZE = 16


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


def create_backup_bytes(snapshot_data=None):
    snapshot_data = snapshot_data or snapshot.export_database_snapshot()
    serialized = json.dumps(
        snapshot_data, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    compressed = gzip.compress(serialized, mtime=0)
    return _fernet().encrypt(compressed)


def _stream_key():
    _fernet()
    key = base64.urlsafe_b64decode(os.environ["BACKUP_ENCRYPTION_KEY"])
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=b"magic-trading-sql-backup-v2").derive(key)


def restore_backup_bytes(encrypted_bytes, allow_plaintext_legacy=False):
    if encrypted_bytes.startswith(STREAM_HEADER):
        prefix_size = len(STREAM_HEADER) + NONCE_SIZE
        if len(encrypted_bytes) < prefix_size + TAG_SIZE:
            raise BackupEncryptionError("Backup is incomplete.")
        nonce = encrypted_bytes[len(STREAM_HEADER):prefix_size]
        ciphertext = encrypted_bytes[prefix_size:-TAG_SIZE]
        tag = encrypted_bytes[-TAG_SIZE:]
        decryptor = Cipher(algorithms.AES(_stream_key()), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(STREAM_HEADER)
        try:
            compressed = decryptor.update(ciphertext) + decryptor.finalize()
        except InvalidTag:
            raise BackupEncryptionError(
                "Backup authentication failed; the key is incorrect or the archive was modified."
            ) from None
    elif allow_plaintext_legacy and encrypted_bytes.startswith(b"\x1f\x8b"):
        compressed = encrypted_bytes
    else:
        try:
            compressed = _fernet().decrypt(encrypted_bytes)
        except InvalidToken:
            raise BackupEncryptionError(
                "Backup authentication failed; the key is incorrect or the archive was modified."
            ) from None
    try:
        snapshot_data = json.loads(gzip.decompress(compressed).decode("utf-8"))
    except (OSError, EOFError, UnicodeDecodeError, json.JSONDecodeError):
        raise BackupEncryptionError("Backup is not a valid compressed database snapshot.") from None
    return snapshot.restore_database_snapshot(snapshot_data)


def create_backup(destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    nonce = os.urandom(NONCE_SIZE)
    encryptor = Cipher(algorithms.AES(_stream_key()), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(STREAM_HEADER)
    compressor = zlib.compressobj(wbits=31)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=destination.parent,
                                         prefix=destination.name + ".", suffix=".tmp",
                                         delete=False) as output:
            temporary_path = Path(output.name)
            output.write(STREAM_HEADER + nonce)
            for chunk in snapshot.iter_database_snapshot_json():
                output.write(encryptor.update(compressor.compress(chunk)))
            output.write(encryptor.update(compressor.flush()))
            output.write(encryptor.finalize())
            output.write(encryptor.tag)
        temporary_path.replace(destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return destination


class _DecryptedReader(io.RawIOBase):
    def __init__(self, source):
        super().__init__()
        self.source = source
        prefix_size = len(STREAM_HEADER) + NONCE_SIZE
        source.seek(0, os.SEEK_END)
        size = source.tell()
        if size < prefix_size + TAG_SIZE:
            raise BackupEncryptionError("Backup is incomplete.")
        source.seek(len(STREAM_HEADER))
        nonce = source.read(NONCE_SIZE)
        source.seek(-TAG_SIZE, os.SEEK_END)
        tag = source.read(TAG_SIZE)
        source.seek(prefix_size)
        self.remaining = size - prefix_size - TAG_SIZE
        self.decryptor = Cipher(algorithms.AES(_stream_key()), modes.GCM(nonce, tag)).decryptor()
        self.decryptor.authenticate_additional_data(STREAM_HEADER)
        self.finalized = False

    def readable(self):
        return True

    def readinto(self, buffer):
        if self.remaining:
            encrypted = self.source.read(min(len(buffer), self.remaining))
            if not encrypted:
                raise BackupEncryptionError("Backup is incomplete.")
            self.remaining -= len(encrypted)
            plaintext = self.decryptor.update(encrypted)
            buffer[:len(plaintext)] = plaintext
            return len(plaintext)
        if not self.finalized:
            self.decryptor.finalize()
            self.finalized = True
        return 0


def restore_backup(source, allow_plaintext_legacy=False):
    with Path(source).open("rb") as archive:
        if archive.read(len(STREAM_HEADER)) != STREAM_HEADER:
            archive.seek(0)
            return restore_backup_bytes(archive.read(), allow_plaintext_legacy=allow_plaintext_legacy)
        try:
            with io.BufferedReader(_DecryptedReader(archive)) as verified:
                while verified.read(1024 * 1024):
                    pass
            with io.BufferedReader(_DecryptedReader(archive)) as decrypted:
                with gzip.GzipFile(fileobj=decrypted, mode="rb") as content:
                    return snapshot.restore_database_snapshot_stream(content)
        except InvalidTag:
            raise BackupEncryptionError(
                "Backup authentication failed; the key is incorrect or the archive was modified."
            ) from None
        except (OSError, EOFError, UnicodeDecodeError, ijson.JSONError):
            raise BackupEncryptionError("Backup is not a valid compressed database snapshot.") from None


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