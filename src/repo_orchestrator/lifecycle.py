"""Finding lifecycle rules shared by the run store (history across runs) and the PR delta (base against head).

One rule, one place: a finding is *resolved* only when its subject was evaluated and found fine, or when the subject itself
is gone while its policy completed; missing evidence (``cannot_evaluate``, ``failed``) never resolves or introduces anything.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .models import Outcome, Status

UNEVALUATED = frozenset({Status.CANNOT_EVALUATE, Status.FAILED})


def subject_vanished(
    row: Mapping[str, Any],
    outcomes: Sequence[Outcome],
    pair: set[Status],
    statuses: set[Status],
    enumerated: Mapping[str, tuple[str, ...]],
) -> bool:
    """The subject is gone, not merely unevaluated: it belongs to a policy's enumerated family, the family was enumerated
    in this run (an outcome of that family exists for the relationship) and nothing in the pair failed to evaluate.

    ``row`` needs ``policy_id``, ``relationship_id`` and ``subject``; ``pair`` is the set of statuses of the policy on
    the relationship, ``statuses`` the statuses of this very subject."""
    prefixes = enumerated.get(row["policy_id"], ())
    if statuses or not prefixes or not row["subject"].startswith(prefixes):
        return False
    if pair & UNEVALUATED:
        return False
    return any(
        o.policy_id == row["policy_id"]
        and o.relationship_id == row["relationship_id"]
        and o.subject.startswith(prefixes)
        for o in outcomes
    )
