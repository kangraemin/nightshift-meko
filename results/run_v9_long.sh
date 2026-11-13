#!/bin/bash
# 120 pages (--schedule long), a quarter of the on-call notes wrong, 3 seeds:
# Night Shift, the runbook lane with every note pasted into the wiki, and Night Shift without the ledger.
cd "$(dirname "$0")/.."
export NIGHTSHIFT_MODEL=qwen3:8b
export NIGHTSHIFT_NUM_CTX=16384  # the wiki lane's prompt grows with every note; keep it all in context
jobs=()
for s in 1 2 3; do
  jobs+=("--seed $s --modes nightshift --run-id off-v9L-s$s")
  jobs+=("--seed $s --modes baseline --wiki-notes --run-id off-v9L-s$s-wikinotes")
done
for s in 1 2 3; do
  jobs+=("--seed $s --modes nightshift --no-ledger --run-id off-v9L-s$s-noledger")
done
printf '%s\n' "${jobs[@]}" | xargs -P 6 -I{} sh -c 'id=$(echo "{}" | sed "s/.*--run-id //"); python3 -m nightshift.run --offline --schedule long --note-errors 0.25 {} > results/$id.log 2>&1'
