import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from database import market_data, storage
from services.backtesting import backtest_price_threshold, evaluate_alerts
from services.deck_importer import analyze_decklist, parse_plain_decklist
from services.market_signals import (
    analyze_snapshot_buyout,
    calculate_reprint_risk,
    scan_snapshot_buyouts,
)
from services.portfolio_analytics import analyze_portfolio, simulate_sale, suggest_arbitrage
from services.source_health import get_source_statuses
from agents.tool_agent import ToolCallingAgent


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "workflow.sqlite3"
        self.path_patch = patch.object(storage, "DATABASE_PATH", self.database_path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        for isolation in (
            patch.object(storage, "DATABASE_URL", None),
            patch.dict("os.environ", {"DATABASE_URL": "", "MTG_DATABASE_URL": ""}),
        ):
            isolation.start()
            self.addCleanup(isolation.stop)
        self.addCleanup(self.temporary_directory.cleanup)

    def test_alert_outcomes_and_threshold_backtest(self):
        storage.record_alert(
            "buy-1", "BUY_SIGNAL", "card-1", "2026-10-01T00:00:00+00:00",
            {"reference_price": 10, "foil": False, "language": "FR", "condition": "NM"},
        )
        for day, price in (
            ("2026-10-01", 10), ("2026-10-02", 8),
            ("2026-10-09", 11), ("2026-10-31", 12),
        ):
            storage.record_price_snapshot("card-1", price, observed_at=f"{day}T12:00:00+00:00")

        outcome = evaluate_alerts(now=datetime(2026, 11, 2, tzinfo=timezone.utc))
        backtest = backtest_price_threshold(
            "card-1", threshold_pct=15, horizon_days=7, direction="drop"
        )

        self.assertEqual(outcome["evaluations_written"], 2)
        self.assertEqual(backtest["sample_count"], 1)
        self.assertEqual(backtest["accuracy"], 1.0)

    def test_portfolio_net_sale_and_daily_mark_to_market(self):
        card = {
            "scryfall_id": "card-1", "name": "Card", "set_code": "set1",
            "status": "FOR_SALE", "quantity": 2, "purchase_price": 10,
            "selling_price": 20, "condition": "LP", "liquidity_rating": "FAST",
            "date_added": "2026-10-01", "legalities": {"commander": "legal"},
        }
        storage.record_price_snapshot(
            "card-1", 21, condition="LP", observed_at="2026-10-14T12:00:00+00:00"
        )

        sale = simulate_sale(card, 20, shipping=1)
        portfolio = analyze_portfolio([card], today=date(2026, 10, 14))

        self.assertEqual(sale["net_per_card"], 17.05)
        self.assertEqual(sale["profit_total"], 14.1)
        self.assertEqual(portfolio["stock_value"], 42)
        self.assertEqual(portfolio["unrealized_profit"], 22)
        self.assertAlmostEqual(portfolio["by_format"]["commander"]["roi_pct"], 110)

    def test_reprint_history_and_buyout_proxy_require_evidence(self):
        old_print = {
            "id": "old", "oracle_id": "oracle-1", "name": "Card",
            "set": "old", "released_at": "2010-01-01", "legalities": {},
            "prices": {"eur": "7.00", "eur_foil": "12.00"},
            "edhrec_rank": 50000,
        }
        new_print = {
            "id": "new", "oracle_id": "oracle-1", "name": "Card",
            "set": "new", "released_at": "2020-01-01", "legalities": {},
            "prices": {"eur": "5.00", "eur_foil": "9.00"},
            "edhrec_rank": 12000,
        }
        market_data.sync_bulk_prints([old_print], synced_at="2010-01-01T00:00:00+00:00")
        market_data.sync_bulk_prints([old_print, new_print], synced_at="2020-01-01T00:00:00+00:00")
        for day, price in (("2020-01-01", 10), ("2020-01-31", 8), ("2020-03-31", 7)):
            storage.record_price_snapshot("old", price, observed_at=f"{day}T00:00:00+00:00")

        risk = calculate_reprint_risk("old", today=date(2026, 10, 14))
        arbitrage = suggest_arbitrage([{
            "scryfall_id": "old", "name": "Card", "set_code": "old",
            "status": "FOR_SALE", "quantity": 1, "purchase_price": 1,
            "selling_price": 10, "condition": "NM",
        }])

        self.assertEqual(risk["expected_change_30d_pct"], -20)
        self.assertEqual(risk["expected_change_90d_pct"], -30)
        self.assertEqual(risk["release_intervals_days"], [3652])
        self.assertEqual(arbitrage[0]["buy_set"], "new")
        demand_history = market_data.get_edhrec_history("old")
        self.assertEqual(demand_history[-1]["edhrec_rank"], 50000)

    def test_snapshot_buyout_uses_reprint_legality_and_edhrec_evidence(self):
        tracked = {
            "id": "spike-card", "oracle_id": "spike-oracle", "name": "Spike Card",
            "set": "set1", "released_at": "2020-01-01", "legalities": {},
            "edhrec_rank": 50000,
        }
        market_data.sync_bulk_prints([tracked], synced_at="2026-09-14T00:00:00+00:00")
        updated = {**tracked, "edhrec_rank": 10000}
        market_data.sync_bulk_prints([updated], synced_at="2026-10-14T00:00:00+00:00")
        for day, price in (
            ("2026-09-01", 10.0), ("2026-09-07", 10.1), ("2026-09-14", 10.0),
            ("2026-10-10", 13.0), ("2026-10-14", 15.0),
        ):
            storage.record_price_snapshot(
                "spike-card", price, observed_at=f"{day}T12:00:00+00:00"
            )

        supported = analyze_snapshot_buyout("spike-card", today=date(2026, 10, 14))
        self.assertEqual(supported["status"], "DEMAND_SUPPORTED")
        self.assertAlmostEqual(supported["edhrec_improvement_pct_30d"], 80)
        stale = analyze_snapshot_buyout("spike-card", today=date(2026, 10, 20))
        self.assertEqual(stale["status"], "STALE_DATA")

        storage.record_alert(
            "recent-reprint", "REPRINT", "new-print", "2026-10-13T00:00:00+00:00",
            {"oracle_id": "spike-oracle", "scryfall_id": "new-print"},
        )
        fundamental = analyze_snapshot_buyout("spike-card", today=date(2026, 10, 14))
        self.assertEqual(fundamental["status"], "FUNDAMENTAL_EVENT")
        self.assertEqual(fundamental["score"], 0)
        storage.record_alert(
            "recent-ban", "LEGALITY_CHANGE", "spike-card", "2026-10-13T01:00:00+00:00",
            {"transitions": {"duel": {"from": "legal", "to": "not_legal"}}},
        )
        legality_event = analyze_snapshot_buyout("spike-card", today=date(2026, 10, 14))
        self.assertEqual(legality_event["status"], "FUNDAMENTAL_EVENT")
        self.assertIn("LEGALITY_CHANGE", {event["signal_type"] for event in legality_event["fundamental_events"]})

    def test_snapshot_buyout_flags_an_unexplained_spike_and_logs_it(self):
        market_data.sync_bulk_prints([{
            "id": "spec-card", "oracle_id": "spec-oracle", "name": "Spec Card",
            "set": "set1", "released_at": "2020-01-01", "legalities": {},
            "edhrec_rank": 50000,
        }], synced_at="2026-09-14T00:00:00+00:00")
        market_data.sync_bulk_prints([{
            "id": "spec-card", "oracle_id": "spec-oracle", "name": "Spec Card",
            "set": "set1", "released_at": "2020-01-01", "legalities": {},
            "edhrec_rank": 50000,
        }], synced_at="2026-10-14T00:00:00+00:00")
        for day, price in (
            ("2026-09-01", 10.0), ("2026-09-07", 10.1), ("2026-09-14", 10.0),
            ("2026-10-10", 13.0), ("2026-10-14", 15.0),
        ):
            storage.record_price_snapshot(
                "spec-card", price, observed_at=f"{day}T12:00:00+00:00"
            )

        finding = analyze_snapshot_buyout("spec-card", today=date(2026, 10, 14))
        alerts = scan_snapshot_buyouts([{
            "scryfall_id": "spec-card", "name": "Spec Card", "status": "FOR_SALE",
        }], today=date(2026, 10, 14))

        self.assertIn(finding["status"], {"SUSPICIOUS", "HIGH_SUSPICION"})
        self.assertGreaterEqual(finding["score"], 60)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["card_name"], "Spec Card")

    def test_deck_import_and_tool_number_validation(self):
        deck = parse_plain_decklist("Commander\n1 Sol Ring (CMM) 396\n2x Fire // Ice")
        analysis = analyze_decklist(
            deck, [{"name": "Sol Ring", "quantity": 1, "status": "FOR_SALE"}],
            fetch_prices=False,
        )

        self.assertEqual(deck[1], {"name": "Fire // Ice", "quantity": 2})
        self.assertEqual(analysis["owned_count"], 1)
        self.assertEqual(analysis["missing"][0]["quantity"], 2)
        self.assertTrue(ToolCallingAgent._contains_unverified_number("Le prix est 99 €", ['{"price": 9.9}']))
        self.assertFalse(ToolCallingAgent._contains_unverified_number("Le prix est 9.9 €", ['{"price": 9.9}']))

    def test_source_freshness_distinguishes_stale_from_failed(self):
        storage.record_source_health("scryfall", True, checked_at="2026-10-14T00:00:00+00:00")
        storage.record_source_health(
            "scryfall_bulk", False, "Bulk HTTP 503", checked_at="2026-10-14T00:00:00+00:00"
        )
        storage.record_source_health("reddit", False, "HTTP 503", checked_at="2026-10-14T00:00:00+00:00")

        statuses = get_source_statuses(now=datetime(2026, 10, 14, tzinfo=timezone.utc))

        self.assertEqual(statuses["scryfall"]["status"], "HEALTHY")
        self.assertEqual(statuses["scryfall_bulk"]["status"], "FAILED")
        self.assertEqual(statuses["reddit"]["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()