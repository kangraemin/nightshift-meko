# Night Shift

On-call agents that never solve the same incident twice, even when a different shift caught it the first time.

Night Shift is a two-shift incident response team built on the [Meko Context Engine](https://mekodata.ai).
Shift A and shift B are two Meko members with their own keys.
When one shift fixes a page, the fix becomes a private memory.
When the agent gives up, the on-call human fixes the page and their note goes into Meko the same way, so the agents also learn from people.
At the end of the night a curator (the datapack owner) promotes every fix the ledger still marks valid, and the other shift finds it at triage.
A Postgres ledger records whether each remembered fix still works, so a fix that stopped working is retired instead of being applied again.

Theme: **02 Agentic support: Never Solve It Twice** (internal SRE support).

In short, measured the way the theme asks ("how much faster and cheaper repeat issues get solved"): against a wiki that holds every on-call note, Night Shift solves repeat pages in 3.3 steps instead of 5.9 and at 42% of the tokens (35% by page 120, as the wiki grows), with 2 to 9 points less accuracy (89% vs 91% over 40 pages; 85% vs 88% over 120, and 79% vs 88% on the last 40). The Postgres ledger cuts wrong fixes 27–52% against memory alone, though the wiki lane applies fewer still. On-call SRE is support for internal customers: the pages are the tickets, the shifts are the support agents. All numbers, including the ones that go against us, are under Results.

- Demo video (4:51): https://kangraemin.github.io/nightshift-meko/docs/video/nightshift-demo.mp4
- Replay a live run in the browser: https://kangraemin.github.io/nightshift-meko/dashboard/
- Slides: [docs/deck.pdf](docs/deck.pdf)

![architecture](docs/architecture.svg)

## What it measures

The same 40 pages are answered twice, by the same local model (qwen3:8b through Ollama), in the same order:

- **Without memory**: the whole team runbook is pasted into the prompt. This is what most teams do today.
- **Night Shift**: no runbook in the prompt. The agent searches Meko first, checks each hit against the Postgres ledger, then investigates.

The incidents come from a deterministic simulator, so nobody grades the answers by opinion.
A page counts as resolved only when the agent applies the right fix to the right target on the right service.
Targets are company-specific (a pgbouncer pool name, a JVM options key, a node name), so the model cannot guess them from general knowledge.
It has to find them in diagnostic output or in a memory.

The 40 pages include:

- 12 first-seen causes, where memory cannot help yet
- 25 repeats of a cause some shift already solved
- 3 traps: the alert looks exactly like an old one, but the old fix no longer works

<!-- RESULTS -->
## Results

Live runs: 3 seeds (each seed shuffles which shift gets which page), two real Meko members, AMP Postgres ledger, qwen3:8b. Every number below comes from the `summary.json` of a run in `results/published/`. The live runs were made with commit d97e29f; later commits added the review step (auto-approve by default, as in those runs), the handback CLI, the webhook and the offline-only options. The offline tables use later code; runs from commit b4bc14b on record their commit in `summary.json`.

| Pages | Runbook in prompt: resolved | Night Shift: resolved | Steps per page | Tokens per page | Seconds per page |
|---|---|---|---|---|---|
| First-seen causes (12) | 56% (67% / 58% / 42%) | **61%** (67% / 58% / 58%) | 8.4 → 7.2 | 11,173 → 8,948 | 216 → 211 |
| Repeats (25) | 56% (56% / 60% / 52%) | **92%** (96% / 92% / 88%) | 8.7 → 2.8 | 11,662 → 3,972 | 161 → 55 |
| Traps (3) | 33% (33% / 33% / 33%) | 33% (33% / 33% / 33%) | 9.7 → 9.4 | 13,358 → 13,945 | 171 → 165 |
| All pages (40) | 54% (57% / 57% / 48%) | **78%** (82% / 78% / 75%) | 8.7 → 4.6 | 11,642 → 6,213 | 178 → 110 |

Cells show the mean over seeds, with each seed in brackets.

What the memory layer did, summed over the live runs:

- 61 fixes learned, 61 promoted by the curator, 28 retired by the ledger. These runs auto-approve every valid fix (`NIGHTSHIFT_REVIEW=auto`, no person at the keyboard); with `NIGHTSHIFT_REVIEW=manual` the curator shares only fixes a person approved with `python -m nightshift.review`.
- 26 pages the agent lost were fixed by the on-call human, and their note went into Meko the same way. In this benchmark the simulator writes that note from the page's true cause (a stand-in for the engineer's write-up), so pages that start from one are easier.
- Split of the 75 live repeat pages: 30 started from a human note and 28 were resolved; 42 started from the agents' own memory and 40 were resolved (95%); 3 used no memory and 1 of them were resolved. The offline table below gives the runbook lane the same human notes for a fair comparison.
- Retired fixes were refused 53 times. A fix the ledger had already retired was still applied 10 times on 8 pages: 7 of those pages were resolved, because the old cause had really come back and a diagnostic printed the same target (the fresh-evidence rule); the rest were wasted retries.
- The librarian answered 40 searches that Meko scored as weak matches. Replaying them, `selflearning_verify` moved 34 to a better match band, 27 of them to the high band (this is Meko's own retrieval score, not a page outcome).

### Where does the gain come from?

Offline (in-process Meko stand-in, SQLite ledger), same pages and model, 3 seeds, all runs on one machine with the same code. Each row switches one thing:

| Offline lane | First-seen | Repeats | All pages | Steps per repeat | Tokens per repeat |
|---|---|---|---|---|---|
| Runbook pasted into the prompt | 61% | 55% (52% / 60% / 52%) | 55% | 8.8 | 11,812 |
| Runbook pasted, plus each on-call human note added to the wiki (`--wiki-notes`) | 56% | 91% (92% / 92% / 88%) | 75% | 5.9 | 11,366 |
| Night Shift, runbook search only, nothing remembered (`--no-memory`) | 61% | 65% (64% / 60% / 72%) | 62% | 7.8 | 8,051 |
| Night Shift, private memory only, no sharing (`--no-share`) | 67% | 72% (76% / 60% / 80%) | 68% | 5.9 | 7,001 |
| Night Shift, shared memory of the agent's own fixes only (`--no-humans`) | 64% | 71% (72% / 68% / 72%) | 66% | 5.4 | 7,261 |
| Night Shift, shared memory of agent and human fixes | 61% | 89% (88% / 92% / 88%) | 78% | 3.3 | 4,816 |

Read fairly:

- Without any human input, repeats go from 55% to 71%: searching the runbook in Meko gives 65%, and the agents' own shared memory adds the rest (a few points, close to the seed-to-seed spread).
- Given the same human notes, a wiki resolves about as many repeats as Night Shift (91% vs 89%). Night Shift does it in 3.3 steps instead of 5.9 and 4,816 tokens instead of 11,366, because it retrieves the one note that matches instead of reading the whole wiki, and the wiki keeps growing every night.
- So the live jump from 56% to 92% on repeats is mostly the human notes reaching the next shift; what Meko adds over a wiki is the cost per page, the agents' own learning, and the ledger below.

- The no-memory row still searches the runbook in Meko instead of pasting it, so it isolates retrieval from memory.
- 13 of the 25 repeats first happened on the other shift. Without sharing, those start from scratch.
- The human notes are written by the simulator from the page's true cause, as a stand-in for what the on-call engineer types after fixing a page by hand. The `--wiki-notes` row gives the runbook lane the same notes, so the comparison stays fair; the `--no-humans` row shows what the agent's own memory does without any human input.

### Does the Postgres ledger matter?

Same offline runs, 3 seeds, with the ledger switched off (`--no-ledger`): memories are still learned and shared, but every remembered fix looks valid forever, like a plain memory store.

| Night Shift lane, offline | Repeats | Traps | All pages | Failed fixes applied to production | Fixes refused by the Fixer |
|---|---|---|---|---|---|
| Memory without the ledger (`--no-ledger`) | 91% | 33% | 78% | 19.7 per run | 22.3 per run |
| Memory checked against the ledger | 89% | 44% | 78% | 12.0 per run | 32.0 per run |

- A failed fix is a change (rollback, restart, drain, config edit) applied to the simulated production that did not clear the page; the simulator counts each one.
- Pages resolved barely move: the ledger does not find causes. What it changes is how many wrong changes reach (simulated) production: 19.7 → 12.0 per run (39% fewer).
- Without the ledger the Fixer still refuses a fix that already failed on the same page; the ledger adds refusals of fixes that failed on earlier pages, on either shift.

With only 3 traps per run the trap column above is noise. A second schedule (`--schedule traps`) keeps the same 12 first-seen causes but adds 10 traps, each followed by a repeat of the new cause; 3 seeds each, offline:

| Trap-heavy schedule | Traps resolved (10 per run) | Repeats | All pages | Failed fixes applied | Fixes refused by the Fixer |
|---|---|---|---|---|---|
| Memory without the ledger | 40% (40% / 40% / 40%) | 78% | 64% | 38.7 per run | 40.3 per run |
| Memory checked against the ledger | 33% (40% / 30% / 30%) | 85% | 68% | 28.3 per run | 37.3 per run |

- The ledger does not help on the trap page itself: an 8B model still has to find the new cause (the small difference is within noise).
- It helps on the page after. When the new cause comes back, Night Shift resolved 24 of 30 with the ledger and 20 of 30 without it, because the stale memory is marked RETIRED and replaced instead of being tried again; failed fixes applied drop accordingly.

### 120 pages, and a quarter of the on-call notes are wrong

The 40-page runs are short: by the end a wiki holding every note is still small. A third schedule (`--schedule long`) runs 120 pages: 40 pages of the 12 original causes, 40 pages in which 10 of them change cause one by one, then 40 pages of repeats. And 25% of on-call human notes now record the wrong fix (the restart the engineer tried first, which only seemed to help), the same notes for both lanes. Offline, 3 seeds, 16k context so the wiki never gets cut:

| 120-page lane | Repeats resolved | Repeats, pages 81–120 | After a cause changed | Tokens per page, pages 1–40 → 81–120 | Failed fixes applied | Wrong-note restarts applied |
|---|---|---|---|---|---|---|
| Runbook + every note pasted in the wiki | 88% | 88% | 84% | 10,397 → 22,332 | 30.7 per run | 0.3 per run |
| Night Shift without the ledger | 84% | 73% | 71% | 4,722 → 8,744 | 110.0 per run | 1.0 per run |
| Night Shift | 85% | 79% | 76% | 4,747 → 7,799 | 53.3 per run | 0.3 per run |

- The wiki lane wins on resolution here: 88% of repeats against 85%, and the gap is wider late in the run (88% vs 79% on pages 81–120). Keeping every note in the prompt works, as long as it fits.
- It is also what it costs: the wiki prompt grows with every note, from 10,397 to 22,332 tokens per page. Night Shift went from 4,747 to 7,799, about 35% of the wiki's cost by the end. These runs use a 16k context so the wiki is never cut.
- The ledger halves wrong changes compared with memory alone (110 → 53 failed fixes per run), but the wiki lane applied fewer still (31): Night Shift's agent tries remembered fixes more readily.
- Wrong notes did little damage in either lane: the recorded fix is a restart of the paged service, and the Fixer refuses a bare service name as a target.
- We also tried showing the model only still-valid memories, with retired ones collapsed into one line. It made repeats worse (120 pages and 40 pages alike) and was reverted; those runs are in `results/published/off-v9b*`.

### What did not improve

- Traps: Night Shift resolved 33% of trap pages, the runbook lane 33%. The ledger stops the stale fix, but finding the new cause is still up to an 8B model.
- The ledger can also over-block: when the old cause really returns but the agent never runs the diagnostic that prints the target, the retired fix stays refused and the page is lost (INC-33 in two of the three live seeds).
- First-seen pages: memory cannot help there. Live, Night Shift scored 61% against 56%; offline, the no-memory control scores 61% and the pasted runbook 61%, so the live first-seen gap is within run-to-run noise, not a gain we claim.
<!-- /RESULTS -->

## How Meko is used

| Meko feature | Where | Why |
|---|---|---|
| `conversation_create` + `conversation_add_message` (reasoning, plan) | every page | Decision trace for each page in the portal |
| `track_token_usage` | every page | local-model tokens show up next to the trace |
| `context_search` | triage | the shift's own memories, logged for self-learning |
| `knowledgebase_search` | triage | the team's collective knowledge: runbook + promoted fixes |
| `memory_add` | after a verified fix, or a human's fix of a lost page | evidence, root cause, fix, target, which shift, which page, agent or human |
| `memory_promote` | curator, end of night | only the datapack owner can promote; contributors cannot |
| `artifact_put` | after a verified fix | postmortem markdown per page |
| `knowledgebase_upload_document` | run setup | the team runbook (with its known gaps) |
| `selflearning_status` / `selflearning_shortfalls` / `selflearning_verify` | Librarian, every 5 nights | answer weak searches, then replay them to prove they improved |
| `context_search` + `knowledgebase_search` + `conversation_add_message` | `nightshift/webhook.py` | a real Alertmanager alert gets the team's memory and the ledger verdict, recorded as a decision trace |
| datapack sharing (Contributor role) | `scripts/share_datapack.py` | shift B is a real second member, not a second agent id |

### Postgres inside the context

Retrieval alone cannot tell a good memory from a stale one.
Night Shift keeps a small ledger next to the memories: in the live run it lives in the datapack's own Postgres (AMP), offline in a local SQLite file.
Meko's documented way to reach a datapack's Postgres is a direct SQL connection (there is no MCP tool for SQL), so the ledger connects with the datapack's own connection string:

- `fix_validity(memory_id, fix_id, service, symptom, learned_by, status, promoted, approval, approved_by, retired_reason, replaced_by)`
- `incidents(mode, inc_id, shift, cause, first_seen, trap, resolved, steps, tokens_in, tokens_out, seconds, stale_fix_applied)`
- `services(name, owner_shift)`

Every memory hit is joined with `fix_validity` before the model sees it, and labelled VALID, RETIRED or unverified.
When a remembered fix fails, the ledger retires it with the reason and later points `replaced_by` at the new memory.
From then on the Fixer refuses that fix and target for the same alert, unless a diagnostic command in the current page prints that exact target.
An old cause can come back (a rollback to the same healthy release, the same flag), and fresh evidence beats an old retirement.
Promoted memories are evicted from private memory and cannot be edited over MCP, so the ledger is how the team keeps memory honest.

### People in the loop

Two places where a person, not the model, has the last word:

- **Review before sharing.** A learned fix stays private to its shift until it is approved. `python -m nightshift.review --run-id prod list` shows the queue, `approve <memory_id> --by alice` lets the curator share it at the end of the night, `reject <memory_id> --reason "..."` retires it in the ledger so the Fixer refuses it. The benchmark sets `NIGHTSHIFT_REVIEW=auto` and records every approval as `auto (benchmark)`; production sets `manual`.
- **Fixes made by hand.** When the agent gives up and an engineer fixes the page, `python -m nightshift.handback --shift A --paged ... --fix F04 --target v5.7.0 ...` stores their note in Meko as that shift's memory, marked as written by a person. In the live runs this happened on 26 pages, and later repeat pages that started from one of those notes were resolved 28 times out of 30 (see Results).

### Use it on real alerts

`python -m nightshift.webhook --run-id prod --port 9095` is an Alertmanager receiver. For each firing alert it searches the shift's memory and the team's knowledge in Meko, joins every hit with the ledger, and answers (and posts to `SLACK_WEBHOOK_URL` if set). Example of the answer format (the case tested in `tests/test_review_webhook.py`):

```
*inventory-svc*: inventory-svc OOMKilled, 4 restarts in 10 min (memory match: high)
Seen before: F04 target v5.7.0, learned by shift A (human) on INC-30. Ledger: still valid.
Do not re-apply F05 target inventory-jvm-opts: INC-30: F05 applied, symptoms persisted; root cause changed.
```

It only suggests; it never touches production. Labels used: `service` (or `job`), `summary` annotation, optional `shift`.

### The Fixer guardrail

Both modes share one rule: a fix is only applied to a target that has appeared in diagnostic output or in a memory shown to the agent.
A fix that already failed in the same page is refused.
This stops a small model from guessing names, and it keeps the comparison fair: neither mode can win by luck.

## Run it

Offline needs no account (in-process Meko stand-in with the same interface, SQLite ledger):

```bash
make setup            # pip install, ollama pull qwen3:8b
make demo-offline     # 40 pages, both modes, about 45 minutes on an M-series Mac
make dashboard        # then open the URL it prints
make demo-quick       # first 12 pages (5 repeats), Night Shift only
make test             # guardrail, ledger, review queue and webhook tests, no model needed
results/run_v8_offline.sh   # every offline row of the tables above (18 runs)
results/run_v8_traps.sh     # the trap-heavy ledger comparison (6 runs)
results/run_v9_long.sh      # the 120-page comparison (9 runs)
results/run_v9b.sh          # the reverted triage change (9 runs)
```

Live run (two Meko members sharing one datapack, AMP Postgres ledger):

```bash
export MEKO_API_KEY=...            # member A, datapack owner = curator
export MEKO_B_API_KEY=...          # member B
export MEKO_EMAIL=... MEKO_PASSWORD=... MEKO_B_EMAIL=... MEKO_B_PASSWORD=...
export NIGHTSHIFT_DATAPACK_ID=$(python scripts/share_datapack.py nightshift-s1 | tail -1)
export NIGHTSHIFT_PG_HOST=... NIGHTSHIFT_PG_PORT=... NIGHTSHIFT_AMP_PASSWORD=...
make demo SEED=1
```

Each seed gets a fresh datapack because promoted memories cannot be deleted over MCP.

Replay any run in the browser: `dashboard/index.html?run=../results/published/<run_id>/events.jsonl`.

## Repository

- `nightshift/sim.py`: incident simulator, 15 causes, 40-page schedule, deterministic grading
- `nightshift/agents.py`: baseline agent, Night Shift agent, human handback, curator, librarian, Fixer guardrail
- `nightshift/review.py`: review queue (approve / reject before the curator shares)
- `nightshift/handback.py`: write down a fix made by hand
- `nightshift/webhook.py`: Alertmanager receiver (triage only)
- `tests/`: Fixer refusals, the fresh-evidence rule, ledger learn/retire/replace, human handback, review queue, webhook, ledger ablation
- `nightshift/meko.py`: minimal MCP client (streamable HTTP, no SDK) and the offline stand-in
- `nightshift/ledger.py`: Postgres / SQLite ledger
- `nightshift/run.py`: benchmark runner, writes `events.jsonl`, `incidents.json`, `summary.json`
- `dashboard/`: split-screen replay of a recorded run
- `runbook/runbook.md`: the team wiki both modes start from
- `scripts/`: architecture diagram, datapack sharing, results table (`results_md.py`), deck (`deck.py`), demo video (`record_demo.py` with a local Kokoro voice in `tts.py` and a generated music bed in `bgm.py`)

## Limits

- The benchmark incidents are simulated, so the scores are measured rather than judged; real pages have messier evidence. The webhook runs the same triage on real alerts, but the agent's diagnostics and fixes are only wired to the simulator.
- One 8B local model. A larger model was not tested.
- No Anthropic or OpenAI API is called anywhere. Meko does its own memory extraction on its side.

License: Apache 2.0
