# Avvia claude-free-router su Windows (PowerShell)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "========================================================"
Write-Host "  claude-free-router"
Write-Host "========================================================"

# Crea providers.json la prima volta
if (-not (Test-Path "providers.json")) {
    Copy-Item "providers.example.json" "providers.json"
    Write-Host "[+] Creato providers.json (personalizzalo se vuoi)"
}

# Carica .env (supporta sia 'export KEY="val"' che 'KEY=val')
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { return }
        if ($line.StartsWith("export ")) { $line = $line.Substring(7).Trim() }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { return }
        $key = $line.Substring(0, $idx).Trim()
        $val = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
        Set-Item -Path "Env:$key" -Value $val
    }
    Write-Host "[+] Variabili caricate da .env"
} else {
    Write-Host "[!] Nessun file .env trovato. Copia .env.example in .env e inserisci le API key."
}

# Verifica Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "[!] Python non trovato. Installa Python 3.8+ da https://python.org"
    Read-Host "Premi Invio per uscire"
    exit 1
}

python server.py
Read-Host "Premi Invio per uscire"
