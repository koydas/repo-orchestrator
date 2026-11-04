"""The Investigator's read-only tools (ADR-0004 §3): ``read``, ``search``, ``list`` over the pinned snapshots, with fixed
caps. No network, no shell, no write: the only I/O is the :class:`PinnedReader`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..collector.snapshot import Missing
from .budgets import BUDGET
from .domain import Observation
from .prompt import ToolCall, fence, scan_instruction_like
from .reader import PinnedReader


@dataclass
class ToolResult:
    ok: bool
    text: str  # what the model is shown (already fenced when it carries repository content)
    observations: list[Observation] = field(default_factory=list)


def _err(msg: str) -> ToolResult:
    return ToolResult(False, f"tool error: {msg}")


def _arg_str(args: dict[str, Any], key: str, limit: int = 300) -> str | None:
    v = args.get(key)
    return v if isinstance(v, str) and 0 < len(v) <= limit else None


def run_tool(reader: PinnedReader, call: ToolCall) -> ToolResult:
    args = dict(call.args)
    repo = _arg_str(args, "repo", 64)
    if repo is None:
        return _err("'repo' is required")
    if not reader.allowed(repo):
        return _err(f"repository {repo!r} is not readable for this investigation")
    sha = reader.sha(repo) or "?"
    if call.name == "list":
        prefix = args.get("prefix", "")
        if not isinstance(prefix, str) or len(prefix) > 300:
            return _err("'prefix' must be a string")
        names = reader.listing(repo, prefix)
        if isinstance(names, Missing):
            return _err(f"{names.cause}: {names.detail}")
        shown = names[: BUDGET.max_list_entries]
        more = f"\n... ({len(names) - len(shown)} more)" if len(names) > len(shown) else ""
        return ToolResult(True, fence("listing", "\n".join(shown) + more, repo=repo, sha=sha, prefix=prefix or "/"))
    path = _arg_str(args, "path")
    if path is None:
        return _err("'path' is required")
    got = reader.text(repo, path)
    if isinstance(got, Missing):
        return _err(f"{got.cause}: {got.detail}")
    _art, text = got
    lines = text.split("\n")
    if call.name == "read":
        start = args.get("start", 1)
        end = args.get("end", start + BUDGET.max_read_lines - 1 if isinstance(start, int) else 0)
        if not (isinstance(start, int) and isinstance(end, int)) or isinstance(start, bool) or isinstance(end, bool):
            return _err("'start' and 'end' must be integers")
        start = max(1, start)
        end = min(end, len(lines), start + BUDGET.max_read_lines - 1)
        if end < start:
            return _err(f"{path} has {len(lines)} lines")
        body = "\n".join(f"{n}\t{lines[n - 1]}" for n in range(start, end + 1))[: BUDGET.max_tool_result_chars]
        obs = scan_instruction_like("\n".join(lines[start - 1 : end]), repo, path, start)
        return ToolResult(True, fence("file", body, repo=repo, sha=sha, path=path, lines=f"{start}-{end}"), obs)
    if call.name == "search":
        query = _arg_str(args, "query", 80)
        if query is None or len(query) < 3:
            return _err("'query' must be 3 to 80 characters")
        needle = query.lower()
        hits = [n for n, line in enumerate(lines, 1) if needle in line.lower()][: BUDGET.max_search_hits]
        body = "\n".join(f"{n}\t{lines[n - 1]}" for n in hits)[: BUDGET.max_tool_result_chars] or "(no match)"
        obs = [o for n in hits for o in scan_instruction_like(lines[n - 1], repo, path, n)]
        return ToolResult(True, fence("search_hits", body, repo=repo, sha=sha, path=path, query=repr(query)), obs)
    return _err(f"unknown tool {call.name!r}")
