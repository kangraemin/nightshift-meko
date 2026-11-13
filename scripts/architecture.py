"""Draw docs/architecture.svg from code (no image tools). python scripts/architecture.py"""
from pathlib import Path

W, H = 1200, 700
INK, MUTED, LINE, ACC, BG = "#16181d", "#5b6270", "#c9cdd4", "#0b7a53", "#ffffff"
out = []


def box(x, y, w, h, title, lines=(), strong=False):
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{BG}" '
               f'stroke="{ACC if strong else INK}" stroke-width="{2 if strong else 1.5}"/>')
    out.append(f'<text x="{x + 14}" y="{y + 26}" font-size="16" font-weight="700" fill="{INK}">{title}</text>')
    for i, t in enumerate(lines):
        out.append(f'<text x="{x + 14}" y="{y + 50 + i * 20}" font-size="13.5" fill="{MUTED}">{t}</text>')


def group(x, y, w, h, title):
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="none" stroke="{LINE}" '
               f'stroke-width="1.5" stroke-dasharray="6 5"/>')
    out.append(f'<text x="{x + 14}" y="{y - 10}" font-size="15" font-weight="700" fill="{INK}">{title}</text>')


def arrow(pts, label="", lx=None, ly=None, color=INK, anchor="start"):
    d = "M" + " L".join(f"{x},{y}" for x, y in pts)
    mk = "a" if color == INK else "g"
    out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6" marker-end="url(#{mk})"/>')
    if label:
        if lx is None:
            (x1, y1), (x2, y2) = pts[0], pts[1]
            lx, ly = (x1 + x2) / 2 + 6, (y1 + y2) / 2 - 6
        out.append(f'<text x="{lx}" y="{ly}" font-size="13" fill="{color}" text-anchor="{anchor}">{label}</text>')


# left: the incident world
group(30, 60, 300, 600, "On-call world (local)")
box(50, 80, 260, 110, "Pager simulator", ["40 pages over 20 nights", "15 root causes, 3 traps", "fix = id + target, checked"])
box(50, 240, 260, 120, "Shift A agent", ["qwen3:8b via Ollama", "triage, investigate, fix", "Meko key of member A"], strong=True)
box(50, 400, 260, 120, "Shift B agent", ["same model, other shift", "never talks to A directly", "Meko key of member B"], strong=True)
box(50, 560, 260, 80, "Fixer guardrail", ["target must appear in evidence"])

# middle: Meko
group(400, 60, 420, 600, "Meko Context Engine (MCP, one shared datapack)")
box(420, 80, 380, 90, "Conversations", ["one per page: reasoning, plan, decision trace", "track_tokens for every model call"])
box(420, 190, 380, 90, "Private memory", ["memory_add after a verified fix", "context_search at triage"])
box(420, 300, 380, 100, "Collective knowledge", ["runbook.md uploaded once", "promoted fixes, found with", "knowledgebase_search by both shifts"], strong=True)
box(420, 420, 380, 80, "Artifacts", ["postmortem per resolved page"])
box(420, 520, 380, 120, "Self-learning", ["selflearning_status / shortfalls", "Librarian answers weak searches", "selflearning_verify replays them"])

# right: Postgres + curator
group(890, 60, 280, 600, "Team side")
box(910, 80, 240, 170, "Postgres fix ledger", ["AMP Postgres (SQLite offline)", "fix_validity: valid / retired", "replaced_by, retired_reason", "incidents: steps, tokens,", "accuracy per mode"], strong=True)
box(910, 300, 240, 120, "Curator", ["datapack owner", "end of each night:", "promote valid fixes"])
box(910, 470, 240, 120, "Baseline agent", ["same model, same pages", "whole runbook in prompt", "no memory"])

# arrows
arrow([(180, 190), (180, 240)], "page", 188, 220)
arrow([(310, 290), (420, 235)])
arrow([(310, 330), (420, 350)])
out.append(f'<text x="318" y="316" font-size="13" fill="{INK}">1 search</text>')
arrow([(310, 420), (420, 385)])
arrow([(310, 280), (360, 280), (360, 125), (420, 125)], "3 trace", 370, 160)
arrow([(800, 235), (850, 235), (850, 150), (910, 150)], "2 still valid?", 855, 140)
arrow([(1030, 250), (1030, 300)], "valid, unpromoted", 1038, 280)
arrow([(910, 360), (800, 360)], "4 promote", 835, 352, color=ACC)
arrow([(420, 600), (345, 600), (345, 508), (310, 508)], "gap notes", 350, 594)
arrow([(50, 600), (40, 600), (40, 135), (50, 135)])
arrow([(180, 520), (180, 560)])
arrow([(310, 470), (420, 470)], "5 postmortem", 322, 462)

svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
       f'font-family="-apple-system, Segoe UI, Roboto, sans-serif">'
       f'<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
       f'<path d="M0,0 L10,5 L0,10 z" fill="{INK}"/></marker>'
       f'<marker id="g" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
       f'<path d="M0,0 L10,5 L0,10 z" fill="{ACC}"/></marker></defs>'
       f'<rect width="{W}" height="{H}" fill="{BG}"/>' + "".join(out) + "</svg>")
p = Path(__file__).resolve().parent.parent / "docs" / "architecture.svg"
p.write_text(svg)
print(p)
