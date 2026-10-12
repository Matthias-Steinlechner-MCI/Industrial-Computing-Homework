"""Lager-Agent - kennt den Bestand aus dem WMS (inventory.db).

Start:  uv run agents/inventory_agent.py      (Port 8101)
"""
import os
import sqlite3

import uvicorn
from fasta2a.pydantic_ai import agent_to_a2a
from pydantic_ai import Agent

from common import agent_url, make_model, skill
from seed import INVENTORY_DB, seed

PORT = int(os.getenv("INVENTORY_PORT", "8101"))

agent = Agent(
    make_model("INVENTORY_MODEL"),
    name="inventory-agent",
    system_prompt=(
        "Du bist der Lager-Agent eines Herstellers von Industriekomponenten. "
        "Du beantwortest ausschliesslich Fragen zu Artikeln, Preisen laut "
        "Preisliste und Lagerbestand. Nutze immer deine Tools, erfinde keine "
        "Zahlen. Gib SKU, Name, Kategorie, Listenpreis, Lagerort, Bestand und "
        "eine eventuelle Fehlmenge strukturiert zurueck."
    ),
)


def _query(sql: str, params: dict) -> list[dict]:
    con = sqlite3.connect(INVENTORY_DB)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


@agent.tool_plain
def search_products(query: str = "", category: str = "") -> list[dict]:
    """Sucht Artikel per Freitext auf SKU oder Name, optional nach Kategorie.

    Args:
        query: Suchbegriff, z.B. "Servomotor" oder "SKU-2001". Leer = alle.
        category: drives, sensors, controllers oder networking. Leer = alle.
    """
    return _query(
        """
        SELECT sku, name, category, unit_price, stock, warehouse FROM products
        WHERE (:q = '' OR sku LIKE :like OR name LIKE :like)
          AND (:cat = '' OR category = :cat)
        ORDER BY name
        """,
        {"q": query, "like": f"%{query}%", "cat": category},
    )


@agent.tool_plain
def check_availability(sku: str, quantity: int) -> dict:
    """Prueft, ob eine Menge eines Artikels sofort ab Lager lieferbar ist.

    Args:
        sku: Artikelnummer, z.B. "SKU-1001".
        quantity: Gewuenschte Stueckzahl.

    Returns:
        Artikeldaten plus available_now (ab Lager) und shortfall (Fehlmenge).
    """
    rows = _query(
        "SELECT sku, name, category, unit_price, stock, warehouse FROM products WHERE sku = :sku",
        {"sku": sku.strip().upper()},
    )
    if not rows:
        return {"error": f"Artikel {sku} existiert nicht im Lager."}

    item = rows[0]
    available = min(item["stock"], quantity)
    return {
        **item,
        "requested": quantity,
        "available_now": available,
        "shortfall": quantity - available,
    }


app = agent_to_a2a(
    agent,
    name="inventory-agent",
    url=agent_url(PORT),
    description=(
        "Lager-Agent (WMS): kennt alle Artikel, Listenpreise, Lagerorte und "
        "Bestaende. Prueft, ob eine Bestellmenge sofort lieferbar ist und "
        "meldet die Fehlmenge."
    ),
    skills=[
        skill("search_products", "Artikelsuche",
              "Findet Artikel per Name, SKU oder Kategorie samt Preis und Bestand.",
              ["inventory", "catalog", "price"], ["Welche Servomotoren gibt es?"]),
        skill("check_availability", "Verfuegbarkeitspruefung",
              "Prueft eine Bestellmenge gegen den Lagerbestand und meldet die Fehlmenge.",
              ["inventory", "stock", "availability"], ["Sind 200 Stueck SKU-1001 lagernd?"]),
    ],
)

if __name__ == "__main__":
    seed()
    uvicorn.run(app, host="0.0.0.0", port=PORT)
