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
        post.side_effect = [good, failed, failed, failed]
        with self.assertRaises(requests.HTTPError):
            scryfall.get_cards_batch([str(index) for index in range(76)])
        self.assertEqual(post.call_args.kwargs["timeout"], 20)
        self.assertEqual(post.call_count, 1 + scryfall.MAX_ATTEMPTS)

    @patch("scryfall.time.sleep")
    @patch("scryfall.requests.get")
    def test_transient_errors_are_retried_with_retry_after_and_not_found_is_not(self, get, sleep):
        limited = Mock(status_code=429, headers={"Retry-After": "2"})
        ok = Mock(status_code=200)
        ok.json.return_value = {"data": ["Sol Ring"]}
        get.side_effect = [limited, requests.ConnectionError("reset"), ok]
        self.assertEqual(scryfall.search_card_autocomplete("sol"), ["Sol Ring"])
        self.assertEqual(get.call_count, 3)
        self.assertIn(2.0, [call.args[0] for call in sleep.call_args_list])
        get.reset_mock(side_effect=True)
        get.return_value = Mock(status_code=404)
        self.assertIsNone(scryfall.get_card_by_name("missing"))
        self.assertEqual(get.call_count, 1)

    @patch("scryfall.time.sleep")
    @patch("scryfall.requests.get")
    def test_retry_delay_is_capped_and_network_failures_surface(self, get, sleep):
        get.return_value = Mock(status_code=503, headers={"Retry-After": "9999"})
        get.return_value.raise_for_status.side_effect = requests.HTTPError("503")
        with self.assertRaises(requests.HTTPError):
            scryfall.get_card_prints("oracle")
        self.assertLessEqual(max(call.args[0] for call in sleep.call_args_list),
                             scryfall.MAX_RETRY_DELAY_SECONDS)
        get.reset_mock(side_effect=True)
        get.side_effect = requests.ConnectionError("down")
        with self.assertRaises(requests.ConnectionError):
            scryfall.get_card_by_name("test")
        self.assertEqual(get.call_count, scryfall.MAX_ATTEMPTS)

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
