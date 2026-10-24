from __future__ import annotations

from html import escape
from pathlib import Path

from PySide6.QtPrintSupport import QPrinter, QPrintDialog
from PySide6.QtGui import QTextDocument, QFont
from PySide6.QtWidgets import QDialog
from barcode import Code128
from barcode.writer import ImageWriter

from .db import data_dir


def receipt_text(sale):
    lines = [
        "سبد | فاکتور فروش",
        f"شماره رسید: {sale['receipt_no']}",
        "-" * 30,
    ]
    for item in sale["items"]:
        lines.append(f"{item['name']}  x{item['qty']:g}")
        lines.append(f"{item['line']:,} تومان")
    lines.extend([
        "-" * 30,
        f"جمع: {sale['subtotal']:,} تومان",
        f"تخفیف: {sale['discount']:,} تومان",
        f"قابل پرداخت: {sale['total']:,} تومان",
        "از خرید شما سپاسگزاریم",
    ])
    return "\n".join(lines)


def print_text(parent, text):
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    dialog = QPrintDialog(printer, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    escaped = escape(text).replace("\n", "<br>")
    doc = QTextDocument()
    doc.setDefaultFont(QFont("Tahoma", 10))
    doc.setHtml(
        '<html><head><meta charset="utf-8"></head>'
        '<body dir="rtl" style="direction:rtl;text-align:right;'
        'font-family:Tahoma,Arial,sans-serif;font-size:10pt;">'
        f'{escaped}</body></html>'
    )
    doc.print_(printer)
    return True


def save_barcode_png(code, name=""):
    output_dir = data_dir() / "labels"
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(ch for ch in str(code) if ch.isalnum() or ch in "-_") or "barcode"
    path = output_dir / safe_name
    saved = Code128(str(code), writer=ImageWriter()).save(
        str(path), options={"write_text": True, "quiet_zone": 2, "module_height": 12}
    )
    return Path(saved)
