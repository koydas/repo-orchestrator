"""C7 report assembly. The *semantic* report is a pure function of the inputs and carries a digest; volatile execution
metadata (run id, timestamps, cache statistics) lives next to it, never inside it (ADR-0006 §6, ADR-0008)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import __version__
from .declarations import Declarations
from .engine import ScanResult
from .extract import EXTRACTOR_VERSION
from .models import Cause, Outcome, Severity, Status, cause_class, digest, to_jsonable

SCHEMA_VERSION = 1
_SEVERITY_ORDER = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}


def outcome_json(o: Outcome, result: ScanResult) -> dict[str, Any]:
    data = to_jsonable(o)
    repos = sorted({e.repo for e in o.evidence} | set(o.affected_repos))
    data["pinned"] = {r: _sha(result, r) for r in repos}
    data["cause_class"] = cause_class(o.cause) if o.cause else None
    return data  # type: ignore[no-any-return]


def _sha(result: ScanResult, repo: str) -> str | None:
    snap = result.snapshot
    return snap.repo(repo).sha if snap.has(repo) else None


def is_incomplete(o: Outcome) -> bool:
    if o.status is Status.FAILED:
        return True
    return o.status is Status.CANNOT_EVALUATE and o.cause != Cause.NOT_CHECKABLE


def build_semantic(
    result: ScanResult,
    declarations: Declarations,
    *,
    requested: Mapping[str, str | None],
    overlay_digest: str | None = None,
) -> dict[str, Any]:
    outcomes = result.outcomes
    by_status: dict[Status, list[Outcome]] = {s: [] for s in Status}
    for o in outcomes:
        by_status[o.status].append(o)

    def rows(items: list[Outcome]) -> list[dict[str, Any]]:
        return [outcome_json(o, result) for o in items]

    findings = sorted(
        by_status[Status.INCONSISTENT],
        key=lambda o: (_SEVERITY_ORDER[o.severity or Severity.LOW], -len(o.affected_repos), o.fingerprint or ""),
    )
    collection = [o for o in by_status[Status.CANNOT_EVALUATE] if o.cause and cause_class(o.cause) == "collection"]
    insufficient = [o for o in by_status[Status.CANNOT_EVALUATE] if o not in collection]
    incomplete = [o for o in outcomes if is_incomplete(o)]
    revisions = []
    for repo_id in result.scope:
        snap = result.snapshot.repo(repo_id)
        revisions.append({
            "repo": repo_id, "slug": snap.slug, "requested": requested.get(repo_id) or "HEAD", "sha": snap.sha,
            "error": snap.failure.cause if snap.failure else None,
            "error_detail": snap.failure.detail if snap.failure else None,
        })  # fmt: skip
    referenced: dict[str, list[str]] = {}
    for o in outcomes:
        for q in o.open_questions:
            referenced.setdefault(q, []).append(f"{o.policy_id}/{o.relationship_id}/{o.subject}")
    for rel in declarations.relationships:
        if rel.open_question:
            referenced.setdefault(rel.open_question, []).append(f"declaration/{rel.id}")
    questions = [
        {
            "id": q.id,
            "status": q.status,
            "text": q.text,
            "effect": q.effect,
            "referenced_by": sorted(set(referenced.get(q.id, []))),
        }
        for q in declarations.questions
    ]
    semantic: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "repo-orchestrator", "version": __version__, "extractors": EXTRACTOR_VERSION},
        "inputs": {
            "declarations_digest": declarations.digest,
            "as_of": result.as_of.isoformat(),
            "scope": list(result.scope),
            "revisions": revisions,
            "overlay_digest": overlay_digest,
            "policies": [{"id": i, "version": v} for i, v in result.policies],
        },
        "run_status": "partial" if incomplete or any(r["error"] for r in revisions) else "complete",
        "summary": {
            "outcomes": len(outcomes),
            "by_status": {s.value: len(by_status[s]) for s in Status},
            "findings_by_severity": {s.value: sum(1 for o in findings if o.severity is s) for s in Severity},
            "incomplete": len(incomplete),
            "not_checkable_by_design": sum(1 for o in outcomes if o.cause == Cause.NOT_CHECKABLE),
        },
        "findings": rows(findings),
        "policy_clarifications": rows(by_status[Status.POLICY_CLARIFICATION]),
        "policy_conflicts": rows(by_status[Status.POLICY_CONFLICT]),
        "excepted": rows(by_status[Status.EXCEPTED]),
        "insufficient_evidence": rows(insufficient),
        "collection_failures": rows(collection),
        "evaluation_failures": rows(by_status[Status.FAILED]),
        "consistent": rows(by_status[Status.CONSISTENT]),
        "coverage": to_jsonable(result.coverage),
        "authorities": to_jsonable(result.authorities),
        "exceptions": to_jsonable(result.exceptions),
        "superseded": to_jsonable(result.superseded),
        "open_questions": questions,
    }
    semantic["result_digest"] = digest(semantic)
    return semantic


def exit_code(semantic: Mapping[str, Any]) -> int:
    """0 clean · 1 findings (evaluation complete) · 2 incomplete evaluation. 3 (configuration/runtime) is the CLI's."""
    if semantic["run_status"] == "partial":
        return 2
    return 1 if semantic["findings"] else 0


# ---------------------------------------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------------------------------------


def fence(text: str) -> str:
    return "```text\n" + text.replace("```", "'''") + "\n```"


def cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def evidence_md(e: Mapping[str, Any]) -> str:
    lines = (
        f":{e['line_start']}" + (f"-{e['line_end']}" if e["line_end"] != e["line_start"] else "")
        if e["line_start"]
        else ""
    )
    head = f"- **{e['side']}** `{e['slug']}@{e['sha'][:12]}` `{e['path']}{lines}` — {e['locator']} (sha256 `{e['content_sha256'][:12]}`, {e['extractor']})"
    return head + "\n" + "\n".join("  " + ln for ln in fence(e["quote"]).splitlines()) if e["quote"] else head


def finding_md(f: Mapping[str, Any]) -> str:
    out = [
        f"### {f['severity'].upper()} · {f['policy_id']} v{f['policy_version']} · {f['relationship_id']} · `{f['subject']}`",
        "",
    ]
    out.append(f"{f['reason']}")
    out.append("")
    out.append(
        f"- Fingerprint `{f['fingerprint']}` · impact **{f['impact']}** · intent {f['intent']} · validation `validated_by_check` · evidence `verified`"
    )
    out.append(
        "- Affected: "
        + ", ".join(f"`{r}`" for r in f["affected_repos"])
        + " · pinned: "
        + ", ".join(f"`{r}@{(s or '?')[:12]}`" for r, s in f["pinned"].items())
    )
    c = f["comparison"]
    if c:
        out.append(f"- Comparison `{c['comparator']}`: {c['left']!r} vs {c['right']!r} → **{c['result']}**")
    if f["detail"].get("attribution"):
        out.append(f"- Attribution: **{f['detail']['attribution']}**")
    if f["open_questions"]:
        out.append("- Depends on open question(s): " + ", ".join(f["open_questions"]))
    if f["fix_sites"]:
        out.append(
            "- Fix sites, in order: " + "; ".join(f"`{s['repo']}:{s['path']}` ({s['reason']})" for s in f["fix_sites"])
        )
    out.append("- Investigate: " + ", ".join(f"`{i}`" for i in f["investigate"]))
    out.append("")
    out.append("Evidence:")
    out.extend(evidence_md(e) for e in f["evidence"])
    out.append("")
    return "\n".join(out)


def brief_table(items: list[Mapping[str, Any]], with_cause: bool = False) -> list[str]:
    head = "| Policy | Relationship | Subject |" + (" Cause |" if with_cause else "") + " Reason |"
    lines = [head, "|---|---|---|" + ("---|" if with_cause else "") + "---|"]
    for o in items:
        cause = f" `{o['cause']}` |" if with_cause else ""
        lines.append(
            f"| {o['policy_id']} | {o['relationship_id']} | `{cell(o['subject'])}` |{cause} {cell(o['reason'])} |"
        )
    return lines


def render_markdown(s: Mapping[str, Any]) -> str:
    sm = s["summary"]
    out = [
        "# Cross-repository coherence report",
        "",
        f"`repo-orchestrator` {s['tool']['version']} · scope {', '.join(s['inputs']['scope'])} · as of {s['inputs']['as_of']} · "
        f"run **{s['run_status']}** · result digest `{s['result_digest'][:16]}`",
        "",
        "## Summary",
        "",
        "| Outcome | Count |",
        "|---|---|",
    ]
    out += [f"| {k} | {v} |" for k, v in sm["by_status"].items()]
    sev = sm["findings_by_severity"]
    out += ["", f"Findings by severity: high {sev['high']}, medium {sev['medium']}, low {sev['low']}. Incomplete evaluations: {sm['incomplete']}"
            f" (plus {sm['not_checkable_by_design']} claim(s) not checkable by design).", ""]  # fmt: skip
    out += ["## Findings (confirmed by a deterministic check)", ""]
    out += [finding_md(f) for f in s["findings"]] if s["findings"] else ["None.", ""]
    sections = [
        ("Policy clarifications (questions for the owner, not findings)", "policy_clarifications", False),
        ("Policy conflicts (for a human)", "policy_conflicts", False),
        ("Intentional exceptions", "excepted", False),
        ("Insufficient or undecidable evidence", "insufficient_evidence", True),
        ("Collection failures", "collection_failures", True),
        ("Evaluation failures", "evaluation_failures", True),
    ]
    for title, key, with_cause in sections:
        out += [f"## {title}", ""]
        out += ([*brief_table(s[key], with_cause), ""]) if s[key] else ["None.", ""]
    out += ["## Consistent relationships", "", *brief_table(s["consistent"]), ""]
    out += [
        "## Coverage of declared relationships",
        "",
        "| Relationship | Baseline | Type | Source → target | Status | Policies | Note |",
        "|---|---|---|---|---|---|---|",
    ]
    out += [
        f"| {c['relationship_id']} | {c['baseline_id']} | {c['type']} | {c['source']} → {c['target']} | {c['status']} | {', '.join(c['policies']) or '—'} | {cell(c['note'])} |"
        for c in s["coverage"]
    ]
    out += ["", "## Open questions", "", "| Id | Status | Effect in this run | Referenced by |", "|---|---|---|---|"]
    out += [
        f"| {q['id']} | {q['status']} | {cell(q['effect'])} | {cell(', '.join(q['referenced_by']) or 'no M1 policy')} |"
        for q in s["open_questions"]
    ]
    out += ["", "## Policy authorities", "", "| Policy | Authority | Verified | Note |", "|---|---|---|---|"]
    out += [
        f"| {a['policy_id']} | `{a['repo']}:{a['path']}` | {a['verified']} | {cell(a['note'])} |"
        for a in s["authorities"]
    ]
    if s["exceptions"]:
        out += ["", "## Exceptions", "", "| Id | Policy | State | Review by | Owner |", "|---|---|---|---|---|"]
        out += [
            f"| {e['id']} | {e['policy']} | {e['state']} | {e['review_by']} | {e['owner']} |" for e in s["exceptions"]
        ]
    out += ["", "## Pinned revisions", "", "| Repository | Requested | Commit | Error |", "|---|---|---|---|"]
    out += [
        f"| `{r['slug']}` | {r['requested'][:12]} | `{r['sha'] or '—'}` | {r['error'] or ''} |"
        for r in s["inputs"]["revisions"]
    ]
    out += [
        "",
        f"Declarations digest `{s['inputs']['declarations_digest'][:16]}`. Everything above is a function of the pinned revisions and the declarations; run metadata is in the JSON report only.",
        "",
    ]
    return "\n".join(out)
