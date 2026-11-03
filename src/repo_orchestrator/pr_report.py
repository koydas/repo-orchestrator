"""C7 report assembly for a pull-request analysis (W2). Same two-part contract as the M1 report (ADR-0011 §4, ADR-0012):
the *semantic* part is a pure function of the inputs (declarations, pull request head/base/files, pinned SHAs, as-of date,
policy and extractor versions) and ends with ``result_digest``; volatile metadata (run id, timestamps, PR state, request
counts) lives in the ``execution`` part and is never hashed."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from . import __version__
from .collector.pr import PullRequest
from .delta import Delta, DeltaLabel
from .engine import Engine, RunOptions, ScanResult
from .extract import EXTRACTOR_VERSION
from .impact import ImpactAssessment
from .models import PR_RUN_KIND, Cause, Outcome, Severity, Status, digest, to_jsonable
from .report import brief_table, cell, finding_md, is_incomplete, outcome_json

if TYPE_CHECKING:
    from .pr_runner import Problem

PR_SCHEMA_VERSION = 1
KIND = PR_RUN_KIND
_SEVERITY_ORDER = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}

GATE_RULE = (
    "exit 0: evaluation complete, no introduced finding · exit 1: complete, at least one introduced finding · "
    "exit 2: incomplete (missing PR, base, head, file list or evidence; undetermined delta): never a pass · "
    "exit 3: configuration or runtime failure. Pre-existing findings never fail a PR gate by themselves."
)


def pr_exit_code(semantic: Mapping[str, Any]) -> int:
    """0 no introduced finding · 1 introduced finding(s) · 2 incomplete (takes precedence). 3 is the CLI's."""
    if semantic["run_status"] == "partial":
        return 2
    return 1 if semantic["findings"]["introduced"] else 0


def _delta_row(d: Delta, base: ScanResult | None, head: ScanResult | None) -> dict[str, Any]:
    outcome = d.outcome
    result = head if d.head is outcome else base
    assert result is not None
    row = outcome_json(outcome, result)
    row["delta"] = {
        "label": d.label.value,
        "reason": d.reason,
        "base_status": d.base.status.value if d.base else None,
        "head_status": d.head.status.value if d.head else None,
    }
    return row


def _sort_key(row: Mapping[str, Any]) -> tuple[int, int, str, str, str]:
    sev = Severity(row["severity"]) if row.get("severity") else Severity.LOW
    return (_SEVERITY_ORDER[sev], -len(row["affected_repos"]), row["relationship_id"], row["policy_id"], row["subject"])


def build_pr_semantic(
    *,
    engine: Engine,
    options: RunOptions,
    pr_repo: str,
    slug: str,
    number: int,
    pr: PullRequest | None,
    pins: Mapping[str, str | None],
    files: Sequence[Any],
    changed: Sequence[str],
    problems: Sequence[Problem],
    assessment: ImpactAssessment,
    base: ScanResult | None,
    head: ScanResult | None,
    deltas: Sequence[Delta],
    values: Sequence[Mapping[str, object]],
) -> dict[str, Any]:
    groups: dict[DeltaLabel, list[dict[str, Any]]] = {label: [] for label in DeltaLabel}
    for d in deltas:
        groups[d.label].append(_delta_row(d, base, head))
    for rows in groups.values():
        rows.sort(key=_sort_key)

    head_outcomes: list[Outcome] = head.outcomes if head else []
    base_outcomes: list[Outcome] = base.outcomes if base else []
    unevaluated = [o for o in head_outcomes if is_incomplete(o)]
    base_unevaluated = [o for o in base_outcomes if is_incomplete(o)]
    clarifications = [o for o in head_outcomes if o.status is Status.POLICY_CLARIFICATION]
    undetermined = groups[DeltaLabel.UNDETERMINED]

    reasons = [f"{p.side}: {p.cause}" for p in problems]
    if unevaluated:
        reasons.append(f"head: {len(unevaluated)} outcome(s) could not be evaluated")
    if undetermined:
        reasons.append(f"{len(undetermined)} undetermined delta(s)")
    run_status = "partial" if reasons else "complete"

    by_delta = {
        entry.relationship_id: [
            {"label": d.label.value, "policy_id": d.policy_id, "subject": d.subject, "fingerprint": d.fingerprint}
            for d in deltas
            if d.relationship_id == entry.relationship_id
        ]
        for entry in assessment.entries
    }
    impact = []
    for entry in assessment.entries:
        row = to_jsonable(entry)
        row["evaluated"] = entry.evaluated
        row["deltas"] = by_delta[entry.relationship_id]
        impact.append(row)

    revisions: list[dict[str, Any]] = []
    if pr is not None:
        for role, sha in (("pr_base", pr.base_sha), ("pr_head", pr.head_sha)):
            revisions.append(
                {
                    "repo": pr_repo,
                    "slug": slug,
                    "role": role,
                    "requested": sha,
                    "sha": sha,
                    "error": None,
                    "error_detail": None,
                }
            )
    if head is not None:
        for repo_id in head.scope:
            if repo_id == pr_repo:
                continue
            snap = head.snapshot.repo(repo_id)
            revisions.append({
                "repo": repo_id, "slug": snap.slug, "role": "pinned", "requested": options.revisions.get(repo_id) or "HEAD",
                "sha": snap.sha, "error": snap.failure.cause if snap.failure else None,
                "error_detail": snap.failure.detail if snap.failure else None,
            })  # fmt: skip

    counts = {label.value: len(groups[label]) for label in DeltaLabel}
    gate_result = "incomplete" if run_status == "partial" else ("fail" if counts["introduced"] else "pass")
    semantic: dict[str, Any] = {
        "schema_version": PR_SCHEMA_VERSION,
        "kind": KIND,
        "tool": {"name": "repo-orchestrator", "version": __version__, "extractors": EXTRACTOR_VERSION},
        "inputs": {
            "declarations_digest": engine.decl.digest,
            "as_of": options.as_of.isoformat(),
            "pull_request": {
                "repo": pr_repo,
                "slug": slug,
                "number": number,
                "base_sha": pr.base_sha if pr else None,
                "head_sha": pr.head_sha if pr else None,
                "head_slug": pr.head_slug if pr else None,
                "changed_files_declared": pr.changed_files if pr else None,
                "files_listed": len(pr.files) if pr else None,
                "files_digest": pr.files_digest if pr else None,
            },
            "revisions": revisions,
            "policies": [{"id": p.meta.id, "version": p.meta.version} for p in engine.policies.values()],
        },
        "run_status": run_status,
        "gate": {
            "result": gate_result,
            "exit_code": 2 if run_status == "partial" else (1 if counts["introduced"] else 0),
            "introduced": counts["introduced"],
            "undetermined": counts["undetermined"],
            "incomplete_reasons": reasons,
            "rule": GATE_RULE,
        },
        "summary": {
            "changed_paths": len(changed),
            "relationships_touched": len(assessment.entries),
            "relationships_not_touched": len(assessment.not_touched),
            "affected_repositories": sorted({r for e in assessment.entries for r in e.affected_repos}),
            "deltas": counts,
            "head_unevaluated": len(unevaluated),
            "not_checkable_by_design": sum(1 for o in head_outcomes if o.cause == Cause.NOT_CHECKABLE),
        },
        "changed_files": [
            {"path": f.path, "status": f.status, "previous_path": f.previous_path}
            for f in sorted(files, key=lambda f: (f.path, f.status))
        ],
        "impact": impact,
        "value_changes": list(values),
        "findings": {
            "introduced": groups[DeltaLabel.INTRODUCED],
            "undetermined": undetermined,
            "pre_existing": groups[DeltaLabel.PRE_EXISTING],
            "resolved": groups[DeltaLabel.RESOLVED],
        },
        "problems": [{"side": p.side, "cause": p.cause, "detail": p.detail} for p in problems],
        "unevaluated": [outcome_json(o, head) for o in unevaluated] if head else [],
        "base_unevaluated": [outcome_json(o, base) for o in base_unevaluated] if base else [],
        "policy_clarifications": [outcome_json(o, head) for o in clarifications] if head else [],
        "not_touched": list(assessment.not_touched),
    }
    semantic["result_digest"] = digest(semantic)
    return semantic


# ---------------------------------------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------------------------------------

_LABEL_TITLES = {
    "introduced": "Introduced by this pull request (fails the gate)",
    "undetermined": "Undetermined (evidence missing on a side; the run is incomplete)",
    "pre_existing": "Pre-existing (present on base and head; not blamed on this pull request, never fails the gate)",
    "resolved": "Resolved by this pull request",
}


def _scalar(v: Any) -> str:
    return repr(v) if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def _value(x: Mapping[str, Any] | None) -> str:
    return "—" if x is None else cell(f"{x['status']}: {_scalar(x['left'])} vs {_scalar(x['right'])}")


def _delta_md(row: Mapping[str, Any]) -> str:
    d = row["delta"]
    line = f"- Delta **{d['label']}**: {d['reason']} (base {d['base_status']}, head {d['head_status']})"
    lines = finding_md(row).splitlines()
    at = next(i for i, ln in enumerate(lines) if ln.startswith("- Fingerprint")) + 1
    return "\n".join([*lines[:at], line, *lines[at:]])


def render_pr_markdown(s: Mapping[str, Any]) -> str:
    pr = s["inputs"]["pull_request"]
    gate, sm = s["gate"], s["summary"]
    base = (pr["base_sha"] or "unavailable")[:12]
    head = (pr["head_sha"] or "unavailable")[:12]
    out = [
        f"# Pull request impact: `{pr['slug']}#{pr['number']}`",
        "",
        f"`repo-orchestrator` {s['tool']['version']} · base `{base}` → head `{head}` · as of {s['inputs']['as_of']} · "
        f"run **{s['run_status']}** · gate **{gate['result']}** (exit {gate['exit_code']}) · result digest `{s['result_digest'][:16]}`",
        "",
        f"> Gate rule: {gate['rule']}",
        "",
    ]
    if gate["incomplete_reasons"]:
        out += ["Incomplete because: " + "; ".join(gate["incomplete_reasons"]) + ".", ""]
    out += ["## Summary", "", "| Item | Count |", "|---|---|"]
    out += [
        f"| changed paths | {sm['changed_paths']} |",
        f"| relationships touched | {sm['relationships_touched']} |",
        f"| incident relationships not touched | {sm['relationships_not_touched']} |",
        *[f"| {k} | {v} |" for k, v in sm["deltas"].items()],
        f"| unevaluated on head | {sm['head_unevaluated']} |",
        "",
        "Affected repositories: " + (", ".join(f"`{r}`" for r in sm["affected_repositories"]) or "none") + ".",
        "",
    ]
    for key in ("introduced", "undetermined", "pre_existing", "resolved"):
        rows = s["findings"][key]
        out += [f"## {_LABEL_TITLES[key]}", ""]
        out += [_delta_md(r) for r in rows] if rows else ["None.", ""]
    out += ["## Impact: relationships this pull request touches", ""]
    if s["impact"]:
        out += [
            "| Relationship | Type | Impact | Affected | Direction | Triggers | Policies | Deltas |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for e in s["impact"]:
            triggers = "; ".join(f"{t['kind']} `{t['path']}` ({cell(t['matched'])})" for t in e["triggers"][:4])
            more = f" (+{len(e['triggers']) - 4})" if len(e["triggers"]) > 4 else ""
            deltas = ", ".join(f"{d['label']} `{d['subject']}`" for d in e["deltas"]) or "no finding"
            pol = ", ".join(e["policies"]) or "none (potential only)"
            out.append(
                f"| {e['relationship_id']} | {e['type']} | **{e['impact']}** | {', '.join(f'`{r}`' for r in e['affected_repos'])} | "
                f"{e['direction']} | {triggers}{more} | {pol} | {deltas} |"
            )
        out.append("")
    else:
        out += ["No declared relationship is touched.", ""]
    out += ["## Changed values (base → head)", ""]
    if s["value_changes"]:
        out += ["| Policy | Relationship | Subject | Base | Head |", "|---|---|---|---|---|"]
        for v in s["value_changes"]:
            out.append(
                f"| {v['policy_id']} | {v['relationship_id']} | `{cell(v['subject'])}` | {_value(v['base'])} | {_value(v['head'])} |"
            )
        out.append("")
    else:
        out += ["None.", ""]
    for title, key in (("Problems", "problems"),):
        out += [f"## {title}", ""]
        if s[key]:
            out += [
                "| Side | Cause | Detail |",
                "|---|---|---|",
                *[f"| {p['side']} | `{p['cause']}` | {cell(p['detail'])} |" for p in s[key]],
            ]
        else:
            out.append("None.")
        out.append("")
    out += ["## Unevaluated on head", ""]
    out += [*brief_table(s["unevaluated"], True), ""] if s["unevaluated"] else ["None.", ""]
    if s["policy_clarifications"]:
        out += [
            "## Policy clarifications on head (questions, not findings)",
            "",
            *brief_table(s["policy_clarifications"]),
            "",
        ]
    out += ["## Changed files", "", f"{sm['changed_paths']} path(s) (renames count both names); first 40:", ""]
    out += [
        f"- `{f['path']}` ({f['status']}{', was `' + f['previous_path'] + '`' if f['previous_path'] else ''})"
        for f in s["changed_files"][:40]
    ]
    out += [
        "",
        "## Incident relationships not touched",
        "",
        ", ".join(f"`{r}`" for r in s["not_touched"]) or "None.",
        "",
    ]
    out += ["## Pinned revisions", "", "| Repository | Role | Commit | Error |", "|---|---|---|---|"]
    out += [
        f"| `{r['slug']}` | {r['role']} | `{r['sha'] or '—'}` | {r['error'] or ''} |" for r in s["inputs"]["revisions"]
    ]
    out += [
        "",
        f"Declarations digest `{s['inputs']['declarations_digest'][:16]}`; changed-file list digest `{(pr['files_digest'] or '—')[:16]}`. "
        "Everything above is a function of the pull request's head and base, the pinned revisions and the declarations; run metadata is in the JSON report only.",
        "",
    ]
    return "\n".join(out)
