# claude-free-router

Usa **Claude Code** (e qualsiasi client che parla l'API di Anthropic) con **provider AI gratuiti**: Groq, Google Gemini, Cerebras, OpenRouter, Mistral, GitHub Models, Together.

Il router espone gli endpoint dell'API Anthropic (`/v1/messages`) e li traduce in chiamate OpenAI-compatibili verso i provider gratuiti, con **fallback automatico**: se un provider esaurisce le quote (429) o va giù, passa al successivo.

```
Claude Code  ──►  claude-free-router  ──►  Groq / Gemini / Cerebras / OpenRouter / ...
 (API Anthropic)     (questo proxy)          (tier gratuiti, con fallback)
```

## Caratteristiche

- ✅ API **Anthropic-compatibile** (`/v1/messages`, streaming SSE, tool calling)
- ✅ **Fallback automatico** tra provider (rate limit / errori / quota esaurita)
- ✅ **Zero dipendenze** (solo Python 3.8+ stdlib)
- ✅ Mappatura modelli: i nomi `claude-*` vengono tradotti nei modelli gratuiti
- ✅ Supporto **tool use** (necessario per Claude Code)
- ✅ Streaming in tempo reale

## Installazione

```bash
git clone https://github.com/XlinuxCachyOSX/claude-free-router.git
cd claude-free-router
cp providers.example.json providers.json
cp .env.example .env
```

Modifica `.env` e inserisci almeno una API key (di qualsiasi provider).

## Provider gratuiti (prendi le chiavi qui)

| Provider | Link chiave | Modelli gratuiti |
|----------|-------------|------------------|
| **Groq** | https://console.groq.com/keys | llama-3.3-70b, llama-3.1-8b |
| **Google Gemini** | https://aistudio.google.com/app/apikey | gemini-2.0-flash |
| **Cerebras** | https://cloud.cerebras.ai/ | llama3.3-70b |
| **OpenRouter** | https://openrouter.ai/keys | modelli `:free` |
| **Mistral** | https://console.mistral.ai/api-keys/ | mistral-small |
| **GitHub Models** | https://github.com/settings/tokens | gpt-4o, gpt-4o-mini |
| **Together AI** | https://api.together.xyz/settings/api-keys | llama-3.3-70b |

Ti basta **una** chiave per iniziare. Più ne aggiungi, più il fallback è robusto.

## Avvio

### Linux / macOS
```bash
./run.sh
# oppure
python3 server.py
```

### Windows
Doppio click su **`run.bat`** (oppure in PowerShell: `./run.ps1`).
Lo script crea `providers.json`, carica il `.env` e avvia il server.

Vedrai:
```
  In ascolto su http://0.0.0.0:8787
  Provider attivi (in ordine di priorità):
    - groq         -> https://api.groq.com/openai/v1
    - gemini       -> https://generativelanguage.googleapis.com/v1beta/openai
```

## Usarlo con Claude Code

```bash
export ANTHROPIC_BASE_URL=http://localhost:8787
export ANTHROPIC_API_KEY=free
claude
```

Per renderlo permanente, aggiungi le due righe a `~/.bashrc` o `~/.config/fish/config.fish`.

**Windows (PowerShell):**
```powershell
$env:ANTHROPIC_BASE_URL = "http://localhost:8787"
$env:ANTHROPIC_API_KEY = "free"
claude
```
Per renderlo permanente: `setx ANTHROPIC_BASE_URL "http://localhost:8787"` e riapri il terminale.

## Usarlo con l'SDK Anthropic (Python)

```python
import anthropic

client = anthropic.Anthropic(base_url="http://localhost:8787", api_key="free")
msg = client.messages.create(
    model="claude-3-5-sonnet-latest",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Ciao!"}],
)
print(msg.content[0].text)
```

## Test rapido

```bash
curl http://localhost:8787/health

curl http://localhost:8787/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-5-sonnet-latest",
    "max_tokens": 256,
    "messages": [{"role": "user", "content": "Scrivi una filastrocca breve"}]
  }'
```

## Configurazione

Modifica `providers.json`:

- **`priority`**: ordine di tentativo (più basso = prima)
- **`base_url`**: endpoint OpenAI-compatibile del provider
- **`api_key_env`**: nome della variabile d'ambiente con la chiave
- **`default_model`**: modello usato se nessun pattern corrisponde
- **`models`**: mappa `pattern` → `modello` (supporta `*`, es. `claude-3-5-sonnet*`)
- **`extra_headers`**: header aggiuntivi (es. OpenRouter)

### Aggiungere un provider

```json
{
  "name": "mio-provider",
  "priority": 8,
  "base_url": "https://api.esempio.com/v1",
  "api_key_env": "ESEMPIO_API_KEY",
  "default_model": "modello-gratuito",
  "models": { "*": "modello-gratuito" }
}
```

## Come funziona il fallback

Per ogni richiesta il router prova i provider in ordine di `priority`. Se un provider risponde con **401/403/404/429/5xx**, passa al prossimo automaticamente. Così quando Groq esaurisce la quota oraria, subentra Gemini, poi Cerebras, ecc.

## Limiti

- La traduzione dei tool è best-effort: modelli molto più piccoli possono sbagliare i formati.
- Alcuni provider non supportano immagini: verranno ignorate o daranno errore.
- Le quote gratuite hanno limiti di richieste/minuto.

## Struttura

```
claude-free-router/
├── server.py                # Proxy Anthropic -> OpenAI-compatibile
├── providers.example.json   # Config provider (copia in providers.json)
├── .env.example             # Template API key (copia in .env)
├── run.sh                   # Avvio Linux/macOS
├── run.bat                  # Avvio Windows (CMD)
├── run.ps1                  # Avvio Windows (PowerShell)
├── README.md
└── LICENSE
```

## Licenza

MIT
