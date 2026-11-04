"""Helpers for the M3 tests: scripted providers, quotes taken from the pinned fixtures, a ready investigation run.
No network, no key, no LangGraph needed to import this module."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repo_orchestrator.collector.cache import CachingSource
from repo_orchestrator.engine import Engine
from repo_orchestrator.investigation.pipeline import Investigated, InvestigationOptions, run_investigation
from repo_orchestrator.investigation.provider import LLMRequest, LLMResponse, ProviderError
from repo_orchestrator.runner import ScanReport
from support import DECLARATIONS, FIXTURES, baseline_revisions, baseline_source, load_decl, scan

PROFILE_ROW = r"^\| \[`agent-patterns`\]"
AP_LINE = "have implementations in public repos"


def fixture_lines(repo_slug: str, sha: str, path: str, root: Path = FIXTURES) -> list[str]:
    p = root / repo_slug / sha / "files" / path
    return p.read_text(encoding="utf-8").split("\n")


def find_line(lines: list[str], regex: str) -> int:
    hits = [i for i, ln in enumerate(lines, 1) if re.search(regex, ln)]
    assert len(hits) == 1, (regex, hits)
    return hits[0]


def quote(
    repo: str, slug: str, path: str, regex: str, *, text: str | None = None, sha: str | None = None
) -> dict[str, Any]:
    """A model-style quote for the single line of a baseline fixture matching ``regex`` (whole line unless ``text``)."""
    decl = load_decl()
    pin = sha or baseline_revisions(decl)[repo]
    real = baseline_revisions(decl)[repo]
    lines = fixture_lines(slug, real, path)
    n = find_line(lines, regex)
    return {
        "repo": repo,
        "sha": pin,
        "path": path,
        "line_start": n,
        "line_end": n,
        "text": text if text is not None else lines[n - 1],
    }


def h2_quotes() -> list[dict[str, Any]]:
    return [
        quote("profile", "koydas/koydas", "README.md", PROFILE_ROW, text="12 extracted from the two repos above"),
        quote(
            "ap", "koydas/agent-patterns", "README.md", AP_LINE, text="16 of 20 have implementations in public repos"
        ),
    ]


def final(
    finding: str,
    claim: str,
    quotes: list[dict[str, Any]],
    values: dict[str, str] | None = None,
    intent: dict[str, Any] | None = None,
) -> str:
    return json.dumps(
        {"final": {"finding": finding, "claim": claim, "quotes": quotes, "values": values or {}, "intent": intent}}
    )


def tool(name: str, **args: Any) -> str:
    return json.dumps({"tool": name, "args": args})


def reply(text: str, in_tok: int = 600, out_tok: int = 150) -> LLMResponse:
    return LLMResponse(text, in_tok, out_tok, "claude-sonnet-5-5", "end_turn", 7)


def subject_of(req: LLMRequest) -> str:
    m = re.search(r"subject (\S+?)\.\n", req.messages[0][1])
    assert m, req.messages[0][1][:200]
    return m.group(1)


@dataclass
class Scripted:
    """A provider whose answers come from ``fn(request, n)``; ``n`` counts the calls for the same subject."""

    fn: Callable[[LLMRequest, int], str | LLMResponse | Exception]
    requests: list[LLMRequest] = field(default_factory=list)
    per_subject: dict[str, int] = field(default_factory=dict)

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        subj = subject_of(request)
        self.per_subject[subj] = self.per_subject.get(subj, 0) + 1
        out = self.fn(request, self.per_subject[subj])
        if isinstance(out, Exception):
            raise out
        return reply(out) if isinstance(out, str) else out


def h2_script(finding: str = "divergent") -> Callable[[LLMRequest, int], str | LLMResponse | Exception]:
    def fn(req: LLMRequest, n: int) -> str:
        subj = subject_of(req)
        if subj == "claim:extracted_from":
            return final(
                finding,
                "The profile says 12 patterns were extracted; agent-patterns says 16 of 20 have public implementations.",
                h2_quotes(),
            )
        return final(
            "cannot_determine",
            "Not enough evidence in the pinned files.",
            [
                quote(
                    "adt",
                    "koydas/ai-dev-tools",
                    "docs/adr/ADR-018-consumer-repo-adoption.md",
                    r"Every repository worked on with Claude Code",
                )
            ],
        )

    return fn


def baseline_run(
    tmp: Path, provider: Any = None, *, recording: Any = None, engine: Engine | None = None
) -> tuple[Investigated, ScanReport]:
    decl = load_decl()
    engine = engine or Engine(decl)
    report = scan(engine=engine, decl=decl)
    opts = InvestigationOptions(
        declarations_dir=DECLARATIONS, cache_dir=FIXTURES, state_db=tmp / "state.sqlite", checkpoint_path=tmp / "checkpoints.sqlite",
        provider=provider, recording=recording, declarations_digest=decl.digest, revisions=baseline_revisions(decl), offline=True,
    )  # fmt: skip
    inv = run_investigation(engine, report, CachingSource(None, FIXTURES), opts)
    return inv, report


__all__ = [
    "ProviderError",
    "Scripted",
    "baseline_run",
    "baseline_source",
    "final",
    "h2_quotes",
    "h2_script",
    "quote",
    "reply",
    "tool",
]
