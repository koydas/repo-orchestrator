"""Filesystem cache of immutable artifacts keyed by (repository, SHA, path); doubles as the offline fixture format."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import cast

from ..models import Cause
from .pr import PullRequest, PullRequestSource, fixture_json, parse_fixture
from .source import SHA_RE, SnapshotSource, SourceError, Tree, TreeEntry, git_blob_sha, safe_relative_path


class CachingSource:
    """Read-through cache. With ``inner=None`` it is a pure offline source: anything not cached is an error."""

    def __init__(self, inner: SnapshotSource | None, root: Path) -> None:
        self._inner = inner
        self._root = root
        self.hits = 0
        self.misses = 0

    def _dir(self, slug: str, sha: str) -> Path:
        owner, _, name = slug.partition("/")
        if not (safe_relative_path(owner) and safe_relative_path(name) and SHA_RE.match(sha)):
            raise SourceError(Cause.FETCH_FAILED, f"unsafe cache key {slug}@{sha}")
        return self._root / owner / name / sha

    def resolve(self, slug: str, ref: str | None) -> str:
        if self._inner is not None:
            return self._inner.resolve(slug, ref)
        if ref is not None and SHA_RE.match(ref):
            return ref
        raise SourceError(Cause.NOT_CACHED, f"offline: cannot resolve moving ref {ref or 'HEAD'} of {slug}; pin a SHA")

    # -- pull requests (M2) ----------------------------------------------------------------------------
    def _pr_dir(self, slug: str, number: int) -> Path:
        owner, _, name = slug.partition("/")
        if not (safe_relative_path(owner) and safe_relative_path(name)) or number < 1:
            raise SourceError(Cause.FETCH_FAILED, f"unsafe cache key {slug}#{number}")
        return self._root / owner / name / "pulls" / str(number)

    def pull_request(self, slug: str, number: int, head_sha: str | None = None) -> PullRequest:
        """Online: always asks the inner source (a PR is mutable) and records the answer under its head SHA, which makes the
        record immutable. Offline: the recorded answer; several recorded heads need an explicit ``head_sha``."""
        directory = self._pr_dir(slug, number)
        if self._inner is not None:
            inner = self._inner
            if not hasattr(inner, "pull_request"):
                raise SourceError(Cause.FETCH_FAILED, "the underlying source cannot read pull requests")
            pr: PullRequest = cast(PullRequestSource, inner).pull_request(slug, number, head_sha)
            self._atomic_write(directory / f"{pr.head_sha}.json", fixture_json(pr).encode("utf-8"))
            return pr
        if head_sha is not None and not SHA_RE.match(head_sha):
            raise SourceError(Cause.FETCH_FAILED, f"{head_sha!r} is not a 40-character commit SHA")
        recorded = sorted(directory.glob("*.json")) if directory.is_dir() else []
        if head_sha is not None:
            recorded = [p for p in recorded if p.name == f"{head_sha}.json"]
        if not recorded:
            raise SourceError(Cause.NOT_CACHED, f"offline: no recorded pull request {slug}#{number}")
        if len(recorded) > 1:
            heads = ", ".join(p.stem[:12] for p in recorded)
            raise SourceError(
                Cause.NOT_CACHED, f"offline: {slug}#{number} has several recorded heads ({heads}); pass --head-sha"
            )
        try:
            raw = recorded[0].read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise SourceError(Cause.FETCH_FAILED, f"unreadable pull request record {recorded[0].name}") from exc
        pr = parse_fixture(slug, number, raw)
        if pr.head_sha != recorded[0].stem:
            raise SourceError(Cause.INTEGRITY, f"{recorded[0].name} records head {pr.head_sha[:12]}")
        return pr

    def tree(self, slug: str, sha: str) -> Tree:
        path = self._dir(slug, sha) / "tree.json"
        cached = self._load_tree(path, sha)
        if cached is not None:
            self.hits += 1
            return cached
        self.misses += 1
        if self._inner is None:
            raise SourceError(Cause.NOT_CACHED, f"offline: no cached tree for {slug}@{sha}")
        tree = self._inner.tree(slug, sha)
        if not tree.truncated:
            self._store_tree(path, tree)
        return tree

    def read(self, slug: str, sha: str, entry: TreeEntry) -> bytes:
        if not safe_relative_path(entry.path):
            raise SourceError(Cause.FETCH_FAILED, f"unsafe path {entry.path!r}")
        path = self._dir(slug, sha) / "files" / entry.path
        if path.is_file():
            data = path.read_bytes()
            if git_blob_sha(data) == entry.blob_sha:
                self.hits += 1
                return data
            if self._inner is None:
                raise SourceError(Cause.INTEGRITY, f"cached {entry.path} does not match its blob id")
        self.misses += 1
        if self._inner is None:
            raise SourceError(Cause.NOT_CACHED, f"offline: {slug}@{sha}:{entry.path} is not cached")
        data = self._inner.read(slug, sha, entry)
        self._atomic_write(path, data)
        return data

    # -- storage -------------------------------------------------------------------------------------
    @staticmethod
    def _load_tree(path: Path, sha: str) -> Tree | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entries = tuple(TreeEntry(p, t, s, b) for p, t, s, b in raw["entries"])
            if raw["sha"] != sha:
                return None
            return Tree(sha, entries, bool(raw.get("truncated", False)))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _store_tree(self, path: Path, tree: Tree) -> None:
        lines = ",\n".join("    " + json.dumps([e.path, e.type, e.size, e.blob_sha]) for e in tree.entries)
        body = f'{{\n  "sha": {json.dumps(tree.sha)},\n  "truncated": false,\n  "entries": [\n{lines}\n  ]\n}}\n'
        self._atomic_write(path, body.encode("utf-8"))

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
