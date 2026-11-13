"""Review queue, human handback CLI, ledger ablation, Alertmanager triage. No model, no network."""
import pytest

from nightshift import agents, handback, review, webhook
from nightshift.ledger import Ledger
from nightshift.meko import accounts


@pytest.fixture(autouse=True)
def _tmp_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NIGHTSHIFT_LEDGER", "sqlite")


NOTE = dict(shift="A", inc="INC-900", paged="inventory-svc", alert="inventory-svc OOMKilled, 4 restarts in 10 min",
            cause="memory leak in new release", evidence_cmd="get_deploys inventory-svc",
            evidence="inventory-svc v5.8.0 deployed 2 min ago; previous v5.7.0 healthy", fix="F04", target="v5.7.0",
            service="inventory-svc")


def test_manual_review_holds_until_approved():
    ma, mb = accounts(True)
    lg = Ledger("r1")
    mid, _ = handback.handback(ma, lg, **NOTE)
    assert lg.status(mid)["learned_by"] == "A (human)"
    assert agents.curator(ma, lg, lambda *a: None, night=0, review="manual") == []   # nobody approved yet
    assert [r["memory_id"] for r in lg.pending_review()] == [mid]
    lg.approve(mid, "alice")
    assert agents.curator(ma, lg, lambda *a: None, night=0, review="manual") == [mid]
    assert mb.kb_search("c", "b", "inventory-svc OOMKilled restarts")[0].memory_id == mid  # shift B sees it now


def test_rejected_fix_is_never_shared_and_counts_as_retired():
    ma, _ = accounts(True)
    lg = Ledger("r2")
    mid, _ = handback.handback(ma, lg, **NOTE)
    lg.reject(mid, "bob", "rollback hides the leak")
    assert agents.curator(ma, lg, lambda *a: None, night=0, review="auto") == []
    st = lg.status(mid)
    assert st["status"] == "retired" and "rejected by bob" in st["retired_reason"]


def test_auto_review_is_marked_as_benchmark():
    ma, _ = accounts(True)
    lg = Ledger("r3")
    mid, _ = handback.handback(ma, lg, **NOTE)
    assert agents.curator(ma, lg, lambda *a: None, night=0, review="auto") == [mid]
    assert lg._x("SELECT approved_by FROM fix_validity WHERE memory_id=?", (mid,))[0][0] == "auto (benchmark)"


def test_review_cli_lists_and_approves(capsys):
    ma, _ = accounts(True)
    lg = Ledger("r4")
    mid, _ = handback.handback(ma, lg, **NOTE)
    assert [r["memory_id"] for r in review.main(["--run-id", "r4", "--offline", "list"])] == [mid]
    review.main(["--run-id", "r4", "--offline", "approve", mid, "--by", "carol"])
    assert Ledger.open("r4").unpromoted_valid() == [mid]


def test_webhook_suggests_valid_fix_and_warns_on_retired():
    ma, mb = accounts(True)
    lg = Ledger("w1")
    mid, _ = handback.handback(ma, lg, **NOTE)
    payload = {"alerts": [{"status": "firing", "labels": {"service": "inventory-svc", "shift": "A"},
                           "annotations": {"summary": "inventory-svc OOMKilled, 4 restarts in 10 min"}},
                          {"status": "resolved", "labels": {"service": "x"}, "annotations": {"summary": "ignored"}}]}
    out = webhook.handle(payload, lambda s: ma if s == "A" else mb, lg)
    assert len(out) == 1 and out[0]["valid"][0]["fix"] == "F04" and out[0]["valid"][0]["target"] == "v5.7.0"
    assert "learned by shift A (human) on INC-900" in webhook.text(out[0])
    lg.retire(mid, "INC-901: F04 applied, symptoms persisted; root cause changed")
    out = webhook.handle(payload, lambda s: ma, lg)
    assert out[0]["valid"] == [] and out[0]["retired"][0]["fix"] == "F04"
    assert "Do not re-apply F04 target v5.7.0" in webhook.text(out[0])


def test_ledger_ablation_shows_retired_memory_as_valid(monkeypatch):
    from nightshift import sim
    ma, _ = accounts(True)
    lg = Ledger("n1")
    inc = sim.build(1)[0]
    c = inc.cause
    mid = ma.memory_add("c", "a", f"Night Shift fix. Page on {c.paged}: '{inc.alert}'. apply_fix {c.fix} target {inc.target}", {})
    lg.learn(mid, c.fix, c.service, f"{c.paged}: {inc.alert}", "A", "INC-00")
    lg.retire(mid, "INC-00b: stopped working")
    seen = {}
    monkeypatch.setattr(agents, "_investigate", lambda res, context, *a, **k: seen.setdefault(k.get("use") or "ctx", context))
    agents.run_nightshift(sim.build(1)[0], ma, lg, lambda *a: None, 1, "m", share=False, use_ledger=True)
    with_ledger = seen.pop("ctx")
    agents.run_nightshift(sim.build(1)[0], ma, lg, lambda *a: None, 1, "m", share=False, use_ledger=False)
    without = seen.pop("ctx")
    assert "- RETIRED" in with_ledger and "- RETIRED" not in without and "- VALID" in without
