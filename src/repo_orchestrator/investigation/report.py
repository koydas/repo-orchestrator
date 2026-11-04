"""Report side of M3: the ``investigation`` section of the semantic report, the execution metadata, the exit code and the
Markdown rendering. The section is additive (ADR-0013 §11: same ``schema_version``) and exists only for ``--investigate``;
the deterministic part of the report is untouched and its digest is kept as ``deterministic_digest`` (ADR-0015 §6)."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any

from ..models import Impact, RelType, digest, severity_for
from .budgets import BUDGET, MODEL, PRICE_TABLE_DATE, PRICES_USD_PER_MTOK, PROMPT_VERSION
from .budgets import EFFORT as EFFORT_LEVEL
from .decisions import effective
from .domain import CandState
from .store import InvestigationStore

SECTION_SCHEMA = 1
EXIT_AWAITING_HUMAN = 4  # complete, but a human decision is pending (only with --investigate)


def _quote_row(q: Mapping[str, Any]) -> dict[str, Any]:
    quote = q["quote"]
    ev = q.get("evidence") or {}
    return {
        "status": q["status"], "reason": q["reason"], "repo": quote["repo"], "sha": quote["sha"], "path": quote["path"],
        "line_start": quote["line_start"], "line_end": quote["line_end"], "text": quote["text"],
        "content_sha256": ev.get("content_sha256"),
    }  # fmt: skip


def _strip(d: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return None if d is None else {k: v for k, v in d.items() if k not in ("id", "decided_at")}


def _row(cand: Mapping[str, Any], result: Mapping[str, Any], decisions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    eff = effective(result, decisions)
    state = eff["state"]
    severity = None
    if state == CandState.CONFIRMED:
        severity = severity_for(Impact(cand["impact"]), RelType(cand["relationship_type"])).value
    return {
        "key": cand["key"], "policy_id": cand["policy_id"], "policy_version": cand["policy_version"],
        "relationship_id": cand["relationship_id"], "subject": cand["subject"], "kind": cand["kind"],
        "question": cand["question"], "relationship_type": cand["relationship_type"], "impact": cand["impact"],
        "origin_status": cand.get("origin_status"), "open_questions": cand.get("open_questions", []),
        "claim_text": cand["claim_text"],
        "deterministic_evidence": [{k: e[k] for k in ("side", "repo", "slug", "sha", "path", "locator", "quote", "content_sha256", "line_start", "line_end")} for e in cand["evidence"]],
        "state": state, "evidence_status": eff.get("evidence_status"), "validation": eff["validation"],
        "reason": eff["reason"], "cause": eff.get("cause"), "claim": eff.get("claim", ""), "finding": eff.get("finding"),
        "quotes": [_quote_row(q) for q in eff.get("quotes", [])], "values": eff.get("values", {}),
        "intent": eff.get("intent"), "entailment": eff.get("entailment"), "observations": eff.get("observations", []),
        "requires_triage": eff.get("requires_triage", False), "evidence_digest": eff["evidence_digest"],
        "severity": severity, "decision": _strip(eff["decision"]), "acknowledged": eff["acknowledged"],
        "stale_decisions": [_strip(d) for d in eff["stale_decisions"]],
    }  # fmt: skip


def _pending_row(cand: Mapping[str, Any]) -> dict[str, Any]:
    """A frozen candidate with no result yet (a run cut short): reported, never hidden, and never a pass."""
    placeholder: dict[str, Any] = {
        "state": CandState.INSUFFICIENT_EVIDENCE.value, "evidence_status": "unverified", "validation": "not_validated",
        "reason": "no result is recorded yet: the run is unfinished (resume it with 'investigations resume')",
        "cause": "not_investigated", "claim": "", "finding": None, "quotes": [], "values": {}, "intent": None,
        "entailment": None, "observations": [], "requires_triage": False, "evidence_digest": "",
    }  # fmt: skip
    return _row(cand, placeholder, [])


def section_status(rows: Sequence[Mapping[str, Any]]) -> str:
    if any(r["state"] == CandState.INSUFFICIENT_EVIDENCE for r in rows):
        return "incomplete"
    if any(r["state"] == CandState.NEEDS_VALIDATION for r in rows):
        return "awaiting_validation"
    return "complete"


def build_section(
    store: InvestigationStore,
    run: Mapping[str, Any],
    claims_info: Mapping[str, Any],
    skipped: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    run_id = run["run_id"]
    results = store.results(run_id)
    all_decisions = {c["key"]: store.decisions(c["key"]) for c in run["candidates"]}
    rows = [
        _row(c, results[c["key"]], all_decisions[c["key"]]) if c["key"] in results else _pending_row(c)
        for c in run["candidates"]
    ]
    rows.sort(key=lambda r: (r["relationship_id"], r["subject"]))
    used = sorted(
        {
            (k, d)
            for r in results.values()
            for k, d in zip(r.get("response_keys", ()), r.get("response_digests", ()), strict=False)
        }
    )
    pairs = [list(p) for p in used]
    applied = sorted(
        (r["key"], r["decision"]["action"], r["decision"]["reason"], r["decision"]["actor"], r["evidence_digest"])
        for r in rows
        if r["decision"]
    )
    by_state = {s.value: sum(1 for r in rows if r["state"] == s) for s in CandState}
    quotes = [q for r in rows for q in r["quotes"]]
    return {
        "schema": SECTION_SCHEMA,
        "inputs": {
            "run_id": run_id,
            "model": MODEL,
            "prompt_version": PROMPT_VERSION,
            "effort": EFFORT_LEVEL,
            "budget": dataclasses.asdict(BUDGET),
            "claims": dict(claims_info),
            "candidates_digest": run["config"]["candidates_digest"],
            "decisions_digest": digest(applied),
        },
        "status": section_status(rows),
        "summary": {
            "candidates": len(rows),
            "by_state": by_state,
            "quotes_verified": sum(1 for q in quotes if q["status"] == "verified"),
            "quotes_unverified": sum(1 for q in quotes if q["status"] != "verified"),
            "observations": sum(len(r["observations"]) for r in rows),
            "pending": len(run["candidates"]) - len(results),
            "awaiting_human": by_state[CandState.NEEDS_VALIDATION.value],
        },
        "candidates": rows,
        "skipped": [dict(s) for s in skipped],
        "recorded_digest": digest(pairs),
    }


def augment(semantic: Mapping[str, Any], section: Mapping[str, Any]) -> dict[str, Any]:
    """Semantic report + investigation. The deterministic part is carried over key for key; its digest is preserved."""
    base = {k: v for k, v in semantic.items() if k != "result_digest"}
    out = {**base, "deterministic_digest": semantic["result_digest"], "investigation": dict(section)}
    out["result_digest"] = digest(out)
    return out


def exit_code(semantic: Mapping[str, Any]) -> int:
    """3 failure (the CLI's) > 2 incomplete > 1 findings > 4 awaiting a human > 0 (ADR-0016 §8). Without an
    ``investigation`` section this is exactly the M1 rule."""
    base = 2 if semantic["run_status"] == "partial" else (1 if semantic["findings"] else 0)
    inv = semantic.get("investigation")
    if inv is None:
        return base
    states = inv["summary"]["by_state"]
    if base == 2 or states[CandState.INSUFFICIENT_EVIDENCE.value]:
        return 2
    if base == 1 or states[CandState.CONFIRMED.value]:
        return 1
    return EXIT_AWAITING_HUMAN if states[CandState.NEEDS_VALIDATION.value] else 0


def execution(store: InvestigationStore, run: Mapping[str, Any], mode: str, phase: str) -> dict[str, Any]:
    u = store.usage(run["run_id"])
    cost = u["cost_micro_usd"]
    in_p, out_p = PRICES_USD_PER_MTOK.get(MODEL, (0.0, 0.0))
    return {
        "mode": mode, "run_id": run["run_id"], "model": MODEL, "phase": phase,
        "usage": u, "cost_usd_estimate": round(cost / 1_000_000, 6),
        "price_table": {"model": MODEL, "usd_per_mtok_input": in_p, "usd_per_mtok_output": out_p, "date": PRICE_TABLE_DATE,
                        "note": "list prices, not an invoice; a replay reports the recorded usage and no new spend"},
        "budget": {
            "max_run_tokens": BUDGET.max_run_tokens, "tokens_used": u["input_tokens"] + u["output_tokens"],
            "max_run_cost_usd": BUDGET.max_run_cost_micro_usd / 1_000_000, "max_investigations": BUDGET.max_investigations,
        },
        "review": {"reviewed_by": run.get("reviewed_by"), "reviewed_at": run.get("reviewed_at")},
    }  # fmt: skip


# ---- Markdown ----------------------------------------------------------------------------------------------------

_TITLES = {
    CandState.CONFIRMED: "Confirmed (verified quotes and a validation)",
    CandState.NEEDS_VALIDATION: "Needs validation (verified quotes, a human decides)",
    CandState.INSUFFICIENT_EVIDENCE: "Insufficient evidence (never a pass)",
    CandState.POLICY_QUESTION: "Policy questions with a proposed intent (outcome unchanged)",
    CandState.DISMISSED: "Dismissed (listed, with the reason)",
}


def _fence(text: str) -> str:
    return "```text\n" + text.replace("```", "'''") + "\n```"


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _candidate_md(r: Mapping[str, Any]) -> list[str]:
    head = f"### {r['policy_id']} · {r['relationship_id']} · `{r['subject']}`"
    lines = [head, "", _cell(r["reason"]), ""]
    meta = [
        f"state **{r['state']}**",
        f"evidence `{r['evidence_status']}`",
        f"validation `{r['validation']}`",
        f"impact {r['impact']}",
    ]
    if r["severity"]:
        meta.append(f"severity {r['severity']}")
    if r["cause"]:
        meta.append(f"cause `{r['cause']}`")
    lines += ["- " + " · ".join(meta), f"- Candidate `{r['key']}` · evidence digest `{r['evidence_digest'][:12]}`"]
    if r["claim"]:
        lines.append(f"- Investigator's claim (a proposal): {r['claim']} (finding: {r['finding']})")
    if r["entailment"]:
        e = r["entailment"]
        lines.append(
            f"- Entailment `{e['kind']}` {e['test']} with {e['values']} → **{e['status']}**"
            + (f" ({e['detail']})" if e.get("detail") else "")
        )
    if r["intent"]:
        i = r["intent"]
        lines.append(
            f"- Proposed intent: **{i['proposal']}** — {i['justification']} (justification quote verified: {i['justification_verified']}); not applied"
        )
    if r["decision"]:
        d = r["decision"]
        lines.append(f"- Human decision: **{d['action']}** by {d['actor']} — {d['reason']}")
    for s in r["stale_decisions"]:
        lines.append(
            f"- Stale decision (evidence changed or state differs, not applied): {s['action']} by {s['actor']}"
        )
    if r["open_questions"]:
        lines.append("- Depends on open question(s): " + ", ".join(r["open_questions"]))
    for o in r["observations"]:
        lines.append(
            f"- Observation: instruction-like text in `{o['repo']}:{o['path']}`"
            + (f":{o['line']}" if o["line"] else "")
            + f" (`{o['pattern']}`), reported and not followed: {_cell(o['excerpt'])}"
        )
    lines += ["", "Deterministic evidence (pre-collected by code):"]
    for e in r["deterministic_evidence"]:
        at = f":{e['line_start']}" if e["line_start"] else ""
        lines += [
            f"- **{e['side']}** `{e['slug']}@{e['sha'][:12]}` `{e['path']}{at}`",
            *["  " + ln for ln in _fence(e["quote"]).splitlines()],
        ]
    if r["quotes"]:
        lines += ["", "Investigator quotes (checked in code against the pinned file at the cited lines):"]
        for q in r["quotes"]:
            mark = "verified" if q["status"] == "verified" else f"UNVERIFIED ({q['reason']})"
            lines += [
                f"- {mark} `{q['repo']}@{q['sha'][:12]}` `{q['path']}:{q['line_start']}-{q['line_end']}`",
                *["  " + ln for ln in _fence(q["text"]).splitlines()],
            ]
    lines.append("")
    return lines


def render_markdown(s: Mapping[str, Any]) -> str:
    """The M3 sections, appended to the deterministic Markdown report."""
    inv = s["investigation"]
    sm = inv["summary"]
    out = [
        "## Bounded investigation (M3, reasoning-assisted)", "",
        f"Policy {inv['inputs']['claims']['policy']['id']} v{inv['inputs']['claims']['policy']['version']} · model `{inv['inputs']['model']}` · "
        f"prompt `{inv['inputs']['prompt_version']}` · run `{inv['inputs']['run_id']}` · investigation **{inv['status']}** · "
        f"recorded-response digest `{inv['recorded_digest'][:16]}` · deterministic digest `{s['deterministic_digest'][:16]}`",
        "",
        "The Investigator proposes; code verifies every quote and decides. A verified quote never confirms a claim: "
        "`confirmed` needs a check or a human. Cost and tokens are in the JSON `execution` part.", "",
        "| State | Count |", "|---|---|", *[f"| {k} | {v} |" for k, v in sm["by_state"].items()],
        "", f"Quotes: {sm['quotes_verified']} verified, {sm['quotes_unverified']} unverified. Observations: {sm['observations']}.", "",
    ]  # fmt: skip
    for state in (
        CandState.CONFIRMED,
        CandState.NEEDS_VALIDATION,
        CandState.INSUFFICIENT_EVIDENCE,
        CandState.POLICY_QUESTION,
        CandState.DISMISSED,
    ):
        rows = [r for r in inv["candidates"] if r["state"] == state]
        out += [f"### {_TITLES[state]}", ""]
        if not rows:
            out += ["None.", ""]
        for r in rows:
            out += [x.replace("### ", "#### ", 1) if x.startswith("### ") else x for x in _candidate_md(r)]
    if inv["skipped"]:
        out += [
            "### Declared claims not investigated",
            "",
            "| Claim | Reason |",
            "|---|---|",
            *[f"| {k['id']} | {_cell(k['reason'])} |" for k in inv["skipped"]],
            "",
        ]
    return "\n".join(out)
