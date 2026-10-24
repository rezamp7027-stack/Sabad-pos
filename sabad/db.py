from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QStandardPaths

APP_NAME = "SabadPOS"


def data_dir() -> Path:
    root = Path(QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    ))
    root.mkdir(parents=True, exist_ok=True)
    return root


DB_PATH = data_dir() / "sabad.db"

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS products (
 id INTEGER PRIMARY KEY,
 barcode TEXT NOT NULL UNIQUE,
 name TEXT NOT NULL,
 category TEXT NOT NULL DEFAULT '',
 unit TEXT NOT NULL DEFAULT 'عدد',
 purchase_price INTEGER NOT NULL DEFAULT 0 CHECK(purchase_price >= 0),
 sale_price INTEGER NOT NULL DEFAULT 0 CHECK(sale_price >= 0),
 stock REAL NOT NULL DEFAULT 0 CHECK(stock >= 0),
 min_stock REAL NOT NULL DEFAULT 0 CHECK(min_stock >= 0),
 active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS sales (
 id INTEGER PRIMARY KEY,
 receipt_no TEXT NOT NULL UNIQUE,
 subtotal INTEGER NOT NULL,
 discount INTEGER NOT NULL DEFAULT 0,
 total INTEGER NOT NULL,
 payment_method TEXT NOT NULL DEFAULT 'نقدی',
 created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS sale_items (
 id INTEGER PRIMARY KEY,
 sale_id INTEGER NOT NULL REFERENCES sales(id),
 product_id INTEGER NOT NULL REFERENCES products(id),
 product_name TEXT NOT NULL,
 barcode TEXT NOT NULL,
 qty REAL NOT NULL CHECK(qty > 0),
 unit_price INTEGER NOT NULL CHECK(unit_price >= 0),
 unit_cost INTEGER NOT NULL CHECK(unit_cost >= 0),
 line_total INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS purchases (
 id INTEGER PRIMARY KEY,
 supplier TEXT NOT NULL DEFAULT '',
 invoice_no TEXT NOT NULL DEFAULT '',
 total INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS purchase_items (
 id INTEGER PRIMARY KEY,
 purchase_id INTEGER NOT NULL REFERENCES purchases(id),
 product_id INTEGER NOT NULL REFERENCES products(id),
 qty REAL NOT NULL CHECK(qty > 0),
 unit_cost INTEGER NOT NULL CHECK(unit_cost >= 0),
 line_total INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS expenses (
 id INTEGER PRIMARY KEY,
 title TEXT NOT NULL,
 amount INTEGER NOT NULL CHECK(amount > 0),
 note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS stock_movements (
 id INTEGER PRIMARY KEY,
 product_id INTEGER NOT NULL REFERENCES products(id),
 kind TEXT NOT NULL,
 qty REAL NOT NULL,
 reference TEXT NOT NULL DEFAULT '',
 note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS sales_returns (
 id INTEGER PRIMARY KEY,
 sale_id INTEGER NOT NULL UNIQUE REFERENCES sales(id),
 receipt_no TEXT NOT NULL,
 amount INTEGER NOT NULL CHECK(amount >= 0),
 reason TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS sales_return_items (
 id INTEGER PRIMARY KEY,
 return_id INTEGER NOT NULL REFERENCES sales_returns(id),
 sale_item_id INTEGER NOT NULL REFERENCES sale_items(id),
 product_id INTEGER NOT NULL REFERENCES products(id),
 qty REAL NOT NULL CHECK(qty > 0),
 unit_cost INTEGER NOT NULL CHECK(unit_cost >= 0),
 line_total INTEGER NOT NULL CHECK(line_total >= 0)
);
CREATE INDEX IF NOT EXISTS idx_returns_date ON sales_returns(created_at);
CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);
CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(created_at);
CREATE INDEX IF NOT EXISTS idx_movements_product ON stock_movements(product_id, created_at);
"""


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def initialize():
    with connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def transaction():
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
