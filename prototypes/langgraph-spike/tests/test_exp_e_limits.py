"""Experiment E: bounded execution. Limits are code; resumed runs and run input cannot move them."""
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from s0_spike.errors import FrozenInputTampered, RunExists
from s0_spike.graph import build_graph
from s0_spike.ledger import Ledger
from s0_spike.limits import Limits

from conftest import approve, by_id


def test_candidate_cap_defers_explicitly(orch):
    rep = orch.start("r1", "too_many")["report"]
    f = by_id(rep)
    assert len(f) == 12 and rep["counts"]["verified"] == 10
    assert [(c, f[c]["outcome"], f[c]["reason"], f[c]["attempt"]) for c in ("c11", "c12")] == \
        [("c11", "insufficient_evidence", "candidate_cap", 0), ("c12", "insufficient_evidence", "candidate_cap", 0)]
    assert orch.ledger.spent("r1") == 10


def test_attempt_cap_and_investigation_cap(orch):
    st = orch.start("r1", "all_fail")  # 10 always-failing candidates; cap 15 investigations, 2 attempts each
    rep = st["report"]
    assert st["spent"] == orch.ledger.spent("r1") == 15
    assert all(f["attempt"] <= 2 for f in rep["findings"])
    assert sum(f["attempt"] == 2 for f in rep["findings"]) == 5
    assert sorted(rep["denials"]) == ["c06", "c07", "c08", "c09", "c10"]
    assert {d["reason"] for d in rep["denials"].values()} == {"investigation_cap"}
    assert rep["counts"]["failed"] == 10  # denied retries leave the failed result visible, never a success


def test_attempt_cap_one_means_no_retry(make_orch):
    o = make_orch(Limits(max_attempts=1))
    f = by_id(o.start("r1", "baseline")["report"])
    assert o.ledger.spent("r1") == 9 and f["c06"]["outcome"] == "failed" and f["c06"]["attempt"] == 1


def test_step_cap_stops_work_and_records_denials(make_orch):
    o = make_orch(Limits(max_steps=16))
    st = o.start("r1", "baseline")
    assert st["steps"] <= 16 and st["phase"] == "awaiting_approval" and approve(o, "r1")["steps"] <= 16
    rep = st["report"]
    assert {d["reason"] for d in rep["denials"].values()} == {"step_cap"}
    assert len(rep["findings"]) == 9  # every candidate still has a final classification
    never = [f for f in rep["findings"] if f["attempt"] == 0]
    assert all(f["outcome"] == "insufficient_evidence" and f["reason"] == "step_cap" for f in never)


@pytest.mark.parametrize("max_steps", [8, 10, 12, 14, 16, 18, 20, 24])
def test_step_cap_holds_through_finalization(make_orch, max_steps):
    o = make_orch(Limits(max_steps=max_steps), name=f"s{max_steps}")
    st = o.start("r1", "baseline")
    assert len(st["report"]["findings"]) == 9
    end = approve(o, "r1")
    assert end["phase"] == "approved" and end["steps"] <= max_steps, (max_steps, end["steps"])


def test_step_cap_below_the_fixed_path_is_refused():
    with pytest.raises(ValueError):
        Limits(max_steps=7)


def test_recursion_limit_is_a_backstop_not_the_budget(orch):
    assert orch.limits.recursion_limit > orch.limits.max_steps
    assert orch._cfg("r1")["recursion_limit"] == orch.limits.recursion_limit


def test_resumed_run_cannot_buy_budget_with_new_input(orch):
    st = orch.start("r1", "all_fail")
    assert st["spent"] == 15
    with pytest.raises(RunExists):
        orch.start("r1", "all_fail")  # same thread, fresh request: refused before the graph is touched
    after = approve(orch, "r1")
    assert after["spent"] == 15 and orch.ledger.spent("r1") == 15
    assert after["report"]["usage"]["investigations_spent"] == 15


def test_extra_budget_fields_in_start_input_are_ignored(tmp_path):
    g = build_graph(InMemorySaver(), Ledger(tmp_path / "l.sqlite"))
    cfg = {"configurable": {"thread_id": "t"}}
    g.invoke({"run_id": "t", "scenario": "all_fail", "spent": 0, "max_investigations": 999, "limits": {"max_attempts": 99}}, cfg)
    v = g.get_state(cfg).values
    assert v["spent"] == 15 and max(r["attempt"] for r in v["results"].values()) == 2


def _paused_before_dispatch(tmp_path):
    ledger = Ledger(tmp_path / "l.sqlite")
    limits = Limits(max_candidates=2, max_investigations=3, max_attempts=2)
    g = build_graph(InMemorySaver(), ledger, limits, interrupt_before=["dispatch"])
    cfg = {"configurable": {"thread_id": "t"}}
    g.invoke({"run_id": "t", "scenario": "all_fail"}, cfg)  # paused before the first dispatch
    g.invoke(None, cfg)  # round 1: two attempts spent; paused before the retry dispatch
    assert g.get_state(cfg).values["spent"] == 2 and ledger.spent("t") == 2
    return g, cfg, ledger


def test_operator_state_edit_can_reset_the_counter_but_not_the_ledger(tmp_path):
    g, cfg, ledger = _paused_before_dispatch(tmp_path)
    g.update_state(cfg, {"spent": 0})  # OPERATOR-level API: not reachable from the facade or the CLI
    g.invoke(None, cfg)  # the router now believes the budget is untouched and dispatches two retries
    v = g.get_state(cfg).values
    assert ledger.spent("t") == 3  # the worker-side check against the ledger stopped the third spend
    # which of the two parallel retries gets the last spend is a thread race: assert the set, not the order
    assert sorted(r["reason"] for k, r in v["results"].items() if k.endswith("#2")) == ["agent_exception", "investigation_cap"]


def test_operator_edit_of_frozen_candidates_is_detected(tmp_path):
    g, cfg, _ = _paused_before_dispatch(tmp_path)
    cands = g.get_state(cfg).values["candidates"]
    g.update_state(cfg, {"candidates": cands[:1]})
    with pytest.raises(FrozenInputTampered):
        g.invoke(None, cfg)
