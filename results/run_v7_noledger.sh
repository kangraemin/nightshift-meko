#!/bin/bash
cd "$(dirname "$0")/.."
export NIGHTSHIFT_MODEL=qwen3:8b
for s in 1 2 3; do python3 -m nightshift.run --offline --seed $s --modes nightshift --no-ledger --run-id off-v7-s$s-noledger > results/off-v7-s$s-noledger.log 2>&1 & done
wait
