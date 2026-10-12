"""Legt die beiden "Altsysteme" an, auf die die Agenten zugreifen.

  inventory.db   - Lagerverwaltung (WMS), gehoert dem Lager-Agenten.
                   Gleicher Produktbestand wie in Part 1.
  production.db  - Fertigungssteuerung (MES), gehoert dem Planungs-Agenten.

Jeder Agent sieht nur seine eigene Datenbank. Wer Daten aus dem anderen
System braucht, muss den zustaendigen Agenten fragen - genau das macht
der Koordinator.

Einzeln ausfuehrbar:  uv run data/seed.py
"""
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).parent
INVENTORY_DB = DATA_DIR / "inventory.db"
PRODUCTION_DB = DATA_DIR / "production.db"

# (sku, name, category, unit_price, stock, warehouse)
PRODUCTS = [
    ("SKU-1001", "Servomotor 400W",          "drives",      289.90,  143, "Innsbruck"),
    ("SKU-1002", "Servomotor 750W",          "drives",      412.50,   38, "Innsbruck"),
    ("SKU-1003", "Frequenzumrichter 2.2kW",  "drives",      534.00,   12, "Wien"),
    ("SKU-2001", "Näherungssensor M12",      "sensors",      34.20,  870, "Innsbruck"),
    ("SKU-2002", "Lichtschranke 5m",         "sensors",      78.60,  214, "Wien"),
    ("SKU-2003", "Druckaufnehmer 0-10bar",   "sensors",     156.40,    0, "Wien"),
    ("SKU-3001", "SPS Kompaktsteuerung",     "controllers", 998.00,   27, "Innsbruck"),
    ("SKU-3002", "IO-Modul 16DI",            "controllers", 187.30,  156, "Innsbruck"),
    ("SKU-4001", "Profinet-Switch 8-Port",   "networking",  445.00,   61, "Wien"),
    ("SKU-4002", "M12-Patchkabel 5m",        "networking",   23.90, 1420, "Innsbruck"),
]

# (category, line_name, backlog_days, setup_days, daily_capacity)
#   backlog_days   - Arbeitstage, bis die Linie frei ist
#   setup_days     - Ruestzeit fuer einen neuen Auftrag
#   daily_capacity - Stueck pro Arbeitstag
PRODUCTION_LINES = [
    ("drives",      "Linie A - Antriebsmontage", 4, 3,  25),
    ("sensors",     "Linie B - Sensorfertigung", 2, 1, 200),
    ("controllers", "Linie C - Steuerungsbau",   6, 5,  10),
    ("networking",  "Linie D - Konfektion",      1, 1, 300),
]

# (warehouse, transit_days)
SHIPPING = [
    ("Innsbruck", 2),
    ("Wien",      3),
]


def seed() -> None:
    con = sqlite3.connect(INVENTORY_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS products (
            sku TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
            unit_price REAL NOT NULL, stock INTEGER NOT NULL, warehouse TEXT NOT NULL
        )
    """)
    con.executemany("INSERT OR REPLACE INTO products VALUES (?, ?, ?, ?, ?, ?)", PRODUCTS)
    con.commit()
    con.close()

    con = sqlite3.connect(PRODUCTION_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS production_lines (
            category TEXT PRIMARY KEY, line_name TEXT NOT NULL,
            backlog_days INTEGER NOT NULL, setup_days INTEGER NOT NULL,
            daily_capacity INTEGER NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS shipping (
            warehouse TEXT PRIMARY KEY, transit_days INTEGER NOT NULL
        )
    """)
    con.executemany("INSERT OR REPLACE INTO production_lines VALUES (?, ?, ?, ?, ?)", PRODUCTION_LINES)
    con.executemany("INSERT OR REPLACE INTO shipping VALUES (?, ?)", SHIPPING)
    con.commit()
    con.close()


if __name__ == "__main__":
    seed()
    print(f"WMS angelegt: {INVENTORY_DB}  ({len(PRODUCTS)} Produkte)")
    print(f"MES angelegt: {PRODUCTION_DB}  ({len(PRODUCTION_LINES)} Linien, {len(SHIPPING)} Lager)")
