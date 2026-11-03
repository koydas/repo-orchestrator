"""Policy API: plain functions with typed metadata (ADR-0003). No DSL, no expression evaluation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .. import extract
from ..collector.snapshot import Artifact, ArtifactRequest, Missing, Snapshot
from ..declarations import Declarations
from ..evidence import listing_evidence, text_evidence
from ..extract import Extracted, ExtractionError
from ..graph import Graph
from ..models import (
    Comparison,
    Evidence,
    FixSite,
    Impact,
    Intent,
    Outcome,
    Relationship,
    ScopeKind,
    Severity,
    Status,
    severity_for,
)


@dataclass(frozen=True)
class Scope:
    kind: ScopeKind
    values: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyMeta:
    id: str
    version: str
    title: str
    aspect: str  # what the policy decides on a relationship; two policies of one aspect and scope are a config error
    scope: Scope
    impact: Impact
    intent: Intent
    check: str = "deterministic"
    # Subject prefixes whose set depends on the analyzed content (links, claimed addresses): a subject of such a family
    # that is no longer emitted, while the family was enumerated, is gone rather than unevaluated.
    enumerated_subjects: tuple[str, ...] = ()


class CannotEvaluate(Exception):
    """Raised by policy code when evidence is missing or undecidable: becomes a ``cannot_evaluate`` outcome."""

    def __init__(self, cause: str, detail: str, evidence: tuple[Evidence, ...] = ()) -> None:
        super().__init__(f"{cause}: {detail}")
        self.cause = cause
        self.detail = detail
        self.evidence = evidence


def no_listings(cfg: Any, rel: Relationship) -> Sequence[tuple[str, str]]:
    return ()


@dataclass(frozen=True)
class Policy:
    meta: PolicyMeta
    prepare: Callable[[Declarations, Graph], Any]  # validates declarations, returns the typed configuration
    applies: Callable[[Relationship], bool]
    requests: Callable[[Any, Relationship], Sequence[ArtifactRequest]]
    evaluate: Callable[[Any, EvalContext, Relationship], list[Outcome]]
    # (repo id, directory) pairs whose *listing* (``ctx.entries``) the policy reads: a file added, removed or renamed below
    # one changes its outcome even though no file the policy reads was edited. Used by C4 as a footprint (M2); a policy that
    # lists a directory must declare it here, and ``tests/test_impact.py`` fails if one does not.
    listings: Callable[[Any, Relationship], Sequence[tuple[str, str]]] = no_listings


@dataclass
class EvalContext:
    snapshot: Snapshot
    graph: Graph
    declarations: Declarations
    as_of: date
    meta: PolicyMeta
    _notes: list[str] = field(default_factory=list)

    # -- reading ------------------------------------------------------------------------------------------
    def artifact(self, repo: str, path: str) -> Artifact:
        found = self.snapshot.artifact(repo, path)
        if isinstance(found, Missing):
            raise CannotEvaluate(found.cause, found.detail)
        return found

    def text(self, repo: str, path: str) -> tuple[Artifact, str]:
        art = self.artifact(repo, path)
        try:
            return art, extract.decode(art.data)
        except ExtractionError as exc:
            raise CannotEvaluate(exc.cause, f"{art.slug}:{path}: {exc.detail}") from exc

    def entries(self, repo: str, prefix: str) -> list[Any]:
        found = self.snapshot.entries(repo, prefix)
        if isinstance(found, Missing):
            raise CannotEvaluate(found.cause, found.detail)
        return found

    def glob(self, repo: str, pattern: str) -> tuple[str, ...]:
        found = self.snapshot.glob(repo, pattern)
        if isinstance(found, Missing):
            raise CannotEvaluate(found.cause, found.detail)
        return found

    def sha(self, repo: str) -> str:
        rev = self.snapshot.revision(repo)
        if isinstance(rev, Missing):
            raise CannotEvaluate(rev.cause, rev.detail)
        return rev

    def slug(self, repo: str) -> str:
        return self.graph.repositories[repo].slug

    # -- evidence -----------------------------------------------------------------------------------------
    def text_ev(self, side: str, art: Artifact, ex: Extracted, locator: str) -> Evidence:
        return text_evidence(side, art, ex, locator)

    def listing_ev(
        self, side: str, repo: str, directory: str, names: list[str], locator: str, extractor: str
    ) -> Evidence:
        return listing_evidence(side, repo, self.slug(repo), self.sha(repo), directory, names, locator, extractor)

    # -- outcomes -----------------------------------------------------------------------------------------
    def _base(self, rel: Relationship, subject: str, status: Status, reason: str, **kw: Any) -> Outcome:
        return Outcome(self.meta.id, self.meta.version, rel.id, subject, status, reason, **kw)

    def consistent(
        self,
        rel: Relationship,
        subject: str,
        reason: str,
        comparison: Comparison | None,
        evidence: Sequence[Evidence],
        locators: Sequence[str],
        **kw: Any,
    ) -> Outcome:
        return self._base(
            rel,
            subject,
            Status.CONSISTENT,
            reason,
            comparison=comparison,
            evidence=tuple(evidence),
            locators=tuple(locators),
            **kw,
        )

    def inconsistent(
        self,
        rel: Relationship,
        subject: str,
        reason: str,
        comparison: Comparison,
        evidence: Sequence[Evidence],
        locators: Sequence[str],
        affected: Sequence[str],
        investigate: Sequence[str],
        fix_sites: Sequence[FixSite] = (),
        **kw: Any,
    ) -> Outcome:
        return self._base(
            rel,
            subject,
            Status.INCONSISTENT,
            reason,
            comparison=comparison,
            evidence=tuple(evidence),
            locators=tuple(locators),
            severity=self._severity(rel),
            impact=self.meta.impact,
            intent=self.meta.intent,
            affected_repos=tuple(affected),
            investigate=tuple(investigate),
            fix_sites=tuple(fix_sites),
            **kw,
        )

    def clarify(
        self,
        rel: Relationship,
        subject: str,
        reason: str,
        question: str,
        evidence: Sequence[Evidence],
        locators: Sequence[str],
        comparison: Comparison | None = None,
        **kw: Any,
    ) -> Outcome:
        return self._base(
            rel,
            subject,
            Status.POLICY_CLARIFICATION,
            reason,
            comparison=comparison,
            evidence=tuple(evidence),
            locators=tuple(locators),
            open_questions=(question,),
            **kw,
        )

    def cannot(self, rel: Relationship, subject: str, exc: CannotEvaluate, locators: Sequence[str] = ()) -> Outcome:
        return self._base(
            rel,
            subject,
            Status.CANNOT_EVALUATE,
            exc.detail,
            cause=exc.cause,
            evidence=exc.evidence,
            locators=tuple(locators),
        )

    def _severity(self, rel: Relationship) -> Severity:
        return severity_for(self.meta.impact, rel.type)


def in_scope(policy_scope: Scope, rel: Relationship) -> bool:
    match policy_scope.kind:
        case ScopeKind.GLOBAL:
            return True
        case ScopeKind.RELATIONSHIP_TYPE:
            return rel.type.value in policy_scope.values
        case ScopeKind.RELATIONSHIP:
            return rel.id in policy_scope.values or rel.baseline_id in policy_scope.values
        case ScopeKind.REPOSITORY:
            return rel.target in policy_scope.values or rel.source in policy_scope.values
    return False
