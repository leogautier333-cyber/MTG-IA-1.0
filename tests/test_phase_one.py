import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from database import snapshot as database_snapshot, storage
from services import inventory
from services import phase_one_monitor


def _card(scryfall_id="stock-card", status="FOR_SALE", **extra):
    return {
        "scryfall_id": scryfall_id, "name": "Stock Card", "set_code": "tst",
        "purchase_price": 4.0, "selling_price": 10.0, "status": status, **extra,
    }


class PhaseOneTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        for patcher in (
            patch.object(storage, "DATABASE_PATH", Path(self.temporary_directory.name) / "test.sqlite3"),
            patch.object(storage, "DATABASE_URL", None),
            patch.dict(os.environ, {"DATABASE_URL": "", "MTG_DATABASE_URL": ""}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_analysis_snapshots_only_stock_and_alerts_at_ten_percent(self):
        inventory.save_collection([_card(), _card("sold-card", "SOLD")])
        inventory.record_price_snapshot(
            "stock-card", 10.0, observed_at="2026-10-22T10:00:00+00:00"
        )
        with patch.object(
            phase_one_monitor.scryfall, "get_cards_batch",
            return_value=[{"id": "stock-card", "prices": {"eur": "11.00"}}],
        ) as get_cards, patch.object(
            phase_one_monitor.requests, "post", return_value=Mock()
        ) as send_discord, patch.dict(
            "os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.invalid/webhook"}
        ):
            result = phase_one_monitor.analyze_stock_prices()

        self.assertEqual(get_cards.call_args.args[0], ["stock-card"])
        self.assertEqual(result["snapshots_written"], 1)
        self.assertEqual(result["alerts_created"], 1)
        self.assertEqual(result["notifications_sent"], 1)
        self.assertEqual(send_discord.call_count, 1)
        self.assertEqual(len(inventory.get_variant_price_history("stock-card")), 2)
        self.assertEqual(inventory.get_price_alerts()[0]["status"], "sent")

    def test_discord_test_alert_does_not_read_or_change_inventory(self):
        with patch.object(phase_one_monitor.requests, "post", return_value=Mock()) as post:
            self.assertTrue(phase_one_monitor.send_test_alert(
                webhook_url="https://discord.invalid/webhook"
            ))
        post.assert_called_once()
        self.assertEqual(inventory.get_price_history(), {})

    def test_sold_cards_are_never_requested_or_snapshotted(self):
        inventory.save_collection([_card("sold-card", "SOLD")])
        with patch.object(phase_one_monitor.scryfall, "get_cards_batch") as get_cards:
            result = phase_one_monitor.analyze_stock_prices()
        get_cards.assert_not_called()
        self.assertEqual(result["snapshots_written"], 0)

    def test_variation_below_ten_percent_does_not_alert(self):
        inventory.save_collection([_card()])
        inventory.record_price_snapshot(
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
        inventory.save_collection([_card()])
        inventory.record_price_snapshot(
            "stock-card", 10.0, observed_at="2026-10-22T10:00:00+00:00"
        )
        with patch.object(
            phase_one_monitor.scryfall, "get_cards_batch",
            return_value=[{"id": "stock-card", "prices": {"eur": "9.00"}}],
        ), patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": ""}):
            result = phase_one_monitor.analyze_stock_prices()
        self.assertEqual(result["alerts_created"], 1)
        self.assertEqual(result["notifications_sent"], 0)
        saved_alerts = inventory.get_price_alerts()
        self.assertEqual(saved_alerts[0]["status"], "pending")
        self.assertEqual(saved_alerts[0]["change_pct"], -10.0)

    def test_pending_alert_for_removed_stock_position_is_cancelled(self):
        inventory.save_collection([])
        inventory.save_price_alerts([{
            "id": "old-alert", "status": "pending", "scryfall_id": "removed-card",
            "foil": False, "card_name": "Removed Card", "message": "old alert",
        }])
        with patch.object(phase_one_monitor.requests, "post") as send_discord:
            result = phase_one_monitor.analyze_stock_prices()
        send_discord.assert_not_called()
        self.assertEqual(result["notifications_sent"], 0)
        self.assertEqual(inventory.get_price_alerts()[0]["status"], "cancelled")

    def test_add_and_sell_card_update_only_the_matching_row(self):
        inventory.save_collection([_card("other-card")])
        self.assertTrue(inventory.add_card_to_collection(
            scryfall_id="new-card", name="New", set_code="tst",
            purchase_price=2, selling_price=5,
        ))
        self.assertTrue(inventory.mark_card_as_sold("new-card", 6.5))
        self.assertFalse(inventory.mark_card_as_sold("new-card", 6.5))
        by_id = {card["scryfall_id"]: card for card in inventory.get_collection()}
        self.assertEqual(by_id["new-card"]["status"], "SOLD")
        self.assertEqual(by_id["new-card"]["actual_sale_price"], 6.5)
        self.assertEqual(by_id["other-card"]["status"], "FOR_SALE")

    def test_restore_accepts_backups_created_before_price_alerts(self):
        inventory.save_collection([_card()])
        snapshot = database_snapshot.export_database_snapshot()
        snapshot["tables"].pop("price_alerts")
        database_snapshot.restore_database_snapshot(snapshot)
        self.assertEqual(len(inventory.get_collection()), 1)


if __name__ == "__main__":
    unittest.main()
