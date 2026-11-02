"""ADR-0008 and ADR-0006 §5-6: idempotent persistence, stable fingerprints, honest history, deterministic reports."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from repo_orchestrator.collector.overlay import delete_path, replace_text
from repo_orchestrator.models import Status
from repo_orchestrator.report import render_markdown
from repo_orchestrator.store import RunStore
from support import AS_OF, one, scan

PROFILE, AP = "koydas/koydas", "koydas/agent-patterns"
FIX_D1 = replace_text(PROFILE, "README.md", "17 multi-agent patterns", "20 multi-agent patterns")
BREAK_CLAIM = replace_text(PROFILE, "README.md", "17 multi-agent patterns", "seventeen multi-agent patterns")
T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


@pytest.fixture
def store():
    s = RunStore(":memory:")
    yield s
    s.close()


# ---- determinism ---------------------------------------------------------------------------------------------------


def test_identical_inputs_give_an_identical_normalized_result_markdown_and_json():
    a, b = scan(), scan()
    assert a.semantic == b.semantic and a.semantic["result_digest"] == b.semantic["result_digest"]
    assert render_markdown(a.semantic) == render_markdown(b.semantic)
    assert json.dumps(a.semantic, sort_keys=True) == json.dumps(b.semantic, sort_keys=True)
    assert a.execution["run_id"] != b.execution["run_id"]  # volatile metadata is separate and never in the digest


def test_the_semantic_report_holds_no_timestamp_run_id_or_machine_specific_value():
    text = json.dumps(scan().semantic)
    for banned in ("run_id", "started_at", "duration_ms", "/home/", "/tmp/"):
        assert banned not in text


def test_the_result_digest_changes_when_and_only_when_the_inputs_or_results_change():
    base = scan().semantic["result_digest"]
    assert scan([FIX_D1]).semantic["result_digest"] != base
    assert scan(as_of=AS_OF.replace(day=3)).semantic["result_digest"] != base  # as_of is a declared input
    assert scan(scope=("adl", "adt")).semantic["result_digest"] != base


def test_outcomes_are_ordered_deterministically_regardless_of_policy_registration_order():
    outcomes = scan().result.outcomes
    keys = [(o.relationship_id, o.policy_id, o.subject) for o in outcomes]
    assert keys == sorted(keys)


# ---- persistence ---------------------------------------------------------------------------------------------------


def test_recording_the_same_run_twice_is_idempotent(store):
    r = scan()
    first = store.record(r.semantic, r.result.outcomes, now=T0)
    again = store.record(r.semantic, r.result.outcomes, now=T0.replace(hour=13))
    assert first.new and not again.new and first.run_key == again.run_key
    assert store.run_count() == 1 and store.run(first.run_key)["executions"] == 2
    assert again.transitions == first.transitions == {"new": sorted(f["fingerprint"] for f in store.findings())}
    assert len(store.findings()) == 2
    row = store.run(first.run_key)
    assert row["run_status"] == "complete" and json.loads(row["policies_json"])[0] == {"id": "P1", "version": "1.0.0"}
    assert row["declarations_digest"] == r.semantic["inputs"]["declarations_digest"] and row["tool_version"]


def test_replaying_an_earlier_run_never_rewrites_history(store):
    d1 = one(scan(), "P1", "R1.ap", "claim:patterns").fingerprint
    for report in (scan(), scan([FIX_D1], as_of=AS_OF.replace(day=3)), scan()):  # A, B, then A replayed
        store.record(report.semantic, report.result.outcomes, now=T0)
    assert [h["event"] for h in store.history(d1)] == ["new", "resolved"] and store.finding(d1)["state"] == "resolved"


def test_a_second_run_of_unchanged_inputs_with_a_new_date_marks_findings_unchanged_not_new(store):
    store.record(scan().semantic, scan().result.outcomes, now=T0)
    later = scan(as_of=AS_OF.replace(day=9))
    rec = store.record(later.semantic, later.result.outcomes, now=T0.replace(day=9))
    assert rec.new and set(rec.transitions) == {"unchanged"} and len(rec.transitions["unchanged"]) == 2


def test_finding_lifecycle_new_resolved_regressed(store):
    d1 = one(scan(), "P1", "R1.ap", "claim:patterns").fingerprint
    # later runs carry later inputs (in practice a new HEAD sha; here a later as_of) so none is a replay of an earlier run
    steps = [
        (scan(), "new"),
        (scan([FIX_D1], as_of=AS_OF.replace(day=3)), "resolved"),
        (scan(as_of=AS_OF.replace(day=4)), "regressed"),
    ]
    for i, (report, event) in enumerate(steps):
        rec = store.record(report.semantic, report.result.outcomes, now=T0.replace(day=2 + i))
        assert d1 in rec.transitions[event], (i, rec.transitions)
    row = store.finding(d1)
    assert row["state"] == "open" and row["regression_count"] == 1
    assert [h["event"] for h in store.history(d1)] == ["new", "resolved", "regressed"]


def test_cannot_evaluate_never_resolves_a_finding(store):
    d1 = one(scan(), "P1", "R1.ap", "claim:patterns").fingerprint
    store.record(scan().semantic, scan().result.outcomes, now=T0)
    partial = scan([BREAK_CLAIM])
    assert partial.semantic["run_status"] == "partial"
    rec = store.record(partial.semantic, partial.result.outcomes, now=T0.replace(day=3))
    assert d1 in rec.transitions["not_evaluated"] and "resolved" not in rec.transitions
    assert store.finding(d1)["state"] == "open"
    recovered = scan(as_of=AS_OF.replace(day=4))
    rec = store.record(recovered.semantic, recovered.result.outcomes, now=T0.replace(day=4))
    assert d1 in rec.transitions["unchanged"] and store.finding(d1)["regression_count"] == 0


def test_a_failed_collection_is_not_a_resolution_either(store):
    d1 = one(scan(), "P1", "R1.ap", "claim:patterns").fingerprint
    store.record(scan().semantic, scan().result.outcomes, now=T0)
    gone = scan([delete_path(AP, "README.md"), delete_path(AP, "patterns")])
    rec = store.record(gone.semantic, gone.result.outcomes, now=T0.replace(day=3))
    assert store.finding(d1)["state"] in {"open"} or d1 in rec.transitions.get("unchanged", []) + rec.transitions.get(
        "not_evaluated", []
    )
    assert "resolved" not in rec.transitions or d1 not in rec.transitions["resolved"]


def test_an_excepted_finding_keeps_its_fingerprint_and_returns_to_open_when_the_exception_lapses(store):
    from datetime import date

    from test_engine import exception, with_exceptions

    d1 = one(scan(), "P1", "R1.ap", "claim:patterns").fingerprint
    decl = with_exceptions(exception(review_by=date(2026, 11, 20)))
    a = scan(decl=decl)
    store.record(a.semantic, a.result.outcomes, now=T0)
    assert store.finding(d1)["state"] == "excepted"
    b = scan(decl=decl, as_of=date(2026, 12, 1))
    rec = store.record(b.semantic, b.result.outcomes, now=T0.replace(day=3))
    assert d1 in rec.transitions["state_changed"] and store.finding(d1)["state"] == "open"


def test_partial_and_complete_runs_with_the_same_inputs_are_distinct_records(store):
    complete, partial = scan(), scan([BREAK_CLAIM])
    store.record(complete.semantic, complete.result.outcomes, now=T0)
    store.record(partial.semantic, partial.result.outcomes, now=T0)
    assert store.run_count() == 2
    from repo_orchestrator.models import digest

    assert store.divergent_complete_runs(digest(complete.semantic["inputs"])) == []


def test_a_determinism_violation_is_detected(store):
    from repo_orchestrator.models import digest

    a = scan()
    store.record(a.semantic, a.result.outcomes, now=T0)
    forged = json.loads(json.dumps(a.semantic))
    forged["result_digest"] = "f" * 64
    store.record(forged, a.result.outcomes, now=T0)
    assert store.divergent_complete_runs(digest(a.semantic["inputs"]))


def test_run_scan_raises_on_a_determinism_violation(store):
    from repo_orchestrator.engine import Engine, RunOptions
    from repo_orchestrator.runner import DeterminismError, run_scan
    from support import baseline_revisions, baseline_source, load_decl

    class Drifting:
        def __init__(self):
            self.flip = False

        def __getattr__(self, name):
            return getattr(self.inner, name)

    d, engine, calls = load_decl(), None, []
    engine = Engine(d)
    options = RunOptions(revisions=baseline_revisions(d), as_of=AS_OF)
    first = run_scan(engine, baseline_source(), options, store=store)
    assert first.record and first.record.new
    # plant a different complete result for the same inputs, as a nondeterministic evaluation would produce
    forged = json.loads(json.dumps(first.semantic))
    forged["result_digest"] = "e" * 64
    store.record(forged, first.result.outcomes, now=T0)
    with pytest.raises(DeterminismError):
        run_scan(engine, baseline_source(), options, store=store)
    del calls


def test_store_survives_reopening_the_file(tmp_path):
    path = tmp_path / "state" / "s.sqlite"
    r = scan()
    s1 = RunStore(path)
    key = s1.record(r.semantic, r.result.outcomes, now=T0).run_key
    s1.close()
    s2 = RunStore(path)
    assert s2.run(key) is not None and len(s2.findings()) == 2
    assert not s2.record(r.semantic, r.result.outcomes, now=T0).new
    s2.close()


def test_every_confirmed_finding_reproduces_from_the_stored_outcome(store):
    r = scan()
    store.record(r.semantic, r.result.outcomes, now=T0)
    rows = store._db.execute("SELECT body_json FROM outcomes WHERE status = ?", (Status.INCONSISTENT.value,)).fetchall()
    bodies = [json.loads(row[0]) for row in rows]
    assert len(bodies) == 2 and all(b["evidence"] and b["fingerprint"] for b in bodies)
