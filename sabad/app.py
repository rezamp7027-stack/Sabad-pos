from __future__ import annotations

import sys
import csv
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QTabWidget, QLabel, QPushButton, QLineEdit, QSpinBox, QDoubleSpinBox,
    QComboBox, QTableWidget, QTableWidgetItem, QMessageBox, QDialog,
    QDialogButtonBox, QInputDialog, QFileDialog, QHeaderView
)

from .db import initialize
from . import services as svc
from .printing import receipt_text, print_text, save_barcode_png
from .purchases_ui import PurchaseDialog


def money(value):
    return f"{int(value):,}"


def cell(value):
    item = QTableWidgetItem(str(value))
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return item


def setup_table(table):
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().setVisible(False)


class ProductDialog(QDialog):
    def __init__(self, parent=None, product=None):
        super().__init__(parent)
        self.setWindowTitle("اطلاعات کالا")
        self.setMinimumWidth(430)
        self.product = product
        form = QFormLayout(self)
        self.barcode = QLineEdit()
        self.name = QLineEdit()
        self.category = QLineEdit()
        self.unit = QComboBox()
        self.unit.addItems(["عدد", "کیلوگرم", "گرم", "لیتر", "بسته", "کارتن", "متر"])
        self.buy = QSpinBox()
        self.sell = QSpinBox()
        self.stock = QDoubleSpinBox()
        self.stock.setDecimals(3)
        self.stock.setRange(0, 100000000)
        self.min_stock = QDoubleSpinBox()
        self.min_stock.setDecimals(3)
        self.min_stock.setRange(0, 100000000)
        for box in (self.buy, self.sell):
            box.setRange(0, 2000000000)
            box.setSuffix(" تومان")
        form.addRow("بارکد", self.barcode)
        form.addRow("نام کالا", self.name)
        form.addRow("دسته‌بندی", self.category)
        form.addRow("واحد", self.unit)
        form.addRow("قیمت خرید", self.buy)
        form.addRow("قیمت فروش", self.sell)
        form.addRow("موجودی اولیه", self.stock)
        form.addRow("حد هشدار موجودی", self.min_stock)
        if product:
            self.barcode.setText(product["barcode"])
            self.name.setText(product["name"])
            self.category.setText(product["category"])
            self.unit.setCurrentText(product["unit"])
            self.buy.setValue(product["purchase_price"])
            self.sell.setValue(product["sale_price"])
            self.stock.setValue(product["stock"])
            self.stock.setEnabled(False)
            self.min_stock.setValue(product["min_stock"])
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def data(self):
        return {
            "barcode": self.barcode.text(), "name": self.name.text(),
            "category": self.category.text(), "unit": self.unit.currentText(),
            "purchase_price": self.buy.value(), "sale_price": self.sell.value(),
            "stock": self.stock.value(), "min_stock": self.min_stock.value()
        }


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("سبد | مدیریت سوپرمارکت")
        self.resize(1180, 760)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.cart = {}
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.build_dashboard()
        self.build_products()
        self.build_pos()
        self.build_reports()
        self.build_expenses()
        self.statusBar().showMessage("آماده به کار")
        self.refresh_all()

    def table(self, headers):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        setup_table(t)
        return t

    def build_dashboard(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("داشبورد فروشگاه")
        title.setStyleSheet("font-size:22px;font-weight:700;padding:12px")
        layout.addWidget(title)
        self.metrics = {}
        names = [
            ("sales", "فروش امروز"), ("receipts", "تعداد فاکتور امروز"),
            ("products", "تعداد کالا"), ("stock_value", "ارزش موجودی به قیمت خرید"),
            ("low_stock", "کالاهای کم‌موجود"), ("expenses", "هزینه امروز")
        ]
        row = QHBoxLayout()
        for key, label in names:
            box = QWidget()
            b = QVBoxLayout(box)
            caption = QLabel(label)
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value = QLabel("۰")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value.setStyleSheet("font-size:20px;font-weight:700;padding:16px;border:1px solid #d7dce2;border-radius:10px")
            b.addWidget(caption)
            b.addWidget(value)
            self.metrics[key] = value
            row.addWidget(box)
        layout.addLayout(row)
        layout.addStretch()
        self.tabs.addTab(page, "داشبورد")

    def build_products(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        tools = QHBoxLayout()
        self.product_search = QLineEdit()
        self.product_search.setPlaceholderText("جست‌وجوی نام، دسته‌بندی یا بارکد")
        self.product_search.textChanged.connect(self.refresh_products)
        tools.addWidget(self.product_search)
        for label, callback in [
            ("کالای جدید", self.new_product), ("ویرایش", self.edit_product),
            ("ثبت خرید", self.open_purchase), ("اصلاح موجودی", self.stock_adjust),
            ("ساخت برچسب بارکد", self.make_label)
        ]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            tools.addWidget(button)
        layout.addLayout(tools)
        self.products_table = self.table([
            "شناسه", "بارکد", "نام کالا", "دسته‌بندی", "واحد",
            "خرید (تومان)", "فروش (تومان)", "موجودی", "حد هشدار"
        ])
        layout.addWidget(self.products_table)
        self.tabs.addTab(page, "کالاها و موجودی")

    def build_pos(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        scan_row = QHBoxLayout()
        self.scan = QLineEdit()
        self.scan.setPlaceholderText("بارکد را اسکن کنید و Enter بزنید")
        self.scan.returnPressed.connect(self.add_scanned)
        scan_row.addWidget(self.scan)
        add = QPushButton("افزودن کالا")
        add.clicked.connect(self.add_scanned)
        scan_row.addWidget(add)
        layout.addLayout(scan_row)
        self.cart_table = self.table(["بارکد", "نام کالا", "تعداد", "قیمت واحد", "جمع ردیف"])
        layout.addWidget(self.cart_table)
        bottom = QHBoxLayout()
        self.discount = QSpinBox()
        self.discount.setRange(0, 2000000000)
        self.discount.setSuffix(" تومان تخفیف")
        self.discount.valueChanged.connect(self.refresh_cart)
        self.payment = QComboBox()
        self.payment.addItems(["نقدی", "کارت‌خوان", "کارت به کارت", "اعتباری"])
        self.total_label = QLabel("قابل پرداخت: ۰ تومان")
        self.total_label.setStyleSheet("font-size:20px;font-weight:700")
        remove = QPushButton("حذف ردیف")
        remove.clicked.connect(self.remove_cart_item)
        edit_qty = QPushButton("تغییر تعداد")
        edit_qty.clicked.connect(self.edit_cart_quantity)
        edit_price = QPushButton("تغییر قیمت")
        edit_price.clicked.connect(self.edit_cart_price)
        clear = QPushButton("خالی کردن سبد")
        clear.clicked.connect(self.clear_cart)
        checkout = QPushButton("ثبت نهایی فروش")
        checkout.setStyleSheet("font-weight:700;padding:10px")
        checkout.clicked.connect(self.checkout)
        for widget in (self.discount, self.payment, edit_qty, edit_price, remove, clear, checkout):
            bottom.addWidget(widget)
        layout.addLayout(bottom)
        layout.addWidget(self.total_label)
        self.tabs.addTab(page, "صندوق فروش")

    def build_reports(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        bar = QHBoxLayout()
        refresh = QPushButton("به‌روزرسانی گزارش")
        refresh.clicked.connect(self.refresh_reports)
        backup = QPushButton("پشتیبان‌گیری")
        backup.clicked.connect(self.backup)
        restore = QPushButton("بازیابی پشتیبان")
        restore.clicked.connect(self.restore_backup)
        returned = QPushButton("مرجوعی کامل فاکتور")
        returned.clicked.connect(self.return_selected_sale)
        partial_returned = QPushButton("مرجوعی جزئی")
        partial_returned.clicked.connect(self.partial_return_selected_sale)
        export = QPushButton("خروجی CSV فروش‌ها")
        export.clicked.connect(self.export_sales_csv)
        bar.addWidget(refresh)
        bar.addWidget(returned)
        bar.addWidget(partial_returned)
        bar.addWidget(export)
        bar.addWidget(backup)
        bar.addWidget(restore)
        bar.addStretch()
        layout.addLayout(bar)
        self.financial_summary_label = QLabel("در حال محاسبه گزارش مالی...")
        self.financial_summary_label.setWordWrap(True)
        self.financial_summary_label.setStyleSheet("font-size:14px;font-weight:600;padding:10px;background:white;border:1px solid #d8dde5;border-radius:8px")
        layout.addWidget(self.financial_summary_label)
        layout.addWidget(QLabel("آخرین فروش‌ها"))
        self.report_table = self.table(["رسید", "تاریخ", "جمع", "تخفیف", "قابل پرداخت", "روش پرداخت", "وضعیت"])
        layout.addWidget(self.report_table)
        layout.addWidget(QLabel("کالاهای کم‌موجود"))
        self.low_table = self.table(["بارکد", "نام کالا", "موجودی", "حد هشدار"])
        layout.addWidget(self.low_table)
        self.tabs.addTab(page, "گزارش و پشتیبان")

    def build_expenses(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.exp_title = QLineEdit()
        self.exp_title.setPlaceholderText("مثلاً اجاره، برق یا حمل‌ونقل")
        self.exp_amount = QSpinBox()
        self.exp_amount.setRange(1, 2000000000)
        self.exp_amount.setSuffix(" تومان")
        self.exp_note = QLineEdit()
        form.addRow("عنوان هزینه", self.exp_title)
        form.addRow("مبلغ", self.exp_amount)
        form.addRow("توضیحات", self.exp_note)
        layout.addLayout(form)
        save = QPushButton("ثبت هزینه")
        save.clicked.connect(self.expense_save)
        layout.addWidget(save)
        layout.addWidget(QLabel("پرداخت به تأمین‌کننده"))
        supplier_form = QFormLayout()
        self.supplier_name = QLineEdit()
        self.supplier_name.setPlaceholderText("نام دقیق تأمین‌کننده مطابق فاکتور خرید")
        self.supplier_payment_amount = QSpinBox()
        self.supplier_payment_amount.setRange(1, 2000000000)
        self.supplier_payment_amount.setSuffix(" تومان")
        self.supplier_payment_note = QLineEdit()
        supplier_form.addRow("تأمین‌کننده", self.supplier_name)
        supplier_form.addRow("مبلغ پرداخت", self.supplier_payment_amount)
        supplier_form.addRow("توضیحات", self.supplier_payment_note)
        layout.addLayout(supplier_form)
        pay = QPushButton("ثبت پرداخت تأمین‌کننده")
        pay.clicked.connect(self.supplier_payment_save)
        layout.addWidget(pay)
        layout.addWidget(QLabel("مانده حساب تأمین‌کنندگان"))
        self.supplier_table = self.table(["تأمین‌کننده", "جمع خرید", "پرداخت‌شده", "مانده بدهی"])
        layout.addWidget(self.supplier_table)
        layout.addStretch()
        self.tabs.addTab(page, "هزینه‌ها و تأمین‌کنندگان")

    def notify_error(self, error):
        QMessageBox.warning(self, "عملیات انجام نشد", str(error))

    def refresh_all(self):
        self.refresh_dashboard()
        self.refresh_products()
        self.refresh_reports()

    def refresh_dashboard(self):
        data = svc.dashboard()
        for key, value in data.items():
            suffix = " تومان" if key in ("sales", "stock_value", "expenses") else ""
            self.metrics[key].setText(money(value) + suffix)

    def refresh_products(self, *_):
        rows = svc.list_products(self.product_search.text())
        self.products_table.setRowCount(len(rows))
        for r, p in enumerate(rows):
            values = [p["id"], p["barcode"], p["name"], p["category"], p["unit"],
                      money(p["purchase_price"]), money(p["sale_price"]), p["stock"], p["min_stock"]]
            for col, value in enumerate(values):
                self.products_table.setItem(r, col, cell(value))

    def selected_product(self):
        row = self.products_table.currentRow()
        if row < 0:
            return None
        pid = int(self.products_table.item(row, 0).text())
        return next((p for p in svc.list_products("") if p["id"] == pid), None)

    def new_product(self):
        dialog = ProductDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                svc.save_product(dialog.data())
                self.refresh_all()
                self.statusBar().showMessage("کالا ثبت شد", 4000)
            except Exception as exc:
                self.notify_error(exc)

    def edit_product(self):
        product = self.selected_product()
        if not product:
            return self.notify_error("ابتدا یک کالا را انتخاب کنید.")
        dialog = ProductDialog(self, product)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                svc.save_product(dialog.data(), product["id"])
                self.refresh_all()
            except Exception as exc:
                self.notify_error(exc)

    def open_purchase(self):
        dialog = PurchaseDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh_all()
            self.statusBar().showMessage("فاکتور خرید ثبت شد و موجودی به‌روزرسانی شد", 5000)

    def stock_adjust(self):
        product = self.selected_product()
        if not product:
            return self.notify_error("ابتدا یک کالا را انتخاب کنید.")
        qty, ok = QInputDialog.getDouble(
            self, "اصلاح موجودی",
            f"مقدار تغییر برای {product['name']} (عدد منفی برای کاهش):",
            0, -10000000, 10000000, 3
        )
        if ok and qty:
            note, accepted = QInputDialog.getText(self, "دلیل اصلاح", "دلیل اصلاح موجودی:")
            if accepted:
                try:
                    svc.adjust_stock(product["id"], qty, "اصلاح موجودی", note)
                    self.refresh_all()
                except Exception as exc:
                    self.notify_error(exc)

    def make_label(self):
        product = self.selected_product()
        if not product:
            return self.notify_error("ابتدا یک کالا را انتخاب کنید.")
        try:
            path = save_barcode_png(product["barcode"], product["name"])
            QMessageBox.information(
                self, "برچسب ساخته شد",
                f"تصویر بارکد ساخته شد:\n{path}\nاندازه و کالیبراسیون چاپگر باید جداگانه تنظیم شود."
            )
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Exception as exc:
            self.notify_error(exc)

    def add_scanned(self):
        barcode = self.scan.text().strip()
        if not barcode:
            return
        product = svc.get_product_by_barcode(barcode)
        self.scan.clear()
        if not product:
            QMessageBox.information(self, "کالا پیدا نشد", "این بارکد ثبت نشده است. ابتدا کالا را تعریف کنید.")
            self.scan.setFocus()
            return
        pid = product["id"]
        if pid in self.cart:
            self.cart[pid]["qty"] += 1
        else:
            self.cart[pid] = {
                "product_id": pid, "barcode": product["barcode"], "name": product["name"],
                "qty": 1, "unit_price": product["sale_price"], "stock": product["stock"]
            }
        self.refresh_cart()
        self.scan.setFocus()

    def refresh_cart(self, *_):
        if not hasattr(self, "cart_table"):
            return
        self.cart_table.setRowCount(len(self.cart))
        subtotal = 0
        for row, item in enumerate(self.cart.values()):
            line = round(item["qty"] * item["unit_price"])
            subtotal += line
            values = [item["barcode"], item["name"], f"{item['qty']:g}",
                      money(item["unit_price"]), money(line)]
            for col, value in enumerate(values):
                self.cart_table.setItem(row, col, cell(value))
        self.discount.setMaximum(subtotal)
        discount = min(self.discount.value(), subtotal)
        self.total_label.setText(f"قابل پرداخت: {money(max(0, subtotal-discount))} تومان")

    def remove_cart_item(self):
        row = self.cart_table.currentRow()
        if row >= 0:
            self.cart.pop(list(self.cart.keys())[row], None)
            self.refresh_cart()

    def edit_cart_quantity(self):
        row = self.cart_table.currentRow()
        if row < 0:
            return self.notify_error("ابتدا یک ردیف از سبد را انتخاب کنید.")
        item = self.cart[list(self.cart.keys())[row]]
        qty, ok = QInputDialog.getDouble(
            self, "تغییر تعداد", f"تعداد برای {item['name']}:",
            item["qty"], 0.001, 10000000, 3
        )
        if ok:
            item["qty"] = qty
            self.refresh_cart()

    def edit_cart_price(self):
        row = self.cart_table.currentRow()
        if row < 0:
            return self.notify_error("ابتدا یک ردیف از سبد را انتخاب کنید.")
        item = self.cart[list(self.cart.keys())[row]]
        price, ok = QInputDialog.getInt(
            self, "تغییر قیمت", f"قیمت هر واحد برای {item['name']} (تومان):",
            int(item["unit_price"]), 0, 2000000000
        )
        if ok:
            item["unit_price"] = price
            self.refresh_cart()

    def clear_cart(self):
        self.cart.clear()
        self.discount.setValue(0)
        self.refresh_cart()

    def checkout(self):
        if not self.cart:
            return self.notify_error("سبد فروش خالی است.")
        answer = QMessageBox.question(self, "تأیید فروش", "فروش ثبت و موجودی کالا کم شود؟")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            sale = svc.record_sale(list(self.cart.values()), self.discount.value(), self.payment.currentText())
            self.clear_cart()
            self.refresh_all()
            self.statusBar().showMessage(f"فروش ثبت شد: {sale['receipt_no']}", 6000)
            answer = QMessageBox.question(self, "فروش ثبت شد", "رسید چاپ شود؟")
            if answer == QMessageBox.StandardButton.Yes:
                print_text(self, receipt_text(sale))
        except Exception as exc:
            self.notify_error(exc)


    def partial_return_selected_sale(self):
        row = self.report_table.currentRow()
        if row >= 0:
            receipt = self.report_table.item(row, 0).text()
        else:
            receipt, accepted = QInputDialog.getText(
                self, "مرجوعی جزئی", "شماره رسید فاکتور را وارد کنید:"
            )
            if not accepted or not receipt.strip():
                return
            receipt = receipt.strip()
        try:
            lines = svc.sale_returnable_items(receipt)
        except Exception as exc:
            return self.notify_error(exc)
        if not lines:
            return self.notify_error("اقلام فاکتور پیدا نشدند.")
        dialog = QDialog(self)
        dialog.setWindowTitle(f"مرجوعی جزئی | {receipt}")
        dialog.resize(820, 480)
        dialog.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            "برای هر قلم، مقدار مرجوعی را وارد کنید. صفر یعنی این قلم مرجوع نمی‌شود."
        ))
        table = self.table(["کالا", "بارکد", "تعداد فاکتور", "قابل مرجوعی", "مقدار مرجوعی"])
        widgets = []
        table.setRowCount(len(lines))
        for r, line in enumerate(lines):
            for col, value in enumerate([
                line["product_name"], line["barcode"], f'{line["qty"]:g}',
                f'{line["remaining_qty"]:g}'
            ]):
                table.setItem(r, col, cell(value))
            qty = QDoubleSpinBox()
            qty.setDecimals(3)
            qty.setRange(0, float(line["remaining_qty"]))
            qty.setSingleStep(1)
            qty.setValue(0)
            table.setCellWidget(r, 4, qty)
            widgets.append((line["sale_item_id"], qty))
        layout.addWidget(table)
        reason = QLineEdit()
        reason.setPlaceholderText("دلیل مرجوعی (اختیاری)")
        layout.addWidget(reason)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("ثبت مرجوعی")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        quantities = [
            {"sale_item_id": item_id, "qty": qty.value()}
            for item_id, qty in widgets if qty.value() > 0
        ]
        if not quantities:
            return self.notify_error("مقدار مرجوعی را وارد نکردید.")
        answer = QMessageBox.question(
            self, "تأیید مرجوعی جزئی",
            f"مرجوعی ثبت شود؟\nرسید: {receipt}\n"
            "موجودی اقلام انتخاب‌شده افزایش می‌یابد و این عملیات باید با دقت انجام شود."
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = svc.return_sale_items_by_receipt(receipt, quantities, reason.text())
            self.refresh_all()
            QMessageBox.information(
                self, "مرجوعی ثبت شد",
                f"رسید: {result['receipt_no']}\n"
                f"مبلغ بازپرداخت ثبت‌شده: {money(result['amount'])} تومان\n"
                "موجودی اقلام انتخاب‌شده به انبار برگشت."
            )
        except Exception as exc:
            self.notify_error(exc)

    def return_selected_sale(self):
        row = self.report_table.currentRow()
        if row >= 0:
            receipt = self.report_table.item(row, 0).text()
        else:
            receipt, accepted = QInputDialog.getText(
                self, "مرجوعی فاکتور", "شماره رسید فاکتور را وارد کنید:"
            )
            if not accepted or not receipt.strip():
                return
            receipt = receipt.strip()
        answer = QMessageBox.question(
            self, "تأیید مرجوعی",
            f"کل فاکتور {receipt} مرجوع شود؟ مبلغ فاکتور به‌عنوان مرجوعی ثبت و همه کالاها به موجودی برمی‌گردند. این عملیات قابل برگشت خودکار نیست."
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        reason, accepted = QInputDialog.getText(
            self, "دلیل مرجوعی", "دلیل مرجوعی (اختیاری):"
        )
        if not accepted:
            return
        try:
            result = svc.return_sale_by_receipt(receipt, reason or "مرجوعی کامل فاکتور")
            self.refresh_all()
            QMessageBox.information(
                self, "مرجوعی ثبت شد",
                f"فاکتور {result['receipt_no']} مرجوع شد.\n"
                f"مبلغ مرجوعی: {money(result['amount'])} تومان\n"
                "موجودی تمام اقلام به انبار برگشت."
            )
        except Exception as exc:
            self.notify_error(exc)

    def refresh_reports(self):
        if not hasattr(self, "report_table"):
            return
        summary = svc.financial_summary()
        self.financial_summary_label.setText(
            f"فروش خالص: {money(summary['sales_total'])} تومان  |  "
            f"بهای تمام‌شده کالای فروخته‌شده: {money(summary['cost_of_goods'])} تومان  |  "
            f"حاشیه سود پیش از تخفیف و هزینه: {money(summary['gross_margin'])} تومان  |  "
            f"تخفیف خالص: {money(summary['discounts'])} تومان  |  "
            f"مرجوعی‌ها: {money(summary['returns_total'])} تومان  |  "
            f"هزینه‌های ثبت‌شده: {money(summary['expenses'])} تومان  |  "
            f"سود تقریبی پس از تخفیف، مرجوعی و هزینه: {money(summary['estimated_profit'])} تومان"
        )
        sales = svc.sales_report()
        self.report_table.setRowCount(len(sales))
        for row, sale in enumerate(sales):
            values = [sale["receipt_no"], sale["created_at"], money(sale["subtotal"]),
                      money(sale["discount"]), money(sale["total"]), sale["payment_method"],
                      "مرجوع‌شده" if sale["is_returned"] else "فروش"]
            for col, value in enumerate(values):
                self.report_table.setItem(row, col, cell(value))
        low = svc.low_stock_products()
        self.low_table.setRowCount(len(low))
        for row, product in enumerate(low):
            for col, value in enumerate([product["barcode"], product["name"], product["stock"], product["min_stock"]]):
                self.low_table.setItem(row, col, cell(value))
        if hasattr(self, "supplier_table"):
            balances = svc.supplier_balances()
            self.supplier_table.setRowCount(len(balances))
            for row, supplier in enumerate(balances):
                values = [supplier["supplier"], money(supplier["purchased"]),
                          money(supplier["paid"]), money(supplier["balance"])]
                for col, value in enumerate(values):
                    self.supplier_table.setItem(row, col, cell(value))

    def expense_save(self):
        try:
            svc.add_expense(self.exp_title.text(), self.exp_amount.value(), self.exp_note.text())
            self.exp_title.clear()
            self.exp_amount.setValue(1)
            self.exp_note.clear()
            self.refresh_dashboard()
            QMessageBox.information(self, "ثبت شد", "هزینه ثبت شد.")
        except Exception as exc:
            self.notify_error(exc)

    def export_sales_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "خروجی فروش‌ها", "sabad-sales.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            rows = svc.sales_report(limit=None)
            with open(path, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["شماره رسید", "تاریخ", "جمع فاکتور", "تخفیف", "قابل پرداخت", "روش پرداخت", "وضعیت"])
                for sale in rows:
                    writer.writerow([
                        sale["receipt_no"], sale["created_at"], sale["subtotal"],
                        sale["discount"], sale["total"], sale["payment_method"],
                        "مرجوع‌شده" if sale["is_returned"] else "فروش"
                    ])
            QMessageBox.information(
                self, "خروجی آماده شد",
                f"فایل CSV شامل {len(rows)} فاکتور ذخیره شد:\n{path}"
            )
        except Exception as exc:
            self.notify_error(exc)

    def restore_backup(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "انتخاب نسخه پشتیبان", "", "SQLite Database (*.db);;All files (*)"
        )
        if not path:
            return
        answer = QMessageBox.warning(
            self, "تأیید بازیابی",
            "با بازیابی، اطلاعات فعلی با اطلاعات نسخه پشتیبان جایگزین می‌شود. "
            "قبل از جایگزینی یک نسخه ایمنی خودکار ساخته خواهد شد. ادامه می‌دهید؟",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            safety_path = svc.restore_from(path)
            self.cart.clear()
            self.refresh_all()
            QMessageBox.information(
                self, "بازیابی کامل شد",
                f"پشتیبان بازیابی شد. نسخه ایمنی دیتابیس قبلی:\n{safety_path}"
            )
        except Exception as exc:
            self.notify_error(exc)

    def supplier_payment_save(self):
        try:
            result = svc.add_supplier_payment(
                self.supplier_name.text(),
                self.supplier_payment_amount.value(),
                self.supplier_payment_note.text()
            )
            self.supplier_payment_amount.setValue(1)
            self.supplier_payment_note.clear()
            self.refresh_all()
            QMessageBox.information(
                self, "پرداخت ثبت شد",
                f"پرداخت {money(result['amount'])} تومان برای {result['supplier']} ثبت شد.\n"
                f"مانده بدهی: {money(result['balance'])} تومان"
            )
        except Exception as exc:
            self.notify_error(exc)

    def backup(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "ذخیره نسخه پشتیبان", "sabad-backup.db", "SQLite Database (*.db)"
        )
        if not path:
            return
        try:
            svc.backup_to(path)
            QMessageBox.information(self, "پشتیبان‌گیری", "نسخه پشتیبان ذخیره شد.")
        except Exception as exc:
            self.notify_error(exc)


def main():
    initialize()
    app = QApplication(sys.argv)
    app.setApplicationName("SabadPOS")
    app.setFont(QFont("Tahoma", 10))
    app.setStyleSheet("""
        QMainWindow, QWidget { background: #f7f8fa; }
        QTabWidget::pane { border: 1px solid #d8dde5; background: white; }
        QTabBar::tab { padding: 10px 18px; }
        QPushButton { padding: 7px 12px; }
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { padding: 6px; }
        QTableWidget { background: white; gridline-color: #e4e7eb; }
    """)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
