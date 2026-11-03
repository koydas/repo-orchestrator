"""P5 Cross-repository references: links from a living document to a file or directory of another analyzed repository
resolve at the target's pinned snapshot.

Only sources declared ``living`` are evaluated. A ``historical`` source (an accepted ADR, a point-in-time record) is not an
operational contract and is skipped (baseline N4, domain-model invariant 7).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .. import extract
from ..collector.snapshot import ArtifactRequest
from ..declarations import Declarations
from ..extract import Extracted
from ..graph import Graph
from ..models import Cause, Comparison, ConfigError, FixSite, Impact, Intent, Outcome, Relationship, ScopeKind
from .base import CannotEvaluate, EvalContext, Policy, PolicyMeta, Scope

META = PolicyMeta(
    id="P5",
    version="1.0.0",
    title="Cross-repository references resolve at the pinned target revision",
    aspect="references",
    scope=Scope(ScopeKind.GLOBAL),
    impact=Impact.FALSIFIES,
    intent=Intent.ACCIDENTAL,
    enumerated_subjects=("ref:", "source:"),
)

ROLES = ("living", "historical")
SHA40 = 40


@dataclass(frozen=True)
class Source:
    repo: str
    path: str
    role: str


@dataclass(frozen=True)
class Config:
    sources: dict[str, tuple[Source, ...]]


def prepare(decl: Declarations, graph: Graph) -> Config:
    sources: dict[str, tuple[Source, ...]] = {}
    for rel in graph.trusted():
        raw = rel.contract.get("references")
        if raw is None:
            continue
        where = f"relationships.yaml:{rel.id}.contract.references"
        parsed = []
        for i, s in enumerate(raw):
            if s.get("repo") not in (rel.source, rel.target) or not isinstance(s.get("path"), str):
                raise ConfigError(f"{where}[{i}]: needs an endpoint 'repo' and a 'path'")
            if s.get("role") not in ROLES:
                raise ConfigError(f"{where}[{i}]: role must be one of {ROLES}")
            parsed.append(Source(s["repo"], s["path"], s["role"]))
        sources[rel.id] = tuple(parsed)
    return Config(sources)


def applies(rel: Relationship) -> bool:
    return "references" in rel.contract


def requests(cfg: Config, rel: Relationship) -> Sequence[ArtifactRequest]:
    return [ArtifactRequest(s.repo, s.path) for s in cfg.sources[rel.id] if s.role == "living"]


def evaluate(cfg: Config, ctx: EvalContext, rel: Relationship) -> list[Outcome]:
    out: list[Outcome] = []
    for source in cfg.sources[rel.id]:
        if source.role != "living":
            continue
        out.extend(_evaluate_source(ctx, rel, source))
    return out


def _evaluate_source(ctx: EvalContext, rel: Relationship, source: Source) -> list[Outcome]:
    target_repo = rel.source if source.repo == rel.target else rel.target
    target = ctx.graph.repositories[target_repo]
    try:
        art, text = ctx.text(source.repo, source.path)
        links = extract.github_links(text, target.slug)
        pinned = ctx.sha(target_repo)
        entries = ctx.entries(target_repo, "")
    except CannotEvaluate as exc:
        return [ctx.cannot(rel, f"source:{source.repo}:{source.path}", exc, [f"{source.repo}:{source.path}"])]
    kinds = {e.path: e.type for e in entries}
    lines = text.splitlines()
    grouped: dict[tuple[str, str], list[extract.GithubLink]] = {}
    for link in links:
        grouped.setdefault((link.kind, link.path), []).append(link)
    if not grouped:
        ex = Extracted("", "", 1, 1, "github_links@1")
        ev = ctx.text_ev("claimant", art, ex, f"no links to {target.slug} found")
        return [
            ctx.consistent(
                rel,
                f"source:{source.repo}:{source.path}",
                f"{source.path} links no file of {target.slug}",
                None,
                [ev],
                [f"{source.repo}:{source.path}"],
                detail={"references": 0},
            )
        ]
    out: list[Outcome] = []
    for (kind, path), occurrences in sorted(grouped.items()):
        first = occurrences[0]
        subject = f"ref:{kind}:{target_repo}:{path}"
        locators = [f"{source.repo}:{source.path}#ref", f"{target_repo}:{path}"]
        ex = Extracted(first.raw, lines[first.line - 1], first.line, first.line, "github_links@1")
        source_ev = ctx.text_ev("claimant", art, ex, f"link to {target.slug}/{path}")
        refs = {o.ref for o in occurrences}
        bad = sorted(r for r in refs if r not in (target.default_branch, pinned))
        if bad:
            out.append(
                ctx.cannot(
                    rel,
                    subject,
                    CannotEvaluate(
                        Cause.REF_NOT_PINNED,
                        f"link ref {bad[0]!r} is neither {target.default_branch!r} nor the pinned {pinned[:12]}; the engine reads one snapshot per repository",
                        (source_ev,),
                    ),
                    locators,
                )
            )
            continue
        found = kinds.get(path)
        wanted = "blob" if kind == "blob" else "tree"
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        siblings = sorted(
            e.path[len(parent) + 1 if parent else 0 :].split("/")[0]
            for e in entries
            if (e.path.startswith(parent + "/") if parent else True)
        )
        siblings = sorted(set(siblings))
        target_ev = ctx.listing_ev("target", target_repo, parent, siblings, f"entries next to {path}", "tree_listing@1")
        ok = found == wanted
        detail = {"lines": [o.line for o in occurrences], "found_type": found}
        comparison = Comparison("resolves", f"{target.slug}/{path}", pinned, "resolves" if ok else "dangling", detail)
        if ok:
            out.append(
                ctx.consistent(
                    rel,
                    subject,
                    f"{path} exists in {target.slug}@{pinned[:12]}",
                    comparison,
                    [source_ev, target_ev],
                    locators,
                    detail=detail,
                )
            )
        else:
            what = "does not exist" if found is None else f"is a {found}, the link expects a {wanted}"
            out.append(
                ctx.inconsistent(
                    rel,
                    subject,
                    f"{source.path} (line {first.line}) links {target.slug}/{path}, which {what} at {pinned[:12]}",
                    comparison,
                    [source_ev, target_ev],
                    locators,
                    affected=[source.repo],
                    investigate=[f"{source.repo}:{source.path}", f"{target_repo}:{parent or '/'}"],
                    fix_sites=[FixSite(source.repo, source.path, "update or remove the link")],
                    detail=detail,
                )
            )
    return out


POLICY = Policy(META, prepare, applies, requests, evaluate)
