"""The Night Shift crew.

Triage      context_search (my shift's private memory, logged for self-learning)
            + knowledgebase_search (promoted team memory + the runbook)
            -> joins every hit against Postgres fix_validity.
Investigator  local model picks one read-only command per step; each turn's
            reasoning and plan are stored with conversation_add_message
            (Meko Decision trace) and tokens with track_token_usage.
Fixer       applies the fix; on a verified fix it memory_add's what worked and
            puts a postmortem artifact; if a remembered fix fails it retires the
            memory in Postgres and the next fix replaces it.
Curator     the datapack owner approves verified fixes -> memory_promote
            (private -> collective, visible to the other shift).
Librarian   selflearning_shortfalls -> memory_add answers -> selflearning_verify.

Baseline is the same model and simulator with the whole runbook pasted into
the prompt and no memory at all.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field

from . import llm
from .sim import COMMANDS, FIXES, SERVICES, Incident

MAX_STEPS = 12
FIX_RE = re.compile(r"apply_fix (F\d\d)")
TARGET_RE = re.compile(r"apply_fix F\d\d target (\S+)")

SYSTEM = f"""You are the on-call investigator for a small SaaS company.
Services: {", ".join(SERVICES)}.
Read-only diagnostic commands (each takes a service): {", ".join(COMMANDS)}.
Fixes you can apply (each takes a service):
""" + "\n".join(f"  {k} {v}" for k, v in FIXES.items()) + """
Every action costs one step. Resolve the page in as few steps as possible.
Every fix needs the exact target (the name in angle brackets). Targets are specific to this company:
you only know one if a diagnostic output or a VALID team memory shows it. A wrong fix or wrong target wastes a step.
Never apply a fix from a memory marked RETIRED.
Reply with JSON only:
{"action": "<command>" or "apply_fix", "service": "<service>", "fix": "<Fxx, only for apply_fix>", "target": "<exact target, only for apply_fix>",
 "root_cause": "<your current hypothesis, short>", "reason": "<why this action, short>"}"""


@dataclass
class Result:
    mode: str
    inc: Incident
    steps: int = 0
    tin: int = 0
    tout: int = 0
    seconds: float = 0.0
    resolved: bool = False
    history: list = field(default_factory=list)
    used_memory: str | None = None
    stale_fix_applied: int = 0
    stale_fix_blocked: int = 0
    match_quality: str = "-"
    root_cause: str = ""
    learned: str | None = None
    retired: list = field(default_factory=list)
    cid: str | None = None
    agent: str = ""

    def row(self):
        return {"inc_id": self.inc.id, "shift": self.inc.shift, "night": self.inc.night, "cause": self.inc.cause.id,
                "alert": self.inc.alert, "first_seen": int(self.inc.first_seen), "trap": int(self.inc.trap),
                "resolved": int(self.resolved), "steps": self.steps, "tokens_in": self.tin, "tokens_out": self.tout,
                "seconds": round(self.seconds, 2), "used_memory": self.used_memory,
                "stale_fix_applied": self.stale_fix_applied,
                "stale_fix_blocked": self.stale_fix_blocked, "match_quality": self.match_quality}


def _fresh_evidence(res, tgt: str, failed_here: bool) -> bool:
    """True when a diagnostic command run in this page printed the target (and it has not failed here)."""
    if failed_here:
        return False
    out = " ".join(h["obs"] for h in res.history if h["action"] in COMMANDS and "fix" not in h)
    return tgt.lower() in set(re.findall(r"[A-Za-z0-9_./-]+", out.lower()))


def _investigate(res: Result, context: str, emit, seed: int, mem_map: dict, memory_evidence: str = "",
                 retired_reasons: dict | None = None):
    """Shared loop. mem_map: fix_id -> (memory_id, status) for memories shown in context."""
    inc = res.inc
    was_retired = {f for f, (_, st) in mem_map.items() if st == "retired"}
    blocked = dict(retired_reasons or {})
    page_failed = set()
    failed = set()
    for step in range(MAX_STEPS):
        hist = "\n".join(f"{i + 1}. {h['action']} {h.get('fix', '')} {h.get('target', '')} {h['service']} -> {h['obs']}"
                         for i, h in enumerate(res.history)) or "(nothing yet)"
        user = f"PAGE: {inc.alert}\n\n{context}\n\nSteps so far:\n{hist}\n\nNext action?"
        a, ti, to = llm.chat_json(SYSTEM, user, seed=seed * 100 + step)
        res.tin += ti
        res.tout += to
        res.steps += 1
        act, svc = a.get("action", "invalid"), a.get("service", "")
        res.root_cause = a.get("root_cause", res.root_cause)
        if act == "apply_fix":
            fix = a.get("fix", "")
            tgt = str(a.get("target", "")).strip()
            key = (fix, svc.lower(), tgt.lower())
            if (fix, tgt.lower()) in blocked and not _fresh_evidence(res, tgt, (fix, tgt.lower()) in page_failed):
                # Ledger guardrail (Night Shift only): a remembered fix + target the ledger retired for
                # this alert is not applied again, on any service, unless this page's own diagnostics
                # name that target (the old cause can come back; fresh evidence beats an old retirement).
                res.stale_fix_blocked += 1
                obs = (f"REFUSED by Fixer: the fix ledger retired {fix} {tgt} for this alert ({blocked[(fix, tgt.lower())]}). "
                       f"The cause is different this time. Find what is different with a command "
                       f"you have not run yet ({', '.join(COMMANDS)}).")
                h = {"action": "apply_fix", "fix": fix, "target": tgt, "service": svc, "obs": obs, "reason": a.get("reason", ""), "ok": False}
                res.history.append(h)
                emit("step", res, h)
                continue
            if key in failed:
                obs = (f"REFUSED by Fixer: {fix} {tgt} on {svc} was already applied in this page and did not help. "
                       f"Whatever suggested it no longer matches this page. Gather new evidence with a command you have not run yet "
                       f"({', '.join(COMMANDS)}).")
                h = {"action": "apply_fix", "fix": fix, "target": tgt, "service": svc, "obs": obs, "reason": a.get("reason", ""), "ok": False}
                res.history.append(h)
                emit("step", res, h)
                continue
            seen = memory_evidence + " " + " ".join(h["obs"] for h in res.history if not h["obs"].startswith(("REFUSED", "raise_", "renew_", "restart_", "rollback_", "fix_", "flush_", "rotate_", "add_", "disable_", "tune_", "free_", "release_", "scale_", "drain_")))
            tokens = set(re.findall(r"[A-Za-z0-9_./-]+", seen.lower()))
            if not tgt or tgt.lower() not in tokens or tgt in SERVICES:
                # Fixer guardrail (both modes): never act on a target nobody has seen.
                obs = (f"REFUSED by Fixer: target '{tgt}' is a service name or does not appear in any evidence or memory. The target is the exact thing in angle brackets (a version, node, key, flag, path...). "
                       f"Find the exact name first with one of: {', '.join(COMMANDS)}.")
                h = {"action": "apply_fix", "fix": fix, "target": tgt, "service": svc, "obs": obs, "reason": a.get("reason", ""), "ok": False}
                res.history.append(h)
                emit("step", res, h)
                continue
            if fix in was_retired:
                res.stale_fix_applied += 1
            ok, obs = inc.apply_fix(fix, svc, tgt)
            if not ok:
                failed.add(key)
                if fix in mem_map:
                    blocked[(fix, tgt.lower())] = f"{fix} {tgt} failed earlier in this page"
                    page_failed.add((fix, tgt.lower()))
                    obs += f" The remembered fix did not work here: the ledger retires it and the Fixer will refuse {fix} {tgt} for the rest of this page."
            h = {"action": "apply_fix", "fix": fix, "target": tgt, "service": svc, "obs": obs, "reason": a.get("reason", ""), "ok": ok}
            res.history.append(h)
            emit("step", res, h)
            if fix in mem_map and not ok and mem_map[fix][0] not in [r[0] for r in res.retired]:
                res.retired.append((mem_map[fix][0], fix, obs))
            if ok:
                res.resolved = True
                return
        else:
            if act not in COMMANDS:
                obs = f"error: '{act}' is not a command. Use one of: {', '.join(COMMANDS)}, apply_fix"
            elif any(h["action"] == act and h["service"] == svc for h in res.history):
                obs = f"(same output as before) {inc.run(act, svc)} -- you already ran {act} on {svc}; try a different command or service"
            else:
                obs = inc.run(act, svc)
            h = {"action": act, "service": svc, "obs": obs, "reason": a.get("reason", "")}
            res.history.append(h)
            emit("step", res, h)


# ---------------------------------------------------------------------------
def run_baseline(inc: Incident, runbook: str, emit, seed: int) -> Result:
    res = Result("baseline", inc)
    t = time.time()
    emit("start", res, {"alert": inc.alert})
    _investigate(res, f"RUNBOOK (team wiki):\n{runbook}", emit, seed, {})
    res.seconds = time.time() - t
    emit("end", res, {"resolved": res.resolved})
    return res


def _hit_line(h, st):
    who = (f"learned by shift {st['learned_by']} in {st['learned_in']}" if st
           else "runbook" if h.source == "collective" and not h.memory_id else "unverified note")
    tag = "COLLECTIVE (approved by curator)" if h.source == "collective" else "PRIVATE (this shift)"
    if st and st["status"] == "retired":
        return f"- RETIRED [{who}; retired: {st['retired_reason']}] {h.text}"
    return f"- VALID {tag} [{who}] {h.text}"


def run_nightshift(inc: Incident, meko, ledger, emit, seed: int, model: str, share: bool = True,
                   memory: bool = True, use_ledger: bool = True) -> Result:
    """memory=False is the retrieval-only control: the runbook is searched in Meko instead of pasted,
    but nothing is remembered, retired or promoted.
    use_ledger=False is the ledger ablation: memories are still learned and shared, but every remembered
    fix looks valid forever (no retirement, no refusal of stale fixes), as with a plain memory store."""
    res = Result("nightshift", inc)
    t = time.time()
    agent = f"nightshift:shift-{inc.shift.lower()}"
    emit("start", res, {"alert": inc.alert})
    cid = meko.conversation(agent, f"{inc.id} {inc.alert}")

    # --- Triage ---------------------------------------------------------------
    query = f"{inc.cause.paged}: {inc.alert}"
    private, band, _ = meko.context_search(cid, agent, query) if memory else ([], "-", {})
    collective = meko.kb_search(cid, agent, query) if share else []
    res.match_quality = band
    lines, mem_map, shown, retired = [], {}, 0, {}
    hits = private[:3] + collective[:4]
    stats = [ledger.status(h.memory_id) if h.memory_id else None for h in hits]
    if not use_ledger:
        stats = [dict(st, status="valid", retired_reason=None) if st else None for st in stats]
    # Only memories learned on the same paged service speak for this alert. A memory from another
    # alert that the model misapplies here must not be retired or blocked for its own alert.
    same = [bool(st) and str(st.get("symptom") or "").startswith(f"{inc.cause.paged}: ") for st in stats]
    for h, st, sm in zip(hits, stats, same):
        m, tm = FIX_RE.search(h.text), TARGET_RE.search(h.text)
        if m and st and tm and sm:
            key = (m.group(1), tm.group(1).lower())
            if st["status"] == "retired":
                retired.setdefault(key, st["retired_reason"])
            elif st["status"] == "valid":
                retired.pop(key, None)
    for h, st, sm in zip(hits, stats, same):
        m = FIX_RE.search(h.text)
        if m and st and sm:
            mem_map[m.group(1)] = (h.memory_id, st["status"])
            if st["status"] == "valid" and res.used_memory is None:
                res.used_memory = h.memory_id
        line = _hit_line(h, st)
        if not st:
            stale = [f"{f} {tg}" for f, tg in retired if re.search(rf"\b{f}\b", h.text)]
            if stale:
                line += f" [NOTE: the ledger retired {', '.join(stale)} for this alert; that exact fix no longer works]"
        lines.append(line)
        shown += 1
    emit("triage", res, {"band": band, "private": len(private), "collective": len(collective),
                         "hits": lines[:4], "used": res.used_memory,
                         "badge": next((l for l in lines if l.startswith("- VALID") and "learned by" in l), None)})
    context = "TEAM MEMORY (from Meko, checked against the Postgres fix ledger):\n" + ("\n".join(lines) if lines else "(no match)")

    # --- Investigator + Fixer -----------------------------------------------------
    _investigate(res, context, emit, seed, mem_map, "\n".join(l for l in lines if "learned by" in l), retired)
    res.seconds = time.time() - t

    plan = [f"{h['action']} {h.get('fix', '')} {h['service']}".replace("  ", " ") for h in res.history]
    ledger_check = "; ".join(l[2:120] for l in lines if l.startswith(("- VALID", "- RETIRED")) and "learned by" in l)
    reasoning = (f"Triage: {band} match, {len(private)} private + {len(collective)} collective hits; "
                 f"used memory {res.used_memory or 'none'}. Postgres ledger check: {ledger_check or 'no ledgered memory matched'}. "
                 f"Hypothesis: {res.root_cause}. "
                 + " | ".join(f"{h['action']}: {h['reason']}" for h in res.history)[:1500])
    meko.add_message(cid, agent, f"Page {inc.id}: {inc.alert}",
                     f"{'RESOLVED' if res.resolved else 'UNRESOLVED'} in {res.steps} steps. Root cause: {res.root_cause}",
                     reasoning, plan)
    meko.track_tokens(cid, res.tin, res.tout, model)

    if not memory:
        emit("end", res, {"resolved": res.resolved, "conversation_id": cid})
        return res

    final_fix = res.history[-1]["fix"] if res.resolved else None
    same_fix_new_target = any(fix == final_fix for _, fix, _ in res.retired)
    for mid, fix, obs in (res.retired if use_ledger else []):
        reason = (f"{inc.id}: target changed between incidents; superseded by a refined memory" if fix == final_fix
                  else f"{inc.id}: {fix} applied, symptoms persisted; root cause changed")
        ledger.retire(mid, reason)
        emit("retire", res, {"memory_id": mid, "fix": fix, "reason": reason})

    if res.resolved:
        fix_h = res.history[-1]
        remembered = fix_h["fix"] in mem_map and mem_map[fix_h["fix"]][1] == "valid" and not same_fix_new_target
        if not remembered:
            evidence = next((h for h in reversed(res.history[:-1]) if "healthy" not in h["obs"] and "no errors" not in h["obs"]), None)
            ev = f" Evidence: {evidence['action']} {evidence['service']} showed '{evidence['obs'][:140]}'." if evidence else ""
            text = (f"Night Shift fix. Page on {inc.cause.paged}: '{inc.alert}'. Root cause: {res.root_cause}.{ev} "
                    f"Resolution: apply_fix {fix_h['fix']} target {fix_h['target']} on {fix_h['service']} (verified by simulator, {inc.id}, shift {inc.shift}).")
            if same_fix_new_target and evidence:
                text += f" The target changes between incidents: read it with {evidence['action']} first."
            elif res.retired:
                text += f" Supersedes the earlier fix {res.retired[0][1]}, which no longer works for this page."
            mid = meko.memory_add(cid, agent, text, {"incident": inc.id, "fix": fix_h["fix"], "service": fix_h["service"],
                                                    "verified": True, "shift": inc.shift})
            if mid:
                ledger.learn(mid, fix_h["fix"], fix_h["service"], f"{inc.cause.paged}: {inc.alert}", inc.shift, inc.id)
                for old, _, _ in res.retired:
                    ledger.replace(old, mid)
                res.learned = mid
                post = (f"# Postmortem {inc.id}\n\n- Page: {inc.alert}\n- Shift: {inc.shift}\n- Root cause: {res.root_cause}\n"
                        f"- Fix: {fix_h['fix']} {fix_h['target']} ({FIXES[fix_h['fix']]})\n- Steps: {res.steps}\n\n## Timeline\n"
                        + "\n".join(f"{i + 1}. {h['action']} {h.get('fix', '')} {h['service']} -> {h['obs']}" for i, h in enumerate(res.history)))
                meko.artifact_put(cid, agent, f"postmortem-{inc.id}.md", post)
                emit("learn", res, {"memory_id": mid, "text": text})
    emit("end", res, {"resolved": res.resolved, "conversation_id": cid})
    res.cid, res.agent = cid, agent
    return res


def human_note(inc_id, paged, alert, cause, evidence_cmd, evidence_out, fix, target, service, shift, changing_cmd=None):
    """The memory text for a fix written by a person (also used by `python -m nightshift.handback`)."""
    text = (f"On-call human fix (the agent could not resolve {inc_id}). Page on {paged}: '{alert}'. "
            f"Root cause: {cause}. Evidence: {evidence_cmd} showed '{evidence_out[:140]}'. "
            f"Resolution: apply_fix {fix} target {target} on {service} (written by the shift {shift} on-call engineer).")
    if changing_cmd:
        text += f" The target changes between incidents: read it with {changing_cmd} first."
    return text


def human_handback(res: Result, meko, ledger, emit):
    """The agent gave up, so the on-call human fixed the page. Their note goes into Meko like any other fix,
    marked as written by a person, so the next shift finds it at triage (theme 02: learn from people)."""
    inc = res.inc
    c = inc.cause
    cmd, out = next(iter(c.clues.items()))
    text = human_note(inc.id, c.paged, inc.alert, c.title, f"{cmd} {c.service}", inc._fmt(out), c.fix, inc.target,
                      c.service, inc.shift, cmd if "{" in c.target else None)
    mid = meko.memory_add(res.cid, res.agent, text, {"incident": inc.id, "fix": c.fix, "service": c.service,
                                                     "verified": True, "shift": inc.shift, "source": "human"})
    if mid:
        ledger.learn(mid, c.fix, c.service, f"{c.paged}: {inc.alert}", f"{inc.shift} (human)", inc.id)
        for old, _, _ in res.retired:
            ledger.replace(old, mid)
        res.learned = mid
        emit("human", res, {"memory_id": mid, "text": text})
    return mid


def curator(owner_meko, ledger, emit, night: int, review: str | None = None):
    """End of night: share every fix that is still valid AND approved by a reviewer.

    review="manual" (production): only fixes a person approved with `python -m nightshift.review approve`
    are shared; the rest wait in the review queue. review="auto" (the benchmark, no human at the
    keyboard): every valid fix is approved as "auto (benchmark)". Default: NIGHTSHIFT_REVIEW or "auto".
    """
    review = review or os.environ.get("NIGHTSHIFT_REVIEW", "auto")
    if review == "auto":
        for row in ledger.pending_review():
            ledger.approve(row["memory_id"], "auto (benchmark)")
    waiting = len(ledger.pending_review())
    ids = ledger.unpromoted_valid()
    if not ids:
        if waiting:
            emit("promote", None, {"night": night, "ids": [], "waiting_review": waiting})
        return []
    cid = owner_meko.conversation("nightshift:curator", f"night {night} approvals")
    r = owner_meko.promote(cid, ids)
    ok = (r or {}).get("inserted_ids", []) + (r or {}).get("updated_ids", [])
    ledger.mark_promoted(ok)
    emit("promote", None, {"night": night, "ids": ok, "waiting_review": waiting})
    return ok


def librarian(meko, ledger, incidents_text: dict, emit, night: int, max_fix: int = 4):
    """Find this shift's weak searches, answer the ones we have since learned, prove it with a replay."""
    cid = meko.conversation("nightshift:librarian", f"night {night} self-learning")
    status = meko.sl_status(cid)
    sf = meko.sl_shortfalls(cid)
    rows = sf.get("items") or sf.get("searches") or sf.get("shortfalls") or []
    done = []
    for row in rows:
        if len(done) >= max_fix:
            break
        q = row.get("query") or row.get("prompt") or ""
        if row.get("state", "open") != "open" or not q:
            continue
        answer = next((txt for alert, txt in incidents_text.items() if alert and alert in q), None)
        if not answer:
            continue
        meko.memory_add(cid, "nightshift:librarian", f"Q: {q}\nA: {answer}", {"source": "librarian", "log_id": row.get("log_id")})
        time.sleep(4)
        v = meko.sl_verify(cid, row.get("log_id"))
        done.append({"query": q, "verify": v})
        emit("librarian", None, {"night": night, "query": q, "verify": v})
    return {"status": status.get("quality") if isinstance(status, dict) else None, "fixed": done}
