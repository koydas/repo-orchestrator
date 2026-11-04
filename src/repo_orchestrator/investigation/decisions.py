"""W4 human decisions on investigation results (ADR-0015 §7). Pure rules + recording in the ``inv_*`` namespace.

A decision is bound to the digest of the evidence the human saw; if the evidence changes the decision is *stale* and not
applied. A human can validate or reject a ``needs_validation`` claim, dismiss an undecided one, acknowledge a confirmed
one; a human cannot promote an ``unverified`` or ``insufficient_evidence`` candidate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .domain import CandState, Validation
from .store import InvestigationStore

# action → states it applies to
APPLIES: dict[str, frozenset[str]] = {
    "validate": frozenset({CandState.NEEDS_VALIDATION}),
    "reject": frozenset({CandState.NEEDS_VALIDATION}),
    "dismiss": frozenset({CandState.INSUFFICIENT_EVIDENCE, CandState.NEEDS_VALIDATION, CandState.POLICY_QUESTION}),
    "acknowledge": frozenset({CandState.CONFIRMED}),
}


class DecisionError(Exception):
    """A decision the rules refuse (unknown candidate, wrong state, no reason)."""


def effective(result: Mapping[str, Any], decisions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The result after the human decisions that match its evidence, in recording order. Adds ``decision`` (the last applied
    one or None), ``acknowledged`` and ``stale_decisions``. Never touches a result the rules do not allow it to."""
    out = dict(result)
    out["decision"] = None
    out["acknowledged"] = False
    out["stale_decisions"] = []
    state = CandState(result["state"])
    for d in decisions:
        if d["evidence_digest"] != result["evidence_digest"]:
            out["stale_decisions"].append(_public(d))
            continue
        action = d["action"]
        if action not in APPLIES or state not in APPLIES[action]:
            out["stale_decisions"].append(_public(d))
            continue
        out["decision"] = _public(d)
        if action == "validate":
            state, out["validation"] = CandState.CONFIRMED, Validation.BY_HUMAN.value
        elif action in ("reject", "dismiss"):
            state = CandState.DISMISSED
        elif action == "acknowledge":
            out["acknowledged"] = True
    out["state"] = state.value
    return out


def _public(d: Mapping[str, Any]) -> dict[str, Any]:
    return {k: d[k] for k in ("id", "action", "reason", "actor", "decided_at", "evidence_digest")}


def record(
    store: InvestigationStore, run_id: str, cand_key: str, action: str, reason: str, actor: str
) -> dict[str, Any]:
    if action not in APPLIES:
        raise DecisionError(f"unknown action {action!r} (validate, reject, dismiss, acknowledge)")
    if not reason.strip():
        raise DecisionError("a reason is required")
    if not actor.strip():
        raise DecisionError("an actor is required")
    result = store.results(run_id).get(cand_key)
    if result is None:
        raise DecisionError(f"run {run_id} has no result for candidate {cand_key}")
    current = effective(result, store.decisions(cand_key))
    if CandState(current["state"]) not in APPLIES[action]:
        raise DecisionError(f"cannot {action} a candidate in state {current['state']}")
    if action == "validate" and result.get("evidence_status") not in ("supported", "verified"):
        raise DecisionError("only a candidate whose quotes are all verified can be validated")
    store.add_decision(cand_key, result["evidence_digest"], action, reason.strip(), actor.strip())
    return effective(result, store.decisions(cand_key))


def pending_triage(store: InvestigationStore, run_id: str) -> list[str]:
    """Candidates that must be decided by a human before the run can be approved: ``breaks`` claims still in
    ``needs_validation`` (ADR-0015 §4)."""
    out = []
    for key, result in sorted(store.results(run_id).items()):
        if (
            result.get("requires_triage")
            and effective(result, store.decisions(key))["state"] == CandState.NEEDS_VALIDATION
        ):
            out.append(key)
    return out
