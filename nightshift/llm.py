"""Local model via Ollama. No hosted LLM API is called anywhere in Night Shift."""
from __future__ import annotations

import json
import os
import urllib.request

OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("NIGHTSHIFT_MODEL", "qwen3:8b")


def chat_json(system: str, user: str, seed: int = 0, retries: int = 1) -> tuple[dict, int, int]:
    """Return (parsed JSON, prompt tokens, output tokens). Tokens come from Ollama's own counters."""
    tin = tout = 0
    last = ""
    for attempt in range(retries + 1):
        body = json.dumps({
            "model": MODEL, "stream": False, "format": "json", "think": False,
            "options": {"temperature": 0.2, "seed": seed + attempt, "num_ctx": 8192},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }).encode()
        req = urllib.request.Request(f"{OLLAMA}/api/chat", body, {"Content-Type": "application/json"})
        r = json.load(urllib.request.urlopen(req, timeout=300))
        tin += r.get("prompt_eval_count", 0)
        tout += r.get("eval_count", 0)
        last = r["message"]["content"]
        try:
            return json.loads(last), tin, tout
        except json.JSONDecodeError:
            continue
    return {"action": "invalid", "raw": last[:200]}, tin, tout
