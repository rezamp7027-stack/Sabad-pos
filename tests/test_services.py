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

    def test_full_sale_return_restores_stock_and_reverses_financial_summary(self):
        pid = self.create_product(stock=5, buy=20000, sell=30000)
        sale = services.record_sale([{"product_id": pid, "qty": 2}], discount=5000)
        self.assertEqual(services.get_product_by_barcode("10001")["stock"], 3)
        returned = services.return_sale(sale["id"], "کالای اشتباه")
        self.assertEqual(returned["amount"], 55000)
        self.assertEqual(services.get_product_by_barcode("10001")["stock"], 5)
        summary = services.financial_summary()
        self.assertEqual(summary["sales_total"], 0)
        self.assertEqual(summary["discounts"], 0)
        self.assertEqual(summary["cost_of_goods"], 0)
        self.assertEqual(summary["gross_margin"], 0)
        self.assertEqual(summary["returns_total"], 55000)
        with db.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM stock_movements WHERE kind='مرجوعی فروش'"
            ).fetchone()[0], 1)

    def test_duplicate_product_lines_cannot_oversell_stock(self):
        pid = self.create_product(stock=3)
        with self.assertRaises(services.BusinessError):
            services.record_sale([
                {"product_id": pid, "qty": 2},
                {"product_id": pid, "qty": 2},
            ])
        self.assertEqual(services.get_product_by_barcode("10001")["stock"], 3)
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 0)

    def test_dashboard_net_sales_reflects_same_day_full_return(self):
        pid = self.create_product(stock=5)
        sale = services.record_sale([{"product_id": pid, "qty": 1}])
        self.assertEqual(services.dashboard()["sales"], sale["total"])
        services.return_sale(sale["id"])
        self.assertEqual(services.dashboard()["sales"], 0)

    def test_sales_report_includes_return_status_and_unlimited_export(self):
        pid = self.create_product(stock=5)
        sale = services.record_sale([{"product_id": pid, "qty": 1}])
        services.return_sale(sale["id"])
        rows = services.sales_report(limit=None)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["receipt_no"], sale["receipt_no"])
        self.assertEqual(rows[0]["is_returned"], 1)
        self.assertEqual(len(services.sales_report(limit=1)), 1)

    def test_backup_rejects_active_database_path(self):
        with self.assertRaises(services.BusinessError):
            services.backup_to(db.DB_PATH)

    def test_sale_cannot_be_returned_twice(self):
        pid = self.create_product(stock=5)
        sale = services.record_sale([{"product_id": pid, "qty": 1}])
        services.return_sale(sale["id"])
        with self.assertRaises(services.BusinessError):
            services.return_sale(sale["id"])
        self.assertEqual(services.get_product_by_barcode("10001")["stock"], 5)
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sales_returns").fetchone()[0], 1)

    def test_supplier_payment_updates_balance_and_rejects_overpayment(self):
        pid = self.create_product(stock=0)
        services.record_purchase(
            [{"product_id": pid, "qty": 5, "unit_cost": 20000}],
            supplier="پخش نمونه", invoice_no="SUP-1"
        )
        result = services.add_supplier_payment("پخش نمونه", 70000, "پرداخت اول")
        self.assertEqual(result["balance"], 30000)
        balances = services.supplier_balances()
        self.assertEqual(len(balances), 1)
        self.assertEqual(balances[0]["purchased"], 100000)
        self.assertEqual(balances[0]["paid"], 70000)
        self.assertEqual(balances[0]["balance"], 30000)
        with self.assertRaises(services.BusinessError):
            services.add_supplier_payment("پخش نمونه", 30001)
        self.assertEqual(services.supplier_balances()[0]["balance"], 30000)

    def test_restore_rejects_unrelated_database_without_changing_live_data(self):
        self.create_product("10001", "شیر")
        unrelated = Path(self.temp.name) / "not-sabad.db"
        import sqlite3
        with sqlite3.connect(unrelated) as conn:
            conn.execute("CREATE TABLE random_data(value TEXT)")
            conn.execute("INSERT INTO random_data VALUES('nope')")
        with self.assertRaises(services.BusinessError):
            services.restore_from(unrelated)
        self.assertIsNotNone(services.get_product_by_barcode("10001"))

    def test_restore_backup_creates_safety_copy_and_restores_data(self):
        self.create_product("10001", "شیر")
        backup = Path(self.temp.name) / "restore-source.db"
        services.backup_to(backup)
        self.create_product("10002", "پنیر")
        safety = services.restore_from(backup)
        self.assertTrue(safety.exists())
        self.assertIsNotNone(services.get_product_by_barcode("10001"))
        self.assertIsNone(services.get_product_by_barcode("10002"))
        with db.connect() as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

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
