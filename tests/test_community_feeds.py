import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from database import storage
from services import community_feeds, inventory, phase_one_monitor, source_health, stock_advisor

NOW = datetime(2026, 11, 25, 18, 0, tzinfo=timezone.utc)
CHANNEL = "UC" + "a" * 22

GOLDFISH = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><published>2026-11-25T16:00:02Z</published>
    <link rel="alternate" type="text/html" href="https://www.mtggoldfish.com/articles/sol"/>
    <title>Why Sol Ring is spiking</title><summary>A &lt;b&gt;quick&lt;/b&gt; look at Solring prices</summary></entry>
  <entry><published>2026-09-01T16:00:02Z</published>
    <link rel="alternate" href="https://www.mtggoldfish.com/articles/old"/>
    <title>Old article about Lightning Bolt</title><summary>x</summary></entry>
  <entry><published>2026-11-24T16:00:02Z</published>
    <link rel="alternate" href="https://www.mtggoldfish.com/articles/other"/>
    <title>Commander podcast</title><summary>Mana Vault and Ox are discussed</summary></entry>
</feed>"""

YOUTUBE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
  <entry><link rel="alternate" href="https://www.youtube.com/watch?v=abc"/>
    <title>Top 10 Lightning Bolt reprints</title><published>2026-11-24T10:00:00+00:00</published>
    <media:group><media:description>All about bolt</media:description></media:group></entry>
</feed>"""

RSS = b"""<rss version="2.0"><channel><item><title>Counterspell tech</title>
<link>https://example.org/c</link><pubDate>Tue, 24 Nov 2026 10:00:00 +0000</pubDate>
<description>desc</description></item></channel></rss>"""

STOCK = [
    {"scryfall_id": "sol", "name": "Sol Ring"}, {"scryfall_id": "bolt", "name": "Lightning Bolt"},
    {"scryfall_id": "ox", "name": "Ox"}, {"scryfall_id": "cs", "name": "Counterspell"},
]


def response(content, status=200):
    reply = Mock(content=content, status_code=status)
    reply.raise_for_status.side_effect = None if status < 400 else requests.HTTPError(str(status))
    return reply


class DatabaseCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for patcher in (
            patch.object(storage, "DATABASE_PATH", Path(directory.name) / "test.sqlite3"),
            patch.object(storage, "DATABASE_URL", None),
            patch.dict(os.environ, {"DATABASE_URL": "", "MTG_DATABASE_URL": "", "GITHUB_ACTIONS": "",
                                    "MTG_YOUTUBE_CHANNEL_IDS": "", "DISCORD_WEBHOOK_URL": "https://discord.invalid/h"}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)


class ParsingTests(unittest.TestCase):
    def test_atom_rss_and_youtube_formats_and_age_filter(self):
        entries = community_feeds.parse_feed(GOLDFISH, now=NOW)
        self.assertEqual([e["url"] for e in entries],
                         ["https://www.mtggoldfish.com/articles/sol", "https://www.mtggoldfish.com/articles/other"])
        self.assertEqual(entries[0]["summary"], "A quick look at Solring prices")
        self.assertEqual(community_feeds.parse_feed(YOUTUBE, now=NOW)[0]["summary"], "All about bolt")
        rss = community_feeds.parse_feed(RSS, now=NOW)[0]
        self.assertEqual((rss["title"], rss["url"]), ("Counterspell tech", "https://example.org/c"))

    def test_invalid_and_oversized_feeds_raise_a_clean_error(self):
        with self.assertRaises(community_feeds.FeedError):
            community_feeds.parse_feed(b"<html>not xml", now=NOW)
        with self.assertRaises(community_feeds.FeedError):
            community_feeds.parse_feed(b"x" * (community_feeds.MAX_FEED_BYTES + 1), now=NOW)

    def test_mentions_match_whole_stock_names_only(self):
        entries = community_feeds.parse_feed(GOLDFISH, now=NOW)
        found = community_feeds.find_mentions(entries, STOCK, "mtggoldfish", "MTGGoldfish")
        self.assertEqual([m["scryfall_id"] for m in found], ["sol"])  # « Solring » et « Ox » (3 lettres) ignorés

    def test_channel_ids_are_validated_and_deduplicated(self):
        raw = f"{CHANNEL}, bad-id ; {CHANNEL},UC{'b' * 22}"
        self.assertEqual(community_feeds.youtube_channel_ids(raw), [CHANNEL, "UC" + "b" * 22])
        self.assertEqual(community_feeds.youtube_channel_ids(""), [])


class ScanTests(DatabaseCase):
    def test_polite_request_and_failure_isolation_with_health(self):
        session = Mock()

        def get(url, **kwargs):
            if "mtggoldfish" in url:
                return response(GOLDFISH)
            if CHANNEL in url:
                return response(YOUTUBE)
            raise requests.ConnectionError("down")

        session.get.side_effect = get
        result = community_feeds.scan_community(
            STOCK, session=session, youtube_ids=[CHANNEL, "UC" + "b" * 22], now=NOW)
        self.assertEqual(sorted(m["scryfall_id"] for m in result["mentions"]), ["bolt", "sol"])
        self.assertEqual(len(result["errors"]), 1)
        for call in session.get.call_args_list:
            self.assertIn("CarnetDeMana", call.kwargs["headers"]["User-Agent"])
            self.assertEqual(call.kwargs["timeout"], community_feeds.TIMEOUT_SECONDS)
        health = storage.get_source_health()
        self.assertTrue(health["mtggoldfish"]["succeeded"])
        self.assertFalse(health["youtube"]["succeeded"])

    def test_http_errors_are_reported_without_raising(self):
        session = Mock()
        session.get.return_value = response(b"", 429)
        result = community_feeds.scan_community(STOCK, session=session, youtube_ids=[], now=NOW)
        self.assertEqual(result["mentions"], [])
        self.assertIn("MTGGoldfish", result["errors"][0])
        self.assertFalse(storage.get_source_health()["mtggoldfish"]["succeeded"])
        self.assertNotIn("youtube", storage.get_source_health())

    def test_youtube_is_monitored_only_when_configured(self):
        self.assertNotIn("youtube", source_health.get_source_statuses())
        self.assertIn("mtggoldfish", source_health.get_source_statuses())
        with patch.dict(os.environ, {"MTG_YOUTUBE_CHANNEL_IDS": CHANNEL}):
            self.assertIn("youtube", source_health.get_source_statuses())


class AnalysisIntegrationTests(DatabaseCase):
    def card(self):
        return {"scryfall_id": "sol", "name": "Sol Ring", "set_code": "tst", "quantity": 1,
                "purchase_price": 1.0, "selling_price": 8.0, "status": "FOR_SALE"}

    def test_mentions_enrich_price_alert_context_without_creating_alerts(self):
        mention = [{"scryfall_id": "sol", "card_name": "Sol Ring", "source_label": "MTGGoldfish",
                    "title": "Why Sol Ring is spiking"}]
        advice = stock_advisor.advise(15.0, 0.0, mentions=mention)
        self.assertEqual(advice["kind"], "ISOLATED_SPIKE")
        self.assertIn("Mention récente", advice["context"])
        self.assertIn("MTGGoldfish", advice["context"])
        self.assertNotIn("Mention", stock_advisor.advise(15.0, 0.0)["context"])

        inventory.save_collection([self.card()])
        inventory.record_price_snapshot("sol", 5.0, observed_at="2026-10-22T10:00:00+00:00")
        with patch.object(phase_one_monitor.scryfall, "get_cards_batch",
                          return_value=[{"id": "sol", "prices": {"eur": "6.00"}}]), \
             patch.object(phase_one_monitor.requests, "post", return_value=Mock()):
            phase_one_monitor.analyze_stock_prices(mentions=mention)
        alerts = inventory.get_price_alerts()
        self.assertEqual([a["kind"] for a in alerts], ["PRICE"])
        self.assertIn("Why Sol Ring is spiking", alerts[0]["context"])

    def test_community_failure_never_breaks_the_analysis(self):
        inventory.save_collection([self.card()])
        with patch.object(community_feeds, "scan_community", side_effect=RuntimeError("boom")):
            self.assertEqual(phase_one_monitor.scan_community_mentions(), [])
        session = Mock()
        session.get.side_effect = requests.ConnectionError("down")
        self.assertEqual(phase_one_monitor.scan_community_mentions(session=session), [])
        self.assertFalse(storage.get_source_health()["mtggoldfish"]["succeeded"])
        with patch.object(phase_one_monitor, "scan_stock_events", return_value=[]), \
             patch.object(phase_one_monitor.scryfall, "get_cards_batch",
                          return_value=[{"id": "sol", "prices": {"eur": "5.00"}}]), \
             patch.object(phase_one_monitor.requests, "post", return_value=Mock()), \
             patch.object(phase_one_monitor.requests, "get", side_effect=requests.ConnectionError("down")):
            result = phase_one_monitor.run_analysis()
        self.assertEqual(result["community_mentions"], 0)
        self.assertEqual(result["snapshots_written"], 1)


if __name__ == "__main__":
    unittest.main()
