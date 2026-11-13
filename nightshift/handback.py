"""Write down a fix you made by hand, so the agents (and the other shift) find it next time.

    python -m nightshift.handback --run-id prod --shift A --inc INC-1042 \
        --paged inventory-svc --alert "inventory-svc OOMKilled, 4 restarts in 10 min" \
        --cause "memory leak in new release" --evidence-cmd "get_deploys inventory-svc" \
        --evidence "inventory-svc v5.8.0 deployed 2 min ago; previous v5.7.0 healthy" \
        --fix F04 --target v5.7.0 --service inventory-svc

The note is stored in Meko as a memory of that shift (same text format the benchmark uses for the
on-call human), recorded as valid in the ledger with learned_by "<shift> (human)", and waits in the
review queue like any other fix. The decision trace is a Meko conversation, so the portal shows who wrote it.
"""
from __future__ import annotations

import argparse
import os
import time

from .agents import human_note
from .ledger import Ledger
from .meko import accounts


def handback(meko, ledger, *, shift, inc, paged, alert, cause, evidence_cmd, evidence, fix, target, service,
             changing_cmd=None):
    agent = f"nightshift:human-{shift.lower()}"
    text = human_note(inc, paged, alert, cause, evidence_cmd, evidence, fix, target, service, shift, changing_cmd)
    cid = meko.conversation(agent, f"{inc} human fix")
    meko.add_message(cid, agent, f"Page {inc}: {alert}", f"RESOLVED by a person. Root cause: {cause}",
                     f"Evidence: {evidence_cmd} -> {evidence}", [f"apply_fix {fix} {service} target {target}"])
    mid = meko.memory_add(cid, agent, text, {"incident": inc, "fix": fix, "service": service, "verified": True,
                                             "shift": shift, "source": "human"})
    if mid:
        ledger.learn(mid, fix, service, f"{paged}: {alert}", f"{shift} (human)", inc)
    return mid, text


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--shift", required=True, choices=["A", "B"])
    ap.add_argument("--inc", default=f"manual-{time.strftime('%m%d%H%M')}")
    for f in ("paged", "alert", "cause", "evidence-cmd", "evidence", "fix", "target", "service"):
        ap.add_argument(f"--{f}", required=True)
    ap.add_argument("--changing-cmd", help="the target changes per incident: read it with this command first")
    a = ap.parse_args(argv)
    if a.offline:
        os.environ["NIGHTSHIFT_LEDGER"] = "sqlite"
    ma, mb = accounts(a.offline)
    mid, text = handback(ma if a.shift == "A" else mb, Ledger.open(a.run_id), shift=a.shift, inc=a.inc, paged=a.paged,
                         alert=a.alert, cause=a.cause, evidence_cmd=a.evidence_cmd, evidence=a.evidence, fix=a.fix,
                         target=a.target, service=a.service, changing_cmd=a.changing_cmd)
    print(f"stored memory {mid}; waiting for review (python -m nightshift.review --run-id {a.run_id} list)")
    print(text)


if __name__ == "__main__":
    main()
