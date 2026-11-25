import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from database import market_data, storage
from services import inventory, phase_one_monitor


def _card(scryfall_id, **extra):
    return {"scryfall_id": scryfall_id, "name": f"Card {scryfall_id}", "set_code": "tst",
            "purchase_price": 0.5, "selling_price": 5.0, "status": "FOR_SALE", **extra}


def _data(card_id, price, legalities, **extra):
    return {"id": card_id, "prices": {"eur": price}, "legalities": legalities,
            "scryfall_uri": f"https://scryfall.com/card/{card_id}", **extra}


class LegalityWatchTests(unittest.TestCase):
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

    def analyze(self, batch):
        with patch.object(phase_one_monitor.scryfall, "get_cards_batch", return_value=batch), \
             patch.object(phase_one_monitor.requests, "post", return_value=Mock()) as post:
            result = phase_one_monitor.analyze_stock_prices()
        return result, post

    def legality_alerts(self):
        return [a for a in inventory.get_price_alerts() if a["kind"] == "LEGALITY_CHANGE"]

    def test_transitions_ignore_new_formats_and_unchanged_statuses(self):
        changes = phase_one_monitor.legality_transitions(
            {"modern": "legal", "commander": "legal", "pauper": "not_legal"},
            {"modern": "legal", "commander": "banned", "pauper": "not_legal", "oathbreaker": "legal"},
        )
        self.assertEqual(changes, {"commander": {"from": "legal", "to": "banned"}})

    def test_first_observation_is_a_silent_baseline_then_a_ban_alerts_once(self):
        inventory.save_collection([_card("a")])
        inventory.record_price_snapshot("a", 5.0, observed_at="2026-10-22T10:00:00+00:00")
        legal = {"commander": "legal", "modern": "legal"}
        self.analyze([_data("a", "5.00", legal)])
        self.assertEqual(self.legality_alerts(), [])
        with storage._database() as connection:
            self.assertEqual(market_data.get_legality_snapshots_with(connection, ["a"]), {"a": legal})

        banned = {"commander": "banned", "modern": "legal"}
        result, post = self.analyze([_data("a", "5.00", banned)])
        alerts = self.legality_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertIn("commander : légal → banni", alerts[0]["message"])
        self.assertIn("Ban", alerts[0]["verdict"])
        self.assertEqual(alerts[0]["source_url"], "https://scryfall.com/card/a")
        embed_text = post.call_args.kwargs["json"]["embeds"][0]["description"]
        self.assertIn("⚖️ Légalité (1)", embed_text)

        self.analyze([_data("a", "5.00", banned)])
        self.assertEqual(len(self.legality_alerts()), 1)

    def test_unban_alerts_and_enriches_the_price_alert_of_the_same_run(self):
        inventory.save_collection([_card("a")])
        inventory.record_price_snapshot("a", 5.0, observed_at="2026-10-22T10:00:00+00:00")
        self.analyze([_data("a", "5.00", {"commander": "banned"})])
        self.analyze([_data("a", "6.00", {"commander": "legal"})])
        legality = self.legality_alerts()[0]
        self.assertIn("Retour", legality["verdict"])
        price = next(a for a in inventory.get_price_alerts() if a["kind"] == "PRICE" and a["trend"] == "UP")
        self.assertIn("légalité", price["context"].lower())

    def test_cards_under_one_euro_are_recorded_but_never_alerted(self):
        inventory.save_collection([_card("bulk", selling_price=0.2)])
        inventory.record_price_snapshot("bulk", 0.3, observed_at="2026-10-22T10:00:00+00:00")
        self.analyze([_data("bulk", "0.30", {"commander": "legal"})])
        self.analyze([_data("bulk", "0.30", {"commander": "banned"})])
        self.assertEqual(self.legality_alerts(), [])
        with storage._database() as connection:
            self.assertEqual(
                market_data.get_legality_snapshots_with(connection, ["bulk"]), {"bulk": {"commander": "banned"}})

    def test_cards_without_legalities_and_sold_cards_are_ignored(self):
        inventory.save_collection([_card("a"), _card("sold", status="SOLD")])
        self.analyze([{"id": "a", "prices": {"eur": "5.00"}}])
        with storage._database() as connection:
            self.assertEqual(market_data.get_legality_snapshots_with(connection, ["a", "sold"]), {})

    def test_spoiler_events_respect_the_one_euro_floor(self):
        inventory.save_collection([_card("rich"), _card("cheap")])
        inventory.record_price_snapshot("rich", 4.0, observed_at="2026-10-22T10:00:00+00:00")
        inventory.record_price_snapshot("cheap", 0.4, observed_at="2026-10-22T10:00:00+00:00")
        scan = {"alerts": [
            {"id": f"spoiler:x:{key}", "category": "SPOILER_REPRINT", "card_name": f"Card {key}",
             "scryfall_id": key, "message": "Réimpression détectée."} for key in ("rich", "cheap")
        ], "errors": []}
        with patch.object(phase_one_monitor.spoiler_watcher, "scan_spoilers", return_value=scan):
            detected = phase_one_monitor.scan_stock_events()
        self.assertEqual([item["scryfall_id"] for item in detected], ["rich"])
        self.assertEqual([a["scryfall_id"] for a in inventory.get_price_alerts()], ["rich"])


if __name__ == "__main__":
    unittest.main()
