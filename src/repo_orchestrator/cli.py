"""Command line: ``repo-orchestrator scan`` (W1), ``analyze-pr`` (W2), ``validate`` and ``dashboard``.

Exit codes, shared by both analyses: 0 complete, nothing to report (``scan``: no finding; ``analyze-pr``: no *introduced*
finding, pre-existing ones never fail) · 1 complete, findings (``analyze-pr``: introduced findings) · 2 evaluation
incomplete (evidence could not be collected or decided; takes precedence over 1) · 3 configuration or runtime failure.
``scan --investigate`` (M3) adds one code: 4 complete, but a human decision is pending (``needs_validation``); precedence
3 > 2 > 1 > 4 > 0, and ``insufficient_evidence`` is exit 2, never 0. Without ``--investigate`` the codes are exactly M1's.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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
from .pr_report import render_pr_markdown
from .pr_runner import PRAnalysis, analyze_pull_request, declared_repo_id
from .report import render_markdown
from .runner import DeterminismError, ScanReport, run_scan
from .store import RunStore

OK, FINDINGS, INCOMPLETE, FAILURE = 0, 1, 2, 3
STATE_DIR = Path(".repo-orchestrator")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="repo-orchestrator",
        description="Deterministic, read-only cross-repository coherence: baseline scan (M1) and PR impact (M2).",
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
    s.add_argument(
        "--investigate",
        action="store_true",
        help="M3: also investigate the declared reasoning candidates (P7, intent reviews) with the bounded Investigator; "
        "needs ANTHROPIC_API_KEY or --replay. Without it the scan is exactly M1",
    )
    s.add_argument(
        "--replay",
        type=Path,
        default=None,
        metavar="FILE",
        help="with --investigate: answer from this recording, offline (no key)",
    )
    s.add_argument(
        "--record",
        type=Path,
        default=None,
        metavar="FILE",
        help="with --investigate: write the model responses used to this file",
    )
    d = sub.add_parser("dashboard", help="render report.json files as a static, read-only HTML dashboard")
    d.add_argument(
        "--reports", type=Path, default=Path("reports"), help="directory searched recursively for report.json"
    )
    d.add_argument("--out", type=Path, default=Path("_site"), help="output directory (build into a fresh one)")
    d.add_argument("--quiet", action="store_true")
    iv = sub.add_parser(
        "investigations", help="M3: list, show, resume, decide on and approve stored investigation runs"
    )
    iv.add_argument("--state-db", type=Path, default=STATE_DIR / "state.sqlite")
    ivs = iv.add_subparsers(dest="inv_command", required=True)
    ivs.add_parser("list", help="stored investigation runs")
    sh = ivs.add_parser("show", help="the candidates of one run and their states")
    sh.add_argument("run")
    sh.add_argument("--json", action="store_true")
    rs = ivs.add_parser(
        "resume", help="continue an interrupted run from its checkpoint (offline, nothing is re-pinned)"
    )
    rs.add_argument("run")
    de = ivs.add_parser("decide", help="W4: record a human decision on one candidate")
    de.add_argument("run")
    de.add_argument("candidate")
    de.add_argument("--action", required=True, choices=["validate", "reject", "dismiss", "acknowledge"])
    de.add_argument("--reason", required=True)
    de.add_argument("--actor", required=True)
    ap = ivs.add_parser("approve", help="record the approval of a run whose triage is done")
    ap.add_argument("run")
    ap.add_argument("--reviewer", required=True)
    ap.add_argument("--note", default=None)
    a = sub.add_parser(
        "analyze-pr",
        help="analyze one pull request: which other repositories it can break, falsify or outdate (W2, read-only)",
    )
    a.add_argument("pr", help="owner/repo#number, or https://github.com/owner/repo/pull/number")
    a.add_argument("--declarations", type=Path, default=Path("declarations"), help="directory of declaration files")
    a.add_argument(
        "--revisions",
        default="head",
        help="revisions of the *other* repositories: 'head' (default-branch HEAD) or a pin set such as 'baseline'. "
        "The pull request's own repository is always read at the PR base and head",
    )
    a.add_argument(
        "--pin",
        action="append",
        default=[],
        metavar="ID=SHA",
        help="pin another repository (repeatable); not allowed for the PR repository",
    )
    a.add_argument("--head-sha", default=None, metavar="SHA", help="offline: select the recorded input with this head")
    a.add_argument("--base-sha", default=None, metavar="SHA", help="offline: select the recorded input with this base")
    a.add_argument("--output-dir", type=Path, default=Path("reports"))
    a.add_argument("--format", default="md,json", help="comma-separated: md, json")
    a.add_argument("--cache-dir", type=Path, default=STATE_DIR / "cache", help="immutable artifact and PR-record cache")
    a.add_argument("--offline", action="store_true", help="read only from the cache; requires pinned revisions")
    a.add_argument("--state-db", type=Path, default=STATE_DIR / "state.sqlite")
    a.add_argument("--no-store", action="store_true", help="do not record the analysis in SQLite")
    a.add_argument(
        "--as-of", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD", help="date used for exception expiry"
    )
    a.add_argument("--max-requests", type=int, default=400, help="GitHub API request budget for the run")
    a.add_argument("--quiet", action="store_true")
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


def _summary(report: ScanReport, paths: list[Path], hide_exit: bool = False) -> str:
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
    if not hide_exit:
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
    if (args.replay or args.record) and not args.investigate:
        raise ConfigError("--replay and --record need --investigate")
    if args.replay and args.record:
        raise ConfigError("--replay and --record are mutually exclusive")
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
    if args.investigate:
        return _investigate(args, engine, decl, report, source, formats)
    paths = _write_reports(report, args.output_dir, formats)
    if not args.quiet:
        print(_summary(report, paths))
    return report.exit_code


def _investigation_modules() -> Any:
    try:
        from .investigation import pipeline
    except ImportError as exc:  # LangGraph is an optional extra (ADR-0017)
        raise ConfigError(
            "the investigation needs the optional dependencies: pip install 'repo-orchestrator[investigation]'"
        ) from exc
    return pipeline


def _investigate(
    args: argparse.Namespace, engine: Engine, decl: Any, report: ScanReport, source: SnapshotSource, formats: set[str]
) -> int:
    import tempfile

    from .investigation import report as inv_report
    from .investigation.provider import AnthropicProvider, ProviderError, Recording

    pipeline = _investigation_modules()
    recording = provider = None
    try:
        if args.replay:
            recording = Recording.loads(args.replay.read_text(encoding="utf-8"))
        else:
            provider = AnthropicProvider.from_env()
    except ProviderError as exc:
        hint = (
            " (set ANTHROPIC_API_KEY, or pass --replay FILE to answer from a recording)"
            if exc.kind == "no_credentials"
            else ""
        )
        raise ConfigError(f"investigation provider: {exc}{hint}") from None
    ephemeral = tempfile.TemporaryDirectory() if args.no_store else None
    try:
        base = Path(ephemeral.name) if ephemeral else args.state_db.parent
        opts = pipeline.InvestigationOptions(
            declarations_dir=args.declarations, cache_dir=args.cache_dir,
            state_db=(base / "state.sqlite") if ephemeral else args.state_db, checkpoint_path=base / "investigations.sqlite",
            provider=provider, recording=recording, declarations_digest=decl.digest, offline=args.offline,
        )  # fmt: skip
        result = pipeline.run_investigation(engine, report, source, opts)
        if args.record:
            used = {k for r in result.store.results(result.run_id).values() for k in r.get("response_keys", ())}
            args.record.parent.mkdir(parents=True, exist_ok=True)
            args.record.write_text(result.store.export_recording(used).dumps(), encoding="utf-8")
    finally:
        if ephemeral:
            ephemeral.cleanup()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    if "md" in formats:
        paths.append(args.output_dir / "report.md")
        paths[-1].write_text(
            render_markdown(result.semantic) + "\n" + inv_report.render_markdown(result.semantic), encoding="utf-8"
        )
    if "json" in formats:
        paths.append(args.output_dir / "report.json")
        body = {"semantic": result.semantic, "execution": {**report.execution, "investigation": result.execution}}
        paths[-1].write_text(json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    if not args.quiet:
        print(_summary(report, paths, hide_exit=True))
        print(
            _investigation_summary(
                result.semantic["investigation"], result.execution, result.exit_code, ephemeral is not None
            )
        )
    return int(result.exit_code)


EXIT_NAMES = [
    "complete, no findings",
    "complete, findings detected",
    "incomplete evaluation",
    "failure",
    "complete, awaiting a human decision",
]


def _investigation_summary(inv: Any, execution: Any, code: int, ephemeral: bool) -> str:
    sm = inv["summary"]
    lines = [
        f"investigation {inv['status']} · run {inv['inputs']['run_id']} · "
        + ", ".join(f"{k} {v}" for k, v in sm["by_state"].items() if v)
        + f" · quotes {sm['quotes_verified']} verified / {sm['quotes_unverified']} unverified · observations {sm['observations']}",
        f"usage: {execution['usage']['llm_calls']} model call(s) ({execution['usage']['replayed_calls']} replayed), "
        f"{execution['usage']['input_tokens']} in / {execution['usage']['output_tokens']} out tokens, "
        f"estimated cost ${execution['cost_usd_estimate']:.4f} ({execution['mode']})",
    ]
    for c in inv["candidates"]:
        why = f" [{c['cause']}]" if c["cause"] else ""
        lines.append(f"  {c['state'].upper()} {c['relationship_id']} {c['subject']}{why}: {c['reason'][:130]}")
    if ephemeral:
        lines.append("  (--no-store: the investigation state is temporary; it cannot be resumed or decided on)")
    lines.append(f"exit code {code} ({EXIT_NAMES[code]})")
    return "\n".join(lines)


def _investigations(args: argparse.Namespace) -> int:
    from .investigation import decisions
    from .investigation import report as inv_report
    from .investigation.provider import AnthropicProvider, NoProvider, ProviderError
    from .investigation.store import InvestigationStore

    pipeline = _investigation_modules()
    store = InvestigationStore(args.state_db)
    cmd = args.inv_command
    if cmd == "list":
        for r in store.runs():
            print(f"{r['run_id']}  {r['status']:<18} {r['created_at']}  reviewed={r['reviewed_at'] or '-'}")
        return OK
    run = store.run(args.run)
    if run is None:
        raise ConfigError(f"unknown investigation run {args.run}")
    opts = pipeline.InvestigationOptions(
        declarations_dir=Path(run["config"]["declarations"]), cache_dir=Path(run["config"]["cache_dir"]), state_db=args.state_db,
        checkpoint_path=args.state_db.parent / "investigations.sqlite",
    )  # fmt: skip
    if cmd == "show":
        section = inv_report.build_section(store, run, run["config"]["claims"], run["config"]["skipped"])
        if args.json:
            print(json.dumps(section, indent=2, sort_keys=True, ensure_ascii=False))
        else:
            print(
                _investigation_summary(
                    section,
                    inv_report.execution(store, run, "stored", run["status"]),
                    inv_report.exit_code({"run_status": "complete", "findings": [], "investigation": section}),
                    False,
                )
            )
        return OK
    if cmd == "resume":
        try:
            opts.provider = AnthropicProvider.from_env()
        except ProviderError:
            opts.provider = NoProvider()  # recorded answers still work; a new call fails closed (no_credentials)
        _, phase = pipeline.resume_run(args.run, opts)
        print(f"run {args.run}: {phase}")
        return OK
    if cmd == "decide":
        eff = decisions.record(store, args.run, args.candidate, args.action, args.reason, args.actor)
        print(f"candidate {args.candidate}: {eff['state']} (decision {args.action} recorded by {args.actor})")
        if not decisions.pending_triage(store, args.run):
            orch = pipeline.open_orchestrator(store, opts)
            try:
                if orch.status(args.run)["phase"] == "awaiting_triage":
                    print(f"run {args.run}: {orch.resume_triage(args.run)['phase']}")
            finally:
                orch.close()
        return OK
    orch = pipeline.open_orchestrator(store, opts)
    try:
        print(f"run {args.run}: {orch.approve(args.run, args.reviewer, args.note)['phase']}")
    finally:
        orch.close()
    return OK


PR_REF = re.compile(
    r"^(?:https://github\.com/(?P<u_owner>[\w.-]+)/(?P<u_repo>[\w.-]+)/pull/(?P<u_n>\d+)/?"
    r"|(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)#(?P<n>\d+))$"
)


def parse_pr_ref(text: str) -> tuple[str, int]:
    m = PR_REF.match(text.strip())
    if not m:
        raise ConfigError(f"expected owner/repo#number or a github.com pull request URL, got {text!r}")
    owner, repo, number = (m["u_owner"], m["u_repo"], m["u_n"]) if m["u_owner"] else (m["owner"], m["repo"], m["n"])
    if int(number) < 1:
        raise ConfigError("a pull request number starts at 1")
    return f"{owner}/{repo}", int(number)


def _pr_summary(a: PRAnalysis, paths: list[Path]) -> str:
    s = a.semantic
    pr = s["inputs"]["pull_request"]
    g, d = s["gate"], s["summary"]["deltas"]
    lines = [
        f"repo-orchestrator {s['tool']['version']} · {pr['slug']}#{pr['number']} · base {(pr['base_sha'] or '?')[:8]} → head {(pr['head_sha'] or '?')[:8]} · run {s['run_status']} · digest {s['result_digest'][:12]}",
        f"changed paths {s['summary']['changed_paths']} · touched relationships {s['summary']['relationships_touched']} · affected: {', '.join(s['summary']['affected_repositories']) or 'none'}",
        "deltas: " + ", ".join(f"{k} {v}" for k, v in d.items()),
    ]
    for e in s["impact"]:
        lines.append(
            f"  IMPACT {e['relationship_id']} {e['type']} → {', '.join(e['affected_repos'])} [{e['impact']}] ({e['direction']})"
        )
    for key, label in (
        ("introduced", "INTRODUCED"),
        ("undetermined", "UNDETERMINED"),
        ("resolved", "RESOLVED"),
        ("pre_existing", "PRE-EXISTING"),
    ):
        for f in s["findings"][key]:
            lines.append(
                f"  {label} [{f['severity']}] {f['policy_id']} {f['relationship_id']} {f['subject']}: {f['reason'][:160]}"
            )
    for p in s["problems"]:
        lines.append(f"  PROBLEM {p['side']} [{p['cause']}]: {p['detail'][:160]}")
    for o in s["unevaluated"]:
        lines.append(
            f"  CANNOT EVALUATE {o['policy_id']} {o['relationship_id']} {o['subject']} [{o['cause']}]: {o['reason'][:140]}"
        )
    lines += [f"report: {p}" for p in paths]
    names = ["complete, no introduced finding", "complete, introduced finding(s)", "incomplete evaluation", "failure"]
    lines.append(f"gate {g['result']} · exit code {a.exit_code} ({names[a.exit_code]})")
    return "\n".join(lines)


def _write_pr_reports(a: PRAnalysis, out_dir: Path, formats: set[str]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    if "md" in formats:
        paths.append(out_dir / "pr-impact.md")
        paths[-1].write_text(render_pr_markdown(a.semantic), encoding="utf-8")
    if "json" in formats:
        paths.append(out_dir / "pr-impact.json")
        body = {"semantic": a.semantic, "execution": a.execution}
        paths[-1].write_text(json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return paths


def _analyze_pr(args: argparse.Namespace) -> int:
    formats = {f.strip() for f in args.format.split(",") if f.strip()}
    if not formats or formats - {"md", "json"}:
        raise ConfigError("--format accepts md and json")
    slug, number = parse_pr_ref(args.pr)
    decl = load_declarations(args.declarations)
    engine = Engine(decl)
    pr_repo = declared_repo_id(engine, slug)  # a repository outside the declared set is a configuration error (exit 3)
    revisions = _revisions(decl.pins, args.revisions, args.pin, set(engine.graph.repositories))
    if any(item.partition("=")[0] == pr_repo for item in args.pin):
        raise ConfigError(
            f"--pin {pr_repo}=…: the pull request's own repository is read at its base and head, not pinned"
        )
    revisions.pop(pr_repo, None)  # a pin set pins the others; this repository is read at the PR base and head
    github: GitHubSource | None = None
    if not args.offline:
        github = GitHubSource(
            os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"), max_requests=args.max_requests
        )
    source = CachingSource(github, args.cache_dir)
    options = RunOptions(revisions=revisions, as_of=args.as_of or date.today(), limits=Limits())
    store = None if args.no_store else RunStore(args.state_db)
    try:
        analysis = analyze_pull_request(
            engine, source, slug, number, options, head_sha=args.head_sha, base_sha=args.base_sha, store=store,
            stats=lambda: {"api_requests": github.requests if github else 0, "cache_hits": source.hits, "cache_misses": source.misses},
        )  # fmt: skip
    finally:
        if store:
            store.close()
    paths = _write_pr_reports(analysis, args.output_dir, formats)
    if not args.quiet:
        print(_pr_summary(analysis, paths))
    return analysis.exit_code


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
        if args.command == "investigations":
            return _investigations(args)
        if args.command == "analyze-pr":
            return _analyze_pr(args)
        if args.command == "dashboard":
            return _dashboard(args)
        return _scan(args)
    except (ConfigError, DeterminismError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return FAILURE
    except Exception as exc:
        if type(exc).__module__.startswith("repo_orchestrator.investigation"):  # DecisionError, WorkflowError, ...
            print(f"error: {exc}", file=sys.stderr)
            return FAILURE
        raise
    except OSError as exc:
        print(f"error: {exc.strerror or exc}", file=sys.stderr)
        return FAILURE


if __name__ == "__main__":
    sys.exit(main())
