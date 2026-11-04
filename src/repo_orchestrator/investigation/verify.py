"""C3-side checks on an Investigator proposal: quote verification, entailment and the decision rule (ADR-0016).

Everything here is a pure function of (candidate, proposal, pinned reader). Nothing the model says changes a state except
through these checks.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..collector.snapshot import Missing
from ..models import Evidence, Impact, digest
from .budgets import BUDGET
from .domain import (
    Candidate,
    CandState,
    Entailment,
    EvStatus,
    ICause,
    Observation,
    Proposal,
    QuoteCheck,
    QuoteIn,
    Result,
    Validation,
)
from .reader import OUT_OF_SCOPE, PinnedReader

EXTRACTOR = "investigator_quote@1"
_OPS = {
    "lt": operator.lt,
    "le": operator.le,
    "eq": operator.eq,
    "ne": operator.ne,
    "ge": operator.ge,
    "gt": operator.gt,
}


def check_quote(q: QuoteIn, reader: PinnedReader) -> QuoteCheck:
    """``verified`` iff the quote is verbatim inside lines ``line_start..line_end`` of the pinned file. Any other outcome
    is ``unverified`` with a reason; nothing is normalized (case, whitespace, Unicode)."""

    def bad(reason: str) -> QuoteCheck:
        return QuoteCheck("unverified", reason, q)

    if not reader.allowed(q.repo):
        return bad(OUT_OF_SCOPE)
    pinned = reader.sha(q.repo)
    if pinned is None:
        return bad("revision_unresolved")
    if q.sha != pinned:
        return bad("revision_mismatch")
    if len(q.text) < BUDGET.min_quote_chars:
        return bad("too_short")
    if q.line_end < q.line_start or q.line_end - q.line_start + 1 > BUDGET.max_quote_lines:
        return bad("bad_line_range")
    got = reader.text(q.repo, q.path)
    if isinstance(got, Missing):
        return bad("unreadable")
    art, text = got
    lines = text.split("\n")
    if q.line_end > len(lines):
        return bad("not_found_at_locator")
    region = "\n".join(lines[q.line_start - 1 : q.line_end])
    if q.text not in region:
        return bad("not_found_at_locator")
    ev = Evidence(
        "investigator", art.repo, art.slug, art.sha, art.path, f"lines {q.line_start}-{q.line_end}", "text", q.text,
        art.content_sha256, art.blob_sha, q.line_start, q.line_end, EXTRACTOR,
    )  # fmt: skip
    return QuoteCheck("verified", "ok", q, ev)


def check_quotes(quotes: Sequence[QuoteIn], reader: PinnedReader) -> tuple[QuoteCheck, ...]:
    return tuple(check_quote(q, reader) for q in quotes)


def _number(value: str) -> int | None:
    return int(value) if re.fullmatch(r"\d{1,9}", value.strip()) else None


def run_entailment(spec: Entailment, values: Mapping[str, str], checks: Sequence[QuoteCheck]) -> dict[str, Any]:
    """The policy's deterministic test. ``status``: holds | fails | not_evaluable. Both values must be numbers and each
    must occur as a whole number token inside a *verified* quote; otherwise the check is not evaluable (a human decides)."""
    out: dict[str, Any] = {"kind": spec.kind, "test": f"{spec.left} {spec.op} {spec.right}", "status": "not_evaluable"}
    raw = {n: values.get(n) for n in (spec.left, spec.right)}
    nums = {n: _number(v) if v is not None else None for n, v in raw.items()}
    out["values"] = {n: raw[n] for n in raw}
    if any(v is None for v in nums.values()):
        out["detail"] = "a named value is missing or is not a number"
        return out
    texts = [c.quote.text for c in checks if c.status == "verified"]
    for name, v in raw.items():
        assert v is not None
        if not any(re.search(rf"(?<!\d){re.escape(v.strip())}(?!\d)", t) for t in texts):
            out["detail"] = f"value {name}={v!r} does not occur in a verified quote"
            return out
    left, right = nums[spec.left], nums[spec.right]
    assert left is not None and right is not None
    out["status"] = "holds" if _OPS[spec.op](left, right) else "fails"
    return out


def evidence_digest(c: Candidate, proposal: Proposal | None, checks: Sequence[QuoteCheck]) -> str:
    """Identity of what a human saw: the candidate's deterministic evidence plus the verified quotes and the claim."""
    return digest(
        {
            "candidate": c.key,
            "evidence": [(e.repo, e.sha, e.path, e.content_sha256, e.quote) for e in c.evidence],
            "claim": proposal.claim if proposal else None,
            "finding": proposal.finding if proposal else None,
            "quotes": [
                (k.quote.repo, k.quote.sha, k.quote.path, k.quote.line_start, k.quote.line_end, k.quote.text, k.status)
                for k in checks
            ],
        }
    )


def failure_result(
    c: Candidate, cause: str, reason: str, observations: Sequence[Observation] = (), response_keys: Sequence[str] = ()
) -> Result:
    return Result(
        c.key, CandState.INSUFFICIENT_EVIDENCE, EvStatus.UNVERIFIED, Validation.NOT_VALIDATED, reason, cause,
        observations=tuple(observations), evidence_digest=evidence_digest(c, None, ()), response_keys=tuple(response_keys),
    )  # fmt: skip


def _intent(p: Proposal, checks: Sequence[QuoteCheck]) -> dict[str, Any] | None:
    if p.intent is None:
        return None
    i = p.intent
    verified = i.quote is not None and checks[i.quote].status == "verified"
    return {
        "proposal": i.proposal, "justification": i.justification, "quote": i.quote, "justification_verified": verified,
        "applied": False, "note": "a proposal; it becomes real only through an exception recorded by a human",
    }  # fmt: skip


def decide(
    c: Candidate,
    p: Proposal,
    checks: Sequence[QuoteCheck],
    observations: Sequence[Observation],
    response_keys: Sequence[str],
) -> Result:
    """The decision rule of ADR-0016 §4. Pure; the model's text is never a state."""
    base: dict[str, Any] = {
        "key": c.key, "claim": p.claim, "finding": p.finding, "quotes": tuple(checks), "values": dict(p.values),
        "intent": _intent(p, checks), "observations": tuple(observations), "response_keys": tuple(response_keys),
        "evidence_digest": evidence_digest(c, p, checks),
    }  # fmt: skip
    verified = [k for k in checks if k.status == "verified"]
    if not checks or len(verified) != len(checks):
        why = (
            "no quote was given"
            if not checks
            else f"{len(checks) - len(verified)} of {len(checks)} quote(s) could not be verified at their pinned location"
        )
        return Result(
            state=CandState.INSUFFICIENT_EVIDENCE, evidence_status=EvStatus.UNVERIFIED, validation=Validation.NOT_VALIDATED,
            reason=f"{why}; a claim without verified quotes is never promoted.", cause=ICause.QUOTE_UNVERIFIED, **base,
        )  # fmt: skip
    if p.finding == "cannot_determine":
        return Result(
            state=CandState.INSUFFICIENT_EVIDENCE, evidence_status=EvStatus.SUPPORTED, validation=Validation.NOT_VALIDATED,
            reason="the Investigator could not determine whether the statement diverges.", cause=ICause.UNDETERMINED, **base,
        )  # fmt: skip
    if p.finding == "consistent":
        return Result(
            state=CandState.DISMISSED, evidence_status=EvStatus.SUPPORTED, validation=Validation.NOT_VALIDATED,
            reason="the Investigator proposes that no divergence exists (verified quotes); listed, not silent.", **base,
        )  # fmt: skip
    if c.kind == "intent_review":
        return Result(
            state=CandState.POLICY_QUESTION, evidence_status=EvStatus.SUPPORTED, validation=Validation.NOT_VALIDATED,
            reason="the deterministic outcome is unchanged and stays a question for the owner; the intent below is only a proposal.", **base,
        )  # fmt: skip
    if c.entailment is not None:
        ent = run_entailment(c.entailment, p.values, checks)
        if ent["status"] == "holds":
            return Result(
                state=CandState.CONFIRMED, evidence_status=EvStatus.SUPPORTED, validation=Validation.BY_CHECK,
                reason=f"entailment check {ent['test']} holds on values located in verified quotes.", entailment=ent, **base,
            )  # fmt: skip
        if ent["status"] == "fails":
            return Result(
                state=CandState.DISMISSED, evidence_status=EvStatus.REFUTED, validation=Validation.NOT_VALIDATED,
                reason=f"entailment check {ent['test']} does not hold: the divergence claim is refuted.", entailment=ent, **base,
            )  # fmt: skip
        return Result(
            state=CandState.NEEDS_VALIDATION, evidence_status=EvStatus.SUPPORTED, validation=Validation.NOT_VALIDATED,
            reason=f"the declared entailment check could not be evaluated ({ent.get('detail')}); a human decides.",
            entailment=ent, requires_triage=c.impact is Impact.BREAKS, **base,
        )  # fmt: skip
    return Result(
        state=CandState.NEEDS_VALIDATION, evidence_status=EvStatus.SUPPORTED, validation=Validation.NOT_VALIDATED,
        reason="all quotes are verified but the conclusion needs validation: the policy declares no entailment check, a human decides.",
        requires_triage=c.impact is Impact.BREAKS, **base,
    )  # fmt: skip
