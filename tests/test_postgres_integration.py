"""Tests PostgreSQL réels : uniquement sur une base locale explicitement réservée aux tests."""
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit


@unittest.skipUnless(os.getenv("MTG_RUN_POSTGRES_TESTS") == "true", "PostgreSQL de test non demandé")
class PostgresIntegrationTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from database import storage

        url = os.environ.get("MTG_TEST_POSTGRES_URL", "")
        parsed = urlsplit(url)
        if (parsed.scheme not in {"postgres", "postgresql"}
                or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.path != "/mtg_test"):
            self.fail("Tests PostgreSQL refusés : URL locale de la base mtg_test obligatoire.")
        self.storage = storage
        self.url = url
        self.schema = "test_" + uuid.uuid4().hex
        self.psycopg_connect = psycopg.connect
        self.admin = self.psycopg_connect(url, autocommit=True, sslmode="disable")
        self.addCleanup(self.admin.close)
        self.admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(
            lambda: self.admin.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema))
            )
        )
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patches = (
            patch.object(storage, "DATABASE_PATH", Path(directory.name) / "legacy.sqlite3"),
            patch.object(storage, "DATABASE_URL", url),
            patch.dict(os.environ, {"DATABASE_URL": url, "MTG_DATABASE_URL": "",
                                    "DATABASE_SSLMODE": "disable"}),
            patch("psycopg.connect", side_effect=self._connect_in_schema),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        storage._INITIALIZED_PATHS.discard(url)
        self.addCleanup(storage._INITIALIZED_PATHS.discard, url)

    def _connect_in_schema(self, *args, **kwargs):
        kwargs["options"] = f"-c search_path={self.schema}"
        return self.psycopg_connect(*args, **kwargs)

    def test_schema_migration_stock_and_backup_roundtrip(self):
        from database import snapshot
        from database.backup import create_backup, restore_backup
        from cryptography.fernet import Fernet
        from services import inventory

        inventory.add_card_to_collection(
            scryfall_id="test-card", name="Test", set_code="tst",
            quantity=2, purchase_price=3, selling_price=5,
        )
        self.assertEqual(self.storage.get_metadata("schema_version"), str(self.storage.SCHEMA_VERSION))
        before = inventory.get_collection()
        backup = snapshot.export_database_snapshot()
        inventory.add_card_to_collection(
            scryfall_id="other", name="Other", set_code="tst",
            purchase_price=1, selling_price=2,
        )
        snapshot.restore_database_snapshot(backup)
        self.assertEqual(inventory.get_collection(), before)
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"BACKUP_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii")}):
            backup_path = Path(directory) / "test.enc"
            create_backup(backup_path)
            inventory.add_card_to_collection(
                scryfall_id="other", name="Other", set_code="tst", purchase_price=1, selling_price=2,
            )
            restore_backup(backup_path)
        self.assertEqual(inventory.get_collection(), before)

    def test_failed_transaction_rolls_back(self):
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with self.storage._database() as connection:
                connection.execute(
                    "INSERT INTO metadata(key, value) VALUES (?, ?)", ("test_rollback", "1")
                )
                raise RuntimeError("rollback")
        self.assertIsNone(self.storage.get_metadata("test_rollback"))

    def test_analysis_lock_is_shared_between_connections(self):
        from services.phase_one_monitor import _analysis_lock
        with _analysis_lock():
            with self.assertRaisesRegex(RuntimeError, "déjà en cours"):
                with _analysis_lock():
                    self.fail("Verrou déjà détenu")
        with _analysis_lock():
            pass
