import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from database import storage
from database.backup import (
    BackupEncryptionError,
    create_backup,
    restore_backup,
)
from database.healthcheck import check_database


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "test.sqlite3"
        self.path_patch = patch.object(storage, "DATABASE_PATH", self.database_path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.backup_key_patch = patch.dict(
            os.environ, {"BACKUP_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii")}
        )
        self.backup_key_patch.start()
        self.addCleanup(self.backup_key_patch.stop)
        self.addCleanup(self.temporary_directory.cleanup)

    def test_healthcheck_initializes_every_required_table(self):
        result = check_database()
        self.assertEqual(result["backend"], "sqlite")
        self.assertEqual(result["table_count"], len(storage.REQUIRED_TABLES))

    def test_legacy_json_is_migrated_and_daily_prices_are_upserted(self):
        legacy_data = {
            "collection.json": [{
                "scryfall_id": "card-1", "name": "Card", "set_code": "set1",
                "purchase_price": 1.0, "selling_price": 2.0,
            }],
            "watchlist.json": [{"scryfall_id": "card-2", "name": "Watched"}],
            "price_history.json": {
                "card-1": {"2026-10-13_12h": {"eur": 1.25, "eur_foil": 4.0}}
            },
            "nexus_market_state.json": {
                "cards": {"card-2": [{"observed_at": "2026-10-12T12:00:00+00:00", "price_eur": 3.5}]}
            },
            "ai_predictions.json": [{
                "prediction_id": "legacy-buy", "category": "BUY_SIGNAL",
                "scryfall_id": "card-2", "timestamp": "2026-10-12T12:00:00+00:00",
                "reference_price": 3.5,
            }],
        }
        legacy_history_path = self.database_path.parent / "price_history.json"
        legacy_history_path.write_text("{}", encoding="utf-8")
        with patch.object(storage, "_read_json", side_effect=lambda path, default: legacy_data.get(path.name, default)):
            storage.initialize_database()

        self.assertEqual(len(storage.get_collection()), 1)
        self.assertEqual(len(storage.get_watchlist()), 1)
        self.assertEqual(storage.get_price_history()["card-1"]["2026-10-13"]["eur"], 1.25)
        self.assertEqual(storage.get_price_history()["card-2"]["2026-10-12"]["eur"], 3.5)
        self.assertEqual(storage.get_alerts_for_evaluation()[0]["alert_id"], "legacy-buy")

        legacy_data["price_history.json"]["card-1"]["2026-10-15_12h"] = {"eur": 1.9}
        legacy_history_path.write_text('{"updated": true}', encoding="utf-8")
        storage._INITIALIZED_PATHS.discard(str(self.database_path.resolve()))
        with patch.object(storage, "_read_json", side_effect=lambda path, default: legacy_data.get(path.name, default)):
            storage.initialize_database()
        self.assertEqual(storage.get_price_history()["card-1"]["2026-10-15"]["eur"], 1.9)

        storage.record_price_snapshot("card-1", 1.5, observed_at="2026-10-14T08:00:00+00:00")
        storage.record_price_snapshot("card-1", 1.75, observed_at="2026-10-14T20:00:00+00:00")
        storage.record_price_snapshot(
            "card-1", 5.0, foil=True, language="EN", condition="LP",
            observed_at="2026-10-14T20:00:00+00:00",
        )

        history = storage.get_price_history()["card-1"]
        self.assertEqual(history["2026-10-14"]["eur"], 1.75)
        self.assertEqual(history["2026-10-14"]["eur_foil"], 5.0)
        self.assertEqual(len(storage.get_variant_price_history("card-1")), 3)
        self.assertEqual(
            len(storage.get_variant_price_history("card-1", foil=True, language="EN", condition="LP")),
            1,
        )

    def test_bulk_diff_detects_reprints_and_duel_legality_changes(self):
        initial = storage.sync_bulk_prints([
            {
                "id": "old-print", "oracle_id": "oracle-1", "name": "Card",
                "set": "old", "legalities": {"duel": "legal"},
            }
        ], synced_at="2026-10-13T00:00:00+00:00")
        self.assertTrue(initial["baseline"])

        result = storage.sync_bulk_prints([
            {
                "id": "old-print", "oracle_id": "oracle-1", "name": "Card",
                "set": "old", "legalities": {"duel": "not_legal"},
            },
            {
                "id": "new-print", "oracle_id": "oracle-1", "name": "Card",
                "set": "new", "legalities": {"duel": "legal"},
            },
        ], synced_at="2026-10-14T00:00:00+00:00")

        self.assertEqual(len(result["reprints"]), 1)
        self.assertEqual(
            result["legality_changes"][0]["transitions"]["duel"],
            {"from": "legal", "to": "not_legal"},
        )

    def test_compressed_backup_restores_all_application_data(self):
        storage.save_collection([{
            "scryfall_id": "card-1", "name": "Card", "set_code": "set1",
            "purchase_price": 1.0, "selling_price": 2.0,
        }])
        storage.save_watchlist([{"scryfall_id": "card-2", "name": "Watched"}])
        storage.record_price_snapshot(
            "card-1", 2.5, observed_at="2026-10-14T12:00:00+00:00"
        )
        storage.sync_bulk_prints([{
            "id": "card-1", "oracle_id": "oracle-1", "name": "Card",
            "set": "set1", "released_at": "2020-01-01", "legalities": {},
            "edhrec_rank": 1200,
        }], synced_at="2026-10-14T12:00:00+00:00")
        storage.record_source_health("scryfall", True, "ok")
        storage.record_alert("alert-1", "BUY_SIGNAL", "card-1", "2026-10-14T00:00:00+00:00", {
            "reference_price": 2.5,
        })
        backup_path = Path(self.temporary_directory.name) / "database.json.gz"
        create_backup(backup_path)

        storage.DATABASE_PATH = Path(self.temporary_directory.name) / "restored.sqlite3"
        counts = restore_backup(backup_path)

        self.assertEqual(counts["inventory"], 1)
        self.assertEqual(counts["watchlist"], 1)
        self.assertEqual(len(storage.get_collection()), 1)
        self.assertEqual(len(storage.get_watchlist()), 1)
        self.assertEqual(storage.get_price_history()["card-1"]["2026-10-14"]["eur"], 2.5)
        self.assertEqual(storage.get_edhrec_history("card-1")[-1]["edhrec_rank"], 1200)
        self.assertTrue(storage.get_source_health()["scryfall"]["succeeded"])
        self.assertEqual(storage.get_alerts_for_evaluation()[0]["alert_id"], "alert-1")

    def test_backup_requires_key_and_ciphertext_hides_card_data(self):
        storage.save_collection([{
            "scryfall_id": "private-card-id", "name": "Private Card",
            "set_code": "secret-set", "purchase_price": 5.0, "selling_price": 8.0,
        }])
        backup_path = Path(self.temporary_directory.name) / "private.json.gz.enc"
        create_backup(backup_path)
        ciphertext = backup_path.read_bytes()
        self.assertNotIn(b"Private Card", ciphertext)
        self.assertNotIn(b"private-card-id", ciphertext)

        with patch.dict(os.environ, {"BACKUP_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii")}):
            with self.assertRaisesRegex(BackupEncryptionError, "authentication failed"):
                restore_backup(backup_path)

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(BackupEncryptionError, "BACKUP_ENCRYPTION_KEY is required"):
                create_backup(Path(self.temporary_directory.name) / "missing-key.enc")


if __name__ == "__main__":
    unittest.main()