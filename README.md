# Industrial Computing – Hausübungen

MCI · DIBSE Master · Semester 3 · Industrial Computing (Ch. Maurer) ·
Matthias Steinlechner

Beide Teile im **Technical Track (Option A)**. Sie bauen aufeinander auf:
gleicher Produktbestand (Industriekomponenten), gleiche Rabattstaffel, ein
gemeinsamer LiteLLM-Proxy.

| Teil | Thema | Ordner |
|---|---|---|
| Part 1 | MCP-Server mit drei Tools (SQL-Lookup, Rabatt-Formel, Audit-Log) und ReAct-Client | [`Part1 - Option A`](Part1%20-%20Option%20A) |
| Part 2 | Drei A2A-Agenten auf eigenen Ports, Koordinator mit dynamischem Registry-Lookup | [`Part2 - Option A`](Part2%20-%20Option%20A) |

Jeder Ordner hat eine eigene README mit Setup, Startbefehlen und Szenarien
sowie ein Ein-Seiten-Abgabedokument — für Part 2 als
[`submission.pdf`](Part2%20-%20Option%20A/submission.pdf).

## Gemeinsame Voraussetzungen

- Python ≥ 3.11 und [uv](https://docs.astral.sh/uv/)
- Ein Gemini-API-Key aus [Google AI Studio](https://aistudio.google.com)
  (Free Tier genügt, 20 Anfragen pro Tag und Modell)
- Nur Part 2: PostgreSQL für die Agent-Registry

## LiteLLM-Proxy

Beide Teile sprechen ihr LLM über einen lokalen
[LiteLLM](https://docs.litellm.ai)-Proxy auf Port 4000 an. Die Clients nennen
nur den Alias `gpt-4o-mini` — welches Modell dahinter steckt, entscheidet
[`litellm/config.yaml`](litellm/config.yaml). In Part 2 dient derselbe Proxy
zusätzlich als Agent-Registry und A2A-Gateway.

**Einmalig installieren:**

```bash
uv tool install "litellm[proxy]" --with prisma
```

Für Part 2 braucht das A2A-Gateway zusätzlich das A2A-SDK, das
`litellm[proxy]` nicht mitbringt:

```bash
uv pip install --python "$env:APPDATA\uv\tools\litellm\Scripts\python.exe" "a2a-sdk>=1.1.0,<2.0"
```

**Konfigurieren:**

```bash
Copy-Item litellm/.env.example litellm/.env
```

Dann in `litellm/.env` mindestens `GEMINI_API_KEY` eintragen, für Part 2
auch `DATABASE_URL`. Die Datei wird nicht committet.

**Datenbank für Part 2 einmalig vorbereiten** (Datenbank `litellm` in
Postgres anlegen, dann im LiteLLM-Paket das Schema erzeugen):

```bash
$env:DATABASE_URL = "postgresql://postgres:<passwort>@localhost:5432/litellm"
Set-Location "$env:APPDATA\uv\tools\litellm\Lib\site-packages\litellm\proxy"
& "$env:APPDATA\uv\tools\litellm\Scripts\python.exe" -m prisma generate --schema=schema.prisma
& "$env:APPDATA\uv\tools\litellm\Scripts\python.exe" -m prisma db push --schema=schema.prisma --skip-generate
```

Unter Windows legt `prisma generate` beim ersten Lauf eine Verknüpfung an und
braucht dafür einmalig eine PowerShell mit Administratorrechten (oder den
aktivierten Entwicklermodus).

**Starten** (aus der Repo-Wurzel, eigenes Fenster):

```bash
.\litellm\start_proxy.ps1
```

### Bekannte Stolpersteine

- **`UnicodeEncodeError` beim Start** — Windows-Konsole in cp1252. Das
  Startskript setzt `PYTHONUTF8=1`; für die eigenen Skripte einmalig
  `[Environment]::SetEnvironmentVariable("PYTHONUTF8","1","User")`.
- **`CERTIFICATE_VERIFY_FAILED` zu Google** — ein Virenscanner (hier Avast)
  oder Firmenproxy bricht HTTPS mit eigenem Wurzelzertifikat auf. In
  `litellm/.env` per `SSL_CERT_FILE` ein PEM-Bundle angeben, das dieses
  Zertifikat enthält.
- **429 / „quota exceeded"** — Free-Tier-Kontingent des Modells für heute
  aufgebraucht. In `litellm/config.yaml` ein anderes Modell hinter den Alias
  hängen oder am nächsten Tag weitermachen.
