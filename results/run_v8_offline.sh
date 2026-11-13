#!/bin/bash
# Every offline variant, current code, same machine, 3 seeds. 6 runs at a time.
cd "$(dirname "$0")/.."
export NIGHTSHIFT_MODEL=qwen3:8b
jobs=()
for s in 1 2 3; do
  jobs+=("--seed $s --run-id off-v8-s$s")                                   # runbook lane + full Night Shift
  jobs+=("--seed $s --modes baseline --wiki-notes --run-id off-v8-s$s-wikinotes")
  jobs+=("--seed $s --modes nightshift --no-humans --run-id off-v8-s$s-nohumans")
  jobs+=("--seed $s --modes nightshift --no-share --run-id off-v8-s$s-noshare")
  jobs+=("--seed $s --modes nightshift --no-memory --run-id off-v8-s$s-nomemory")
  jobs+=("--seed $s --modes nightshift --no-ledger --run-id off-v8-s$s-noledger")
done
printf '%s\n' "${jobs[@]}" | xargs -P 6 -I{} sh -c 'id=$(echo "{}" | sed "s/.*--run-id //"); python3 -m nightshift.run --offline {} > results/$id.log 2>&1'
