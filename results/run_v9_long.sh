#!/bin/bash
# 120 pages (--schedule long), a quarter of the on-call notes wrong, 3 seeds:
# Night Shift, the runbook lane with every note pasted into the wiki, and Night Shift without the ledger. 6 at a time.
cd "$(dirname "$0")/.."
export NIGHTSHIFT_MODEL=qwen3:8b
export NIGHTSHIFT_NUM_CTX=16384  # the wiki lane's prompt grows with every note; keep it all in context
run() { id=$1; shift; [ -e results/$id/summary.json ] && return
  python3 -m nightshift.run --offline --schedule long --note-errors 0.25 "$@" --run-id $id > results/$id.log 2>&1; }
for s in 1 2 3; do
  run off-v9L-s$s --seed $s --modes nightshift &
  run off-v9L-s$s-wikinotes --seed $s --modes baseline --wiki-notes &
done
wait
for s in 1 2 3; do run off-v9L-s$s-noledger --seed $s --modes nightshift --no-ledger & done
wait
