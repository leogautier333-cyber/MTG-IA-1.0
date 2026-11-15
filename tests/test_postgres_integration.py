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
        events_before = self.storage.get_inventory_events()
        self.assertEqual(len(events_before), 2)
        backup = snapshot.export_database_snapshot()
        inventory.add_card_to_collection(
            scryfall_id="other", name="Other", set_code="tst",
            purchase_price=1, selling_price=2,
        )
        snapshot.restore_database_snapshot(backup)
        self.assertEqual(inventory.get_collection(), before)
        self.assertEqual(self.storage.get_inventory_events(), events_before)
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

    def test_migration_from_version_one_preserves_stock(self):
        card = {"scryfall_id": "test", "name": "Test", "set_code": "tst",
                "purchase_price": 1, "selling_price": 2}
        self.storage.save_collection([card])
        with self.storage._database() as connection:
            connection.execute("DROP TRIGGER inventory_history_trigger ON inventory")
            connection.execute("DROP FUNCTION record_inventory_event()")
            connection.execute("DROP TABLE inventory_events")
            connection.execute("DROP TABLE inventory_history_control")
            connection.execute("UPDATE metadata SET value = '1' WHERE key = 'schema_version'")
        self.storage._INITIALIZED_PATHS.discard(self.url)
        self.assertEqual(self.storage.get_collection(), [card])
        events = self.storage.get_inventory_events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1]["new"], card)

    def test_batch_variant_history_matches_individual_queries(self):
        self.storage.record_price_snapshot(
            "test", 4, foil=True, language="EN", observed_at="2026-01-01T10:00:00+00:00"
        )
        variants = [("test", True, "EN", "NM"), ("missing", False, "FR", "NM")]
        expected = {key: self.storage.get_variant_price_history(*key) for key in variants}
        self.assertEqual(self.storage.get_variant_price_histories_batch(variants), expected)

    def test_stream_backup_restores_multiple_batches_and_history(self):
        from database.backup import create_backup, restore_backup
        from cryptography.fernet import Fernet

        cards = [{"scryfall_id": f"card-{index}", "name": "Card", "set_code": "tst",
                  "purchase_price": 1, "selling_price": 2} for index in range(501)]
        self.storage.save_collection(cards)
        events = self.storage.get_inventory_events()
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"BACKUP_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii")}):
            path = Path(directory) / "backup.enc"
            create_backup(path)
            self.storage.save_collection([])
            counts = restore_backup(path)
        self.assertEqual(counts["inventory"], len(cards))
        self.assertEqual(self.storage.get_collection(), cards)
        self.assertEqual(self.storage.get_inventory_events(), events)

    def test_bulk_batches_and_incomplete_stream_rolls_back(self):
        from database import market_data

        cards = [{"id": f"card-{index}", "oracle_id": f"oracle-{index}", "name": "Card",
                  "set": "tst", "edhrec_rank": index + 1} for index in range(501)]
        market_data.sync_bulk_prints(iter(cards))
        with self.assertRaisesRegex(ValueError, "incomplet"):
            market_data.sync_bulk_prints(iter(cards[:1]))
        with self.storage._database() as connection:
            count = connection.execute("SELECT COUNT(*) AS count FROM bulk_prints").fetchone()["count"]
        self.assertEqual(count, 501)

    def test_analysis_lock_is_shared_between_connections(self):
        from services.phase_one_monitor import _analysis_lock
        with _analysis_lock():
            with self.assertRaisesRegex(RuntimeError, "déjà en cours"):
                with _analysis_lock():
                    self.fail("Verrou déjà détenu")
        with _analysis_lock():
            pass
