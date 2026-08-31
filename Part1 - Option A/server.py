"""MCP Math & Database Tool Server.

Stellt drei MCP-Tools bereit:
  1. lookup_products        - SQL-Abfrage auf die SQLite-Produktdatenbank
  2. compute_tiered_discount - Formel-Engine fuer gestaffelte Mengenrabatte
  3. append_audit_event     - haengt Ereignisse an eine lokale Logdatei an

Zusaetzlich zwei Resources, ueber die der Client den Zustand lesen kann,
ohne ein Tool auszufuehren.

Start:  uv run server.py      (HTTP-Transport auf Port 8010)
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastmcp import FastMCP

from seed_db import DB_PATH, seed

BASE_DIR = Path(__file__).parent
AUDIT_LOG = BASE_DIR / "logs" / "audit_log.txt"

mcp = FastMCP("Math_And_Database_Tools")


# ---------------------------------------------------------------------------
# TOOL 1 - SQL-Lookup auf die Produktdatenbank
# ---------------------------------------------------------------------------
@mcp.tool()
def lookup_products(
    search: str = "",
    category: str = "",
    only_in_stock: bool = False,
    limit: int = 10,
) -> list[dict]:
    """Sucht Produkte im Lagerbestand.

    Args:
        search: Freitext, der gegen SKU und Produktname geprueft wird.
        category: Optionaler Filter auf die Kategorie
                  (drives, sensors, controllers, networking).
        only_in_stock: Wenn True, werden Produkte mit Bestand 0 ausgeblendet.
        limit: Maximale Anzahl Treffer.

    Returns:
        Liste von Produkten mit sku, name, category, unit_price, stock, warehouse.
    """
    # Parametrisierte Query - kein String-Concat, damit keine SQL-Injection
    # ueber vom LLM erzeugte Argumente moeglich ist.
    sql = """
        SELECT sku, name, category, unit_price, stock, warehouse
        FROM products
        WHERE (:search = '' OR sku LIKE :like OR name LIKE :like)
          AND (:category = '' OR category = :category)
          AND (:only_in_stock = 0 OR stock > 0)
        ORDER BY name
        LIMIT :limit
    """
    params = {
        "search": search,
        "like": f"%{search}%",
        "category": category,
        "only_in_stock": 1 if only_in_stock else 0,
        "limit": max(1, min(limit, 50)),
    }

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# TOOL 2 - Formel-Engine fuer gestaffelte Rabatte
# ---------------------------------------------------------------------------

# Marginale Staffelung: jede Stufe gilt nur fuer die Stueck IN dieser Stufe,
# nicht rueckwirkend fuer die gesamte Bestellung.
DISCOUNT_TIERS = [
    (1, 9, 0.00),
    (10, 49, 0.05),
    (50, 199, 0.12),
    (200, 499, 0.18),
    (500, None, 0.25),
]

# Zusaetzlicher Nachlass nach Kundenstatus, multiplikativ auf den Restpreis.
CUSTOMER_TIERS = {"standard": 0.00, "silver": 0.02, "gold": 0.04, "platinum": 0.07}


@mcp.tool()
def compute_tiered_discount(
    unit_price: float,
    quantity: int,
    customer_tier: str = "standard",
) -> dict:
    """Berechnet den Endpreis einer Bestellung mit marginaler Rabattstaffel.

    Die Mengenstaffel wirkt marginal: Bei 250 Stueck bekommen die ersten 9
    Stueck 0 %, die Stueck 10-49 fuenf Prozent, 50-199 zwoelf Prozent und
    200-250 achtzehn Prozent. Auf den so ermittelten Betrag wird anschliessend
    der Kundenstatus-Nachlass angewendet.

    Args:
        unit_price: Listenpreis pro Stueck.
        quantity: Bestellmenge in Stueck.
        customer_tier: standard, silver, gold oder platinum.

    Returns:
        Aufschluesselung mit Bruttobetrag, Rabatt je Stufe, Endbetrag und
        effektivem Rabattsatz.
    """
    if quantity < 1:
        raise ValueError("quantity muss mindestens 1 sein")
    if unit_price < 0:
        raise ValueError("unit_price darf nicht negativ sein")

    tier_key = customer_tier.lower().strip()
    if tier_key not in CUSTOMER_TIERS:
        raise ValueError(
            f"customer_tier '{customer_tier}' unbekannt. "
            f"Erlaubt: {', '.join(CUSTOMER_TIERS)}"
        )

    breakdown = []
    discounted_total = 0.0

    for lower, upper, rate in DISCOUNT_TIERS:
        if quantity < lower:
            break
        units = (min(quantity, upper) if upper else quantity) - lower + 1
        gross = units * unit_price
        saved = gross * rate
        discounted_total += gross - saved
        breakdown.append({
            "range": f"{lower}-{upper}" if upper else f"{lower}+",
            "units": units,
            "rate": rate,
            "gross": round(gross, 2),
            "saved": round(saved, 2),
        })

    list_total = quantity * unit_price
    loyalty_rate = CUSTOMER_TIERS[tier_key]
    loyalty_saved = discounted_total * loyalty_rate
    final_total = discounted_total - loyalty_saved

    return {
        "quantity": quantity,
        "unit_price": round(unit_price, 2),
        "list_total": round(list_total, 2),
        "volume_breakdown": breakdown,
        "customer_tier": tier_key,
        "loyalty_rate": loyalty_rate,
        "loyalty_saved": round(loyalty_saved, 2),
        "final_total": round(final_total, 2),
        "total_saved": round(list_total - final_total, 2),
        "effective_discount": round(
            (list_total - final_total) / list_total, 4
        ) if list_total else 0.0,
    }


# ---------------------------------------------------------------------------
# TOOL 3 - Audit-Log
# ---------------------------------------------------------------------------
@mcp.tool()
def append_audit_event(event: str, details: str = "") -> dict:
    """Haengt ein Ereignis an das lokale Audit-Log an.

    Args:
        event: Kurzbezeichnung des Ereignisses, z.B. "quote_created".
        details: Freitext mit Zusatzinformationen.

    Returns:
        Der geschriebene Eintrag samt Zeitstempel und Zeilennummer.
    """
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"{stamp} | {event} | {details}".rstrip(" |")

    with AUDIT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")

    with AUDIT_LOG.open("r", encoding="utf-8") as fh:
        count = sum(1 for _ in fh)

    return {"written": line, "line_number": count, "file": str(AUDIT_LOG)}


# ---------------------------------------------------------------------------
# RESOURCES - lesender Zugriff ohne Tool-Ausfuehrung
# ---------------------------------------------------------------------------
@mcp.resource("audit://log")
def read_audit_log() -> str:
    """Liefert den vollstaendigen Inhalt des Audit-Logs."""
    if not AUDIT_LOG.exists():
        return "(Audit-Log ist noch leer)"
    return AUDIT_LOG.read_text(encoding="utf-8")


@mcp.resource("inventory://schema")
def read_inventory_schema() -> str:
    """Beschreibt die Struktur der Produkttabelle."""
    return (
        "Tabelle products(sku TEXT PK, name TEXT, category TEXT, "
        "unit_price REAL, stock INTEGER, warehouse TEXT). "
        "Kategorien: drives, sensors, controllers, networking."
    )


if __name__ == "__main__":
    seed()
    print(f"SQLite bereit: {DB_PATH}")
    print("MCP-Server startet auf http://0.0.0.0:8010/mcp")
    mcp.run(transport="http", host="0.0.0.0", port=8010)
