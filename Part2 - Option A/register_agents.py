"""Traegt die laufenden Agenten in die LiteLLM-Agent-Registry ein.

Die Agenten beschreiben sich selbst: Jeder liefert unter
/.well-known/agent-card.json seinen Namen, seine Beschreibung und seine
Skills. Dieses Skript liest die Karten und registriert sie in LiteLLM.
Ein bereits registrierter Agent mit gleichem Namen wird aktualisiert,
nicht doppelt angelegt.

Der Koordinator kennt danach keine Ports und keine Namen - er findet die
Agenten ueber die Registry.

Hinweis zu LiteLLM: Die Agenten liegen dauerhaft in Postgres, werden nach
einem Proxy-Neustart aber nicht zuverlaessig in den Speicher geladen. Ein
erneutes POST scheitert dann am Unique-Constraint auf agent_name. Darum
merkt sich dieses Skript die vergebenen IDs in .registry_ids.json und
aktualisiert per PUT - das holt den Agenten wieder in die Registry.

Start:  uv run register_agents.py        (Agenten muessen laufen)
        uv run register_agents.py --list (nur anzeigen)
"""
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

ID_CACHE = Path(__file__).parent / ".registry_ids.json"

LITELLM_URL = os.getenv("LITELLM_URL", "http://localhost:4000")
HEADERS = {"Authorization": f"Bearer {os.getenv('LITELLM_KEY', 'sk-1234')}"}
HOST = os.getenv("AGENT_HOST", "localhost")

AGENT_ENDPOINTS = [
    f"http://{HOST}:{os.getenv('INVENTORY_PORT', '8101')}",
    f"http://{HOST}:{os.getenv('QUOTE_PORT', '8102')}",
    f"http://{HOST}:{os.getenv('PLANNING_PORT', '8103')}",
]


def to_litellm_card(card: dict, fallback_url: str) -> dict:
    """Uebersetzt eine A2A-1.0-Agent-Card in das Format, das LiteLLM erwartet.

    A2A 1.0 fuehrt die Adresse unter supportedInterfaces[].url, LiteLLM
    erwartet sie als url auf oberster Ebene. Skills werden 1:1 uebernommen.
    """
    interfaces = card.get("supportedInterfaces") or [{}]
    return {
        "protocolVersion": interfaces[0].get("protocolVersion", "1.0"),
        "name": card["name"],
        "description": card.get("description", ""),
        "url": interfaces[0].get("url") or card.get("url") or fallback_url,
        "version": card.get("version", "1.0.0"),
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "capabilities": card.get("capabilities", {}),
        "skills": card.get("skills", []),
    }


def list_registered(client: httpx.Client) -> list[dict]:
    resp = client.get(f"{LITELLM_URL}/v1/agents", headers=HEADERS)
    resp.raise_for_status()
    return resp.json()


def load_id_cache() -> dict[str, str]:
    try:
        return json.loads(ID_CACHE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def register(client: httpx.Client) -> None:
    # Bekannte IDs: zuerst aus dem lokalen Cache, dann was der Proxy gerade kennt
    known = load_id_cache()
    known.update({a["agent_name"]: a["agent_id"] for a in list_registered(client)})

    for endpoint in AGENT_ENDPOINTS:
        try:
            card = client.get(f"{endpoint}/.well-known/agent-card.json").json()
        except httpx.HTTPError as exc:
            print(f"  [uebersprungen] {endpoint} nicht erreichbar ({exc.__class__.__name__})")
            continue

        name = card["name"]
        body = {"agent_name": name, "agent_card_params": to_litellm_card(card, endpoint)}

        resp, action = None, "registriert"
        if name in known:
            resp = client.put(f"{LITELLM_URL}/v1/agents/{known[name]}", headers=HEADERS, json=body)
            action = "aktualisiert"
            if resp.status_code == 404:  # ID gibt es nicht mehr -> neu anlegen
                resp = None
        if resp is None:
            resp = client.post(f"{LITELLM_URL}/v1/agents", headers=HEADERS, json=body)
            action = "registriert"

        if resp.is_error:
            hint = ""
            if "Unique constraint" in resp.text:
                hint = (" -> Name ist in der LiteLLM-DB schon vergeben, aber die ID ist "
                        "unbekannt. Agent in der Admin-UI (Agents) loeschen und neu registrieren.")
            print(f"  [Fehler] {name}: HTTP {resp.status_code} {resp.text[:200]}{hint}")
            continue

        known[name] = resp.json().get("agent_id", known.get(name))
        print(f"  [{action}] {name:<16} -> {body['agent_card_params']['url']}  ({known[name]})")

    ID_CACHE.write_text(json.dumps(known, indent=2), encoding="utf-8")


def show(client: httpx.Client) -> None:
    agents = list_registered(client)
    print(f"\n{len(agents)} Agent(en) in der Registry:")
    for a in agents:
        params = a.get("agent_card_params", {})
        skills = ", ".join(s.get("id", "?") for s in params.get("skills", []))
        print(f"  - {a['agent_name']:<16} {params.get('url', '?'):<24} skills: {skills}")


if __name__ == "__main__":
    with httpx.Client(timeout=15) as http:
        if "--list" not in sys.argv:
            print(f"Registriere Agenten bei {LITELLM_URL} ...")
            register(http)
        show(http)
