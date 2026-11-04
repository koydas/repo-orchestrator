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
    Operand,
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


def _whole_token(text: str, start: int, end: int) -> bool:
    """The characters ``text[start:end]`` are a whole number as written: not a fragment of a longer, signed, decimal,
    grouped or alphanumeric token (``1`` and ``5`` in ``1.5``, ``12`` in ``-12``, ``1,200`` or ``v12``)."""
    before = text[start - 1] if start > 0 else ""
    after, after2 = (text[end] if end < len(text) else ""), (text[end + 1] if end + 1 < len(text) else "")
    return not (re.match(r"[\w.,+\-]", before) or re.match(r"\w", after) or (after in ".," and after2.isdigit()))


def _operand_value(op: Operand, evidence: Sequence[Evidence]) -> tuple[int | None, str]:
    """The number a declared operand denotes, extracted by code from its declared evidence line: (value, '') or (None, why)."""
    lines = [e for e in evidence if e.side == op.source]
    if op.index >= len(lines):
        return None, f"operand {op.name}: no {op.source} evidence line #{op.index}"
    line = lines[op.index].quote
    m = re.search(op.regex, line)
    if m is None or m.re.groups != 1 or m.start(1) < 0:
        return None, f"operand {op.name}: the declared pattern does not match its {op.source} line"
    if not _whole_token(line, m.start(1), m.end(1)):
        return None, f"operand {op.name}: the matched number is a fragment of a longer token"
    value = _number(m.group(1))
    return (value, "") if value is not None else (None, f"operand {op.name}: {m.group(1)!r} is not a number")


def run_entailment(spec: Entailment, values: Mapping[str, str], evidence: Sequence[Evidence]) -> dict[str, Any]:
    """The policy's deterministic test. ``status``: holds | fails | not_evaluable. Each operand is extracted *by code* from
    its declared evidence line (so the model cannot swap or invent an operand); the Investigator must name the same
    numbers, otherwise the check is not evaluable and a human decides."""
    left, right = spec.left, spec.right
    out: dict[str, Any] = {
        "kind": spec.kind, "test": f"{left.name} {spec.op} {right.name}", "status": "not_evaluable", "values": {},
    }  # fmt: skip
    nums: dict[str, int] = {}
    for operand in (left, right):
        value, why = _operand_value(operand, evidence)
        if value is None:
            out["detail"] = why
            return out
        out["values"][operand.name] = str(value)
        said = values.get(operand.name)
        if said is None or _number(said) != value:
            out["detail"] = (
                f"the Investigator's value for {operand.name} ({said!r}) does not match the declared source ({value})"
            )
            return out
        nums[operand.name] = value
    out["status"] = "holds" if _OPS[spec.op](nums[left.name], nums[right.name]) else "fails"
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
    c: Candidate,
    cause: str,
    reason: str,
    observations: Sequence[Observation] = (),
    response_keys: Sequence[str] = (),
    response_digests: Sequence[str] = (),
) -> Result:
    return Result(
        c.key, CandState.INSUFFICIENT_EVIDENCE, EvStatus.UNVERIFIED, Validation.NOT_VALIDATED, reason, cause,
        observations=tuple(observations), evidence_digest=evidence_digest(c, None, ()), response_keys=tuple(response_keys),
        response_digests=tuple(response_digests),
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
    response_digests: Sequence[str] = (),
) -> Result:
    """The decision rule of ADR-0016 §4. Pure; the model's text is never a state."""
    base: dict[str, Any] = {
        "key": c.key, "claim": p.claim, "finding": p.finding, "quotes": tuple(checks), "values": dict(p.values),
        "intent": _intent(p, checks), "observations": tuple(observations), "response_keys": tuple(response_keys),
        "response_digests": tuple(response_digests),
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
        ent = run_entailment(c.entailment, p.values, c.evidence)
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
