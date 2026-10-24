from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QComboBox,
    QDoubleSpinBox, QSpinBox, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QDialogButtonBox
)

from . import services as svc


def _money(value):
    return f"{int(value):,}"


class PurchaseDialog(QDialog):
    """Register supplier purchase invoices and increase inventory atomically."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ثبت فاکتور خرید")
        self.resize(850, 560)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.lines = []
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.supplier = QLineEdit()
        self.invoice = QLineEdit()
        self.product = QComboBox()
        self.qty = QDoubleSpinBox()
        self.qty.setDecimals(3)
        self.qty.setRange(0.001, 10000000)
        self.qty.setValue(1)
        self.cost = QSpinBox()
        self.cost.setRange(0, 2000000000)
        self.cost.setSuffix(" تومان")
        form.addRow("تأمین‌کننده", self.supplier)
        form.addRow("شماره فاکتور تأمین‌کننده", self.invoice)
        form.addRow("کالا", self.product)
        form.addRow("تعداد/مقدار", self.qty)
        form.addRow("قیمت خرید هر واحد", self.cost)
        root.addLayout(form)

        add_row = QHBoxLayout()
        add_button = QPushButton("افزودن به فاکتور")
        add_button.clicked.connect(self.add_line)
        add_row.addWidget(add_button)
        add_row.addStretch()
        root.addLayout(add_row)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["کالا", "مقدار", "قیمت واحد", "جمع ردیف", ""])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.cellDoubleClicked.connect(self.remove_line)
        root.addWidget(self.table)
        self.total = QLabel("جمع خرید: ۰ تومان")
        self.total.setStyleSheet("font-size:20px;font-weight:700")
        root.addWidget(self.total)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("ثبت نهایی خرید")
        buttons.accepted.connect(self.save_purchase)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.refresh_products()

    def refresh_products(self):
        self.product.clear()
        for p in svc.list_products():
            self.product.addItem(f"{p['name']} | {p['barcode']}", p["id"])
        current = self.product.currentData()
        if current is not None:
            p = next((x for x in svc.list_products() if x["id"] == current), None)
            if p:
                self.cost.setValue(p["purchase_price"])

    def add_line(self):
        product_id = self.product.currentData()
        if product_id is None:
            QMessageBox.warning(self, "کالا موجود نیست", "ابتدا یک کالا در بخش کالاها تعریف کنید.")
            return
        product = next((p for p in svc.list_products() if p["id"] == product_id), None)
        if not product:
            QMessageBox.warning(self, "خطا", "کالا پیدا نشد.")
            return
        self.lines.append({
            "product_id": product_id, "name": product["name"],
            "qty": self.qty.value(), "unit_cost": self.cost.value()
        })
        self.render_lines()

    def render_lines(self):
        self.table.setRowCount(len(self.lines))
        total = 0
        for row, line in enumerate(self.lines):
            amount = round(line["qty"] * line["unit_cost"])
            total += amount
            values = [line["name"], f"{line['qty']:g}", _money(line["unit_cost"]), _money(amount), "حذف"]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)
            self.table.item(row, 4).setForeground(Qt.GlobalColor.red)
        self.total.setText(f"جمع خرید: {_money(total)} تومان")

    def remove_line(self, row, _column):
        if 0 <= row < len(self.lines):
            self.lines.pop(row)
            self.render_lines()

    def save_purchase(self):
        if not self.lines:
            QMessageBox.warning(self, "فاکتور خالی", "حداقل یک کالا به فاکتور اضافه کنید.")
            return
        try:
            result = svc.record_purchase(
                self.lines, self.supplier.text(), self.invoice.text()
            )
        except Exception as exc:
            QMessageBox.warning(self, "ثبت خرید ناموفق", str(exc))
            return
        QMessageBox.information(
            self, "خرید ثبت شد",
            f"فاکتور خرید ثبت شد.\nمبلغ: {_money(result['total'])} تومان\nموجودی کالاها به‌روزرسانی شد."
        )
        self.accept()
