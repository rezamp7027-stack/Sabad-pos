from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from .db import connect, transaction


class BusinessError(Exception):
    """A user-correctable business rule violation."""


def list_products(search=""):
    with connect() as c:
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
    with connect() as c:
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


def record_sale(items, discount=0, payment_method="نقدی"):
    if not items:
        raise BusinessError("سبد فروش خالی است.")
    discount = int(discount)
    with transaction() as c:
        normalized, subtotal = [], 0
        for item in items:
            qty = float(item["qty"])
            if qty <= 0:
                raise BusinessError("تعداد باید بیشتر از صفر باشد.")
            p = c.execute(
                "SELECT * FROM products WHERE id=? AND active=1",
                (item["product_id"],)
            ).fetchone()
            if not p:
                raise BusinessError("یکی از کالاها دیگر در دسترس نیست.")
            if float(p["stock"]) < qty:
                raise BusinessError(f"موجودی «{p['name']}» کافی نیست.")
            price = int(item.get("unit_price", p["sale_price"]))
            if price < 0:
                raise BusinessError("قیمت فروش نامعتبر است.")
            line = round(qty * price)
            subtotal += line
            normalized.append((p, qty, price, line))

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
        if prior:
            raise BusinessError("این فاکتور قبلاً مرجوع شده است.")
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
        return {"id": return_id, "receipt_no": sale["receipt_no"],
                "amount": int(sale["total"]), "returned_cost": returned_cost}


def return_sale_by_receipt(receipt_no, reason="مرجوعی کامل فاکتور"):
    with connect() as c:
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


def dashboard():
    with connect() as c:
        today = c.execute(
            """SELECT COALESCE(SUM(total),0) total, COUNT(*) count FROM sales
            WHERE date(created_at)=date('now','localtime')"""
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
    with connect() as c:
        return c.execute(
            "SELECT * FROM sales ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()



def financial_summary():
    """Return lifetime net figures after full-sale returns; profit remains an estimate."""
    with connect() as c:
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
        sold_cost = c.execute(
            "SELECT COALESCE(SUM(qty*unit_cost),0) total FROM sale_items"
        ).fetchone()["total"]
        returned_cost = c.execute(
            "SELECT COALESCE(SUM(qty*unit_cost),0) total FROM sales_return_items"
        ).fetchone()["total"]
        expenses = c.execute(
            "SELECT COALESCE(SUM(amount),0) total FROM expenses"
        ).fetchone()["total"]
        net_subtotal = int(sales["subtotal"] - returns["subtotal"])
        net_discounts = int(sales["discount"] - returns["discount"])
        net_cost = round(sold_cost - returned_cost)
        gross_margin = net_subtotal - net_cost
        estimated_profit = gross_margin - net_discounts - int(expenses)
        return {
            "sales_total": int(sales["total"] - returns["amount"]),
            "discounts": net_discounts,
            "cost_of_goods": net_cost,
            "gross_margin": gross_margin,
            "expenses": int(expenses),
            "estimated_profit": estimated_profit,
            "returns_total": int(returns["amount"]),
        }

def low_stock_products():
    with connect() as c:
        return c.execute(
            """SELECT * FROM products WHERE active=1 AND stock<=min_stock
            ORDER BY stock,name"""
        ).fetchall()


def backup_to(path):
    source = connect()
    try:
        dest = sqlite3.connect(str(path))
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
