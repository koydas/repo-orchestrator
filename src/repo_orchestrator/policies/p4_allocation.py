"""P4 Shared resource allocation: MetalLB addresses claimed in any analyzed repository are unique and inside the pool."""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from dataclasses import dataclass

from ..collector.snapshot import ArtifactRequest
from ..declarations import Declarations
from ..extract import ExtractionError, yaml_docs
from ..graph import Graph
from ..locators import LocatorSpec, parse_locator
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
    id="P4",
    version="1.0.0",
    title="Shared address claims are unique and inside the declared pool",
    aspect="address_allocation",
    scope=Scope(ScopeKind.RELATIONSHIP_TYPE, ("shared_resource",)),
    impact=Impact.BREAKS,
    intent=Intent.ACCIDENTAL,
)

IPv4 = ipaddress.IPv4Address

# Other ways to claim an address. Not understood in M1, so a site using one is never declared safe (docs/m1/limitations.md).
UNSUPPORTED_CLAIM_KEYS = ("loadBalancerIP", "metallb.universe.tf/loadBalancerIPs")


@dataclass(frozen=True)
class ClaimSource:
    repo: str
    glob: str


@dataclass(frozen=True)
class Allocation:
    pool: LocatorSpec
    annotation: str
    sources: tuple[ClaimSource, ...]


@dataclass(frozen=True)
class Config:
    allocations: dict[str, Allocation]


@dataclass(frozen=True)
class Claim:
    address: IPv4
    repo: str
    path: str
    evidence: Evidence


def prepare(decl: Declarations, graph: Graph) -> Config:
    allocations: dict[str, Allocation] = {}
    for rel in graph.trusted():
        raw = rel.contract.get("allocation")
        if raw is None:
            continue
        where = f"relationships.yaml:{rel.id}.contract.allocation"
        repos = {rel.source, rel.target}
        pool = parse_locator({"kind": "yaml_path", **raw.get("pool", {})}, f"{where}.pool", repos)
        sources = []
        for i, s in enumerate(raw.get("claim_sources") or []):
            if s.get("repo") not in repos or not isinstance(s.get("glob"), str):
                raise ConfigError(f"{where}.claim_sources[{i}]: needs an endpoint 'repo' and a 'glob'")
            sources.append(ClaimSource(s["repo"], s["glob"]))
        if not sources or not isinstance(raw.get("annotation"), str):
            raise ConfigError(f"{where}: 'annotation' and at least one claim source are required")
        allocations[rel.id] = Allocation(pool, raw["annotation"], tuple(sources))
    return Config(allocations)


def applies(rel: Relationship) -> bool:
    return rel.type is RelType.SHARED_RESOURCE and "allocation" in rel.contract


def requests(cfg: Config, rel: Relationship) -> Sequence[ArtifactRequest]:
    a = cfg.allocations[rel.id]
    return [ArtifactRequest(a.pool.repo, a.pool.path), *(ArtifactRequest(s.repo, glob=s.glob) for s in a.sources)]


def _parse_pool(values: object) -> list[tuple[IPv4, IPv4]]:
    if not isinstance(values, list):
        raise CannotEvaluate(Cause.PARSE_ERROR, "pool addresses are not a list")
    ranges: list[tuple[IPv4, IPv4]] = []
    for item in values:
        try:
            text = str(item).strip()
            if "-" in text:
                lo, hi = (IPv4(p.strip()) for p in text.split("-", 1))
            elif "/" in text:
                net = ipaddress.IPv4Network(text, strict=False)
                lo, hi = net[0], net[-1]
            else:
                lo = hi = IPv4(text)
        except ValueError as exc:
            raise CannotEvaluate(Cause.UNSUPPORTED_SYNTAX, f"unsupported pool entry {item!r}") from exc
        if lo > hi:
            raise CannotEvaluate(Cause.PARSE_ERROR, f"pool range {item!r} is reversed")
        ranges.append((lo, hi))
    return ranges


def _site_claims(ctx: EvalContext, repo: str, path: str, annotation: str) -> list[Claim]:
    art, text = ctx.text(repo, path)
    try:
        docs = yaml_docs(text)
    except ExtractionError as exc:
        raise CannotEvaluate(exc.cause, f"{art.slug}:{path}: {exc.detail}") from exc
    claims: list[Claim] = []
    for doc in docs:
        for other in UNSUPPORTED_CLAIM_KEYS:
            for value, _, start, _ in doc.find_keys(other):
                if value not in (None, ""):  # an empty value claims nothing
                    raise CannotEvaluate(
                        Cause.UNSUPPORTED_SYNTAX, f"{path}:{start}: '{other}' is an address claim form M1 does not read"
                    )
        for value, quote, start, end in doc.find_keys(annotation):
            if not isinstance(value, str):
                raise CannotEvaluate(Cause.UNSUPPORTED_SYNTAX, f"{path}:{start}: {annotation} is not a string")
            for part in (p.strip() for p in value.split(",")):
                try:
                    address = IPv4(part)
                except ValueError as exc:
                    raise CannotEvaluate(
                        Cause.UNSUPPORTED_SYNTAX, f"{path}:{start}: unsupported address {part!r}"
                    ) from exc
                from ..extract import Extracted

                claims.append(
                    Claim(
                        address,
                        repo,
                        path,
                        ctx.text_ev("claim", art, Extracted(part, quote, start, end, "yaml_annotation@1"), annotation),
                    )
                )
    return claims


def evaluate(cfg: Config, ctx: EvalContext, rel: Relationship) -> list[Outcome]:
    alloc = cfg.allocations[rel.id]
    out: list[Outcome] = []
    pool_locator = f"{alloc.pool.repo}:{alloc.pool.path}#{alloc.pool.params['yaml_path']}"
    try:
        p_art, p_text = ctx.text(alloc.pool.repo, alloc.pool.path)
        p_ex = alloc.pool.resolve(p_text)
        ranges = _parse_pool(p_ex.value)
        pool_ev = ctx.text_ev("pool", p_art, p_ex, alloc.pool.label)
    except ExtractionError as exc:
        return [ctx.cannot(rel, "pool", CannotEvaluate(exc.cause, f"{alloc.pool.path}: {exc.detail}"), [pool_locator])]
    except CannotEvaluate as exc:
        return [ctx.cannot(rel, "pool", exc, [pool_locator])]

    claims: list[Claim] = []
    failures: list[Outcome] = []
    for source in alloc.sources:
        try:
            paths = ctx.glob(source.repo, source.glob)
        except CannotEvaluate as exc:
            failures.append(
                ctx.cannot(rel, f"claims:{source.repo}:{source.glob}", exc, [f"{source.repo}:{source.glob}"])
            )
            continue
        for path in paths:
            try:
                claims.extend(_site_claims(ctx, source.repo, path, alloc.annotation))
            except CannotEvaluate as exc:
                failures.append(ctx.cannot(rel, f"claim-site:{source.repo}:{path}", exc, [f"{source.repo}:{path}"]))
    out.extend(failures)

    by_address: dict[IPv4, list[Claim]] = {}
    for claim in claims:
        by_address.setdefault(claim.address, []).append(claim)
    pool_text = [f"{lo}-{hi}" for lo, hi in ranges]
    for address in sorted(by_address):
        sites = by_address[address]
        subject = f"address:{address}"
        locators = [pool_locator, *sorted(f"{c.repo}:{c.path}#{alloc.annotation}" for c in sites)]
        evidence = [pool_ev, *(c.evidence for c in sites)]
        inside = any(lo <= address <= hi for lo, hi in ranges)
        distinct_sites = sorted({(c.repo, c.path) for c in sites})
        problems = []
        if not inside:
            problems.append(f"{address} is outside the pool {', '.join(pool_text)}")
        if len(sites) > 1:
            problems.append(
                f"{address} is claimed {len(sites)} times: "
                + ", ".join(f"{ctx.slug(r)}:{p}" for r, p in distinct_sites)
            )
        detail = {"claimed_by": [{"repo": r, "path": p} for r, p in distinct_sites], "pool": pool_text}
        if problems:
            result = "duplicate" if len(sites) > 1 else "out_of_pool"
            out.append(
                ctx.inconsistent(
                    rel,
                    subject,
                    "; ".join(problems),
                    Comparison("unique_within_range", str(address), pool_text, result, detail),
                    evidence,
                    locators,
                    affected=sorted({c.repo for c in sites}),
                    investigate=[f"{r}:{p}" for r, p in distinct_sites] + [f"{alloc.pool.repo}:{alloc.pool.path}"],
                    fix_sites=[FixSite(r, p, "claims " + str(address)) for r, p in distinct_sites],
                    detail=detail,
                )
            )
        elif failures:
            out.append(
                ctx.cannot(
                    rel,
                    subject,
                    CannotEvaluate(
                        failures[0].cause or Cause.INTERNAL_ERROR,
                        f"{address} looks unique and inside the pool, but a claim source could not be read ({failures[0].subject})",
                        tuple(evidence),
                    ),
                    locators,
                )
            )
        else:
            out.append(
                ctx.consistent(
                    rel,
                    subject,
                    f"{address} is inside the pool and claimed once",
                    Comparison("unique_within_range", str(address), pool_text, "unique", detail),
                    evidence,
                    locators,
                )
            )
    if not claims and not failures:
        out.append(
            ctx.consistent(
                rel, "claims", "no address claims found in the declared sources", None, [pool_ev], [pool_locator]
            )
        )
    return out


POLICY = Policy(META, prepare, applies, requests, evaluate)
