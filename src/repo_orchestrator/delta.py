"""Base-against-head classification of findings (W2, docs/workflows.md): the product form of what M1's evaluation did in
test code.

Identity is the finding *fingerprint* (policy, relationship, subject, normalized locators: no value, line or SHA).

* on base and on head                  → ``pre-existing``  (never blamed on the pull request)
* on head only, base evaluated fine    → ``introduced``
* on base only, head evaluated fine    → ``resolved``      (by this pull request)
* anything that depends on a side that could not be evaluated → ``undetermined``

``cannot_evaluate`` and ``failed`` on either side never become ``introduced`` or ``resolved``: absence of evidence is not
evidence of absence (ADR-0006, ADR-0011 §6).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from .lifecycle import UNEVALUATED, subject_vanished
from .models import Outcome, Status


class DeltaLabel(StrEnum):
    INTRODUCED = "introduced"
    PRE_EXISTING = "pre-existing"
    RESOLVED = "resolved"
    UNDETERMINED = "undetermined"


@dataclass(frozen=True)
class Delta:
    label: DeltaLabel
    policy_id: str
    relationship_id: str
    subject: str
    fingerprint: str | None
    reason: str
    base: Outcome | None  # the outcome of the subject on base (None: no outcome)
    head: Outcome | None

    @property
    def outcome(self) -> Outcome:
        """The finding itself: from head when it exists there, otherwise from base (resolved)."""
        found = self.head if self.head is not None and self.head.status is Status.INCONSISTENT else self.base
        assert found is not None
        return found


def _index(
    outcomes: Sequence[Outcome],
) -> tuple[dict[tuple[str, str, str], Outcome], dict[tuple[str, str], set[Status]]]:
    by_subject: dict[tuple[str, str, str], Outcome] = {}
    by_pair: dict[tuple[str, str], set[Status]] = {}
    for o in outcomes:
        by_subject.setdefault(o.subject_key, o)
        by_pair.setdefault((o.policy_id, o.relationship_id), set()).add(o.status)
    return by_subject, by_pair


def _row(o: Outcome) -> dict[str, str]:
    return {"policy_id": o.policy_id, "relationship_id": o.relationship_id, "subject": o.subject}


def classify_delta(
    base: Sequence[Outcome], head: Sequence[Outcome], enumerated: Mapping[str, tuple[str, ...]]
) -> list[Delta]:
    """Deltas for every confirmed finding on either side, sorted by (relationship, policy, subject)."""
    b_subject, b_pair = _index(base)
    h_subject, h_pair = _index(head)
    b_findings = {o.fingerprint: o for o in base if o.status is Status.INCONSISTENT and o.fingerprint}
    h_findings = {o.fingerprint: o for o in head if o.status is Status.INCONSISTENT and o.fingerprint}
    deltas: list[Delta] = []

    for fp, h in h_findings.items():
        if fp in b_findings:
            deltas.append(
                Delta(
                    DeltaLabel.PRE_EXISTING,
                    h.policy_id,
                    h.relationship_id,
                    h.subject,
                    fp,
                    "the same finding is present on base and on head: not caused by this pull request",
                    b_findings[fp],
                    h,
                )
            )
            continue
        b = b_subject.get(h.subject_key)
        if b is not None and b.status in UNEVALUATED | {Status.POLICY_CLARIFICATION}:
            label, why = DeltaLabel.UNDETERMINED, f"base could not be evaluated for this subject ({b.status.value})"
        elif b is not None:
            label, why = DeltaLabel.INTRODUCED, f"on base this subject was {b.status.value}; on head it is inconsistent"
        else:
            pair = b_pair.get((h.policy_id, h.relationship_id), set())
            if (
                pair
                and not pair & UNEVALUATED
                and h.subject.startswith(enumerated.get(h.policy_id, ()))
                and enumerated.get(h.policy_id)
            ):
                label, why = (
                    DeltaLabel.INTRODUCED,
                    "the subject does not exist on base (the policy completed there) and is inconsistent on head",
                )
            else:
                label, why = (
                    DeltaLabel.UNDETERMINED,
                    "base has no outcome for this subject and cannot show it was absent",
                )
        deltas.append(Delta(label, h.policy_id, h.relationship_id, h.subject, fp, why, b, h))

    for fp, b in b_findings.items():
        if fp in h_findings:
            continue
        hd = h_subject.get(b.subject_key)
        if hd is not None and hd.status in UNEVALUATED | {Status.POLICY_CLARIFICATION}:
            label, why = DeltaLabel.UNDETERMINED, f"head could not be evaluated for this subject ({hd.status.value})"
        elif hd is not None:
            label, why = DeltaLabel.RESOLVED, f"on base this subject was inconsistent; on head it is {hd.status.value}"
        else:
            pair = h_pair.get((b.policy_id, b.relationship_id), set())
            if subject_vanished(_row(b), head, pair, set(), enumerated):
                label, why = DeltaLabel.RESOLVED, "the subject no longer exists on head (the policy completed there)"
            else:
                label, why = DeltaLabel.UNDETERMINED, "head has no outcome for this subject and cannot show it is gone"
        deltas.append(Delta(label, b.policy_id, b.relationship_id, b.subject, fp, why, b, hd))

    return sorted(deltas, key=lambda d: (d.relationship_id, d.policy_id, d.subject, d.label.value))
