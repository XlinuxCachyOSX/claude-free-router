#!/usr/bin/env python3
"""
claude-free-router
------------------
Un proxy che espone l'API di Anthropic (/v1/messages) e la traduce in chiamate
OpenAI-compatibili verso provider con tier gratuito (Groq, Gemini, OpenRouter,
Cerebras, Mistral, Together, GitHub Models...).

Così Claude Code e l'SDK Anthropic "parlano" con provider gratuiti,
con fallback automatico quando uno esaurisce le quote.

Zero dipendenze esterne (solo stdlib). Python 3.8+.
"""

import fnmatch
import json
import os
import sys
import time
import uuid
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("CFR_CONFIG", BASE_DIR / "providers.json"))
DEFAULT_TIMEOUT = int(os.environ.get("CFR_TIMEOUT", "120"))
# Se valorizzato, l'accesso richiede questo token (header x-api-key o Authorization).
# Il client deve usarlo come ANTHROPIC_API_KEY. Se vuoto, l'accesso e' libero.
AUTH_TOKEN = os.environ.get("CFR_AUTH_TOKEN", "")
# Keep-alive: su Render la variabile RENDER_EXTERNAL_URL e' impostata in automatico.
# Se presente (o se imposti CFR_SELF_URL), il server pinga il proprio URL pubblico
# ogni CFR_KEEPALIVE secondi per evitare lo spegnimento per inattivita' (piano free).
KEEPALIVE_URL = (os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("CFR_SELF_URL") or "").rstrip("/")
KEEPALIVE_INTERVAL = int(os.environ.get("CFR_KEEPALIVE", "600"))


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_config():
    path = CONFIG_PATH
    if not path.exists():
        example = BASE_DIR / "providers.example.json"
        print(f"[i] Config non trovata ({path}); uso {example.name}")
        path = example
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["host"] = os.environ.get("CFR_HOST") or cfg.get("host", "0.0.0.0")
    cfg["port"] = int(os.environ.get("CFR_PORT") or os.environ.get("PORT") or cfg.get("port", 8787))
    providers = []
    for p in cfg.get("providers", []):
        key = p.get("api_key")
        if not key and p.get("api_key_env"):
            key = os.environ.get(p["api_key_env"], "")
        if not key:
            continue  # salta provider senza chiave
        p["_key"] = key
        providers.append(p)
    providers.sort(key=lambda x: x.get("priority", 100))
    cfg["_providers"] = providers
    return cfg


def resolve_model(provider, requested_model):
    """Mappa il nome modello 'claude-...' inviato dal client su un modello del provider."""
    for pattern, target in provider.get("models", {}).items():
        if fnmatch.fnmatch(requested_model or "", pattern):
            return target
    return provider.get("default_model")


# --------------------------------------------------------------------------- #
# Traduzione Anthropic -> OpenAI
# --------------------------------------------------------------------------- #
def _content_to_openai(content):
    """Converte il content di un messaggio Anthropic in formato OpenAI."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        btype = block.get("type")
        if btype == "text":
            parts.append({"type": "text", "text": block.get("text", "")})
        elif btype == "image":
            src = block.get("source", {})
            if src.get("type") == "base64":
                url = f"data:{src.get('media_type','image/png')};base64,{src.get('data','')}"
            else:
                url = src.get("url", "")
            parts.append({"type": "image_url", "image_url": {"url": url}})
    if len(parts) == 1 and parts[0]["type"] == "text":
        return parts[0]["text"]
    return parts if parts else ""


def _tools_to_openai(tools):
    out = []
    for t in tools or []:
        out.append({
            "type": "function",
            "function": {
                "name": t.get("name"),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return out


def _tool_choice_to_openai(tc):
    if not tc:
        return None
    t = tc.get("type")
    if t == "auto":
        return "auto"
    if t == "any":
        return "required"
    if t == "tool":
        return {"type": "function", "function": {"name": tc.get("name")}}
    if t == "none":
        return "none"
    return None


def anthropic_to_openai(body, provider):
    model = resolve_model(provider, body.get("model"))
    messages = []

    system = body.get("system")
    if system:
        if isinstance(system, list):
            system = "\n".join(b.get("text", "") for b in system if b.get("type") == "text")
        messages.append({"role": "system", "content": system})

    for msg in body.get("messages", []):
        role = msg.get("role")
        content = msg.get("content")

        if isinstance(content, list):
            tool_calls = []
            tool_results = []
            text_parts = []
            for block in content:
                btype = block.get("type")
                if btype == "tool_use":
                    tc = {
                        "id": block.get("id"),
                        "type": "function",
                        "function": {
                            "name": block.get("name"),
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    }
                    sig = THOUGHT_SIG.get(block.get("id"))
                    if sig:
                        tc["extra_content"] = {"google": {"thought_signature": sig}}
                    tool_calls.append(tc)
                elif btype == "tool_result":
                    c = block.get("content")
                    if isinstance(c, list):
                        c = "\n".join(x.get("text", "") for x in c if x.get("type") == "text")
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id"),
                        "content": c if isinstance(c, str) else json.dumps(c),
                    })
                else:
                    text_parts.append(block)

            if role == "assistant" and tool_calls:
                conv = _content_to_openai(text_parts)
                messages.append({"role": "assistant", "content": conv or None, "tool_calls": tool_calls})
            else:
                for tr in tool_results:
                    messages.append(tr)
                if text_parts:
                    messages.append({"role": role, "content": _content_to_openai(text_parts)})
        else:
            messages.append({"role": role, "content": content})

    max_tokens = body.get("max_tokens", 4096)
    cap = provider.get("max_tokens_cap")
    if cap:
        max_tokens = min(max_tokens, cap)
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if "temperature" in body:
        payload["temperature"] = body["temperature"]
    if "top_p" in body:
        payload["top_p"] = body["top_p"]
    if body.get("stop_sequences"):
        payload["stop"] = body["stop_sequences"]
    if body.get("tools"):
        payload["tools"] = _tools_to_openai(body["tools"])
        tc = _tool_choice_to_openai(body.get("tool_choice"))
        if tc:
            payload["tool_choice"] = tc
    payload["stream"] = bool(body.get("stream"))
    return payload, model


# --------------------------------------------------------------------------- #
# Traduzione OpenAI -> Anthropic
# --------------------------------------------------------------------------- #
STOP_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "end_turn",
}


# Gemini 3.x richiede che ogni function call riporti il suo thought_signature.
# Lo conserviamo in memoria (id tool -> signature) per rimandarlo al turno successivo.
THOUGHT_SIG = {}
MAX_SIG_CACHE = 2048


def _extract_thought_sig(tc):
    return ((tc.get("extra_content") or {}).get("google") or {}).get("thought_signature")


def _cache_sig(tid, sig):
    if tid and sig:
        THOUGHT_SIG[tid] = sig
        while len(THOUGHT_SIG) > MAX_SIG_CACHE:
            THOUGHT_SIG.pop(next(iter(THOUGHT_SIG)))


def openai_to_anthropic(resp, requested_model):
    choice = (resp.get("choices") or [{}])[0]
    message = choice.get("message", {})
    content = []

    text = message.get("content")
    if text:
        content.append({"type": "text", "text": text})

    for tc in message.get("tool_calls") or []:
        fn = tc.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        tid = tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}"
        _cache_sig(tid, _extract_thought_sig(tc))
        content.append({
            "type": "tool_use",
            "id": tid,
            "name": fn.get("name"),
            "input": args,
        })

    finish = choice.get("finish_reason")
    stop_reason = STOP_MAP.get(finish, "end_turn")
    usage = resp.get("usage", {}) or {}

    return {
        "id": "msg_" + uuid.uuid4().hex[:24],
        "type": "message",
        "role": "assistant",
        "model": requested_model,
        "content": content or [{"type": "text", "text": ""}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# --------------------------------------------------------------------------- #
# Chiamata upstream
# --------------------------------------------------------------------------- #
class UpstreamError(Exception):
    def __init__(self, status, text):
        self.status = status
        self.text = text
        super().__init__(f"upstream {status}: {text[:200]}")


def call_provider(provider, payload):
    url = provider["base_url"].rstrip("/") + "/chat/completions"
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {provider['_key']}",
        "User-Agent": "claude-free-router/1.0",
    }
    for k, v in provider.get("extra_headers", {}).items():
        headers[k] = v
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        return urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise UpstreamError(e.code, body)


def iter_providers_attempt(providers, payload, requested_model, body):
    """Generatore: prova i provider in ordine, restituisce (provider, response)."""
    last_err = None
    for provider in providers:
        p_payload, _ = anthropic_to_openai(body, provider)
        p_payload["stream"] = payload["stream"]
        try:
            resp = call_provider(provider, p_payload)
            yield provider, resp
            return
        except UpstreamError as e:
            last_err = e
            # 429 (rate limit) o 5xx -> prova il prossimo provider
            if e.status in (401, 403, 404, 413, 429, 500, 502, 503, 504):
                print(f"[~] {provider['name']} ha fallito ({e.status}), provo il prossimo...")
                continue
            raise
    raise last_err or UpstreamError(0, "nessun provider disponibile")


# --------------------------------------------------------------------------- #
# SSE helpers (Anthropic streaming)
# --------------------------------------------------------------------------- #
def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


def stream_openai_to_anthropic(upstream, requested_model, write):
    """Legge lo stream OpenAI e lo riscrive come eventi Anthropic."""
    msg_id = "msg_" + uuid.uuid4().hex[:24]
    write(sse("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id, "type": "message", "role": "assistant",
            "model": requested_model, "content": [],
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    }))

    text_index = None
    tool_state = {}   # openai index -> {"block": n, "id":..., "name":..., "args":""}
    next_block = 0
    output_tokens = 0
    finish_reason = None

    def ensure_text_block():
        nonlocal text_index, next_block
        if text_index is None:
            text_index = next_block
            next_block += 1
            write(sse("content_block_start", {
                "type": "content_block_start", "index": text_index,
                "content_block": {"type": "text", "text": ""},
            }))
        return text_index

    for raw in upstream:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue

        usage = chunk.get("usage")
        if usage:
            output_tokens = usage.get("completion_tokens", output_tokens)

        choice = (chunk.get("choices") or [{}])[0]
        delta = choice.get("delta", {}) or {}
        if choice.get("finish_reason"):
            finish_reason = choice["finish_reason"]

        piece = delta.get("content")
        if piece:
            idx = ensure_text_block()
            write(sse("content_block_delta", {
                "type": "content_block_delta", "index": idx,
                "delta": {"type": "text_delta", "text": piece},
            }))

        for tc in delta.get("tool_calls") or []:
            oi = tc.get("index", 0)
            if oi not in tool_state:
                st = {"block": next_block, "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
                      "name": (tc.get("function") or {}).get("name", ""), "args": ""}
                tool_state[oi] = st
                next_block += 1
                write(sse("content_block_start", {
                    "type": "content_block_start", "index": st["block"],
                    "content_block": {"type": "tool_use", "id": st["id"], "name": st["name"], "input": {}},
                }))
            _cache_sig(tool_state[oi]["id"], _extract_thought_sig(tc))
            fn = tc.get("function") or {}
            if fn.get("name") and not tool_state[oi]["name"]:
                tool_state[oi]["name"] = fn["name"]
            if fn.get("arguments"):
                tool_state[oi]["args"] += fn["arguments"]
                write(sse("content_block_delta", {
                    "type": "content_block_delta", "index": tool_state[oi]["block"],
                    "delta": {"type": "input_json_delta", "partial_json": fn["arguments"]},
                }))

    if text_index is not None:
        write(sse("content_block_stop", {"type": "content_block_stop", "index": text_index}))
    for st in tool_state.values():
        write(sse("content_block_stop", {"type": "content_block_stop", "index": st["block"]}))

    stop_reason = STOP_MAP.get(finish_reason, "end_turn")
    write(sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    }))
    write(sse("message_stop", {"type": "message_stop"}))


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    config = None

    def log_message(self, fmt, *args):
        print(f"[http] {self.address_string()} {fmt % args}")

    def _json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _start_chunked(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _chunk(self, data: bytes):
        self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def _end_chunks(self):
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _authorized(self):
        if not AUTH_TOKEN:
            return True
        supplied = self.headers.get("x-api-key") or ""
        if not supplied:
            auth = self.headers.get("authorization", "")
            supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else auth.strip()
        return supplied == AUTH_TOKEN

    def do_GET(self):
        if self.path in ("/", "/health"):
            return self._json(200, {"status": "ok", "service": "claude-free-router",
                                    "providers": [p["name"] for p in self.config["_providers"]]})
        if self.path == "/v1/models":
            if not self._authorized():
                return self._json(401, {"type": "error", "error": {"type": "authentication_error", "message": "token non valido"}})
            data = [{"id": m, "object": "model", "type": "model"}
                    for m in ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest", "claude-3-opus-latest"]]
            return self._json(200, {"object": "list", "data": data})
        return self._json(404, {"type": "error", "error": {"type": "not_found_error", "message": "not found"}})

    def do_POST(self):
        # Leggi SEMPRE il body prima di rispondere: su connessioni keep-alive un
        # corpo non letto resta nel buffer e corrompe la richiesta successiva.
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""

        if self.path not in ("/v1/messages", "/v1/messages?beta=true"):
            return self._json(404, {"type": "error", "error": {"type": "not_found_error", "message": "not found"}})
        if not self._authorized():
            return self._json(401, {"type": "error", "error": {"type": "authentication_error", "message": "token non valido"}})

        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "bad json"}})

        requested_model = body.get("model", "claude-3-5-sonnet-latest")
        is_stream = bool(body.get("stream"))
        providers = self.config["_providers"]

        # Prova il primo provider che risponde, con fallback
        dummy = {"stream": is_stream}
        try:
            gen = iter_providers_attempt(providers, dummy, requested_model, body)
            provider, upstream = next(gen)
        except (UpstreamError, StopIteration) as e:
            err = e if isinstance(e, UpstreamError) else UpstreamError(0, "nessun provider")
            return self._json(err.status or 500, {
                "type": "error",
                "error": {"type": "api_error", "message": f"Tutti i provider hanno fallito: {err.text[:300]}"},
            })

        if is_stream:
            self._start_chunked()
            try:
                stream_openai_to_anthropic(upstream, requested_model, self._chunk)
            except Exception as ex:
                print(f"[!] errore stream: {ex}")
            finally:
                self._end_chunks()
        else:
            try:
                data = json.loads(upstream.read().decode("utf-8"))
            except Exception as ex:
                return self._json(502, {"type": "error", "error": {"type": "api_error", "message": str(ex)}})
            return self._json(200, openai_to_anthropic(data, requested_model))


def _keepalive_loop():
    url = KEEPALIVE_URL + "/health"
    while True:
        time.sleep(KEEPALIVE_INTERVAL)
        try:
            urllib.request.urlopen(url, timeout=30).read()
        except Exception:
            pass


def main():
    config = load_config()
    if not config["_providers"]:
        print("[!] Nessun provider configurato con API key. Controlla providers.json / variabili d'ambiente.")
        sys.exit(1)
    Handler.config = config
    host, port = config["host"], config["port"]
    print("=" * 56)
    print("  claude-free-router")
    print("=" * 56)
    print(f"  In ascolto su http://{host}:{port}")
    print(f"  Provider attivi (in ordine di priorità):")
    for p in config["_providers"]:
        print(f"    - {p['name']:<12} -> {p['base_url']}")
    print("-" * 56)
    print("  Claude Code:")
    print(f"    export ANTHROPIC_BASE_URL=http://localhost:{port}")
    print("    export ANTHROPIC_API_KEY=free")
    print("    claude")
    print("=" * 56)
    server = ThreadingHTTPServer((host, port), Handler)
    if KEEPALIVE_URL:
        import threading
        threading.Thread(target=_keepalive_loop, daemon=True).start()
        print(f"  Keep-alive attivo: ping {KEEPALIVE_URL}/health ogni {KEEPALIVE_INTERVAL}s")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[+] Arresto.")


if __name__ == "__main__":
    main()
