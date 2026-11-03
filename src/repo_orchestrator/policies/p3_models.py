"""P3 Model tags: every model tag ``ollama-chat`` hard-codes is pulled by ``gitops-homelab``.

Reads living code and manifests only; accepted ADRs are point-in-time records and never an input (baseline N4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..collector.snapshot import ArtifactRequest
from ..declarations import Declarations
from ..extract import ExtractionError
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
    id="P3",
    version="1.0.0",
    title="Model tags hard-coded by the consumer are pulled by the provider",
    aspect="model_tags",
    scope=Scope(ScopeKind.RELATIONSHIP, ("R7",)),
    impact=Impact.BREAKS,
    intent=Intent.ACCIDENTAL,
)


@dataclass(frozen=True)
class TagContract:
    consumer: tuple[LocatorSpec, ...]
    provider: LocatorSpec


@dataclass(frozen=True)
class Config:
    contracts: dict[str, TagContract]


def prepare(decl: Declarations, graph: Graph) -> Config:
    contracts: dict[str, TagContract] = {}
    for rel in graph.trusted():
        raw = rel.contract.get("model_tags")
        if raw is None:
            continue
        where = f"relationships.yaml:{rel.id}.contract.model_tags"
        repos = {rel.source, rel.target}
        consumer = tuple(
            parse_locator(x, f"{where}.consumer[{i}]", repos) for i, x in enumerate(raw.get("consumer") or [])
        )
        if not consumer or any(spec.kind != "js_const_string" for spec in consumer):
            raise ConfigError(f"{where}.consumer: one or more js_const_string locators are required")
        provider = parse_locator(raw.get("provider"), f"{where}.provider", repos)
        if provider.kind != "yaml_path":
            raise ConfigError(f"{where}.provider: a yaml_path locator is required")
        contracts[rel.id] = TagContract(consumer, provider)
    return Config(contracts)


def applies(rel: Relationship) -> bool:
    return rel.type is RelType.RUNTIME_CONTRACT and "model_tags" in rel.contract


def requests(cfg: Config, rel: Relationship) -> Sequence[ArtifactRequest]:
    c = cfg.contracts[rel.id]
    return [ArtifactRequest(c.provider.repo, c.provider.path), *(ArtifactRequest(s.repo, s.path) for s in c.consumer)]


def evaluate(cfg: Config, ctx: EvalContext, rel: Relationship) -> list[Outcome]:
    c = cfg.contracts[rel.id]
    out: list[Outcome] = []
    provider_locator = f"{c.provider.repo}:{c.provider.path}#{c.provider.params['yaml_path']}"
    provider_ev: Evidence | None = None
    provider_error: CannotEvaluate | None = None
    pulled: list[str] = []
    try:
        p_art, p_text = ctx.text(c.provider.repo, c.provider.path)
        p_ex = c.provider.resolve(p_text)
        if not isinstance(p_ex.value, list) or not all(isinstance(v, str) for v in p_ex.value):
            raise CannotEvaluate(
                Cause.PARSE_ERROR, f"{c.provider.path}: {c.provider.params['yaml_path']} is not a list of strings"
            )
        pulled = list(p_ex.value)
        provider_ev = ctx.text_ev("provider", p_art, p_ex, c.provider.label)
    except ExtractionError as exc:
        provider_error = CannotEvaluate(exc.cause, f"{c.provider.path}: {exc.detail}")
    except CannotEvaluate as exc:
        provider_error = exc
    for spec in c.consumer:
        subject = f"const:{spec.params['name']}"
        locators = [f"{spec.repo}:{spec.path}#{spec.params['name']}", provider_locator]
        try:
            if provider_error is not None:
                raise provider_error
            art, text = ctx.text(spec.repo, spec.path)
            ex = spec.resolve(text)
        except ExtractionError as exc:
            out.append(ctx.cannot(rel, subject, CannotEvaluate(exc.cause, f"{spec.path}: {exc.detail}"), locators))
            continue
        except CannotEvaluate as exc:
            out.append(ctx.cannot(rel, subject, exc, locators))
            continue
        tag = str(ex.value)
        assert provider_ev is not None  # provider_error would have been raised above
        evidence = [ctx.text_ev("consumer", art, ex, spec.label), provider_ev]
        member = tag in pulled
        comparison = Comparison("member_of", tag, pulled, "member" if member else "not_member")
        if member:
            out.append(
                ctx.consistent(
                    rel, subject, f"{spec.params['name']} = {tag!r} is pulled", comparison, evidence, locators
                )
            )
        else:
            out.append(
                ctx.inconsistent(
                    rel,
                    subject,
                    f"{spec.params['name']} = {tag!r} in {ctx.slug(spec.repo)} is not in the models pulled by {ctx.slug(c.provider.repo)}",
                    comparison,
                    evidence,
                    locators,
                    affected=[spec.repo],
                    investigate=[f"{c.provider.repo}:{c.provider.path}", f"{spec.repo}:{spec.path}"],
                    fix_sites=[
                        FixSite(c.provider.repo, c.provider.path, f"add {tag!r} to models.pull"),
                        FixSite(spec.repo, spec.path, "or revert the tag"),
                    ],
                )
            )
    return out


POLICY = Policy(META, prepare, applies, requests, evaluate)
