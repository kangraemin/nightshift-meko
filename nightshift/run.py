"""Run the benchmark: baseline vs Night Shift on the same 40 pages, same seed.

    python -m nightshift.run --offline --limit 6        # no account needed
    python -m nightshift.run --seed 1                   # real Meko + AMP Postgres
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import time
from pathlib import Path

from . import agents, llm, sim
from .ledger import Ledger
from .meko import accounts

ROOT = Path(__file__).resolve().parent.parent


class Events:
    def __init__(self, path: Path):
        self.f = open(path, "a")
        self.t0 = time.time()

    def __call__(self, kind, res, data):
        ev = {"t": round(time.time() - self.t0, 2), "kind": kind, **data}
        if res is not None:
            ev.update({"mode": res.mode, "inc": res.inc.id, "shift": res.inc.shift, "steps": res.steps,
                       "tokens": res.tin + res.tout})
        self.f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        self.f.flush()


def summarize(rows):
    def agg(rs):
        if not rs:
            return None
        return {"n": len(rs), "accuracy": round(sum(r["resolved"] for r in rs) / len(rs), 3),
                "steps_mean": round(st.mean(r["steps"] for r in rs), 2),
                "tokens_mean": round(st.mean(r["tokens_in"] + r["tokens_out"] for r in rs)),
                "seconds_mean": round(st.mean(r["seconds"] for r in rs), 2),
                "stale_fix_applied": sum(r.get("stale_fix_applied") or 0 for r in rs),
                "stale_fix_blocked": sum(r.get("stale_fix_blocked") or 0 for r in rs)}
    out = {}
    for mode in ("baseline", "nightshift"):
        rs = [r for r in rows if r["mode"] == mode]
        out[mode] = {"all": agg(rs), "first_seen": agg([r for r in rs if r["first_seen"] and not r["trap"]]),
                     "repeat": agg([r for r in rs if not r["first_seen"]]),
                     "trap": agg([r for r in rs if r["trap"]])}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="in-process Meko stand-in + SQLite")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--limit", type=int, default=len(sim.SCHEDULE))
    ap.add_argument("--modes", default="baseline,nightshift")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--no-librarian", action="store_true")
    ap.add_argument("--no-share", action="store_true", help="ablation: private memory only, no curator, no collective search")
    a = ap.parse_args()

    run_id = a.run_id or f"{'off' if a.offline else 'live'}-s{a.seed}{'-noshare' if a.no_share else ''}-{time.strftime('%m%d%H%M')}"
    out = ROOT / "results" / run_id
    out.mkdir(parents=True, exist_ok=True)
    if a.offline:
        os.environ["NIGHTSHIFT_LEDGER"] = "sqlite"
    emit = Events(out / "events.jsonl")
    ledger = Ledger(run_id)
    ledger.upsert_services({s: "A" if i % 2 == 0 else "B" for i, s in enumerate(sim.SERVICES)})
    ma, mb = accounts(a.offline)
    runbook = (ROOT / "runbook" / "runbook.md").read_text()
    modes = a.modes.split(",")
    if "nightshift" in modes:
        cid = ma.conversation("nightshift:librarian", f"{run_id} setup")
        up = ma.kb_upload(cid, "runbook.md", runbook.encode())
        emit("setup", None, {"run_id": run_id, "runbook": str(up)[:300], "ledger": ledger.kind,
                             "model": llm.MODEL, "datapack": getattr(ma, "datapack", "")})

    incidents = sim.build(a.seed)[: a.limit]
    rows, learned_text, night = [], {}, -1
    for inc in incidents:
        if inc.night != night and night >= 0 and "nightshift" in modes and not a.no_share:
            agents.curator(ma, ledger, emit, night)
            if not a.no_librarian and night % 5 == 4:
                for m in (ma, mb):
                    try:
                        agents.librarian(m, ledger, learned_text, emit, night)
                    except Exception as e:  # self-learning is best effort; never lose a benchmark night
                        emit("librarian", None, {"night": night, "error": str(e)[:200]})
        night = inc.night
        for mode in modes:
            fresh = sim.build(a.seed)[inc.idx]          # identical incident for each mode
            if mode == "baseline":
                r = agents.run_baseline(fresh, runbook, emit, a.seed)
            else:
                meko = ma if inc.shift == "A" else mb
                r = agents.run_nightshift(fresh, meko, ledger, emit, a.seed, llm.MODEL, share=not a.no_share)
                if r.resolved:
                    learned_text[fresh.alert] = (f"{fresh.cause.paged} root cause: {r.root_cause}; "
                                                 f"fix {r.history[-1].get('fix')} on {r.history[-1]['service']}")
            ledger.record_incident(mode, r.row())
            rows.append({"mode": mode, **r.row(), "history": r.history, "root_cause": r.root_cause,
                         "learned": r.learned, "retired": [x[0] for x in r.retired]})
            print(f"{inc.id} {inc.shift} {inc.cause.id:4} {'NEW ' if inc.first_seen else 'rep '}{'TRAP ' if inc.trap else ''}"
                  f"{mode:10} {'OK ' if r.resolved else 'FAIL'} steps={r.steps:2} tok={r.tin + r.tout:6} {r.seconds:5.1f}s "
                  f"mq={r.match_quality}", flush=True)
    if "nightshift" in modes and not a.no_share:
        agents.curator(ma, ledger, emit, night)

    summary = {"run_id": run_id, "seed": a.seed, "model": llm.MODEL, "ledger": ledger.kind,
               "offline": a.offline, "share": not a.no_share, "incidents": len(incidents), "results": summarize(rows),
               "meko_calls": {"A": dict(ma.calls), "B": dict(mb.calls)},
               "fix_validity": [list(map(str, r)) for r in ledger.table("fix_validity")]}
    (out / "incidents.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    emit("done", None, {"summary": summary["results"]})
    print(json.dumps(summary["results"], indent=1))


if __name__ == "__main__":
    main()
