from __future__ import annotations

import sqlite3
from contextlib import closing
import uuid
from datetime import datetime

from . import db as database
from .db import connect, initialize, transaction


class BusinessError(Exception):
    """A user-correctable business rule violation."""


def list_products(search=""):
    with closing(connect()) as c:
        if search.strip():
            s = f"%{search.strip()}%"
            return c.execute(
                """SELECT * FROM products WHERE active=1
                AND (name LIKE ? OR barcode LIKE ? OR category LIKE ?)
                ORDER BY name LIMIT 500""", (s, s, s)
            ).fetchall()
        return c.execute(
            "SELECT * FROM products WHERE active=1 ORDER BY name LIMIT 500"
        ).fetchall()


def get_product_by_barcode(barcode):
    with closing(connect()) as c:
        return c.execute(
            "SELECT * FROM products WHERE barcode=? AND active=1",
            (barcode.strip(),)
        ).fetchone()


def save_product(data, product_id=None):
    barcode = str(data["barcode"]).strip()
    name = str(data["name"]).strip()
    purchase_price = int(data["purchase_price"])
    sale_price = int(data["sale_price"])
    stock = float(data.get("stock", 0))
    min_stock = float(data.get("min_stock", 0))
    if not barcode or not name:
        raise BusinessError("نام کالا و بارکد الزامی است.")
    if min(purchase_price, sale_price) < 0 or min(stock, min_stock) < 0:
        raise BusinessError("قیمت و موجودی نمی‌توانند منفی باشند.")

    with transaction() as c:
        if product_id:
            existing = c.execute(
                "SELECT id FROM products WHERE barcode=? AND id<>?",
                (barcode, product_id)
            ).fetchone()
            if existing:
                raise BusinessError("این بارکد قبلاً برای کالای دیگری ثبت شده است.")
            old = c.execute(
                "SELECT id FROM products WHERE id=?", (product_id,)
            ).fetchone()
            if not old:
                raise BusinessError("کالا پیدا نشد.")
            c.execute(
                """UPDATE products SET barcode=?,name=?,category=?,unit=?,
                purchase_price=?,sale_price=?,min_stock=? WHERE id=?""",
                (barcode, name, str(data.get("category", "")).strip(),
                 data.get("unit", "عدد"), purchase_price, sale_price,
                 min_stock, product_id)
            )
            return product_id

        try:
            cur = c.execute(
                """INSERT INTO products
                (barcode,name,category,unit,purchase_price,sale_price,stock,min_stock)
                VALUES(?,?,?,?,?,?,?,?)""",
                (barcode, name, str(data.get("category", "")).strip(),
                 data.get("unit", "عدد"), purchase_price, sale_price,
                 stock, min_stock)
            )
        except sqlite3.IntegrityError as exc:
            if "barcode" in str(exc).lower():
                raise BusinessError("این بارکد قبلاً ثبت شده است.") from exc
            raise BusinessError("اطلاعات کالا معتبر نیست.") from exc
        pid = cur.lastrowid
        if stock:
            c.execute(
                """INSERT INTO stock_movements
                (product_id,kind,qty,reference,note) VALUES(?,?,?,?,?)""",
                (pid, "موجودی اولیه", stock, "INIT", "ثبت موجودی هنگام ایجاد کالا")
            )
        return pid


def adjust_stock(product_id, qty_delta, kind="اصلاح موجودی", note=""):
    qty_delta = float(qty_delta)
    if qty_delta == 0:
        raise BusinessError("مقدار تغییر موجودی صفر است.")
    with transaction() as c:
        p = c.execute(
            "SELECT stock FROM products WHERE id=? AND active=1", (product_id,)
        ).fetchone()
        if not p:
            raise BusinessError("کالا پیدا نشد.")
        new_stock = float(p["stock"]) + qty_delta
        if new_stock < 0:
            raise BusinessError("موجودی کافی نیست.")
        c.execute("UPDATE products SET stock=? WHERE id=?", (new_stock, product_id))
        c.execute(
            "INSERT INTO stock_movements(product_id,kind,qty,note) VALUES(?,?,?,?)",
            (product_id, kind, qty_delta, note)
        )


def record_sale(items, discount=0, payment_method="نقدی", customer_name=""):
    if not items:
        raise BusinessError("سبد فروش خالی است.")
    discount = int(discount)
    customer_name = str(customer_name or "").strip()
    if payment_method == "اعتباری" and not customer_name:
        raise BusinessError("برای فروش اعتباری، نام مشتری الزامی است.")
    with transaction() as c:
        normalized, subtotal = [], 0
        requested_by_product = {}
        for item in items:
            qty = float(item["qty"])
            if qty <= 0:
                raise BusinessError("تعداد باید بیشتر از صفر باشد.")
            product_id = int(item["product_id"])
            p = c.execute(
                "SELECT * FROM products WHERE id=? AND active=1",
                (product_id,)
            ).fetchone()
            if not p:
                raise BusinessError("یکی از کالاها دیگر در دسترس نیست.")
            requested_by_product[product_id] = requested_by_product.get(product_id, 0.0) + qty
            price = int(item.get("unit_price", p["sale_price"]))
            if price < 0:
                raise BusinessError("قیمت فروش نامعتبر است.")
            line = round(qty * price)
            subtotal += line
            normalized.append((p, qty, price, line))

        for product_id, requested_qty in requested_by_product.items():
            p = c.execute("SELECT name,stock FROM products WHERE id=?", (product_id,)).fetchone()
            if float(p["stock"]) < requested_qty:
                raise BusinessError(f"موجودی «{p['name']}» کافی نیست.")

        if discount < 0 or discount > subtotal:
            raise BusinessError("تخفیف باید بین صفر و جمع فاکتور باشد.")
        total = subtotal - discount
        receipt = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:5].upper()
        cur = c.execute(
            """INSERT INTO sales(receipt_no,subtotal,discount,total,payment_method)
            VALUES(?,?,?,?,?)""",
            (receipt, subtotal, discount, total, payment_method)
        )
        sale_id = cur.lastrowid
        if payment_method == "اعتباری":
            c.execute("INSERT INTO customers(name) VALUES(?) ON CONFLICT(name) DO NOTHING",
                      (customer_name,))
            customer_id = c.execute(
                "SELECT id FROM customers WHERE name=? COLLATE NOCASE", (customer_name,)
            ).fetchone()["id"]
            c.execute(
                """INSERT INTO customer_ledger
                (customer_id,delta,kind,reference,note) VALUES(?,?,?,?,?)""",
                (customer_id, total, "فروش اعتباری", receipt, f"فاکتور {receipt}")
            )
        for p, qty, price, line in normalized:
            c.execute(
                """INSERT INTO sale_items
                (sale_id,product_id,product_name,barcode,qty,unit_price,unit_cost,line_total)
                VALUES(?,?,?,?,?,?,?,?)""",
                (sale_id, p["id"], p["name"], p["barcode"], qty, price,
                 p["purchase_price"], line)
            )
            c.execute("UPDATE products SET stock=stock-? WHERE id=?", (qty, p["id"]))
            c.execute(
                """INSERT INTO stock_movements
                (product_id,kind,qty,reference,note) VALUES(?,?,?,?,?)""",
                (p["id"], "فروش", -qty, receipt, "کسر موجودی فروش")
            )
        return {
            "id": sale_id, "receipt_no": receipt, "subtotal": subtotal,
            "discount": discount, "total": total,
            "items": [
                {"name": p["name"], "barcode": p["barcode"], "qty": qty,
                 "price": price, "line": line}
                for p, qty, price, line in normalized
            ]
        }


def record_purchase(lines, supplier="", invoice_no=""):
    if not lines:
        raise BusinessError("هیچ کالایی برای خرید وارد نشده است.")
    with transaction() as c:
        total, prepared = 0, []
        for line in lines:
            qty, cost = float(line["qty"]), int(line["unit_cost"])
            if qty <= 0 or cost < 0:
                raise BusinessError("مقدار یا قیمت خرید نامعتبر است.")
            p = c.execute(
                "SELECT * FROM products WHERE id=? AND active=1",
                (line["product_id"],)
            ).fetchone()
            if not p:
                raise BusinessError("کالا پیدا نشد.")
            amount = round(qty * cost)
            total += amount
            prepared.append((p, qty, cost, amount))
        cur = c.execute(
            "INSERT INTO purchases(supplier,invoice_no,total) VALUES(?,?,?)",
            (supplier.strip(), invoice_no.strip(), total)
        )
        purchase_id = cur.lastrowid
        for p, qty, cost, amount in prepared:
            c.execute(
                """INSERT INTO purchase_items
                (purchase_id,product_id,qty,unit_cost,line_total) VALUES(?,?,?,?,?)""",
                (purchase_id, p["id"], qty, cost, amount)
            )
            c.execute(
                "UPDATE products SET stock=stock+?,purchase_price=? WHERE id=?",
                (qty, cost, p["id"])
            )
            c.execute(
                """INSERT INTO stock_movements
                (product_id,kind,qty,reference,note) VALUES(?,?,?,?,?)""",
                (p["id"], "خرید", qty, str(purchase_id), f"خرید از {supplier}".strip())
            )
        return {"id": purchase_id, "total": total}



def sale_returnable_items(receipt_no):
    """List sale lines and quantities still eligible for return."""
    with closing(connect()) as c:
        sale = c.execute("SELECT id FROM sales WHERE receipt_no=?", (str(receipt_no).strip(),)).fetchone()
        if not sale:
            raise BusinessError("فاکتور فروش پیدا نشد.")
        if c.execute("SELECT 1 FROM sales_returns WHERE sale_id=?", (sale["id"],)).fetchone():
            raise BusinessError("این فاکتور قبلاً به‌طور کامل مرجوع شده است.")
        return c.execute(
            """SELECT i.id AS sale_item_id, i.product_name, i.barcode, i.qty,
            i.unit_price, i.line_total,
            MAX(0, i.qty-COALESCE((SELECT SUM(ri.qty) FROM sale_return_event_items ri
                WHERE ri.sale_item_id=i.id),0)) AS remaining_qty
            FROM sale_items i WHERE i.sale_id=? ORDER BY i.id""",
            (sale["id"],)
        ).fetchall()


def return_sale_items(sale_id, quantities, reason="مرجوعی جزئی"):
    """Return selected quantities from a sale; repeated partial returns are supported."""
    if not quantities:
        raise BusinessError("برای مرجوعی حداقل یک قلم انتخاب کنید.")
    reason = str(reason or "").strip()
    with transaction() as c:
        sale = c.execute("SELECT * FROM sales WHERE id=?", (int(sale_id),)).fetchone()
        if not sale:
            raise BusinessError("فاکتور فروش پیدا نشد.")
        if c.execute("SELECT 1 FROM sales_returns WHERE sale_id=?", (sale["id"],)).fetchone():
            raise BusinessError("این فاکتور قبلاً به‌طور کامل مرجوع شده است.")
        items = c.execute(
            "SELECT * FROM sale_items WHERE sale_id=? ORDER BY id", (sale["id"],)
        ).fetchall()
        by_id = {int(item["id"]): item for item in items}
        requested = {}
        for entry in quantities:
            item_id = int(entry["sale_item_id"])
            qty = float(entry["qty"])
            if qty <= 0:
                raise BusinessError("مقدار مرجوعی باید بیشتر از صفر باشد.")
            if item_id not in by_id:
                raise BusinessError("قلم انتخاب‌شده متعلق به این فاکتور نیست.")
            requested[item_id] = requested.get(item_id, 0.0) + qty
        prepared_gross = []
        subtotal_amount = 0
        # Track each line's cumulative returned quantity/value so repeated partial
        # returns sum exactly to the original line total despite integer rounding.
        for item_id, qty in sorted(requested.items()):
            item = by_id[item_id]
            returned = c.execute(
                """SELECT COALESCE(SUM(qty),0) qty,
                COALESCE(SUM(subtotal_amount),0) gross
                FROM sale_return_event_items WHERE sale_item_id=?""",
                (item_id,)
            ).fetchone()
            already_qty = float(returned["qty"])
            remaining = float(item["qty"]) - already_qty
            if qty > remaining + 1e-8:
                raise BusinessError(
                    f"مقدار مرجوعی «{item['product_name']}» بیشتر از مقدار باقی‌مانده است ({remaining:g})."
                )
            gross_before = int(returned["gross"])
            gross_after = round(
                int(item["line_total"]) * min(float(item["qty"]), already_qty + qty)
                / float(item["qty"])
            )
            gross = max(0, gross_after - gross_before)
            prepared_gross.append((item, qty, gross))
            subtotal_amount += gross

        prior = c.execute(
            """SELECT COALESCE(SUM(amount),0) amount,
            COALESCE(SUM(subtotal_amount),0) subtotal,
            COALESCE(SUM(discount_amount),0) discount
            FROM sale_return_events WHERE sale_id=?""",
            (sale["id"],)
        ).fetchone()
        prior_amount = int(prior["amount"])
        prior_gross = int(prior["subtotal"])
        prior_discount = int(prior["discount"])

        # Allocate the invoice discount by cumulative returned gross, not by
        # rounding each return independently. This prevents discount drift over
        # multiple returns and guarantees that a fully returned invoice refunds
        # exactly its original total.
        cumulative_gross = prior_gross
        cumulative_discount = prior_discount
        prepared = []
        for item, qty, gross in prepared_gross:
            cumulative_gross += gross
            target_discount = round(
                int(sale["discount"]) * min(cumulative_gross, int(sale["subtotal"]))
                / max(1, int(sale["subtotal"]))
            )
            line_discount = max(0, target_discount - cumulative_discount)
            line_discount = min(gross, line_discount)
            cumulative_discount += line_discount
            prepared.append((item, qty, gross, line_discount, max(0, gross - line_discount)))

        discount_amount = sum(line[3] for line in prepared)
        amount = max(0, subtotal_amount - discount_amount)
        remaining_refund = max(0, int(sale["total"]) - prior_amount)
        if amount > remaining_refund:
            # Guard against legacy return records created by earlier versions.
            amount = remaining_refund
            discount_amount = subtotal_amount - amount
            adjusted = []
            remaining_discount = discount_amount
            for item, qty, gross, _old_discount, _net in prepared:
                line_discount = min(gross, remaining_discount)
                remaining_discount -= line_discount
                adjusted.append((item, qty, gross, line_discount, gross - line_discount))
            prepared = adjusted
        cur = c.execute(
            """INSERT INTO sale_return_events
            (sale_id,receipt_no,amount,subtotal_amount,discount_amount,reason)
            VALUES(?,?,?,?,?,?)""",
            (sale["id"], sale["receipt_no"], amount, subtotal_amount, discount_amount, reason)
        )
        event_id = cur.lastrowid
        returned_cost = 0
        for item, qty, gross, line_discount, net in prepared:
            c.execute(
                """INSERT INTO sale_return_event_items
                (event_id,sale_item_id,product_id,qty,unit_cost,subtotal_amount,discount_amount,amount)
                VALUES(?,?,?,?,?,?,?,?)""",
                (event_id, item["id"], item["product_id"], qty, item["unit_cost"],
                 gross, line_discount, net)
            )
            c.execute("UPDATE products SET stock=stock+? WHERE id=?", (qty, item["product_id"]))
            c.execute(
                """INSERT INTO stock_movements
                (product_id,kind,qty,reference,note) VALUES(?,?,?,?,?)""",
                (item["product_id"], "مرجوعی فروش", qty, sale["receipt_no"],
                 f"مرجوعی جزئی بابت فاکتور {sale['receipt_no']}")
            )
            returned_cost += round(qty * int(item["unit_cost"]))
        _adjust_customer_credit(c, sale, -amount, "مرجوعی اعتباری", sale["receipt_no"],
                                f"مرجوعی جزئی فاکتور {sale['receipt_no']}")
        return {"id": event_id, "receipt_no": sale["receipt_no"], "amount": amount,
                "returned_cost": returned_cost, "subtotal_amount": subtotal_amount,
                "discount_amount": discount_amount}


def return_sale(sale_id, reason="مرجوعی کامل فاکتور"):
    """Return an entire sale exactly once and restore its quantities atomically."""
    reason = str(reason or "").strip()
    with transaction() as c:
        sale = c.execute("SELECT * FROM sales WHERE id=?", (int(sale_id),)).fetchone()
        if not sale:
            raise BusinessError("فاکتور فروش پیدا نشد.")
        prior = c.execute(
            "SELECT id FROM sales_returns WHERE sale_id=?", (sale["id"],)
        ).fetchone()
        partial = c.execute(
            "SELECT id FROM sale_return_events WHERE sale_id=? LIMIT 1", (sale["id"],)
        ).fetchone()
        if prior or partial:
            raise BusinessError("این فاکتور قبلاً مرجوع شده یا مرجوعی جزئی دارد؛ برای ادامه فقط مقدار باقی‌مانده را مرجوع کنید.")
        items = c.execute(
            "SELECT * FROM sale_items WHERE sale_id=? ORDER BY id", (sale["id"],)
        ).fetchall()
        if not items:
            raise BusinessError("اقلام فاکتور پیدا نشدند.")
        cur = c.execute(
            "INSERT INTO sales_returns(sale_id,receipt_no,amount,reason) VALUES(?,?,?,?)",
            (sale["id"], sale["receipt_no"], sale["total"], reason)
        )
        return_id = cur.lastrowid
        returned_cost = 0
        for item in items:
            qty = float(item["qty"])
            returned_cost += round(qty * int(item["unit_cost"]))
            c.execute(
                """INSERT INTO sales_return_items
                (return_id,sale_item_id,product_id,qty,unit_cost,line_total)
                VALUES(?,?,?,?,?,?)""",
                (return_id, item["id"], item["product_id"], qty,
                 item["unit_cost"], item["line_total"])
            )
            c.execute("UPDATE products SET stock=stock+? WHERE id=?",
                      (qty, item["product_id"]))
            c.execute(
                """INSERT INTO stock_movements
                (product_id,kind,qty,reference,note) VALUES(?,?,?,?,?)""",
                (item["product_id"], "مرجوعی فروش", qty, sale["receipt_no"],
                 f"بازگشت موجودی بابت فاکتور {sale['receipt_no']}")
            )
        _adjust_customer_credit(c, sale, -int(sale["total"]), "مرجوعی اعتباری",
                                sale["receipt_no"], f"مرجوعی کامل فاکتور {sale['receipt_no']}")
        return {"id": return_id, "receipt_no": sale["receipt_no"],
                "amount": int(sale["total"]), "returned_cost": returned_cost}


def return_sale_items_by_receipt(receipt_no, quantities, reason="مرجوعی جزئی"):
    with closing(connect()) as c:
        sale = c.execute("SELECT id FROM sales WHERE receipt_no=?", (str(receipt_no).strip(),)).fetchone()
    if not sale:
        raise BusinessError("فاکتور فروش پیدا نشد.")
    return return_sale_items(sale["id"], quantities, reason)


def return_sale_by_receipt(receipt_no, reason="مرجوعی کامل فاکتور"):
    with closing(connect()) as c:
        sale = c.execute("SELECT id FROM sales WHERE receipt_no=?", (receipt_no,)).fetchone()
    if not sale:
        raise BusinessError("فاکتور فروش پیدا نشد.")
    return return_sale(sale["id"], reason)


def add_expense(title, amount, note=""):
    title, amount = title.strip(), int(amount)
    if not title or amount <= 0:
        raise BusinessError("عنوان و مبلغ هزینه معتبر نیست.")
    with transaction() as c:
        c.execute(
            "INSERT INTO expenses(title,amount,note) VALUES(?,?,?)",
            (title, amount, note.strip())
        )




def restore_from(path):
    """Restore a validated SQLite backup and roll back to a safety copy on failure."""
    from pathlib import Path

    source_path = Path(path).expanduser().resolve()
    current_path = Path(database.DB_PATH).expanduser().resolve()
    if not source_path.is_file():
        raise BusinessError("فایل پشتیبان پیدا نشد.")
    if source_path == current_path:
        raise BusinessError("فایل پشتیبان نباید همان دیتابیس فعال باشد.")

    safety_path = None
    safety_created = False
    source = None
    try:
        source = sqlite3.connect(str(source_path), timeout=10)
        source.execute("PRAGMA query_only=ON")
        integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise BusinessError("فایل پشتیبان سالم نیست و بازیابی انجام نشد.")
        tables = {row[0] for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        required_columns = {
            "products": {"id", "barcode", "name", "category", "unit", "purchase_price",
                         "sale_price", "stock", "min_stock", "active", "created_at"},
            "sales": {"id", "receipt_no", "subtotal", "discount", "total",
                      "payment_method", "created_at"},
            "sale_items": {"id", "sale_id", "product_id", "product_name", "barcode",
                           "qty", "unit_price", "unit_cost", "line_total"},
            "purchases": {"id", "supplier", "invoice_no", "total", "created_at"},
            "purchase_items": {"id", "purchase_id", "product_id", "qty", "unit_cost", "line_total"},
            "expenses": {"id", "title", "amount", "note", "created_at"},
            "stock_movements": {"id", "product_id", "kind", "qty", "reference", "note", "created_at"},
        }
        if not set(required_columns).issubset(tables):
            raise BusinessError("این فایل، نسخه پشتیبان معتبر سبد نیست.")
        for table, expected in required_columns.items():
            actual = {row[1] for row in source.execute(f'PRAGMA table_info("{table}")')}
            if not expected.issubset(actual):
                raise BusinessError(
                    f"ساختار جدول «{table}» با نسخه پشتیبان سبد سازگار نیست."
                )

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        safety_path = current_path.with_name(
            current_path.name + f".pre-restore-{stamp}.db"
        )
        backup_to(safety_path)
        safety_created = True

        destination = connect()
        try:
            source.backup(destination)
        finally:
            destination.close()
        source.close()
        source = None

        # Add any tables introduced in newer versions to an older restored backup.
        initialize()
        return safety_path
    except Exception as exc:
        if source is not None:
            source.close()
        rollback_error = None
        if safety_created and safety_path is not None and safety_path.is_file():
            try:
                safety = sqlite3.connect(str(safety_path), timeout=10)
                try:
                    destination = connect()
                    try:
                        safety.backup(destination)
                    finally:
                        destination.close()
                finally:
                    safety.close()
                initialize()
            except Exception as rollback_exc:
                rollback_error = rollback_exc
        if isinstance(exc, BusinessError):
            if rollback_error:
                raise BusinessError(
                    f"{exc} همچنین بازگردانی نسخه ایمنی خودکار ناموفق بود: {rollback_error}"
                ) from exc
            raise
        message = f"بازیابی پشتیبان انجام نشد: {exc}"
        if rollback_error:
            message += f"؛ بازگردانی نسخه ایمنی نیز ناموفق بود: {rollback_error}"
        elif safety_created and safety_path:
            message += f"؛ نسخه ایمنی دیتابیس در این مسیر است: {safety_path}"
        raise BusinessError(message) from exc


def _adjust_customer_credit(conn, sale, delta, kind, reference, note):
    if sale["payment_method"] != "اعتباری" or not delta:
        return
    entry = conn.execute(
        """SELECT customer_id FROM customer_ledger
        WHERE reference=? AND kind='فروش اعتباری' LIMIT 1""",
        (sale["receipt_no"],)
    ).fetchone()
    if entry:
        conn.execute(
            """INSERT INTO customer_ledger
            (customer_id,delta,kind,reference,note) VALUES(?,?,?,?,?)""",
            (entry["customer_id"], int(delta), kind, reference, note)
        )


def customer_balances():
    with closing(connect()) as c:
        return c.execute(
            """SELECT cu.id,cu.name,cu.phone,
            COALESCE(SUM(l.delta),0) balance
            FROM customers cu LEFT JOIN customer_ledger l ON l.customer_id=cu.id
            GROUP BY cu.id,cu.name,cu.phone ORDER BY balance DESC,cu.name"""
        ).fetchall()


def add_customer_payment(customer_name, amount, note=""):
    customer_name = str(customer_name or "").strip()
    amount = int(amount)
    if not customer_name or amount <= 0:
        raise BusinessError("نام مشتری و مبلغ پرداخت معتبر الزامی است.")
    with transaction() as c:
        customer = c.execute(
            "SELECT id,name FROM customers WHERE name=? COLLATE NOCASE",
            (customer_name,)
        ).fetchone()
        if not customer:
            raise BusinessError("مشتری ثبت‌شده‌ای با این نام پیدا نشد.")
        balance = int(c.execute(
            "SELECT COALESCE(SUM(delta),0) FROM customer_ledger WHERE customer_id=?",
            (customer["id"],)
        ).fetchone()[0])
        if balance <= 0:
            raise BusinessError("این مشتری بدهی قابل دریافت ندارد.")
        if amount > balance:
            raise BusinessError(f"مبلغ دریافتی از بدهی بیشتر است. مانده: {balance:,} تومان")
        c.execute(
            """INSERT INTO customer_ledger
            (customer_id,delta,kind,reference,note) VALUES(?,?,?,?,?)""",
            (customer["id"], -amount, "دریافت از مشتری", "", str(note or "").strip())
        )
        return {"customer": customer["name"], "amount": amount, "balance": balance - amount}


def supplier_balances():
    """Summarize recorded purchases and payments by supplier name."""
    with closing(connect()) as c:
        return c.execute(
            """SELECT p.supplier,
            COALESCE(p.purchased,0) purchased,
            COALESCE(pay.paid,0) paid,
            COALESCE(p.purchased,0)-COALESCE(pay.paid,0) balance
            FROM (
                SELECT supplier, SUM(total) purchased FROM purchases
                WHERE TRIM(supplier)<>'' GROUP BY supplier
            ) p
            LEFT JOIN (
                SELECT supplier, SUM(amount) paid FROM supplier_payments
                GROUP BY supplier
            ) pay ON pay.supplier=p.supplier
            ORDER BY balance DESC, p.supplier"""
        ).fetchall()


def add_supplier_payment(supplier, amount, note=""):
    supplier = str(supplier or "").strip()
    amount = int(amount)
    if not supplier or amount <= 0:
        raise BusinessError("نام تأمین‌کننده و مبلغ پرداخت معتبر الزامی است.")
    with transaction() as c:
        purchased = c.execute(
            "SELECT COALESCE(SUM(total),0) FROM purchases WHERE supplier=?",
            (supplier,)
        ).fetchone()[0]
        paid = c.execute(
            "SELECT COALESCE(SUM(amount),0) FROM supplier_payments WHERE supplier=?",
            (supplier,)
        ).fetchone()[0]
        balance = int(purchased) - int(paid)
        if purchased <= 0:
            raise BusinessError("برای این تأمین‌کننده فاکتور خرید ثبت نشده است.")
        if amount > balance:
            raise BusinessError(f"مبلغ پرداخت از بدهی ثبت‌شده بیشتر است. مانده: {balance:,} تومان")
        cur = c.execute(
            "INSERT INTO supplier_payments(supplier,amount,note) VALUES(?,?,?)",
            (supplier, amount, str(note or "").strip())
        )
        return {"id": cur.lastrowid, "supplier": supplier,
                "amount": amount, "balance": balance - amount}


def dashboard():
    with closing(connect()) as c:
        today = c.execute(
            """SELECT
                COALESCE((SELECT SUM(total) FROM sales
                    WHERE date(created_at)=date('now','localtime')),0)
                - COALESCE((SELECT SUM(amount) FROM sales_returns
                    WHERE date(created_at)=date('now','localtime')),0)
                - COALESCE((SELECT SUM(amount) FROM sale_return_events
                    WHERE date(created_at)=date('now','localtime')),0) total,
                (SELECT COUNT(*) FROM sales
                    WHERE date(created_at)=date('now','localtime')) count"""
        ).fetchone()
        stock = c.execute(
            """SELECT COUNT(*) n, COALESCE(SUM(stock*purchase_price),0) value
            FROM products WHERE active=1"""
        ).fetchone()
        low = c.execute(
            "SELECT COUNT(*) n FROM products WHERE active=1 AND stock<=min_stock"
        ).fetchone()
        expenses = c.execute(
            """SELECT COALESCE(SUM(amount),0) n FROM expenses
            WHERE date(created_at)=date('now','localtime')"""
        ).fetchone()
        return {
            "sales": today["total"], "receipts": today["count"],
            "products": stock["n"], "stock_value": stock["value"],
            "low_stock": low["n"], "expenses": expenses["n"]
        }


def sales_report(limit=200):
    """Return newest sales with a return-status flag; None means no row limit."""
    with closing(connect()) as c:
        query = """SELECT s.*,
            CASE WHEN r.id IS NOT NULL OR
                COALESCE((SELECT SUM(e.amount) FROM sale_return_events e WHERE e.sale_id=s.id),0)>=s.total
                THEN 1 ELSE 0 END AS is_returned,
            COALESCE((SELECT SUM(e.amount) FROM sale_return_events e WHERE e.sale_id=s.id),0) AS partial_return_amount,
            EXISTS(SELECT 1 FROM sale_return_events e WHERE e.sale_id=s.id) AS has_partial_return
            FROM sales s LEFT JOIN sales_returns r ON r.sale_id=s.id
            ORDER BY s.id DESC"""
        if limit is None:
            return c.execute(query).fetchall()
        limit = int(limit)
        if limit < 1:
            raise BusinessError("محدودیت گزارش باید بیشتر از صفر باشد.")
        return c.execute(query + " LIMIT ?", (limit,)).fetchall()



def financial_summary():
    """Return lifetime net figures after full-sale returns; profit remains an estimate."""
    with closing(connect()) as c:
        sales = c.execute(
            """SELECT COALESCE(SUM(total),0) total,
            COALESCE(SUM(discount),0) discount,
            COALESCE(SUM(subtotal),0) subtotal FROM sales"""
        ).fetchone()
        returns = c.execute(
            """SELECT COALESCE(SUM(r.amount),0) amount,
            COALESCE(SUM(s.subtotal),0) subtotal,
            COALESCE(SUM(s.discount),0) discount
            FROM sales_returns r JOIN sales s ON s.id=r.sale_id"""
        ).fetchone()
        partial_returns = c.execute(
            """SELECT COALESCE(SUM(amount),0) amount,
            COALESCE(SUM(subtotal_amount),0) subtotal,
            COALESCE(SUM(discount_amount),0) discount
            FROM sale_return_events"""
        ).fetchone()
        sold_cost = c.execute(
            "SELECT COALESCE(SUM(qty*unit_cost),0) total FROM sale_items"
        ).fetchone()["total"]
        returned_cost = c.execute(
            """SELECT
            COALESCE((SELECT SUM(qty*unit_cost) FROM sales_return_items),0) +
            COALESCE((SELECT SUM(qty*unit_cost) FROM sale_return_event_items),0) total"""
        ).fetchone()["total"]
        expenses = c.execute(
            "SELECT COALESCE(SUM(amount),0) total FROM expenses"
        ).fetchone()["total"]
        net_subtotal = int(sales["subtotal"] - returns["subtotal"] - partial_returns["subtotal"])
        net_discounts = int(sales["discount"] - returns["discount"] - partial_returns["discount"])
        net_cost = round(sold_cost - returned_cost)
        gross_margin = net_subtotal - net_cost
        estimated_profit = gross_margin - net_discounts - int(expenses)
        return {
            "sales_total": int(sales["total"] - returns["amount"] - partial_returns["amount"]),
            "discounts": net_discounts,
            "cost_of_goods": net_cost,
            "gross_margin": gross_margin,
            "expenses": int(expenses),
            "estimated_profit": estimated_profit,
            "returns_total": int(returns["amount"] + partial_returns["amount"]),
        }

def low_stock_products():
    with closing(connect()) as c:
        return c.execute(
            """SELECT * FROM products WHERE active=1 AND stock<=min_stock
            ORDER BY stock,name"""
        ).fetchall()


def backup_to(path):
    from pathlib import Path

    target = Path(path).expanduser().resolve()
    active = Path(database.DB_PATH).expanduser().resolve()
    if target == active:
        raise BusinessError("مسیر نسخه پشتیبان نباید با دیتابیس فعال یکسان باشد.")
    target.parent.mkdir(parents=True, exist_ok=True)
    source = connect()
    try:
        dest = sqlite3.connect(str(target), timeout=10)
        try:
            source.backup(dest)
            result = dest.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise BusinessError("بررسی سلامت نسخه پشتیبان ناموفق بود.")
        finally:
            dest.close()
    except sqlite3.Error as exc:
        raise BusinessError(f"پشتیبان‌گیری انجام نشد: {exc}") from exc
    finally:
        source.close()
    return target
