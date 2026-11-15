import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import patch

import ai_engine
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

    def test_market_predictions_batch_variants_and_ignore_owned_watchlist(self):
        for card_id, foil, language, prices in (
            ("a", False, "FR", (10, 12)),
            ("b", True, "EN", (10, 8)),
        ):
            for day, price in enumerate(prices, 10):
                storage.record_price_snapshot(
                    card_id, price, foil=foil, language=language,
                    observed_at=f"2026-10-{day}T10:00:00+00:00",
                )
        collection = [{"scryfall_id": "a", "status": "FOR_SALE", "name": "Owned"}]
        watchlist = [{"scryfall_id": "a", "name": "Owned"},
                     {"scryfall_id": "b", "foil": True, "language": "EN", "name": "Watched"}]
        with patch.object(ai_engine, "PREDICTIONS_FILE", storage.DATABASE_PATH.parent / "predictions.json"), \
             patch.object(storage, "_connect", wraps=storage._connect) as connect:
            predictions = ai_engine._create_market_predictions(
                collection, watchlist, ai_engine.datetime(2026, 10, 12)
            )
        self.assertEqual(connect.call_count, 1)
        self.assertEqual([item["category"] for item in predictions], ["SELL_RECOMMENDATION", "BUY_SIGNAL"])
        self.assertEqual([item["reference_price"] for item in predictions], [12, 8])

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

    def test_bulk_sync_batches_database_operations(self):
        storage.initialize_database()
        calls = []
        original_database = market_data._database

        @contextmanager
        def intercepted_database():
            with original_database() as connection:
                class Proxy:
                    def execute(self, query, parameters=()):
                        calls.append("execute")
                        return connection.execute(query, parameters)

                    def executemany(self, query, parameters):
                        calls.append("executemany")
                        return connection.executemany(query, parameters)

                yield Proxy()

        cards = ({"id": f"card-{index}", "oracle_id": f"oracle-{index}", "name": "Card",
                  "set": "tst", "edhrec_rank": index + 1} for index in range(501))
        with patch.object(market_data, "_database", intercepted_database):
            summary = market_data.sync_bulk_prints(cards)
        self.assertTrue(summary["baseline"])
        self.assertLessEqual(len(calls), 10)

    def test_bulk_reprint_detection_respects_changes_earlier_in_batch(self):
        market_data.sync_bulk_prints([
            {"id": "existing", "oracle_id": "old-oracle", "name": "Card", "set": "tst"}
        ])
        summary = market_data.sync_bulk_prints([
            {"id": "existing", "oracle_id": "new-oracle", "name": "Card", "set": "tst"},
            {"id": "new", "oracle_id": "old-oracle", "name": "Card", "set": "tst"},
            {"id": "reprint", "oracle_id": "old-oracle", "name": "Card", "set": "tst"},
        ])
        self.assertEqual([card["scryfall_id"] for card in summary["new_cards"]], ["new"])
        self.assertEqual([card["scryfall_id"] for card in summary["reprints"]], ["reprint"])


class DiscordFragmentTests(unittest.TestCase):
    def test_stock_empty_filter_edit_and_sale(self):
        script = """
from streamlit.testing.v1 import AppTest
from services import inventory
import data_manager

app = AppTest.from_file("app.py", default_timeout=60).run()
assert not app.exception
assert any("Aucune carte enregistrée" in item.value for item in app.info)
inventory.add_cards_to_collection([
    dict(scryfall_id=card_id, name=name, set_code="tst", purchase_price=2,
         selling_price=5, image_url="https://example.invalid/x",
         mana_cost="{1}", type_line="Creature", oracle_text="Test")
    for card_id, name in (("a", "Alpha"), ("b", "Beta"))
])
data_manager.clear_cache()
app.run()
assert not app.exception
assert any(m.label == "Cartes en stock" and m.value == "2 exemplaires" for m in app.metric)
next(w for w in app.text_input if w.label == "Rechercher").set_value("Alpha").run()
assert not app.exception
assert any("1 ligne(s)" in item.value for item in app.caption)
next(w for w in app.number_input if w.label == "Quantité").set_value(3)
next(b for b in app.button if b.label == "Enregistrer").click().run()
assert not app.exception
assert next(card for card in inventory.get_collection() if card["scryfall_id"] == "a")["quantity"] == 3
next(w for w in app.radio if w.label == "Affichage").set_value("Tableau").run()
assert not app.exception
next(w for w in app.number_input if w.label == "Prix réel de vente (€) :").set_value(7.0)
next(b for b in app.button if b.label == "Confirmer la vente").click().run()
assert not app.exception
cards = inventory.get_collection()
assert any(card["scryfall_id"] == "a" and card["status"] == "SOLD"
           and card["actual_sale_price"] == 7 for card in cards), cards
assert any(card["scryfall_id"] == "b" and card["status"] == "FOR_SALE" for card in cards), cards
"""
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            for name in ("DATABASE_URL", "MTG_DATABASE_URL", "GITHUB_ACTIONS", "DISCORD_WEBHOOK_URL"):
                env.pop(name, None)
            env["MTG_DATABASE_PATH"] = str(Path(directory) / "test.sqlite3")
            env["MTG_LAZY_NAVIGATION"] = "true"
            env["PYTHONIOENCODING"] = "utf-8"
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                env=env, capture_output=True, text=True, timeout=90,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_optional_navigation_only_renders_selected_tab(self):
        script = """
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from services import github_actions, inventory

inventory.add_card_to_collection(scryfall_id="sold", name="Sold Card", set_code="tst",
                                 purchase_price=2, selling_price=5)
inventory.mark_card_as_sold("sold", 10)

with patch.object(github_actions, "latest_run", return_value=None) as github:
    app = AppTest.from_file("app.py", default_timeout=60).run()
    assert not app.exception
    assert github.call_count == 0
    assert any("Tableau de bord" in h.value for h in app.header)
    app.radio(key="main_navigation").set_value("Prix & Alertes").run()
    assert not app.exception
    assert github.call_count == 3, github.call_count
    assert not any("Tableau de bord" in h.value for h in app.header)
    app.radio(key="main_navigation").set_value("Bilan des ventes").run()
    assert not app.exception
    assert any("Bilan des ventes" in h.value for h in app.header)
    assert any(m.label == "Chiffre d'affaires" and m.value == "10.00 €" for m in app.metric)
    assert any("Détail des ventes" in h.value for h in app.subheader)
    assert github.call_count == 3
    app.radio(key="main_navigation").set_value("Recherche & Ajout").run()
    assert not app.exception
    with patch("scryfall.search_card_autocomplete", return_value=["Example"]), \\
         patch("scryfall.get_card_by_name", return_value={
             "id": "test", "name": "Example", "oracle_id": "oracle",
             "set": "tst", "collector_number": "1"
         }), \\
         patch("scryfall.get_card_prints", return_value=[{
             "id": "test", "name": "Example", "set": "tst", "set_name": "Test",
             "collector_number": "1", "prices": {"eur": "2"}
         }]):
        app.text_input[0].set_value("Example").run()
        assert not app.exception
        assert any(b.label == "Ajouter à mon stock" for b in app.button)
        next(b for b in app.button if b.label == "Ajouter à mon stock").click().run()
        assert not app.exception
        assert any(card["scryfall_id"] == "test" for card in inventory.get_collection())
    app.radio(key="main_navigation").set_value("Nexus").run()
    assert not app.exception
    app.radio(key="main_navigation").set_value("Stock & Ventes").run()
    assert not app.exception
    assert any("Tableau de bord" in h.value for h in app.header)
"""
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            for name in ("DATABASE_URL", "MTG_DATABASE_URL", "GITHUB_ACTIONS", "DISCORD_WEBHOOK_URL"):
                env.pop(name, None)
            env["MTG_DATABASE_PATH"] = str(Path(directory) / "test.sqlite3")
            env["MTG_LAZY_NAVIGATION"] = "true"
            env["GITHUB_DISPATCH_TOKEN"] = "fake-token"
            env["PYTHONIOENCODING"] = "utf-8"
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                env=env, capture_output=True, text=True, timeout=90,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

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
            env.pop("MTG_LAZY_NAVIGATION", None)
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
