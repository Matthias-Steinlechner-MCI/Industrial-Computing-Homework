# Startet den LiteLLM-Proxy fuer beide Hausuebungen.
# Liest Schluessel und Einstellungen aus litellm/.env (Vorlage: .env.example).
#
# Aufruf aus der Repo-Wurzel:   .\litellm\start_proxy.ps1
# Anderer Port:                 .\litellm\start_proxy.ps1 -Port 4001

param([int]$Port = 4000)

$envFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $envFile)) {
    Write-Error "litellm/.env fehlt. Vorlage kopieren: Copy-Item litellm/.env.example litellm/.env"
    exit 1
}

foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
    $name, $value = $line -split '=', 2
    $name = $name.Trim(); $value = $value.Trim()
    if ($value) { Set-Item -Path "env:$name" -Value $value }
}

if (-not $env:GEMINI_API_KEY) {
    Write-Error "GEMINI_API_KEY ist in litellm/.env nicht gesetzt."
    exit 1
}
if (-not $env:DATABASE_URL) {
    Write-Warning "DATABASE_URL fehlt - Part 1 laeuft, die Agent-Registry fuer Part 2 nicht."
}

# LiteLLMs Startbanner und die Beispiele geben Unicode aus
$env:PYTHONUTF8 = "1"

litellm --config (Join-Path $PSScriptRoot "config.yaml") --port $Port
