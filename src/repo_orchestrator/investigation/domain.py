"""Types of the investigation: candidates, proposals, quote checks, results (ADR-0016). JSON round-trip is explicit
because a candidate crosses the graph checkpoint and the store as plain data."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..models import Evidence, Impact, to_jsonable


class CandState(StrEnum):
    CONFIRMED = "confirmed"
    NEEDS_VALIDATION = "needs_validation"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    DISMISSED = "dismissed"
    POLICY_QUESTION = "policy_question"


class EvStatus(StrEnum):
    VERIFIED = "verified"
    SUPPORTED = "supported"
    UNVERIFIED = "unverified"
    REFUTED = "refuted"


class Validation(StrEnum):
    BY_CHECK = "validated_by_check"
    BY_HUMAN = "validated_by_human"
    NOT_VALIDATED = "not_validated"


class ICause:
    """Stable cause codes of an ``insufficient_evidence`` / dismissed investigation."""

    BUDGET = "budget"
    PROVIDER_ERROR = "provider_error"
    PROVIDER_REFUSAL = "provider_refusal"
    NO_CREDENTIALS = "no_credentials"
    REPLAY_MISS = "replay_miss"
    MALFORMED = "malformed_output"
    QUOTE_UNVERIFIED = "quote_unverified"
    UNDETERMINED = "investigator_undetermined"
    CLAIM_NOT_FOUND = "claim_not_found"
    EVIDENCE_MISSING = "evidence_missing"
    SNAPSHOT_CHANGED = "snapshot_changed"
    NOT_INVESTIGATED = "not_investigated"
    INTERNAL = "internal_error"


KIND_CLAIM = "qualitative_claim"
KIND_INTENT = "intent_review"
OPS = ("lt", "le", "eq", "ne", "ge", "gt")


@dataclass(frozen=True)
class Entailment:
    """A deterministic test the policy declares: the divergence holds iff ``values[left] <op> values[right]``."""

    kind: str  # numeric_relation (the only kind in M3)
    left: str
    op: str
    right: str


@dataclass(frozen=True)
class Candidate:
    key: str
    policy_id: str
    policy_version: str
    relationship_id: str
    subject: str
    kind: str
    question: str
    claim_text: str
    evidence: tuple[Evidence, ...]
    impact: Impact
    relationship_type: str
    repos: tuple[str, ...]  # repositories the tools may read (the relationship's endpoints)
    entailment: Entailment | None = None
    open_questions: tuple[str, ...] = ()
    prefilter_failure: tuple[str, str] | None = None  # (cause, detail): the candidate cannot be investigated
    origin_status: str | None = None  # intent reviews: the deterministic status that stays unchanged


def candidate_key(policy_id: str, relationship_id: str, subject: str) -> str:
    return hashlib.sha256("|".join([policy_id, relationship_id, subject]).encode()).hexdigest()[:16]


def evidence_from_json(d: Mapping[str, Any]) -> Evidence:
    return Evidence(**{k: d[k] for k in Evidence.__dataclass_fields__})


def candidate_to_json(c: Candidate) -> dict[str, Any]:
    out = to_jsonable(c)
    assert isinstance(out, dict)
    return out


def candidate_from_json(d: Mapping[str, Any]) -> Candidate:
    ent = d.get("entailment")
    pf = d.get("prefilter_failure")
    return Candidate(
        key=d["key"], policy_id=d["policy_id"], policy_version=d["policy_version"],
        relationship_id=d["relationship_id"], subject=d["subject"], kind=d["kind"], question=d["question"],
        claim_text=d["claim_text"], evidence=tuple(evidence_from_json(e) for e in d["evidence"]),
        impact=Impact(d["impact"]), relationship_type=d["relationship_type"], repos=tuple(d["repos"]),
        entailment=Entailment(**ent) if ent else None, open_questions=tuple(d.get("open_questions", ())),
        prefilter_failure=(pf[0], pf[1]) if pf else None, origin_status=d.get("origin_status"),
    )  # fmt: skip


@dataclass(frozen=True)
class QuoteIn:
    """A quote as the model wrote it (untrusted)."""

    repo: str
    sha: str
    path: str
    line_start: int
    line_end: int
    text: str


@dataclass(frozen=True)
class IntentIn:
    proposal: str  # accidental | intentional | undetermined
    justification: str
    quote: int | None  # index into the proposal's quotes


@dataclass(frozen=True)
class Proposal:
    finding: str  # divergent | consistent | cannot_determine
    claim: str
    quotes: tuple[QuoteIn, ...]
    values: Mapping[str, str]
    intent: IntentIn | None


@dataclass(frozen=True)
class QuoteCheck:
    status: str  # verified | unverified
    reason: str  # ok | not_found_at_locator | revision_mismatch | repo_out_of_scope | unreadable | too_short | ...
    quote: QuoteIn
    evidence: Evidence | None = None  # built by code from the snapshot, only when verified


@dataclass(frozen=True)
class Observation:
    kind: str  # instruction_like_text
    repo: str
    path: str
    line: int | None
    excerpt: str
    pattern: str


@dataclass
class CallStats:
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_micro_usd: int = 0
    latency_ms: int = 0
    replayed_calls: int = 0
    retries: int = 0


@dataclass(frozen=True)
class Result:
    """The outcome of one investigation after the decision rule (code), before any human decision."""

    key: str
    state: CandState
    evidence_status: EvStatus | None
    validation: Validation
    reason: str
    cause: str | None = None
    claim: str = ""
    finding: str | None = None
    quotes: tuple[QuoteCheck, ...] = ()
    values: Mapping[str, str] = field(default_factory=dict)
    intent: Mapping[str, Any] | None = None
    entailment: Mapping[str, Any] | None = None
    observations: tuple[Observation, ...] = ()
    requires_triage: bool = False
    evidence_digest: str = ""
    response_keys: tuple[str, ...] = ()
