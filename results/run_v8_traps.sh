#!/bin/bash
# Trap-heavy schedule (10 traps), offline, ledger on vs off, 3 seeds.
cd "$(dirname "$0")/.."
export NIGHTSHIFT_MODEL=qwen3:8b
for s in 1 2 3; do
  python3 -m nightshift.run --offline --schedule traps --seed $s --modes nightshift --run-id off-v8t-s$s > results/off-v8t-s$s.log 2>&1 &
  python3 -m nightshift.run --offline --schedule traps --seed $s --modes nightshift --no-ledger --run-id off-v8t-s$s-noledger > results/off-v8t-s$s-noledger.log 2>&1 &
done
wait
