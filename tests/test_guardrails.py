"""Deterministic tests for the parts of Night Shift that decide what may be applied.

No model and no network: the local model is replaced by a scripted list of actions,
Meko by the offline stand-in, Postgres by the SQLite ledger.
"""
import pytest

from nightshift import agents, sim
from nightshift.agents import Result, _fresh_evidence, _investigate, human_handback
from nightshift.ledger import Ledger
from nightshift.meko import accounts


@pytest.fixture(autouse=True)
def _tmp_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the SQLite ledger lives in ./results


def script(monkeypatch, actions):
    """Make the model answer with these actions, in order."""
    it = iter(actions)
    monkeypatch.setattr(agents.llm, "chat_json", lambda *a, **k: (next(it), 10, 5))


def first_page():
    inc = sim.build(1)[0]  # C01: pgbouncer pool on checkout-api, target pgb-checkout-rw
    return inc, inc.cause


def run(inc, actions, monkeypatch, **kw):
    script(monkeypatch, actions + [{"action": "get_logs", "service": "x"}] * agents.MAX_STEPS)
    res = Result("nightshift", inc)
    _investigate(res, "", lambda *a: None, seed=1, mem_map=kw.pop("mem_map", {}), **kw)
    return res


def test_fixer_refuses_unseen_target(monkeypatch):
    inc, c = first_page()
    res = run(inc, [{"action": "apply_fix", "service": c.service, "fix": c.fix, "target": inc.target}], monkeypatch)
    assert res.history[0]["obs"].startswith("REFUSED by Fixer: target")
    assert not inc.tries  # nothing was applied


def test_fixer_applies_target_after_diagnostic(monkeypatch):
    inc, c = first_page()
    cmd = next(iter(c.clues))
    res = run(inc, [{"action": cmd, "service": c.service},
                    {"action": "apply_fix", "service": c.service, "fix": c.fix, "target": inc.target}], monkeypatch)
    assert res.resolved and res.steps == 2


def test_fixer_refuses_service_name_as_target(monkeypatch):
    inc, c = first_page()
    res = run(inc, [{"action": "apply_fix", "service": c.service, "fix": c.fix, "target": c.service}], monkeypatch)
    assert "REFUSED" in res.history[0]["obs"] and not inc.tries


def test_same_failed_fix_is_refused_twice(monkeypatch):
    inc, c = first_page()
    wrong = {"action": "apply_fix", "service": c.service, "fix": "F99" if "F99" in sim.FIXES else c.fix,
             "target": "pgb-other"}
    res = run(inc, [wrong, wrong], monkeypatch, memory_evidence="memory says pgb-other")
    assert res.history[0]["ok"] is False and not res.history[0]["obs"].startswith("REFUSED")
    assert res.history[1]["obs"].startswith("REFUSED by Fixer") and len(inc.tries) == 1


def test_retired_fix_refused_without_fresh_evidence(monkeypatch):
    inc, c = first_page()
    blocked = {(c.fix, inc.target.lower()): "failed on INC-00"}
    res = run(inc, [{"action": "apply_fix", "service": c.service, "fix": c.fix, "target": inc.target}], monkeypatch,
              memory_evidence=f"old memory: {inc.target}", retired_reasons=blocked)
    assert "fix ledger retired" in res.history[0]["obs"]
    assert res.stale_fix_blocked == 1 and not inc.tries


def test_retired_fix_allowed_when_this_page_prints_target(monkeypatch):
    inc, c = first_page()
    cmd = next(iter(c.clues))
    blocked = {(c.fix, inc.target.lower()): "failed on INC-00"}
    res = run(inc, [{"action": cmd, "service": c.service},
                    {"action": "apply_fix", "service": c.service, "fix": c.fix, "target": inc.target}], monkeypatch,
              mem_map={c.fix: ("m1", "retired")}, retired_reasons=blocked)
    assert res.resolved
    assert res.stale_fix_applied == 1  # counted as a retired fix applied, and it worked


def test_fresh_evidence_rules():
    inc, c = first_page()
    res = Result("nightshift", inc)
    res.history = [{"action": "get_pool_stats", "service": c.service, "obs": f"pool {inc.target}: cl_waiting=120"}]
    assert _fresh_evidence(res, inc.target, failed_here=False)
    assert not _fresh_evidence(res, inc.target, failed_here=True)
    assert not _fresh_evidence(res, "pgb-other", failed_here=False)
    # a fix result echoing the target is not a diagnostic
    res.history = [{"action": "apply_fix", "fix": c.fix, "service": c.service, "obs": f"{inc.target} applied"}]
    assert not _fresh_evidence(res, inc.target, failed_here=False)


def test_ledger_learn_retire_replace():
    lg = Ledger("t1")
    lg.learn("m1", "F01", "checkout-api", "pool full", "A", "INC-01")
    assert lg.status("m1")["status"] == "valid" and lg.unpromoted_valid() == []  # waits for review
    lg.approve("m1", "alice")
    assert lg.unpromoted_valid() == ["m1"]
    lg.mark_promoted(["m1"])
    assert lg.unpromoted_valid() == []
    lg.retire("m1", "did not work on INC-24")
    lg.learn("m2", "F01b", "checkout-api", "pool full", "B", "INC-24")
    lg.replace("m1", "m2")
    s = lg.status("m1")
    assert s["status"] == "retired" and s["replaced_by"] == "m2" and "INC-24" in s["retired_reason"]
    assert Ledger("t2").status("m1") is None  # runs do not see each other's rows


def test_human_handback_is_searchable_by_other_shift():
    a, b = accounts(offline=True)
    lg = Ledger("t3")
    inc, c = first_page()
    res = Result("nightshift", inc, cid=a.conversation("nightshift:shift-a", "t"), agent="nightshift:shift-a")
    res.retired = [("m-old", c.fix, "failed")]
    lg.learn("m-old", c.fix, c.service, "x", "A", "INC-00")
    mid = human_handback(res, a, lg, lambda *x: None)
    st = lg.status(mid)
    assert st["learned_by"] == "A (human)" and st["status"] == "valid"
    assert lg.status("m-old")["replaced_by"] == mid
    a.promote(res.cid, [mid])
    hits = b.kb_search(b.conversation("nightshift:shift-b", "t"), "nightshift:shift-b", inc.alert)
    assert any(inc.target in h.text for h in hits)
