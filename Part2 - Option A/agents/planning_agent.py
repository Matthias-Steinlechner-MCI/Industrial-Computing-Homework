"""Planungs-Agent - berechnet Liefertermine aus dem MES (production.db).

Start:  uv run agents/planning_agent.py      (Port 8103)
"""
import math
import os
import sqlite3
from datetime import date, timedelta

import uvicorn
from fasta2a.pydantic_ai import agent_to_a2a
from pydantic_ai import Agent

from common import agent_url, make_model, skill
from seed import PRODUCTION_DB, seed

PORT = int(os.getenv("PLANNING_PORT", "8103"))

agent = Agent(
    make_model("PLANNING_MODEL"),
    name="planning-agent",
    system_prompt=(
        "Du bist der Planungs-Agent der Fertigung. Du berechnest Liefertermine "
        "ausschliesslich ueber dein Tool plan_delivery. Du brauchst Kategorie, "
        "Lagerort, die sofort lieferbare Menge und die Fehlmenge. Gib die "
        "Termine fuer Lagerware und Fertigungsmenge getrennt an, mit "
        "Aufschluesselung der Arbeitstage. Wichtig: delivery_date ist der Tag "
        "der Zustellung beim Kunden (inklusive Transportzeit), nicht der "
        "Versandtag - nenne es auch so. Steht die Kategorie oder der Lagerort "
        "nicht ausdruecklich im Auftrag, rate NICHT und rufe das Tool nicht auf: "
        "Antworte stattdessen, welche Angabe fehlt. Ein geratener Lagerort "
        "ergibt einen falschen Termin."
    ),
)


def add_business_days(start: date, days: int) -> date:
    """Zaehlt Arbeitstage (Mo-Fr) ab einem Startdatum."""
    current = start
    while days > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days -= 1
    return current


@agent.tool_plain
def plan_delivery(category: str, warehouse: str, available_now: int, shortfall: int) -> dict:
    """Berechnet Liefertermine fuer Lagerware und Fehlmenge.

    Lagerware geht sofort in den Versand. Die Fehlmenge wird gefertigt:
    Wartezeit bis die Linie frei ist + Ruestzeit + Fertigungsdauer laut
    Tageskapazitaet, danach Versand ab dem Lagerort.

    Args:
        category: drives, sensors, controllers oder networking.
        warehouse: Lagerort, von dem versendet wird (Innsbruck oder Wien).
        available_now: Stueck, die sofort ab Lager gehen.
        shortfall: Stueck, die gefertigt werden muessen.
    """
    con = sqlite3.connect(PRODUCTION_DB)
    con.row_factory = sqlite3.Row
    try:
        line = con.execute(
            "SELECT * FROM production_lines WHERE category = ?", (category.strip().lower(),)
        ).fetchone()
        ship = con.execute(
            "SELECT transit_days FROM shipping WHERE warehouse = ?", (warehouse.strip(),)
        ).fetchone()
    finally:
        con.close()

    if line is None:
        return {"error": f"Keine Fertigungslinie fuer Kategorie '{category}'."}
    if ship is None:
        return {"error": f"Lagerort '{warehouse}' unbekannt."}

    today = date.today()
    transit = ship["transit_days"]
    result = {"planned_on": today.isoformat(), "warehouse": warehouse, "transit_days": transit}

    if available_now > 0:
        result["from_stock"] = {
            "units": available_now,
            "delivery_date": add_business_days(today, transit).isoformat(),
            "business_days": transit,
        }

    if shortfall > 0:
        run_days = math.ceil(shortfall / line["daily_capacity"])
        total = line["backlog_days"] + line["setup_days"] + run_days + transit
        result["from_production"] = {
            "units": shortfall,
            "line": line["line_name"],
            "backlog_days": line["backlog_days"],
            "setup_days": line["setup_days"],
            "run_days": run_days,
            "daily_capacity": line["daily_capacity"],
            "business_days": total,
            "delivery_date": add_business_days(today, total).isoformat(),
        }

    return result


app = agent_to_a2a(
    agent,
    name="planning-agent",
    url=agent_url(PORT),
    description=(
        "Planungs-Agent (MES): berechnet Liefertermine. Lagerware geht sofort "
        "in den Versand, Fehlmengen werden auf der passenden Fertigungslinie "
        "eingeplant (Auslastung, Ruestzeit, Kapazitaet)."
    ),
    skills=[
        skill("plan_delivery", "Lieferterminplanung",
              "Berechnet Liefertermine fuer Lagerware und Fertigungsmengen in Arbeitstagen.",
              ["production", "planning", "delivery", "lead time"],
              ["Wann koennen 50 Stueck Druckaufnehmer geliefert werden?"]),
    ],
)

if __name__ == "__main__":
    seed()
    uvicorn.run(app, host="0.0.0.0", port=PORT)
