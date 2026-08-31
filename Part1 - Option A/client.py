"""ReAct-Agent, der die MCP-Tools ueber den LiteLLM-Proxy nutzt.

Der Ablauf entspricht dem Muster aus Sample 4 der Vorlesung:
  1. Verbindung zum MCP-Server, Tools dynamisch entdecken
  2. MCP-Toolschemas in das OpenAI-Function-Format uebersetzen
  3. ReAct-Schleife: Reasoning -> Action -> Observation, bis das Modell
     eine textuelle Endantwort liefert

Alles, was auf der Konsole erscheint, wird zusaetzlich nach
logs/execution_log.txt geschrieben - das ist der geforderte Execution-Log.

Start:  uv run client.py            (alle Szenarien)
        uv run client.py 2          (nur Szenario 2)
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from openai import OpenAI

load_dotenv()

BASE_DIR = Path(__file__).parent
EXECUTION_LOG = BASE_DIR / "logs" / "execution_log.txt"

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8010/mcp")
MODEL_NAME = os.getenv("LITELLM_DEFAULT_MODEL", "gpt-4o-mini")
MAX_STEPS = 8

llm_client = OpenAI(
    base_url=os.getenv("LITELLM_API"),
    api_key=os.getenv("LITELLM_KEY"),
)

SYSTEM_PROMPT = (
    "Du bist ein Vertriebsassistent fuer Industriekomponenten und arbeitest "
    "nach dem ReAct-Muster: denken, ein Tool aufrufen, das Ergebnis auswerten, "
    "erneut denken. Erfinde niemals Preise, Bestaende oder Rabatte - hole "
    "jede Zahl ueber die bereitgestellten Tools. Protokolliere abgeschlossene "
    "Angebote am Ende mit append_audit_event."
)

SCENARIOS = [
    "Wie viele Servomotoren haben wir auf Lager und was kosten sie?",

    "Ein Kunde mit Gold-Status will 250 Stueck des Naeherungssensors M12. "
    "Ermittle den Listenpreis aus der Datenbank, berechne den Endpreis mit "
    "der Rabattstaffel und protokolliere das Angebot im Audit-Log.",

    "Welche Produkte sind aktuell nicht lieferbar? Protokolliere das Ergebnis "
    "als Ereignis 'stockout_review' im Audit-Log.",
]


# ---------------------------------------------------------------------------
# Logging - Konsole und Datei gleichzeitig
# ---------------------------------------------------------------------------
def log(message: str = "") -> None:
    print(message)
    EXECUTION_LOG.parent.mkdir(parents=True, exist_ok=True)
    with EXECUTION_LOG.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def convert_mcp_to_openai_tools(mcp_tools) -> list[dict]:
    """Uebersetzt die MCP-Toolbeschreibungen in OpenAI-Function-Schemas."""
    tools = []
    for tool in mcp_tools.tools:
        # Aeltere mcp-Versionen nennen das Feld inputSchema, neuere input_schema.
        schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
        tools.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": schema or {"type": "object", "properties": {}},
            },
        })
    return tools


# ---------------------------------------------------------------------------
# ReAct-Schleife
# ---------------------------------------------------------------------------
async def run_react_agent(session: ClientSession, tools: list[dict], prompt: str) -> None:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    for step in range(1, MAX_STEPS + 1):
        log(f"[Reasoning {step}] Anfrage an das Modell ({MODEL_NAME}) ...")

        response = llm_client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        message = response.choices[0].message

        # Endantwort erreicht - die Schleife ist fertig
        if not message.tool_calls:
            log("")
            log("[Final Answer]")
            log(message.content or "(leere Antwort)")
            return

        messages.append(message)

        for call in message.tool_calls:
            args = json.loads(call.function.arguments or "{}")
            log(f"[Action] {call.function.name}({json.dumps(args, ensure_ascii=False)})")

            result = await session.call_tool(call.function.name, args)
            observation = "\n".join(
                block.text for block in result.content if hasattr(block, "text")
            )

            preview = observation if len(observation) <= 400 else observation[:400] + " ..."
            log(f"[Observation] {preview}")

            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": observation,
            })
        log("")

    log(f"[Abbruch] Kein Ergebnis nach {MAX_STEPS} Schritten.")


async def main() -> None:
    selected = SCENARIOS
    if len(sys.argv) > 1:
        index = int(sys.argv[1]) - 1
        selected = [SCENARIOS[index]]

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log("=" * 78)
    log(f"Lauf {stamp} | Modell {MODEL_NAME} | MCP {MCP_SERVER_URL}")
    log("=" * 78)

    # Je nach mcp-Version liefert der Client zwei oder drei Werte
    # (aeltere Versionen haengen einen get_session_id-Callback an).
    async with streamable_http_client(MCP_SERVER_URL) as streams:
        read_stream, write_stream = streams[0], streams[1]
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            mcp_tools = await session.list_tools()
            tools = convert_mcp_to_openai_tools(mcp_tools)
            log(f"Verbunden. Entdeckte Tools: {[t['function']['name'] for t in tools]}")

            resources = await session.list_resources()
            log(f"Entdeckte Resources: {[str(r.uri) for r in resources.resources]}")

            for number, prompt in enumerate(selected, start=1):
                log("")
                log("-" * 78)
                log(f"SZENARIO {number}: {prompt}")
                log("-" * 78)
                await run_react_agent(session, tools, prompt)

            log("")
            log("=" * 78)
            log("Audit-Log ueber MCP-Resource gelesen:")
            content = await session.read_resource("audit://log")
            for block in content.contents:
                if hasattr(block, "text"):
                    log(block.text.rstrip())


if __name__ == "__main__":
    asyncio.run(main())
