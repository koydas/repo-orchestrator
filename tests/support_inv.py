"""Helpers for the M3 tests: scripted providers, quotes taken from the pinned fixtures, a ready investigation run.
No network, no key, no LangGraph needed to import this module."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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


def intent_answer(proposal: str = "intentional") -> str:
    return final(
        "divergent",
        "The repository documents that it is not in the plugin scope.",
        [
            quote(
                "adt",
                "koydas/ai-dev-tools",
                "docs/adr/ADR-018-consumer-repo-adoption.md",
                r"Every repository worked on with Claude Code",
            )
        ],
        intent={
            "proposal": proposal,
            "justification": "ADR-018 scopes the plugin to repositories worked on with Claude Code",
            "quote": 0,
        },
    )


def scripted_baseline() -> Scripted:
    """The scripted Investigator behind the committed recording: H2 divergent (a human decides), H1 with a proposed intent."""
    return Scripted(
        by_subject(
            {
                "claim:extracted_from": final(
                    "divergent",
                    "The profile says 12 patterns were extracted; agent-patterns says 16 of 20 have public implementations.",
                    h2_quotes(),
                )
            },
            default=intent_answer(),
        )
    )


def baseline_run(
    tmp: Path,
    provider: Any = None,
    *,
    recording: Any = None,
    engine: Engine | None = None,
    mutations: Any = (),
    budget: Any = None,
    decl: Any = None,
    declarations_dir: Path | None = None,
    as_of: Any = None,
) -> tuple[Investigated, ScanReport]:
    decl = decl or load_decl()
    engine = engine or Engine(decl)
    source = baseline_source(mutations)
    report = scan(engine=engine, decl=decl, source=source, **({"as_of": as_of} if as_of else {}))
    opts = InvestigationOptions(
        declarations_dir=declarations_dir or DECLARATIONS,
        cache_dir=FIXTURES,
        state_db=tmp / "state.sqlite",
        checkpoint_path=tmp / "checkpoints.sqlite",
        provider=provider,
        recording=recording,
        declarations_digest=decl.digest,
        revisions=baseline_revisions(decl),
        offline=True,
    )
    if budget is not None:
        opts.budget = budget
    inv = run_investigation(engine, report, source, opts)
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


T1_YAML = """
  - id: T1
    baseline: R2
    relationship: R2
    subject: "claim:schema_versions_agree"
    impact: breaks
    question: "Does the version the consumer supports differ from the version the producer emits? Name them consumer_version and producer_version."
    claimant: {repo: adt, path: scripts/loop-insights.mjs, line_regex: '^export const SUPPORTED_SCHEMA_VERSION', phrase_regex: 'SUPPORTED_SCHEMA_VERSION = \\d+'}
    upstream:
      - {repo: adl, path: scripts/lib/findings_export.mjs, line_regex: '^export const FINDINGS_SCHEMA_VERSION'}
"""

T1_ENTAILMENT = (
    "    entailment:\n"
    "      kind: numeric_relation\n"
    "      op: ne\n"
    "      left: {name: consumer_version, source: claimant, regex: 'SUPPORTED_SCHEMA_VERSION = (\\d+)'}\n"
    "      right: {name: producer_version, source: upstream, index: 0, regex: 'FINDINGS_SCHEMA_VERSION = (\\d+)'}\n"
)


def custom_declarations(tmp: Path, *, entailment: bool = True, extra_claims: str = "") -> Path:
    """A copy of ``declarations/`` whose investigation.yaml also declares a ``breaks`` claim (T1) on the R2 runtime contract."""
    import shutil

    dest = tmp / "declarations"
    shutil.copytree(DECLARATIONS, dest)
    text = (dest / "investigation.yaml").read_text()
    block = T1_YAML.rstrip("\n") + "\n" + (T1_ENTAILMENT if entailment else "") + extra_claims
    text = text.replace("intent_reviews:", block + "intent_reviews:", 1)
    (dest / "investigation.yaml").write_text(text)
    return dest


def t1_quotes(consumer: str = "1", producer: str = "1") -> list[dict[str, Any]]:
    return [
        quote(
            "adt",
            "koydas/ai-dev-tools",
            "scripts/loop-insights.mjs",
            r"^export const SUPPORTED_SCHEMA_VERSION",
            text=f"export const SUPPORTED_SCHEMA_VERSION = {consumer};",
        ),
        quote(
            "adl",
            "koydas/autonomous-dev-loop",
            "scripts/lib/findings_export.mjs",
            r"^export const FINDINGS_SCHEMA_VERSION",
            text=f"export const FINDINGS_SCHEMA_VERSION = {producer};",
        ),
    ]


def by_subject(mapping: dict[str, Any], default: Any = None) -> Callable[[LLMRequest, int], Any]:
    """A provider function: ``mapping[subject]`` is a string, an exception, or a callable ``(n) -> str | exception``."""

    def fn(req: LLMRequest, n: int) -> Any:
        entry = mapping.get(subject_of(req), default)
        return entry(n) if callable(entry) else entry

    return fn
