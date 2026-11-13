import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import storage
from services import inventory, stock_filters


class StockFilterTests(unittest.TestCase):
    def setUp(self):
        self.cards = [
            {"name": "Beta", "set_code": "abc", "collector_number": "12",
             "status": "FOR_SALE", "selling_price": 5, "quantity": 2,
             "language": "FR", "condition": "NM", "foil": True},
            {"name": "Alpha", "set_code": "xyz", "collector_number": "30",
             "status": "FOR_SALE", "selling_price": 2, "quantity": 1,
             "language": "EN", "condition": "EX", "foil": False},
            {"name": "Sold", "set_code": "abc", "status": "SOLD",
             "selling_price": 100, "quantity": 1},
        ]

    def test_combined_filters_preserve_original_index(self):
        result = stock_filters.filter_stock(
            self.cards, query="12", set_code="ABC", language="FR",
            condition="NM", foil="Foil", min_price=5, max_price=5,
        )
        self.assertEqual([index for index, _ in result], [0])
        self.assertEqual(stock_filters.filter_stock(self.cards, foil="Non foil")[0][0], 1)

    def test_price_and_name_sort_and_status(self):
        self.assertEqual(
            [i for i, _ in stock_filters.filter_stock(self.cards, sort_by="Prix de vente : décroissant")],
            [0, 1],
        )
        self.assertEqual(
            [i for i, _ in stock_filters.filter_stock(self.cards, sort_by="Prix de vente : croissant")],
            [1, 0],
        )
        self.assertEqual(
            [i for i, _ in stock_filters.filter_stock(self.cards, sort_by="Nom : A à Z")],
            [1, 0],
        )
        self.assertEqual([i for i, _ in stock_filters.filter_stock(self.cards, status="SOLD")], [2])

    def test_invalid_price_range_is_explicit(self):
        with self.assertRaises(ValueError):
            stock_filters.filter_stock(self.cards, min_price=10, max_price=5)


class SelectedSaleTests(unittest.TestCase):
    def test_sale_updates_selected_variant_and_rejects_stale_record(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(storage, "DATABASE_PATH", Path(directory) / "stock.sqlite3"), \
             patch.object(storage, "DATABASE_URL", None), \
             patch.dict(os.environ, {"DATABASE_URL": "", "MTG_DATABASE_URL": "", "GITHUB_ACTIONS": ""}):
            for language in ("FR", "EN"):
                inventory.add_card_to_collection(
                    scryfall_id="same-card", name="Card", set_code="tst", language=language,
                    purchase_price=1, selling_price=2,
                )
            cards = inventory.get_collection()
            self.assertTrue(inventory.mark_selected_card_as_sold(cards[1], 3))
            updated = inventory.get_collection()
            self.assertEqual(updated[0]["status"], "FOR_SALE")
            self.assertEqual(updated[1]["status"], "SOLD")
            self.assertEqual(updated[1]["actual_sale_price"], 3)
            self.assertFalse(inventory.mark_selected_card_as_sold(cards[1], 3))


if __name__ == "__main__":
    unittest.main()
