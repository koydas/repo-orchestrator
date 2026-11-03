"""C1 extension (M2): the pull-request boundary. GET only; the answer is a frozen, validated :class:`PullRequest`.

A pull request is mutable on GitHub (its head moves, its state changes). What an analysis depends on is therefore pinned
in the record itself: the head and base commit SHAs and the changed-file list. Everything downstream works on those and
on immutable trees; nothing here is executed or imported.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from ..models import Cause
from .source import SHA_RE, SourceError, safe_relative_path

FIXTURE_SCHEMA_VERSION = 1
MAX_FILES = 3000  # GitHub lists at most 3000 files of a pull request; a PR at the cap is treated as truncated
STATUSES = frozenset({"added", "removed", "modified", "renamed", "copied", "changed", "unchanged"})
SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class ChangedFile:
    path: str
    status: str  # added | removed | modified | renamed | copied | changed | unchanged
    previous_path: str | None = None  # set for renames and copies


@dataclass(frozen=True)
class PullRequest:
    slug: str  # the repository the pull request targets
    number: int
    state: str  # open | closed (volatile: kept out of the reproducible report)
    merged: bool
    base_sha: str
    head_sha: str
    head_slug: str  # differs from ``slug`` for a fork
    base_ref: str
    head_ref: str
    changed_files: int  # what GitHub says the PR changes
    files: tuple[ChangedFile, ...]  # what was actually listed

    @property
    def files_complete(self) -> bool:
        return len(self.files) >= self.changed_files and self.changed_files < MAX_FILES

    @property
    def files_digest(self) -> str:
        body = [[f.path, f.status, f.previous_path] for f in sorted(self.files, key=lambda f: (f.path, f.status))]
        return hashlib.sha256(json.dumps(body, separators=(",", ":")).encode()).hexdigest()


class PullRequestSource(Protocol):
    def pull_request(
        self, slug: str, number: int, head_sha: str | None = None, base_sha: str | None = None
    ) -> PullRequest:
        """Resolve ``slug#number`` to head SHA, base SHA and changed files. ``head_sha`` and ``base_sha`` select among
        recorded inputs when reading offline (the same head can be recorded with another base); a live source ignores them."""


def _sha(value: Any, where: str) -> str:
    if not isinstance(value, str) or not SHA_RE.match(value):
        raise SourceError(Cause.FETCH_FAILED, f"{where}: not a 40-character commit SHA")
    return value


def _text(m: Mapping[str, Any], key: str, where: str) -> str:
    value = m.get(key)
    if not isinstance(value, str) or not value:
        raise SourceError(Cause.FETCH_FAILED, f"{where}: missing '{key}'")
    return value


def parse_file(item: Any, where: str) -> ChangedFile:
    """One entry of ``GET pulls/{n}/files`` or of a fixture; anything unexpected is a fetch failure (fail closed)."""
    if not isinstance(item, Mapping):
        raise SourceError(Cause.FETCH_FAILED, f"{where}: file entry is not an object")
    path = item.get("filename", item.get("path"))
    previous = item.get("previous_filename", item.get("previous_path"))
    status = item.get("status")
    if not isinstance(path, str) or not safe_relative_path(path):
        raise SourceError(Cause.FETCH_FAILED, f"{where}: unsafe or missing file path {path!r}")
    if previous is not None and (not isinstance(previous, str) or not safe_relative_path(previous)):
        raise SourceError(Cause.FETCH_FAILED, f"{where}: unsafe previous path {previous!r}")
    if status not in STATUSES:
        raise SourceError(Cause.FETCH_FAILED, f"{where}: unknown file status {status!r}")
    return ChangedFile(path, str(status), previous)


def parse_pull_request(slug: str, number: int, meta: Any, files: Sequence[Any]) -> PullRequest:
    where = f"{slug}#{number}"
    if not isinstance(meta, Mapping):
        raise SourceError(Cause.FETCH_FAILED, f"{where}: unexpected pull request payload")
    head, base = meta.get("head"), meta.get("base")
    if not isinstance(head, Mapping) or not isinstance(base, Mapping):
        raise SourceError(Cause.FETCH_FAILED, f"{where}: pull request has no head or base")
    head_repo = head.get("repo")
    head_slug = head_repo.get("full_name") if isinstance(head_repo, Mapping) else None
    declared = meta.get("changed_files")
    if not isinstance(declared, int) or isinstance(declared, bool) or declared < 0:
        raise SourceError(Cause.FETCH_FAILED, f"{where}: missing changed_files count")
    parsed = tuple(parse_file(f, where) for f in files)
    return PullRequest(
        slug=slug,
        number=number,
        state=str(meta.get("state", "unknown")),
        merged=bool(meta.get("merged", False)),
        base_sha=_sha(base.get("sha"), f"{where} base"),
        head_sha=_sha(head.get("sha"), f"{where} head"),
        # a deleted fork has no head repo: the head commit is then read through the base repository (fail closed if absent)
        head_slug=head_slug if isinstance(head_slug, str) and SLUG_RE.match(head_slug) else slug,
        base_ref=str(base.get("ref", "")),
        head_ref=str(head.get("ref", "")),
        changed_files=declared,
        files=parsed,
    )


def record_name(pr: PullRequest) -> str:
    """One record per (head, base): the base can move without a new head, and the file list follows the pair."""
    return f"{pr.head_sha}.{pr.base_sha}.json"


# ---- fixture format (the cache family: <root>/<owner>/<name>/pulls/<number>/<head sha>.<base sha>.json) ---------------------------


def fixture_json(pr: PullRequest, *, synthetic: str | None = None) -> str:
    body: dict[str, Any] = {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "kind": "pull_request",
        "slug": pr.slug,
        "number": pr.number,
        "head": {"sha": pr.head_sha, "slug": pr.head_slug, "ref": pr.head_ref},
        "base": {"sha": pr.base_sha, "ref": pr.base_ref},
        "state": pr.state,
        "merged": pr.merged,
        "changed_files": pr.changed_files,
        "files": [{"path": f.path, "status": f.status, "previous_path": f.previous_path} for f in pr.files],
    }
    if synthetic:
        body["synthetic"] = synthetic
    return json.dumps(body, indent=2, sort_keys=True) + "\n"


def parse_fixture(slug: str, number: int, raw: str) -> PullRequest:
    where = f"fixture {slug}#{number}"
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise SourceError(Cause.FETCH_FAILED, f"{where}: not valid JSON") from exc
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != FIXTURE_SCHEMA_VERSION
        or data.get("kind") != "pull_request"
    ):
        raise SourceError(
            Cause.FETCH_FAILED, f"{where}: unsupported fixture (expected pull_request schema {FIXTURE_SCHEMA_VERSION})"
        )
    if data.get("slug") != slug or data.get("number") != number:
        raise SourceError(Cause.INTEGRITY, f"{where}: fixture is for {data.get('slug')}#{data.get('number')}")
    meta = {
        "state": data.get("state"),
        "merged": data.get("merged"),
        "changed_files": data.get("changed_files"),
        "head": {
            "sha": (data.get("head") or {}).get("sha"),
            "ref": (data.get("head") or {}).get("ref", ""),
            "repo": {"full_name": (data.get("head") or {}).get("slug")},
        },
        "base": {"sha": (data.get("base") or {}).get("sha"), "ref": (data.get("base") or {}).get("ref", "")},
    }
    files = data.get("files")
    if not isinstance(files, list):
        raise SourceError(Cause.FETCH_FAILED, f"{where}: 'files' must be a list")
    return parse_pull_request(slug, number, meta, files)
