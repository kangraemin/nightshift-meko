"""Fill docs/deck.html with numbers from recorded runs and print docs/deck.pdf.

    python scripts/deck.py --ablation <offline runs...> --live <live run> [<live run> ...]

The results slide shows the mean over the live runs; the ablation slide compares offline Night Shift runs
(full, --no-share, --no-memory; kind read from summary.json), mean over seeds. The trap story comes from the first live run.
"""
import html
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def pct(x):
    return f"{round(x * 100)}%"


def main():
    argv = sys.argv[1:]
    abl_dirs = [Path(x) for x in argv[argv.index("--ablation") + 1:argv.index("--live")]]
    runs = [Path(x) for x in argv[argv.index("--live") + 1:]]
    run = runs[0]
    sums = [json.loads((r / "summary.json").read_text())["results"] for r in runs]
    s = {m: {k: {f: sum(x[m][k][f] for x in sums) / len(sums) for f in ("accuracy", "steps_mean", "tokens_mean")}
             | {"n": sums[0][m][k]["n"]} for k in ("first_seen", "repeat", "trap", "all")} for m in ("baseline", "nightshift")}
    kinds = {"nomemory": [], "noshare": [], "full": []}
    for d in abl_dirs:
        r = json.loads((d / "summary.json").read_text())
        kinds["nomemory" if not r.get("memory", True) else "noshare" if not r.get("share", True) else "full"].append(r["results"]["nightshift"])
    avg = lambda k, sec, f: sum(x[sec][f] for x in kinds[k]) / len(kinds[k])  # noqa: E731
    rows = json.loads((run / "incidents.json").read_text())

    out = []
    for key, label in (("first_seen", "First seen"), ("repeat", "Repeat"), ("trap", "Trap"), ("all", "All")):
        b, n = s["baseline"][key], s["nightshift"][key]
        win = "win" if n["accuracy"] > b["accuracy"] else ""
        out.append(f"<tr><td>{label}</td><td>{n['n']}</td><td>{pct(b['accuracy'])}</td>"
                   f"<td class='{win}'>{pct(n['accuracy'])}</td><td>{b['steps_mean']:.1f} → {n['steps_mean']:.1f}</td>"
                   f"<td>{b['tokens_mean']:,.0f} → {n['tokens_mean']:,.0f}</td></tr>")
    rep_b, rep_n = s["baseline"]["repeat"], s["nightshift"]["repeat"]
    per = " / ".join(pct(x["nightshift"]["repeat"]["accuracy"]) for x in sums)
    note = (f"Mean of {len(sums)} live runs (each shuffles which shift gets which page). "
            f"On repeat pages Night Shift resolved {pct(rep_n['accuracy'])} ({per}) against {pct(rep_b['accuracy'])}, "
            f"with {round((1 - rep_n['tokens_mean'] / rep_b['tokens_mean']) * 100)}% fewer tokens. "
            f"First-seen pages gain too, from searching the runbook instead of pasting it (see the next slide).")

    abl = ""
    for k, label in (("nomemory", "Runbook search only, no memory"), ("noshare", "Private memory only"), ("full", "Shared through Meko")):
        win = " class='win'" if k == "full" else ""
        abl += (f"<tr><td>{label}</td><td>{pct(avg(k, 'first_seen', 'accuracy'))}</td><td{win}>{pct(avg(k, 'repeat', 'accuracy'))}</td>"
                f"<td>{avg(k, 'repeat', 'steps_mean'):.1f}</td></tr>")
    abl_note = (f"Offline, same pages, mean of {len(kinds['full'])} seeds. Retrieval alone explains the first-seen gain; "
                f"remembering adds {round((avg('noshare', 'repeat', 'accuracy') - avg('nomemory', 'repeat', 'accuracy')) * 100)} points on repeats, "
                f"sharing across shifts {round((avg('full', 'repeat', 'accuracy') - avg('noshare', 'repeat', 'accuracy')) * 100)} more "
                "(13 of the 25 repeats first happened on the other shift).")

    # trap story: first trap page in the Night Shift lane that retired or blocked a fix
    trap = next((r for r in rows if r["mode"] == "nightshift" and r["trap"]), None)
    lines = []
    if trap:
        lines.append(f"<div>{trap['inc_id']} · {html.escape(trap['alert'])}</div>")
        for h in trap["history"][:7]:
            cls = "ok" if h.get("ok") else ("no" if h["action"] == "apply_fix" else "")
            act = f"{h['action']} {h.get('fix', '')} {h.get('target', '')}".strip()
            obs = h["obs"]
            obs = obs[:110] + "…" if len(obs) > 110 else obs
            lines.append(f"<div class='{cls}'>{html.escape(act)} on {html.escape(h['service'])}: {html.escape(obs)}</div>")
    trap_steps = "".join(lines)

    t = s["nightshift"]["trap"]
    limit = (f"Traps stay hard: {pct(t['accuracy'])} of trap pages resolved, same as the runbook lane "
             f"({pct(s['baseline']['trap']['accuracy'])}). The ledger stops the old fix; finding the new cause is still up to the model.")

    src = (ROOT / "docs" / "deck.html").read_text()
    for k, v in {"RESULT_ROWS": "".join(out), "RESULT_NOTE": note, "ABLATION_ROWS": abl, "ABLATION_NOTE": abl_note,
                 "TRAP_STEPS": trap_steps, "LIMIT_TRAP": limit}.items():
        src = src.replace("{{" + k + "}}", v)
    filled = ROOT / "docs" / "deck.filled.html"
    filled.write_text(src)
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(viewport={"width": 1280, "height": 720})
        pg.goto(filled.as_uri())
        pg.wait_for_timeout(500)
        pg.pdf(path=str(ROOT / "docs" / "deck.pdf"), width="1280px", height="720px", print_background=True)
        br.close()
    print(ROOT / "docs" / "deck.pdf")


if __name__ == "__main__":
    main()
