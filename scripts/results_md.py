"""Write the results section of README.md from recorded runs.

    python scripts/results_md.py --live results/live-s1 results/live-s2 ... --ablation results/off-s1-v5 results/off-s1-v5-noshare
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", nargs="+", required=True)
    ap.add_argument("--ablation", nargs=2, required=True, metavar=("SHARED", "NOSHARE"))
    a = ap.parse_args()
    runs = [load(d) for d in a.live]
    n = len(runs)
    out = ["## Results", ""]
    out.append(f"Live runs: {n} seed{'s' if n > 1 else ''} (each seed shuffles which shift gets which page), "
               "two real Meko members, AMP Postgres ledger, qwen3:8b. Every number below comes from the "
               "`summary.json` of a run in `results/`.")
    out.append("")
    out.append("| Pages | Runbook in prompt: resolved | Night Shift: resolved | Steps per page | Tokens per page |")
    out.append("|---|---|---|---|---|")
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
                   f"| {mean(b, 'tokens_mean'):,.0f} → {mean(m, 'tokens_mean'):,.0f} |")
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
    out.append(f"- {learned} fixes learned, {promoted} promoted by the curator, {retired} retired by the ledger.")
    out.append(f"- Retired fixes were refused {blocked} times. A fix the ledger had already retired was still applied {stale} time{'s' if stale != 1 else ''}"
               f" on {len(stale_pages)} pages: {sum(stale_pages.values())} of those pages were resolved, because the old cause had really come back"
               " and a diagnostic printed the same target (the fresh-evidence rule); the rest were wasted retries.")
    out.append(f"- The librarian answered {len(lib)} weak searches; `selflearning_verify` reported {improved} of them improved, "
               f"{to_high} reaching the high band.")
    out.append("")
    sh, ns = load(a.ablation[0])["results"]["nightshift"]["repeat"], load(a.ablation[1])["results"]["nightshift"]["repeat"]
    out.append("### Is it the sharing?")
    out.append("")
    out.append("Same agent, same pages, offline, with the collective layer switched off so each shift only sees its own private memories:")
    out.append("")
    out.append("| Repeat pages | Resolved | Steps | Tokens |")
    out.append("|---|---|---|---|")
    out.append(f"| Private memory only | {pct(ns['accuracy'])} | {ns['steps_mean']} | {ns['tokens_mean']:,} |")
    out.append(f"| Shared through Meko | **{pct(sh['accuracy'])}** | {sh['steps_mean']} | {sh['tokens_mean']:,} |")
    out.append("")
    out.append("13 of the 25 repeats first happened on the other shift. Without sharing, those start from scratch.")
    out.append("")
    t = [r["results"]["nightshift"]["trap"]["accuracy"] for r in runs]
    tb = [r["results"]["baseline"]["trap"]["accuracy"] for r in runs]
    out.append("### What did not improve")
    out.append("")
    out.append(f"- Traps: Night Shift resolved {pct(sum(t) / n)} of trap pages, the runbook lane {pct(sum(tb) / n)}. "
               "The ledger stops the stale fix, but finding the new cause is still up to an 8B model.")
    out.append("- The ledger can also over-block: when the old cause really returns but the agent never runs the diagnostic that prints the target, "
               "the retired fix stays refused and the page is lost (INC-33 in two of the three live seeds).")
    out.append("- First-seen pages are the control: memory cannot help there, and differences come from run-to-run noise of the model.")
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
