"""Evidence records and their verification by code (ADR-0006): authentic evidence is a property of the record,
whether the claim it supports is correct is the comparator's job."""

from __future__ import annotations

import hashlib

from .collector.snapshot import Artifact, Snapshot
from .extract import Extracted, ExtractionError, decode
from .models import Evidence

MAX_LISTED = 40
MORE_MARKER = "... ("


def text_evidence(side: str, art: Artifact, ex: Extracted, locator: str) -> Evidence:
    return Evidence(
        side,
        art.repo,
        art.slug,
        art.sha,
        art.path,
        locator,
        "text",
        ex.quote,
        art.content_sha256,
        art.blob_sha,
        ex.line_start,
        ex.line_end,
        ex.extractor,
    )


def listing_evidence(
    side: str, repo: str, slug: str, sha: str, directory: str, names: list[str], locator: str, extractor: str
) -> Evidence:
    """Evidence for a tree listing: the (sorted) entry names under ``directory``, capped for readability."""
    shown = names[:MAX_LISTED]
    quote = "\n".join(shown) + (f"\n{MORE_MARKER}{len(names) - MAX_LISTED} more)" if len(names) > MAX_LISTED else "")
    content = hashlib.sha256("\n".join(sorted(names)).encode("utf-8")).hexdigest()
    return Evidence(
        side, repo, slug, sha, directory, locator, "tree_listing", quote, content, None, None, None, extractor
    )


def verify(ev: Evidence, snapshot: Snapshot) -> bool:
    """True when the record is authentic at its pinned revision: the quote exists where it says it does."""
    if not snapshot.has(ev.repo):
        return False
    if snapshot.revision(ev.repo) != ev.sha:
        return False
    if ev.kind == "text":
        art = snapshot.artifact(ev.repo, ev.path)
        if not isinstance(art, Artifact) or art.content_sha256 != ev.content_sha256 or art.blob_sha != ev.blob_sha:
            return False
        try:
            return ev.quote in decode(art.data)
        except ExtractionError:
            return False
    if ev.kind == "tree_listing":
        entries = snapshot.entries(ev.repo, ev.path)
        if not isinstance(entries, list):
            return False
        prefix = ev.path.strip("/")
        prefix = prefix + "/" if prefix else ""
        relative = {e.path[len(prefix) :] for e in entries}
        known = relative | {r.split("/")[0] for r in relative}
        lines = [ln for ln in ev.quote.splitlines() if ln and not ln.startswith(MORE_MARKER)]
        return all(ln in known for ln in lines)
    return False
    return False
