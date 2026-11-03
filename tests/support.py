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
from repo_orchestrator.collector.source import SnapshotSource, SourceError, Tree, TreeEntry, git_blob_sha
from repo_orchestrator.declarations import Declarations, load_declarations
from repo_orchestrator.engine import Engine, RunOptions
from repo_orchestrator.models import Outcome, Relationship, Status
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
