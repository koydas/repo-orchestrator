"""The LLM boundary against a local fake of the Messages API (real HTTP on 127.0.0.1; no internet, no real key)."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from repo_orchestrator.investigation.provider import (
    AnthropicProvider,
    LLMRequest,
    LLMResponse,
    NoProvider,
    ProviderError,
    Recording,
    ReplayProvider,
    response_digest,
)

KEY = "sk-ant-api03-TESTSECRET-do-not-leak"
REQ = LLMRequest("claude-sonnet-5-5", "system text", (("user", "hello"),), 2000)


class Fake:
    """A one-route Messages API fake. ``plan`` is a list of callables ``(handler) -> None`` consumed in order."""

    def __init__(self):
        self.seen: list[dict] = []
        self.plan: list = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.seen.append(
                    {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body}
                )
                (outer.plan.pop(0) if outer.plan else ok())(self)

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def ok(text="{}", stop="end_turn", usage=(11, 5)):
    def send(h):
        payload = {"id": "msg_1", "type": "message", "model": "claude-sonnet-5-5", "stop_reason": stop,
                   "content": [{"type": "text", "text": text}], "usage": {"input_tokens": usage[0], "output_tokens": usage[1]}}  # fmt: skip
        raw = json.dumps(payload).encode()
        h.send_response(200)
        h.send_header("Content-Length", str(len(raw)))
        h.end_headers()
        h.wfile.write(raw)

    return send


def status(code):
    def send(h):
        h.send_response(code)
        h.send_header("Content-Length", "0")
        h.end_headers()

    return send


@pytest.fixture
def fake(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    f = Fake()
    yield f
    f.close()


def test_the_request_is_the_documented_messages_call_without_sampling_parameters_tools_or_thinking(fake):
    fake.plan = [ok("hi", usage=(123, 45))]
    got = AnthropicProvider(KEY, fake.url).complete(REQ)
    seen = fake.seen[0]
    assert seen["path"] == "/v1/messages"
    assert seen["headers"]["x-api-key"] == KEY and seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["headers"]["content-type"] == "application/json"
    assert set(seen["body"]) == {"model", "max_tokens", "system", "messages", "output_config"}
    assert seen["body"]["output_config"] == {"effort": "medium"} and seen["body"]["messages"] == [
        {"role": "user", "content": "hello"}
    ]
    assert (got.text, got.input_tokens, got.output_tokens, got.stop_reason) == ("hi", 123, 45, "end_turn")


@pytest.mark.parametrize(
    ("handler", "kind", "transient"),
    [
        (status(429), "rate_limited", True),
        (status(408), "rate_limited", True),
        (status(500), "server", True),
        (status(529), "server", True),
        (status(401), "auth", False),
        (status(403), "auth", False),
        (status(400), "invalid", False),
        (ok(stop="refusal"), "refusal", False),
    ],
)
def test_http_errors_map_to_stable_kinds_and_only_transient_ones_are_retried_by_the_investigator(
    fake, handler, kind, transient
):
    fake.plan = [handler]
    with pytest.raises(ProviderError) as exc:
        AnthropicProvider(KEY, fake.url).complete(REQ)
    assert exc.value.kind == kind and exc.value.transient is transient
    assert KEY not in str(exc.value)


def test_timeout_garbage_and_connection_failures_are_provider_errors_not_crashes(fake):
    def slow(h):
        time.sleep(1.0)
        ok()(h)

    fake.plan = [slow]
    with pytest.raises(ProviderError) as exc:
        AnthropicProvider(KEY, fake.url, timeout=0.2).complete(REQ)
    assert exc.value.kind == "timeout"

    def garbage(h):
        h.send_response(200)
        h.send_header("Content-Length", "8")
        h.end_headers()
        h.wfile.write(b"not json")

    fake.plan = [garbage]
    with pytest.raises(ProviderError) as exc:
        AnthropicProvider(KEY, fake.url).complete(REQ)
    assert exc.value.kind == "invalid"
    with pytest.raises(ProviderError) as exc:
        AnthropicProvider(KEY, "http://127.0.0.1:1").complete(REQ)
    assert exc.value.kind == "network"


def test_the_key_is_masked_everywhere_it_could_be_printed():
    p = AnthropicProvider(KEY)
    assert KEY not in repr(p) and KEY not in str(p) and "<masked>" in repr(p)
    with pytest.raises(ProviderError) as exc:
        AnthropicProvider("")
    assert exc.value.kind == "no_credentials"
    assert (
        AnthropicProvider.from_env({"ANTHROPIC_API_KEY": KEY, "ANTHROPIC_BASE_URL": "http://x.test/"})._base
        == "http://x.test"
    )
    with pytest.raises(ProviderError):
        AnthropicProvider.from_env({})


def test_request_keys_are_content_addressed_and_sensitive_to_every_part():
    base = REQ.key
    assert base == LLMRequest("claude-sonnet-5-5", "system text", (("user", "hello"),), 2000).key
    for other in (
        LLMRequest("claude-opus-5-5", "system text", (("user", "hello"),), 2000),
        LLMRequest("claude-sonnet-5-5", "system text!", (("user", "hello"),), 2000),
        LLMRequest("claude-sonnet-5-5", "system text", (("user", "hello!"),), 2000),
        LLMRequest("claude-sonnet-5-5", "system text", (("user", "hello"),), 1999),
        LLMRequest("claude-sonnet-5-5", "system text", (("user", "hello"),), 2000, "high"),
    ):
        assert other.key != base


def test_recording_round_trip_replay_miss_and_no_provider_fail_closed():
    rec = Recording()
    rec.add(REQ, LLMResponse("answer", 10, 4, "claude-sonnet-5-5", "end_turn", 99))
    again = Recording.loads(rec.dumps())
    assert ReplayProvider(again).complete(REQ).text == "answer"
    with pytest.raises(ProviderError) as exc:
        ReplayProvider(again).complete(LLMRequest("claude-sonnet-5-5", "other", (("user", "x"),), 2000))
    assert exc.value.kind == "replay_miss"
    with pytest.raises(ProviderError) as exc:
        NoProvider().complete(REQ)
    assert exc.value.kind == "no_credentials"
    for bad in (
        "nope",
        "{}",
        json.dumps({"format": 2, "entries": {}}),
        json.dumps({"format": 1, "entries": {"k": {"text": "t"}}}),
    ):
        with pytest.raises(ProviderError):
            Recording.loads(bad)
    assert response_digest("k", LLMResponse("a", 1, 1, "m", "end_turn")) == response_digest(
        "k", LLMResponse("a", 9, 9, "m", "end_turn", 5)
    )
    assert response_digest("k", LLMResponse("a", 1, 1, "m", "end_turn")) != response_digest(
        "k", LLMResponse("b", 1, 1, "m", "end_turn")
    )
    assert "claude" not in json.dumps({k: v["request"].get("system_sha256") for k, v in rec.entries.items()})
