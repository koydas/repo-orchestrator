"""W2 as one function: ``resolve PR → pin base/head → change set → C4 → evaluate touched → delta → report → persist``.

Same shape as M1's :func:`~repo_orchestrator.runner.run_scan`: no framework, no service. All I/O goes through the sources
handed in; the analysis itself runs on frozen snapshots. Nothing is written to GitHub, and nothing is written to the
baseline-scan history of the run store.
"""

from __future__ import annotations

import dataclasses
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from .collector.pr import ChangedFile, PullRequest, PullRequestSource
from .collector.snapshot import ArtifactRequest, Missing, RepoSnapshot, RepoTarget, Snapshot, collect
from .collector.source import SnapshotSource, SourceError, Tree, TreeEntry
from .delta import Delta, classify_delta
from .engine import Engine, RunOptions, ScanResult
from .extract import ExtractionError, decode
from .impact import FootprintUnavailable, ImpactAssessment, analyze, value_changes
from .models import Cause, ConfigError, digest
from .pr_report import build_pr_semantic, pr_exit_code
from .runner import DeterminismError
from .store import PRRecordResult, RunStore


class PRSource(SnapshotSource, PullRequestSource, Protocol):
    """A source that serves trees, blobs and pull requests (GitHubSource and CachingSource do)."""


@dataclass(frozen=True)
class Problem:
    """Something that stops the analysis from being complete. Never a pass."""

    side: str  # pull_request | base | head | change_set
    cause: str
    detail: str


@dataclass
class PRAnalysis:
    semantic: dict[str, Any]
    execution: dict[str, Any]
    exit_code: int
    record: PRRecordResult | None
    deltas: list[Delta] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)


class _Memo:
    """In-memory memo of ``resolve`` and ``tree`` for the duration of one analysis: base and head evaluations read the same
    other repositories, so each is resolved and listed once (fewer requests, and one pinned SHA per repository)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._resolved: dict[tuple[str, str | None], str | SourceError] = {}
        self._trees: dict[tuple[str, str], Tree | SourceError] = {}

    def resolve(self, slug: str, ref: str | None) -> str:
        key = (slug, ref)
        if key not in self._resolved:
            try:
                self._resolved[key] = self._inner.resolve(slug, ref)
            except SourceError as exc:  # a failure is memoized too: it is not retried by every later reader
                self._resolved[key] = exc
        found = self._resolved[key]
        if isinstance(found, SourceError):
            raise found
        return found

    def tree(self, slug: str, sha: str) -> Tree:
        if (slug, sha) not in self._trees:
            try:
                self._trees[(slug, sha)] = self._inner.tree(slug, sha)
            except SourceError as exc:
                self._trees[(slug, sha)] = exc
        found = self._trees[(slug, sha)]
        if isinstance(found, SourceError):
            raise found
        return found

    def read(self, slug: str, sha: str, entry: TreeEntry) -> bytes:
        data: bytes = self._inner.read(slug, sha, entry)
        return data


class _Reader:
    """Reads the living documents whose links define a dynamic footprint (C4), at the pinned revision of their repository."""

    def __init__(self, engine: Engine, source: SnapshotSource, pins: dict[str, str | None]) -> None:
        self._engine = engine
        self._source = source
        self._pins = pins
        self._wanted: dict[str, set[str]] = {}
        for rel in engine.graph.trusted():
            for src in rel.contract.get("references") or []:
                if src.get("role") == "living":
                    self._wanted.setdefault(src["repo"], set()).add(src["path"])
        self._snapshots: dict[str, Snapshot] = {}

    def __call__(self, repo: str, path: str) -> str:
        if repo not in self._snapshots:
            slug = self._engine.graph.repositories[repo].slug
            requests = [ArtifactRequest(repo, p) for p in sorted(self._wanted.get(repo, {path}) | {path})]
            self._snapshots[repo] = collect(self._source, [RepoTarget(repo, slug, self._pins.get(repo))], requests)
        art = self._snapshots[repo].artifact(repo, path)
        if isinstance(art, Missing):
            raise FootprintUnavailable(art.cause)
        try:
            return decode(art.data)
        except ExtractionError as exc:
            raise FootprintUnavailable(exc.cause) from exc


def tree_changes(base: Tree, head: Tree) -> set[str]:
    """Blob paths whose content differs between two trees (added, removed or modified)."""
    b = {e.path: e.blob_sha for e in base.entries if e.type == "blob"}
    h = {e.path: e.blob_sha for e in head.entries if e.type == "blob"}
    return {p for p in b.keys() | h.keys() if b.get(p) != h.get(p)}


def declared_repo_id(engine: Engine, slug: str) -> str:
    for repo in engine.graph.repositories.values():
        if repo.slug.lower() == slug.lower():
            if not repo.analyzed:
                raise ConfigError(
                    f"{repo.slug} is declared but outside the analyzed scope (M1-M2 analyze six repositories)"
                )
            return repo.id
    declared = sorted(r.slug for r in engine.graph.repositories.values() if r.analyzed)
    raise ConfigError(f"{slug} is not a declared repository (declared: {', '.join(declared)})")


def _needed_repos(engine: Engine, pr_repo: str, touched: frozenset[str]) -> tuple[str, ...]:
    """Repositories the evaluation needs: the PR repository, both endpoints of each touched relationship, and the
    repositories of the authorities, exceptions and repository authorities that can decide those relationships."""
    repos = {pr_repo}
    for rel in engine.graph.trusted():
        if rel.id not in touched:
            continue
        repos |= {rel.source, rel.target}
        for policy in engine.relationship_policies(rel.id):
            repos |= {a.repo for a in engine.authority_refs(policy.meta.id)}
    repos |= {e.authority.repo for e in engine.decl.exceptions if touched & set(e.relationships)}
    repos |= {a.authority.repo for a in engine.decl.repository_authorities if touched & set(a.relationships)}
    return tuple(sorted(r for r in repos if engine.graph.repositories[r].analyzed))


def _pin_others(
    engine: Engine, source: SnapshotSource, pr_repo: str, requested: dict[str, str | None]
) -> dict[str, str | None]:
    """One immutable SHA per other repository, resolved once. A repository that cannot be resolved keeps its requested ref:
    the collector then records the failure and every policy that needs it is ``cannot_evaluate``."""
    pins: dict[str, str | None] = {}
    for repo in engine.graph.repositories.values():
        if not repo.analyzed or repo.id == pr_repo:
            continue
        try:
            pins[repo.id] = source.resolve(repo.slug, requested.get(repo.id))
        except SourceError:
            pins[repo.id] = requested.get(repo.id)
    return pins


def _snapshot_side(source: SnapshotSource, repo: str, slug: str, sha: str) -> RepoSnapshot:
    return collect(source, [RepoTarget(repo, slug, sha)], []).repo(repo)


def analyze_pull_request(
    engine: Engine,
    source: PRSource,
    slug: str,
    number: int,
    options: RunOptions,
    *,
    head_sha: str | None = None,
    store: RunStore | None = None,
    stats: Callable[[], dict[str, int]] | None = None,
) -> PRAnalysis:
    started = datetime.now(UTC)
    t0 = time.monotonic()
    pr_repo = declared_repo_id(engine, slug)  # ConfigError → exit 3: the PR repository must be declared and analyzed
    decl_slug = engine.graph.repositories[pr_repo].slug
    if options.relationships is not None:
        raise ConfigError("a pull request analysis selects the relationships itself")
    memo = _Memo(source)
    problems: list[Problem] = []
    pr: PullRequest | None = None
    try:
        pr = source.pull_request(decl_slug, number, head_sha)
    except SourceError as exc:
        problems.append(Problem("pull_request", exc.kind, exc.message))

    pins = _pin_others(engine, memo, pr_repo, dict(options.revisions))
    base_snap = head_snap = None
    files: tuple[ChangedFile, ...] = ()
    changed: set[str] = set()
    if pr is not None:
        files = pr.files
        base_snap = _snapshot_side(memo, pr_repo, decl_slug, pr.base_sha)
        head_snap = _snapshot_side(memo, pr_repo, decl_slug, pr.head_sha)
        changed = {f.path for f in files} | {f.previous_path for f in files if f.previous_path}
        for side, snap in (("base", base_snap), ("head", head_snap)):
            if snap.failure is not None:
                problems.append(
                    Problem(side, snap.failure.cause, f"{snap.slug}@{snap.requested[:12]}: {snap.failure.detail}")
                )
        if not pr.files_complete:
            problems.append(
                Problem(
                    "change_set",
                    Cause.PR_FILES_TRUNCATED,
                    f"GitHub reports {pr.changed_files} changed files, {len(pr.files)} were listed (the API lists at most 3000)",
                )
            )
        if base_snap.tree is not None and head_snap.tree is not None:
            if base_snap.tree.truncated or head_snap.tree.truncated:
                problems.append(
                    Problem(
                        "change_set",
                        Cause.TREE_TRUNCATED,
                        "a tree listing is truncated: the file list cannot be cross-checked",
                    )
                )
            else:
                diff = tree_changes(base_snap.tree, head_snap.tree)
                unexplained = sorted(diff - changed)
                if unexplained:
                    shown = ", ".join(unexplained[:5]) + (" …" if len(unexplained) > 5 else "")
                    problems.append(
                        Problem(
                            "change_set",
                            Cause.PR_FILE_LIST_MISMATCH,
                            f"{len(unexplained)} path(s) differ between base and head but are not in the PR file list ({shown}); "
                            "the base is not the merge base, or the list is incomplete",
                        )
                    )
                changed |= diff  # the trees are the evidence; the API list can only add renames and mode changes

    assessment = ImpactAssessment()
    if pr is not None and changed:
        assessment = analyze(engine, pr_repo, changed, _Reader(engine, memo, pins))
    elif pr is not None:
        assessment = ImpactAssessment(
            not_touched=sorted(r.id for r in engine.graph.trusted() if pr_repo in (r.source, r.target))
        )

    base_result: ScanResult | None = None
    head_result: ScanResult | None = None
    deltas: list[Delta] = []
    values: list[dict[str, object]] = []
    evaluable = (
        pr is not None
        and base_snap is not None
        and head_snap is not None
        and not (base_snap.failure or head_snap.failure)
    )
    if evaluable and assessment.touched:
        assert pr is not None
        scope = _needed_repos(engine, pr_repo, assessment.touched)
        shared = dataclasses.replace(options, scope=scope, relationships=assessment.touched)
        base_result = engine.run(memo, dataclasses.replace(shared, revisions={**pins, pr_repo: pr.base_sha}))
        head_result = engine.run(memo, dataclasses.replace(shared, revisions={**pins, pr_repo: pr.head_sha}))
        enumerated = {
            p.meta.id: p.meta.enumerated_subjects for p in engine.policies.values() if p.meta.enumerated_subjects
        }
        deltas = classify_delta(base_result.outcomes, head_result.outcomes, enumerated)
        values = value_changes(base_result.outcomes, head_result.outcomes)

    semantic = build_pr_semantic(
        engine=engine,
        options=options,
        pr_repo=pr_repo,
        slug=decl_slug,
        number=number,
        pr=pr,
        pins=pins,
        files=files,
        changed=sorted(changed),
        problems=problems,
        assessment=assessment,
        base=base_result,
        head=head_result,
        deltas=deltas,
        values=values,
    )
    record = None
    if store is not None:
        record = store.record_pr(semantic, [d for d in deltas])
        if semantic["run_status"] == "complete":
            divergent = store.divergent_complete_pr_runs(digest(semantic["inputs"]))
            if divergent:
                raise DeterminismError(f"identical inputs produced {len(divergent)} different results: {divergent}")
    execution = {
        "run_id": uuid.uuid4().hex,
        "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_ms": round((time.monotonic() - t0) * 1000),
        "stats": stats() if stats else {},
        "pull_request": {"state": pr.state, "merged": pr.merged, "base_ref": pr.base_ref, "head_ref": pr.head_ref}
        if pr
        else None,
        "persisted": record is not None,
        "run_key": record.run_key if record else None,
        "new_run": record.new if record else None,
    }
    return PRAnalysis(semantic, execution, pr_exit_code(semantic), record, deltas, problems)
