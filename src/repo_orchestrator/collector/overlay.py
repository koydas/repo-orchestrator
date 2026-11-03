"""In-memory mutations over a source, for evaluation (mutation cases U1-U5) and, later, PR heads (M2).

The mutated tree reports new blob ids, so integrity checks stay meaningful; nothing is written anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Cause, digest
from .source import SnapshotSource, SourceError, Tree, TreeEntry, git_blob_sha


@dataclass(frozen=True)
class Mutation:
    op: str  # replace_text | add_file | delete | rename
    slug: str
    path: str
    new_path: str = ""
    old: str = ""
    new: str = ""
    content: str = ""


def replace_text(slug: str, path: str, old: str, new: str) -> Mutation:
    return Mutation("replace_text", slug, path, old=old, new=new)


def add_file(slug: str, path: str, content: str) -> Mutation:
    return Mutation("add_file", slug, path, content=content)


def delete_path(slug: str, path: str) -> Mutation:
    return Mutation("delete", slug, path)


def rename_path(slug: str, path: str, new_path: str) -> Mutation:
    return Mutation("rename", slug, path, new_path=new_path)


class OverlaySource:
    def __init__(self, inner: SnapshotSource, mutations: list[Mutation]) -> None:
        self._inner = inner
        self._mutations = tuple(mutations)
        self._contents: dict[str, bytes] = {}
        self._origin: dict[tuple[str, str], TreeEntry] = {}

    @property
    def digest(self) -> str:
        return digest([m for m in self._mutations])

    def resolve(self, slug: str, ref: str | None) -> str:
        return self._inner.resolve(slug, ref)

    def tree(self, slug: str, sha: str) -> Tree:
        base = self._inner.tree(slug, sha)
        entries = {e.path: e for e in base.entries}
        for m in (m for m in self._mutations if m.slug == slug):
            if m.op == "replace_text":
                entry = entries.get(m.path)
                if entry is None:
                    raise SourceError(Cause.NOT_FOUND, f"mutation target {m.path} not in {slug}")
                text = self._read_base(slug, sha, entry).decode("utf-8")
                if m.old not in text:
                    raise SourceError(Cause.LOCATOR_NO_MATCH, f"mutation text {m.old!r} not found in {m.path}")
                self._put(slug, entries, m.path, text.replace(m.old, m.new, 1).encode("utf-8"))
            elif m.op == "add_file":
                self._put(slug, entries, m.path, m.content.encode("utf-8"))
            elif m.op == "delete":
                for p in [p for p in entries if p == m.path or p.startswith(m.path + "/")]:
                    del entries[p]
            elif m.op == "rename":
                moved = [p for p in entries if p == m.path or p.startswith(m.path + "/")]
                if not moved:
                    raise SourceError(Cause.NOT_FOUND, f"rename source {m.path} not in {slug}")
                for p in moved:
                    old_entry = entries.pop(p)
                    new_path = m.new_path + p[len(m.path) :]
                    entries[new_path] = TreeEntry(new_path, old_entry.type, old_entry.size, old_entry.blob_sha)
                    self._origin[(slug, new_path)] = self._origin.get((slug, p), old_entry)
            else:
                raise SourceError(Cause.FETCH_FAILED, f"unknown mutation {m.op}")
        return Tree(sha, tuple(sorted(entries.values(), key=lambda e: e.path)), base.truncated)

    def read(self, slug: str, sha: str, entry: TreeEntry) -> bytes:
        if entry.blob_sha in self._contents:
            return self._contents[entry.blob_sha]
        origin = self._origin.get((slug, entry.path), entry)
        return self._inner.read(slug, sha, origin)

    def _read_base(self, slug: str, sha: str, entry: TreeEntry) -> bytes:
        return self.read(slug, sha, self._origin.get((slug, entry.path), entry))

    def _put(self, slug: str, entries: dict[str, TreeEntry], path: str, data: bytes) -> None:
        blob = git_blob_sha(data)
        self._contents[blob] = data
        entries[path] = TreeEntry(path, "blob", len(data), blob)
        parts = path.split("/")[:-1]
        for i in range(1, len(parts) + 1):  # a new file implies its parent directories
            directory = "/".join(parts[:i])
            entries.setdefault(directory, TreeEntry(directory, "tree", None, "0" * 40))
