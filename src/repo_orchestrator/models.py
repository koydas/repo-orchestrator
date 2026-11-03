"""Domain model: explicit, immutable types shared by every component (docs/domain-model.md)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class RelType(StrEnum):
    RUNTIME_CONTRACT = "runtime_contract"
    BUILD_DEPENDENCY = "build_dependency"
    DEPLOYMENT = "deployment"
    SHARED_RESOURCE = "shared_resource"
    TOOLING_ADOPTION = "tooling_adoption"
    DOCUMENTARY = "documentary"
    CONCEPTUAL = "conceptual"


class Origin(StrEnum):
    DECLARED = "declared"
    OBSERVED = "observed"
    INFERRED = "inferred"


class Applicability(StrEnum):
    IN_SCOPE = "in_scope"
    UNDETERMINED = "undetermined"  # owner decision pending (baseline Q1): policies may only ask, never accuse


class Impact(StrEnum):
    BREAKS = "breaks"
    FALSIFIES = "falsifies"
    OUTDATES = "outdates"


class Severity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Status(StrEnum):
    """Outcome of one policy on one subject of one relationship."""

    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"  # confirmed finding (deterministic check failed)
    CANNOT_EVALUATE = "cannot_evaluate"  # evidence missing, unavailable or undecidable: never a pass
    POLICY_CLARIFICATION = "policy_clarification"  # applicability ambiguous: a question for the owner
    POLICY_CONFLICT = "policy_conflict"  # a wider policy contradicts a repository's own authority
    EXCEPTED = "excepted"  # a valid, unexpired exception suppresses the finding
    FAILED = "failed"  # the engine itself failed (policy crash, unverifiable evidence, collision)


class Intent(StrEnum):
    ACCIDENTAL = "accidental"
    INTENTIONAL = "intentional"
    UNDETERMINED = "undetermined"


class ScopeKind(StrEnum):
    GLOBAL = "global"
    RELATIONSHIP_TYPE = "relationship_type"
    RELATIONSHIP = "relationship"
    REPOSITORY = "repository"


# Narrowest scope wins (docs/coherence-model.md → Precedence).
SCOPE_RANK: dict[ScopeKind, int] = {
    ScopeKind.GLOBAL: 0,
    ScopeKind.RELATIONSHIP_TYPE: 1,
    ScopeKind.RELATIONSHIP: 2,
    ScopeKind.REPOSITORY: 3,
}


class Cause:
    """Stable codes explaining a ``cannot_evaluate`` / ``failed`` outcome."""

    # collection
    REPOSITORY_NOT_FOUND = "repository_not_found"
    PRIVATE_REPOSITORY = "private_repository_out_of_scope"
    PERMISSION_DENIED = "permission_denied"
    REVISION_UNRESOLVED = "revision_unresolved"
    FETCH_FAILED = "fetch_failed"
    RATE_LIMITED = "rate_limited"
    BUDGET_EXHAUSTED = "api_budget_exhausted"
    NOT_CACHED = "not_cached_offline"
    INTEGRITY = "integrity_mismatch"
    # artifact
    NOT_FOUND = "artifact_not_found"
    TOO_LARGE = "artifact_too_large"
    TREE_TRUNCATED = "tree_truncated"
    NOT_COLLECTED = "artifact_not_collected"
    # parsing / locators
    PARSE_ERROR = "parse_error"
    LOCATOR_NO_MATCH = "locator_no_match"
    LOCATOR_AMBIGUOUS = "locator_ambiguous"
    LOCATOR_INVALID = "locator_invalid"
    UNSUPPORTED_SYNTAX = "unsupported_syntax"
    # by design
    NOT_CHECKABLE = "not_checkable_deterministically"
    REF_NOT_PINNED = "reference_not_pinned"
    AUTHORITY_UNRESOLVED = "authority_unresolved"
    # internal
    INTERNAL_ERROR = "internal_error"
    EVIDENCE_UNVERIFIABLE = "evidence_unverifiable"
    FINGERPRINT_COLLISION = "fingerprint_collision"


_COLLECTION = {
    Cause.REPOSITORY_NOT_FOUND,
    Cause.PRIVATE_REPOSITORY,
    Cause.PERMISSION_DENIED,
    Cause.REVISION_UNRESOLVED,
    Cause.FETCH_FAILED,
    Cause.RATE_LIMITED,
    Cause.BUDGET_EXHAUSTED,
    Cause.NOT_CACHED,
    Cause.INTEGRITY,
}
_ARTIFACT = {Cause.NOT_FOUND, Cause.TOO_LARGE, Cause.TREE_TRUNCATED, Cause.NOT_COLLECTED, Cause.AUTHORITY_UNRESOLVED}
_PARSE = {Cause.PARSE_ERROR, Cause.UNSUPPORTED_SYNTAX}
_LOCATOR = {Cause.LOCATOR_NO_MATCH, Cause.LOCATOR_AMBIGUOUS, Cause.LOCATOR_INVALID, Cause.REF_NOT_PINNED}


def cause_class(cause: str) -> str:
    """collection | artifact | parse | locator | by_design | internal."""
    if cause in _COLLECTION:
        return "collection"
    if cause in _ARTIFACT:
        return "artifact"
    if cause in _PARSE:
        return "parse"
    if cause in _LOCATOR:
        return "locator"
    if cause == Cause.NOT_CHECKABLE:
        return "by_design"
    return "internal"


@dataclass(frozen=True)
class Repository:
    id: str
    slug: str  # owner/name
    scope: str  # primary | secondary | out_of_m1
    analyzed: bool = True
    note: str = ""
    default_branch: str = "main"


@dataclass(frozen=True)
class Anchor:
    repo: str
    path: str
    role: str = ""


@dataclass(frozen=True)
class Relationship:
    id: str
    baseline_id: str
    type: RelType
    source: str  # the repository whose change affects the target
    target: str
    origin: Origin
    description: str
    applicability: Applicability = Applicability.IN_SCOPE
    open_question: str | None = None
    anchors: tuple[Anchor, ...] = ()
    contract: Mapping[str, Any] = field(default_factory=dict)
    evidence_ref: str = ""


@dataclass(frozen=True)
class Evidence:
    side: str
    repo: str
    slug: str
    sha: str
    path: str
    locator: str
    kind: str  # text | tree_listing
    quote: str
    content_sha256: str
    blob_sha: str | None
    line_start: int | None
    line_end: int | None
    extractor: str


@dataclass(frozen=True)
class Comparison:
    comparator: str
    left: Any
    right: Any
    result: (
        str  # e.g. equal, not_equal, subset, not_subset, in_pool, out_of_pool, unique, duplicate, resolves, dangling
    )
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FixSite:
    repo: str
    path: str
    reason: str


@dataclass(frozen=True)
class Outcome:
    policy_id: str
    policy_version: str
    relationship_id: str
    subject: str
    status: Status
    reason: str
    comparison: Comparison | None = None
    evidence: tuple[Evidence, ...] = ()
    locators: tuple[str, ...] = ()
    cause: str | None = None
    severity: Severity | None = None
    impact: Impact | None = None
    intent: Intent | None = None
    affected_repos: tuple[str, ...] = ()
    investigate: tuple[str, ...] = ()
    fix_sites: tuple[FixSite, ...] = ()
    open_questions: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)
    fingerprint: str | None = None
    exception_id: str | None = None
    superseded_by: str | None = None

    @property
    def subject_key(self) -> tuple[str, str, str]:
        return (self.policy_id, self.relationship_id, self.subject)


def to_jsonable(obj: Any) -> Any:
    """Plain, order-stable JSON structure for dataclasses, enums, mappings and sequences."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, StrEnum):
        return obj.value
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Mapping):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        return sorted(to_jsonable(v) for v in obj)
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj


def canonical_json(obj: Any) -> str:
    return json.dumps(to_jsonable(obj), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def severity_for(impact: Impact, rel_type: RelType) -> Severity:
    """docs/coherence-model.md → Prioritization: derived from impact class and relationship type, never an LLM."""
    if impact is Impact.BREAKS:
        return Severity.HIGH
    if impact is Impact.FALSIFIES or rel_type is RelType.TOOLING_ADOPTION:
        return Severity.MEDIUM
    return Severity.LOW


class ConfigError(Exception):
    """Invalid declaration or policy configuration. Raised before anything is collected."""
