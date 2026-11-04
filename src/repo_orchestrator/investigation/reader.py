"""Read-only view of the run's pinned snapshots for the Investigator (C5's only window on the repositories).

No network of its own (a :class:`SnapshotSource` does the fetching and is the existing read-only collector), no shell,
no write. Every file served is checked against the blob id of the pinned tree, so what the model sees and what a quote is
verified against are the same bytes.
"""

from __future__ import annotations

from collections.abc import Collection

from .. import extract
from ..collector.snapshot import Artifact, Limits, Missing, Snapshot
from ..collector.source import SnapshotSource, SourceError, git_blob_sha
from ..models import Cause

OUT_OF_SCOPE = "repo_out_of_scope"


class PinnedReader:
    def __init__(
        self, snapshot: Snapshot, source: SnapshotSource | None, allowed: Collection[str], limits: Limits | None = None
    ) -> None:
        self._snapshot = snapshot
        self._source = source
        self._allowed = frozenset(allowed)
        self._limits = limits or Limits()
        self._extra: dict[tuple[str, str], Artifact | Missing] = {}
        self.accessed: dict[tuple[str, str], Artifact] = {}  # the manifest of what was served

    def allowed(self, repo: str) -> bool:
        return repo in self._allowed and self._snapshot.has(repo)

    def sha(self, repo: str) -> str | None:
        if not self.allowed(repo):
            return None
        rev = self._snapshot.revision(repo)
        return None if isinstance(rev, Missing) else rev

    def slug(self, repo: str) -> str:
        return self._snapshot.repo(repo).slug

    def artifact(self, repo: str, path: str) -> Artifact | Missing:
        if not self.allowed(repo):
            return Missing(OUT_OF_SCOPE, f"{repo} is not readable for this investigation")
        found = self._snapshot.artifact(repo, path)
        if isinstance(found, Missing) and found.cause == Cause.NOT_COLLECTED:
            found = self._fetch(repo, path)
        if isinstance(found, Artifact):
            self.accessed[(repo, path)] = found
        return found

    def _fetch(self, repo: str, path: str) -> Artifact | Missing:
        key = (repo, path)
        if key in self._extra:
            return self._extra[key]
        snap = self._snapshot.repo(repo)
        entry = next((e for e in snap.tree.entries if e.path == path), None) if snap.tree else None
        result: Artifact | Missing
        if entry is None or entry.type != "blob":
            result = Missing(Cause.NOT_FOUND, f"{snap.slug}@{snap.sha}: {path} does not exist")
        elif entry.size is not None and entry.size > self._limits.max_file_bytes:
            result = Missing(Cause.TOO_LARGE, f"{path} is {entry.size} bytes")
        elif self._source is None or snap.sha is None:
            result = Missing(Cause.NOT_CACHED, f"{path} is not available offline")
        else:
            try:
                data = self._source.read(snap.slug, snap.sha, entry)
            except SourceError as exc:
                result = Missing(exc.kind, exc.message)
            else:
                if git_blob_sha(data) != entry.blob_sha:
                    result = Missing(Cause.INTEGRITY, f"{path} does not match its blob id")
                else:
                    result = Artifact(repo, snap.slug, snap.sha, path, data, entry.blob_sha)
        self._extra[key] = result
        return result

    def text(self, repo: str, path: str) -> tuple[Artifact, str] | Missing:
        art = self.artifact(repo, path)
        if isinstance(art, Missing):
            return art
        try:
            return art, extract.decode(art.data)
        except extract.ExtractionError as exc:
            return Missing(exc.cause, f"{path}: {exc.detail}")

    def listing(self, repo: str, prefix: str) -> list[str] | Missing:
        if not self.allowed(repo):
            return Missing(OUT_OF_SCOPE, f"{repo} is not readable for this investigation")
        found = self._snapshot.entries(repo, prefix)
        if isinstance(found, Missing):
            return found
        return sorted(e.path for e in found)
