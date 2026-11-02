"""The I/O boundary: everything above this protocol works on fixed snapshots."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Protocol

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class SourceError(Exception):
    """A collection failure with a stable ``kind`` (a :class:`~repo_orchestrator.models.Cause` code)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class TreeEntry:
    path: str
    type: str  # blob | tree
    size: int | None
    blob_sha: str  # git object id


@dataclass(frozen=True)
class Tree:
    sha: str
    entries: tuple[TreeEntry, ...]
    truncated: bool = False


class SnapshotSource(Protocol):
    def resolve(self, slug: str, ref: str | None) -> str:
        """Resolve a branch, tag or SHA (``None`` = default branch) to an immutable commit SHA."""

    def tree(self, slug: str, sha: str) -> Tree:
        """Recursive listing of the repository at ``sha``."""

    def read(self, slug: str, sha: str, entry: TreeEntry) -> bytes:
        """Content of one blob entry of that tree."""


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def safe_relative_path(path: str) -> bool:
    if not path or path.startswith("/") or "\\" in path or "\0" in path:
        return False
    return all(part not in ("", ".", "..") for part in path.split("/"))
