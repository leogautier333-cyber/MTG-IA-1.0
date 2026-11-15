import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from database import storage
from services import (
    alert_triage, csv_import, deck_importer, github_actions, inventory, portfolio_history,
    portfolio_metrics, pricing, sales_report,
)


class PricingTests(unittest.TestCase):
    def test_unknown_purchase_price_does_not_produce_margin_suggestion(self):
        card = {"scryfall_id": "a", "status": "FOR_SALE", "purchase_price_unknown": True}
        line = pricing.suggestions([card], {"a": {"2026-01-01": {"eur": 10}}})[0]
        self.assertIsNone(line["purchase_price"])
        self.assertIsNone(line["suggested"])
        self.assertEqual(line["status"], "COST_UNKNOWN")

    def test_minimum_price_includes_fee_and_margin(self):
        self.assertAlmostEqual(pricing.minimum_price(10, 15), 10 * 1.15 / 0.95)

    def test_suggested_price_never_rounds_below_minimum(self):
        advice = pricing.suggest(1.0, 1.0, 1.0, min_margin_pct=0)
        self.assertEqual(advice["suggested"], 1.06)
        self.assertGreaterEqual(advice["suggested"], pricing.minimum_price(1, 0))
        advice = pricing.suggest(1.0, 9.5, 1.0, min_margin_pct=15)
        self.assertEqual(advice["suggested"], 11.51)

    def test_margin_uses_decimal_half_up_rounding(self):
        margin = inventory.calculate_net_margin(0.10, 0)
        self.assertEqual(margin["cm_fee"], 0.01)
        self.assertEqual(margin["net_profit"], 0.09)

    def test_statuses(self):
        self.assertEqual(pricing.suggest(None, 5, 8)["status"], "NO_DATA")
        self.assertEqual(pricing.suggest(5, 10, 12)["status"], "BELOW_MIN")
        self.assertEqual(pricing.suggest(20, 5, 30)["status"], "TOO_HIGH")
        self.assertEqual(pricing.suggest(20, 5, 10)["status"], "TOO_LOW")
        self.assertEqual(pricing.suggest(20, 5, 20.5)["status"], "ALIGNED")

    def test_latest_market_price_uses_newest_day(self):
        history = {"a": {"2026-01-01": {"eur": 1}, "2026-01-03": {"eur": 3}}}
        self.assertEqual(pricing.latest_market_price(history, "a"), 3.0)
        self.assertIsNone(pricing.latest_market_price(history, "a", foil=True))


class PortfolioTests(unittest.TestCase):
    def test_deck_missing_prices_are_marked_partial(self):
        with mock.patch.object(deck_importer.scryfall, "get_card_by_name", return_value=None):
            result = deck_importer.analyze_decklist([{"name": "Test", "quantity": 2}], [])
        self.assertEqual(result["unpriced_count"], 2)
        self.assertEqual(result["estimated_acquisition_cost_eur"], 0)

    def test_unrealized_profit_only_uses_priced_known_cost_positions(self):
        collection = [
            {"scryfall_id": "a", "purchase_price": 5, "status": "FOR_SALE"},
            {"scryfall_id": "b", "purchase_price": 8, "status": "FOR_SALE"},
            {"scryfall_id": "c", "purchase_price": 0, "purchase_price_unknown": True,
             "status": "FOR_SALE"},
        ]
        history = {"a": {"2026-11-12": {"eur": 10}}, "c": {"2026-11-12": {"eur": 50}}}
        result = portfolio_metrics.summarize(collection, history, today=date(2026, 11, 12))
        self.assertEqual(result["unrealized_net"], 4.5)
        self.assertEqual(result["unknown_cost"], 1)

    def test_asking_value_uses_only_active_stock_and_quantities_without_market_prices(self):
        collection = [
            {"scryfall_id": "a", "quantity": 3, "selling_price": 1.25, "status": "FOR_SALE"},
            {"scryfall_id": "", "quantity": 2, "selling_price": 4.0, "status": "FOR_SALE"},
            {"scryfall_id": "c", "quantity": 9, "selling_price": 100.0, "status": "SOLD"},
            {"scryfall_id": "d", "quantity": 2, "selling_price": 50.0, "status": "WITHDRAWN"},
        ]
        metrics = portfolio_metrics.summarize(collection, {})
        self.assertEqual(metrics["cards"], 2)
        self.assertEqual(metrics["copies"], 5)
        self.assertEqual(metrics["asking_value"], 11.75)
        self.assertEqual(metrics["coverage_pct"], 0)

    def test_sold_cards_leave_the_value(self):
        history = {"a": {"2026-01-01": {"eur": 10}, "2026-01-05": {"eur": 12}}}
        cards = [{"scryfall_id": "a", "quantity": 2, "purchase_price": 5, "date_added": "2026-01-01T10:00",
                  "status": "SOLD", "date_sold": "2026-01-05T10:00"}]
        series = portfolio_history.value_series(cards, history)
        self.assertEqual(series[0], {"date": "2026-01-01", "value": 20.0, "cost": 10.0})
        self.assertEqual(series[1]["value"], 0.0)

    def test_metrics_cover_only_current_stock_and_flag_missing_stale_prices(self):
        collection = [
            {"scryfall_id": "a", "name": "A", "set_code": "SET", "quantity": 2,
             "purchase_price": 5, "status": "FOR_SALE", "date_added": "2026-01-01"},
            {"scryfall_id": "b", "name": "B", "quantity": 1,
             "purchase_price": 4, "status": "FOR_SALE"},
            {"scryfall_id": "c", "quantity": 1, "purchase_price": 3, "status": "SOLD"},
        ]
        history = {"a": {"2026-11-10": {"eur": 10}}}
        metrics = portfolio_metrics.summarize(collection, history, today=date(2026, 11, 12))
        self.assertEqual(metrics["invested"], 14)
        self.assertEqual(metrics["gross_value"], 20)
        self.assertEqual(metrics["net_value"], 19)
        self.assertEqual(metrics["missing_prices"], 1)
        self.assertEqual(metrics["coverage_pct"], 50)
        self.assertEqual([item["drop_pct"] for item in metrics["scenarios"]], [10, 20, 30])

    def test_metrics_aggregate_same_card_positions(self):
        collection = [
            {"scryfall_id": "a", "foil": False, "name": "A", "quantity": 1,
             "purchase_price": 2, "status": "FOR_SALE"},
            {"scryfall_id": "a", "foil": False, "name": "A", "quantity": 3,
             "purchase_price": 4, "status": "FOR_SALE"},
        ]
        metrics = portfolio_metrics.summarize(
            collection, {"a": {"2026-11-12": {"eur": 10}}}, today=date(2026, 11, 12)
        )
        self.assertEqual(len(metrics["positions"]), 1)
        self.assertEqual(metrics["positions"][0]["quantity"], 4)
        self.assertEqual(metrics["positions"][0]["market_value"], 40)

    def test_alert_triage_ranks_large_stock_impact_and_marks_rumor(self):
        collection = [{
            "scryfall_id": "a", "foil": False, "quantity": 10, "status": "FOR_SALE",
        }]
        history = {"a": {"2026-11-12": {"eur": 10}}}
        alerts = [
            {"kind": "PRICE", "scryfall_id": "a", "foil": False, "quantity": 10,
             "previous_price": 10, "current_price": 8, "created_at": "2026-11-12"},
            {"kind": "REDDIT_HYPE", "scryfall_id": "a", "created_at": "2026-11-12"},
        ]
        ranked = alert_triage.prioritize(alerts, collection, history, today=date(2026, 11, 12))
        self.assertEqual(ranked[0]["impact_eur"], 20)
        self.assertEqual(ranked[0]["priority"], "À traiter en priorité")
        self.assertIn("rumeur", ranked[1]["reliability"])
        foil_event = alert_triage.prioritize(
            [{"kind": "SPOILER_REPRINT", "scryfall_id": "foil-only",
              "created_at": "2026-11-12"}],
            [{"scryfall_id": "foil-only", "foil": True, "quantity": 2, "status": "FOR_SALE"}],
            {"foil-only": {"2026-11-12": {"eur_foil": 15}}},
            today=date(2026, 11, 12),
        )[0]
        self.assertEqual(foil_event["priority"], "À surveiller de près")
        historical = alert_triage.prioritize(
            [{"kind": "PRICE", "scryfall_id": "sold", "quantity": 3,
              "previous_price": 10, "current_price": 20, "created_at": "2026-11-12"}],
            collection, history, today=date(2026, 11, 12),
        )[0]
        self.assertIsNone(historical["impact_eur"])
        self.assertEqual(historical["priority"], "Historique · hors stock")


class DecisionJournalTests(unittest.TestCase):
    def test_decision_is_persisted_on_matching_inventory_record(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(storage, "DATABASE_PATH", Path(directory) / "decisions.sqlite3"), \
             mock.patch.object(storage, "DATABASE_URL", None), \
             mock.patch.dict(os.environ, {"DATABASE_URL": "", "MTG_DATABASE_URL": ""}):
            inventory.add_card_to_collection(
                scryfall_id="journal-card", name="Journal Card", set_code="tst",
                purchase_price=2, selling_price=5,
            )
            card = inventory.get_collection()[0]
            self.assertTrue(inventory.record_decision(card, "Conserver", "La demande reste stable", "3 mois"))
            updated = inventory.get_collection()[0]["decision_journal"][0]
            self.assertEqual(updated["decision"], "Conserver")
            self.assertEqual(updated["horizon"], "3 mois")
            with self.assertRaises(ValueError):
                inventory.record_decision(card, "Inconnu", "raison")


class SaleMarketComparisonTests(unittest.TestCase):
    def test_sale_compares_against_latest_price_known_at_sale_date(self):
        collection = [{
            "scryfall_id": "sold", "name": "Sold Card", "set_code": "SET",
            "status": "SOLD", "quantity": 2, "purchase_price": 3,
            "actual_sale_price": 12, "date_sold": "2026-11-10T12:00:00",
        }]
        history = {"sold": {
            "2026-11-08": {"eur": 5},
            "2026-11-11": {"eur": 9},
        }}
        lines = sales_report.compare_to_market(sales_report.sale_lines(collection), history)
        self.assertEqual(lines[0]["market_reference"], 5)
        self.assertEqual(lines[0]["sale_vs_market"], 14)
        self.assertEqual(sales_report.summarize(lines)["market_compared_sales"], 1)


class CsvImportTests(unittest.TestCase):
    def test_invalid_quantity_and_prices_are_reported(self):
        for quantity, price in (("nan", "1"), ("inf", "1"), ("-2", "1"),
                                ("1.5", "1"), ("1", "-1"), ("1", "inf")):
            with self.subTest(quantity=quantity, price=price):
                rows, errors = csv_import.parse_csv(
                    f"name,quantity,selling_price\nTest,{quantity},{price}\n"
                )
                self.assertEqual(rows, [])
                self.assertEqual(len(errors), 1)

    def test_parse_semicolon_french_headers(self):
        rows, errors = csv_import.parse_csv("Nom;Édition;Quantité;Prix d'achat\nSol Ring;c21;2;1,5\n")
        self.assertEqual(errors, [])
        self.assertEqual((rows[0]["name"], rows[0]["quantity"], rows[0]["purchase_price"]), ("Sol Ring", 2, 1.5))

    def test_missing_columns(self):
        rows, errors = csv_import.parse_csv("foo,bar\n1,2\n")
        self.assertEqual(rows, [])
        self.assertTrue(errors)

    def test_resolve_reports_unknown_cards(self):
        rows, _ = csv_import.parse_csv("name,set\nSol Ring,c21\nNope,xxx\n")
        fetch = lambda ids: {"found": [{"id": "1", "name": "Sol Ring", "set": "c21", "collector_number": "1"}]}
        cards, errors = csv_import.resolve(rows, fetch=fetch)
        self.assertEqual([c["scryfall_id"] for c in cards], ["1"])
        self.assertEqual(len(errors), 1)


class GitHubActionsTests(unittest.TestCase):
    def test_trigger_success_and_no_token_leak(self):
        session = mock.Mock()
        session.post.return_value = mock.Mock(status_code=204)
        with mock.patch.dict(os.environ, {github_actions.TOKEN_VARIABLE: "secret-token"}):
            self.assertTrue(github_actions.trigger("backup.yml", session=session))
            session.post.return_value = mock.Mock(status_code=403)
            with self.assertRaises(github_actions.GitHubActionsError) as raised:
                github_actions.trigger("backup.yml", session=session)
        self.assertNotIn("secret-token", str(raised.exception))

    def test_unknown_workflow_and_missing_token(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(github_actions.is_configured())
            with self.assertRaises(github_actions.GitHubActionsError):
                github_actions.trigger("backup.yml", session=mock.Mock())
            with self.assertRaises(github_actions.GitHubActionsError):
                github_actions.trigger("evil.yml")


if __name__ == "__main__":
    unittest.main()
