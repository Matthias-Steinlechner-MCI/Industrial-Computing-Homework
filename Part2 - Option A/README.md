# Part 2 – Option A: Distributed Remote A2A System with Dynamic Registry Lookup

Ein Kunde fragt nach Preis und Liefertermin für Industriekomponenten. Drei
Fach-Agenten — jeder als eigener Prozess auf eigenem Port, jeder mit eigenem
„Altsystem" — lösen die Anfrage gemeinsam. Ein Koordinator findet sie zur
Laufzeit in der LiteLLM-Agent-Registry und spricht sie per A2A an.

Die Domäne setzt Part 1 fort: gleicher Produktbestand, gleiche Rabattstaffel.

**Abgabedokument:** [`submission.pdf`](submission.pdf) (eine Seite) · Screenshots in voller
Auflösung unter [`screenshots/`](screenshots).

## Architektur

```
                    ┌───────────────────────────────────────────┐
  Kunde ──Anfrage──►│ coordinator.py   (Root-Agent)             │
                    │  1 discover  2 plan  3 delegate  4 check  │
                    └───────┬──────────────────────────▲────────┘
                            │ GET /v1/agents           │
                            │ POST /v1/a2a/{id}/...    │
                    ┌───────▼──────────────────────────┴────────┐
                    │ LiteLLM-Proxy :4000                        │
                    │  Agent-Registry (Postgres) · A2A-Gateway   │
                    │  LLM-Gateway (Gemini, Azure)               │
                    └──┬──────────────┬───────────────┬─────────┘
                  A2A  │         A2A  │          A2A  │
             ┌─────────▼───┐ ┌────────▼────┐ ┌────────▼──────┐
             │ inventory   │ │ quote       │ │ planning      │
             │ agent :8101 │ │ agent :8102 │ │ agent :8103   │
             │ WMS         │ │ Preisengine │ │ MES           │
             │ inventory.db│ │             │ │ production.db │
             └─────────────┘ └─────────────┘ └───────────────┘
```

Jeder Agent sieht nur sein eigenes System. Wer Daten aus einem anderen
braucht, muss den zuständigen Agenten fragen — die Zusammenarbeit ist damit
erzwungen, nicht nur behauptet.

| Agent | Port | System | Tools | Modell |
|---|---|---|---|---|
| `inventory-agent` | 8101 | Lagerverwaltung (`inventory.db`) | `search_products`, `check_availability` | `gemini-flash-lite-latest` |
| `quote-agent` | 8102 | Preisengine | `create_quote` | `gemini-3.5-flash-lite` |
| `planning-agent` | 8103 | Fertigungssteuerung (`production.db`) | `plan_delivery` | `gemini-3-flash-preview` |
| Koordinator | — | — | `delegate_task`, `submit_final_answer` | `gemini-3.5-flash` + Rückfallkette |

Die Agenten laufen bewusst auf **unterschiedlichen LLMs**. Für die
Zusammenarbeit spielt das keine Rolle — sie reden A2A, nicht „Gemini".
Nebenbei verteilt es das Free-Tier-Kontingent, das pro Modell gilt.

## Was „Dynamic Registry Lookup" hier heißt

1. **Selbstbeschreibung.** Jeder Agent liefert unter
   `/.well-known/agent-card.json` Name, Beschreibung und Skills.
2. **Registrierung.** `register_agents.py` liest die Karten und trägt sie in
   die LiteLLM-Registry ein. Ports und Namen stehen nur dort, nicht im
   Koordinator.
3. **Discovery.** Der Koordinator fragt bei jeder Anfrage
   `GET /v1/agents?health_check=true` ab — das liefert nur Agenten, die gerade
   erreichbar sind. Aus Beschreibung und Skills baut er seinen System-Prompt,
   und die erlaubten Agentennamen seines Werkzeugs `delegate_task` sind ein
   Enum, das bei jedem Lauf neu aus der Registry entsteht. Ein neuer Agent
   wird nach der Registrierung ohne Codeänderung genutzt.
4. **Aufruf.** Alle Aufträge gehen per A2A über das LiteLLM-Gateway
   (`/v1/a2a/{agent_id}/message/send`, Ergebnis per `tasks/get`). Damit
   erscheint jeder Agentenaufruf im LiteLLM-Log.

## Die Prüfschleife des Koordinators

In der Vorlesung blieb bei `main_dynamic_lookup.py` eine Lücke offen: Der
Router weist eine Anfrage genau **einmal** einem Agenten zu, ohne zu prüfen,
ob der Kunde danach alles hat. Der Koordinator hier arbeitet in Runden:

- Er zerlegt die Anfrage, delegiert, wertet die Antworten aus und delegiert
  weiter. Unabhängige Aufträge laufen **parallel** an verschiedene Agenten.
- Fehlt einem Agenten eine Angabe, meldet er das zurück, und der Koordinator
  holt sie beim zuständigen Agenten nach.
- Die Schleife endet erst mit `submit_final_answer`, und dabei muss er
  **jeden Teil der Anfrage abhaken** und angeben, welcher Agent ihn
  erfüllt hat. Das landet im Protokoll.

## Setup

Voraussetzungen: Python ≥ 3.11, [uv](https://docs.astral.sh/uv/) und der
LiteLLM-Proxy aus der Repo-Wurzel **mit Postgres** — Installation, A2A-SDK
und Datenbank stehen in der [Haupt-README](../README.md#litellm-proxy).

```bash
uv sync
cp .env.example .env
```

Zwei Einstellungen im Proxy sind für Part 2 nötig und in
[`litellm/config.yaml`](../litellm/config.yaml) bereits gesetzt:

- **Agent-Ports freigeben.** LiteLLM blockiert Agent-Adressen auf
  `localhost` als SSRF-Schutz, ausser sie stehen in
  `general_settings.user_url_allowed_hosts` (hier `localhost:8101` bis `:8103`).
- **A2A-SDK.** Das Gateway leitet A2A-Nachrichten mit `a2a-sdk` weiter, das
  `litellm[proxy]` nicht mitinstalliert. Ohne das Paket scheitert jeder
  Aufruf mit `NoneType takes no arguments`.

## Starten

**1. Proxy** (aus der Repo-Wurzel, eigenes Fenster):

```bash
.\litellm\start_proxy.ps1
```

**2. Agenten** — startet alle drei in eigenen Fenstern und registriert sie:

```bash
.\start_agents.ps1
```

Oder einzeln:

```bash
uv run agents/inventory_agent.py
uv run agents/quote_agent.py
uv run agents/planning_agent.py
uv run register_agents.py
```

**3. Koordinator:**

```bash
uv run coordinator.py
```

Einzelnes Szenario: `uv run coordinator.py 2` · Freie Anfrage:
`uv run coordinator.py "Was kosten 20 Lichtschranken?"`

Registry anzeigen: `uv run register_agents.py --list`

### Docker (ungetestet)

`docker/Dockerfile` und `docker/docker-compose.yml` starten die drei Agenten
als getrennte Container. Sie sind **nicht getestet**: Docker Desktop startet
auf dem Entwicklungsrechner nicht (Virtualisierung im BIOS deaktiviert). Die
Abgabe erfüllt die Aufgabe über getrennte Prozesse auf eigenen Ports.

```bash
docker compose -f docker/docker-compose.yml up --build
```

## Szenarien

| # | Anfrage | Was es zeigt |
|---|---|---|
| 1 | Gold-Kunde, 200 Servomotoren 400W | Teillieferung (143 lagernd, 57 Fertigung), Angebot über 10.000 € → freigabepflichtig |
| 2 | Silver-Kunde, 50 Druckaufnehmer | Bestand 0, alles aus Fertigung, Lager Wien |
| 3 | Standard-Kunde, 3 SPS + 40 IO-Module | Mehrere Positionen, ein Angebot, Summe über 10.000 € |

## Protokolle

| Wo | Inhalt |
|---|---|
| `logs/coordinator_run.txt` | Vollständiger Lauf: Discovery, Runden, Delegationen, Antworten, Checkliste |
| Agentenfenster | Eingehende A2A-Requests je Agent (`message/send`, `tasks/get`) live |
| LiteLLM Admin-UI → Logs | Jeder Agentenaufruf über das Gateway und jeder LLM-Aufruf mit Modell |

## Probleme, die unterwegs gelöst wurden

**Asynchrone Tasks.** `message/send` liefert bei fasta2a keinen Text, sondern
einen Task im Zustand `submitted`. Das Ergebnis muss per `tasks/get`
abgeholt werden. Sample 6 der Vorlesung fiel hier auf `str(result)` zurück.
Zusätzlich meldet das Gateway Zustände in A2A-1.0-Schreibweise
(`TASK_STATE_COMPLETED`), die Abfrage danach in 0.3-Schreibweise
(`completed`) — der Koordinator normalisiert beides.

**Zeitstempel.** fasta2a 2.1 schreibt Task-Zeitstempel ohne Zeitzone. Das
A2A-SDK in LiteLLM parst sie per Protobuf und hält das `-` im Datum für eine
Zeitzonenangabe — die Antwort wird verworfen. `agents/common.py` ersetzt den
Zeitgeber durch einen RFC-3339-konformen mit `Z`.

**Agent-Card-Format.** A2A 1.0 führt die Adresse unter
`supportedInterfaces[].url`, LiteLLM erwartet sie als `url` auf oberster
Ebene. `register_agents.py` übersetzt.

**Registry nach Neustart.** LiteLLM speichert die Agenten in Postgres, lädt
sie nach einem Proxy-Neustart aber nicht zuverlässig in den Speicher. Ein
erneutes Anlegen scheitert am Unique-Constraint auf dem Namen.
`register_agents.py` merkt sich deshalb die IDs (`.registry_ids.json`) und
aktualisiert per `PUT`, was den Agenten zurück in die Registry holt.

**Geratene Werte.** Ein früher Lauf lieferte für 50 Druckaufnehmer den
20.10. statt des 21.10.: Der Koordinator hatte keinen Lagerort mitgegeben,
und der Planungs-Agent setzte Innsbruck statt Wien ein. Der Planungs-Agent
fragt jetzt nach, statt zu raten, und der Koordinator gibt Kategorie und
Lagerort immer mit.

**Aufgeteilte Angebote.** Für 3 SPS und 40 IO-Module erstellte der
Angebots-Agent anfangs zwei Einzelangebote — beide unter der
Freigabegrenze von 10.000 €, zusammen darüber. `create_quote` nimmt jetzt
alle Positionen auf einmal, und die Grenze gilt für die Gesamtsumme.

**Kontingente.** Das Gemini-Free-Tier erlaubt 20 Anfragen pro Tag und
Modell und ist zeitweise überlastet (503). Der Koordinator weicht über eine
Modellkette aus, die Fach-Agenten über ein `FallbackModel`. Sind alle
Modelle erschöpft, bricht der Koordinator die Anfrage sauber ab, statt eine
Antwort zu erfinden.

**Avast und HTTPS.** Avast bricht HTTPS mit einem eigenen Wurzelzertifikat
auf, dem Python nicht vertraut. Der Proxy bekommt per `SSL_CERT_FILE` ein
Bundle aus certifi und dem Windows-Wurzelspeicher.

## Einsatz von KI

Claude (Anthropic) habe ich als Hilfsmittel verwendet. Konzeption, Umsetzung
und Programmierung habe ich selbst durchgeführt. Claude diente lediglich zum
Erstellen der Abgabedokumente sowie als Hilfestellung bei einzelnen Schritten,
bei denen ich nicht mehr weitergewusst habe.
