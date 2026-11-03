"""Shared test helpers: offline baseline source, mutations, synthetic worlds. No network anywhere."""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Callable, Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

from repo_orchestrator.collector.cache import CachingSource
from repo_orchestrator.collector.overlay import Mutation, OverlaySource
from repo_orchestrator.collector.pr import PullRequest
from repo_orchestrator.collector.source import SnapshotSource, SourceError, Tree, TreeEntry, git_blob_sha
from repo_orchestrator.declarations import Declarations, load_declarations
from repo_orchestrator.engine import Engine, RunOptions
from repo_orchestrator.models import Outcome, Relationship, Status
from repo_orchestrator.pr_runner import PRAnalysis, analyze_pull_request
from repo_orchestrator.runner import ScanReport, run_scan

ROOT = Path(__file__).resolve().parent.parent
DECLARATIONS = ROOT / "declarations"
FIXTURES = ROOT / "tests" / "fixtures" / "baseline"
AS_OF = date(2026, 11, 2)


def load_decl() -> Declarations:
    return load_declarations(DECLARATIONS)


def baseline_revisions(decl: Declarations) -> dict[str, str | None]:
    return dict(decl.pins["baseline"].revisions)


def baseline_source(mutations: Iterable[Mutation] = ()) -> SnapshotSource:
    base = CachingSource(None, FIXTURES)
    muts = list(mutations)
    return OverlaySource(base, muts) if muts else base


def scan(
    mutations: Iterable[Mutation] = (),
    *,
    decl: Declarations | None = None,
    source: SnapshotSource | None = None,
    scope: tuple[str, ...] | None = None,
    as_of: date = AS_OF,
    engine: Engine | None = None,
    store: Any = None,
) -> ScanReport:
    decl = decl or load_decl()
    engine = engine or Engine(decl)
    src = source or baseline_source(mutations)
    options = RunOptions(revisions=baseline_revisions(decl), scope=scope, as_of=as_of)
    return run_scan(engine, src, options, store=store, overlay_digest=getattr(src, "digest", None))


def find(report: ScanReport | list[Outcome], policy: str, rel: str, subject: str | None = None) -> list[Outcome]:
    outcomes = report.result.outcomes if isinstance(report, ScanReport) else report
    return [
        o
        for o in outcomes
        if o.policy_id == policy and o.relationship_id == rel and (subject is None or o.subject == subject)
    ]


def one(report: ScanReport, policy: str, rel: str, subject: str) -> Outcome:
    found = find(report, policy, rel, subject)
    assert len(found) == 1, (
        f"expected one {policy}/{rel}/{subject}, got {[(o.status, o.subject) for o in find(report, policy, rel)]}"
    )
    return found[0]


def statuses(report: ScanReport, status: Status) -> list[Outcome]:
    return [o for o in report.result.outcomes if o.status is status]


def fingerprints(report: ScanReport) -> set[str]:
    return {o.fingerprint for o in report.result.outcomes if o.status is Status.INCONSISTENT and o.fingerprint}


def classify(base: ScanReport, head: ScanReport) -> dict[str, str]:
    """Test-side stand-in for M2's base/head comparison: fingerprint → introduced | pre-existing | resolved."""
    b, h = fingerprints(base), fingerprints(head)
    return {**{fp: "pre-existing" if fp in b else "introduced" for fp in h}, **{fp: "resolved" for fp in b - h}}


def with_policy_params(decl: Declarations, policy_id: str, **changes: Any) -> Declarations:
    params = {k: dict(v) for k, v in decl.policy_params.items()}
    params[policy_id] = {**params[policy_id], **changes}
    return dataclasses.replace(decl, policy_params=params)


def with_contract(decl: Declarations, rel_id: str, edit: Callable[[dict[str, Any]], None]) -> Declarations:
    import copy

    rels: list[Relationship] = []
    for r in decl.relationships:
        if r.id == rel_id:
            contract = copy.deepcopy(dict(r.contract))
            edit(contract)
            r = dataclasses.replace(r, contract=contract)
        rels.append(r)
    return dataclasses.replace(decl, relationships=tuple(rels))


# ---- synthetic in-memory world -------------------------------------------------------------------------


class MemorySource:
    """A tiny fake of the whole GitHub boundary: ``{slug: {path: text}}`` at a fake, stable SHA per slug."""

    def __init__(
        self, repos: Mapping[str, Mapping[str, str]], *, fail: Mapping[str, SourceError] | None = None
    ) -> None:
        self.repos = {k: {p: v.encode() for p, v in files.items()} for k, files in repos.items()}
        self.fail = dict(fail or {})
        self.reads: list[tuple[str, str]] = []

    @staticmethod
    def sha_of(slug: str) -> str:
        return hashlib.sha1(slug.encode(), usedforsecurity=False).hexdigest()

    def resolve(self, slug: str, ref: str | None) -> str:
        if slug in self.fail:
            raise self.fail[slug]
        if slug not in self.repos:
            raise SourceError("repository_not_found", slug)
        return self.sha_of(slug)

    def tree(self, slug: str, sha: str) -> Tree:
        files = self.repos[slug]
        entries = {p: TreeEntry(p, "blob", len(d), git_blob_sha(d)) for p, d in files.items()}
        for p in list(entries):
            parts = p.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                d = "/".join(parts[:i])
                entries.setdefault(d, TreeEntry(d, "tree", None, "0" * 40))
        return Tree(sha, tuple(sorted(entries.values(), key=lambda e: e.path)))

    def read(self, slug: str, sha: str, entry: TreeEntry) -> bytes:
        self.reads.append((slug, entry.path))
        return self.repos[slug][entry.path]


# ---- pull requests (M2) --------------------------------------------------------------------------------------


class PRWorld:
    """The offline world of one pull request: PR metadata from the committed fixture (``tests/fixtures/pr``), the PR
    repository at its base (the baseline revision) and at its head (base + the case's mutations, in memory), and every other
    repository at the baseline, optionally with ``world`` mutations already applied (a coherent base)."""

    def __init__(
        self, case: Any, *, fail: Mapping[str, SourceError] | None = None, pr: PullRequest | None = None
    ) -> None:
        from pr_cases import PR_FIXTURES  # tests/pr_cases.py

        self.case = case
        self.fail = dict(fail or {})
        self._pr = pr
        self._records = CachingSource(None, PR_FIXTURES)
        self._world = (
            OverlaySource(CachingSource(None, FIXTURES), list(case.world))
            if case.world
            else CachingSource(None, FIXTURES)
        )
        self._base_src: SnapshotSource = OverlaySource(self._world, list(case.base)) if case.base else self._world
        self._head = OverlaySource(self._base_src, list(case.mutations))
        self.requests: list[str] = []

    def pull_request(self, slug: str, number: int, head_sha: str | None = None) -> PullRequest:
        self.requests.append(f"pull_request {slug}#{number}")
        if "pull_request" in self.fail:
            raise self.fail["pull_request"]
        return self._pr or self._records.pull_request(slug, number, head_sha)

    def resolve(self, slug: str, ref: str | None) -> str:
        if ref is None:
            raise SourceError("not_cached_offline", f"offline: cannot resolve {slug} HEAD")
        return ref

    def _key(self, slug: str, sha: str) -> tuple[SnapshotSource, str]:
        if slug == self.case.slug and sha == self.case.head_sha:
            if "head" in self.fail:
                raise self.fail["head"]
            return self._head, self.case.base_sha
        if slug == self.case.slug and sha == self.case.base_sha:
            if "base" in self.fail:
                raise self.fail["base"]
            return self._base_src, sha
        return self._world, sha

    def tree(self, slug: str, sha: str) -> Tree:
        source, real = self._key(slug, sha)
        tree = source.tree(slug, real)
        return Tree(sha, tree.entries, tree.truncated)

    def read(self, slug: str, sha: str, entry: TreeEntry) -> bytes:
        source, real = self._key(slug, sha)
        return source.read(slug, real, entry)


def pr_options(**kw: Any) -> RunOptions:
    decl = load_decl()
    return RunOptions(revisions=baseline_revisions(decl), as_of=kw.pop("as_of", AS_OF), **kw)


def analyze(
    case: Any,
    *,
    world: PRWorld | None = None,
    decl: Declarations | None = None,
    store: Any = None,
    head_sha: str | None = None,
) -> PRAnalysis:
    decl = decl or load_decl()
    world = world or PRWorld(case)
    return analyze_pull_request(
        Engine(decl), world, case.slug, case.number, pr_options(), head_sha=head_sha, store=store
    )


def materialize_pr_cache(root: Path, cases: Iterable[Any]) -> Path:
    """An offline cache directory for the CLI: the baseline fixtures plus, per case, the PR record (copied from the committed
    fixture) and the head tree with the files that differ from base, in the cache layout ``<owner>/<name>/<sha>/``."""
    import json
    import shutil

    from pr_cases import PR_FIXTURES, fixture_path

    shutil.copytree(FIXTURES, root, dirs_exist_ok=True)
    for case in cases:
        record = fixture_path(case, root)
        record.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fixture_path(case, PR_FIXTURES), record)
        world = PRWorld(case)
        owner, name = case.slug.split("/")
        head_dir = root / owner / name / case.head_sha
        base_tree = world.tree(case.slug, case.base_sha)
        head_tree = world.tree(case.slug, case.head_sha)
        shutil.copytree(root / owner / name / case.base_sha / "files", head_dir / "files", dirs_exist_ok=True)
        known = {e.path: e.blob_sha for e in base_tree.entries}
        for entry in head_tree.entries:
            if entry.type == "blob" and known.get(entry.path) != entry.blob_sha:
                try:
                    data = world.read(case.slug, case.head_sha, entry)
                except SourceError:
                    continue  # content of a renamed file that was never cached: nothing reads it
                target = head_dir / "files" / entry.path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        listing = [[e.path, e.type, e.size, e.blob_sha] for e in head_tree.entries]
        (head_dir / "tree.json").write_text(
            json.dumps({"sha": case.head_sha, "truncated": False, "entries": listing}) + "\n", encoding="utf-8"
        )
    return root
