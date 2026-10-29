import os
import unittest
from unittest import mock

from services import csv_import, github_actions, portfolio_history, pricing


class PricingTests(unittest.TestCase):
    def test_minimum_price_includes_fee_and_margin(self):
        self.assertAlmostEqual(pricing.minimum_price(10, 15), 10 * 1.15 / 0.95)

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
    def test_sold_cards_leave_the_value(self):
        history = {"a": {"2026-01-01": {"eur": 10}, "2026-01-05": {"eur": 12}}}
        cards = [{"scryfall_id": "a", "quantity": 2, "purchase_price": 5, "date_added": "2026-01-01T10:00",
                  "status": "SOLD", "date_sold": "2026-01-05T10:00"}]
        series = portfolio_history.value_series(cards, history)
        self.assertEqual(series[0], {"date": "2026-01-01", "value": 20.0, "cost": 10.0})
        self.assertEqual(series[1]["value"], 0.0)


class CsvImportTests(unittest.TestCase):
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
