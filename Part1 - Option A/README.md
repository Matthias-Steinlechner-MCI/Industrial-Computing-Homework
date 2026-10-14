# Part 1 – Option A: Building a Custom MCP Math & Database Tool Server

MCP-Server mit drei Tools plus ReAct-Agent, der die Tools über einen
LiteLLM-Proxy ansteuert. Aufgebaut auf dem Basiscode der Vorlesung
(Sample 3 und Sample 4).

**Abgabedokument:** [`submission.pdf`](submission.pdf) (eine Seite) · Screenshots in voller
Auflösung unter [`screenshots/`](screenshots).

## Architektur

```
  client.py  ──HTTP──►  LiteLLM-Proxy  ──►  LLM hinter dem Alias gpt-4o-mini
     │                  localhost:4000       (Konfiguration, siehe unten)
     │
     └──MCP streamable-http──►  server.py  (localhost:8010/mcp)
                                   ├── lookup_products         → SQLite
                                   ├── compute_tiered_discount → Formel-Engine
                                   ├── append_audit_event      → logs/audit_log.txt
                                   ├── resource audit://log
                                   └── resource inventory://schema
```

Der Client kennt die Tools nicht im Voraus: Er ruft `list_tools()` auf dem
MCP-Server auf, übersetzt die Schemas ins OpenAI-Function-Format und übergibt
sie dem Modell. Neue Tools im Server erscheinen ohne Codeänderung im Client.

## Die drei Tools

### 1. `lookup_products` — SQL-Lookup (sqlite3)

Fragt die Tabelle `products` in `inventory.db` ab. Filter: Freitext auf SKU und
Name, Kategorie, optional nur lieferbare Artikel, Limit.

Die Query ist **parametrisiert** (`:search`, `:category`, …). Da die Argumente
vom LLM kommen, wäre String-Verkettung hier eine SQL-Injection-Lücke.

### 2. `compute_tiered_discount` — Formel-Engine

Mengenstaffel, **marginal** angewendet — jede Stufe gilt nur für die Stück in
ihrer Bandbreite, nicht rückwirkend für die gesamte Bestellung:

| Menge | Rabatt |
|---|---|
| 1 – 9 | 0 % |
| 10 – 49 | 5 % |
| 50 – 199 | 12 % |
| 200 – 499 | 18 % |
| 500 + | 25 % |

Darauf wirkt multiplikativ ein Kundenstatus-Nachlass: `standard` 0 %,
`silver` 2 %, `gold` 4 %, `platinum` 7 %.

Beispiel — 250 Stück à 34,20 €, Gold-Kunde:

```
  1–9      9 Stk    0 %  →   307,80
  10–49   40 Stk    5 %  →  1.299,60
  50–199 150 Stk   12 %  →  4.514,40
  200–499 51 Stk   18 %  →  1.430,24
                    Summe   7.552,04
  Gold-Nachlass 4 %      →   −302,08
                  Endpreis  7.249,96   (Liste 8.550,00, effektiv −15,21 %)
```

Die Funktion liefert die vollständige Aufschlüsselung zurück, nicht nur den
Endbetrag — das Modell kann die Rechnung dadurch nachvollziehbar wiedergeben,
statt sie selbst zu schätzen.

### 3. `append_audit_event` — Log-Audit

Hängt `ISO-Zeitstempel | event | details` an `logs/audit_log.txt` an und gibt
die geschriebene Zeile samt Zeilennummer zurück. Derselbe Inhalt ist über die
MCP-Resource `audit://log` lesbar — Tool schreibt, Resource liest.

## Setup

Voraussetzung: Python ≥ 3.11, [uv](https://docs.astral.sh/uv/) und der
LiteLLM-Proxy aus der Repo-Wurzel — Installation und Konfiguration stehen in
der [Haupt-README](../README.md#litellm-proxy). Für Part 1 genügt ein
Gemini-Key, Postgres wird nicht gebraucht.

```bash
uv sync
```

Environment-Datei anlegen:

```bash
cp .env.example .env
```

Inhalt von `.env`:

```
LITELLM_API=http://localhost:4000
LITELLM_KEY=sk-1234
LITELLM_DEFAULT_MODEL=gpt-4o-mini
MCP_SERVER_URL=http://localhost:8010/mcp
```

Unter Windows zusätzlich einmalig setzen — die Skripte geben Emoji und Umlaute
aus, was an einer cp1252-Konsole sonst mit `UnicodeEncodeError` abbricht:

```bash
[Environment]::SetEnvironmentVariable("PYTHONUTF8","1","User")
```

## Ausführen

Drei Terminals bzw. drei Schritte:

**1. LiteLLM-Proxy** (aus der Repo-Wurzel, eigenes Fenster)

```bash
.\litellm\start_proxy.ps1
```

**2. MCP-Server**

```bash
uv run server.py
```

Legt beim Start `inventory.db` mit zehn Produkten an und lauscht auf
`http://0.0.0.0:8010/mcp`.

**3. Agent**

```bash
uv run client.py
```

Einzelnes Szenario: `uv run client.py 2`

Die Datenbank kann auch separat neu aufgebaut werden:

```bash
uv run seed_db.py
```

## Szenarien

Der Client fährt drei Anfragen, die unterschiedliche Tool-Kombinationen
erzwingen:

1. **Nur Datenbank** — „Wie viele Servomotoren haben wir auf Lager und was
   kosten sie?"
2. **Alle drei Tools verkettet** — „Gold-Kunde will 250 Stück des
   Näherungssensors M12" → Lookup, dann Rabattberechnung, dann Audit-Eintrag
3. **Datenbank plus Audit** — „Welche Produkte sind nicht lieferbar?" mit
   Protokollierung als `stockout_review`

## Logs

| Datei | Inhalt |
|---|---|
| `logs/execution_log.txt` | Vollständiger Agent-Lauf: Reasoning, Action, Observation, Endantwort |
| `logs/audit_log.txt` | Die vom Audit-Tool geschriebenen Ereignisse |
| `logs/server.log` | stdout des MCP-Servers |

## Anmerkungen zur Umsetzung

**Modellname als Alias.** `LITELLM_DEFAULT_MODEL=gpt-4o-mini` ist nur ein
Alias im Proxy. Die Protokolle in `logs/` entstanden am 31.08.2026 mit einem
Azure-Deployment `gpt-5-mini` hinter diesem Alias. Nach dem Ablauf des
Azure-Testabos zeigt der Alias in [`litellm/config.yaml`](../litellm/config.yaml)
auf Gemini — der Client blieb dabei unverändert. Ein Kontrolllauf von
Szenario 2 am 14.10.2026 mit `gemini-3-flash-preview` lieferte dieselben drei
Tool-Aufrufe und denselben Endpreis von 7.249,96 €. Der Wechsel des Anbieters
ist eine Konfigurationsänderung im Proxy, keine Codeänderung: genau der Zweck
eines LLM-Gateways.

**`temperature` und Reasoning-Modelle.** Reasoning-Modelle wie `gpt-5-mini`
akzeptieren nur den Default-Wert für `temperature`. Der Proxy ist deshalb mit
`litellm_settings.drop_params: true` konfiguriert, was nicht unterstützte
Parameter still entfernt, statt den Request mit HTTP 400 abzuweisen.

**Versionsunterschiede im `mcp`-Paket.** `streamable_http_client` liefert je
nach Version zwei oder drei Werte, und das Feld des Tool-Schemas heißt
`inputSchema` oder `input_schema`. Der Client behandelt beide Varianten, damit
er sowohl mit der Version aus Sample 4 als auch mit der aktuellen läuft.

## Einsatz von KI

Claude (Anthropic) habe ich als Hilfsmittel verwendet. Konzeption, Umsetzung
und Programmierung habe ich selbst durchgeführt. Claude diente lediglich zum
Erstellen der Abgabedokumente sowie als Hilfestellung bei einzelnen Schritten,
bei denen ich nicht mehr weitergewusst habe.
