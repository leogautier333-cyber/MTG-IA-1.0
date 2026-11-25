import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from database import market_data, storage
from modules.stock_view import render_popularity_badge
from services import inventory, phase_one_monitor, spoiler_watcher


def _card(scryfall_id, **extra):
    return {"scryfall_id": scryfall_id, "name": f"Card {scryfall_id}", "set_code": "tst",
            "purchase_price": 0.1, "selling_price": 1.0, "status": "FOR_SALE", **extra}


class AlertFloorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for patcher in (
            patch.object(storage, "DATABASE_PATH", Path(directory.name) / "test.sqlite3"),
            patch.object(storage, "DATABASE_URL", None),
            patch.dict(os.environ, {"DATABASE_URL": "", "MTG_DATABASE_URL": "", "GITHUB_ACTIONS": "",
                                    "DISCORD_WEBHOOK_URL": "https://discord.invalid/hook"}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_floor_is_decimal_exact_and_uses_the_higher_price(self):
        meets = phase_one_monitor.meets_alert_floor
        self.assertEqual(phase_one_monitor.MIN_ALERT_UNIT_PRICE, Decimal("1.00"))
        self.assertTrue(meets(1.0, 1.0))
        self.assertTrue(meets(0.99, 1.0))
        self.assertTrue(meets(1.2, 0.9))
        self.assertFalse(meets(0.99, 0.99))
        self.assertFalse(meets(0.5, 0.9))
        self.assertTrue(meets(0.1 + 0.9, 0))

    def test_bulk_cards_are_tracked_but_never_alerted(self):
        inventory.save_collection([_card("bulk"), _card("valuable"), _card("boundary")])
        for card_id, price in (("bulk", 0.20), ("valuable", 5.0), ("boundary", 1.0)):
            inventory.record_price_snapshot(card_id, price, observed_at="2026-10-22T10:00:00+00:00")
        prices = {"bulk": "0.40", "valuable": "6.00", "boundary": "1.20"}
        batch = [{"id": key, "prices": {"eur": value}, "edhrec_rank": 100 + index}
                 for index, (key, value) in enumerate(prices.items())]
        with patch.object(phase_one_monitor.scryfall, "get_cards_batch", return_value=batch), \
             patch.object(phase_one_monitor.requests, "post", return_value=Mock()):
            result = phase_one_monitor.analyze_stock_prices()
        self.assertEqual(result["snapshots_written"], 3)
        self.assertEqual({a["scryfall_id"] for a in inventory.get_price_alerts()}, {"valuable", "boundary"})
        for card_id in prices:
            self.assertEqual(len(inventory.get_variant_price_history(card_id)), 2, card_id)
            self.assertEqual(len(market_data.get_edhrec_history(card_id)), 1, card_id)

    def test_drop_from_above_the_floor_still_alerts_and_old_bulk_alerts_are_cancelled(self):
        inventory.save_collection([_card("fall"), _card("old-bulk")])
        inventory.record_price_snapshot("fall", 1.2, observed_at="2026-10-22T10:00:00+00:00")
        inventory.save_price_alerts([{
            "id": "legacy", "kind": "PRICE", "status": "pending", "scryfall_id": "old-bulk", "foil": False,
            "card_name": "Old", "previous_price": 0.20, "current_price": 0.30, "change_pct": 50.0,
        }])
        batch = [{"id": "fall", "prices": {"eur": "0.90"}}, {"id": "old-bulk", "prices": {"eur": "0.30"}}]
        with patch.object(phase_one_monitor.scryfall, "get_cards_batch", return_value=batch), \
             patch.object(phase_one_monitor.requests, "post", return_value=Mock()) as post:
            phase_one_monitor.analyze_stock_prices()
        statuses = {a["id"]: a["status"] for a in inventory.get_price_alerts()}
        self.assertEqual(statuses["legacy"], "cancelled")
        fall = next(a for a in inventory.get_price_alerts() if a["scryfall_id"] == "fall")
        self.assertEqual(fall["trend"], "DOWN")
        self.assertEqual(post.call_count, 1)

    def test_target_alert_ignores_the_floor(self):
        inventory.save_collection([_card("cheap", target_price=0.5)])
        with patch.object(phase_one_monitor.scryfall, "get_cards_batch",
                          return_value=[{"id": "cheap", "prices": {"eur": "0.60"}}]), \
             patch.object(phase_one_monitor.requests, "post", return_value=Mock()):
            phase_one_monitor.analyze_stock_prices()
        self.assertEqual([a["kind"] for a in inventory.get_price_alerts()], ["TARGET_REACHED"])


class PopularityBadgeTests(unittest.TestCase):
    def test_badge_states(self):
        self.assertIn("pop-up", render_popularity_badge(12.4))
        self.assertIn("+12%", render_popularity_badge(12.4))
        self.assertIn("pop-down", render_popularity_badge(-30))
        self.assertIn("-30%", render_popularity_badge(-30))
        self.assertIn("pop-flat", render_popularity_badge(0.2))
        self.assertIn("pop-na", render_popularity_badge(None))
        self.assertIn("n/d", render_popularity_badge(None))
        self.assertIn("99+", render_popularity_badge(250))


class SpoilerWatcherTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for patcher in (
            patch.object(storage, "DATABASE_PATH", Path(directory.name) / "test.sqlite3"),
            patch.object(storage, "DATABASE_URL", None),
            patch.dict(os.environ, {"DATABASE_URL": "", "MTG_DATABASE_URL": "", "GITHUB_ACTIONS": ""}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_scan_uses_scryfall_only_and_reports_a_failure(self):
        self.assertFalse(hasattr(spoiler_watcher, "fetch_reddit_posts"))
        session = Mock()
        session.get.side_effect = requests.ConnectionError("down")
        session.post.side_effect = requests.ConnectionError("down")
        result = spoiler_watcher.scan_spoilers([], [_card("x")], session=session)
        self.assertEqual(set(result), {"alerts", "spoiler_count", "errors"})
        self.assertTrue(result["errors"])
        self.assertTrue(all("reddit" not in url for call in session.get.call_args_list for url in call.args))
        self.assertFalse(storage.get_source_health()["scryfall"]["succeeded"])
        self.assertNotIn("reddit", storage.get_source_health())


if __name__ == "__main__":
    unittest.main()
