"""Alertmanager receiver: a real alert gets the team's memory, checked against the ledger, in seconds.

    python -m nightshift.webhook --run-id prod --port 9095
    # alertmanager.yml:  receivers: [{name: nightshift, webhook_configs: [{url: "http://host:9095/alertmanager"}]}]

For each firing alert it searches the shift's private memory and the team's collective knowledge in
Meko, joins every hit with the Postgres fix ledger, and answers with what the team already knows:
fixes still valid (and who learned them, on which page), and fixes the ledger retired for this alert,
so nobody re-applies them. It does not touch production: it suggests, a person or the agent acts.
If SLACK_WEBHOOK_URL is set, the same text is posted there. Each triage is a Meko conversation (decision trace).
Labels used: `service` (or `job`) and the annotation `summary` (or `alertname`); `shift` label picks the key (default A).
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from .agents import FIX_RE, TARGET_RE, _hit_line
from .ledger import Ledger
from .meko import accounts


def triage(meko, ledger, shift: str, paged: str, alert: str) -> dict:
    agent = f"nightshift:shift-{shift.lower()}"
    cid = meko.conversation(agent, f"alertmanager: {alert}")
    query = f"{paged}: {alert}"
    private, band, _ = meko.context_search(cid, agent, query)
    collective = meko.kb_search(cid, agent, query)
    valid, retired, other = [], [], []
    for h in private[:3] + collective[:4]:
        st = ledger.status(h.memory_id) if h.memory_id else None
        m, t = FIX_RE.search(h.text), TARGET_RE.search(h.text)
        item = {"line": _hit_line(h, st), "fix": m.group(1) if m else None, "target": t.group(1) if t else None,
                "memory_id": h.memory_id, "source": h.source}
        same = bool(st) and str(st.get("symptom") or "").startswith(f"{paged}: ")
        if st and same and st["status"] == "retired":
            retired.append(item | {"reason": st["retired_reason"]})
        elif st and same:
            valid.append(item | {"learned_by": st["learned_by"], "learned_in": st["learned_in"]})
        else:
            other.append(item)
    out = {"alert": alert, "service": paged, "match": band, "valid": valid, "retired": retired, "related": other[:3]}
    meko.add_message(cid, agent, f"Alert: {alert}", text(out), f"{band} match; {len(valid)} valid, {len(retired)} retired",
                     [f"suggest {v['fix']} target {v['target']}" for v in valid])
    return out


def text(t: dict) -> str:
    lines = [f"*{t['service']}*: {t['alert']} (memory match: {t['match']})"]
    for v in t["valid"]:
        lines.append(f"Seen before: {v['fix']} target {v['target']}, learned by shift {v['learned_by']} on {v['learned_in']}. Ledger: still valid.")
    for r in t["retired"]:
        lines.append(f"Do not re-apply {r['fix']} target {r['target']}: {r['reason']}.")
    if not t["valid"] and not t["retired"]:
        lines.append("Nothing the team has fixed matches this alert yet. When it is fixed, record it: python -m nightshift.handback ...")
    return "\n".join(lines)


def handle(payload: dict, meko_for, ledger) -> list[dict]:
    out = []
    for al in payload.get("alerts", []):
        if al.get("status", "firing") != "firing":
            continue
        lb, an = al.get("labels", {}), al.get("annotations", {})
        paged = lb.get("service") or lb.get("job") or "unknown"
        alert = an.get("summary") or lb.get("alertname") or "alert"
        shift = (lb.get("shift") or "A").upper()
        out.append(triage(meko_for(shift), ledger, shift, paged, alert))
    return out


def serve(run_id: str, port: int, offline: bool = False):
    if offline:
        os.environ["NIGHTSHIFT_LEDGER"] = "sqlite"
    ma, mb = accounts(offline)
    ledger = Ledger.open(run_id)
    slack = os.environ.get("SLACK_WEBHOOK_URL")

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
            res = handle(json.loads(body or b"{}"), lambda s: ma if s == "A" else mb, ledger)
            if slack:
                for t in res:
                    urllib.request.urlopen(urllib.request.Request(slack, json.dumps({"text": text(t)}).encode(),
                                                                  {"Content-Type": "application/json"}), timeout=10)
            data = json.dumps(res).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    print(f"Night Shift webhook on :{port}/alertmanager (ledger run {run_id}, {ledger.kind})")
    HTTPServer(("", port), H).serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--port", type=int, default=9095)
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    serve(a.run_id, a.port, a.offline)
