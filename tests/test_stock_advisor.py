import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from database import market_data, storage
from services import inventory, phase_one_monitor, stock_advisor


class AdvisorRulesTests(unittest.TestCase):
    def test_healthy_rise_when_popularity_grows(self):
        advice = stock_advisor.advise(12.0, popularity_delta=15.0)
        self.assertEqual(advice["kind"], "HEALTHY_RISE")
        self.assertIn("Conserver", advice["verdict"])

    def test_speculative_bubble_when_price_jumps_without_popularity(self):
        for popularity in (None, 0.0, -5.0):
            advice = stock_advisor.advise(35.0, popularity_delta=popularity)
            self.assertEqual(advice["kind"], "SPECULATIVE_BUBBLE")
            self.assertIn("Vente conseillée", advice["verdict"])

    def test_reprint_event_overrides_price_reading(self):
        advice = stock_advisor.advise(-12.0, None, [{"category": "SPOILER_REPRINT"}])
        self.assertEqual(advice["kind"], "REPRINT_DROP")
        rising = stock_advisor.advise(12.0, 20.0, [{"event_type": "REPRINT_RUMOR"}])
        self.assertEqual(rising["kind"], "REPRINT_RISK")

    def test_drop_with_growing_popularity_is_temporary(self):
        self.assertEqual(stock_advisor.advise(-11.0, 10.0)["kind"], "TEMPORARY_DROP")
        self.assertEqual(stock_advisor.advise(-11.0, None)["kind"], "UNEXPLAINED_DROP")

    def test_popularity_badge(self):
        self.assertTrue(stock_advisor.popularity_badge(12).startswith("🟢"))
        self.assertTrue(stock_advisor.popularity_badge(-5).startswith("🔴"))
        self.assertTrue(stock_advisor.popularity_badge(None).startswith("⚪"))


class AdvisorIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for patcher in (
            patch.object(storage, "DATABASE_PATH", Path(directory.name) / "test.sqlite3"),
            patch.object(storage, "DATABASE_URL", None),
            patch.dict(os.environ, {"DATABASE_URL": "", "MTG_DATABASE_URL": ""}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.card = {
            "scryfall_id": "stock-card", "name": "Stock Card", "set_code": "tst", "quantity": 3,
            "purchase_price": 4.0, "selling_price": 10.0, "status": "FOR_SALE",
        }

    def test_popularity_delta_uses_rank_history_within_30_days(self):
        market_data.record_demand_snapshot("stock-card", 1000, "2026-10-10")
        market_data.record_demand_snapshot("stock-card", 800, "2026-10-27")
        delta = stock_advisor.popularity_delta_pct("stock-card", today=date(2026, 10, 28))
        self.assertAlmostEqual(delta, 20.0)
        self.assertIsNone(stock_advisor.popularity_delta_pct("unknown", today=date(2026, 10, 28)))

    def test_price_alert_embed_contains_context_and_verdict(self):
        inventory.save_collection([self.card])
        inventory.record_price_snapshot("stock-card", 10.0, observed_at="2026-10-22T10:00:00+00:00")
        market_data.record_demand_snapshot("stock-card", 1000, "2026-10-20")
        card_data = {"id": "stock-card", "prices": {"eur": "11.50"}, "edhrec_rank": 700}
        with patch.object(phase_one_monitor.scryfall, "get_cards_batch", return_value=[card_data]), \
             patch.object(phase_one_monitor.requests, "post", return_value=Mock()) as post, \
             patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.invalid/webhook"}):
            phase_one_monitor.analyze_stock_prices()

        embed = post.call_args.kwargs["json"]["embeds"][0]
        fields = {field["name"]: field["value"] for field in embed["fields"]}
        self.assertEqual(fields["Quantité"], "3")
        self.assertIn("10.00 € ➡️ 11.50 €", fields["Prix"])
        self.assertIn("Contexte IA", fields)
        self.assertIn("Verdict", fields)
        self.assertEqual(embed["color"], phase_one_monitor.COLOR_UP)

    def test_event_scan_is_limited_to_stock_and_deduplicated(self):
        inventory.save_collection([self.card, {**self.card, "scryfall_id": "sold", "status": "SOLD"}])
        spoiler_alert = {
            "id": "spoiler:x:stock-card", "category": "SPOILER_REPRINT", "card_name": "Stock Card",
            "scryfall_id": "stock-card", "message": "Réimpression détectée.",
        }
        scan = {"alerts": [spoiler_alert], "events": [], "errors": []}
        with patch.object(
            phase_one_monitor.spoiler_watcher, "scan_spoilers_and_trends", return_value=scan
        ) as scan_mock:
            first = phase_one_monitor.scan_stock_events()
            phase_one_monitor.scan_stock_events()

        tracked = scan_mock.call_args.args[1]
        self.assertEqual([card["scryfall_id"] for card in tracked], ["stock-card"])
        self.assertEqual(len(first), 1)
        self.assertEqual(len(inventory.get_price_alerts()), 1)
        self.assertIn("Vendre", inventory.get_price_alerts()[0]["verdict"].title())

    def test_pending_event_alert_is_delivered_once(self):
        inventory.save_collection([self.card])
        inventory.save_price_alerts([{
            "id": "event-1", "kind": "SPOILER_REPRINT", "status": "pending", "scryfall_id": "stock-card",
            "foil": False, "card_name": "Stock Card", "message": "Réimpression.", "verdict": "Vendre.",
        }])
        with patch.object(phase_one_monitor.requests, "post", return_value=Mock()) as post, \
             patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.invalid/webhook"}):
            phase_one_monitor._deliver_pending_alerts({("stock-card", False)})
            phase_one_monitor._deliver_pending_alerts({("stock-card", False)})
        self.assertEqual(post.call_count, 1)
        self.assertEqual(inventory.get_price_alerts()[0]["status"], "sent")


if __name__ == "__main__":
    unittest.main()
