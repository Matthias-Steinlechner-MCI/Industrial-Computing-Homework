# Startet die drei Fach-Agenten, jeden als eigenen Prozess in eigenem Fenster.
# Danach registriert es sie in der LiteLLM-Registry.
#
# Voraussetzung: LiteLLM-Proxy laeuft (..\litellm\start_proxy.ps1)
# Aufruf:        .\start_agents.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

$agents = @(
    @{ Name = "inventory-agent"; Script = "agents\inventory_agent.py"; Port = 8101 },
    @{ Name = "quote-agent";     Script = "agents\quote_agent.py";     Port = 8102 },
    @{ Name = "planning-agent";  Script = "agents\planning_agent.py";  Port = 8103 }
)

foreach ($a in $agents) {
    $cmd = "`$host.UI.RawUI.WindowTitle = '$($a.Name) :$($a.Port)'; " +
           "`$env:PYTHONUTF8 = '1'; `$env:PYDANTIC_AI_NO_BANNER = '1'; " +
           "Set-Location '$root'; uv run $($a.Script)"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd
    Write-Host "gestartet: $($a.Name) auf Port $($a.Port)"
}

# Warten, bis alle drei ihre Agent Card ausliefern
Write-Host "warte auf die Agenten ..."
foreach ($a in $agents) {
    $url = "http://localhost:$($a.Port)/.well-known/agent-card.json"
    for ($i = 0; $i -lt 60; $i++) {
        try { Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 2 | Out-Null; break }
        catch { Start-Sleep -Seconds 1 }
    }
}

Set-Location $root
$env:PYTHONUTF8 = "1"
uv run register_agents.py
