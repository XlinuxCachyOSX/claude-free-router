#!/usr/bin/env bash
# Avvia claude-free-router
set -e
cd "$(dirname "$0")"

# Carica le variabili d'ambiente se presente un file .env
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

# Crea providers.json dalla configurazione di esempio la prima volta
if [ ! -f providers.json ]; then
    cp providers.example.json providers.json
    echo "[+] Creato providers.json (personalizzalo se vuoi)"
fi

exec python3 server.py
