"""Experiment C: failure isolation. Recovery rules are asserted explicitly.

| Fault              | Recovery                                            | Final outcome                       |
| agent exception    | retried up to max_attempts                          | verified if a retry works, else failed |
| malformed output   | retried (counts as an attempt)                      | verified if a retry works, else failed |
| missing evidence   | not retried (a retry would repeat the same output)  | insufficient_evidence               |
| budget exhaustion  | not retried                                         | insufficient_evidence               |
| process killed     | recover(): same attempt re-runs, siblings do not    | like any other candidate            |
"""
from conftest import by_id

SUCCESS = ("verified", "supported")


def _f(o):
    return by_id(o.start("r1", "baseline")["report"])


def test_agent_exception_transient_then_persistent(orch):
    f = _f(orch)
    assert (f["c06"]["outcome"], f["c06"]["attempt"]) == ("verified", 2)  # recovered on retry
    assert (f["c05"]["outcome"], f["c05"]["reason"], f["c05"]["attempt"]) == ("failed", "agent_exception", 2)
    assert f["c05"]["evidence"] == [] and f["c05"]["claim"] == ""


def test_malformed_result_retried_then_classified(orch):
    f = _f(orch)
    assert (f["c08"]["outcome"], f["c08"]["attempt"]) == ("verified", 2)
    state = orch.graph.get_state(orch._cfg("r1")).values
    first = state["results"]["c08#1"]
    assert (first["outcome"], first["reason"]) == ("failed", "malformed_output")  # the failed attempt stays in history


def test_missing_and_fabricated_evidence_are_not_success(orch):
    f = _f(orch)
    assert (f["c07"]["outcome"], f["c07"]["reason"], f["c07"]["attempt"]) == ("insufficient_evidence", "missing_evidence", 1)
    assert (f["c04"]["outcome"], f["c04"]["reason"]) == ("insufficient_evidence", "unverified_quote")
    assert f["c04"]["evidence"] == [f["c04"]["evidence"][0]] and all("99" not in e["quote"] for e in f["c04"]["evidence"])


def test_budget_exhaustion_is_insufficient_and_not_retried(orch):
    f = _f(orch)
    assert (f["c09"]["outcome"], f["c09"]["reason"], f["c09"]["attempt"]) == ("insufficient_evidence", "budget_exhausted", 1)


def test_agent_self_declared_outcome_never_counts(orch):
    st = orch.start("r1", "baseline")
    state = orch.graph.get_state(orch._cfg("r1")).values
    assert state["results"]["c03#1"]["agent_asserted_outcome"] == "verified"
    assert by_id(st["report"])["c03"]["outcome"] == "supported"
    assert state["results"]["c04#1"]["agent_asserted_outcome"] == "verified"
    assert by_id(st["report"])["c04"]["outcome"] == "insufficient_evidence"


def test_every_success_is_backed_by_verified_evidence(orch):
    for f in _f(orch).values():
        if f["outcome"] in SUCCESS:
            assert f["attempt"] >= 1 and f["evidence"], f
        if f["outcome"] == "failed":
            assert not f["evidence"]


def test_counts_match_findings(orch):
    rep = orch.start("r1", "baseline")["report"]
    assert rep["counts"] == {"verified": 4, "supported": 1, "insufficient_evidence": 3, "failed": 1}
    assert sum(rep["counts"].values()) == len(rep["findings"]) == 9


def test_interrupted_execution_is_never_reported_as_success(orch):
    # In-process stand-in for a kill: stop the graph before the fan-out, then inspect. The real hard-kill
    # variant (separate process, os._exit) is in test_exp_a_persistence.
    from langgraph.checkpoint.memory import InMemorySaver
    from s0_spike.graph import build_graph
    g = build_graph(InMemorySaver(), orch.ledger, interrupt_before=["collect"])
    cfg = {"configurable": {"thread_id": "t"}}
    g.invoke({"run_id": "t", "scenario": "small"}, cfg)
    s = g.get_state(cfg)
    assert s.next == ("collect",) and s.values["status"] == "running" and "report" not in s.values
    g.invoke(None, cfg)
    assert g.get_state(cfg).values["status"] == "awaiting_approval"
