import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from database import storage
from services import cardmarket_import, inventory, portfolio_history


HEADER = (
    "ArticleID;idProduct;Name;SetCode;Language;Condition;Foil;Price_EUR;Amount;delete\n"
)


def export_line(article="100", product="123", name="Carte", foil="Y", price="1,25", amount="2"):
    return f'"=""{article}""";"=""{product}""";{name};TST;Français;EX;{foil};"{price}";{amount};N\n'


def resolved(article="100", product="123", card_id="card-1", quantity=2):
    return {
        "cardmarket_article_id": article, "cardmarket_product_id": product,
        "scryfall_id": card_id, "name": "Card", "set_code": "tst",
        "collector_number": "1", "language": "FR", "condition": "EX",
        "foil": True, "quantity": quantity, "selling_price": 1.25,
    }


class CardmarketImportTests(unittest.TestCase):
    def test_detects_cardmarket_export_including_incomplete_headers(self):
        self.assertTrue(cardmarket_import.is_cardmarket_export("\ufeff\n" + HEADER + export_line()))
        self.assertTrue(cardmarket_import.is_cardmarket_export("articleid;idproduct;Name\n"))
        self.assertFalse(cardmarket_import.is_cardmarket_export("name,set,quantity\nSol Ring,c21,1"))

    def test_parse_excel_ids_blank_first_line_and_cardmarket_columns(self):
        rows = cardmarket_import.parse("\ufeff\n" + HEADER + export_line())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["article_id"], "100")
        self.assertEqual(rows[0]["product_id"], "123")
        self.assertEqual(rows[0]["selling_price"], 1.25)
        self.assertEqual(rows[0]["quantity"], 2)
        self.assertEqual(rows[0]["language"], "FR")
        self.assertTrue(rows[0]["foil"])

    def test_invalid_or_empty_input_is_rejected(self):
        for value in (HEADER, HEADER + export_line(amount="0"), HEADER + export_line(price="nan"),
                      HEADER + export_line() + export_line()):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cardmarket_import.parse(value)

    @patch("services.cardmarket_import.time.sleep")
    def test_resolve_deduplicates_products_and_marks_unknown(self, _sleep):
        rows = cardmarket_import.parse(HEADER + export_line() + export_line(article="101"))
        session = Mock()
        session.get.return_value = Mock(
            status_code=200,
            json=Mock(return_value={
                "id": "card-1", "cardmarket_id": 123, "name": "Card", "set": "tst",
                "collector_number": "1",
            }),
        )
        cards, errors = cardmarket_import.resolve(rows, session)
        self.assertEqual(errors, [])
        self.assertEqual(len(cards), 2)
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(
            session.get.call_args.args[0], "https://api.scryfall.com/cards/cardmarket/123"
        )
        self.assertEqual(cards[0]["name"], "Card")
        session.get.return_value.status_code = 404
        cards, errors = cardmarket_import.resolve(rows, session)
        self.assertEqual(errors, [])
        self.assertEqual(len(cards), 2)
        self.assertTrue(cards[0]["scryfall_resolution_pending"])
        self.assertEqual(cards[0]["scryfall_id"], "")
        self.assertEqual(cards[0]["name"], "Carte")

    @patch("services.cardmarket_import.time.sleep")
    def test_malformed_response_is_rejected(self, _sleep):
        rows = cardmarket_import.parse(HEADER + export_line())
        session = Mock()
        session.get.return_value = Mock(status_code=200, json=Mock(return_value={"id": "bad"}))
        cards, errors = cardmarket_import.resolve(rows, session)
        self.assertFalse(cards)
        self.assertTrue(errors)


class StockSynchronizationTests(unittest.TestCase):
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

    def test_update_preserves_cost_journal_sales_and_archives_absent_cards(self):
        old = {**resolved(), "status": "FOR_SALE", "purchase_price": 4,
               "date_added": "2026-01-01", "decision_journal": [{"notes": "thèse"}]}
        removed = {**resolved("101", "124", "card-2"), "status": "FOR_SALE",
                   "purchase_price": 2, "date_added": "2026-01-01"}
        sold = {**resolved("102", "125", "card-3"), "status": "SOLD",
                "purchase_price": 3, "actual_sale_price": 5, "date_sold": "2026-01-03"}
        inventory.save_collection([old, removed, sold])
        before = inventory.get_collection()
        result = storage.synchronize_cardmarket_stock(
            [resolved(quantity=3), resolved("103", "126", "card-4")],
            before, "2026-11-12T12:00:00+00:00",
        )
        self.assertEqual(result, {"matched": 1, "added": 1, "withdrawn": 1})
        after = inventory.get_collection()
        self.assertEqual(after[0]["quantity"], 3)
        self.assertEqual(after[0]["purchase_price"], 4)
        self.assertEqual(after[0]["decision_journal"], [{"notes": "thèse"}])
        self.assertEqual(after[1]["status"], "WITHDRAWN")
        self.assertFalse(portfolio_history._was_held(after[1], "2026-11-12"))
        self.assertEqual(after[2], sold)
        self.assertTrue(after[3]["purchase_price_unknown"])

    def test_large_stock_matches_by_article_in_any_order(self):
        cards = [
            {**resolved(str(index), str(index + 1000), f"card-{index}"),
             "status": "FOR_SALE", "purchase_price": index + 1}
            for index in range(300)
        ]
        inventory.save_collection(cards)
        before = inventory.get_collection()
        incoming = [
            resolved(str(index), str(index + 1000), f"card-{index}", quantity=3)
            for index in reversed(range(300))
        ]
        result = storage.synchronize_cardmarket_stock(incoming, before, "2026-11-12")
        self.assertEqual(result, {"matched": 300, "added": 0, "withdrawn": 0})
        after = inventory.get_collection()
        self.assertEqual([card["purchase_price"] for card in after], list(range(1, 301)))
        self.assertTrue(all(card["quantity"] == 3 for card in after))

    def test_stale_preview_and_duplicate_ids_abort_without_changing_stock(self):
        card = {**resolved(), "status": "FOR_SALE", "purchase_price": 4}
        inventory.save_collection([card])
        with self.assertRaisesRegex(ValueError, "changé"):
            storage.synchronize_cardmarket_stock([resolved()], [], "2026-11-12")
        before = inventory.get_collection()
        with self.assertRaisesRegex(ValueError, "double"):
            storage.synchronize_cardmarket_stock([resolved(), resolved()], before, "2026-11-12")
        self.assertEqual(inventory.get_collection(), before)

    def test_first_import_matches_only_unique_manual_variant(self):
        manual = {**resolved(), "status": "FOR_SALE", "purchase_price": 6}
        manual.pop("cardmarket_article_id")
        manual.pop("cardmarket_product_id")
        inventory.save_collection([manual])
        result = storage.synchronize_cardmarket_stock([resolved()], inventory.get_collection(), "2026-11-12")
        self.assertEqual(result["matched"], 1)
        self.assertEqual(inventory.get_collection()[0]["purchase_price"], 6)

    def test_error_after_first_update_rolls_back_whole_transaction(self):
        first = {**resolved(), "status": "FOR_SALE", "purchase_price": 1}
        second = {**resolved("101", "124", "card-2"), "status": "FOR_SALE", "purchase_price": 2}
        inventory.save_collection([first, second])
        before = inventory.get_collection()
        with self.assertRaisesRegex(ValueError, "Produit modifié"):
            storage.synchronize_cardmarket_stock(
                [resolved(quantity=7), resolved("101", "999", "card-2")], before, "2026-11-12"
            )
        self.assertEqual(inventory.get_collection(), before)

    def test_unresolved_article_can_be_resolved_in_later_import(self):
        unresolved = {**resolved(), "scryfall_id": "", "scryfall_resolution_pending": True}
        storage.synchronize_cardmarket_stock([unresolved], [], "2026-11-12")
        before = inventory.get_collection()
        incoming = {**resolved(), "scryfall_resolution_pending": False}
        result = storage.synchronize_cardmarket_stock([incoming], before, "2026-11-13")
        self.assertEqual(result["matched"], 1)
        self.assertEqual(inventory.get_collection()[0]["scryfall_id"], "card-1")
        self.assertFalse(inventory.get_collection()[0]["scryfall_resolution_pending"])


if __name__ == "__main__":
    unittest.main()
