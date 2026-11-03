"""P2 JSON contracts: producer and consumer agree on ``schema_version`` and on the declared top-level key set."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from .. import extract
from ..collector.snapshot import ArtifactRequest
from ..declarations import Declarations
from ..extract import ExtractionError, int_of_js
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

VERSION_KEY = "schema_version"

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
    acceptance: LocatorSpec | None = None  # consumers: the guard that decides which version is accepted


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
        parse_locator(raw["acceptance"], f"{where}.{name}.acceptance", repos) if raw.get("acceptance") else None,
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
    specs = [s for side in (c.first, c.second) for s in (side.version, side.keys, side.acceptance) if s is not None]
    return [ArtifactRequest(spec.repo, spec.path) for spec in specs]


def _resolve(ctx: EvalContext, side: Side, check_emitted: bool) -> tuple[object, list[Evidence]]:
    """A side's ``schema_version`` and its evidence.

    A producer's version is what its document-building object *emits*: the declared constant must be the value of
    ``schema_version`` there (an integer literal overrides it, anything else is undecidable). A consumer's version is what
    it *accepts*: the operand of its ``schema_version !== …`` guard, never the shape of the object it returns.
    """
    spec, label = side.version, side.name
    art, text = ctx.text(spec.repo, spec.path)
    try:
        ex = spec.resolve(text)
        value: object = int_of_js(str(ex.value)) if spec.kind == "js_object_value" else ex.value
        evidence = [ctx.text_ev(label, art, ex, spec.label)]
        if spec.kind == "js_const_int" and side.acceptance is not None:
            guard = side.acceptance.resolve(text)
            evidence.append(ctx.text_ev(label, art, guard, "accepted schema_version"))
            value = _connected(spec, str(guard.value), "the version it accepts", int(str(ex.value)), tuple(evidence))
        elif (
            spec.kind == "js_const_int"
            and check_emitted
            and side.keys.kind == "js_object_keys"
            and side.keys.path == spec.path
        ):
            literal = extract.js_object_value(text, side.keys.params["after"], side.keys.params["literal"], VERSION_KEY)
            lines = text.splitlines()
            line_no = next(
                (
                    n
                    for n in range(literal.line_start, literal.line_end + 1)
                    if re.search(rf"\b{VERSION_KEY}\b\s*:", lines[n - 1])
                ),
                literal.line_start,
            )
            entry = extract.Extracted(str(literal.value), lines[line_no - 1], line_no, line_no, literal.extractor)
            evidence.append(ctx.text_ev(label, art, entry, f"emitted {VERSION_KEY}"))
            value = _connected(spec, str(literal.value), "the version it emits", int(str(ex.value)), tuple(evidence))
    except ExtractionError as exc:
        raise CannotEvaluate(exc.cause, f"{art.slug}:{spec.path}: {exc.detail}") from exc
    return value, evidence


def _connected(spec: LocatorSpec, expression: str, what: str, declared: int, evidence: tuple[Evidence, ...]) -> int:
    """The declared constant (its value), an integer literal (that literal), or undecidable."""
    if expression == spec.params["name"]:
        return declared
    if re.fullmatch(r"\d+", expression):
        return int(expression)
    raise CannotEvaluate(
        Cause.LOCATOR_NO_MATCH,
        f"{spec.path}: {what} is `{expression}`, not the declared constant {spec.params['name']}",
        evidence,
    )


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
            if subject == "schema_version":
                a_val, a_ev = _resolve(ctx, c.first, check_emitted=True)
                b_val, b_ev = _resolve(ctx, c.second, check_emitted=peer)
            else:
                a_val, a_ev = _resolve(ctx, Side(c.first.name, a_spec, a_spec), check_emitted=False)
                b_val, b_ev = _resolve(ctx, Side(c.second.name, b_spec, b_spec), check_emitted=False)
        except CannotEvaluate as exc:
            out.append(ctx.cannot(rel, subject, exc, locators))
            continue
        out.append(_compare(ctx, rel, c, subject, a_val, b_val, [*a_ev, *b_ev], locators, peer))
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
