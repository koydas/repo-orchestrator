"""Command line: ``repo-orchestrator scan``, ``validate`` and ``dashboard``.

Exit codes: 0 scan complete, no findings · 1 scan complete, findings detected · 2 evaluation incomplete (evidence could
not be collected or decided; takes precedence over 1) · 3 configuration or runtime failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from . import __version__
from .collector.cache import CachingSource
from .collector.github import GitHubSource
from .collector.snapshot import Limits
from .collector.source import SnapshotSource
from .dashboard import build_dashboard
from .declarations import load_declarations
from .engine import Engine, RunOptions
from .models import ConfigError
from .report import render_markdown
from .runner import DeterminismError, ScanReport, run_scan
from .store import RunStore

OK, FINDINGS, INCOMPLETE, FAILURE = 0, 1, 2, 3
STATE_DIR = Path(".repo-orchestrator")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="repo-orchestrator", description="Deterministic, read-only cross-repository coherence scan (M1)."
    )
    p.add_argument("--version", action="version", version=f"repo-orchestrator {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="load and validate the declarations; collect nothing")
    v.add_argument("--declarations", type=Path, default=Path("declarations"))
    s = sub.add_parser("scan", help="run the baseline coherence scan (W1)")
    s.add_argument("--declarations", type=Path, default=Path("declarations"), help="directory of declaration files")
    s.add_argument("--repos", help="comma-separated repository ids to analyze (default: every analyzed repository)")
    s.add_argument(
        "--revisions",
        default="head",
        help="'head' (default-branch HEAD) or a named pin set from pins.yaml, e.g. 'baseline'",
    )
    s.add_argument(
        "--pin", action="append", default=[], metavar="ID=SHA", help="pin one repository to a commit (repeatable)"
    )
    s.add_argument("--output-dir", type=Path, default=Path("reports"))
    s.add_argument("--format", default="md,json", help="comma-separated: md, json")
    s.add_argument(
        "--cache-dir",
        type=Path,
        default=STATE_DIR / "cache",
        help="immutable artifact cache (also the offline fixture format)",
    )
    s.add_argument("--offline", action="store_true", help="read only from the cache; requires pinned revisions")
    s.add_argument("--state-db", type=Path, default=STATE_DIR / "state.sqlite")
    s.add_argument("--no-store", action="store_true", help="do not record the run in SQLite")
    s.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="date used for exception expiry (default: today, UTC)",
    )
    s.add_argument("--max-requests", type=int, default=400, help="GitHub API request budget for the run")
    s.add_argument("--quiet", action="store_true")
    d = sub.add_parser("dashboard", help="render report.json files as a static, read-only HTML dashboard")
    d.add_argument(
        "--reports", type=Path, default=Path("reports"), help="directory searched recursively for report.json"
    )
    d.add_argument("--out", type=Path, default=Path("_site"), help="output directory (build into a fresh one)")
    d.add_argument("--quiet", action="store_true")
    return p


def _revisions(decl_pins: Any, name: str, pins: Sequence[str], known: set[str]) -> dict[str, str | None]:
    revisions: dict[str, str | None] = {}
    if name != "head":
        if name not in decl_pins:
            raise ConfigError(f"unknown pin set {name!r} (declared: {sorted(decl_pins)}, or 'head')")
        revisions.update(decl_pins[name].revisions)
    for item in pins:
        repo_id, sep, sha = item.partition("=")
        if not sep or repo_id not in known or not sha:
            raise ConfigError(f"--pin expects ID=SHA with a declared repository id, got {item!r}")
        revisions[repo_id] = sha.lower()
    return revisions


def _summary(report: ScanReport, paths: list[Path]) -> str:
    s = report.semantic
    by = s["summary"]["by_status"]
    lines = [
        f"repo-orchestrator {s['tool']['version']} · scope: {', '.join(s['inputs']['scope'])} · run {s['run_status']} · digest {s['result_digest'][:12]}",
        "revisions: " + ", ".join(f"{r['repo']}@{(r['sha'] or 'unresolved')[:8]}" for r in s["inputs"]["revisions"]),
        "outcomes: " + ", ".join(f"{k} {v}" for k, v in by.items() if v),
    ]
    for f in s["findings"]:
        fix = f" → fix at {f['fix_sites'][0]['repo']}:{f['fix_sites'][0]['path']}" if f["fix_sites"] else ""
        lines.append(
            f"  FINDING [{f['severity']}] {f['policy_id']} {f['relationship_id']} {f['subject']}: {f['reason']}{fix}"
        )
    for key, label in (
        ("insufficient_evidence", "CANNOT EVALUATE"),
        ("collection_failures", "COLLECTION FAILURE"),
        ("evaluation_failures", "EVALUATION FAILURE"),
    ):
        for o in s[key]:
            lines.append(
                f"  {label} {o['policy_id']} {o['relationship_id']} {o['subject']} [{o['cause']}]: {o['reason'][:140]}"
            )
    for o in s["policy_clarifications"]:
        lines.append(
            f"  QUESTION {o['policy_id']} {o['relationship_id']} {o['subject']} ({', '.join(o['open_questions'])}): {o['reason'][:140]}"
        )
    for o in s["policy_conflicts"]:
        lines.append(f"  POLICY CONFLICT {o['policy_id']} {o['relationship_id']} {o['subject']}")
    lines += [f"report: {p}" for p in paths]
    lines.append(
        f"exit code {report.exit_code} ({['complete, no findings', 'complete, findings detected', 'incomplete evaluation', 'failure'][report.exit_code]})"
    )
    return "\n".join(lines)


def _write_reports(report: ScanReport, out_dir: Path, formats: set[str]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    if "md" in formats:
        paths.append(out_dir / "report.md")
        paths[-1].write_text(render_markdown(report.semantic), encoding="utf-8")
    if "json" in formats:
        paths.append(out_dir / "report.json")
        body = {"semantic": report.semantic, "execution": report.execution}
        paths[-1].write_text(json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return paths


def _scan(args: argparse.Namespace) -> int:
    formats = {f.strip() for f in args.format.split(",") if f.strip()}
    if not formats or formats - {"md", "json"}:
        raise ConfigError("--format accepts md and json")
    decl = load_declarations(args.declarations)
    engine = Engine(decl)
    scope = tuple(r.strip() for r in args.repos.split(",")) if args.repos else None
    revisions = _revisions(decl.pins, args.revisions, args.pin, set(engine.graph.repositories))
    inner: SnapshotSource | None = None
    github: GitHubSource | None = None
    if not args.offline:
        github = GitHubSource(
            os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"), max_requests=args.max_requests
        )
        inner = github
    source = CachingSource(inner, args.cache_dir)
    options = RunOptions(revisions=revisions, scope=scope, as_of=args.as_of or date.today(), limits=Limits())
    store = None if args.no_store else RunStore(args.state_db)
    try:
        report = run_scan(
            engine, source, options, store=store,
            stats=lambda: {"api_requests": github.requests if github else 0, "cache_hits": source.hits, "cache_misses": source.misses},
        )  # fmt: skip
    finally:
        if store:
            store.close()
    paths = _write_reports(report, args.output_dir, formats)
    if not args.quiet:
        print(_summary(report, paths))
    return report.exit_code


def _dashboard(args: argparse.Namespace) -> int:
    result = build_dashboard(args.reports, args.out)
    for skipped in result.skipped:
        print(f"warning: skipped {skipped.file}: {skipped.reason}", file=sys.stderr)
    if not args.quiet:
        print(f"dashboard: {result.reports} report(s), {len(result.skipped)} skipped → {args.out}")
    return OK


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            decl = load_declarations(args.declarations)
            engine = Engine(decl)
            trusted = engine.graph.trusted()
            print(
                f"declarations OK: {len(engine.graph.repositories)} repositories, {len(trusted)} relationships, {len(engine.policies)} policies"
            )
            return OK
        if args.command == "dashboard":
            return _dashboard(args)
        return _scan(args)
    except (ConfigError, DeterminismError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return FAILURE
    except OSError as exc:
        print(f"error: {exc.strerror or exc}", file=sys.stderr)
        return FAILURE


if __name__ == "__main__":
    sys.exit(main())
