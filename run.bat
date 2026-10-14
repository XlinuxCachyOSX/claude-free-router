@echo off
REM Avvia claude-free-router su Windows (doppio click su questo file)
cd /d "%~dp0"

where powershell >nul 2>nul
if %errorlevel%==0 (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1"
) else (
    echo ========================================================
    echo   claude-free-router
    echo ========================================================
    if not exist providers.json copy providers.example.json providers.json
    echo [!] PowerShell non trovato: imposta manualmente le variabili d'ambiente
    echo     oppure usa run.ps1. Avvio il server...
    python server.py
)

pause
