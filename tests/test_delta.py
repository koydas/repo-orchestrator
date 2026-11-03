"""Base-against-head classification: every combination of evidence on the two sides (docs/workflows.md → W2)."""

from __future__ import annotations

import pytest

from repo_orchestrator.delta import DeltaLabel, classify_delta
from repo_orchestrator.models import Outcome, Status

S = Status
ENUM = {"P5": ("ref:",)}


def out(status, subject="claim:x", fp=None, policy="P1", rel="R1"):
    fp = fp if fp is not None else ("fp-" + subject if status is S.INCONSISTENT else None)
    return Outcome(policy, "1.0.0", rel, subject, status, "", fingerprint=fp)


def labels(base, head, enumerated=ENUM):
    return {(d.policy_id, d.subject): d.label for d in classify_delta(base, head, enumerated)}


# (base status, head status) of the same subject → label of the finding they involve
HEAD_FINDING = [
    (S.INCONSISTENT, DeltaLabel.PRE_EXISTING),
    (S.CONSISTENT, DeltaLabel.INTRODUCED),
    (S.EXCEPTED, DeltaLabel.INTRODUCED),
    (S.POLICY_CONFLICT, DeltaLabel.INTRODUCED),
    (S.CANNOT_EVALUATE, DeltaLabel.UNDETERMINED),
    (S.FAILED, DeltaLabel.UNDETERMINED),
    (S.POLICY_CLARIFICATION, DeltaLabel.UNDETERMINED),
]
BASE_FINDING = [
    (S.INCONSISTENT, DeltaLabel.PRE_EXISTING),
    (S.CONSISTENT, DeltaLabel.RESOLVED),
    (S.EXCEPTED, DeltaLabel.RESOLVED),
    (S.POLICY_CONFLICT, DeltaLabel.RESOLVED),
    (S.CANNOT_EVALUATE, DeltaLabel.UNDETERMINED),
    (S.FAILED, DeltaLabel.UNDETERMINED),
    (S.POLICY_CLARIFICATION, DeltaLabel.UNDETERMINED),
]


@pytest.mark.parametrize(("base_status", "expected"), HEAD_FINDING)
def test_a_finding_on_head_is_labelled_by_what_base_could_say(base_status, expected):
    assert labels([out(base_status)], [out(S.INCONSISTENT)]) == {("P1", "claim:x"): expected}


@pytest.mark.parametrize(("head_status", "expected"), BASE_FINDING)
def test_a_finding_on_base_is_labelled_by_what_head_can_say(head_status, expected):
    assert labels([out(S.INCONSISTENT)], [out(head_status)]) == {("P1", "claim:x"): expected}


def test_missing_evidence_on_either_side_is_never_introduced_or_resolved():
    for status in (S.CANNOT_EVALUATE, S.FAILED):
        assert labels([out(status)], [out(S.INCONSISTENT)])[("P1", "claim:x")] is DeltaLabel.UNDETERMINED
        assert labels([out(S.INCONSISTENT)], [out(status)])[("P1", "claim:x")] is DeltaLabel.UNDETERMINED


def test_two_consistent_sides_have_no_delta():
    assert labels([out(S.CONSISTENT)], [out(S.CONSISTENT)]) == {}


def test_identity_is_the_fingerprint_so_a_moved_number_stays_pre_existing():
    base = out(S.INCONSISTENT, fp="same")
    head = out(S.INCONSISTENT, fp="same")
    assert labels([base], [head]) == {("P1", "claim:x"): DeltaLabel.PRE_EXISTING}


def test_a_different_fingerprint_on_the_same_subject_is_a_new_finding_and_the_old_one_resolved():
    deltas = classify_delta([out(S.INCONSISTENT, fp="old")], [out(S.INCONSISTENT, fp="new")], ENUM)
    assert sorted(d.label.value for d in deltas) == ["introduced", "resolved"]


class TestSubjectsThatAppearOrVanish:
    def test_a_new_member_of_an_enumerated_family_is_introduced_when_the_policy_completed_on_base(self):
        base = [out(S.CONSISTENT, "ref:blob:x:a.md", policy="P5")]
        head = [out(S.CONSISTENT, "ref:blob:x:a.md", policy="P5"), out(S.INCONSISTENT, "ref:blob:x:b.md", policy="P5")]
        assert labels(base, head) == {("P5", "ref:blob:x:b.md"): DeltaLabel.INTRODUCED}

    def test_a_new_subject_is_undetermined_when_base_could_not_enumerate_the_family(self):
        base = [out(S.CANNOT_EVALUATE, "*", policy="P5")]
        head = [out(S.INCONSISTENT, "ref:blob:x:b.md", policy="P5")]
        assert labels(base, head) == {("P5", "ref:blob:x:b.md"): DeltaLabel.UNDETERMINED}

    def test_a_new_subject_of_a_static_family_is_undetermined_not_introduced(self):
        assert labels([out(S.CONSISTENT, "claim:a")], [out(S.INCONSISTENT, "claim:b")]) == {
            ("P1", "claim:b"): DeltaLabel.UNDETERMINED
        }

    def test_a_new_subject_with_no_base_outcome_at_all_is_undetermined(self):
        assert labels([], [out(S.INCONSISTENT, "ref:blob:x:b.md", policy="P5")]) == {
            ("P5", "ref:blob:x:b.md"): DeltaLabel.UNDETERMINED
        }

    def test_a_vanished_enumerated_subject_is_resolved_when_the_policy_completed_on_head(self):
        base = [out(S.INCONSISTENT, "ref:blob:x:a.md", policy="P5")]
        head = [out(S.CONSISTENT, "ref:blob:x:other.md", policy="P5")]
        assert labels(base, head) == {("P5", "ref:blob:x:a.md"): DeltaLabel.RESOLVED}

    def test_a_vanished_subject_is_undetermined_when_head_failed_to_evaluate_the_relationship(self):
        base = [out(S.INCONSISTENT, "ref:blob:x:a.md", policy="P5")]
        head = [
            out(S.CONSISTENT, "ref:blob:x:other.md", policy="P5"),
            out(S.CANNOT_EVALUATE, "ref:blob:x:c.md", policy="P5"),
        ]
        assert labels(base, head) == {("P5", "ref:blob:x:a.md"): DeltaLabel.UNDETERMINED}

    def test_a_vanished_static_subject_is_undetermined_it_is_just_not_there(self):
        assert labels([out(S.INCONSISTENT, "claim:a")], [out(S.CONSISTENT, "claim:b")]) == {
            ("P1", "claim:a"): DeltaLabel.UNDETERMINED
        }

    def test_a_policy_level_cannot_evaluate_on_head_leaves_every_base_finding_undetermined(self):
        base = [out(S.INCONSISTENT, "claim:a"), out(S.INCONSISTENT, "claim:b")]
        head = [out(S.CANNOT_EVALUATE, "*")]
        assert set(labels(base, head).values()) == {DeltaLabel.UNDETERMINED}


def test_results_are_sorted_and_stable():
    base = [out(S.INCONSISTENT, "claim:b"), out(S.INCONSISTENT, "claim:a")]
    head = [out(S.INCONSISTENT, "claim:a"), out(S.INCONSISTENT, "claim:b")]
    first = [(d.subject, d.label) for d in classify_delta(base, head, ENUM)]
    assert first == [(d.subject, d.label) for d in classify_delta(list(reversed(base)), list(reversed(head)), ENUM)]
    assert [s for s, _ in first] == ["claim:a", "claim:b"]


def test_the_outcome_of_a_delta_is_the_head_finding_or_else_the_base_one():
    resolved = classify_delta([out(S.INCONSISTENT)], [out(S.CONSISTENT)], ENUM)[0]
    assert resolved.outcome.status is S.INCONSISTENT and resolved.head is not None
    introduced = classify_delta([out(S.CONSISTENT)], [out(S.INCONSISTENT)], ENUM)[0]
    assert introduced.outcome is introduced.head
