"""Erzeugt die SQLite-Produktdatenbank, die das SQL-Lookup-Tool abfragt.

Wird vom Server beim Start automatisch aufgerufen, kann aber auch einzeln
laufen:  uv run seed_db.py
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "inventory.db"

# (sku, name, category, unit_price, stock, warehouse)
PRODUCTS = [
    ("SKU-1001", "Servomotor 400W",          "drives",     289.90, 143, "Innsbruck"),
    ("SKU-1002", "Servomotor 750W",          "drives",     412.50,  38, "Innsbruck"),
    ("SKU-1003", "Frequenzumrichter 2.2kW",  "drives",     534.00,  12, "Wien"),
    ("SKU-2001", "Näherungssensor M12",      "sensors",     34.20, 870, "Innsbruck"),
    ("SKU-2002", "Lichtschranke 5m",         "sensors",     78.60, 214, "Wien"),
    ("SKU-2003", "Druckaufnehmer 0-10bar",   "sensors",    156.40,   0, "Wien"),
    ("SKU-3001", "SPS Kompaktsteuerung",     "controllers", 998.00,  27, "Innsbruck"),
    ("SKU-3002", "IO-Modul 16DI",            "controllers", 187.30, 156, "Innsbruck"),
    ("SKU-4001", "Profinet-Switch 8-Port",   "networking",  445.00,  61, "Wien"),
    ("SKU-4002", "M12-Patchkabel 5m",        "networking",   23.90, 1420, "Innsbruck"),
]


def seed() -> Path:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            sku         TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            category    TEXT NOT NULL,
            unit_price  REAL NOT NULL,
            stock       INTEGER NOT NULL,
            warehouse   TEXT NOT NULL
        )
    """)
    cur.executemany(
        "INSERT OR REPLACE INTO products VALUES (?, ?, ?, ?, ?, ?)", PRODUCTS
    )
    con.commit()
    con.close()
    return DB_PATH


if __name__ == "__main__":
    path = seed()
    print(f"Datenbank angelegt: {path}  ({len(PRODUCTS)} Produkte)")
