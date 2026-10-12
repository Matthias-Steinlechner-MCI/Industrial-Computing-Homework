"""Angebots-Agent - Preisengine mit marginaler Mengenstaffel.

Die Staffel ist dieselbe wie in Part 1. Neu ist die Freigabegrenze:
Angebote ueber 10.000 EUR werden als freigabepflichtig markiert
(Human-in-the-Loop).

Ein Angebot umfasst immer ALLE Positionen einer Bestellung, und die
Freigabegrenze gilt fuer die Gesamtsumme. Getrennte Angebote pro Position
wuerden die Grenze aushebeln: zwei Positionen zu je 6.000 EUR laegen
einzeln darunter, zusammen aber darueber.

Start:  uv run agents/quote_agent.py      (Port 8102)
"""
import os
from datetime import date, timedelta
from uuid import uuid4

import uvicorn
from fasta2a.pydantic_ai import agent_to_a2a
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from common import agent_url, make_model, skill

PORT = int(os.getenv("QUOTE_PORT", "8102"))

# Marginale Staffel: jede Stufe gilt nur fuer die Stueck in ihrer Bandbreite.
VOLUME_TIERS = [
    (1, 9, 0.00),
    (10, 49, 0.05),
    (50, 199, 0.12),
    (200, 499, 0.18),
    (500, None, 0.25),
]
CUSTOMER_TIERS = {"standard": 0.00, "silver": 0.02, "gold": 0.04, "platinum": 0.07}
APPROVAL_LIMIT_EUR = 10_000.00
QUOTE_VALID_DAYS = 14


class QuoteLine(BaseModel):
    sku: str = Field(description="Artikelnummer, z.B. SKU-1001")
    unit_price: float = Field(description="Listenpreis pro Stueck in EUR")
    quantity: int = Field(description="Bestellmenge in Stueck")


agent = Agent(
    make_model("QUOTE_MODEL"),
    name="quote-agent",
    system_prompt=(
        "Du bist der Angebots-Agent. Du berechnest verbindliche Angebotspreise "
        "ausschliesslich ueber dein Tool create_quote - rechne nie selbst. Pro "
        "Position brauchst du SKU, Listenpreis und Menge, dazu den Kundenstatus "
        "(fehlt er, nimm 'standard'). Erstelle fuer eine Bestellung immer EIN "
        "Angebot mit allen Positionen, niemals mehrere Einzelangebote. Gib die "
        "Angebotsnummer, die Positionen mit Staffel, den Endbetrag, die "
        "Gueltigkeit und den Freigabestatus zurueck."
    ),
)


def _price_line(line: QuoteLine) -> dict:
    breakdown, net = [], 0.0
    for lower, upper, rate in VOLUME_TIERS:
        if line.quantity < lower:
            break
        units = (min(line.quantity, upper) if upper else line.quantity) - lower + 1
        amount = units * line.unit_price * (1 - rate)
        net += amount
        breakdown.append({
            "range": f"{lower}-{upper}" if upper else f"{lower}+",
            "units": units, "rate": rate, "net": round(amount, 2),
        })
    return {
        "sku": line.sku,
        "quantity": line.quantity,
        "unit_price": round(line.unit_price, 2),
        "list_total": round(line.quantity * line.unit_price, 2),
        "after_volume_discount": round(net, 2),
        "volume_breakdown": breakdown,
        "_net": net,
    }


@agent.tool_plain
def create_quote(lines: list[QuoteLine], customer_tier: str = "standard") -> dict:
    """Erstellt EIN Angebot ueber alle Positionen einer Bestellung.

    Pro Position gilt die marginale Mengenstaffel, danach wird der
    Kundenstatus-Rabatt auf die Summe angewendet. Die Freigabegrenze von
    10.000 EUR gilt fuer den Endbetrag des gesamten Angebots.

    Args:
        lines: Alle Positionen der Bestellung.
        customer_tier: standard, silver, gold oder platinum.
    """
    tier = customer_tier.lower().strip()
    if tier not in CUSTOMER_TIERS:
        return {"error": f"Kundenstatus '{customer_tier}' unbekannt. Erlaubt: {', '.join(CUSTOMER_TIERS)}"}
    if not lines:
        return {"error": "Ein Angebot braucht mindestens eine Position."}
    if any(l.quantity < 1 or l.unit_price < 0 for l in lines):
        return {"error": "Jede Position braucht Menge >= 1 und Preis >= 0."}

    priced = [_price_line(l) for l in lines]
    list_total = sum(p["list_total"] for p in priced)
    subtotal = sum(p.pop("_net") for p in priced)
    final_total = subtotal * (1 - CUSTOMER_TIERS[tier])

    return {
        "quote_id": f"Q-{date.today():%Y%m%d}-{uuid4().hex[:6].upper()}",
        "lines": priced,
        "list_total": round(list_total, 2),
        "after_volume_discount": round(subtotal, 2),
        "customer_tier": tier,
        "customer_discount": CUSTOMER_TIERS[tier],
        "final_total": round(final_total, 2),
        "effective_discount": round((list_total - final_total) / list_total, 4) if list_total else 0.0,
        "valid_until": (date.today() + timedelta(days=QUOTE_VALID_DAYS)).isoformat(),
        "requires_manager_approval": final_total > APPROVAL_LIMIT_EUR,
        "approval_limit_eur": APPROVAL_LIMIT_EUR,
    }


app = agent_to_a2a(
    agent,
    name="quote-agent",
    url=agent_url(PORT),
    description=(
        "Angebots-Agent (Preisengine): erstellt EIN Angebot ueber alle "
        "Positionen einer Bestellung - marginale Mengenstaffel je Position, "
        "Kundenstatus-Rabatt auf die Summe, Angebotsnummer und Gueltigkeit. "
        "Angebote ueber 10.000 EUR Gesamtsumme sind freigabepflichtig."
    ),
    skills=[
        skill("create_quote", "Angebotserstellung",
              "Berechnet ein Angebot ueber alle Positionen (SKU, Listenpreis, Menge) und den Kundenstatus.",
              ["pricing", "quote", "discount", "approval"],
              ["Angebot fuer 3x SKU-3001 zu 998 EUR und 40x SKU-3002 zu 187,30 EUR, Standard-Kunde"]),
    ],
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
