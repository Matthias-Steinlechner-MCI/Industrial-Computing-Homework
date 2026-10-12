# Ein Image fuer alle drei Fach-Agenten. Welcher Agent startet, bestimmt
# die Variable AGENT (inventory | quote | planning).
#
# UNGETESTET: Docker Desktop startet auf dem Entwicklungsrechner nicht
# (SVM im BIOS deaktiviert). Die Agenten wurden als lokale Prozesse auf
# eigenen Ports getestet, siehe README.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYDANTIC_AI_NO_BANNER=1

COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY agents/ agents/
COPY data/seed.py data/seed.py

ENV AGENT=inventory
CMD ["sh", "-c", "uv run --frozen agents/${AGENT}_agent.py"]
