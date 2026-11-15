import unittest
from unittest.mock import Mock, patch

import requests
import scryfall


class ScryfallErrorTests(unittest.TestCase):
    @patch("scryfall.time.sleep")
    @patch("scryfall.requests.post")
    def test_batch_does_not_hide_failed_chunk(self, post, _sleep):
        good = Mock(status_code=200)
        good.json.return_value = {"data": [{"id": "one"}]}
        failed = Mock(status_code=503)
        failed.raise_for_status.side_effect = requests.HTTPError("503")
        post.side_effect = [good, failed]
        with self.assertRaises(requests.HTTPError):
            scryfall.get_cards_batch([str(index) for index in range(76)])
        self.assertEqual(post.call_args.kwargs["timeout"], 20)

    @patch("scryfall.time.sleep")
    @patch("scryfall.requests.get")
    def test_named_card_distinguishes_not_found_from_service_error(self, get, _sleep):
        get.return_value = Mock(status_code=404)
        self.assertIsNone(scryfall.get_card_by_name("missing"))
        get.return_value = Mock(status_code=503)
        get.return_value.raise_for_status.side_effect = requests.HTTPError("503")
        with self.assertRaises(requests.HTTPError):
            scryfall.get_card_by_name("test")
        self.assertEqual(get.call_args.kwargs["timeout"], 10)

    @patch("scryfall.time.sleep")
    @patch("scryfall.requests.post")
    def test_identifier_errors_are_not_reported_as_missing_cards(self, post, _sleep):
        post.return_value.raise_for_status.side_effect = requests.HTTPError("429")
        with self.assertRaises(requests.HTTPError):
            scryfall.get_cards_by_identifiers([{"name": "Test"}])
        self.assertEqual(post.call_args.kwargs["timeout"], 20)
