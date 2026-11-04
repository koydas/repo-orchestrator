"""The LLM boundary (ADR-0015): the only module of the package that talks to a model provider, and the only one that
reads the API key.

Three implementations of one method, ``complete(request) -> response``: the live Anthropic Messages API (standard
library HTTP, no SDK), a replay of recorded responses (no network, no key, fails closed on a miss) and a wrapper that
records. The key lives in one attribute, goes into one header and is never printed (``__repr__`` masks it).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .budgets import API_VERSION, EFFORT

DEFAULT_BASE_URL = "https://api.anthropic.com"
RECORDING_FORMAT = 1


@dataclass(frozen=True)
class LLMRequest:
    model: str
    system: str
    messages: tuple[tuple[str, str], ...]  # (role, content), alternating, first is "user"
    max_tokens: int
    effort: str = EFFORT

    def canonical(self) -> dict[str, Any]:
        return {
            "v": 1, "provider": "anthropic-messages", "model": self.model, "max_tokens": self.max_tokens,
            "effort": self.effort, "system": self.system, "messages": [list(m) for m in self.messages],
        }  # fmt: skip

    @property
    def key(self) -> str:
        blob = json.dumps(self.canonical(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    @property
    def chars(self) -> int:
        return len(self.system) + sum(len(c) for _, c in self.messages)


@dataclass(frozen=True)
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    stop_reason: str
    latency_ms: int = 0


class ProviderError(Exception):
    """``kind``: timeout | rate_limited | server | network | auth | invalid | refusal | replay_miss | no_credentials.
    ``transient`` kinds are retried once by the Investigator."""

    TRANSIENT = frozenset({"timeout", "rate_limited", "server", "network"})

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind

    @property
    def transient(self) -> bool:
        return self.kind in self.TRANSIENT


class Provider(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...


class AnthropicProvider:
    """POST {base}/v1/messages. No retries here (the budget counts every call), no tool-use blocks, no sampling params."""

    def __init__(self, api_key: str, base_url: str | None = None, timeout: float = 90.0) -> None:
        if not api_key:
            raise ProviderError("no_credentials", "ANTHROPIC_API_KEY is not set")
        self._key = api_key
        self._base = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._timeout = timeout

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AnthropicProvider:
        env = os.environ if environ is None else environ
        return cls(env.get("ANTHROPIC_API_KEY", ""), env.get("ANTHROPIC_BASE_URL") or None)

    def __repr__(self) -> str:
        return f"AnthropicProvider(base_url={self._base!r}, api_key=<masked>)"

    def complete(self, request: LLMRequest) -> LLMResponse:
        body = {
            "model": request.model, "max_tokens": request.max_tokens, "system": request.system,
            "messages": [{"role": r, "content": c} for r, c in request.messages],
            "output_config": {"effort": request.effort},
        }  # fmt: skip
        req = urllib.request.Request(
            f"{self._base}/v1/messages", data=json.dumps(body).encode(), method="POST",
            headers={"x-api-key": self._key, "anthropic-version": API_VERSION, "content-type": "application/json"},
        )  # fmt: skip
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            code = exc.code
            kind = (
                "auth"
                if code in (401, 403)
                else "rate_limited"
                if code in (408, 429)
                else "server"
                if code >= 500
                else "invalid"
            )
            raise ProviderError(kind, f"HTTP {code}") from None
        except TimeoutError:
            raise ProviderError("timeout", "request timed out") from None
        except (urllib.error.URLError, OSError):
            raise ProviderError("network", "connection failed") from None
        except ValueError:
            raise ProviderError("invalid", "response is not JSON") from None
        return _parse_response(payload, int((time.monotonic() - t0) * 1000))


def _parse_response(payload: Any, latency_ms: int) -> LLMResponse:
    if not isinstance(payload, dict):
        raise ProviderError("invalid", "unexpected response shape")
    stop = str(payload.get("stop_reason") or "")
    if stop == "refusal":
        raise ProviderError("refusal", "the model declined the request")
    blocks = payload.get("content")
    raw_usage = payload.get("usage")
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    if not isinstance(blocks, list):
        raise ProviderError("invalid", "no content")
    text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
    return LLMResponse(
        text, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0), str(payload.get("model") or ""), stop, latency_ms
    )  # fmt: skip


class Recording:
    """request key → recorded response. The file format of ``--record`` / ``--replay`` and of the store table."""

    def __init__(self, entries: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self.entries: dict[str, dict[str, Any]] = {k: dict(v) for k, v in (entries or {}).items()}

    def add(self, request: LLMRequest, response: LLMResponse) -> None:
        self.entries[request.key] = {
            "text": response.text, "input_tokens": response.input_tokens, "output_tokens": response.output_tokens,
            "model": response.model, "stop_reason": response.stop_reason, "latency_ms": response.latency_ms,
            "request": {"model": request.model, "max_tokens": request.max_tokens, "effort": request.effort,
                        "system_sha256": hashlib.sha256(request.system.encode()).hexdigest(), "messages": len(request.messages)},
        }  # fmt: skip

    def get(self, key: str) -> LLMResponse | None:
        e = self.entries.get(key)
        if e is None:
            return None
        return LLMResponse(
            e["text"], e["input_tokens"], e["output_tokens"], e["model"], e["stop_reason"], e.get("latency_ms", 0)
        )

    def dumps(self) -> str:
        return (
            json.dumps(
                {"format": RECORDING_FORMAT, "entries": self.entries}, indent=1, sort_keys=True, ensure_ascii=False
            )
            + "\n"
        )

    @classmethod
    def loads(cls, raw: str) -> Recording:
        try:
            data = json.loads(raw)
            entries = data["entries"]
            if data.get("format") != RECORDING_FORMAT or not isinstance(entries, dict):
                raise ValueError("unsupported recording format")
            for e in entries.values():
                LLMResponse(e["text"], e["input_tokens"], e["output_tokens"], e["model"], e["stop_reason"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("invalid", f"unreadable recording: {exc}") from None
        return cls(entries)


def response_digest(request_key: str, response: LLMResponse) -> str:
    """What the semantic report commits to: the request and the response text, not usage or latency."""
    return hashlib.sha256(f"{request_key}\0{response.text}\0{response.model}".encode()).hexdigest()


class ReplayProvider:
    """Answers from a recording; a request it never saw is ``replay_miss`` (no network, no key, fails closed)."""

    def __init__(self, recording: Recording) -> None:
        self._rec = recording

    def complete(self, request: LLMRequest) -> LLMResponse:
        found = self._rec.get(request.key)
        if found is None:
            raise ProviderError("replay_miss", f"no recorded response for request {request.key[:16]}")
        return found


class NoProvider:
    """Used when a stored run is resumed without credentials: every call that is not already recorded fails closed."""

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise ProviderError("no_credentials", "no live provider is configured and the call is not recorded")
