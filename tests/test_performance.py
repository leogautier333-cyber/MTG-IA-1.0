import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from database import market_data, storage
from services import market_monitor, market_signals, stock_advisor


class BatchReadTests(unittest.TestCase):
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

    def test_batch_matches_individual_histories_with_variants_and_missing_cards(self):
        market_data.record_demand_snapshot("a", 1000, "2026-10-10")
        market_data.record_demand_snapshot("a", 800, "2026-10-27")
        market_data.sync_bulk_prints([{"id": "b", "edhrec_rank": 500}], synced_at="2026-10-28")
        storage.record_price_snapshot("a", 10, observed_at="2026-10-10T10:00:00+00:00")
        storage.record_price_snapshot("a", 12, observed_at="2026-10-27T10:00:00+00:00")
        storage.record_price_snapshot("a", 30, foil=True, language="EN",
                                      observed_at="2026-10-27T10:00:00+00:00")
        variants = [("a", False, "FR", "NM"), ("a", True, "EN", "NM"),
                    ("b", False, "FR", "NM"), ("missing", False, "FR", "NM")]
        expected_points = {key: storage.get_price_points(*key) for key in variants}
        ids = ["a", "b", "missing"]
        expected_histories = {card_id: market_data.get_edhrec_history(card_id) for card_id in ids}
        expected_popularity = {
            card_id: stock_advisor.popularity_delta_pct(card_id, today=date(2026, 10, 28))
            for card_id in ids
        }
        with patch.object(storage, "_connect", wraps=storage._connect) as connect:
            points = storage.get_price_points_batch(variants)
            histories = market_data.get_edhrec_histories(ids)
            popularity = stock_advisor.popularity_deltas_pct(ids, today=date(2026, 10, 28))
        self.assertEqual(connect.call_count, 3)
        self.assertEqual(points, expected_points)
        self.assertEqual(histories, expected_histories)
        self.assertEqual(popularity, expected_popularity)
        original = market_signals.analyze_snapshot_buyout("a", today=date(2026, 10, 28))
        batched = market_signals.analyze_snapshot_buyout(
            "a", today=date(2026, 10, 28), points=points[variants[0]],
            demand_history=histories["a"],
        )
        self.assertEqual(batched, original)

    def test_empty_batch_does_not_connect(self):
        with patch.object(storage, "_connect", wraps=storage._connect) as connect:
            self.assertEqual(storage.get_price_points_batch([]), {})
            self.assertEqual(market_data.get_edhrec_histories([]), {})
        connect.assert_not_called()

    def test_watchlist_histories_are_read_in_one_connection(self):
        storage.record_price_snapshot("a", 10, observed_at="2026-10-10T10:00:00+00:00")
        storage.record_price_snapshot("a", 8, observed_at="2026-10-11T10:00:00+00:00")
        storage.record_price_snapshot("a", 20, foil=True, language="EN",
                                      observed_at="2026-10-11T10:00:00+00:00")
        watchlist = [
            {"scryfall_id": "a"},
            {"scryfall_id": "a", "foil": True, "language": "EN"},
            {"scryfall_id": "missing"},
        ]
        keys = [market_monitor._variant_key(card) for card in watchlist]
        expected = {key: storage.get_variant_price_history(*key) for key in keys}
        with patch.object(storage, "_connect", wraps=storage._connect) as connect:
            histories = market_monitor.get_watchlist_observations(watchlist)
        self.assertEqual(histories, expected)
        self.assertEqual(connect.call_count, 1)

    def test_large_batch_is_chunked_and_preserves_input_keys(self):
        ids = [f"card-{index}" for index in range(501)]
        storage.initialize_database()
        with patch.object(storage, "_connect", wraps=storage._connect) as connect:
            histories = market_data.get_edhrec_histories(ids)
            points = storage.get_price_points_batch((card_id, False, "FR", "NM") for card_id in ids)
        self.assertEqual(connect.call_count, 2)
        self.assertEqual(len(histories), 501)
        self.assertEqual(len(points), 501)
        self.assertTrue(all(not history for history in histories.values()))


class DiscordFragmentTests(unittest.TestCase):
    def test_discord_click_sends_and_shows_result(self):
        script = """
import streamlit as st
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from database import storage
from services import inventory, phase_one_monitor, github_actions

inventory.add_cards_to_collection([
    dict(scryfall_id=f"fake-{n}", name=f"Card {n}", set_code="TST",
         image_url="https://example.invalid/x", mana_cost="{1}", type_line="Creature", oracle_text="Test")
    for n in range(10)
])
with patch.object(github_actions, "latest_run", return_value=None) as github, \\
     patch.object(github_actions, "trigger") as trigger, \\
     patch.object(phase_one_monitor, "_send_webhook") as send, \\
     patch.object(storage, "_connect", wraps=storage._connect) as connect:
    app = AppTest.from_file("app.py", default_timeout=60).run()
    assert not app.exception
    assert connect.call_count <= 10, connect.call_count
    assert github.call_count == 3, github.call_count
    next(b for b in app.button if b.label == "Envoyer une alerte test Discord").click().run()
    assert not app.exception
    assert send.call_count == 1
    assert github.call_count == 3, github.call_count
    assert any("Alerte test envoyée" in item.value for item in app.success)
    send.side_effect = RuntimeError("test error")
    next(b for b in app.button if b.label == "Envoyer une alerte test Discord").click().run()
    assert any("Test Discord impossible : test error" in item.value for item in app.error)
    send.side_effect = None
    next(b for b in app.button if b.label == "Envoyer une alerte test Discord").click().run()
    assert not app.error
    next(b for b in app.button if b.key == "gh_run_stock_analysis.yml").click().run()
    assert trigger.call_count == 1
    assert github.call_count == 6, github.call_count
"""
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            for name in ("DATABASE_URL", "MTG_DATABASE_URL", "GITHUB_ACTIONS", "DISCORD_WEBHOOK_URL"):
                env.pop(name, None)
            env["MTG_DATABASE_PATH"] = str(Path(directory) / "test.sqlite3")
            env["GITHUB_DISPATCH_TOKEN"] = "fake-token"
            env["PYTHONIOENCODING"] = "utf-8"
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                env=env, capture_output=True, text=True, timeout=90,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
