"""P2 JSON contracts: producer and consumer agree on ``schema_version`` and on the declared top-level key set."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..collector.snapshot import ArtifactRequest
from ..declarations import Declarations
from ..extract import ExtractionError, int_of_js
from ..graph import Graph
from ..locators import LocatorSpec, parse_locator
from ..models import (
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
    id="P2",
    version="1.0.0",
    title="Published JSON contracts agree on schema_version and key set",
    aspect="json_contract",
    scope=Scope(ScopeKind.RELATIONSHIP_TYPE, ("runtime_contract",)),
    impact=Impact.BREAKS,
    intent=Intent.ACCIDENTAL,
)


@dataclass(frozen=True)
class Side:
    name: str
    version: LocatorSpec
    keys: LocatorSpec


@dataclass(frozen=True)
class ContractConfig:
    document: str
    mode: str  # producer_consumer | peer
    first: Side  # producer, or peer a
    second: Side  # consumer, or peer b


@dataclass(frozen=True)
class Config:
    contracts: dict[str, ContractConfig]


def _side(raw: object, name: str, where: str, repos: set[str]) -> Side:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}.{name}: must be a mapping")
    return Side(
        name,
        parse_locator(raw.get("version"), f"{where}.{name}.version", repos),
        parse_locator(raw.get("keys"), f"{where}.{name}.keys", repos),
    )


def prepare(decl: Declarations, graph: Graph) -> Config:
    contracts: dict[str, ContractConfig] = {}
    for rel in graph.trusted():
        raw = rel.contract.get("json")
        if raw is None:
            continue
        where = f"relationships.yaml:{rel.id}.contract.json"
        mode = raw.get("mode")
        repos = {rel.source, rel.target}
        if mode == "producer_consumer":
            first, second = (
                _side(raw.get("producer"), "producer", where, repos),
                _side(raw.get("consumer"), "consumer", where, repos),
            )
        elif mode == "peer":
            first, second = _side(raw.get("a"), "a", where, repos), _side(raw.get("b"), "b", where, repos)
        else:
            raise ConfigError(f"{where}: mode must be producer_consumer or peer")
        contracts[rel.id] = ContractConfig(str(raw.get("document", "")), str(mode), first, second)
    return Config(contracts)


def applies(rel: Relationship) -> bool:
    return rel.type is RelType.RUNTIME_CONTRACT and "json" in rel.contract


def requests(cfg: Config, rel: Relationship) -> Sequence[ArtifactRequest]:
    c = cfg.contracts[rel.id]
    return [ArtifactRequest(spec.repo, spec.path) for side in (c.first, c.second) for spec in (side.version, side.keys)]


def _resolve(ctx: EvalContext, spec: LocatorSpec, side: str) -> tuple[object, Evidence]:
    art, text = ctx.text(spec.repo, spec.path)
    try:
        ex = spec.resolve(text)
        value: object = ex.value
        if spec.kind == "js_object_value":
            value = int_of_js(str(ex.value))
    except ExtractionError as exc:
        raise CannotEvaluate(exc.cause, f"{art.slug}:{spec.path}: {exc.detail}") from exc
    return value, ctx.text_ev(side, art, ex, spec.label)


def evaluate(cfg: Config, ctx: EvalContext, rel: Relationship) -> list[Outcome]:
    c = cfg.contracts[rel.id]
    peer = c.mode == "peer"
    out: list[Outcome] = []
    for subject in ("schema_version", "key_set"):
        a_spec = c.first.version if subject == "schema_version" else c.first.keys
        b_spec = c.second.version if subject == "schema_version" else c.second.keys
        locators = [
            f"{a_spec.repo}:{a_spec.path}#{c.document}.{subject}",
            f"{b_spec.repo}:{b_spec.path}#{c.document}.{subject}",
        ]
        try:
            a_val, a_ev = _resolve(ctx, a_spec, c.first.name)
            b_val, b_ev = _resolve(ctx, b_spec, c.second.name)
        except CannotEvaluate as exc:
            out.append(ctx.cannot(rel, subject, exc, locators))
            continue
        out.append(_compare(ctx, rel, c, subject, a_val, b_val, [a_ev, b_ev], locators, peer))
    return out


def _compare(
    ctx: EvalContext,
    rel: Relationship,
    c: ContractConfig,
    subject: str,
    a: object,
    b: object,
    evidence: list[Evidence],
    locators: list[str],
    peer: bool,
) -> Outcome:
    first_repo, second_repo = c.first.version.repo, c.second.version.repo
    detail: dict[str, object] = {"document": c.document, "mode": c.mode}
    if subject == "schema_version":
        comparison = Comparison("equal", a, b, "equal" if a == b else "not_equal", detail)
        ok = a == b
        reason = f"{c.document}: {c.first.name} {ctx.slug(first_repo)} has schema_version {a}, {c.second.name} {ctx.slug(second_repo)} has {b}"
    else:
        assert isinstance(a, tuple) and isinstance(b, tuple)
        if peer:
            ok = set(a) == set(b)
            comparison = Comparison("set_equal", sorted(a), sorted(b), "equal" if ok else "not_equal", detail)
            detail |= {"only_in_first": sorted(set(a) - set(b)), "only_in_second": sorted(set(b) - set(a))}
            reason = f"{c.document}: key sets {'agree' if ok else 'differ'}"
        else:
            missing = sorted(set(b) - set(a))  # keys the consumer reads that the producer does not emit
            ok = not missing
            comparison = Comparison("subset", sorted(b), sorted(a), "subset" if ok else "not_subset", detail)
            detail |= {"consumer_keys_missing_in_producer": missing, "producer_only_keys": sorted(set(a) - set(b))}
            reason = f"{c.document}: consumer reads {len(b)} keys, producer emits {len(a)}" + (
                f"; missing: {', '.join(missing)}" if missing else ""
            )
    if ok:
        return ctx.consistent(rel, subject, reason, comparison, evidence, locators, detail=detail)
    affected = [first_repo, second_repo] if peer else [second_repo]
    fix_sites = (
        [
            FixSite(first_repo, c.first.version.path, "one side must follow the other"),
            FixSite(second_repo, c.second.version.path, "one side must follow the other"),
        ]
        if peer
        else [
            FixSite(second_repo, c.second.version.path, "consumer: accept both versions first"),
            FixSite(first_repo, c.first.version.path, "producer: change after the consumer accepts both"),
        ]
    )
    return ctx.inconsistent(
        rel,
        subject,
        reason,
        comparison,
        evidence,
        locators,
        affected=affected,
        investigate=[
            f"{c.first.version.repo}:{c.first.version.path}",
            f"{c.second.version.repo}:{c.second.version.path}",
        ],
        fix_sites=fix_sites,
        detail=detail,
    )


POLICY = Policy(META, prepare, applies, requests, evaluate)
