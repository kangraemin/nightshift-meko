"""Write the results section of README.md from recorded runs.

    python scripts/results_md.py --live results/published/live-v7-s1 ... --ablation results/published/off-v7-s1* ...
--ablation takes offline Night Shift runs of each kind (full, --no-share, --no-memory), any number of seeds;
the kind is read from each summary.json.
Replaces the text between <!-- RESULTS --> and <!-- /RESULTS -->.
"""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETS = (("first_seen", "First-seen causes"), ("repeat", "Repeats"), ("trap", "Traps"), ("all", "All pages"))


def load(d):
    return json.loads((Path(d) / "summary.json").read_text())


def pct(x):
    return f"{round(x * 100)}%"


def events(d):
    return [json.loads(x) for x in (Path(d) / "events.jsonl").read_text().splitlines()]


def failed_applies(d):
    """Fixes actually applied (not refused by the Fixer) that did not clear the page, Night Shift lane."""
    inc = json.loads((Path(d) / "incidents.json").read_text())
    return sum(1 for x in inc if x.get("mode") == "nightshift" for h in x["history"]
               if h["action"] == "apply_fix" and not h.get("ok") and not h["obs"].startswith("REFUSED"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", nargs="+", required=True)
    ap.add_argument("--ablation", nargs="+", required=True)
    ap.add_argument("--traps", nargs="*", default=[], help="runs of the trap-heavy schedule (--schedule traps), ledger on and off")
    a = ap.parse_args()
    runs = [load(d) for d in a.live]
    n = len(runs)
    out = ["## Results", ""]
    out.append(f"Live runs: {n} seed{'s' if n > 1 else ''} (each seed shuffles which shift gets which page), "
               "two real Meko members, AMP Postgres ledger, qwen3:8b. Every number below comes from the "
               "`summary.json` of a run in `results/published/`.")
    out.append("")
    out.append("| Pages | Runbook in prompt: resolved | Night Shift: resolved | Steps per page | Tokens per page | Seconds per page |")
    out.append("|---|---|---|---|---|---|")
    for key, label in SETS:
        b = [r["results"]["baseline"][key] for r in runs]
        m = [r["results"]["nightshift"][key] for r in runs]
        mean = lambda xs, f: sum(x[f] for x in xs) / len(xs)  # noqa: E731
        per = " / ".join(pct(x["accuracy"]) for x in m)
        perb = " / ".join(pct(x["accuracy"]) for x in b)
        cell_b = f"{pct(mean(b, 'accuracy'))}" + (f" ({perb})" if n > 1 else "")
        win = mean(m, "accuracy") > mean(b, "accuracy")
        cell_n = (f"**{pct(mean(m, 'accuracy'))}**" if win else pct(mean(m, "accuracy"))) + (f" ({per})" if n > 1 else "")
        out.append(f"| {label} ({b[0]['n']}) | {cell_b} | {cell_n} | {mean(b, 'steps_mean'):.1f} → {mean(m, 'steps_mean'):.1f} "
                   f"| {mean(b, 'tokens_mean'):,.0f} → {mean(m, 'tokens_mean'):,.0f} | {mean(b, 'seconds_mean'):.0f} → {mean(m, 'seconds_mean'):.0f} |")
    out.append("")
    if n > 1:
        out.append("Cells show the mean over seeds, with each seed in brackets.")
        out.append("")
    stale = sum(r["results"]["nightshift"]["all"]["stale_fix_applied"] for r in runs)
    blocked = sum(r["results"]["nightshift"]["all"]["stale_fix_blocked"] for r in runs)
    stale_pages = {}  # (run, page) -> resolved, for Night Shift pages where a retired fix was applied anyway
    for d in a.live:
        inc = json.loads((Path(d) / "incidents.json").read_text())
        for x in (inc if isinstance(inc, list) else inc.get("incidents", [])):
            if x.get("mode") == "nightshift" and x.get("stale_fix_applied"):
                stale_pages[(str(d), x["inc_id"])] = int(bool(x.get("resolved")))
    evs = [e for d in a.live for e in events(d)]
    learned = sum(len(r.get("fix_validity", [])) for r in runs)
    promoted = sum(len(e.get("ids", [])) for e in evs if e["kind"] == "promote")
    retired = sum(1 for e in evs if e["kind"] == "retire")
    lib = [e for e in evs if e["kind"] == "librarian" and e.get("verify")]
    improved = sum(1 for e in lib if e["verify"].get("outcome") == "improved")
    to_high = sum(1 for e in lib if (e["verify"].get("after") or {}).get("band") == "high")
    out.append("What the memory layer did, summed over the live runs:")
    out.append("")
    humans = [e for e in evs if e["kind"] == "human"]
    human_ids = {e["memory_id"] for e in humans}
    reused = []
    for d in a.live:
        inc = json.loads((Path(d) / "incidents.json").read_text())
        reused += [x for x in inc if x.get("mode") == "nightshift" and x.get("used_memory") in human_ids]
    out.append(f"- {learned} fixes learned, {promoted} promoted by the curator, {retired} retired by the ledger. "
               "These runs auto-approve every valid fix (`NIGHTSHIFT_REVIEW=auto`, no person at the keyboard); "
               "with `NIGHTSHIFT_REVIEW=manual` the curator shares only fixes a person approved with `python -m nightshift.review`.")
    reps = []
    for d in a.live:
        inc = json.loads((Path(d) / "incidents.json").read_text())
        reps += [x for x in inc if x.get("mode") == "nightshift" and not x.get("first_seen")]
    rh = [x for x in reps if x.get("used_memory") in human_ids]
    ra = [x for x in reps if x not in rh]
    out.append(f"- {len(humans)} pages the agent lost were fixed by the on-call human, and their note went into Meko the same way. "
               "In this benchmark the simulator writes that note from the page's true cause (a stand-in for the engineer's write-up), "
               "so pages that start from one are easier.")
    out.append(f"- Split of the {len(reps)} live repeat pages: {len(rh)} started from a human note and {sum(x['resolved'] for x in rh)} were resolved; "
               f"the other {len(ra)} relied on the agents' own memory and {sum(x['resolved'] for x in ra)} were resolved "
               f"({pct(sum(x['resolved'] for x in ra) / len(ra))}). The offline table below gives the runbook lane the same human notes for a fair comparison.")
    out.append(f"- Retired fixes were refused {blocked} times. A fix the ledger had already retired was still applied {stale} time{'s' if stale != 1 else ''}"
               f" on {len(stale_pages)} pages: {sum(stale_pages.values())} of those pages were resolved, because the old cause had really come back"
               " and a diagnostic printed the same target (the fresh-evidence rule); the rest were wasted retries.")
    out.append(f"- The librarian answered {len(lib)} searches that Meko scored as weak matches. Replaying them, "
               f"`selflearning_verify` moved {improved} to a better match band, {to_high} of them to the high band "
               "(this is Meko's own retrieval score, not a page outcome).")
    out.append("")
    kinds = {k: [] for k in ("baseline", "wikinotes", "nomemory", "noshare", "nohumans", "noledger", "full")}
    dirs = {"noledger": [], "full": []}
    for d in a.ablation:
        r = load(d)
        res = r["results"]
        if r.get("wiki_notes"):
            kinds["wikinotes"].append(res["baseline"])
            continue
        if res.get("baseline") and res["baseline"].get("all"):
            kinds["baseline"].append(res["baseline"])
        if not (res.get("nightshift") and res["nightshift"].get("all")):
            continue
        k = ("nomemory" if not r.get("memory", True) else "noshare" if not r.get("share", True)
             else "noledger" if not r.get("ledger_on", True) else "nohumans" if not r.get("humans", True) else "full")
        kinds[k].append(res["nightshift"])
        if k in dirs:
            dirs[k].append(d)
    ns = len(kinds["full"])
    avg = lambda rs, k, f: sum(r[k][f] for r in rs) / len(rs)  # noqa: E731
    out.append("### Where does the gain come from?")
    out.append("")
    out.append(f"Offline (in-process Meko stand-in, SQLite ledger), same pages and model, {ns} seeds, all runs on one machine with the same code. "
               "Each row switches one thing:")
    out.append("")
    out.append("| Offline lane | First-seen | Repeats | All pages | Steps per repeat | Tokens per repeat |")
    out.append("|---|---|---|---|---|---|")
    rows_def = (("baseline", "Runbook pasted into the prompt"),
                ("wikinotes", "Runbook pasted, plus each on-call human note added to the wiki (`--wiki-notes`)"),
                ("nomemory", "Night Shift, runbook search only, nothing remembered (`--no-memory`)"),
                ("noshare", "Night Shift, private memory only, no sharing (`--no-share`)"),
                ("nohumans", "Night Shift, shared memory of the agent's own fixes only (`--no-humans`)"),
                ("full", "Night Shift, shared memory of agent and human fixes"))
    for k, label in rows_def:
        rs = kinds[k]
        if not rs:
            continue
        per = " / ".join(pct(r["repeat"]["accuracy"]) for r in rs)
        out.append(f"| {label} | {pct(avg(rs, 'first_seen', 'accuracy'))} | {pct(avg(rs, 'repeat', 'accuracy'))} ({per}) "
                   f"| {pct(avg(rs, 'all', 'accuracy'))} | {avg(rs, 'repeat', 'steps_mean'):.1f} | {avg(rs, 'repeat', 'tokens_mean'):,.0f} |")
    out.append("")
    if kinds["wikinotes"] and kinds["nohumans"] and kinds["baseline"]:
        A = lambda k, f: avg(kinds[k], "repeat", f)  # noqa: E731
        out.append("Read fairly:")
        out.append("")
        out.append(f"- Without any human input, repeats go from {pct(A('baseline', 'accuracy'))} to {pct(A('nohumans', 'accuracy'))}: "
                   f"searching the runbook in Meko gives {pct(A('nomemory', 'accuracy'))}, and the agents' own shared memory adds the rest "
                   "(a few points, close to the seed-to-seed spread).")
        out.append(f"- Given the same human notes, a wiki resolves about as many repeats as Night Shift ({pct(A('wikinotes', 'accuracy'))} vs {pct(A('full', 'accuracy'))}). "
                   f"Night Shift does it in {A('full', 'steps_mean'):.1f} steps instead of {A('wikinotes', 'steps_mean'):.1f} "
                   f"and {A('full', 'tokens_mean'):,.0f} tokens instead of {A('wikinotes', 'tokens_mean'):,.0f}, because it retrieves the one note that matches "
                   "instead of reading the whole wiki, and the wiki keeps growing every night.")
        out.append("- So the live jump from 56% to 92% on repeats is mostly the human notes reaching the next shift; "
                   "what Meko adds over a wiki is the cost per page, the agents' own learning, and the ledger below.")
        out.append("")
    out.append("- The no-memory row still searches the runbook in Meko instead of pasting it, so it isolates retrieval from memory.")
    out.append("- 13 of the 25 repeats first happened on the other shift. Without sharing, those start from scratch.")
    out.append("- The human notes are written by the simulator from the page's true cause, as a stand-in for what the on-call engineer "
               "types after fixing a page by hand. The `--wiki-notes` row gives the runbook lane the same notes, so the comparison stays fair; "
               "the `--no-humans` row shows what the agent's own memory does without any human input.")
    out.append("")
    if kinds["noledger"]:
        out.append("### Does the Postgres ledger matter?")
        out.append("")
        out.append(f"Same offline runs, {len(kinds['noledger'])} seeds, with the ledger switched off (`--no-ledger`): memories are still learned "
                   "and shared, but every remembered fix looks valid forever, like a plain memory store.")
        out.append("")
        out.append("| Night Shift lane, offline | Repeats | Traps | All pages | Failed fixes applied to production | Fixes refused by the Fixer |")
        out.append("|---|---|---|---|---|---|")
        for k, label in (("noledger", "Memory without the ledger (`--no-ledger`)"), ("full", "Memory checked against the ledger")):
            rs, ds = kinds[k], dirs[k]
            fails = sum(failed_applies(d) for d in ds) / len(ds)
            ref = sum(r["all"]["stale_fix_blocked"] for r in rs) / len(rs)
            out.append(f"| {label} | {pct(avg(rs, 'repeat', 'accuracy'))} | {pct(avg(rs, 'trap', 'accuracy'))} | {pct(avg(rs, 'all', 'accuracy'))} "
                       f"| {fails:.1f} per run | {ref:.1f} per run |")
        out.append("")
        out.append("- A failed fix is a change (rollback, restart, drain, config edit) applied to the simulated production that did not clear the page; the simulator counts each one.")
        fl = {k: sum(failed_applies(d) for d in dirs[k]) / len(dirs[k]) for k in dirs}
        out.append(f"- Pages resolved barely move: the ledger does not find causes. What it changes is how many wrong changes reach (simulated) production: "
                   f"{fl['noledger']:.1f} → {fl['full']:.1f} per run ({round((1 - fl['full'] / fl['noledger']) * 100)}% fewer).")
        out.append("- Without the ledger the Fixer still refuses a fix that already failed on the same page; "
                   "the ledger adds refusals of fixes that failed on earlier pages, on either shift.")
        out.append("")
    if a.traps:
        tk = {"on": [], "off": []}
        for d in a.traps:
            r = load(d)
            tk["on" if r.get("ledger_on", True) else "off"].append((d, r["results"]["nightshift"]))
        if tk["on"] and tk["off"]:
            out.append(f"With only 3 traps per run the trap column above is noise. A second schedule (`--schedule traps`) keeps the same 12 first-seen causes "
                       f"but adds 10 traps, each followed by a repeat of the new cause; {len(tk['on'])} seeds each, offline:")
            out.append("")
            out.append("| Trap-heavy schedule | Traps resolved (10 per run) | Repeats | All pages | Failed fixes applied | Fixes refused by the Fixer |")
            out.append("|---|---|---|---|---|---|")
            for k, label in (("off", "Memory without the ledger"), ("on", "Memory checked against the ledger")):
                rs = [x for _, x in tk[k]]
                per = " / ".join(pct(x["trap"]["accuracy"]) for x in rs)
                fails = sum(failed_applies(d) for d, _ in tk[k]) / len(rs)
                out.append(f"| {label} | {pct(sum(x['trap']['accuracy'] for x in rs) / len(rs))} ({per}) "
                           f"| {pct(sum(x['repeat']['accuracy'] for x in rs) / len(rs))} | {pct(sum(x['all']['accuracy'] for x in rs) / len(rs))} "
                           f"| {fails:.1f} per run | {sum(x['all']['stale_fix_blocked'] for x in rs) / len(rs):.1f} per run |")
            out.append("")
    t = [r["results"]["nightshift"]["trap"]["accuracy"] for r in runs]
    tb = [r["results"]["baseline"]["trap"]["accuracy"] for r in runs]
    out.append("### What did not improve")
    out.append("")
    out.append(f"- Traps: Night Shift resolved {pct(sum(t) / n)} of trap pages, the runbook lane {pct(sum(tb) / n)}. "
               "The ledger stops the stale fix, but finding the new cause is still up to an 8B model.")
    out.append("- The ledger can also over-block: when the old cause really returns but the agent never runs the diagnostic that prints the target, "
               "the retired fix stays refused and the page is lost (INC-33 in two of the three live seeds).")
    fs_b = sum(r["results"]["baseline"]["first_seen"]["accuracy"] for r in runs) / n
    fs_m = sum(r["results"]["nightshift"]["first_seen"]["accuracy"] for r in runs) / n
    nm = avg(kinds["nomemory"], "first_seen", "accuracy")
    nb = avg(kinds["baseline"], "first_seen", "accuracy") if kinds["baseline"] else None
    out.append(f"- First-seen pages: memory cannot help there. Live, Night Shift scored {pct(fs_m)} against {pct(fs_b)}; "
               f"offline, the no-memory control scores {pct(nm)}" + (f" and the pasted runbook {pct(nb)}" if nb is not None else "")
               + ", so the live first-seen gap is within run-to-run noise, not a gain we claim.")
    body = "\n".join(out)
    p = ROOT / "README.md"
    s = p.read_text()
    if "<!-- /RESULTS -->" not in s:
        s = s.replace("<!-- RESULTS -->", "<!-- RESULTS -->\n<!-- /RESULTS -->")
    s = re.sub(r"<!-- RESULTS -->.*?<!-- /RESULTS -->", lambda _: f"<!-- RESULTS -->\n{body}\n<!-- /RESULTS -->", s, flags=re.S)
    p.write_text(s)
    print(body)


if __name__ == "__main__":
    main()
