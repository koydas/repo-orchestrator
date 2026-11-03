"""Small explicit GitHub REST client: GET only, bounded retries, bounded request budget, no SDK.

Endpoints used (all read-only; ``contents: read`` on public repositories is enough, a token only raises the rate limit):
``GET /repos/{slug}``, ``GET /repos/{slug}/commits/{ref}`` (SHA media type),
``GET /repos/{slug}/git/trees/{sha}?recursive=1`` and ``GET /repos/{slug}/git/blobs/{blob_sha}``.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from ..models import Cause
from .source import SHA_RE, SourceError, Tree, TreeEntry, git_blob_sha

API = "https://api.github.com"


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes


class TransportError(Exception):
    """Network-level failure (DNS, TLS, reset, timeout)."""


Transport = Callable[[str, Mapping[str, str], float], Response]


class _ApiOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only within api.github.com over https, so a bearer token can never be sent to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        target = urlparse(newurl)
        if target.scheme != "https" or target.hostname != "api.github.com":
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_ApiOnlyRedirects)


def urllib_transport(url: str, headers: Mapping[str, str], timeout: float) -> Response:
    if not url.startswith("https://"):
        raise TransportError("only https URLs are allowed")
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with _OPENER.open(request, timeout=timeout) as resp:
            return Response(resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TransportError(type(exc).__name__) from exc


class GitHubSource:
    def __init__(
        self,
        token: str | None = None,
        *,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 4,
        backoff_seconds: float = 0.5,
        max_retry_after: float = 30.0,
        max_requests: int = 400,
        timeout: float = 20.0,
    ) -> None:
        self._token = token
        self._transport = transport
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._max_retry_after = max_retry_after
        self._max_requests = max_requests
        self._timeout = timeout
        self.requests = 0

    # -- plumbing ------------------------------------------------------------------------------------
    def _headers(self, accept: str) -> dict[str, str]:
        headers = {
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "repo-orchestrator-m1 (read-only)",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _delay(self, attempt: int, response: Response | None) -> float:
        if response is not None and (retry_after := response.headers.get("retry-after")):
            try:
                return float(str(retry_after))
            except ValueError:
                pass
        return float(self._backoff * (2 ** (attempt - 1)))

    def _get(self, path: str, *, accept: str = "application/vnd.github+json", not_found: str) -> Response:
        url = f"{API}{path}"
        last = "no attempt made"
        rate_limited = False
        for attempt in range(1, self._max_attempts + 1):
            if self.requests >= self._max_requests:
                raise SourceError(Cause.BUDGET_EXHAUSTED, f"request budget of {self._max_requests} reached")
            self.requests += 1
            response: Response | None = None
            try:
                response = self._transport(url, self._headers(accept), self._timeout)
            except TransportError as exc:
                last = f"network error ({exc})"
                rate_limited = False
            else:
                status = response.status
                if status == 200:
                    return response
                if status == 404:
                    raise SourceError(not_found, f"404 for {path}")
                if status == 401:
                    raise SourceError(Cause.PERMISSION_DENIED, f"401 for {path}")
                limited = status == 429 or (
                    status == 403
                    and (response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers)
                )
                if status == 403 and not limited:
                    raise SourceError(Cause.PERMISSION_DENIED, f"403 for {path}")
                if limited or status >= 500:
                    rate_limited = limited
                    last = f"HTTP {status}"
                else:
                    raise SourceError(Cause.FETCH_FAILED, f"HTTP {status} for {path}")
            if attempt < self._max_attempts:
                delay = self._delay(attempt, response)
                if delay > self._max_retry_after:
                    raise SourceError(
                        Cause.RATE_LIMITED, f"wait of {delay:.0f}s exceeds the {self._max_retry_after:.0f}s cap"
                    )
                self._sleep(delay)
        kind = Cause.RATE_LIMITED if rate_limited else Cause.FETCH_FAILED
        raise SourceError(kind, f"gave up on {path} after {self._max_attempts} attempts ({last})")

    @staticmethod
    def _json(response: Response, path: str) -> Any:
        try:
            return json.loads(response.body)
        except ValueError as exc:
            raise SourceError(Cause.FETCH_FAILED, f"malformed JSON from {path}") from exc

    # -- SnapshotSource --------------------------------------------------------------------------------
    def resolve(self, slug: str, ref: str | None) -> str:
        info = self._json(self._get(f"/repos/{slug}", not_found=Cause.REPOSITORY_NOT_FOUND), slug)
        if not isinstance(info, dict) or "default_branch" not in info:
            raise SourceError(Cause.FETCH_FAILED, f"unexpected repository payload for {slug}")
        if info.get("private") is True:
            raise SourceError(
                Cause.PRIVATE_REPOSITORY, f"{slug} is private; private repositories are out of scope (Q6)"
            )
        target = ref or str(info["default_branch"])
        response = self._get(
            f"/repos/{slug}/commits/{target}",
            accept="application/vnd.github.sha",
            not_found=Cause.REVISION_UNRESOLVED,
        )
        sha = response.body.decode("ascii", "replace").strip().lower()
        if not SHA_RE.match(sha):
            raise SourceError(Cause.FETCH_FAILED, f"unexpected commit SHA payload for {slug}@{target}")
        if SHA_RE.match(target) and sha != target:
            raise SourceError(Cause.REVISION_UNRESOLVED, f"{slug}: {target} resolved to {sha}")
        return sha

    def tree(self, slug: str, sha: str) -> Tree:
        path = f"/repos/{slug}/git/trees/{sha}?recursive=1"
        data = self._json(self._get(path, not_found=Cause.REVISION_UNRESOLVED), path)
        if not isinstance(data, dict) or not isinstance(data.get("tree"), list):
            raise SourceError(Cause.FETCH_FAILED, f"unexpected tree payload for {slug}@{sha}")
        entries = []
        for item in data["tree"]:
            if item.get("type") in ("blob", "tree"):
                size = item.get("size")
                entries.append(
                    TreeEntry(
                        str(item["path"]), str(item["type"]), size if isinstance(size, int) else None, str(item["sha"])
                    )
                )
        return Tree(sha, tuple(sorted(entries, key=lambda e: e.path)), bool(data.get("truncated")))

    def read(self, slug: str, sha: str, entry: TreeEntry) -> bytes:
        path = f"/repos/{slug}/git/blobs/{entry.blob_sha}"
        data = self._json(self._get(path, not_found=Cause.NOT_FOUND), path)
        if not isinstance(data, dict) or data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
            raise SourceError(Cause.FETCH_FAILED, f"unexpected blob payload for {entry.path}")
        try:
            content = base64.b64decode(data["content"], validate=False)
        except ValueError as exc:
            raise SourceError(Cause.FETCH_FAILED, f"undecodable blob for {entry.path}") from exc
        if git_blob_sha(content) != entry.blob_sha:
            raise SourceError(Cause.INTEGRITY, f"{slug}@{sha}:{entry.path} does not match its blob id")
        return content
