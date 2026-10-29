import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from database import storage
from services import inventory, phase_one_monitor, sales_report, weekly_report


def _card(scryfall_id="c1", status="FOR_SALE", **extra):
    return {
        "scryfall_id": scryfall_id, "name": f"Card {scryfall_id}", "set_code": "tst",
        "purchase_price": 4.0, "selling_price": 10.0, "status": status, "quantity": 1, **extra,
    }


def _history(points):
    return lambda scryfall_id, foil=False, **_: points.get(scryfall_id, [])


class SalesReportTests(unittest.TestCase):
    def test_monthly_summary_applies_fees_quantity_and_ignores_unsold(self):
        collection = [
            _card("a", "SOLD", actual_sale_price=10.0, quantity=2, date_sold="2026-10-05T10:00:00+00:00"),
            _card("b", "SOLD", actual_sale_price=8.0, purchase_price=2.0, date_sold="2026-09-20T10:00:00+00:00"),
            _card("c"),
        ]
        months = sales_report.monthly_summary(collection)
        self.assertEqual([m["month"] for m in months], ["2026-10", "2026-09"])
        october = months[0]
        self.assertEqual(october["cards_sold"], 2)
        self.assertEqual(october["revenue"], 20.0)
        self.assertEqual(october["fees"], 1.0)
        self.assertEqual(october["net_profit"], 11.0)
        self.assertEqual(october["roi_percent"], 137.5)

    def test_sales_since_only_counts_recent_sales(self):
        collection = [
            _card("a", "SOLD", actual_sale_price=10.0, date_sold="2026-10-27T10:00:00+00:00"),
            _card("b", "SOLD", actual_sale_price=10.0, date_sold="2026-10-01T10:00:00+00:00"),
        ]
        recent = sales_report.sales_since(collection, date(2026, 10, 22))
        self.assertEqual(recent["cards_sold"], 1)


class WeeklyReportTests(unittest.TestCase):
    def test_totals_cover_only_stock_for_sale(self):
        collection = [_card("a", quantity=2), _card("b", "SOLD", actual_sale_price=9.0)]
        totals = weekly_report.stock_totals(collection)
        self.assertEqual(totals["references"], 1)
        self.assertEqual(totals["value"], 20.0)
        self.assertEqual(totals["cost"], 8.0)

    def test_movers_are_sorted_and_need_a_week_of_history(self):
        today = date(2026, 10, 29)
        history = _history({
            "a": [{"snapshot_date": "2026-10-20", "price_eur": 10.0}, {"snapshot_date": "2026-10-29", "price_eur": 12.0}],
            "b": [{"snapshot_date": "2026-10-20", "price_eur": 10.0}, {"snapshot_date": "2026-10-29", "price_eur": 8.0}],
            "c": [{"snapshot_date": "2026-10-28", "price_eur": 10.0}, {"snapshot_date": "2026-10-29", "price_eur": 50.0}],
        })
        movers = weekly_report.price_movers([_card("a"), _card("b"), _card("c")], today, history)
        self.assertEqual([m["name"] for m in movers], ["Card a", "Card b"])
        self.assertAlmostEqual(movers[0]["change_pct"], 20.0)

    def test_report_is_sent_as_embed_with_all_sections(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(storage, "DATABASE_PATH", Path(directory) / "t.sqlite3"), \
             patch.object(storage, "DATABASE_URL", None), \
             patch.dict(os.environ, {"DATABASE_URL": "", "MTG_DATABASE_URL": "",
                                     "DISCORD_WEBHOOK_URL": "https://discord.invalid/hook"}):
            inventory.save_collection([_card("a")])
            with patch.object(weekly_report.requests, "post", return_value=Mock()) as post, \
                 patch.object(phase_one_monitor.requests, "post", return_value=Mock()) as post2:
                weekly_report.send_weekly_report(today=date(2026, 10, 29))
        embed = (post.call_args or post2.call_args).kwargs["json"]["embeds"][0]
        names = [field["name"] for field in embed["fields"]]
        self.assertIn("Stock en vente", names)
        self.assertIn("Ventes de la semaine", names)
        self.assertIn("Plus fortes hausses (7 j)", names)


class TargetAndSourceAlertTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for patcher in (
            patch.object(storage, "DATABASE_PATH", Path(directory.name) / "test.sqlite3"),
            patch.object(storage, "DATABASE_URL", None),
            patch.dict(os.environ, {"DATABASE_URL": "", "MTG_DATABASE_URL": "",
                                    "DISCORD_WEBHOOK_URL": "https://discord.invalid/hook"}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _analyze(self, price):
        with patch.object(
            phase_one_monitor.scryfall, "get_cards_batch",
            return_value=[{"id": "c1", "prices": {"eur": price}}],
        ), patch.object(phase_one_monitor.requests, "post", return_value=Mock()) as post:
            result = phase_one_monitor.analyze_stock_prices()
        return result, post

    def test_target_alert_fires_once_when_price_reaches_target(self):
        inventory.save_collection([_card(target_price=12.0)])
        result, post = self._analyze("11.00")
        self.assertEqual(result["alerts_created"], 0)
        result, post = self._analyze("12.00")
        self.assertEqual(result["alerts_created"], 1)
        self.assertEqual(post.call_count, 1)
        self.assertIn("objectif", post.call_args.kwargs["json"]["embeds"][0]["description"])
        result, post = self._analyze("13.00")
        self.assertEqual(result["alerts_created"], 0)
        post.assert_not_called()

    def test_quick_edit_sets_and_clears_target(self):
        inventory.save_collection([_card()])
        original = inventory.get_collection()[0]
        self.assertTrue(inventory.update_card(original, 1, 4, 10, 15))
        edited = inventory.get_collection()[0]
        self.assertEqual(edited["target_price"], 15.0)
        self.assertTrue(inventory.update_card(edited, 1, 4, 10, 0))
        self.assertNotIn("target_price", inventory.get_collection()[0])

    def test_source_problem_alert_is_sent_once_per_day(self):
        with patch.object(phase_one_monitor.requests, "post", return_value=Mock()) as post:
            phase_one_monitor._notify_source_problem(["Reddit r/mtgfinance: timeout"])
            phase_one_monitor._notify_source_problem(["Reddit r/mtgfinance: timeout"])
        self.assertEqual(post.call_count, 1)
        self.assertIn("SOURCE INDISPONIBLE", post.call_args.kwargs["json"]["embeds"][0]["title"])


if __name__ == "__main__":
    unittest.main()
