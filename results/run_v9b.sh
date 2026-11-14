#!/bin/bash
# After the triage change (870a932): Night Shift and its no-ledger ablation on the 120-page schedule,
# and Night Shift on the 40-page schedule to check nothing regressed. 3 seeds, 6 at a time.
cd "$(dirname "$0")/.."
export NIGHTSHIFT_MODEL=qwen3:8b
export NIGHTSHIFT_NUM_CTX=16384
run() { id=$1; shift; [ -e results/$id/summary.json ] && return
  python3 -m nightshift.run --offline "$@" --run-id $id > results/$id.log 2>&1; }
for s in 1 2 3; do
  run off-v9bL-s$s --schedule long --note-errors 0.25 --seed $s --modes nightshift &
  run off-v9bL-s$s-noledger --schedule long --note-errors 0.25 --seed $s --modes nightshift --no-ledger &
done
wait
for s in 1 2 3; do run off-v9b-s$s --seed $s --modes nightshift & done
wait
