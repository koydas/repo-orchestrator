"""Graph routing and state transitions."""
from langgraph.checkpoint.memory import InMemorySaver

from s0_spike.graph import build_graph
from s0_spike.ledger import Ledger
from s0_spike.limits import DEFAULT_LIMITS

from conftest import approve, by_id


def test_phase_transitions_small(orch):
    assert orch.status("r1")["phase"] == "not_found"
    st = orch.start("r1", "small")
    assert st["phase"] == "awaiting_approval" and st["next"] == ["approval_gate"]
    assert st["spent"] == 3 and st["rounds"] == 1
    assert approve(orch, "r1")["phase"] == "approved"


def test_node_order_small(tmp_path):
    g = build_graph(InMemorySaver(), Ledger(tmp_path / "l.sqlite"))
    cfg = {"configurable": {"thread_id": "t"}}
    order = []
    for chunk in g.stream({"run_id": "t", "scenario": "small"}, cfg, stream_mode="updates"):
        order += [k for k in chunk if k != "__interrupt__"]
    assert order == ["init_run", "freeze_input", "dispatch"] + ["investigate"] * 3 + ["collect", "aggregate"]
    # approval_gate never completes in this stream: it is paused on interrupt(), and no finalize node ran
    assert g.get_state(cfg).next == ("approval_gate",)


def test_retry_loop_routes_back_to_dispatch(orch):
    st = orch.start("r1", "baseline")
    assert st["rounds"] == 2  # round 1: nine attempts; round 2: the three retriable failures
    f = by_id(st["report"])
    assert [f[c]["attempt"] for c in ("c05", "c06", "c08")] == [2, 2, 2]
    assert f["c01"]["attempt"] == 1  # completed candidates are not dispatched again
    assert st["spent"] == 12


def test_start_input_exposes_only_run_request(tmp_path):
    g = build_graph(InMemorySaver(), Ledger(tmp_path / "l.sqlite"))
    cfg = {"configurable": {"thread_id": "t"}}
    g.invoke({"run_id": "t", "scenario": "small", "spent": -100, "rounds": -5, "status": "approved",
              "report": {"x": 1}, "decision": {"decision": "approve"}}, cfg)
    v = g.get_state(cfg).values
    assert v["spent"] == 3 and v["status"] == "awaiting_approval" and v["report"]["findings"]
    assert "decision" not in v


def test_invalid_requests_are_rejected_before_any_work(orch):
    import pytest
    from s0_spike.errors import InvalidRunRequest
    for rid in ("", "-rf", "../x", "a b"):
        with pytest.raises(InvalidRunRequest):
            orch.start(rid, "small")
    with pytest.raises(Exception):
        orch.start("r1", "no_such_scenario")
    assert orch.ledger.spent("r1") == 0
