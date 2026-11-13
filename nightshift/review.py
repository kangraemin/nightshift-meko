"""Review queue: a person approves or rejects learned fixes before the curator shares them.

    python -m nightshift.review --run-id prod list
    python -m nightshift.review --run-id prod approve <memory_id> --by alice
    python -m nightshift.review --run-id prod reject <memory_id> --by alice --reason "restarts hide the leak"
    python -m nightshift.review --run-id prod promote          # share approved fixes now (datapack owner key)

The benchmark runs with NIGHTSHIFT_REVIEW=auto, so every valid fix is approved as "auto (benchmark)".
Set NIGHTSHIFT_REVIEW=manual and the curator only shares what was approved here.
A rejected fix is retired in the ledger: it is never shared and the Fixer refuses it like any stale fix.
"""
from __future__ import annotations

import argparse
import json

from .ledger import Ledger


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("cmd", choices=["list", "approve", "reject", "promote"])
    ap.add_argument("memory_id", nargs="?")
    ap.add_argument("--by", default="reviewer")
    ap.add_argument("--reason", default="")
    a = ap.parse_args(argv)
    if a.offline:
        import os
        os.environ["NIGHTSHIFT_LEDGER"] = "sqlite"
    lg = Ledger.open(a.run_id)
    if a.cmd == "list":
        rows = lg.pending_review()
        for r in rows:
            print(f"{r['memory_id']}  {r['fix_id']} on {r['service']}  [{r['symptom']}]  learned by {r['learned_by']} in {r['learned_in']}")
        print(f"{len(rows)} fix(es) waiting for review")
        return rows
    if a.cmd in ("approve", "reject") and not a.memory_id:
        ap.error("memory_id is required")
    if a.cmd == "approve":
        lg.approve(a.memory_id, a.by)
    elif a.cmd == "reject":
        if not a.reason:
            ap.error("--reason is required: the next shift reads it")
        lg.reject(a.memory_id, a.by, a.reason)
    else:
        from . import agents
        from .meko import accounts
        owner, _ = accounts(a.offline)
        ok = agents.curator(owner, lg, lambda *x: None, night=-1, review="manual")
        print(f"promoted {len(ok)} approved fix(es)")
        return ok
    print(json.dumps(lg.status(a.memory_id)))


if __name__ == "__main__":
    main()
