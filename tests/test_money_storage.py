import os
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from database import storage
from database.healthcheck import check_database
from services import inventory


class CentAmountTests(unittest.TestCase):
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

    def test_amounts_are_rounded_half_up_to_the_cent_and_bounded(self):
        for value, expected in ((0.145, 0.15), (2.675, 2.68), (1.005, 1.01), (1, 1.0),
                                ("3.999", 4.0), (0.004, 0.0), (-0.0, 0.0), (999999999.995, 1000000000.0)):
            with self.subTest(value=value):
                result = storage.validate_monetary_amount(value, "Montant")
                self.assertEqual(result, expected)
        self.assertEqual(str(storage.validate_monetary_amount(-0.0, "Montant")), "0.0")
        for value in (-0.01, float("nan"), float("inf"), "abc", None, 1000000000.01, 1e300):
            with self.subTest(value=value), self.assertRaises(ValueError):
                storage.validate_monetary_amount(value, "Montant")

    def test_cent_values_round_trip_exactly_through_float_storage(self):
        for cents in list(range(0, 1_000_000)) + list(range(1_000_000, 100_000_000_001, 99_991)):
            exact = Decimal(cents) / 100
            if Decimal(repr(float(exact))) != exact:
                self.fail(f"{cents} centimes ne survivent pas Ã  l'aller-retour float")

    def test_inventory_columns_and_payload_store_the_same_cent_amounts(self):
        inventory.add_card_to_collection(
            scryfall_id="a", name="A", set_code="tst", purchase_price=0.145, selling_price=2.675,
        )
        card = inventory.get_collection()[0]
        self.assertEqual((card["purchase_price"], card["selling_price"]), (0.15, 2.68))
        with storage._database() as connection:
            row = connection.execute("SELECT purchase_price, selling_price FROM inventory").fetchone()
        self.assertEqual((row["purchase_price"], row["selling_price"]), (0.15, 2.68))
        self.assertTrue(inventory.update_card(card, 1, 0.333, 4.005, 9.994))
        updated = inventory.get_collection()[0]
        self.assertEqual((updated["purchase_price"], updated["selling_price"], updated["target_price"]),
                         (0.33, 4.01, 9.99))
        self.assertTrue(inventory.mark_selected_card_as_sold(updated, 7.777))
        self.assertEqual(inventory.get_collection()[0]["actual_sale_price"], 7.78)
        self.assertEqual(check_database()["inconsistent_inventory_entries"], [])
        storage.record_price_snapshot("a", 1.005, observed_at="2026-10-10")
        self.assertEqual(storage.get_price_history()["a"]["2026-10-10"]["eur"], 1.01)

    def test_decimal_helpers_keep_money_exact(self):
        from modules import market_analyzer
        from services import alert_triage, deck_importer

        collection = [{"name": name, "status": "FOR_SALE", "selling_price": price}
                      for name, price in (("A", 0.1), ("B", 0.2), ("C", 9.99))]
        lots = market_analyzer.suggest_exchange_lot(collection, 0.3, max_cards=2)
        self.assertEqual((lots[0]["cards"], lots[0]["difference"], lots[0]["value"]), (["A", "B"], 0.0, 0.3))
        with patch("scryfall.get_card_by_name", return_value={"prices": {"eur": "0.10"}}):
            analysis = deck_importer.analyze_decklist([{"name": "Missing", "quantity": 3}], [])
        self.assertEqual(analysis["estimated_acquisition_cost_eur"], 0.3)
        ranked = alert_triage.prioritize(
            [{"id": "x", "kind": "PRICE", "scryfall_id": "s", "previous_price": 0.1, "current_price": 0.3}],
            [{"scryfall_id": "s", "status": "FOR_SALE", "quantity": 3}],
            {"s": {"2026-10-10": {"eur": 0.3}}}, today=date(2026, 10, 10),
        )
        self.assertEqual(ranked[0]["impact_eur"], 0.6)
        self.assertEqual(ranked[0]["portfolio_share_pct"], 100.0)

    def test_healthcheck_reports_existing_sub_cent_amounts_without_changing_them(self):
        inventory.add_card_to_collection(
            scryfall_id="a", name="A", set_code="tst", purchase_price=1, selling_price=2,
        )
        storage.record_price_snapshot("a", 2, observed_at="2026-10-10")
        with storage._database() as connection:
            connection.execute("UPDATE inventory SET selling_price = 2.005")
            connection.execute("UPDATE price_snapshots SET price_eur = 2.0049")
        report = check_database()["sub_cent_amounts"]
        self.assertEqual({name: len(ids) for name, ids in report.items()},
                         {"inventory": 1, "price_snapshots": 1})
        with storage._database() as connection:
            self.assertEqual(connection.execute("SELECT selling_price FROM inventory").fetchone()[0], 2.005)


if __name__ == "__main__":
    unittest.main()
