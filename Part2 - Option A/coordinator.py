"""Koordinator - der Root-Agent, der die Fach-Agenten zur Laufzeit findet.

Ablauf pro Kundenanfrage:
  1. DISCOVER  Verfuegbare Agenten aus der LiteLLM-Registry holen
               (/v1/agents?health_check=true liefert nur erreichbare).
               Der Koordinator kennt weder Ports noch Namen im Voraus.
  2. PLAN      Aus Beschreibung und Skills der Agenten baut er sein
               Werkzeug delegate_task - die erlaubten Agentennamen sind
               ein Enum, das bei jedem Lauf neu aus der Registry entsteht.
  3. DELEGATE  Auftraege gehen per A2A ueber das LiteLLM-Gateway an die
               Agenten. Unabhaengige Auftraege laufen parallel.
  4. CHECK     Nach jeder Runde entscheidet er, ob die Anfrage vollstaendig
               beantwortet ist. Erst submit_final_answer beendet die Schleife,
               und dabei muss er jeden Teil der Anfrage abhaken.

Schritt 4 schliesst die Luecke, die in der Vorlesung bei
main_dynamic_lookup.py offen blieb: Dort weist der Router eine Anfrage
genau einmal einem Agenten zu und prueft nicht, ob der Kunde alles hat.

Start:  uv run coordinator.py          (alle Szenarien)
        uv run coordinator.py 2        (nur Szenario 2)
        uv run coordinator.py "freie Anfrage in Anfuehrungszeichen"
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

load_dotenv()

LITELLM_URL = os.getenv("LITELLM_URL", "http://localhost:4000")
LITELLM_KEY = os.getenv("LITELLM_KEY", "sk-1234")
HEADERS = {"Authorization": f"Bearer {LITELLM_KEY}"}

# Mehrere Modelle, kommagetrennt: bei Rate-Limit wird das naechste genommen.
COORDINATOR_MODELS = [m.strip() for m in os.getenv("COORDINATOR_MODEL", "gpt-4o-mini").split(",") if m.strip()]

MAX_ROUNDS = 10
TASK_TIMEOUT_S = 180
LOG_FILE = Path(__file__).parent / "logs" / "coordinator_run.txt"

SCENARIOS = [
    "Wir sind Gold-Kunde und brauchen 200 Stueck Servomotor 400W. "
    "Bitte ein Angebot und den Liefertermin.",

    "Was kosten 50 Druckaufnehmer 0-10bar fuer uns als Silver-Kunde, "
    "und wann koennten die bei uns sein?",

    "Fuer eine neue Anlage brauchen wir 3 SPS Kompaktsteuerungen und 40 "
    "IO-Module 16DI. Wir sind Standard-Kunde. Was kostet das insgesamt und "
    "wann ist alles da?",
]

llm = AsyncOpenAI(base_url=LITELLM_URL, api_key=LITELLM_KEY)


# ---------------------------------------------------------------------------
# Protokoll - Konsole und Datei
# ---------------------------------------------------------------------------
def log(message: str = "") -> None:
    print(message, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


# ---------------------------------------------------------------------------
# 1. DISCOVER - Agenten aus der Registry
# ---------------------------------------------------------------------------
async def discover_agents(client: httpx.AsyncClient) -> dict[str, dict]:
    resp = await client.get(f"{LITELLM_URL}/v1/agents", params={"health_check": "true"}, headers=HEADERS)
    resp.raise_for_status()

    agents = {}
    for entry in resp.json():
        card = entry.get("agent_card_params", {})
        agents[entry["agent_name"]] = {
            "id": entry["agent_id"],
            "description": card.get("description", ""),
            "skills": [f"{s.get('name')}: {s.get('description')}" for s in card.get("skills", [])],
        }
    return agents


def build_tools(agents: dict[str, dict]) -> list[dict]:
    """Die Werkzeuge des Koordinators - das Enum entsteht aus der Registry."""
    return [
        {
            "type": "function",
            "function": {
                "name": "delegate_task",
                "description": (
                    "Gibt einen Teilauftrag per A2A an einen Fach-Agenten. Formuliere "
                    "den Auftrag vollstaendig und mit allen noetigen Zahlen - der Agent "
                    "kennt das Gespraech nicht. Unabhaengige Auftraege an verschiedene "
                    "Agenten duerfen im selben Schritt parallel erfolgen."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent_name": {"type": "string", "enum": sorted(agents)},
                        "task": {"type": "string", "description": "Der vollstaendige Teilauftrag."},
                    },
                    "required": ["agent_name", "task"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "submit_final_answer",
                "description": (
                    "Beendet die Bearbeitung. Nur aufrufen, wenn JEDER Teil der "
                    "Kundenanfrage mit Daten aus den Agenten beantwortet ist."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string", "description": "Antwort an den Kunden."},
                        "checklist": {
                            "type": "array",
                            "description": "Jeder Teil der Anfrage und womit er erfuellt wurde.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "requirement": {"type": "string"},
                                    "fulfilled_by": {"type": "string"},
                                },
                                "required": ["requirement", "fulfilled_by"],
                            },
                        },
                    },
                    "required": ["answer", "checklist"],
                },
            },
        },
    ]


def system_prompt(agents: dict[str, dict]) -> str:
    directory = "\n".join(
        f"- {name}: {info['description']}\n    Skills: " + "; ".join(info["skills"])
        for name, info in agents.items()
    )
    return (
        "Du bist der Koordinator (Root-Agent) im Vertrieb eines Herstellers von "
        "Industriekomponenten. Du rechnest und schaetzt NICHTS selbst - jede Zahl "
        "kommt von einem Fach-Agenten. Diese Agenten sind laut Registry gerade "
        f"erreichbar:\n{directory}\n\n"
        "Vorgehen: Zerlege die Anfrage in Teilauftraege, delegiere sie, werte die "
        "Antworten aus und delegiere weiter, bis alles beantwortet ist. Typische "
        "Reihenfolge: erst Artikel und Bestand klaeren, dann mit dem Listenpreis "
        "das Angebot und mit Lagermenge/Fehlmenge den Liefertermin.\n"
        "Regeln fuer die Auftraege:\n"
        "- Fordere beim Bestand fuer jeden Artikel SKU, Listenpreis, Kategorie, "
        "Lagerort, sofort lieferbare Menge und Fehlmenge an.\n"
        "- Gib einem Planungsauftrag pro Artikel immer Kategorie, Lagerort, "
        "sofort lieferbare Menge und Fehlmenge mit.\n"
        "- Fordere fuer eine Bestellung EIN Angebot ueber alle Positionen an, "
        "nie mehrere Einzelangebote - die Freigabegrenze gilt fuer die Summe.\n"
        "- Ist das Angebot freigabepflichtig, sag dem Kunden das ausdruecklich.\n"
        "Beende mit submit_final_answer und hake dabei jeden Teil der Anfrage ab."
    )


# ---------------------------------------------------------------------------
# 3. DELEGATE - A2A ueber das LiteLLM-Gateway
# ---------------------------------------------------------------------------
def _state(task: dict) -> str:
    """Normalisiert 'TASK_STATE_COMPLETED' (A2A 1.0) und 'completed' (0.3)."""
    return str(task.get("status", {}).get("state", "")).lower().replace("task_state_", "")


def _text_of(task: dict) -> str:
    texts = [p["text"] for a in task.get("artifacts", []) for p in a.get("parts", []) if p.get("text")]
    if not texts:
        msg = task.get("status", {}).get("message") or {}
        texts = [p["text"] for p in msg.get("parts", []) if p.get("text")]
    return "\n".join(texts) or "(Agent hat keinen Text geliefert)"


async def call_agent(client: httpx.AsyncClient, agent_id: str, text: str) -> str:
    send = {
        "jsonrpc": "2.0",
        "id": str(uuid4()),
        "method": "message/send",
        "params": {"message": {
            "kind": "message", "role": "user", "messageId": str(uuid4()),
            "parts": [{"kind": "text", "text": text}],
        }},
    }
    resp = await client.post(f"{LITELLM_URL}/v1/a2a/{agent_id}/message/send", json=send, headers=HEADERS)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        return f"(A2A-Fehler: {data['error'].get('message')})"

    result = data["result"]
    if "message" in result:  # Agent hat direkt geantwortet
        return "\n".join(p.get("text", "") for p in result["message"].get("parts", []))

    # Sonst ein Task: der Agent arbeitet asynchron, Ergebnis per tasks/get abholen
    task = result.get("task", result)
    deadline = time.monotonic() + TASK_TIMEOUT_S
    while _state(task) in {"submitted", "working"}:
        if time.monotonic() > deadline:
            return f"(Zeitueberschreitung nach {TASK_TIMEOUT_S}s, Task {task['id']})"
        await asyncio.sleep(1.5)
        poll = {"jsonrpc": "2.0", "id": str(uuid4()), "method": "tasks/get", "params": {"id": task["id"]}}
        resp = await client.post(f"{LITELLM_URL}/a2a/{agent_id}", json=poll, headers=HEADERS)
        resp.raise_for_status()
        polled = resp.json()
        if "error" in polled:
            return f"(A2A-Fehler beim Abholen: {polled['error'].get('message')})"
        task = polled["result"].get("task", polled["result"])

    if _state(task) != "completed":
        return f"(Agent meldet Status '{_state(task)}': {_text_of(task)})"
    return _text_of(task)


# ---------------------------------------------------------------------------
# LLM-Aufruf mit Modellwechsel
# ---------------------------------------------------------------------------
# Free-Tier-Modelle sind mal erschoepft (429), mal ueberlastet (503 "high
# demand"). Beides ist voruebergehend - also das naechste Modell probieren.
TRANSIENT_ERRORS = (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError)


class AllModelsUnavailable(Exception):
    pass


def _is_quota_error(exc: APIStatusError) -> bool:
    """LiteLLM reicht Gemini-Kontingentfehler teils als 403 mit '429' im Text weiter."""
    text = str(exc)
    return "429" in text or "quota" in text.lower() or "RESOURCE_EXHAUSTED" in text


async def ask_llm(messages: list, tools: list):
    for model in COORDINATOR_MODELS:
        try:
            resp = await llm.chat.completions.create(
                model=model, messages=messages, tools=tools, tool_choice="required",
            )
            return resp.choices[0].message, model
        except TRANSIENT_ERRORS as exc:
            reason = exc.__class__.__name__
        except APIStatusError as exc:
            if not _is_quota_error(exc):
                raise
            reason = f"Kontingent erschoepft, HTTP {exc.status_code}"
        log(f"  [Modellwechsel] {model} nicht verfuegbar ({reason}), naechstes Modell")
    raise AllModelsUnavailable(", ".join(COORDINATOR_MODELS))


# ---------------------------------------------------------------------------
# 2.-4. Koordinationsschleife
# ---------------------------------------------------------------------------
async def handle_request(client: httpx.AsyncClient, request: str) -> None:
    agents = await discover_agents(client)
    log(f"[Discover] Registry liefert {len(agents)} erreichbare Agenten: {', '.join(sorted(agents)) or '-'}")
    if not agents:
        log("[Abbruch] Keine Agenten erreichbar - laufen sie und sind sie registriert?")
        return

    tools = build_tools(agents)
    messages = [
        {"role": "system", "content": system_prompt(agents)},
        {"role": "user", "content": request},
    ]

    for round_no in range(1, MAX_ROUNDS + 1):
        try:
            message, model = await ask_llm(messages, tools)
        except AllModelsUnavailable as exc:
            log(f"\n[Abbruch] Kein Koordinator-Modell verfuegbar ({exc}). "
                "Free-Tier-Kontingent fuer heute erschoepft - spaeter erneut starten.")
            return
        calls = message.tool_calls or []
        log(f"\n[Runde {round_no}] Koordinator ({model}) plant {len(calls)} Schritt(e)")
        messages.append(message)

        delegations = []
        for call in calls:
            args = json.loads(call.function.arguments or "{}")

            if call.function.name == "submit_final_answer":
                log("[Check] Koordinator erklaert die Anfrage fuer vollstaendig beantwortet:")
                for item in args.get("checklist", []):
                    log(f"   [x] {item.get('requirement')}  <-  {item.get('fulfilled_by')}")
                log("\n[Antwort an den Kunden]")
                log(args.get("answer", ""))
                return

            target = args.get("agent_name")
            if target not in agents:
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": f"Agent '{target}' ist nicht in der Registry."})
                continue
            log(f"  [Delegate -> {target}] {args.get('task')}")
            delegations.append((call, target, args.get("task", "")))

        # Unabhaengige Auftraege laufen parallel an die verteilten Agenten
        started = time.monotonic()
        results = await asyncio.gather(*(call_agent(client, agents[t]["id"], task) for _, t, task in delegations))
        if delegations:
            log(f"  ({len(delegations)} Agent(en) antworteten nach {time.monotonic() - started:.1f}s)")

        for (call, target, _), answer in zip(delegations, results):
            preview = answer if len(answer) <= 600 else answer[:600] + " ..."
            log(f"  [Result <- {target}] " + preview.replace("\n", "\n      "))
            messages.append({"role": "tool", "tool_call_id": call.id, "content": answer})

    log(f"\n[Abbruch] Keine vollstaendige Antwort nach {MAX_ROUNDS} Runden.")


async def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        requests = [SCENARIOS[int(sys.argv[1]) - 1]]
    elif len(sys.argv) > 1:
        requests = [" ".join(sys.argv[1:])]
    else:
        requests = SCENARIOS

    log("=" * 78)
    log(f"Lauf {datetime.now(timezone.utc).isoformat(timespec='seconds')} | "
        f"Koordinator-Modelle {', '.join(COORDINATOR_MODELS)} | Registry {LITELLM_URL}")
    log("=" * 78)

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        for number, request in enumerate(requests, start=1):
            log("")
            log("-" * 78)
            log(f"ANFRAGE {number}: {request}")
            log("-" * 78)
            await handle_request(client, request)


if __name__ == "__main__":
    asyncio.run(main())
