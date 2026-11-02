"""Snapshot: the frozen, in-memory result of a collection. Policies run on this and nothing else."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from ..models import Cause
from .source import SnapshotSource, SourceError, Tree, TreeEntry


@dataclass(frozen=True)
class Limits:
    max_file_bytes: int = 1_048_576
    max_files_per_repo: int = 300
    max_total_bytes: int = 16 * 1_048_576
    max_glob_matches: int = 60


@dataclass(frozen=True)
class RepoTarget:
    id: str
    slug: str
    ref: str | None  # None = default branch HEAD; otherwise a pinned SHA (or branch/tag)


@dataclass(frozen=True)
class ArtifactRequest:
    repo: str
    path: str | None = None
    glob: str | None = None

    def __post_init__(self) -> None:
        if (self.path is None) == (self.glob is None):
            raise ValueError("exactly one of path or glob is required")


@dataclass(frozen=True)
class Artifact:
    repo: str
    slug: str
    sha: str
    path: str
    data: bytes
    blob_sha: str

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True)
class Missing:
    cause: str
    detail: str


@dataclass
class RepoSnapshot:
    id: str
    slug: str
    requested: str  # "HEAD" or the pinned ref
    sha: str | None = None
    tree: Tree | None = None
    failure: Missing | None = None
    artifacts: dict[str, Artifact | Missing] = field(default_factory=dict)
    globs: dict[str, tuple[str, ...] | Missing] = field(default_factory=dict)


def glob_regex(pattern: str) -> re.Pattern[str]:
    """``*`` matches within a path segment, ``**`` across segments, ``?`` one non-slash character."""
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if c == "*":
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out) + r"\Z")


class Snapshot:
    def __init__(self, repos: Mapping[str, RepoSnapshot]) -> None:
        self._repos = dict(repos)

    def ids(self) -> list[str]:
        return sorted(self._repos)

    def repo(self, repo_id: str) -> RepoSnapshot:
        return self._repos[repo_id]

    def has(self, repo_id: str) -> bool:
        return repo_id in self._repos

    def revision(self, repo_id: str) -> str | Missing:
        snap = self._repos[repo_id]
        if snap.sha is None or snap.failure is not None:
            return snap.failure or Missing(Cause.REVISION_UNRESOLVED, f"{snap.slug} has no pinned revision")
        return snap.sha

    def artifact(self, repo_id: str, path: str) -> Artifact | Missing:
        snap = self._repos[repo_id]
        if snap.failure is not None:
            return snap.failure
        found = snap.artifacts.get(path)
        if found is not None:
            return found
        if snap.tree is not None:
            entry = next((e for e in snap.tree.entries if e.path == path), None)
            if entry is None:
                if snap.tree.truncated:
                    return Missing(Cause.TREE_TRUNCATED, f"{snap.slug}: tree listing is truncated, {path} may exist")
                return Missing(Cause.NOT_FOUND, f"{snap.slug}@{snap.sha}: {path} does not exist")
            if entry.type != "blob":
                return Missing(Cause.NOT_FOUND, f"{snap.slug}@{snap.sha}: {path} is a directory, not a file")
        return Missing(Cause.NOT_COLLECTED, f"{snap.slug}: {path} was never requested (policy bug)")

    def entries(self, repo_id: str, prefix: str = "") -> list[TreeEntry] | Missing:
        """Entries under ``prefix`` (a directory, trailing slash optional); Missing when the listing is unusable."""
        snap = self._repos[repo_id]
        if snap.failure is not None:
            return snap.failure
        if snap.tree is None:
            return Missing(Cause.REVISION_UNRESOLVED, f"{snap.slug}: no tree")
        if snap.tree.truncated:
            return Missing(Cause.TREE_TRUNCATED, f"{snap.slug}: tree listing is truncated; counts would be wrong")
        base = prefix.strip("/")
        wanted = base + "/" if base else ""
        return [e for e in snap.tree.entries if e.path.startswith(wanted)]

    def glob(self, repo_id: str, pattern: str) -> tuple[str, ...] | Missing:
        snap = self._repos[repo_id]
        if snap.failure is not None:
            return snap.failure
        found = snap.globs.get(pattern)
        return found if found is not None else Missing(Cause.NOT_COLLECTED, f"glob {pattern!r} was never requested")


def collect(
    source: SnapshotSource,
    targets: Iterable[RepoTarget],
    requests: Iterable[ArtifactRequest],
    limits: Limits = Limits(),  # noqa: B008 - frozen dataclass
) -> Snapshot:
    """Resolve, list and fetch. Never raises for a source failure: failures become :class:`Missing` records."""
    wanted = sorted(set(requests), key=lambda r: (r.repo, r.path or "", r.glob or ""))
    repos: dict[str, RepoSnapshot] = {}
    total = 0
    for target in targets:
        snap = RepoSnapshot(target.id, target.slug, target.ref or "HEAD")
        repos[target.id] = snap
        try:
            snap.sha = source.resolve(target.slug, target.ref)
            snap.tree = source.tree(target.slug, snap.sha)
        except SourceError as exc:
            snap.failure = Missing(exc.kind, exc.message)
            continue
        by_path = {e.path: e for e in snap.tree.entries}
        paths: list[str] = []
        for req in (r for r in wanted if r.repo == target.id):
            if req.path is not None:
                paths.append(req.path)
            else:
                assert req.glob is not None
                regex = glob_regex(req.glob)
                matches = tuple(sorted(p for p, e in by_path.items() if e.type == "blob" and regex.match(p)))
                if snap.tree.truncated:
                    snap.globs[req.glob] = Missing(Cause.TREE_TRUNCATED, f"{target.slug}: truncated tree")
                elif len(matches) > limits.max_glob_matches:
                    snap.globs[req.glob] = Missing(Cause.TOO_LARGE, f"glob {req.glob!r} matches {len(matches)} files")
                else:
                    snap.globs[req.glob] = matches
                    paths.extend(matches)
        for path in sorted(set(paths)):
            if len(snap.artifacts) >= limits.max_files_per_repo:
                snap.artifacts[path] = Missing(
                    Cause.BUDGET_EXHAUSTED, f"more than {limits.max_files_per_repo} files requested"
                )
                continue
            entry = by_path.get(path)
            if entry is None or entry.type != "blob":
                continue  # reported as artifact_not_found by Snapshot.artifact
            if entry.size is not None and entry.size > limits.max_file_bytes:
                snap.artifacts[path] = Missing(
                    Cause.TOO_LARGE, f"{path} is {entry.size} bytes (limit {limits.max_file_bytes})"
                )
                continue
            if total >= limits.max_total_bytes:
                snap.artifacts[path] = Missing(Cause.BUDGET_EXHAUSTED, "total byte budget reached")
                continue
            try:
                data = source.read(target.slug, snap.sha, entry)
            except SourceError as exc:
                snap.artifacts[path] = Missing(exc.kind, exc.message)
                continue
            total += len(data)
            snap.artifacts[path] = Artifact(target.id, target.slug, snap.sha, path, data, entry.blob_sha)
    return Snapshot(repos)
