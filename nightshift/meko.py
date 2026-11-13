"""Meko access for Night Shift.

`Meko` talks to the hosted Meko MCP server over streamable HTTP (no SDK, stdlib only).
`FakeMeko` is an in-process stand-in with the same surface so the whole demo runs
offline (`make demo-offline`) without an account. Measured results in the README
come from the real server.
"""
from __future__ import annotations

import base64
import itertools
import json
import os
import re
import time
import urllib.request
from collections import Counter
from dataclasses import dataclass

URL = os.environ.get("MEKO_URL", "https://mcp.mekodata.ai/mcp")


@dataclass
class Hit:
    text: str
    memory_id: str | None
    source: str        # "private" (my agents' memory) or "collective" (promoted, team-wide)
    score: float


class MekoError(RuntimeError):
    pass


class Meko:
    def __init__(self, name: str, key: str, datapack: str):
        self.name, self.key, self.datapack = name, key, datapack
        self._ids = itertools.count(1)
        self._session = None
        self.calls: Counter = Counter()
        self.log: list[dict] = []
        self._init()

    # --- transport -------------------------------------------------------
    def _post(self, payload: dict):
        h = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream", "User-Agent": "nightshift/0.1"}
        if self._session:
            h["Mcp-Session-Id"] = self._session
        req = urllib.request.Request(URL, json.dumps(payload).encode(), h)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    self._session = r.headers.get("Mcp-Session-Id") or self._session
                    body = r.read().decode()
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 502, 503, 504) and attempt < 3:
                    time.sleep(2 + attempt * 3)
                    continue
                raise
        if not body.strip():
            return None
        if body.lstrip().startswith("{"):
            return json.loads(body)
        msgs = [json.loads(l[5:]) for l in body.splitlines() if l.startswith("data:") and l[5:].strip()]
        return msgs[-1] if msgs else None

    def _init(self):
        self._post({"jsonrpc": "2.0", "id": next(self._ids), "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "nightshift", "version": "0.1"}}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, tool: str, **args):
        self.calls[tool] += 1
        t = time.time()
        r = self._post({"jsonrpc": "2.0", "id": next(self._ids), "method": "tools/call",
                        "params": {"name": tool, "arguments": args}})
        res = (r or {}).get("result", {})
        out = res.get("structuredContent")
        if isinstance(out, dict) and "result" in out and isinstance(out["result"], str):
            try:
                out = json.loads(out["result"])
            except json.JSONDecodeError:
                out = out["result"]
        if out is None:
            txt = "".join(c.get("text", "") for c in res.get("content", []))
            try:
                out = json.loads(txt)
            except json.JSONDecodeError:
                out = txt
        self.log.append({"tool": tool, "ms": int((time.time() - t) * 1000)})
        if isinstance(out, str) and ("validation error" in out or out.startswith("Error")):
            raise MekoError(f"{tool}: {out[:300]}")
        return out

    # --- the surface Night Shift uses -------------------------------------
    def conversation(self, agent: str, title: str) -> str:
        r = self.call("conversation_create", agent_id=agent, title=title, datapack_id=self.datapack)
        return r.get("id") or r.get("conversation_id")

    def add_message(self, cid, agent, input, output, reasoning, plan):
        return self.call("conversation_add_message", conversation_id=cid, agent_id=agent, input=input,
                         output=output, reasoning=reasoning, plan=list(plan), datapack_id=self.datapack)

    def memory_add(self, cid, agent, text, metadata: dict) -> str | None:
        r = self.call("memory_add", conversation_id=cid, agent_id=agent, text=text,
                      metadata=json.dumps(metadata), datapack_id=self.datapack)
        res = (r or {}).get("results") or []
        return res[0]["id"] if res else None

    def context_search(self, cid, agent, query, limit=5) -> tuple[list[Hit], str, dict]:
        r = self.call("context_search", conversation_id=cid, agent_id=agent, query=query,
                      datapack_id=self.datapack, limit=limit)
        hits = []
        for m in ((r.get("results") or {}).get("memory") or []):
            hits.append(Hit(m.get("memory") or m.get("data") or m.get("text") or "", m.get("id"),
                            "private", float(m.get("score") or m.get("rerank_score") or 0)))
        return hits, r.get("match_quality", "low"), r

    def kb_search(self, cid, agent, query, limit=5) -> list[Hit]:
        r = self.call("knowledgebase_search", conversation_id=cid, agent_id=agent, query=query,
                      datapack_id=self.datapack, limit=limit)
        hits = []
        for k in (r.get("results") or []):
            meta = k.get("metadata_filters") or {}
            hits.append(Hit(k.get("chunk_text", ""), meta.get("memory_id"), "collective",
                            float(k.get("rerank_score") or 0)))
        return hits

    def promote(self, cid, memory_ids):
        return self.call("memory_promote", conversation_id=cid, memory_ids=list(memory_ids), datapack_id=self.datapack)

    def track_tokens(self, cid, tin, tout, model):
        return self.call("track_token_usage", conversation_id=cid, name="nightshift-local-llm",
                         input_tokens=tin, output_tokens=tout, total_tokens=tin + tout, model=model,
                         datapack_id=self.datapack)

    def artifact_put(self, cid, agent, filename, text):
        return self.call("artifact_put", filename=filename, content_base64=base64.b64encode(text.encode()).decode(),
                         content_type="text/markdown", conversation_id=cid, datapack_id=self.datapack, agent_id=agent)

    def kb_upload(self, cid, filename, data: bytes, wait=True):
        """Presigned multipart POST to S3, then poll indexing."""
        import hashlib
        import uuid
        r = self.call("knowledgebase_upload_document", filename=filename, size=len(data),
                      content_hash=hashlib.sha256(data).hexdigest(), conversation_id=cid,
                      content_head_base64=base64.b64encode(data[:3072]).decode(), datapack_id=self.datapack)
        if r.get("already_uploaded") or not r.get("url"):
            return r
        b = uuid.uuid4().hex
        parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                 for k, v in r["fields"].items()]
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                     f'Content-Type: text/markdown\r\n\r\n'.encode() + data + f"\r\n--{b}--\r\n".encode())
        req = urllib.request.Request(r["url"], b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={b}"})
        urllib.request.urlopen(req, timeout=60).read()
        st = {}
        for _ in range(30 if wait else 0):
            time.sleep(10)
            st = self.call("knowledgebase_document_status", document_key=r["key"], conversation_id=cid,
                           datapack_id=self.datapack)
            if str(st.get("state", st.get("status", ""))).lower() in ("indexed", "ready", "completed", "complete", "failed", "error"):
                break
        return {"upload": r.get("key"), "status": st}

    def sl_status(self, cid):
        return self.call("selflearning_status", datapack_id=self.datapack, conversation_id=cid)

    def sl_shortfalls(self, cid, todo="add_context"):
        return self.call("selflearning_shortfalls", todo_id=todo, datapack_id=self.datapack, conversation_id=cid)

    def sl_verify(self, cid, log_id):
        return self.call("selflearning_verify", log_id=log_id, datapack_id=self.datapack, conversation_id=cid)


# ---------------------------------------------------------------------------
# Offline stand-in. Shared state across the two "accounts" mimics one datapack.
# ---------------------------------------------------------------------------
_WORD = re.compile(r"[a-z0-9_.-]+")


def _toks(s: str) -> set[str]:
    return {w for w in _WORD.findall(s.lower()) if len(w) > 2}


class _Store:
    def __init__(self):
        self.private: dict[str, list[dict]] = {}
        self.collective: list[dict] = []
        self.log: list[dict] = []
        self.n = itertools.count(1)


class FakeMeko:
    def __init__(self, name: str, store: _Store, owner: bool):
        self.name, self.s, self.owner = name, store, owner
        self.calls: Counter = Counter()
        self.log: list[dict] = []
        self.datapack = "offline"

    def _search(self, rows, query):
        q = _toks(query)
        scored = []
        for m in rows:
            t = _toks(m["text"])
            sc = len(q & t) / max(1, len(q))
            scored.append((sc, m))
        scored.sort(key=lambda x: -x[0])
        return [(sc, m) for sc, m in scored if sc > 0.15][:5]

    def conversation(self, agent, title):
        self.calls["conversation_create"] += 1
        return f"off-{next(self.s.n)}"

    def add_message(self, *a, **k):
        self.calls["conversation_add_message"] += 1

    def memory_add(self, cid, agent, text, metadata):
        self.calls["memory_add"] += 1
        mid = f"mem-{next(self.s.n):04d}"
        self.s.private.setdefault(self.name, []).append({"id": mid, "text": text})
        return mid

    def context_search(self, cid, agent, query, limit=5):
        self.calls["context_search"] += 1
        res = self._search(self.s.private.get(self.name, []), query)
        best = res[0][0] if res else 0
        band = "high" if best >= 0.5 else "mid" if best >= 0.3 else "low"
        if band != "high":
            self.s.log.append({"log_id": f"log-{next(self.s.n)}", "query": query, "band": band, "who": self.name})
        return [Hit(m["text"], m["id"], "private", sc) for sc, m in res], band, {}

    def kb_search(self, cid, agent, query, limit=5):
        self.calls["knowledgebase_search"] += 1
        return [Hit(m["text"], m["id"], "collective", sc) for sc, m in self._search(self.s.collective, query)]

    def promote(self, cid, memory_ids):
        self.calls["memory_promote"] += 1
        if not self.owner:
            return {"error": "FORBIDDEN"}
        for rows in self.s.private.values():
            for m in list(rows):
                if m["id"] in memory_ids:
                    rows.remove(m)
                    self.s.collective.append(m)
        return {"inserted_ids": list(memory_ids)}

    def track_tokens(self, *a, **k):
        self.calls["track_token_usage"] += 1

    def kb_upload(self, cid, filename, data: bytes, wait=True):
        self.calls["knowledgebase_upload_document"] += 1
        for sec in data.decode().split("\n## ")[1:]:
            self.s.collective.append({"id": None, "text": "## " + sec.strip()})
        return {"status": "offline"}

    def artifact_put(self, *a, **k):
        self.calls["artifact_put"] += 1

    def sl_status(self, cid):
        self.calls["selflearning_status"] += 1
        low = [l for l in self.s.log if l["who"] == self.name]
        return {"quality": {"searches": len(low)}, "todos": [{"id": "add_context", "searches": len(low)}]}

    def sl_shortfalls(self, cid, todo="add_context"):
        self.calls["selflearning_shortfalls"] += 1
        return {"items": [{"query": l["query"], "log_id": l["log_id"], "band": l["band"], "state": "open"}
                          for l in self.s.log if l["who"] == self.name]}

    def sl_verify(self, cid, log_id):
        self.calls["selflearning_verify"] += 1
        row = next(l for l in self.s.log if l["log_id"] == log_id)
        _, band, _ = self.context_search(cid, "librarian", row["query"])
        self.s.log = [l for l in self.s.log if l["log_id"] != log_id]
        return {"before": row["band"], "after": band, "outcome": "improved" if band == "high" else "unchanged"}


def accounts(offline: bool):
    """Return (shift A client, shift B client). A is the datapack owner (curator)."""
    if offline:
        st = _Store()
        return FakeMeko("A", st, owner=True), FakeMeko("B", st, owner=False)
    dp = os.environ["NIGHTSHIFT_DATAPACK_ID"]
    return (Meko("A", os.environ["MEKO_API_KEY"], dp), Meko("B", os.environ["MEKO_B_API_KEY"], dp))
