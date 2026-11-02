"""C3 Policy engine: applicability, precedence, exceptions, repository authorities, evidence verification, fingerprints.

Pipeline (docs/workflows.md W1): ``prepare → plan → collect → evaluate → resolve → verify``. All I/O sits behind the
:class:`~repo_orchestrator.collector.source.SnapshotSource` passed to :meth:`Engine.run`; evaluation itself runs on a frozen
:class:`~repo_orchestrator.collector.snapshot.Snapshot`.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from . import evidence as evidence_mod
from .collector.snapshot import Artifact, ArtifactRequest, Limits, Missing, RepoTarget, Snapshot, collect
from .collector.source import SnapshotSource
from .declarations import AuthorityRef, Declarations, ExceptionDecl
from .extract import ExtractionError, decode
from .graph import Graph
from .models import (
    SCOPE_RANK,
    Applicability,
    Cause,
    ConfigError,
    Evidence,
    Intent,
    Outcome,
    Relationship,
    Status,
    cause_class,
)
from .policies import REGISTRY
from .policies.base import CannotEvaluate, EvalContext, Policy, in_scope


@dataclass(frozen=True)
class RunOptions:
    revisions: dict[str, str | None] = field(default_factory=dict)  # repo id → pinned ref (None = default branch HEAD)
    scope: tuple[str, ...] | None = None  # repo ids; None = every analyzed repository
    as_of: date = field(default_factory=date.today)
    limits: Limits = field(default_factory=Limits)


@dataclass(frozen=True)
class AuthorityResult:
    policy_id: str
    repo: str
    path: str
    quote: str | None
    verified: bool
    note: str


@dataclass(frozen=True)
class ExceptionState:
    id: str
    policy: str
    state: str  # applied | expired | authority_unresolved | unused
    review_by: str
    owner: str
    matched: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class Coverage:
    relationship_id: str
    baseline_id: str
    type: str
    source: str
    target: str
    status: str  # evaluated | no_policy | not_analyzed | outside_scope | proposal
    policies: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class Superseded:
    relationship_id: str
    policy: str
    by: str


@dataclass
class ScanResult:
    outcomes: list[Outcome]
    snapshot: Snapshot
    scope: tuple[str, ...]
    as_of: date
    authorities: list[AuthorityResult]
    exceptions: list[ExceptionState]
    coverage: list[Coverage]
    superseded: list[Superseded]
    policies: list[tuple[str, str]]


def fingerprint(o: Outcome) -> str:
    """policy + relationship + subject + normalized locators: no SHAs, no line numbers (ADR-0006 §5)."""
    key = "|".join([o.policy_id, o.relationship_id, o.subject, *sorted(set(o.locators))])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


class Engine:
    def __init__(self, declarations: Declarations, policies: Sequence[Policy] = REGISTRY) -> None:
        self.decl = declarations
        self.graph = Graph.from_declarations(declarations)
        ids = [p.meta.id for p in policies]
        if len(set(ids)) != len(ids):
            raise ConfigError(f"duplicate policy ids: {ids}")
        self.policies = {p.meta.id: p for p in policies}
        self.configs = {p.meta.id: p.prepare(declarations, self.graph) for p in policies}
        self._validate_references()
        self.plan_by_rel = self._select_policies()

    # -- load-time validation ----------------------------------------------------------------------------
    def _validate_references(self) -> None:
        rel_ids = {r.id for r in self.graph.trusted()}
        for exc in self.decl.exceptions:
            if exc.policy not in self.policies:
                raise ConfigError(f"exception {exc.id}: unknown policy {exc.policy!r}")
            unknown = [r for r in exc.relationships if r not in rel_ids]
            if unknown:
                raise ConfigError(f"exception {exc.id}: unknown relationship(s) {unknown}")
        for auth in self.decl.repository_authorities:
            if auth.policy not in self.policies or auth.repo not in self.graph.repositories:
                raise ConfigError(f"repository authority {auth.id}: unknown policy or repository")
            unknown = [r for r in auth.relationships if r not in rel_ids]
            if unknown:
                raise ConfigError(f"repository authority {auth.id}: unknown relationship(s) {unknown}")
        for pid, params in self.decl.policy_params.items():
            if pid not in self.policies:
                raise ConfigError(f"policies.yaml declares parameters for unknown policy {pid!r}")
            for ref in params.get("authority", []) or []:
                if ref.get("repo") not in self.graph.repositories:
                    raise ConfigError(f"policies.yaml:{pid}.authority: unknown repository {ref.get('repo')!r}")

    def _select_policies(self) -> dict[str, tuple[list[Policy], list[Superseded]]]:
        """Per trusted relationship: the policies that apply after precedence (docs/coherence-model.md → Precedence)."""
        selected: dict[str, tuple[list[Policy], list[Superseded]]] = {}
        for rel in self.graph.trusted():
            by_aspect: dict[str, list[Policy]] = {}
            for p in self.policies.values():
                if p.applies(rel) and in_scope(p.meta.scope, rel):
                    by_aspect.setdefault(p.meta.aspect, []).append(p)
            chosen: list[Policy] = []
            dropped: list[Superseded] = []
            for aspect, group in sorted(by_aspect.items()):
                top = max(SCOPE_RANK[p.meta.scope.kind] for p in group)
                winners = [p for p in group if SCOPE_RANK[p.meta.scope.kind] == top]
                if len(winners) > 1:
                    raise ConfigError(
                        f"same-scope policy conflict on {rel.id} ({aspect}): "
                        + ", ".join(p.meta.id for p in winners)
                        + f" are all scoped {winners[0].meta.scope.kind.value}"
                    )
                chosen.append(winners[0])
                dropped.extend(Superseded(rel.id, p.meta.id, winners[0].meta.id) for p in group if p is not winners[0])
            selected[rel.id] = (sorted(chosen, key=lambda p: p.meta.id), dropped)
        return selected

    # -- planning ----------------------------------------------------------------------------------------
    def _authorities(self, policy_id: str) -> list[AuthorityRef]:
        out = []
        for raw in self.decl.policy_params.get(policy_id, {}).get("authority") or []:
            out.append(AuthorityRef(str(raw["repo"]), str(raw["path"]), raw.get("quote")))
        return out

    def selected_repos(self, scope: Iterable[str] | None) -> tuple[str, ...]:
        wanted = list(scope) if scope is not None else self.graph.analyzed_ids()
        for repo_id in wanted:
            repo = self.graph.repositories.get(repo_id)
            if repo is None:
                raise ConfigError(f"unknown repository {repo_id!r} (declared: {sorted(self.graph.repositories)})")
            if not repo.analyzed:
                raise ConfigError(f"repository {repo_id!r} is declared but outside the M1 scope")
        return tuple(sorted(set(wanted)))

    def _evaluable(self, rel: Relationship, selected: set[str]) -> bool:
        return self.graph.evaluable(rel) and rel.source in selected and rel.target in selected

    def plan(self, selected: set[str]) -> list[ArtifactRequest]:
        requests: list[ArtifactRequest] = []
        for rel in self.graph.trusted():
            if not self._evaluable(rel, selected):
                continue
            for p in self.plan_by_rel[rel.id][0]:
                requests.extend(p.requests(self.configs[p.meta.id], rel))
                requests.extend(ArtifactRequest(a.repo, a.path) for a in self._authorities(p.meta.id))
        for exc in self.decl.exceptions:
            requests.append(ArtifactRequest(exc.authority.repo, exc.authority.path))
        for auth in self.decl.repository_authorities:
            requests.append(ArtifactRequest(auth.authority.repo, auth.authority.path))
        return sorted(
            {r for r in requests if r.repo in selected and self.graph.repositories[r.repo].analyzed},
            key=lambda r: (r.repo, r.path or "", r.glob or ""),
        )

    # -- run ---------------------------------------------------------------------------------------------
    def run(self, source: SnapshotSource, options: RunOptions) -> ScanResult:
        selected = set(self.selected_repos(options.scope))
        targets = [
            RepoTarget(rid, self.graph.repositories[rid].slug, options.revisions.get(rid)) for rid in sorted(selected)
        ]
        snapshot = collect(source, targets, self.plan(selected), options.limits)
        outcomes: list[Outcome] = []
        authorities: list[AuthorityResult] = []
        verified_policies: dict[str, bool] = {}
        coverage: list[Coverage] = []
        superseded: list[Superseded] = []
        for pid in sorted(self.policies):
            results = [self._check_authority(pid, a, snapshot) for a in self._authorities(pid)]
            authorities.extend(results)
            verified_policies[pid] = all(r.verified or r.note.startswith("declared") for r in results)
        for rel in self.graph.trusted():
            policies, dropped = self.plan_by_rel[rel.id]
            if not self._evaluable(rel, selected):
                reason = (
                    "an endpoint is outside the M1 repository scope"
                    if not self.graph.evaluable(rel)
                    else "an endpoint is outside the selected scope"
                )
                coverage.append(
                    Coverage(
                        rel.id,
                        rel.baseline_id,
                        rel.type.value,
                        rel.source,
                        rel.target,
                        "not_analyzed" if not self.graph.evaluable(rel) else "outside_scope",
                        tuple(p.meta.id for p in policies),
                        reason,
                    )
                )
                continue
            superseded.extend(dropped)
            if not policies:
                coverage.append(
                    Coverage(
                        rel.id,
                        rel.baseline_id,
                        rel.type.value,
                        rel.source,
                        rel.target,
                        "no_policy",
                        (),
                        "declared; no M1 policy evaluates this relationship",
                    )
                )
                continue
            coverage.append(
                Coverage(
                    rel.id,
                    rel.baseline_id,
                    rel.type.value,
                    rel.source,
                    rel.target,
                    "evaluated",
                    tuple(p.meta.id for p in policies),
                )
            )
            for policy in policies:
                outcomes.extend(
                    self._evaluate(policy, rel, snapshot, options.as_of, verified_policies[policy.meta.id], authorities)
                )
        coverage.extend(
            Coverage(
                r.id,
                r.baseline_id,
                r.type.value,
                r.source,
                r.target,
                "proposal",
                (),
                "observed/inferred: reported, never evaluated",
            )
            for r in self.graph.proposals()
        )
        outcomes = self._verify_and_fingerprint(outcomes, snapshot)
        outcomes = self._apply_repository_authorities(outcomes, snapshot)
        outcomes, exception_states = self._apply_exceptions(outcomes, snapshot, options.as_of)
        outcomes.sort(key=lambda o: (o.relationship_id, o.policy_id, o.subject))
        return ScanResult(
            outcomes, snapshot, tuple(sorted(selected)), options.as_of, authorities, exception_states,
            sorted(coverage, key=lambda c: c.relationship_id), superseded, [(p.meta.id, p.meta.version) for p in self.policies.values()],
        )  # fmt: skip

    def _check_authority(self, policy_id: str, ref: AuthorityRef, snapshot: Snapshot) -> AuthorityResult:
        repo = self.graph.repositories[ref.repo]
        if not repo.analyzed:
            return AuthorityResult(
                policy_id, ref.repo, ref.path, ref.quote, True, "declared in a repository outside the analyzed set"
            )
        if not snapshot.has(ref.repo):
            return AuthorityResult(
                policy_id, ref.repo, ref.path, ref.quote, False, "repository outside the selected scope"
            )
        return _resolve_authority(policy_id, ref, snapshot)

    def _evaluate(
        self,
        policy: Policy,
        rel: Relationship,
        snapshot: Snapshot,
        as_of: date,
        authority_ok: bool,
        authorities: list[AuthorityResult],
    ) -> list[Outcome]:
        ctx = EvalContext(snapshot, self.graph, self.decl, as_of, policy.meta)
        if not authority_ok:
            failed = [a for a in authorities if a.policy_id == policy.meta.id and not a.verified]
            detail = "; ".join(f"{a.repo}:{a.path} ({a.note})" for a in failed)
            # a collection failure keeps its own cause; only an artifact that resolved wrongly is `authority_unresolved`
            cause = failed[0].note if cause_class(failed[0].note) == "collection" else Cause.AUTHORITY_UNRESOLVED
            return [ctx.cannot(rel, "*", CannotEvaluate(cause, f"policy authority not resolved: {detail}"))]
        try:
            return [_ask_when_undetermined(o, rel) for o in policy.evaluate(self.configs[policy.meta.id], ctx, rel)]
        except CannotEvaluate as exc:
            return [ctx.cannot(rel, "*", exc)]
        except Exception as exc:
            return [
                Outcome(
                    policy.meta.id,
                    policy.meta.version,
                    rel.id,
                    "*",
                    Status.FAILED,
                    f"{type(exc).__name__}: {str(exc)[:160]}",
                    cause=Cause.INTERNAL_ERROR,
                )
            ]

    # -- post-processing ---------------------------------------------------------------------------------
    def _verify_and_fingerprint(self, outcomes: list[Outcome], snapshot: Snapshot) -> list[Outcome]:
        result: list[Outcome] = []
        seen: dict[str, Outcome] = {}
        for o in outcomes:
            bad = [e for e in o.evidence if not evidence_mod.verify(e, snapshot)]
            if bad:
                result.append(
                    _failed(
                        o,
                        Cause.EVIDENCE_UNVERIFIABLE,
                        f"evidence not verifiable at {bad[0].repo}:{bad[0].path}@{bad[0].sha[:12]}",
                    )
                )
                continue
            if o.status is not Status.INCONSISTENT:
                result.append(o)
                continue
            fp = fingerprint(o)
            if fp in seen:
                result.append(
                    _failed(
                        o,
                        Cause.FINGERPRINT_COLLISION,
                        f"fingerprint {fp} already used by {seen[fp].policy_id}/{seen[fp].relationship_id}/{seen[fp].subject}",
                    )
                )
                continue
            marked = dataclasses.replace(o, fingerprint=fp)
            seen[fp] = marked
            result.append(marked)
        return result

    def _apply_repository_authorities(self, outcomes: list[Outcome], snapshot: Snapshot) -> list[Outcome]:
        result: list[Outcome] = []
        for o in outcomes:
            if o.status is not Status.INCONSISTENT:
                result.append(o)
                continue
            converted = o
            for auth in self.decl.repository_authorities:
                if (
                    auth.policy != o.policy_id
                    or o.relationship_id not in auth.relationships
                    or (auth.subject and auth.subject != o.subject)
                ):
                    continue
                check = (
                    _resolve_authority(o.policy_id, auth.authority, snapshot)
                    if snapshot.has(auth.authority.repo)
                    else None
                )
                if check is None or not check.verified:
                    continue  # an unverifiable authority never suppresses a finding
                art = snapshot.artifact(auth.authority.repo, auth.authority.path)
                assert isinstance(art, Artifact)
                quote = auth.authority.quote or decode(art.data).splitlines()[0]
                ev = Evidence(
                    "repository_authority",
                    art.repo,
                    art.slug,
                    art.sha,
                    art.path,
                    auth.statement,
                    "text",
                    quote,
                    art.content_sha256,
                    art.blob_sha,
                    None,
                    None,
                    "repository_authority@1",
                )
                converted = dataclasses.replace(
                    o, status=Status.POLICY_CONFLICT, evidence=(*o.evidence, ev),
                    reason=f"{o.reason} The repository's own authority governs this: {auth.statement}",
                    detail={**o.detail, "repository_authority": auth.id},
                )  # fmt: skip
                break
            result.append(converted)
        return result

    def _apply_exceptions(
        self, outcomes: list[Outcome], snapshot: Snapshot, as_of: date
    ) -> tuple[list[Outcome], list[ExceptionState]]:
        states: list[ExceptionState] = []
        valid: list[ExceptionDecl] = []
        for exc in self.decl.exceptions:
            if as_of > exc.review_by:
                states.append(
                    ExceptionState(
                        exc.id,
                        exc.policy,
                        "expired",
                        exc.review_by.isoformat(),
                        exc.owner,
                        note=f"review date {exc.review_by} passed; the finding returns to confirmed",
                    )
                )
                continue
            repo = self.graph.repositories.get(exc.authority.repo)
            if repo is None or (
                repo.analyzed
                and (
                    not snapshot.has(exc.authority.repo)
                    or not _resolve_authority(exc.policy, exc.authority, snapshot).verified
                )
            ):
                states.append(
                    ExceptionState(
                        exc.id,
                        exc.policy,
                        "authority_unresolved",
                        exc.review_by.isoformat(),
                        exc.owner,
                        note="authority artifact or quote not found at the pinned revision; not applied",
                    )
                )
                continue
            valid.append(exc)
            states.append(ExceptionState(exc.id, exc.policy, "unused", exc.review_by.isoformat(), exc.owner))
        by_id = {s.id: s for s in states}
        result: list[Outcome] = []
        for o in outcomes:
            if o.status is not Status.INCONSISTENT:
                result.append(o)
                continue
            expired = [
                s.id
                for s in states
                if s.state == "expired" and self._matches(next(e for e in self.decl.exceptions if e.id == s.id), o)
            ]
            match = next((e for e in valid if self._matches(e, o)), None)
            if match is None:
                result.append(
                    dataclasses.replace(o, detail={**o.detail, "expired_exceptions": expired}) if expired else o
                )
                continue
            state = by_id[match.id]
            by_id[match.id] = dataclasses.replace(
                state, state="applied", matched=(*state.matched, f"{o.relationship_id}/{o.subject}")
            )
            result.append(dataclasses.replace(o, status=Status.EXCEPTED, intent=Intent.INTENTIONAL, exception_id=match.id,
                                              reason=f"{o.reason} Excepted by {match.id} (owner {match.owner}, review by {match.review_by}): {match.justification}"))  # fmt: skip
        return result, sorted(by_id.values(), key=lambda s: s.id)

    @staticmethod
    def _matches(exc: ExceptionDecl, o: Outcome) -> bool:
        return (
            exc.policy == o.policy_id
            and o.relationship_id in exc.relationships
            and (exc.subject is None or exc.subject == o.subject)
        )


def _resolve_authority(policy_id: str, ref: AuthorityRef, snapshot: Snapshot) -> AuthorityResult:
    art = snapshot.artifact(ref.repo, ref.path)
    if isinstance(art, Missing):
        return AuthorityResult(policy_id, ref.repo, ref.path, ref.quote, False, art.cause)
    if ref.quote is None:
        return AuthorityResult(policy_id, ref.repo, ref.path, ref.quote, True, "resolved")
    try:
        found = ref.quote in decode(art.data)
    except ExtractionError:
        found = False
    return AuthorityResult(
        policy_id, ref.repo, ref.path, ref.quote, found, "resolved, quote verified" if found else "quote not found"
    )


def _ask_when_undetermined(o: Outcome, rel: Relationship) -> Outcome:
    """An unresolved owner decision never becomes a confirmed finding: on an `undetermined` relationship a failing
    check is reported as a question (docs/coherence-model.md; baseline Q1)."""
    if rel.applicability is not Applicability.UNDETERMINED or o.status is not Status.INCONSISTENT:
        return o
    question = rel.open_question or ""
    return dataclasses.replace(
        o, status=Status.POLICY_CLARIFICATION, severity=None, impact=None, intent=Intent.UNDETERMINED, fingerprint=None,
        open_questions=(*o.open_questions, question), reason=f"{o.reason} Whether this relationship is in scope is undecided ({question}).",
    )  # fmt: skip


def _failed(o: Outcome, cause: str, reason: str) -> Outcome:
    return Outcome(
        o.policy_id,
        o.policy_version,
        o.relationship_id,
        o.subject,
        Status.FAILED,
        reason,
        cause=cause,
        locators=o.locators,
    )
