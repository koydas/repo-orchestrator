"""Pure functions: output validation, outcome classification, reducers. No agent claim is trusted."""
import pytest

from s0_spike.agent import Budget, BudgetExhausted, MalformedOutput, classify, fact_check, validate_output
from s0_spike.errors import ReducerConflict
from s0_spike.models import merge_denials, merge_results
from s0_spike.scenarios import load_scenario


def _c01():
    sc = load_scenario("baseline")
    return sc, next(c for c in sc.candidates if c["id"] == "c01"), sc.responses["c01"][0]["output"]


@pytest.mark.parametrize("raw", [
    "text", None, [], {}, {"candidate_id": "c01"},
    {"candidate_id": "c01", "claim": "maybe", "evidence": []},
    {"candidate_id": "other", "claim": "drift", "evidence": []},
    {"candidate_id": "c01", "claim": "drift", "evidence": "x"},
    {"candidate_id": "c01", "claim": "drift", "evidence": [{"repo": "a"}]},
    {"candidate_id": "c01", "claim": "drift", "evidence": [{"repo": "a", "sha": "s", "path": "p", "quote": ""}]},
    {"candidate_id": "c01", "claim": "drift", "evidence": [{"repo": "a", "sha": "s", "path": "p", "quote": 3}]},
])
def test_malformed_outputs_are_rejected(raw):
    _, cand, _ = _c01()
    with pytest.raises(MalformedOutput):
        validate_output(raw, cand)


def test_self_declared_outcome_is_ignored():
    sc, cand, out = _c01()
    claim, ev = validate_output({**out, "outcome": "refuted", "confidence": 1.0}, cand)
    assert classify(cand, claim, ev, sc.snapshots, sc.pins)[0] == "verified"  # by code, not by the field


def test_verified_requires_code_check_and_every_site_covered():
    sc, cand, out = _c01()
    claim, ev = validate_output(out, cand)
    assert classify(cand, claim, ev, sc.snapshots, sc.pins)[:2] == ("verified", "claim_confirmed_by_check")
    assert classify(cand, claim, ev[:1], sc.snapshots, sc.pins)[:2] == ("insufficient_evidence", "missing_evidence")


def test_wrong_sha_or_altered_quote_is_unverified():
    sc, cand, out = _c01()
    claim, ev = validate_output(out, cand)
    bad_sha = [{**ev[0], "sha": "0" * 40}, ev[1]]
    bad_quote = [{**ev[0], "quote": ev[0]["quote"] + " extra"}, ev[1]]
    for bad in (bad_sha, bad_quote):
        assert classify(cand, claim, bad, sc.snapshots, sc.pins)[:2] == ("insufficient_evidence", "unverified_quote")


def test_claim_contradicted_by_deterministic_check():
    sc = load_scenario("baseline")
    cand = next(c for c in sc.candidates if c["id"] == "c04")  # both repos say 5: no drift
    assert fact_check(cand, sc.snapshots, sc.pins) == "no_drift"
    ev = [{"repo": s["repo"], "sha": sc.pins[s["repo"]], "path": s["path"],
           "quote": sc.snapshots[f"{s['repo']}@{sc.pins[s['repo']]}:{s['path']}"]} for s in cand["sites"]]
    assert classify(cand, "drift", ev, sc.snapshots, sc.pins)[:2] == ("insufficient_evidence", "claim_contradicted_by_check")
    assert classify(cand, "no_drift", ev, sc.snapshots, sc.pins)[0] == "verified"


def test_qualitative_claim_is_never_verified():
    sc = load_scenario("baseline")
    cand = next(c for c in sc.candidates if c["id"] == "c03")
    claim, ev = validate_output(sc.responses["c03"][0]["output"], cand)
    assert classify(cand, claim, ev, sc.snapshots, sc.pins)[:2] == ("supported", "no_mechanical_check")


def test_budget_is_enforced_in_code():
    b = Budget(5)
    b.spend(3)
    with pytest.raises(BudgetExhausted):
        b.spend(3)


def test_merge_results_idempotent_replay_and_conflict():
    r = {"c1#1": {"candidate_id": "c1", "attempt": 1, "outcome": "verified"}}
    assert merge_results(r, dict(r)) == r  # re-executed task writing the same result
    with pytest.raises(ReducerConflict):
        merge_results(r, {"c1#1": {"candidate_id": "c1", "attempt": 1, "outcome": "failed"}})
    merged = merge_results(r, {"c2#1": {"candidate_id": "c2", "attempt": 1, "outcome": "failed"}})
    assert set(merged) == {"c1#1", "c2#1"} and set(r) == {"c1#1"}  # inputs not mutated


def test_merge_denials_first_reason_stands():
    assert merge_denials({"c1": {"reason": "a"}}, {"c1": {"reason": "b"}, "c2": {"reason": "c"}}) == \
        {"c1": {"reason": "a"}, "c2": {"reason": "c"}}
