import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from database import storage
from services import inventory, phase_one_monitor


def alert(index, trend="STABLE"):
    return {
        "id": f"alert-{index}", "kind": "PRICE", "status": "pending",
        "scryfall_id": f"card-{index}", "foil": bool(index % 2),
        "card_name": f"Carte {index}", "set_code": "tst", "quantity": index + 1,
        "previous_price": 10.0, "current_price": 11.0, "change_pct": 10.0,
        "trend": trend, "created_at": "2026-11-13T10:00:00+00:00",
    }


class DiscordSummaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for patcher in (
            patch.object(storage, "DATABASE_PATH", Path(directory.name) / "test.sqlite3"),
            patch.object(storage, "DATABASE_URL", None),
            patch.dict(os.environ, {"DATABASE_URL": "", "MTG_DATABASE_URL": "",
                                    "GITHUB_ACTIONS": "", "DISCORD_WEBHOOK_URL": "https://discord.invalid/test"}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_many_alerts_fit_one_message_with_complete_attachment(self):
        alerts = [alert(i, ("UP", "DOWN", "STABLE")[i % 3]) for i in range(90)]
        inventory.save_price_alerts(alerts)
        positions = {(item["scryfall_id"], item["foil"]) for item in alerts}
        session = Mock()
        self.assertEqual(phase_one_monitor._deliver_pending_alerts(positions, session), 90)
        session.post.assert_called_once()
        kwargs = session.post.call_args.kwargs
        payload = json.loads(kwargs["data"]["payload_json"])
        self.assertIn("90 alerte(s)", payload["embeds"][0]["title"])
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        attachment = kwargs["files"]["files[0]"][1].decode("utf-8")
        for item in alerts:
            self.assertIn(f"• {item['card_name']} [", attachment)
        self.assertIn("Hausses (30)", attachment)
        self.assertIn("Baisses (30)", attachment)
        self.assertIn("Stagnations (30)", attachment)
        self.assertTrue(all(item["status"] == "sent" for item in inventory.get_price_alerts()))
        self.assertEqual(phase_one_monitor._deliver_pending_alerts(positions, session), 0)
        session.post.assert_called_once()

    def test_ambiguous_failure_keeps_unconfirmed_batch_and_resend_is_flagged(self):
        alerts = [alert(0), alert(1), alert(2)]
        inventory.save_price_alerts(alerts)
        session = Mock()
        session.post.side_effect = requests.Timeout("test")
        positions = {(item["scryfall_id"], item["foil"]) for item in alerts[:2]}
        self.assertEqual(phase_one_monitor._deliver_pending_alerts(positions, session), 0)
        saved = {item["id"]: item for item in inventory.get_price_alerts()}
        self.assertEqual({key: item["status"] for key, item in saved.items()},
                         {"alert-0": "sending", "alert-1": "sending", "alert-2": "cancelled"})
        first_lot = saved["alert-0"]["delivery_id"]
        self.assertEqual(saved["alert-0"]["delivery_attempts"], 1)
        session.post.side_effect = None
        response = Mock()
        response.json.return_value = {"id": "message-1"}
        session.post.return_value = response
        self.assertEqual(phase_one_monitor._deliver_pending_alerts(positions, session), 2)
        self.assertEqual(session.post.call_count, 2)
        embed = session.post.call_args.kwargs["json"]["embeds"][0]
        self.assertIn("Reprise après interruption", embed["description"])
        self.assertIn(first_lot, embed["description"])
        self.assertNotEqual(embed["footer"]["text"], f"Lot {first_lot}")
        final = {item["id"]: item for item in inventory.get_price_alerts()}
        self.assertEqual(final["alert-0"]["status"], "sent")
        self.assertEqual(final["alert-0"]["discord_message_id"], "message-1")
        self.assertEqual(final["alert-0"]["delivery_attempts"], 2)

    def test_claim_is_saved_before_posting_and_refusals_restore_state(self):
        item = alert(0)
        inventory.save_price_alerts([item])
        positions = {("card-0", False)}
        observed = []
        session = Mock()

        def refuse(*args, **kwargs):
            observed.append(inventory.get_price_alerts()[0]["status"])
            error = requests.HTTPError("test")
            raise error

        session.post.side_effect = refuse
        self.assertEqual(phase_one_monitor._deliver_pending_alerts(positions, session), 0)
        self.assertEqual(observed, ["sending"])
        self.assertEqual(inventory.get_price_alerts()[0]["status"], "pending")
        session.post.side_effect = requests.ConnectTimeout("test")
        self.assertEqual(phase_one_monitor._deliver_pending_alerts(positions, session), 0)
        self.assertEqual(inventory.get_price_alerts()[0]["status"], "pending")
        session.post.side_effect = requests.ReadTimeout("test")
        phase_one_monitor._deliver_pending_alerts(positions, session)
        unconfirmed = inventory.get_price_alerts()[0]
        self.assertEqual(unconfirmed["status"], "sending")
        session.post.side_effect = requests.HTTPError("test")
        self.assertEqual(phase_one_monitor._deliver_pending_alerts(positions, session), 0)
        restored = inventory.get_price_alerts()[0]
        self.assertEqual(restored["status"], "sending")
        self.assertEqual(restored["delivery_id"], unconfirmed["delivery_id"])

    def test_mixed_alerts_and_legacy_price_are_grouped(self):
        legacy = alert(0)
        legacy.pop("trend")
        target = {**alert(1), "kind": "TARGET_REACHED", "message": "Objectif atteint : Carte 1"}
        event = {**alert(2), "kind": "SPOILER_REPRINT", "message": "Réimpression détectée"}
        payload, attachment = phase_one_monitor._summary_payload([legacy, target, event])
        self.assertIsNone(attachment)
        description = payload["embeds"][0]["description"]
        self.assertIn("Hausses (1)", description)
        self.assertIn("Objectifs atteints (1)", description)
        self.assertIn("Veille du stock (1)", description)
        self.assertIn("Réimpression détectée", description)

    def test_oversized_attachment_is_not_sent(self):
        item = alert(0)
        item["card_name"] = "x" * (phase_one_monitor.DISCORD_ATTACHMENT_LIMIT + 1)
        inventory.save_price_alerts([item])
        session = Mock()
        self.assertEqual(phase_one_monitor._deliver_pending_alerts({("card-0", False)}, session), 0)
        session.post.assert_not_called()
        self.assertEqual(inventory.get_price_alerts()[0]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
