"""Gemeinsame Bausteine fuer alle Fach-Agenten."""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import fasta2a.storage
from dotenv import load_dotenv
from fasta2a.schema import Skill
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider


class _RFC3339Datetime(datetime):
    """Zeitstempel in UTC mit 'Z' - so wie die A2A-Spezifikation sie verlangt.

    fasta2a 2.1 schreibt Task-Zeitstempel als lokale Zeit ohne Zeitzone
    (datetime.now().isoformat()). Das A2A-SDK im LiteLLM-Gateway parst sie
    per Protobuf und haelt das '-' im Datum fuer eine Zeitzonenangabe ->
    "Failed to parse timestamp field". Mit 'Z' kommt die Antwort durch.
    """

    @classmethod
    def now(cls, tz=None):
        d = datetime.now(timezone.utc)
        return cls(d.year, d.month, d.day, d.hour, d.minute, d.second, d.microsecond, tzinfo=timezone.utc)

    def isoformat(self, sep="T", timespec="auto"):
        return super().isoformat(sep, timespec).replace("+00:00", "Z")


fasta2a.storage.datetime = _RFC3339Datetime

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# data/ als Modulpfad, damit die Agenten den Seed direkt importieren koennen
sys.path.insert(0, str(ROOT / "data"))

LITELLM_URL = os.getenv("LITELLM_URL", "http://localhost:4000")
LITELLM_KEY = os.getenv("LITELLM_KEY", "sk-1234")
AGENT_MODEL = os.getenv("AGENT_MODEL", "gpt-4o-mini")
AGENT_HOST = os.getenv("AGENT_HOST", "localhost")


def make_model(model_env: str) -> FallbackModel:
    """Jeder Agent spricht sein LLM ueber den LiteLLM-Proxy an.

    Das Modell kommt pro Agent aus einer eigenen Variable (z.B. QUOTE_MODEL).
    So laufen die Agenten auf unterschiedlichen LLMs - der Proxy und das
    A2A-Protokoll machen das fuer die Zusammenarbeit irrelevant.

    Hinter dem eigenen Modell haengt AGENT_MODEL als Rueckfall: Ist das
    Free-Tier-Modell erschoepft (429) oder ueberlastet (503), antwortet der
    Agent trotzdem, statt seinen Task endlos auf "working" stehen zu lassen.
    """
    provider = OpenAIProvider(base_url=LITELLM_URL, api_key=LITELLM_KEY)
    names = [os.getenv(model_env, AGENT_MODEL)]
    if AGENT_MODEL not in names:
        names.append(AGENT_MODEL)
    return FallbackModel(*(OpenAIChatModel(n, provider=provider) for n in names))


def agent_url(port: int) -> str:
    """Oeffentliche Adresse, die in der Agent Card steht."""
    return f"http://{AGENT_HOST}:{port}"


def skill(skill_id: str, name: str, description: str, tags: list[str], examples: list[str]) -> Skill:
    return Skill(
        id=skill_id,
        name=name,
        description=description,
        tags=tags,
        examples=examples,
        input_modes=["text/plain"],
        output_modes=["text/plain"],
    )
