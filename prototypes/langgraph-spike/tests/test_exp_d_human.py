"""Experiment D: durable human approval boundary, terminal-state protection, no duplicate irreversible effect."""
import pytest
from langgraph.types import Command

from s0_spike.errors import NotAwaitingApproval, RunAlreadyFinalized, RunExists, RunNotFound
from s0_spike.ledger import Ledger

from conftest import approve, cli


def test_execution_pauses_before_final_approval_with_no_effect(orch):
    st = orch.start("r1", "small")
    assert st["phase"] == "awaiting_approval" and st["outcome"] is None
    assert orch.ledger.publication("r1") is None and orch.ledger.calls("r1", "publish_attempt") == []


def test_approval_and_rejection_produce_distinct_outcomes(make_orch):
    a, r = make_orch(name="a"), make_orch(name="r")
    a.start("r1", "small"); r.start("r1", "small")
    sa, sr = approve(a, "r1", "approve"), approve(r, "r1", "reject")
    assert (sa["phase"], sr["phase"]) == ("approved", "rejected")
    assert sa["outcome"]["decision"] == "approved" and sr["outcome"]["decision"] == "rejected"
    pa, pr = a.ledger.publication("r1"), r.ledger.publication("r1")
    assert pa["decision"] == "approved" and pa["payload"] and pr["decision"] == "rejected" and pr["payload"] is None


def test_approval_and_rejection_across_separate_processes(tmp_path):
    for rid, decision in (("ra", "approve"), ("rr", "reject")):
        cli(tmp_path, "start", "--run-id", rid, "--scenario", "small")
        _, st = cli(tmp_path, "status", "--run-id", rid)  # a different process than the one that paused
        code, end = cli(tmp_path, "resume", "--run-id", rid, "--decision", decision, "--reviewer", "me",
                        "--report-digest", st["pending_report_digest"])
        assert code == 0 and end["phase"] == {"approve": "approved", "reject": "rejected"}[decision]


@pytest.mark.parametrize("answer", [
    {"decision": "approve", "reviewer": "x", "report_digest": "stale"},
    {"decision": "yes", "reviewer": "x"},
    {"decision": "approve", "reviewer": "", "report_digest": None},
    "approve",
])
def test_invalid_decision_never_approves(orch, answer):
    st = orch.start("r1", "small")
    orch._run(Command(resume=answer), "r1")  # bypass the facade's argument shape on purpose
    after = orch.status("r1")
    assert after["phase"] == "awaiting_approval" and orch.ledger.publication("r1") is None
    assert approve(orch, "r1")["phase"] == "approved"  # the gate is still usable with a valid answer


def test_finalized_run_cannot_be_reopened_through_normal_calls(orch):
    orch.start("r1", "small"); approve(orch, "r1")
    before, pub = orch.graph.get_state(orch._cfg("r1")).values, orch.ledger.publication("r1")
    for call in (lambda: orch.start("r1", "baseline"), lambda: orch.recover("r1"),
                 lambda: orch.resume("r1", "reject", "x", before["report"]["digest"])):
        with pytest.raises(RunAlreadyFinalized):
            call()
    assert orch.graph.get_state(orch._cfg("r1")).values == before and orch.ledger.publication("r1") == pub


def test_finalized_run_protected_at_graph_level_too(orch):
    from s0_spike.errors import RunAlreadyInitialized
    orch.start("r1", "small"); approve(orch, "r1")
    before = orch.graph.get_state(orch._cfg("r1")).values
    with pytest.raises(RunAlreadyInitialized):
        orch.graph.invoke({"run_id": "r1", "scenario": "baseline"}, orch._cfg("r1"))  # raw input, bypassing the facade
    assert orch.graph.get_state(orch._cfg("r1")).values == before  # identity fields are write-once (keep_first)
    assert orch.status("r1")["phase"] == "approved"


def test_start_on_existing_paused_run_is_refused(orch):
    orch.start("r1", "small")
    with pytest.raises(RunExists):
        orch.start("r1", "small")
    assert orch.status("r1")["phase"] == "awaiting_approval"  # the pause was not disturbed


def test_unknown_run_and_wrong_phase(orch):
    with pytest.raises(RunNotFound):
        orch.resume("nope", "approve", "x", "d")
    with pytest.raises(RunNotFound):
        orch.recover("nope")
    orch.start("r1", "small")
    with pytest.raises(NotAwaitingApproval):
        orch.recover("r1")


def test_irreversible_effect_runs_once_when_finalize_is_killed_and_replayed(tmp_path):
    cli(tmp_path, "start", "--run-id", "run-a", "--scenario", "crash_in_finalize")
    _, st = cli(tmp_path, "status", "--run-id", "run-a")
    code, _ = cli(tmp_path, "resume", "--run-id", "run-a", "--decision", "approve", "--reviewer", "r",
                  "--report-digest", st["pending_report_digest"])
    assert code == 137  # killed after the effect committed, before the checkpoint
    led = Ledger(tmp_path / "ledger.sqlite")
    assert led.publication("run-a")["decision"] == "approved"
    _, mid = cli(tmp_path, "status", "--run-id", "run-a")
    assert mid["phase"] == "incomplete" and mid["next"] == ["finalize_approved"]
    code, end = cli(tmp_path, "recover", "--run-id", "run-a")
    assert code == 0 and end["phase"] == "approved"
    assert len(led.calls("run-a", "publish_attempt")) == 2  # the node did run twice...
    assert len(led.calls("run-a", "publish_performed")) == 1  # ...the effect happened once
