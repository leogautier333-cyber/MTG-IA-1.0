import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import phase_one_json
from services import phase_one_monitor


class PhaseOneTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.data_dir_patch = patch.object(phase_one_json, "DATA_DIR", Path(self.temporary_directory.name))
        self.data_dir_patch.start()
        self.addCleanup(self.data_dir_patch.stop)
        self.addCleanup(self.temporary_directory.cleanup)

    def test_analysis_snapshots_only_stock_and_alerts_at_ten_percent(self):
        phase_one_json.save_collection([
            {
                "scryfall_id": "stock-card", "name": "Stock Card", "set_code": "tst",
                "purchase_price": 4.0, "selling_price": 10.0, "status": "FOR_SALE",
            },
            {
                "scryfall_id": "sold-card", "name": "Sold Card", "set_code": "tst",
                "purchase_price": 4.0, "selling_price": 10.0, "status": "SOLD",
            },
        ])
        phase_one_json.record_price_snapshot(
            "stock-card", 10.0, observed_at="2026-10-22T10:00:00+00:00"
        )
        mock_response = unittest.mock.Mock()
        with patch.object(
            phase_one_monitor.scryfall, "get_cards_batch",
            return_value=[{"id": "stock-card", "prices": {"eur": "11.00"}}],
        ) as get_cards, patch.object(
            phase_one_monitor.requests, "post", return_value=mock_response
        ) as send_discord, patch.dict(
            "os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.invalid/webhook"}
        ):
            result = phase_one_monitor.analyze_stock_prices()

        self.assertEqual(get_cards.call_args.args[0], ["stock-card"])
        self.assertEqual(result["snapshots_written"], 1)
        self.assertEqual(result["alerts_created"], 1)
        self.assertEqual(result["notifications_sent"], 1)
        self.assertEqual(send_discord.call_count, 1)
        self.assertEqual(
            len(phase_one_json.get_variant_price_history("stock-card")), 2
        )
        saved_alerts = json.loads((phase_one_json.DATA_DIR / "price_alerts.json").read_text())
        self.assertEqual(saved_alerts[0]["status"], "sent")

    def test_discord_test_alert_does_not_read_or_change_inventory(self):
        mock_response = unittest.mock.Mock()
        with patch.object(phase_one_monitor.requests, "post", return_value=mock_response) as post:
            self.assertTrue(phase_one_monitor.send_test_alert(
                webhook_url="https://discord.invalid/webhook"
            ))
        post.assert_called_once()
        self.assertFalse((phase_one_json.DATA_DIR / "price_history.json").exists())

    def test_sold_cards_are_never_requested_or_snapshotted(self):
        phase_one_json.save_collection([{
            "scryfall_id": "sold-card", "name": "Sold", "set_code": "tst",
            "purchase_price": 1.0, "selling_price": 2.0, "status": "SOLD",
        }])
        with patch.object(phase_one_monitor.scryfall, "get_cards_batch") as get_cards:
            result = phase_one_monitor.analyze_stock_prices()
        get_cards.assert_not_called()
        self.assertEqual(result["snapshots_written"], 0)

    def test_variation_below_ten_percent_does_not_alert(self):
        phase_one_json.save_collection([{
            "scryfall_id": "stock-card", "name": "Stock Card", "set_code": "tst",
            "purchase_price": 4.0, "selling_price": 10.0, "status": "FOR_SALE",
        }])
        phase_one_json.record_price_snapshot(
            "stock-card", 10.0, observed_at="2026-10-22T10:00:00+00:00"
        )
        with patch.object(
            phase_one_monitor.scryfall, "get_cards_batch",
            return_value=[{"id": "stock-card", "prices": {"eur": "10.99"}}],
        ), patch.object(phase_one_monitor.requests, "post") as send_discord:
            result = phase_one_monitor.analyze_stock_prices()
        self.assertEqual(result["snapshots_written"], 1)
        self.assertEqual(result["alerts_created"], 0)
        send_discord.assert_not_called()

    def test_ten_percent_drop_creates_pending_alert_without_webhook(self):
        phase_one_json.save_collection([{
            "scryfall_id": "stock-card", "name": "Stock Card", "set_code": "tst",
            "purchase_price": 4.0, "selling_price": 10.0, "status": "FOR_SALE",
        }])
        phase_one_json.record_price_snapshot(
            "stock-card", 10.0, observed_at="2026-10-22T10:00:00+00:00"
        )
        with patch.object(
            phase_one_monitor.scryfall, "get_cards_batch",
            return_value=[{"id": "stock-card", "prices": {"eur": "9.00"}}],
        ), patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": ""}):
            result = phase_one_monitor.analyze_stock_prices()
        self.assertEqual(result["alerts_created"], 1)
        self.assertEqual(result["notifications_sent"], 0)
        saved_alerts = json.loads((phase_one_json.DATA_DIR / "price_alerts.json").read_text())
        self.assertEqual(saved_alerts[0]["status"], "pending")
        self.assertEqual(saved_alerts[0]["change_pct"], -10.0)

    def test_pending_alert_for_removed_stock_position_is_cancelled(self):
        phase_one_json.save_collection([])
        (phase_one_json.DATA_DIR / "price_alerts.json").write_text(json.dumps([{
            "id": "old-alert", "status": "pending", "scryfall_id": "removed-card",
            "foil": False, "card_name": "Removed Card", "message": "old alert",
        }]))
        with patch.object(phase_one_monitor.requests, "post") as send_discord:
            result = phase_one_monitor.analyze_stock_prices()
        send_discord.assert_not_called()
        self.assertEqual(result["notifications_sent"], 0)
        saved_alerts = json.loads((phase_one_json.DATA_DIR / "price_alerts.json").read_text())
        self.assertEqual(saved_alerts[0]["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()