import tempfile
import unittest
from pathlib import Path

from sabad import db, services


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_db_path = db.DB_PATH
        db.DB_PATH = Path(self.temp.name) / "test.db"
        db.initialize()

    def tearDown(self):
        db.DB_PATH = self.original_db_path
        self.temp.cleanup()

    def create_product(self, barcode="10001", name="شیر", stock=10, buy=20000, sell=30000):
        return services.save_product({
            "barcode": barcode, "name": name, "category": "لبنیات",
            "unit": "عدد", "purchase_price": buy, "sale_price": sell,
            "stock": stock, "min_stock": 2
        })

    def test_sale_reduces_stock_and_records_total(self):
        pid = self.create_product()
        sale = services.record_sale([{"product_id": pid, "qty": 2}], discount=5000)
        self.assertEqual(sale["subtotal"], 60000)
        self.assertEqual(sale["total"], 55000)
        product = services.get_product_by_barcode("10001")
        self.assertEqual(product["stock"], 8)

    def test_sale_rejects_insufficient_stock_without_partial_changes(self):
        first = self.create_product("10001", "شیر", stock=10)
        second = self.create_product("10002", "پنیر", stock=1)
        with self.assertRaises(services.BusinessError):
            services.record_sale([
                {"product_id": first, "qty": 2},
                {"product_id": second, "qty": 3},
            ])
        self.assertEqual(services.get_product_by_barcode("10001")["stock"], 10)
        self.assertEqual(services.get_product_by_barcode("10002")["stock"], 1)
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 0)

    def test_purchase_increases_stock_and_updates_purchase_price(self):
        pid = self.create_product(stock=2)
        result = services.record_purchase(
            [{"product_id": pid, "qty": 5, "unit_cost": 22000}],
            supplier="تأمین‌کننده نمونه", invoice_no="INV-1"
        )
        self.assertEqual(result["total"], 110000)
        product = services.get_product_by_barcode("10001")
        self.assertEqual(product["stock"], 7)
        self.assertEqual(product["purchase_price"], 22000)

    def test_duplicate_barcode_is_rejected(self):
        self.create_product()
        with self.assertRaises(services.BusinessError):
            self.create_product(name="کالای تکراری")

    def test_financial_summary_uses_recorded_sale_costs_discounts_and_expenses(self):
        pid = self.create_product(stock=5, buy=20000, sell=30000)
        services.record_sale([{"product_id": pid, "qty": 2}], discount=5000)
        services.add_expense("حمل", 3000)
        summary = services.financial_summary()
        self.assertEqual(summary["sales_total"], 55000)
        self.assertEqual(summary["cost_of_goods"], 40000)
        self.assertEqual(summary["gross_margin"], 20000)
        self.assertEqual(summary["discounts"], 5000)
        self.assertEqual(summary["expenses"], 3000)
        self.assertEqual(summary["estimated_profit"], 12000)

    def test_expense_and_backup(self):
        self.create_product()
        services.add_expense("برق", 120000, "قبض ماهانه")
        backup = Path(self.temp.name) / "backup.db"
        services.backup_to(backup)
        self.assertTrue(backup.exists())
        import sqlite3
        with sqlite3.connect(backup) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM products").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
