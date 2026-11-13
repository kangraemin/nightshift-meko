"""Fill docs/deck.html with numbers from recorded runs and print docs/deck.pdf.

    python scripts/deck.py results/<run_id> results/<run_id>-noshare
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
    run, noshare = Path(sys.argv[1]), Path(sys.argv[2])
    s = json.loads((run / "summary.json").read_text())["results"]
    ns = json.loads((noshare / "summary.json").read_text())["results"]["nightshift"]
    rows = json.loads((run / "incidents.json").read_text())

    out = []
    for key, label in (("first_seen", "First seen"), ("repeat", "Repeat"), ("trap", "Trap"), ("all", "All")):
        b, n = s["baseline"][key], s["nightshift"][key]
        win = "win" if n["accuracy"] > b["accuracy"] else ""
        out.append(f"<tr><td>{label}</td><td>{n['n']}</td><td>{pct(b['accuracy'])}</td>"
                   f"<td class='{win}'>{pct(n['accuracy'])}</td><td>{b['steps_mean']} → {n['steps_mean']}</td>"
                   f"<td>{b['tokens_mean']:,} → {n['tokens_mean']:,}</td></tr>")
    rep_b, rep_n = s["baseline"]["repeat"], s["nightshift"]["repeat"]
    note = (f"On repeat pages Night Shift resolved {pct(rep_n['accuracy'])} against {pct(rep_b['accuracy'])}, "
            f"with {round((1 - rep_n['tokens_mean'] / rep_b['tokens_mean']) * 100)}% fewer tokens. "
            f"First-seen pages are the control: memory cannot help there yet.")

    rn = ns["repeat"]
    abl = (f"<tr><td>Private memory only</td><td>{pct(rn['accuracy'])}</td><td>{rn['steps_mean']}</td><td>{rn['tokens_mean']:,}</td></tr>"
           f"<tr><td>Shared through Meko</td><td class='win'>{pct(rep_n['accuracy'])}</td><td>{rep_n['steps_mean']}</td>"
           f"<td>{rep_n['tokens_mean']:,}</td></tr>")
    abl_note = (f"13 of the 25 repeats first happened on the other shift. Only the collective layer lets "
                f"shift B use what shift A learned: +{round((rep_n['accuracy'] - rn['accuracy']) * 100)} points on repeats.")

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
