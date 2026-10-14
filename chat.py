import json
import os
import urllib.request

URL = os.environ.get("CFR_URL", "http://127.0.0.1:8787/v1/messages")
MODEL = os.environ.get("CFR_MODEL", "claude-3-5-sonnet-latest")
SYSTEM = os.environ.get(
    "CFR_SYSTEM",
    "Sei Claude, un assistente AI creato da Anthropic. "
    "Rispondi in modo chiaro, utile e conciso, in italiano.",
)


def ask(messages):
    data = json.dumps(
        {"model": MODEL, "max_tokens": 1024, "system": SYSTEM, "messages": messages}
    ).encode()
    req = urllib.request.Request(
        URL, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.load(r)
    return "".join(b.get("text", "") for b in out.get("content", []))


def main():
    print("claude-free-router - chat (scrivi 'esci' per uscire)\n")
    messages = []
    while True:
        try:
            user = input("tu> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user.lower() in ("exit", "quit", "esci"):
            break
        if not user:
            continue
        messages.append({"role": "user", "content": user})
        try:
            reply = ask(messages)
        except Exception as e:
            print("errore:", e)
            messages.pop()
            continue
        print("ai>", reply, "\n")
        messages.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()
