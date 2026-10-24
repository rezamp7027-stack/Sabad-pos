from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from contextlib import closing, contextmanager
from pathlib import Path

from PySide6.QtCore import QStandardPaths

APP_NAME = "SabadPOS"


def data_dir() -> Path:
    """Use a stable per-application path, independent of QApplication startup order."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    root = base / APP_NAME

    # Migrate a database created by older builds that relied on Qt's implicit app name.
    legacy = Path(QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    ))
    if (legacy != root and (legacy / "sabad.db").is_file()
            and not (root / "sabad.db").exists()):
        root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy / "sabad.db", root / "sabad.db")
        legacy_wal = legacy / "sabad.db-wal"
        if legacy_wal.is_file():
            shutil.copy2(legacy_wal, root / "sabad.db-wal")

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

-- Separate event tables allow multiple partial returns without breaking older databases.
CREATE TABLE IF NOT EXISTS sale_return_events (
 id INTEGER PRIMARY KEY,
 sale_id INTEGER NOT NULL REFERENCES sales(id),
 receipt_no TEXT NOT NULL,
 amount INTEGER NOT NULL CHECK(amount >= 0),
 subtotal_amount INTEGER NOT NULL CHECK(subtotal_amount >= 0),
 discount_amount INTEGER NOT NULL CHECK(discount_amount >= 0),
 reason TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_sale_return_events_sale ON sale_return_events(sale_id);
CREATE INDEX IF NOT EXISTS idx_sale_return_events_date ON sale_return_events(created_at);
CREATE TABLE IF NOT EXISTS sale_return_event_items (
 id INTEGER PRIMARY KEY,
 event_id INTEGER NOT NULL REFERENCES sale_return_events(id),
 sale_item_id INTEGER NOT NULL REFERENCES sale_items(id),
 product_id INTEGER NOT NULL REFERENCES products(id),
 qty REAL NOT NULL CHECK(qty > 0),
 unit_cost INTEGER NOT NULL CHECK(unit_cost >= 0),
 subtotal_amount INTEGER NOT NULL CHECK(subtotal_amount >= 0),
 discount_amount INTEGER NOT NULL CHECK(discount_amount >= 0),
 amount INTEGER NOT NULL CHECK(amount >= 0)
);
CREATE TABLE IF NOT EXISTS supplier_payments (
 id INTEGER PRIMARY KEY,
 supplier TEXT NOT NULL,
 amount INTEGER NOT NULL CHECK(amount > 0),
 note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_supplier_payments_name ON supplier_payments(supplier);

CREATE TABLE IF NOT EXISTS customers (
 id INTEGER PRIMARY KEY,
 name TEXT NOT NULL COLLATE NOCASE UNIQUE,
 phone TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS customer_ledger (
 id INTEGER PRIMARY KEY,
 customer_id INTEGER NOT NULL REFERENCES customers(id),
 delta INTEGER NOT NULL,
 kind TEXT NOT NULL,
 reference TEXT NOT NULL DEFAULT '',
 note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_customer_ledger_customer ON customer_ledger(customer_id, created_at);


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
    with closing(connect()) as conn:
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
