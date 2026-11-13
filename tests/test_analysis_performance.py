import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import scryfall

from database import storage
from services import inventory, phase_one_monitor


class AnalysisPerformanceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for patcher in (
            patch.object(storage, "DATABASE_PATH", Path(directory.name) / "test.sqlite3"),
            patch.object(storage, "DATABASE_URL", None),
            patch.dict(os.environ, {"DATABASE_URL": "", "MTG_DATABASE_URL": "",
                                    "GITHUB_ACTIONS": "", "DISCORD_WEBHOOK_URL": "https://discord.invalid/test"}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_analysis_90_positions_uses_bounded_connections(self):
        cards = [{"scryfall_id": f"card-{i}", "name": f"Carte {i}", "set_code": "tst",
                  "purchase_price": 0, "selling_price": 10, "status": "FOR_SALE"}
                 for i in range(90)]
        inventory.save_collection(cards)
        for card in cards:
            inventory.record_price_snapshot(
                card["scryfall_id"], 10, observed_at="2026-10-01T10:00:00+00:00"
            )
        response = [{"id": card["scryfall_id"], "prices": {"eur": "11"}, "edhrec_rank": 1000}
                    for card in cards]
        original_connect = storage._connect
        connections = []

        def delayed_connect():
            connections.append(1)
            time.sleep(0.003)
            return original_connect()

        with patch.object(storage, "_connect", side_effect=delayed_connect), \
             patch.object(phase_one_monitor.scryfall, "get_cards_batch", return_value=response), \
             patch.object(phase_one_monitor.requests, "post", return_value=Mock()) as post:
            start = time.monotonic()
            result = phase_one_monitor.analyze_stock_prices()
            elapsed = time.monotonic() - start
        print(f"90 positions: {len(connections)} connexions SQL, {elapsed:.3f}s (latence simulée)")
        self.assertEqual(result["snapshots_written"], 90)
        self.assertEqual(result["alerts_created"], 90)
        self.assertEqual(result["notifications_sent"], 90)
        post.assert_called_once()
        self.assertLessEqual(len(connections), 10)

    def test_latest_same_day_and_foil_histories_are_compared_separately(self):
        from datetime import datetime, timezone

        day = datetime.now(timezone.utc).date().isoformat()
        cards = [
            {"scryfall_id": "same", "name": "Carte", "set_code": "tst",
             "purchase_price": 0, "selling_price": 1, "status": "FOR_SALE", "foil": foil}
            for foil in (False, True)
        ]
        inventory.save_collection(cards)
        inventory.record_price_snapshot("same", 10, observed_at=f"{day}T00:00:00+00:00")
        inventory.record_price_snapshot("same", 20, foil=True, observed_at=f"{day}T00:00:00+00:00")
        inventory.record_price_snapshot("same", 99, language="EN",
                                        observed_at=f"{day}T00:00:00+00:00")
        with patch.object(phase_one_monitor.scryfall, "get_cards_batch", return_value=[
            {"id": "same", "prices": {"eur": "11", "eur_foil": "18"}, "edhrec_rank": 1000}
        ]), patch.object(phase_one_monitor.requests, "post", return_value=Mock()):
            result = phase_one_monitor.analyze_stock_prices()
        self.assertEqual(result["snapshots_written"], 2)
        alerts = {item["foil"]: item for item in inventory.get_price_alerts()}
        self.assertEqual(alerts[False]["previous_price"], 10)
        self.assertEqual(alerts[False]["trend"], "UP")
        self.assertEqual(alerts[True]["previous_price"], 20)
        self.assertEqual(alerts[True]["trend"], "DOWN")
        self.assertEqual(len(inventory.get_variant_price_history("same")), 1)
        self.assertEqual(inventory.get_variant_price_history("same", language="EN")[0]["price_eur"], 99)

    def test_failed_write_rolls_back_snapshots_ranks_and_alerts(self):
        from database import market_data

        inventory.save_collection([{"scryfall_id": "x", "name": "Carte", "set_code": "tst",
                                    "purchase_price": 0, "selling_price": 1, "status": "FOR_SALE"}])
        inventory.record_price_snapshot("x", 10, observed_at="2026-10-01T10:00:00+00:00")
        with patch.object(phase_one_monitor.scryfall, "get_cards_batch", return_value=[
            {"id": "x", "prices": {"eur": "11"}, "edhrec_rank": 1000}
        ]), patch.object(storage, "_upsert_price_alerts", side_effect=RuntimeError("test failure")), \
             patch.object(phase_one_monitor.requests, "post") as post:
            with self.assertRaisesRegex(RuntimeError, "test failure"):
                phase_one_monitor.analyze_stock_prices()
        post.assert_not_called()
        self.assertEqual([x["price_eur"] for x in inventory.get_variant_price_history("x")], [10])
        self.assertEqual(market_data.get_edhrec_history("x"), [])
        self.assertEqual(inventory.get_price_alerts(), [])

    def test_more_than_500_cards_queries_all_positions(self):
        with storage._database() as connection:
            records = [(f"card-{index}", False, index + 1) for index in range(501)]
            storage._record_stock_snapshots(connection, records, "2026-10-01T10:00:00+00:00")
            latest = storage._get_latest_stock_snapshots(
                connection, [f"card-{index}" for index in range(501)]
            )
        self.assertEqual(len(latest), 501)
        self.assertEqual(latest[("card-500", False)]["price_eur"], 501)

    def test_scryfall_batch_timeout_is_explicit(self):
        with patch.object(scryfall.time, "sleep"), \
             patch.object(scryfall.requests, "post", side_effect=requests.Timeout("test")) as post:
            with self.assertRaises(requests.Timeout):
                scryfall.get_cards_batch(["card"])
        self.assertEqual(post.call_args.kwargs["timeout"], 20)


if __name__ == "__main__":
    unittest.main()
