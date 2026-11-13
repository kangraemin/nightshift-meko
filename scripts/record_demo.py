"""Record the narrated demo video: intro cards, dashboard replay of a recorded run, Meko portal tour, results.

    python scripts/record_demo.py results/<run_id> results/<run_id>-noshare  -> docs/video/nightshift-demo.mp4
Needs MEKO_EMAIL / MEKO_PASSWORD (portal tour), ffmpeg, and a Python with kokoro-onnx for the voice
(KOKORO_PY, default ~/.cache/kk/v/bin/python; model files in KOKORO_DIR). No paid API is called.
Every step on screen is timed to its narration line; captions show the same words.
"""
import http.server
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "video"
VO = OUT / "voice"
W, H = 1280, 720
PORT = 8793
GAP = 0.35  # pause after each line

CAP_CSS = """#nscap{position:fixed;left:0;right:0;bottom:30px;text-align:center;z-index:99999;pointer-events:none}
#nscap span{display:inline-block;max-width:1100px;background:rgba(22,24,29,.88);color:#fff;padding:10px 20px;
border-radius:8px;font:500 24px/1.4 -apple-system,'Segoe UI',Roboto,sans-serif}#nscap span:empty{display:none}"""
CAP_JS = """(css)=>{if(document.getElementById('nscap'))return;const s=document.createElement('style');s.textContent=css;
document.head.appendChild(s);const d=document.createElement('div');d.id='nscap';d.innerHTML='<span></span>';document.body.appendChild(d);
window.nscap=t=>{document.querySelector('#nscap span').textContent=t||''};}"""
# slow zoom on a page that would otherwise sit still while a line is spoken
DRIFT_JS = """(s)=>{for(const e of document.querySelectorAll('body > *:not(#nscap):not(script):not(style)')){
e.style.transformOrigin=`50% ${window.scrollY-e.offsetTop}px`;e.style.transition='none';e.style.transform='scale(1)';void e.offsetWidth;
e.style.transition=`transform ${s}s linear`;e.style.transform='scale(1.05)';}}"""

INTRO = [
    ("title", None, "i_title", "Night Shift. On-call agents that never solve the same incident twice, even when the other shift solved it."),
    ("problem", "line", "i_p1", "Shift A fixes the 2 a.m. page. The fix lives in their own notes."),
    ("problem", "line", "i_p2", "The next night, shift B gets the same page, and starts from the wiki again."),
    ("problem", "line", "i_p3", "And sometimes a remembered fix stops working, but people keep applying it."),
    ("arch", None, "i_a1", "Night Shift runs the two shifts as two Meko members who share one datapack."),
    ("arch", None, "i_a2", "Each page is traced in a Meko conversation. A verified fix becomes a private memory and a postmortem."),
    ("arch", None, "i_a3", "At the end of the night, the curator promotes those fixes to the team's collective knowledge."),
    ("arch", None, "i_a4", "A Postgres ledger is joined with every memory hit, and records whether each remembered fix still works."),
    ("arch", None, "i_a5", "Now, the same 40 pages, answered twice by the same local model."),
]
PORTAL = [  # (path, [(key, line, scroll px)])
    ("", [("p_1", "The same run in the Meko portal: one datapack for the whole team.", 80),
          ("p_2", "Context efficiency: tokens sent per query, against a full context baseline.", 250),
          ("p_3", "Collective learning: conversations become memories, and promoted memories become shared knowledge.", "Collective")]),
    ("/members", [("p_4", "Two members. Shift A owns the datapack and curates. Shift B is a contributor, so only A can promote.", 0)]),
    ("/knowledge", [("p_5", "Shared knowledge: the team runbook, plus the fixes the curator promoted.", 300)]),
    ("/artifacts", [("p_6", "Each newly learned fix also left a postmortem artifact, from both shifts.", 200)]),
    ("?tab=self-learning&step=summary", [("p_7", "Self-learning: Meko scores how well each search matched. The librarian answers the weak ones and replays them.", 300)]),
]
# the two Meko members are the author's own accounts: show them as shift A and shift B
# Replace the real accounts on screen with neutral labels. Pairs come from the environment, never from this file:
# MEKO_EMAIL / MEKO_B_EMAIL, plus MASK_PAIRS="Real Name B=Shift B;Real Name=Shift A;AB=SA" (exact short text like avatar initials).
MASK_JS = """(R)=>{const f=()=>{const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
let n;while((n=w.nextNode())){let t=n.nodeValue,o=t;for(const[a,b]of R){if(a.length<=3){if(t.trim()===a)t=b;}else t=t.split(a).join(b);}
if(t!==o)n.nodeValue=t;}};f();
if(!window.nsMask){window.nsMask=new MutationObserver(f);window.nsMask.observe(document.body,{subtree:true,childList:true,characterData:true});}}"""


def mask_pairs():
    pairs = [(os.environ.get("MEKO_B_EMAIL", ""), "shift-b@team"), (os.environ.get("MEKO_EMAIL", ""), "shift-a@team")]
    for item in os.environ.get("MASK_PAIRS", "").split(";"):
        if "=" in item:
            a, b = item.split("=", 1)
            pairs.append((a.strip(), b.strip()))
    return [p for p in pairs if p[0]]
SCROLL_TO_TEXT_JS = """(t)=>{const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let n;
while((n=w.nextNode())){if(n.nodeValue.includes(t)){let e=n.parentElement;for(let i=0;i<3&&e.parentElement;i++)e=e.parentElement;
e.scrollIntoView({behavior:'smooth',block:'center'});return;}}}"""
DASH_END = ("d_end", "Totals for all 40 pages, and below them what the team learned, promoted, and retired in the ledger.")


def caption(pg, text, top=False):
    pg.evaluate(CAP_JS, CAP_CSS)
    pg.evaluate("([t,top])=>{const c=document.getElementById('nscap');c.style.top=top?'10px':'auto';"
                "c.style.bottom=top?'auto':'30px';window.nscap(t)}", [text, top])


def serve():
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(ROOT), **k)  # noqa: E731
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", PORT), handler)
    httpd.RequestHandlerClass.log_message = lambda *a: None
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class Seg:
    """One recorded segment: remembers when each narration line started, relative to the video start."""

    def __init__(self, p, name, durs, storage_state=None, browser=None):
        self.name, self.durs, self.lines, self.trim = name, durs, [], 0.0
        self.br = browser or p.chromium.launch()
        self.ctx = self.br.new_context(viewport={"width": W, "height": H}, storage_state=storage_state,
                                       record_video_dir=str(OUT / "raw" / name), record_video_size={"width": W, "height": H})
        self.t0 = time.monotonic()
        self.pg = self.ctx.new_page()

    def ready(self):
        """Content is on screen: cut everything recorded before this moment."""
        self.trim = time.monotonic() - self.t0

    def say(self, key):
        self.lines.append((key, time.monotonic() - self.t0))
        return self.durs[key]

    def close(self):
        path = self.pg.video.path()
        self.ctx.close()
        self.br.close()
        dst = OUT / "raw" / f"{self.name}.webm"
        shutil.move(path, dst)
        return dst, [(k, t - self.trim) for k, t in self.lines], self.trim


def cards_intro(p, durs, fills):
    src = (OUT / "cards.html").read_text()
    for k, v in fills.items():
        src = src.replace("{{" + k + "}}", v)
    (OUT / "cards.filled.html").write_text(src)
    s = Seg(p, "intro", durs)
    s.pg.goto(f"http://127.0.0.1:{PORT}/docs/video/cards.filled.html")
    s.pg.wait_for_timeout(300)
    s.ready()
    cur = None
    for scene, act, key, text in INTRO:
        if scene != cur:
            s.pg.evaluate("id=>show(id)", scene)
            cur = scene
            s.pg.wait_for_timeout(350)
        if act == "line":
            s.pg.evaluate("()=>line('#problem')")
        elif act:
            s.pg.evaluate("id=>reveal(id)", act)
        s.pg.evaluate("t=>cap(t)", text if scene != "title" else "")
        d = s.say(key)
        s.pg.wait_for_timeout(int((d + GAP) * 1000))
    return s.close()


def cards_outro(p, durs, rows):
    s = Seg(p, "outro", durs)
    s.pg.goto(f"http://127.0.0.1:{PORT}/docs/video/cards.filled.html")
    s.pg.wait_for_timeout(300)
    s.pg.evaluate("()=>show('results')")
    s.pg.wait_for_timeout(450)
    s.ready()
    s.pg.evaluate("()=>cap('')")
    d = s.say("o_res")
    n = rows + 1  # result rows, then the note
    for _ in range(n):
        s.pg.evaluate("()=>line('#results')")
        s.pg.wait_for_timeout(int(d * 1000 / n))
    s.pg.wait_for_timeout(int(GAP * 1000))
    s.pg.evaluate("()=>show('end')")
    d = s.say("o_end")
    s.pg.wait_for_timeout(int((d + 1.2) * 1000))
    return s.close()


def page_spans(run):
    """Seconds each page took in its slower lane of the recorded run."""
    span = {}
    for line in (run / "events.jsonl").read_text().splitlines():
        e = json.loads(line)
        if e.get("inc"):
            a = span.setdefault(e["inc"], {}).setdefault(e["mode"], [e["t"], e["t"]])
            a[0], a[1] = min(a[0], e["t"]), max(a[1], e["t"])
    return {inc: max(v[1] - v[0] for v in m.values()) for inc, m in span.items()}


def dashboard(p, run_rel, beats, durs, speed, spans):
    s = Seg(p, "dashboard", durs)
    pg = s.pg
    pg.goto(f"http://127.0.0.1:{PORT}/dashboard/index.html?run=../{run_rel}/events.jsonl")
    pg.wait_for_function("document.querySelector('#runinfo').textContent.length > 0")
    s.ready()
    # narrated pages play slower so each one lasts as long as its line
    slow = {inc: max(0.5, min(speed, spans.get(inc, 1) / (durs["b_" + inc] + GAP + 0.6))) for inc in beats}
    pg.evaluate("m=>{window.nsSpeedFor=m}", slow)
    pg.evaluate("v=>{const s=document.querySelector('#speed');if(![...s.options].some(o=>o.value===v))s.add(new Option(v+'x',v));}", str(speed))
    pg.select_option("#speed", str(speed))
    pg.click("#play")
    seen, shown_at, cur = set(), None, ""
    while True:
        m = re.match(r"(INC-\d+)", pg.inner_text("#pageid") or "")
        inc = m.group(1) if m else ""
        if inc != cur:
            cur = inc
            if inc in beats and inc not in seen:
                seen.add(inc)
                caption(pg, beats[inc], top=True)
                s.say("b_" + inc)
                shown_at = (time.monotonic(), durs["b_" + inc])
            elif shown_at and time.monotonic() - shown_at[0] > shown_at[1]:
                caption(pg, "", top=True)  # a new page: drop the old caption once its line is spoken
                shown_at = None
        if pg.inner_text("#play").strip() == "Replay":
            break
        pg.wait_for_timeout(100)
    caption(pg, DASH_END[1])
    pg.evaluate("()=>{document.body.style.paddingBottom='100vh';"
                "const t=document.querySelector('.totals');window.scrollTo(0,t.getBoundingClientRect().top+window.scrollY-20)}")
    d = s.say(DASH_END[0])
    pg.evaluate(DRIFT_JS, d + GAP)  # totals stay in place; a slow zoom keeps the frame moving
    pg.wait_for_timeout(int((d + GAP) * 1000))
    return s.close()


def portal(p, datapack, durs):
    """One short segment per portal page, each cut to start once the page has loaded (no white frames)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from share_datapack import login
    br0 = p.chromium.launch()
    state = login(br0, os.environ["MEKO_EMAIL"], os.environ["MEKO_PASSWORD"]).context.storage_state()  # off camera
    br0.close()
    base = f"https://cloud.mekodata.ai/datapacks/{datapack}"
    out = []
    for i, (path, lines) in enumerate(PORTAL):
        s = Seg(p, f"portal{i}", durs, storage_state=state)
        pg = s.pg
        pg.goto(base + path)
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(2500)
        pg.keyboard.press("Escape")
        pg.evaluate(MASK_JS, mask_pairs())
        pg.wait_for_timeout(300)
        s.ready()
        for key, text, dy in lines:
            caption(pg, text, top=(key == "p_7"))  # p_7 talks about the score near the bottom
            d = s.say(key)
            pg.evaluate(DRIFT_JS, d + GAP + 0.5)  # keep the frame moving while the line is spoken
            steps = max(1, int((d + GAP) * 10))
            if isinstance(dy, str):  # bring the card the line talks about to the middle, above the caption
                pg.evaluate(SCROLL_TO_TEXT_JS, dy)
                dy = 0
            for _ in range(steps):
                if dy:
                    pg.mouse.wheel(0, dy / steps)
                pg.wait_for_timeout(100)
        out.append(s.close())
    return out


def results(run, noshare, seeds=()):
    s = json.loads((run / "summary.json").read_text())["results"]
    ns = json.loads((noshare / "summary.json").read_text())["results"]["nightshift"]
    pct = lambda x: f"{round(x * 100)}%"  # noqa: E731
    rows = "<tr><th></th><th>Runbook in prompt</th><th>Night Shift</th><th>No sharing*</th></tr>"
    for k, label in (("repeat", "Repeat pages"), ("first_seen", "First-seen pages"), ("trap", "Trap pages")):
        b, n, x = s["baseline"][k], s["nightshift"][k], ns[k]
        rows += (f"<tr class='line'><td>{label} ({n['n']})</td><td>{pct(b['accuracy'])}</td>"
                 f"<td class='{'win' if n['accuracy'] > b['accuracy'] else ''}'>{pct(n['accuracy'])}</td><td>{pct(x['accuracy'])}</td></tr>")
    nr, br_, tr = s["nightshift"]["repeat"], s["baseline"]["repeat"], s["nightshift"]["trap"]
    note = (f"Repeat pages: {br_['steps_mean']} → {nr['steps_mean']} steps, "
            f"{br_['tokens_mean']:,} → {nr['tokens_mean']:,} tokens per page. "
            f"*Night Shift with private memory only, offline run of the same pages.")
    if seeds:  # every live seed, so the card does not rest on the one run replayed above
        reps = [json.loads((d / "summary.json").read_text())["results"] for d in seeds]
        m = lambda mode: round(sum(r[mode]["repeat"]["accuracy"] for r in reps) / len(reps) * 100)  # noqa: E731
        note += f" Over {len(reps)} live seeds, repeat pages: {m('baseline')}% → {m('nightshift')}%."
    fewer = round((1 - nr["tokens_mean"] / br_["tokens_mean"]) * 100)
    say = (f"In the run you just watched, on repeat pages, Night Shift resolved {round(nr['accuracy'] * 100)} percent, "
           f"against {round(br_['accuracy'] * 100)} for the runbook, with {fewer} percent fewer tokens. "
           f"In an offline run with private memory only, it reached {round(ns['repeat']['accuracy'] * 100)} percent. "
           f"Traps stay hard: {round(tr['accuracy'] * 100)} percent, against {round(s['baseline']['trap']['accuracy'] * 100)} for the runbook.")
    if seeds:
        say += f" Across all {len(seeds)} live seeds, repeat pages went from {m('baseline')} to {m('nightshift')} percent."
    fills = {"RES_TITLE": "The live run above: pages resolved", "RES_ROWS": rows, "RES_NOTE": note}
    return fills, say, 3


def ffdur(f):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                                capture_output=True, text=True).stdout.strip())


def main():
    run, noshare = Path(sys.argv[1]), Path(sys.argv[2])
    beats = json.loads((OUT / "beats.json").read_text())
    speed = int(os.environ.get("REPLAY_SPEED", "22"))
    setup = next(json.loads(x) for x in (run / "events.jsonl").read_text().splitlines() if '"setup"' in x)
    datapack = setup["datapack"]
    fills, res_say, nrows = results(run, noshare, [Path(x) for x in sys.argv[3:]])

    lines = {key: text for _, _, key, text in INTRO}
    lines.update({"b_" + k: v for k, v in beats.items()})
    lines[DASH_END[0]] = DASH_END[1]
    lines.update({k: t for _, ls in PORTAL for k, t, _ in ls})
    lines["o_res"] = res_say
    lines["o_end"] = "The code, the replay dashboard, and every recorded run are on GitHub. No paid model API was used."
    (OUT / "narration.json").write_text(json.dumps(lines, indent=1))
    kpy = os.path.expanduser(os.environ.get("KOKORO_PY", "~/.cache/kk/v/bin/python"))
    subprocess.run([kpy, str(ROOT / "scripts" / "tts.py")], check=True)
    durs = {k: v["seconds"] for k, v in json.loads((VO / "durations.json").read_text()).items()}

    (OUT / "raw").mkdir(parents=True, exist_ok=True)
    httpd = serve()
    run_rel = str(run.relative_to(ROOT)) if run.is_absolute() else str(run)
    with sync_playwright() as p:
        parts = [cards_intro(p, durs, fills),
                 dashboard(p, run_rel, beats, durs, speed, page_spans(run)),
                 *portal(p, datapack, durs),
                 cards_outro(p, durs, nrows)]
    httpd.shutdown()

    mp4s, cues, off = [], [], 0.0
    for i, (w, said, trim) in enumerate(parts):
        m = OUT / "raw" / f"{i}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{trim:.2f}", "-i", str(w), "-vf", "fps=30,format=yuv420p",
                        "-c:v", "libx264", "-crf", "20", "-an", str(m)], check=True)
        cues += [(key, off + t) for key, t in said]
        off += ffdur(m)
        mp4s.append(m)
    lst = OUT / "raw" / "list.txt"
    lst.write_text("".join(f"file '{m}'\n" for m in mp4s))
    silent = OUT / "raw" / "silent.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", str(silent)], check=True)
    (OUT / "raw" / "cues.json").write_text(json.dumps(cues, indent=1))

    # voice track: every line placed at the moment its caption appeared
    ins, flt = [], []
    for i, (key, t) in enumerate(cues):
        ins += ["-i", str(VO / f"{key}.wav")]
        flt.append(f"[{i}:a]aresample=44100,adelay={int(t * 1000)}:all=1[v{i}]")
    flt.append("".join(f"[v{i}]" for i in range(len(cues))) + f"amix=inputs={len(cues)}:normalize=0,apad,atrim=0:{off:.2f}[out]")
    voice = OUT / "raw" / "voice.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *ins, "-filter_complex", ";".join(flt), "-map", "[out]",
                    "-ac", "1", str(voice)], check=True)
    bgm = OUT / "raw" / "bgm.wav"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "bgm.py"), f"{off:.2f}", str(bgm)], check=True)
    # voice to about -16 LUFS, bed about 16 dB under it
    voice_n, bgm_n = OUT / "raw" / "voice_n.wav", OUT / "raw" / "bgm_n.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(voice), "-af", "loudnorm=I=-16:TP=-2:LRA=11", "-ar", "44100", str(voice_n)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(bgm), "-af", "loudnorm=I=-32:TP=-9:LRA=7", "-ar", "44100", str(bgm_n)], check=True)
    final = OUT / "nightshift-demo.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-i", str(voice_n), "-i", str(bgm_n),
                    "-filter_complex", "[1:a][2:a]amix=inputs=2:normalize=0,alimiter=limit=0.7:level=0[a]",
                    "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest",
                    "-movflags", "+faststart", str(final)], check=True)
    print(final, round(off, 1), "s")


if __name__ == "__main__":
    main()
