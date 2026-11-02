"""Experiment B: fan-out of independent investigations."""
from s0_spike.models import normalize_report

from conftest import by_id

IDS = [f"c{i:02d}" for i in range(1, 7)]


def _done_order(o, run_id):
    return [k.split("#")[0] for _, k in o.ledger.calls(run_id, "done")]


def test_five_plus_candidates_fan_out_with_correct_association(orch):
    st = orch.start("r1", "parallel")
    f = by_id(st["report"])
    assert sorted(f) == IDS and len(st["report"]["findings"]) == 6  # none dropped, none duplicated
    for cid, r in f.items():  # each result's evidence belongs to its own candidate's repositories
        assert all(e["repo"].endswith(f"-{cid}") for e in r["evidence"])
    assert [r["candidate_id"] for r in st["report"]["findings"]] == IDS  # canonical order


def test_failure_does_not_erase_successes(orch):
    f = by_id(orch.start("r1", "parallel")["report"])
    assert f["c03"]["outcome"] == "failed" and f["c03"]["reason"] == "agent_exception" and f["c03"]["attempt"] == 2
    assert [f[c]["outcome"] for c in IDS if c != "c03"] == ["verified"] * 5


def test_aggregation_independent_of_completion_order(make_orch):
    a, b = make_orch(name="a"), make_orch(name="b")
    ra, rb = a.start("r1", "parallel")["report"], b.start("r1", "parallel_rev")["report"]
    order_a, order_b = _done_order(a, "r1"), _done_order(b, "r1")
    assert order_a != sorted(order_a) and order_b != sorted(order_b)  # completion order was not the canonical order
    assert order_a[:6] != order_b[:6]  # and the two runs completed in different orders
    assert normalize_report(ra) == normalize_report(rb) and ra["digest"] == rb["digest"]


def test_each_candidate_investigated_exactly_once_per_attempt(orch):
    orch.start("r1", "parallel")
    keys = [k for _, k in orch.ledger.calls("r1", "agent")]
    assert len(keys) == len(set(keys)) == 7  # 6 first attempts + the retry of c03
    assert orch.ledger.spent("r1") == 7


def test_reducer_keeps_all_parallel_results_in_state(orch):
    orch.start("r1", "parallel")
    state = orch.graph.get_state(orch._cfg("r1")).values
    assert sorted(state["results"]) == sorted([f"{c}#1" for c in IDS] + ["c03#2"])
