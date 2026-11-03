"""Static report dashboard: a read-only projection of ``report.json`` files (ADR-0012).

``repo-orchestrator dashboard`` reads reports that ``scan`` already wrote and writes plain HTML. It never evaluates,
re-classifies or infers anything: counts, statuses, ordering and run status are the report's own. Standard library only,
no network, no script in the generated pages, no timestamp of its own, so identical reports give identical pages.

Every string that comes from a report goes through :func:`e` before it reaches HTML; links to GitHub are built only from
validated parts (``owner/name``, a 40-hex commit, a clean path), never from a URL found in a report.
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import quote

from .models import ConfigError, Status, digest
from .report import SCHEMA_VERSION

REPO_URL = "https://github.com/koydas/repo-orchestrator"
# The companion dashboards, one tap away (ai-dev-tools ADR-021).
LOOP_DASHBOARD_URL = "https://koydas.github.io/autonomous-dev-loop/"
TOOLS_DASHBOARD_URL = "https://koydas.github.io/ai-dev-tools/"
MANIFEST = "index.json"
MAX_REPORT_BYTES = 8_000_000
RECENT = 5

_STATUSES = tuple(s.value for s in Status)
_SEVERITIES = ("high", "medium", "low")
# Which outcome status each report section may contain. A section holding anything else is a malformed report: this is
# what keeps `cannot_evaluate`, `policy_*` and `excepted` out of the confirmed findings.
_SECTION_STATUS = {
    "findings": Status.INCONSISTENT,
    "policy_clarifications": Status.POLICY_CLARIFICATION,
    "policy_conflicts": Status.POLICY_CONFLICT,
    "excepted": Status.EXCEPTED,
    "insufficient_evidence": Status.CANNOT_EVALUATE,
    "collection_failures": Status.CANNOT_EVALUATE,
    "evaluation_failures": Status.FAILED,
    "consistent": Status.CONSISTENT,
}
_STARTED = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# ---------------------------------------------------------------------------------------------------------
# Validation: a report is checked against the M1 contract before anything is drawn from it
# ---------------------------------------------------------------------------------------------------------


class ReportError(ValueError):
    """A report file that does not honour the M1 report contract."""


def _fail(path: str, why: str) -> NoReturn:
    raise ReportError(f"{path}: {why}")


def _need(d: Mapping[str, Any], key: str, kind: type | tuple[type, ...], path: str, *, none: bool = False) -> Any:
    kinds = kind if isinstance(kind, tuple) else (kind,)
    if key not in d:
        _fail(f"{path}.{key}", "is missing")
    v = d[key]
    if v is None and none:
        return None
    if not isinstance(v, kinds) or (isinstance(v, bool) and bool not in kinds):
        _fail(f"{path}.{key}", "must be " + " or ".join(k.__name__ for k in kinds) + (" or null" if none else ""))
    return v


def _obj(v: object, path: str) -> dict[str, Any]:
    if not isinstance(v, dict):
        _fail(path, "must be an object")
    return v


def _rows(d: Mapping[str, Any], key: str, path: str) -> list[dict[str, Any]]:
    items = _need(d, key, list, path)
    return [_obj(item, f"{path}.{key}[{i}]") for i, item in enumerate(items)]


def _strs(d: Mapping[str, Any], key: str, path: str) -> list[str]:
    items = _need(d, key, list, path)
    for i, item in enumerate(items):
        if not isinstance(item, str):
            _fail(f"{path}.{key}[{i}]", "must be a string")
    return items  # type: ignore[no-any-return]


def _check_evidence(ev: Mapping[str, Any], path: str) -> None:
    for key in ("side", "repo", "slug", "sha", "path", "locator", "kind", "quote", "content_sha256", "extractor"):
        _need(ev, key, str, path)
    _need(ev, "blob_sha", str, path, none=True)
    for key in ("line_start", "line_end"):
        _need(ev, key, int, path, none=True)


def _check_outcome(o: Mapping[str, Any], section: str, path: str) -> None:
    for key in ("policy_id", "policy_version", "relationship_id", "subject", "reason"):
        _need(o, key, str, path)
    expected = _SECTION_STATUS[section]
    if _need(o, "status", str, path) != expected.value:
        _fail(f"{path}.status", f"{o['status']!r} does not belong in {section!r} (expected {expected.value!r})")
    for key in ("affected_repos", "investigate", "open_questions"):
        _strs(o, key, path)
    for i, ev in enumerate(_rows(o, "evidence", path)):
        _check_evidence(ev, f"{path}.evidence[{i}]")
    for i, fix in enumerate(_rows(o, "fix_sites", path)):
        for key in ("repo", "path", "reason"):
            _need(fix, key, str, f"{path}.fix_sites[{i}]")
    _need(o, "pinned", dict, path)
    _need(o, "detail", dict, path)
    _need(o, "comparison", dict, path, none=True)
    for key in ("cause", "cause_class", "fingerprint", "severity", "impact", "intent", "exception_id", "superseded_by"):
        _need(o, key, str, path, none=True)
    if section == "findings":
        if o["severity"] not in _SEVERITIES:
            _fail(f"{path}.severity", f"must be one of {', '.join(_SEVERITIES)}")
        if not o["fingerprint"]:
            _fail(f"{path}.fingerprint", "is required on a finding")


def run_key(semantic: Mapping[str, Any]) -> str:
    """The identity the run store gives a run (ADR-0011 §5): same inputs and same result, same key, persisted or not."""
    return digest([digest(semantic["inputs"]), semantic["result_digest"]])[:32]


def validate_report(raw: object, name: str = "report") -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(semantic, execution)`` or raise :class:`ReportError` naming the first field that breaks the contract."""
    top = _obj(raw, name)
    p = f"{name}.semantic"
    sem = _obj(top.get("semantic"), p)
    exe = _obj(top.get("execution"), f"{name}.execution")
    if sem.get("schema_version") != SCHEMA_VERSION:
        _fail(f"{p}.schema_version", f"must be {SCHEMA_VERSION}")
    _need(_obj(sem.get("tool"), f"{p}.tool"), "version", str, f"{p}.tool")
    if _need(sem, "run_status", str, p) not in ("complete", "partial"):
        _fail(f"{p}.run_status", "must be 'complete' or 'partial'")
    inputs = _obj(sem.get("inputs"), f"{p}.inputs")
    if not _DATE.fullmatch(_need(inputs, "as_of", str, f"{p}.inputs")):
        _fail(f"{p}.inputs.as_of", "must be YYYY-MM-DD")
    _need(inputs, "declarations_digest", str, f"{p}.inputs")
    _strs(inputs, "scope", f"{p}.inputs")
    _need(inputs, "policies", list, f"{p}.inputs")
    for i, rev in enumerate(_rows(inputs, "revisions", f"{p}.inputs")):
        rp = f"{p}.inputs.revisions[{i}]"
        for key in ("repo", "slug", "requested"):
            _need(rev, key, str, rp)
        for key in ("sha", "error", "error_detail"):
            _need(rev, key, str, rp, none=True)
    counted: Counter[str] = Counter()
    for section, status in _SECTION_STATUS.items():
        for i, o in enumerate(_rows(sem, section, p)):
            _check_outcome(o, section, f"{p}.{section}[{i}]")
        counted[status.value] += len(sem[section])
    summary = _obj(sem.get("summary"), f"{p}.summary")
    by_status = _obj(summary.get("by_status"), f"{p}.summary.by_status")
    for status_name in _STATUSES:
        if _need(by_status, status_name, int, f"{p}.summary.by_status") != counted[status_name]:
            _fail(
                f"{p}.summary.by_status.{status_name}",
                f"is {by_status[status_name]} but the report lists {counted[status_name]}",
            )
    for key in ("outcomes", "incomplete", "not_checkable_by_design"):
        _need(summary, key, int, f"{p}.summary")
    if summary["outcomes"] != sum(counted.values()):
        _fail(f"{p}.summary.outcomes", f"is {summary['outcomes']} but the report lists {sum(counted.values())}")
    for key in ("coverage", "authorities", "exceptions", "superseded", "open_questions"):
        _rows(sem, key, p)
    body = {k: v for k, v in sem.items() if k != "result_digest"}
    if _need(sem, "result_digest", str, p) != digest(body):
        _fail(f"{p}.result_digest", "does not match the semantic content (edited or truncated report)")
    _check_execution(exe, run_key(sem), f"{name}.execution")
    return sem, exe


def _check_execution(exe: Mapping[str, Any], key: str, path: str) -> None:
    started = exe.get("started_at")
    if started is not None and not (isinstance(started, str) and _STARTED.match(started)):
        _fail(f"{path}.started_at", "must be YYYY-MM-DDTHH:MM:SSZ or null")
    history = exe.get("history", {})
    if not isinstance(history, dict) or not all(
        isinstance(v, list) and all(isinstance(x, str) for x in v) for v in history.values()
    ):
        _fail(f"{path}.history", "must map an event to a list of fingerprints")
    stats = exe.get("stats")
    if stats is not None and not (isinstance(stats, dict) and all(isinstance(k, str) for k in stats)):
        _fail(f"{path}.stats", "must be an object or null")
    recorded = exe.get("run_key")
    if recorded is not None and recorded != key:
        _fail(f"{path}.run_key", "does not match the run identity of the report's inputs and result")


# ---------------------------------------------------------------------------------------------------------
# Loading: report.json files in, one entry per run identity, newest first
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Report:
    key: str
    semantic: dict[str, Any]
    execution: dict[str, Any]
    files: tuple[str, ...]

    @property
    def started_at(self) -> str | None:
        return self.execution.get("started_at")

    @property
    def when(self) -> str:
        """Sort key: the execution time, else midnight of the ``as_of`` date (an input, not a clock)."""
        return self.started_at or f"{self.semantic['inputs']['as_of']}T00:00:00Z"


@dataclass(frozen=True)
class Skipped:
    file: str
    reason: str


def load_reports(directory: Path) -> tuple[list[Report], list[Skipped]]:
    """Every valid ``*.json`` under *directory* (the manifest excepted), deduplicated by run identity.

    Duplicates (the same inputs and the same result) keep the earliest execution, as the run store does. A file that
    cannot be used is reported in the second value, never dropped silently.
    """
    if not directory.is_dir():
        raise ConfigError(f"reports directory not found: {directory}")
    found: dict[str, Report] = {}
    skipped: list[Skipped] = []
    for path in sorted(directory.rglob("*.json")):
        name = path.relative_to(directory).as_posix()
        if path.name == MANIFEST or not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_REPORT_BYTES:
                raise ReportError(f"{name}: file larger than {MAX_REPORT_BYTES} bytes")
            sem, exe = validate_report(json.loads(path.read_text(encoding="utf-8")), name)
        except json.JSONDecodeError as exc:
            skipped.append(Skipped(name, f"invalid JSON ({exc.msg}, line {exc.lineno})"))
        except RecursionError:
            skipped.append(Skipped(name, "JSON nested too deeply"))
        except (OSError, UnicodeDecodeError) as exc:
            skipped.append(Skipped(name, f"unreadable ({type(exc).__name__})"))
        except ReportError as exc:
            skipped.append(Skipped(name, str(exc)))
        else:
            key = run_key(sem)
            report = Report(key, sem, exe, (name,))
            known = found.get(key)
            if known is None:
                found[key] = report
            else:
                keep = report if report.when < known.when else known
                found[key] = Report(key, keep.semantic, keep.execution, tuple(sorted({*known.files, name})))
    ordered = sorted(found.values(), key=lambda r: (r.when, r.key), reverse=True)
    return ordered, skipped


# ---------------------------------------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------------------------------------


def e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _fmt_date(iso: str) -> str:
    m = _DATE.match(iso)
    return f"{_MONTHS[int(m[2]) - 1]} {int(m[3])}, {m[1]}" if m and 1 <= int(m[2]) <= 12 else iso


def _fmt_dt(iso: str) -> str:
    return f"{_fmt_date(iso)} · {iso[11:16]} UTC" if _STARTED.match(iso) else _fmt_date(iso)


def _when(r: Report) -> str:
    return _fmt_dt(r.started_at) if r.started_at else f"As of {_fmt_date(r.semantic['inputs']['as_of'])}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _gh_url(
    slug: str, kind: str, sha: str | None = None, path: str = "", lines: tuple[int | None, int | None] = (None, None)
) -> str | None:
    """A github.com URL from validated parts only, or None. ``kind``: repo | commit | blob | tree."""
    if not _SLUG.match(slug) or not slug.split("/")[1].strip("."):
        return None
    if kind == "repo":
        return f"https://github.com/{slug}"
    if not sha or not _SHA.match(sha):
        return None
    if kind == "commit":
        return f"https://github.com/{slug}/commit/{sha}"
    segments = path.split("/")
    if not path or any(s in ("", ".", "..") or "\\" in s or _CTRL.search(s) for s in segments):
        return None
    url = f"https://github.com/{slug}/{kind}/{sha}/" + "/".join(quote(s, safe="") for s in segments)
    first, last = lines
    if kind == "blob" and isinstance(first, int) and first > 0:
        url += f"#L{first}" + (f"-L{last}" if isinstance(last, int) and last > first else "")
    return url


def _link(url: str | None, inner_html: str) -> str:
    return f'<a href="{e(url)}" rel="noopener noreferrer">{inner_html}</a>' if url else inner_html


def _code(text: object) -> str:
    return f"<code>{e(text)}</code>"


def _pill(tone: str, text: str, title: str = "") -> str:
    tip = f' title="{e(title)}"' if title else ""
    return f'<span class="pill {tone}"{tip}>{e(text)}</span>'


# Outcome statuses: colour is never the only signal (icon + label). Green is reserved for `consistent` and red for
# confirmed inconsistencies; everything unevaluated or ambiguous is amber or neutral.
STATUS_META = {
    "consistent": ("good", "✓", "Consistent", "Evidence from both sides agrees."),
    "inconsistent": ("bad", "✕", "Inconsistent", "A deterministic check failed: a confirmed finding."),
    "cannot_evaluate": ("neutral", "○", "Cannot evaluate", "Evidence missing, unavailable or undecidable. Neither a pass nor a finding."),
    "policy_clarification": ("warn", "?", "Policy clarification", "Applicability is ambiguous or an owner decision is open. A question, not a finding."),
    "policy_conflict": ("warn", "⇄", "Policy conflict", "A wider policy contradicts a repository's own verified authority. For a human. Not a finding."),
    "excepted": ("neutral", "⊘", "Excepted", "A valid, unexpired exception suppresses the finding. Not a finding."),
    "failed": ("warn", "!", "Failed", "The engine itself failed on this outcome. The run is partial."),
}  # fmt: skip
_SEVERITY_TONE = {"high": "bad", "medium": "warn", "low": "neutral"}


def _status_pill(status: str) -> str:
    tone, icon, label, hint = STATUS_META[status]
    return _pill(tone, f"{icon} {label}", hint)


def _run_pill(status: str) -> str:
    """The run status is a different thing from any outcome status: outlined, never green or red."""
    tone, icon, hint = (
        ("warn", "◐", "Some outcomes could not be evaluated: findings may be missing.")
        if status == "partial"
        else ("neutral", "●", "Every outcome was decided or reported as not checkable by design.")
    )
    return f'<span class="run {tone}" title="{e(hint)}">{icon} Run {e(status)}</span>'


def _scope_label(sem: Mapping[str, Any]) -> str:
    requested = {r["requested"] for r in sem["inputs"]["revisions"]}
    return (
        "Default-branch HEAD"
        if requested == {"HEAD"}
        else "Mixed revisions"
        if "HEAD" in requested
        else "Pinned revisions"
    )


# ---------------------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------------------

STYLE = """
:root { color-scheme: light dark; --bg: #f4f6fb; --surface: #fff; --surface-2: #f7f8fc; --ink: #101828; --ink-2: #475467; --muted: #667085; --border: #e3e8f0; --accent: #6941f5; --accent-bg: rgba(105,65,245,.10); --link: #2f5fe0;
  --good: #16a34a; --bad: #dc2626; --warn: #b45309; --neutral: #667085; --radius: 12px; }
@media (prefers-color-scheme: dark) { :root { --bg: #0a0f1d; --surface: #111a2c; --surface-2: #16213a; --ink: #e8edf7; --ink-2: #b3bed3; --muted: #8190ab; --border: #22304b; --accent: #7c5cff; --accent-bg: rgba(124,92,255,.16); --link: #7aa7ff;
  --good: #22c55e; --bad: #f05252; --warn: #fbbf24; --neutral: #8190ab; } }
:root { --good-bg: color-mix(in srgb, var(--good) 14%, var(--surface)); --bad-bg: color-mix(in srgb, var(--bad) 14%, var(--surface)); --warn-bg: color-mix(in srgb, var(--warn) 14%, var(--surface)); --neutral-bg: color-mix(in srgb, var(--neutral) 14%, var(--surface)); }
* { box-sizing: border-box; min-width: 0; }
html { scroll-padding-top: 72px; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; -webkit-font-smoothing: antialiased; overflow-wrap: anywhere; }
a { color: var(--link); text-decoration: none; } a:hover { text-decoration: underline; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 4px; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .92em; }
header { position: sticky; top: 0; z-index: 20; background: color-mix(in srgb, var(--bg) 88%, transparent); backdrop-filter: blur(8px); border-bottom: 1px solid var(--border); }
.bar { max-width: 1160px; margin: 0 auto; padding: 10px 16px; display: flex; align-items: center; gap: 6px 22px; flex-wrap: wrap; }
.brand { display: flex; align-items: center; gap: 10px; font-weight: 750; font-size: 16px; color: var(--ink); letter-spacing: -.3px; white-space: nowrap; } .brand:hover { text-decoration: none; }
.brand-icon { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 9px; color: #fff; background: linear-gradient(135deg,#8b5cf6,#6366f1); }
.brand-icon svg { width: 18px; height: 18px; }
nav.top { display: flex; gap: 4px; flex: 1; } nav.top a { color: var(--ink-2); padding: 8px 12px; border-radius: 8px; white-space: nowrap; font-weight: 600; }
nav.top a[aria-current="page"] { color: var(--ink); background: var(--accent-bg); }
.tools { display: flex; gap: 14px; margin-left: auto; white-space: nowrap; }
main { max-width: 1160px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { margin: 0; font-size: 26px; line-height: 1.2; letter-spacing: -.4px; } h2 { margin: 0; font-size: 17px; } h3 { margin: 0; font-size: 14px; }
.sub { color: var(--ink-2); margin: 4px 0 0; } .note { color: var(--muted); font-size: 13px; } .muted { color: var(--muted); }
.head { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px 24px; align-items: end; margin-bottom: 20px; }
.head .meta { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; margin-top: 8px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; margin-bottom: 16px; }
.card-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
.grid { display: grid; gap: 16px; } .g2 { grid-template-columns: repeat(2, minmax(0, 1fr)); } .g2 > .card { margin-bottom: 0; }
.cols { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 16px; align-items: start; } .cols > aside { position: sticky; top: 64px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin: 0 0 16px; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 14px; }
.tile .label { font-size: 11px; font-weight: 650; text-transform: uppercase; letter-spacing: .5px; color: var(--muted); }
.tile .value { font-size: 28px; font-weight: 750; line-height: 1.2; font-variant-numeric: tabular-nums; } .tile .sub { font-size: 12px; margin: 0; color: var(--muted); }
.tile.bad .value { color: var(--bad); } .tile.warn .value { color: var(--warn); }
.status { display: flex; align-items: center; gap: 10px; margin: 0 0 16px; padding: 10px 14px; border: 1px solid var(--border); border-left: 5px solid var(--tone); border-radius: var(--radius); background: var(--surface); color: var(--ink); }
.status strong { display: block; color: var(--tone); } .status small { display: block; color: var(--ink-2); font-size: 12px; } .status:hover { text-decoration: none; }
.mark { display: grid; place-items: center; flex: none; width: 24px; height: 24px; border: 1.5px solid var(--tone); border-radius: 50%; color: var(--tone); font-weight: 700; }
.tone-good { --tone: var(--good); } .tone-bad { --tone: var(--bad); } .tone-warn { --tone: var(--warn); } .tone-neutral { --tone: var(--neutral); }
.pill { display: inline-block; padding: 1px 9px; border-radius: 6px; font-size: 12px; font-weight: 600; white-space: nowrap; }
.run { display: inline-block; padding: 1px 10px; border: 1px solid currentColor; border-radius: 999px; font-size: 12px; font-weight: 650; white-space: nowrap; }
.good { color: var(--good); background: var(--good-bg); } .bad { color: var(--bad); background: var(--bad-bg); } .warn { color: var(--warn); background: var(--warn-bg); } .neutral { color: var(--neutral); background: var(--neutral-bg); }
.run.good, .run.bad, .run.warn, .run.neutral { background: none; }
.chip { display: inline-block; padding: 0 8px; border: 1px solid var(--border); border-radius: 999px; font-size: 12px; color: var(--ink-2); background: var(--surface-2); }
ul.plain { list-style: none; margin: 0; padding: 0; } ul.plain > li { padding: 9px 0; border-top: 1px solid var(--border); } ul.plain > li:first-child { border-top: 0; }
.split { display: flex; justify-content: space-between; align-items: center; gap: 4px 12px; flex-wrap: wrap; }
.counts { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; font-variant-numeric: tabular-nums; }
.legend li { display: flex; justify-content: space-between; gap: 12px; align-items: center; } .legend .zero { opacity: .55; }
table { width: 100%; border-collapse: collapse; font-size: 13px; } th, td { text-align: left; padding: 9px 8px; border-top: 1px solid var(--border); vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: 12px; border-top: 0; } td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.btn { display: inline-flex; align-items: center; gap: 6px; padding: 7px 13px; min-height: 36px; border: 1px solid var(--accent); border-radius: 8px; background: var(--accent-bg); color: var(--ink); font-weight: 600; } .btn:hover { text-decoration: none; background: var(--surface-2); }
.notice { padding: 10px 14px; border: 1px solid var(--border); border-left: 4px solid var(--neutral); border-radius: 10px; background: var(--surface); color: var(--ink-2); font-size: 13px; margin: 0 0 16px; }
.notice.warn { border-left-color: var(--warn); } .notice strong { color: var(--ink); }
.item { padding: 14px 0; border-top: 1px solid var(--border); } .item:first-of-type { border-top: 0; padding-top: 4px; }
.item-head { display: flex; flex-wrap: wrap; gap: 6px 8px; align-items: center; } .item p { margin: 8px 0; }
dl.meta { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 4px 14px; margin: 8px 0; font-size: 13px; } dl.meta dt { color: var(--muted); } dl.meta dd { margin: 0; }
dl.meta ol, dl.meta ul { margin: 0; padding-left: 18px; }
details { margin: 6px 0; } summary { cursor: pointer; color: var(--ink-2); font-weight: 600; min-height: 28px; } summary:hover { color: var(--ink); }
details.panel { border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); padding: 0 16px; margin: 0 0 12px; } details.panel > summary { padding: 12px 0; color: var(--ink); font-size: 15px; } details.panel[open] { padding-bottom: 14px; }
.ev { margin: 8px 0; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-2); font-size: 13px; }
pre { margin: 6px 0 0; padding: 8px 10px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; white-space: pre-wrap; overflow-wrap: anywhere; max-width: 100%; }
.jump { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 16px; } .jump a { padding: 4px 12px; border: 1px solid var(--border); border-radius: 999px; background: var(--surface); color: var(--ink-2); font-weight: 600; }
.row-link { display: block; color: inherit; } .row-link:hover { text-decoration: none; }
.tabbar { display: none; }
footer { max-width: 1160px; margin: 0 auto; padding: 0 16px 32px; color: var(--muted); font-size: 13px; }
@media (max-width: 900px) { .g2, .cols { grid-template-columns: minmax(0, 1fr); } .cols > aside { position: static; } .g2 > .card { margin-bottom: 0; } }
@media (max-width: 760px) {
  nav.top, .tools { display: none; } main { padding-top: 14px; } h1 { font-size: 22px; } body { padding-bottom: 72px; }
  .tabbar { display: grid; grid-template-columns: repeat(3, 1fr); position: fixed; bottom: 0; left: 0; right: 0; z-index: 25; background: var(--surface); border-top: 1px solid var(--border); padding: 4px 4px calc(4px + env(safe-area-inset-bottom)); }
  .tabbar a { display: grid; justify-items: center; gap: 2px; padding: 6px 2px; min-height: 44px; font-size: 10.5px; font-weight: 600; color: var(--muted); } .tabbar a:hover { text-decoration: none; }
  .tabbar a[aria-current="page"] { color: var(--accent); } .tabbar svg { width: 22px; height: 22px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
  .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  table.stack thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); } table.stack, table.stack tbody, table.stack tr, table.stack td { display: block; width: 100%; }
  table.stack tr { padding: 10px 0; border-top: 1px solid var(--border); } table.stack td { border: 0; padding: 3px 0 3px 38%; position: relative; text-align: left; min-height: 24px; }
  table.stack td::before { content: attr(data-label); position: absolute; left: 0; width: 34%; color: var(--muted); font-size: 12px; font-weight: 600; }
  table.stack td.num { text-align: left; } dl.meta { grid-template-columns: minmax(0, 1fr); gap: 0; } dl.meta dd { margin-bottom: 6px; }
}
@media (max-width: 420px) { main { padding: 12px 12px 40px; } .card { padding: 12px; } details.panel { padding: 0 12px; } }
"""

_CURRENT = ' aria-current="page"'
_ICON = {
    "layers": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/></svg>',
    "overview": '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>',
    "reports": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3.5" cy="6" r="1"/><circle cx="3.5" cy="12" r="1"/><circle cx="3.5" cy="18" r="1"/></svg>',
    "latest": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M9 14h6M9 18h4"/></svg>',
}


def _layout(title: str, page: str, body: str, *, root: str, latest: Report | None, version: str | None) -> str:
    def nav(href: str, label: str, key: str) -> str:
        return f'<a href="{root}{href}"{_CURRENT if page == key else ""}>{label}</a>'

    tabs = [("index.html", "Overview", "overview", "overview"), ("reports.html", "Reports", "reports", "reports")]
    if latest:
        tabs.append((f"reports/{latest.key}.html", "Latest", "report", "latest"))
    tabbar = "".join(
        f'<a href="{root}{href}"{_CURRENT if page == key else ""}>{_ICON[icon]}{label}</a>'
        for href, label, key, icon in tabs
    )
    built = f"Static projection of the report.json files of repo-orchestrator{f' {e(version)}' if version else ''}: read-only, nothing is re-evaluated here."
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light dark">
<title>{e(title)} — Repo Orchestrator</title><style>{STYLE}</style></head>
<body><header><div class="bar">
<a class="brand" href="{root}index.html"><span class="brand-icon">{_ICON["layers"]}</span>Repo Orchestrator</a>
<nav class="top" aria-label="Pages">{nav("index.html", "Overview", "overview")}{nav("reports.html", "Reports", "reports")}</nav>
<span class="tools"><a href="{LOOP_DASHBOARD_URL}" rel="noopener">Dev loop ⇄</a><a href="{TOOLS_DASHBOARD_URL}" rel="noopener">Tools ⇄</a><a href="{REPO_URL}" rel="noopener">Repository →</a></span>
</div></header>
<main>{body}</main>
<footer>{built}</footer>
<nav class="tabbar" aria-label="Pages">{tabbar}</nav>
</body></html>
"""


@dataclass(frozen=True)
class _Ctx:
    """Lookups a report's outcomes need to be linked: repository id → (slug, commit), open question id → text."""

    revs: dict[str, tuple[str, str | None]]
    questions: dict[str, str]
    history: dict[str, str]  # fingerprint → run-store event of this run


def _ctx(r: Report) -> _Ctx:
    sem = r.semantic
    revs = {x["repo"]: (x["slug"], x["sha"]) for x in sem["inputs"]["revisions"]}
    events = {fp: ev for ev, fps in r.execution.get("history", {}).items() for fp in fps}
    return _Ctx(
        revs, {q["id"]: q.get("text", "") for q in sem["open_questions"] if isinstance(q.get("id"), str)}, events
    )


def _repo_html(ctx: _Ctx, repo_id: str) -> str:
    slug, sha = ctx.revs.get(repo_id, (None, None))
    if slug is None:
        return _code(repo_id)
    pin = f" {_link(_gh_url(slug, 'commit', sha), _code(sha[:7]))}" if sha else ""
    return f"{_link(_gh_url(slug, 'repo'), e(slug))}{pin}"


def _path_ref_html(ctx: _Ctx, repo_id: str, path: str) -> str:
    """A path inside a repository of the run, linked at the commit the run pinned."""
    slug, sha = ctx.revs.get(repo_id, (None, None))
    url = _gh_url(slug, "blob", sha, path) if slug else None
    return _link(url, _code(f"{repo_id}:{path}"))


def _evidence_html(ev: Mapping[str, Any]) -> str:
    lines = ""
    if ev["line_start"]:
        lines = f":{ev['line_start']}" + (
            f"-{ev['line_end']}" if ev["line_end"] not in (None, ev["line_start"]) else ""
        )
    kind = "tree" if ev["kind"] == "tree_listing" else "blob"
    where = _link(
        _gh_url(ev["slug"], kind, ev["sha"], ev["path"], (ev["line_start"], ev["line_end"])),
        _code(f"{ev['path']}{lines}"),
    )
    commit = _link(_gh_url(ev["slug"], "commit", ev["sha"]), _code(f"{ev['slug']}@{ev['sha'][:12]}"))
    quote_html = f"<pre>{e(ev['quote'])}</pre>" if ev["quote"] else ""
    return (
        f'<div class="ev"><span class="chip">{e(ev["side"])}</span> {commit} {where}'
        f'<div class="note">{e(ev["locator"])} · {e(ev["kind"])} · {e(ev["extractor"])} · sha256 {_code(ev["content_sha256"][:12])}'
        f"{' · blob ' + _code(ev['blob_sha'][:12]) if ev['blob_sha'] else ''}</div>{quote_html}</div>"
    )


def _fmt_value(v: object) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (list, tuple)):
        return ", ".join(_fmt_value(x) for x in v) or "—"
    return str(v)


def _json_pre(value: object) -> str:
    return f"<pre>{e(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))}</pre>"


def _outcome_html(o: Mapping[str, Any], ctx: _Ctx, anchor: str, *, finding: bool) -> str:
    head = []
    if finding:
        head.append(
            _pill(
                _SEVERITY_TONE[o["severity"]],
                o["severity"].capitalize(),
                "Severity: impact class and relationship type",
            )
        )
    else:
        head.append(_status_pill(o["status"]))
    head += [
        f'<span class="chip">{e(o["policy_id"])} v{e(o["policy_version"])}</span>',
        f'<span class="chip">{e(o["relationship_id"])}</span>',
        _code(o["subject"]),
    ]
    meta: list[tuple[str, str]] = []
    if finding:
        meta.append(("Impact · intent", f"{e(o['impact'])} · {e(o['intent'])}"))
        attribution = o["detail"].get("attribution")
        if isinstance(attribution, str):
            meta.append(("Attributed to", e(attribution)))
        meta.append((
            "Verification",
            _pill("neutral", "Evidence verified", "Each quote was re-checked in code against the pinned snapshot")
            + " "
            + _pill("neutral", "Conclusion validated by check", "A deterministic check reached the conclusion; there is no confidence score"),
        ))  # fmt: skip
        if o["fingerprint"] in ctx.history:
            meta.append(("In the run store", _pill("neutral", ctx.history[o["fingerprint"]].replace("_", " "))))
    elif o["cause"]:
        meta.append(("Cause", f"{_code(o['cause'])} <span class='note'>({e(o['cause_class'])})</span>"))
    if o["exception_id"]:
        meta.append(("Exception", _code(o["exception_id"])))
    if o["superseded_by"]:
        meta.append(("Superseded by", _code(o["superseded_by"])))
    if o["affected_repos"]:
        meta.append(("Affected repositories", "<br>".join(_repo_html(ctx, r) for r in o["affected_repos"])))
    c = o["comparison"]
    if c:
        meta.append(
            (
                "Comparison",
                f"{e(c.get('comparator'))}: {_code(c.get('left'))} vs {_code(c.get('right'))} → <strong>{e(c.get('result'))}</strong>",
            )
        )
    if o["open_questions"]:
        items = "".join(f"<li>{_code(q)} {e(ctx.questions.get(q, ''))}</li>" for q in o["open_questions"])
        meta.append(("Open questions", f"<ul>{items}</ul>"))
    if o["fix_sites"]:
        items = "".join(
            f"<li>{_path_ref_html(ctx, f['repo'], f['path'])} <span class='muted'>{e(f['reason'])}</span></li>"
            for f in o["fix_sites"]
        )
        meta.append(("Where to fix, in order", f"<ol>{items}</ol>"))
    if o["investigate"]:
        refs = []
        for ref in o["investigate"]:
            repo_id, _, path = ref.partition(":")
            refs.append(_path_ref_html(ctx, repo_id, path) if path else _code(ref))
        meta.append(("Investigate", " · ".join(refs)))
    dl = "".join(f"<dt>{label}</dt><dd>{value}</dd>" for label, value in meta)
    evidence = ""
    if o["evidence"]:
        evidence = f"<details><summary>Evidence · {_plural(len(o['evidence']), 'source')}</summary>{''.join(_evidence_html(x) for x in o['evidence'])}</details>"
    technical = {k: o[k] for k in ("fingerprint", "locators", "detail", "pinned") if o.get(k)}
    tech = f"<details><summary>Technical metadata</summary>{_json_pre(technical)}</details>" if technical else ""
    return (
        f'<article class="item" id="{e(anchor)}"><div class="item-head">{"".join(head)}</div>'
        f'<p>{e(o["reason"])}</p><dl class="meta">{dl}</dl>{evidence}{tech}</article>'
    )


def _stat(label: str, value: int, note: str, tone: str = "") -> str:
    return f'<div class="tile {tone}"><div class="label">{e(label)}</div><div class="value">{value}</div><p class="sub">{e(note)}</p></div>'


def _tiles(sem: Mapping[str, Any]) -> str:
    by, sm = sem["summary"]["by_status"], sem["summary"]
    tiles = [
        _stat("Outcomes", sm["outcomes"], f"{by['consistent']} consistent"),
        _stat(
            "Confirmed inconsistencies",
            by["inconsistent"],
            "deterministic checks failed",
            "bad" if by["inconsistent"] else "",
        ),
    ]
    notes = [f"{sm['incomplete']} incomplete"] if sm["incomplete"] else []
    notes += [f"{sm['not_checkable_by_design']} not checkable by design"] if sm["not_checkable_by_design"] else []
    tiles.append(
        _stat(
            "Cannot evaluate",
            by["cannot_evaluate"],
            " · ".join(notes) or "none incomplete",
            "warn" if sm["incomplete"] else "",
        )
    )
    policy = by["policy_clarification"] + by["policy_conflict"]
    if policy:
        tiles.append(
            _stat(
                "Policy",
                policy,
                f"{_plural(by['policy_clarification'], 'clarification')} · {_plural(by['policy_conflict'], 'conflict')}",
                "warn",
            )
        )
    if by["excepted"]:
        tiles.append(_stat("Excepted", by["excepted"], "suppressed by an exception"))
    if by["failed"]:
        tiles.append(_stat("Failed", by["failed"], "engine failures", "warn"))
    return f'<div class="tiles">{"".join(tiles)}</div>'


def _breakdown(sem: Mapping[str, Any]) -> str:
    by = sem["summary"]["by_status"]
    items = "".join(
        f'<li class="{"zero" if not by[s] else ""}">{_status_pill(s)}<strong>{by[s]}</strong></li>' for s in STATUS_META
    )
    return f'<ul class="plain legend">{items}</ul>'


def _status_line(r: Report) -> str:
    sem = r.semantic
    n, partial = sem["summary"]["by_status"]["inconsistent"], sem["run_status"] == "partial"
    if n:
        tone, mark = "bad", "✕"
        title = f"{n} confirmed {'inconsistency' if n == 1 else 'inconsistencies'}"
    elif partial:
        tone, mark, title = "warn", "!", "No confirmed inconsistency, but the run is partial"
    else:
        tone, mark, title = "good", "✓", "No confirmed inconsistency"
    sub = f"Run {sem['run_status']} · {_plural(sem['summary']['outcomes'], 'outcome')} · {_when(r)}"
    return (
        f'<a class="status tone-{tone}" href="reports/{r.key}.html"><span class="mark" aria-hidden="true">{mark}</span>'
        f"<span><strong>{e(title)}</strong><small>{e(sub)}</small></span></a>"
    )


def _report_row(r: Report, root: str = "") -> str:
    sem = r.semantic
    by = sem["summary"]["by_status"]
    bad = _pill("bad" if by["inconsistent"] else "neutral", f"{by['inconsistent']} inconsistent")
    unknown = _pill("neutral", f"{by['cannot_evaluate']} cannot evaluate")
    return (
        f'<li><a class="row-link" href="{root}reports/{r.key}.html"><div class="split"><strong>{e(_when(r))}</strong>{_run_pill(sem["run_status"])}</div>'
        f'<div class="split note"><span>{e(_scope_label(sem))} · as of {e(_fmt_date(sem["inputs"]["as_of"]))}</span>'
        f'<span class="counts">{bad}{unknown}</span></div></a></li>'
    )


def _skipped_html(skipped: Sequence[Skipped]) -> str:
    if not skipped:
        return ""
    items = "".join(f"<li>{_code(s.file)} {e(s.reason)}</li>" for s in skipped)
    return (
        f'<div class="notice warn"><strong>{_plural(len(skipped), "report file")} could not be used</strong> and '
        f"{'is' if len(skipped) == 1 else 'are'} not shown.<details><summary>Details</summary><ul>{items}</ul></details></div>"
    )


def _empty_html(skipped: Sequence[Skipped]) -> str:
    return (
        f'{_skipped_html(skipped)}<div class="card"><h2>No report yet</h2><p class="sub">This dashboard shows the report.json files written by '
        "<code>repo-orchestrator scan</code>; none was found, so there is nothing to display. Nothing is invented in their place.</p>"
        "<pre>repo-orchestrator scan --output-dir reports/\nrepo-orchestrator dashboard --reports reports --out _site</pre></div>"
    )


def render_overview(reports: Sequence[Report], skipped: Sequence[Skipped]) -> str:
    if not reports:
        body = f'<div class="head"><div><h1>Overview</h1><p class="sub">Cross-repository coherence reports.</p></div></div>{_empty_html(skipped)}'
        return _layout("Overview", "overview", body, root="", latest=None, version=None)
    r = reports[0]
    sem, ctx = r.semantic, _ctx(r)
    by = sem["summary"]["by_status"]
    findings = sem["findings"]
    repos = "".join(
        f"<li><div class='split'><span>{_repo_html(ctx, rev['repo'])}</span><span>"
        + (_pill("warn", f"unresolved: {rev['error']}") if rev["error"] else "")
        + (
            _pill("bad", _plural(n, "finding"))
            if (n := sum(1 for f in findings if rev["repo"] in f["affected_repos"]))
            else ""
        )
        + "</span></div></li>"
        for rev in sem["inputs"]["revisions"]
    )
    if findings:
        shown = "".join(
            f"<li><a class='row-link' href='reports/{r.key}.html#f-{e(f['fingerprint'])}'><div class='item-head'>"
            f"{_pill(_SEVERITY_TONE[f['severity']], f['severity'].capitalize())}<strong>{e(', '.join(f['affected_repos']))}</strong>"
            f"<span class='chip'>{e(f['policy_id'])}</span></div><div class='note'>{e(f['reason'])}</div></a></li>"
            for f in findings[:3]
        )
        latest_findings = f'<ul class="plain">{shown}</ul>'
    else:
        latest_findings = (
            '<p class="note">No confirmed inconsistency in this run.'
            + (" The run is partial: that is not proof that none exists." if sem["run_status"] == "partial" else "")
            + "</p>"
        )
    recent = "".join(_report_row(x) for x in reports[:RECENT])
    body = f"""<div class="head"><div><h1>Overview</h1><p class="sub">Latest report of {e(_plural(len(reports), "report"))}.</p></div>
<a class="btn" href="reports/{r.key}.html">Open the detailed report →</a></div>
{_skipped_html(skipped)}{_status_line(r)}{_tiles(sem)}
<div class="grid g2">
<section class="card"><div class="card-head"><h2>Latest run</h2>{_run_pill(sem["run_status"])}</div>
<p class="note">{e(_when(r))} · {e(_scope_label(sem))} · as of {e(_fmt_date(sem["inputs"]["as_of"]))}</p>{_breakdown(sem)}</section>
<section class="card"><div class="card-head"><h2>Repositories ({len(sem["inputs"]["revisions"])})</h2><span class="note">pinned commits of this run</span></div><ul class="plain">{repos}</ul></section>
<section class="card"><div class="card-head"><h2>Recent reports</h2><a href="reports.html">View all →</a></div><ul class="plain">{recent}</ul></section>
<section class="card"><div class="card-head"><h2>Latest findings</h2><a href="reports/{r.key}.html#findings">View all ({by["inconsistent"]}) →</a></div>{latest_findings}</section>
</div>"""
    return _layout("Overview", "overview", body, root="", latest=r, version=sem["tool"]["version"])


def render_reports(reports: Sequence[Report], skipped: Sequence[Skipped]) -> str:
    if not reports:
        body = f'<div class="head"><div><h1>Reports</h1><p class="sub">Generated reports, newest first.</p></div></div>{_empty_html(skipped)}'
        return _layout("Reports", "reports", body, root="", latest=None, version=None)
    rows = "".join(
        f'<tr><td data-label="Executed"><a href="reports/{r.key}.html"><strong>{e(_when(r))}</strong></a><div class="note">{e(_scope_label(r.semantic))} · as of {e(_fmt_date(r.semantic["inputs"]["as_of"]))}</div></td>'
        f'<td data-label="Run">{_run_pill(r.semantic["run_status"])}</td>'
        f'<td class="num" data-label="Inconsistent">{_pill("bad" if r.semantic["summary"]["by_status"]["inconsistent"] else "neutral", str(r.semantic["summary"]["by_status"]["inconsistent"]))}</td>'
        f'<td class="num" data-label="Cannot evaluate">{r.semantic["summary"]["by_status"]["cannot_evaluate"]}</td>'
        f'<td data-label="Report"><a href="reports/{r.key}.html">Details →</a></td></tr>'
        for r in reports
    )
    body = f"""<div class="head"><div><h1>Reports</h1><p class="sub">{e(_plural(len(reports), "report"))}, newest first. A run repeated with identical inputs and result is listed once.</p></div></div>
{_skipped_html(skipped)}<div class="card"><table class="stack"><thead><tr><th>Executed</th><th>Run</th><th class="num">Inconsistent</th><th class="num">Cannot evaluate</th><th>Report</th></tr></thead><tbody>{rows}</tbody></table></div>"""
    return _layout(
        "Reports", "reports", body, root="", latest=reports[0], version=reports[0].semantic["tool"]["version"]
    )


_OTHER = (
    ("policy_clarifications", "Policy clarifications", "Questions for the owner. Never findings."),
    ("policy_conflicts", "Policy conflicts", "A wider policy contradicts a repository's own verified authority. For a human to decide. Never findings."),
    ("excepted", "Intentional exceptions", "Suppressed by a valid, unexpired exception. Never findings."),
    ("insufficient_evidence", "Insufficient or undecidable evidence", "Neither a pass nor a finding. Makes the run partial, except claims not checkable by design."),
    ("collection_failures", "Collection failures", "A repository or artifact could not be read. Makes the run partial."),
    ("evaluation_failures", "Evaluation failures", "The engine failed on these outcomes. Makes the run partial."),
    ("consistent", "Consistent relationships", "Evidence from both sides agrees."),
)  # fmt: skip


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    head = "".join(f"<th>{e(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f'<td data-label="{e(h)}">{c}</td>' for h, c in zip(headers, row, strict=True)) + "</tr>"
        for row in rows
    )
    return f'<table class="stack"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def _details_html(r: Report, ctx: _Ctx) -> str:
    sem, exe = r.semantic, r.execution
    inputs = sem["inputs"]
    revs = _table(
        ["Repository", "Requested", "Pinned commit", "Error"],
        [
            [
                f"{_code(x['repo'])} {_link(_gh_url(x['slug'], 'repo'), e(x['slug']))}",
                _code(x["requested"][:12]),
                _link(_gh_url(x["slug"], "commit", x["sha"]), _code(x["sha"])) if x["sha"] else "—",
                f"{e(x['error'])} {e(x['error_detail'])}" if x["error"] else "",
            ]
            for x in inputs["revisions"]
        ],
    )
    facts = _table(
        ["As of", "Scope", "Declarations digest", "Policies", "Result digest", "Run identity"],
        [[
            e(inputs["as_of"]), e(", ".join(inputs["scope"])), _code(inputs["declarations_digest"][:16]),
            e(", ".join(f"{p['id']} v{p['version']}" for p in inputs["policies"] if isinstance(p, dict))),
            _code(sem["result_digest"][:16]), _code(r.key),
        ]],
    )  # fmt: skip
    cov = _table(
        ["Relationship", "Baseline", "Type", "Direction", "Status", "Policies", "Note"],
        [
            [
                e(c.get("relationship_id")),
                e(c.get("baseline_id")),
                e(c.get("type")),
                e(f"{c.get('source')} → {c.get('target')}"),
                e(c.get("status")),
                e(_fmt_value(c.get("policies"))),
                e(c.get("note")),
            ]
            for c in sem["coverage"]
        ],
    )
    questions = _table(
        ["Id", "Status", "Question", "Effect in this run", "Referenced by"],
        [
            [
                e(q.get("id")),
                e(q.get("status")),
                e(q.get("text")),
                e(q.get("effect")),
                e(_fmt_value(q.get("referenced_by"))),
            ]
            for q in sem["open_questions"]
        ],
    )
    authorities = _table(
        ["Policy", "Authority", "Verified", "Note"],
        [
            [
                e(a.get("policy_id")),
                _code(f"{a.get('repo')}:{a.get('path')}"),
                e(_fmt_value(a.get("verified"))),
                e(a.get("note")),
            ]
            for a in sem["authorities"]
        ],
    )
    exceptions = (
        _table(
            ["Id", "Policy", "State", "Review by", "Owner"],
            [
                [e(x.get("id")), e(x.get("policy")), e(x.get("state")), e(x.get("review_by")), e(x.get("owner"))]
                for x in sem["exceptions"]
            ],
        )
        if sem["exceptions"]
        else '<p class="note">No exception declared.</p>'
    )
    superseded = f"<h3>Superseded</h3>{_json_pre(sem['superseded'])}" if sem["superseded"] else ""
    history = exe.get("history", {})
    if exe.get("persisted") and history:
        events = "".join(
            f"<li>{e(ev.replace('_', ' '))}: {' '.join(_code(fp) for fp in fps)}</li>"
            for ev, fps in sorted(history.items())
        )
        hist = (
            f'<ul>{events}</ul><p class="note">Transitions of this run against the run store. A finding that is only '
            "<em>not evaluated</em> is not resolved: absence from a partial run proves nothing.</p>"
        )
    else:
        hist = '<p class="note">Not recorded: this report was written without a run store (<code>--no-store</code>) or the store holds no transition for it.</p>'
    runtime = _table(
        ["Run id", "Started", "Duration", "Stats", "Persisted", "New in store", "Report files"],
        [[
            _code(exe.get("run_id")), e(exe.get("started_at") or "not recorded"), e(f"{exe['duration_ms']} ms" if isinstance(exe.get("duration_ms"), int) else "—"),
            e(_fmt_value(exe.get("stats") and [f"{k}={v}" for k, v in sorted(exe["stats"].items())])), e(_fmt_value(exe.get("persisted"))),
            e(_fmt_value(exe.get("new_run"))), e(", ".join(r.files)),
        ]],
    )  # fmt: skip
    panels = [
        ("Inputs and pinned revisions", facts + revs),
        ("Coverage of declared relationships", cov),
        ("Open questions", questions),
        ("Policy authorities and exceptions", authorities + exceptions + superseded),
        (
            "Execution metadata (volatile, not part of the reproducible result)",
            runtime + f"<h3>Finding history</h3>{hist}",
        ),
    ]
    out = "".join(f'<details class="panel"><summary>{e(t)}</summary>{content}</details>' for t, content in panels)
    return f'<h2 id="details" style="margin:24px 0 10px">Technical details</h2>{out}<p class="note">Original file: <a href="{r.key}.json">report.json</a></p>'


def render_report(r: Report, latest: Report) -> str:
    sem, ctx = r.semantic, _ctx(r)
    by, sm = sem["summary"]["by_status"], sem["summary"]
    if sem["run_status"] == "partial":
        caution = (
            f'<div class="notice warn"><strong>Partial run.</strong> {_plural(sm["incomplete"], "outcome")} could not be evaluated or failed, '
            "so findings may be missing. A finding that does not appear here is <strong>not</strong> resolved.</div>"
        )
    else:
        caution = f'<div class="notice"><strong>Complete run.</strong> No outcome was left undecided; {sm["not_checkable_by_design"]} claim(s) not checkable by design are reported, not counted as failures.</div>'
    findings = (
        "".join(_outcome_html(f, ctx, f"f-{f['fingerprint']}", finding=True) for f in sem["findings"])
        or '<p class="note">No confirmed inconsistency in this run.</p>'
    )
    others = []
    for key, title, hint in _OTHER:
        items = sem[key]
        if not items:
            continue
        cards = "".join(_outcome_html(o, ctx, f"o-{key}-{i}", finding=False) for i, o in enumerate(items))
        others.append(
            f'<details class="panel" id="{key}"{" open" if key != "consistent" else ""}><summary>{e(title)} ({len(items)})</summary><p class="note">{e(hint)}</p>{cards}</details>'
        )
    others_html = "".join(others) or '<p class="note">No other outcome.</p>'
    jump = f'<nav class="jump" aria-label="On this page"><a href="#summary">Summary</a><a href="#findings">Findings ({by["inconsistent"]})</a><a href="#other">Other outcomes ({sm["outcomes"] - by["inconsistent"]})</a><a href="#details">Details</a></nav>'
    body = f"""<p class="note"><a href="../reports.html">← Reports</a></p>
<div class="head"><div><h1>{e(_when(r))}</h1><div class="meta">{_run_pill(sem["run_status"])}<span class="chip">{e(_scope_label(sem))}</span><span class="chip">as of {e(_fmt_date(sem["inputs"]["as_of"]))}</span><span class="chip">digest {e(sem["result_digest"][:12])}</span></div></div></div>
{jump}
<div class="cols"><div>
<section id="summary">{caution}{_tiles(sem)}</section>
<section class="card" id="findings"><div class="card-head"><h2>Findings ({by["inconsistent"]})</h2><span class="note">Confirmed by a deterministic check · ordered by severity, affected repositories, fingerprint</span></div>{findings}</section>
<h2 id="other" style="margin:24px 0 10px">Other outcomes</h2>{others_html}
{_details_html(r, ctx)}
</div><aside><section class="card"><h2>Outcomes by status</h2>{_breakdown(sem)}</section>
<section class="card"><h2>Repositories</h2><ul class="plain">{"".join(f"<li>{_repo_html(ctx, x['repo'])}</li>" for x in sem["inputs"]["revisions"])}</ul></section></aside></div>"""
    return _layout(_when(r), "report", body, root="../", latest=latest, version=sem["tool"]["version"])


# ---------------------------------------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildResult:
    reports: int
    skipped: tuple[Skipped, ...]
    files: tuple[str, ...]


def status_json(reports: Sequence[Report]) -> str:
    """Same shape as the sibling dashboards' status.json (ai-dev-tools ADR-021): ok | warn | bad plus a headline."""
    if not reports:
        data: dict[str, Any] = {"generated_at": None, "status": "warn", "headline": "no report published"}
    else:
        sem = reports[0].semantic
        n = sem["summary"]["by_status"]["inconsistent"]
        status = "bad" if n else "warn" if sem["run_status"] == "partial" else "ok"
        parts = [f"{n} confirmed inconsistenc{'y' if n == 1 else 'ies'}"] if n else []
        parts += ["partial run"] if sem["run_status"] == "partial" else []
        data = {
            "generated_at": sem["inputs"]["as_of"],
            "status": status,
            "headline": " · ".join(parts) or "no confirmed inconsistency",
        }
    return json.dumps({"schema_version": 1, "app": "repo-orchestrator", **data}, indent=2) + "\n"


def build_dashboard(reports_dir: Path, out_dir: Path) -> BuildResult:
    """Write the three views, one JSON copy per report (the history carrier) and status.json into *out_dir*.

    Existing files are overwritten and none is deleted: build into a fresh directory.
    """
    reports, skipped = load_reports(reports_dir)
    pages: dict[str, str] = {
        "index.html": render_overview(reports, skipped),
        "reports.html": render_reports(reports, skipped),
    }
    for r in reports:
        pages[f"reports/{r.key}.html"] = render_report(r, reports[0])
        pages[f"reports/{r.key}.json"] = (
            json.dumps({"semantic": r.semantic, "execution": r.execution}, indent=2, sort_keys=True, ensure_ascii=False)
            + "\n"
        )
    pages[f"reports/{MANIFEST}"] = (
        json.dumps({"schema_version": 1, "reports": [f"{r.key}.json" for r in reports]}, indent=2) + "\n"
    )
    pages["status.json"] = status_json(reports)
    for name, content in pages.items():
        target = out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return BuildResult(len(reports), tuple(skipped), tuple(sorted(pages)))
