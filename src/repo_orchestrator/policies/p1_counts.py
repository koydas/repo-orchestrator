"""P1 Documentary counts: published repository counts match their declared counting rule.

Attribution (baseline D2): when the described repository's own README repeats the same stale number, the root cause is
upstream; the claimant is not blamed on its own.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .. import extract
from ..collector.snapshot import ArtifactRequest
from ..declarations import Declarations
from ..extract import ExtractionError
from ..graph import Graph
from ..models import (
    Cause,
    Comparison,
    ConfigError,
    Evidence,
    FixSite,
    Impact,
    Intent,
    Outcome,
    Relationship,
    RelType,
    ScopeKind,
)
from .base import CannotEvaluate, EvalContext, Policy, PolicyMeta, Scope

META = PolicyMeta(
    id="P1",
    version="1.0.0",
    title="Documentary counts match the described repositories",
    aspect="counts",
    scope=Scope(ScopeKind.REPOSITORY, ("profile",)),
    impact=Impact.FALSIFIES,
    intent=Intent.ACCIDENTAL,
)

ADR_FILE = re.compile(r"(?:ADR-)?\d{3,4}-[^/]+\.md")
ADR_INDEX_ENTRY = re.compile(r"^\s*[-*]\s+\[(?:ADR-)?\d{3,4}\b", re.MULTILINE)
RULE_PARAMS = {
    "numbered_adr_files": ("dir",),
    "pattern_dirs": ("dir",),
    "h3_under_h2": ("path", "section"),
    "tests_lower_bound": (),
}


@dataclass(frozen=True)
class Upstream:
    path: str
    line_regex: str
    value_regex: str


@dataclass(frozen=True)
class CrossCheck:
    rule: str
    path: str


@dataclass(frozen=True)
class Claim:
    id: str
    claimant_path: str
    line_regex: str
    value_regex: str
    rule: str
    params: dict[str, str]
    upstream: Upstream | None
    cross_checks: tuple[CrossCheck, ...]


@dataclass(frozen=True)
class RelConfig:
    claimant: str
    described: str
    claims: tuple[Claim, ...]


@dataclass(frozen=True)
class Config:
    rels: dict[str, RelConfig]
    provisional: dict[str, str]


@dataclass(frozen=True)
class Counted:
    value: int
    evidence: Evidence
    rule: str


def _regex(raw: Any, where: str, key: str) -> str:
    if not isinstance(raw, str):
        raise ConfigError(f"{where}: '{key}' must be a string")
    try:
        re.compile(raw)
    except re.error as exc:
        raise ConfigError(f"{where}: invalid regex in '{key}': {exc}") from exc
    return raw


def prepare(decl: Declarations, graph: Graph) -> Config:
    params = decl.policy_params.get("P1", {})
    provisional = {str(k): str(v) for k, v in (params.get("provisional_rules") or {}).items()}
    rels: dict[str, RelConfig] = {}
    for rel in graph.trusted():
        counts = rel.contract.get("counts")
        if counts is None:
            continue
        claims = []
        for i, raw in enumerate(counts.get("claims", [])):
            where = f"relationships.yaml:{rel.id}.contract.counts.claims[{i}]"
            rule = raw.get("rule")
            if rule not in RULE_PARAMS:
                raise ConfigError(f"{where}: unknown counting rule {rule!r} (allowed: {sorted(RULE_PARAMS)})")
            rule_params = {}
            for key in RULE_PARAMS[str(rule)]:
                if not isinstance(raw.get(key), str):
                    raise ConfigError(f"{where}: rule {rule} requires '{key}'")
                rule_params[key] = raw[key]
            upstream = None
            if raw.get("upstream"):
                u = raw["upstream"]
                upstream = Upstream(
                    str(u["path"]),
                    _regex(u.get("line_regex"), where, "upstream.line_regex"),
                    _regex(u.get("value_regex"), where, "upstream.value_regex"),
                )
            checks = tuple(CrossCheck(str(c["rule"]), str(c["path"])) for c in raw.get("cross_checks", []))
            for check in checks:
                if check.rule != "adr_index_entries":
                    raise ConfigError(f"{where}: unknown cross-check rule {check.rule!r}")
            claims.append(
                Claim(
                    str(raw.get("id") or ""),
                    str(raw.get("claimant_path", "README.md")),
                    _regex(raw.get("line_regex"), where, "line_regex"),
                    _regex(raw.get("value_regex"), where, "value_regex"),
                    str(rule),
                    rule_params,
                    upstream,
                    checks,
                )
            )
            if not claims[-1].id:
                raise ConfigError(f"{where}: missing 'id'")
        rels[rel.id] = RelConfig(rel.target, rel.source, tuple(claims))
    return Config(rels, provisional)


def applies(rel: Relationship) -> bool:
    return rel.type is RelType.DOCUMENTARY and "counts" in rel.contract


def requests(cfg: Config, rel: Relationship) -> Sequence[ArtifactRequest]:
    rc = cfg.rels[rel.id]
    out: list[ArtifactRequest] = []
    for c in rc.claims:
        out.append(ArtifactRequest(rc.claimant, c.claimant_path))
        if c.upstream:
            out.append(ArtifactRequest(rc.described, c.upstream.path))
        if c.rule == "h3_under_h2":
            out.append(ArtifactRequest(rc.described, c.params["path"]))
        out.extend(ArtifactRequest(rc.described, x.path) for x in c.cross_checks)
    return out


# -- counting rules ---------------------------------------------------------------------------------------


def _count_adr_files(ctx: EvalContext, repo: str, claim: Claim) -> Counted:
    directory = claim.params["dir"].strip("/")
    names = sorted(
        e.path[len(directory) + 1 :]
        for e in ctx.entries(repo, directory)
        if e.type == "blob"
        and "/" not in e.path[len(directory) + 1 :]
        and ADR_FILE.fullmatch(e.path.rsplit("/", 1)[-1])
    )
    ev = ctx.listing_ev(
        "counted", repo, directory, names, f"numbered ADR files in {directory}/", "count_numbered_adr_files@1"
    )
    return Counted(len(names), ev, claim.rule)


def _count_pattern_dirs(ctx: EvalContext, repo: str, claim: Claim) -> Counted:
    directory = claim.params["dir"].strip("/")
    names = sorted(
        {
            e.path[len(directory) + 1 :]
            for e in ctx.entries(repo, directory)
            if e.type == "tree" and "/" not in e.path[len(directory) + 1 :]
        }
    )
    ev = ctx.listing_ev("counted", repo, directory, names, f"directories directly under {directory}/", "count_dirs@1")
    return Counted(len(names), ev, claim.rule)


def _count_h3(ctx: EvalContext, repo: str, claim: Claim) -> Counted:
    art, text = ctx.text(repo, claim.params["path"])
    ex = _guard(lambda: extract.md_h3_under_h2(text, claim.params["section"]))
    ev = ctx.text_ev("counted", art, ex, f"### headings under '## {claim.params['section']}'")
    return Counted(int(ex.value), ev, claim.rule)


def _guard(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except ExtractionError as exc:
        raise CannotEvaluate(exc.cause, exc.detail) from exc


def _count_index(ctx: EvalContext, repo: str, check: CrossCheck) -> Counted:
    art, text = ctx.text(repo, check.path)
    lines = text.splitlines()
    matches = [i for i, line in enumerate(lines, start=1) if ADR_INDEX_ENTRY.match(line)]
    if not matches:
        raise CannotEvaluate(Cause.LOCATOR_NO_MATCH, f"{check.path}: no ADR index entries")
    first, last = matches[0], matches[-1]
    ex = extract.Extracted(len(matches), lines[first - 1], first, last, "count_adr_index@1")
    return Counted(len(matches), ctx.text_ev("cross_check", art, ex, f"ADR index entries in {check.path}"), check.rule)


def _count(ctx: EvalContext, repo: str, claim: Claim) -> Counted:
    if claim.rule == "numbered_adr_files":
        return _count_adr_files(ctx, repo, claim)
    if claim.rule == "pattern_dirs":
        return _count_pattern_dirs(ctx, repo, claim)
    if claim.rule == "h3_under_h2":
        return _count_h3(ctx, repo, claim)
    raise CannotEvaluate(
        Cause.NOT_CHECKABLE, "no reliable static count exists for a tests claim (coherence-model → Counting claims)"
    )


# -- evaluation -------------------------------------------------------------------------------------------


def _extract_int(text: str, line_regex: str, value_regex: str) -> extract.Extracted:
    ex = _guard(lambda: extract.md_line_value(text, line_regex, value_regex))
    return extract.Extracted(
        _guard(lambda: extract.to_int(ex.value)), ex.quote, ex.line_start, ex.line_end, ex.extractor
    )


def evaluate(cfg: Config, ctx: EvalContext, rel: Relationship) -> list[Outcome]:
    rc = cfg.rels[rel.id]
    out: list[Outcome] = []
    for claim in rc.claims:
        subject = f"claim:{claim.id}"
        locators = [f"{rc.claimant}:{claim.claimant_path}#{claim.id}", f"{rc.described}:count:{claim.id}"]
        claim_ev: Evidence | None = None
        try:
            art, text = ctx.text(rc.claimant, claim.claimant_path)
            claimed_ex = _extract_int(text, claim.line_regex, claim.value_regex)
            claim_ev = ctx.text_ev("claimant", art, claimed_ex, f"claim '{claim.id}' in {claim.claimant_path}")
            counted = _count(ctx, rc.described, claim)
        except CannotEvaluate as exc:
            out.append(
                ctx.cannot(
                    rel,
                    subject,
                    CannotEvaluate(exc.cause, exc.detail, (claim_ev,) if claim_ev else exc.evidence),
                    locators,
                )
            )
            continue
        claimed = int(claimed_ex.value)
        evidence = [claim_ev, counted.evidence]
        if claimed == counted.value:
            out.append(
                ctx.consistent(
                    rel,
                    subject,
                    f"{claimed} {claim.id} claimed, {counted.value} counted ({claim.rule})",
                    Comparison("equal", claimed, counted.value, "equal", {"rule": claim.rule}),
                    evidence,
                    locators,
                )
            )
            continue
        out.append(_mismatch(cfg, ctx, rel, rc, claim, subject, locators, claimed, counted, evidence))
    return out


def _mismatch(
    cfg: Config,
    ctx: EvalContext,
    rel: Relationship,
    rc: RelConfig,
    claim: Claim,
    subject: str,
    locators: list[str],
    claimed: int,
    counted: Counted,
    evidence: list[Evidence],
) -> Outcome:
    question = cfg.provisional.get(claim.rule)
    alternatives: dict[str, int] = {}
    notes: list[str] = []
    for check in claim.cross_checks:
        try:
            alt = _count_index(ctx, rc.described, check)
        except CannotEvaluate as exc:
            notes.append(f"cross-check {check.rule} unavailable: {exc.detail}")
            continue
        alternatives[check.rule] = alt.value
        evidence.append(alt.evidence)
    detail: dict[str, Any] = {"rule": claim.rule, "alternative_counts": alternatives, "notes": notes}
    comparison = Comparison("equal", claimed, counted.value, "not_equal", detail)
    matching = [rule for rule, value in alternatives.items() if value == claimed]
    if question and matching:
        reason = (
            f"{rc.claimant} states {claimed} {claim.id}; the declared rule '{claim.rule}' counts {counted.value} but rule "
            f"'{matching[0]}' counts {claimed}. Which rule applies is open ({question})."
        )
        return ctx.clarify(rel, subject, reason, question, evidence, locators, comparison)
    attribution = "claimant"
    fix_sites = [FixSite(rc.claimant, claim.claimant_path, f"states {claimed}, {counted.value} counted")]
    investigate = [
        f"{rc.claimant}:{claim.claimant_path}",
        f"{rc.described}:{claim.params.get('dir', claim.params.get('path', ''))}",
    ]
    if claim.upstream:
        try:
            u_art, u_text = ctx.text(rc.described, claim.upstream.path)
            u_ex = _extract_int(u_text, claim.upstream.line_regex, claim.upstream.value_regex)
            evidence.append(ctx.text_ev("upstream", u_art, u_ex, f"claim '{claim.id}' in {claim.upstream.path}"))
            detail["upstream_value"] = u_ex.value
            if u_ex.value == claimed:
                attribution = "upstream"
                fix_sites = [
                    FixSite(
                        rc.described, claim.upstream.path, f"states {claimed}, {counted.value} counted: root cause"
                    ),
                    FixSite(
                        rc.claimant,
                        claim.claimant_path,
                        f"repeats the upstream value {claimed}; update after the upstream fix",
                    ),
                ]
                investigate.insert(0, f"{rc.described}:{claim.upstream.path}")
            elif u_ex.value != counted.value:
                attribution = "both"
                fix_sites.insert(
                    0, FixSite(rc.described, claim.upstream.path, f"states {u_ex.value}, {counted.value} counted")
                )
        except CannotEvaluate as exc:
            attribution = "claimant_upstream_unreadable"
            notes.append(f"upstream README unavailable: {exc.detail}")
    detail["attribution"] = attribution
    reason = f"{ctx.slug(rc.claimant)} {claim.claimant_path} states {claimed} {claim.id}; {ctx.slug(rc.described)} has {counted.value} ({claim.rule})."
    if attribution == "upstream":
        reason += f" {ctx.slug(rc.described)} {claim.upstream.path if claim.upstream else ''} repeats {claimed}: the root cause is upstream."
    return ctx.inconsistent(
        rel,
        subject,
        reason,
        comparison,
        evidence,
        locators,
        affected=[rc.claimant, rc.described],
        investigate=investigate,
        fix_sites=fix_sites,
        open_questions=(question,) if question else (),
        detail=detail,
    )


def listings(cfg: Config, rel: Relationship) -> Sequence[tuple[str, str]]:
    """The directories whose entries are counted (``numbered_adr_files``, ``pattern_dirs``)."""
    rc = cfg.rels[rel.id]
    return [
        (rc.described, c.params["dir"].strip("/"))
        for c in rc.claims
        if c.rule in ("numbered_adr_files", "pattern_dirs")
    ]


POLICY = Policy(META, prepare, applies, requests, evaluate, listings)
