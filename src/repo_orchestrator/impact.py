"""C4 Impact analyzer: from the files a pull request changes to the relationships it can affect.

Deterministic, no I/O: the engine, the declarations and a text reader handed in by the caller are all it sees.

**Anchoring.** A relationship is *touched* when a changed path falls into its footprint on the PR repository's side:

* ``anchor``     a declared anchor (a file, or a directory: everything below it);
* ``request``    an artifact one of its policies reads (a path or a glob such as ``apps/*/service.yaml``);
* ``listing``    a path below a directory whose listing a policy reads (counted ADR directories, an adopter's skills);
* ``reference``  a file that a living document of the *other* repository links (P5): a rename or removal there dangles it;
* ``authority``  a policy, exception or repository authority artifact: changing it changes what the policy may conclude;
* ``unresolved`` the footprint could not be computed (an unreadable linking document): the relationship is treated as
  touched, so the evaluation reports what it cannot decide instead of silently skipping it.

**One hop.** Only relationships incident to the PR repository are traversed (plus those governed by a changed authority).
No transitive closure (docs/architecture.md → C4).

**Impact class** comes from the relationship type (docs/domain-model.md → Relationship types): ``runtime_contract``,
``build_dependency``, ``deployment`` and ``shared_resource`` *break*; ``documentary`` *falsifies*; ``tooling_adoption`` and
``conceptual`` *outdate*. Contracts that must agree on both sides are *bilateral*: a change on either side affects the other
endpoint. The others are *unilateral*: a change in the described / publishing / originating repository (``source``) affects
``target``; a change in ``target`` itself can only make its own claim or adoption stale (direction ``self``); a conceptual
target never outdates its origin.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from . import extract
from .collector.snapshot import glob_regex
from .engine import Engine
from .models import Impact, Outcome, Relationship, RelType, to_jsonable

TextReader = Callable[[str, str], str]  # (repo id, path) -> text; raises FootprintUnavailable

IMPACT_BY_TYPE: dict[RelType, Impact] = {
    RelType.RUNTIME_CONTRACT: Impact.BREAKS,
    RelType.BUILD_DEPENDENCY: Impact.BREAKS,
    RelType.DEPLOYMENT: Impact.BREAKS,
    RelType.SHARED_RESOURCE: Impact.BREAKS,
    RelType.TOOLING_ADOPTION: Impact.OUTDATES,
    RelType.DOCUMENTARY: Impact.FALSIFIES,
    RelType.CONCEPTUAL: Impact.OUTDATES,
}
BILATERAL = frozenset({RelType.RUNTIME_CONTRACT, RelType.BUILD_DEPENDENCY, RelType.DEPLOYMENT, RelType.SHARED_RESOURCE})


class FootprintUnavailable(Exception):
    """The document that defines a dynamic footprint could not be read."""


@dataclass(frozen=True)
class Trigger:
    kind: str  # anchor | request | listing | reference | authority | unresolved
    repo: str
    path: str  # the changed path (or the unreadable document for ``unresolved``)
    matched: str  # the anchor, request, link target or authority it fell into

    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.kind, self.repo, self.path, self.matched)


@dataclass(frozen=True)
class ImpactEntry:
    relationship_id: str
    baseline_id: str
    type: RelType
    impact: Impact
    direction: str  # bilateral | downstream | self | policy
    affected_repos: tuple[str, ...]
    triggers: tuple[Trigger, ...]
    policies: tuple[str, ...]  # evaluated policies; empty: declared, but no policy evaluates this relationship
    note: str = ""

    @property
    def evaluated(self) -> bool:
        return bool(self.policies)


@dataclass
class ImpactAssessment:
    entries: list[ImpactEntry] = field(default_factory=list)
    not_touched: list[str] = field(default_factory=list)  # incident relationships no changed path reaches

    @property
    def touched(self) -> frozenset[str]:
        return frozenset(e.relationship_id for e in self.entries)


def _under(path: str, base: str) -> bool:
    base = base.rstrip("/")
    return path == base or path.startswith(base + "/")


def _affected(rel: Relationship, pr_repo: str, policy_trigger: bool) -> tuple[str, tuple[str, ...]] | None:
    """(direction, affected repositories), or None when a change here cannot affect anything by this relationship."""
    if policy_trigger:
        return "policy", (rel.source, rel.target)
    if rel.type in BILATERAL:
        return "bilateral", (rel.target if pr_repo == rel.source else rel.source,)
    if pr_repo == rel.source:
        return "downstream", (rel.target,)
    if rel.type is RelType.CONCEPTUAL:
        return None
    return "self", (pr_repo,)


def analyze(engine: Engine, pr_repo: str, changed: Iterable[str], read_document: TextReader) -> ImpactAssessment:
    """Map the changed paths of ``pr_repo`` to relationships. ``changed`` includes previous names of renamed files."""
    paths = sorted(set(changed))
    graph = engine.graph
    out = ImpactAssessment()
    for rel in graph.trusted():
        if not graph.evaluable(rel):
            continue
        incident = pr_repo in (rel.source, rel.target)
        triggers: list[Trigger] = []
        policy_trigger = False
        policies = engine.relationship_policies(rel.id)
        if incident:
            triggers.extend(_anchor_triggers(rel, pr_repo, paths))
            triggers.extend(_request_triggers(engine, rel, pr_repo, paths))
            triggers.extend(_listing_triggers(engine, rel, pr_repo, paths))
            triggers.extend(_reference_triggers(engine, rel, pr_repo, paths, read_document))
        auth = _authority_triggers(engine, rel, pr_repo, paths)
        if auth:
            triggers.extend(auth)
            policy_trigger = True
        if not triggers:
            if incident:
                out.not_touched.append(rel.id)
            continue
        where = _affected(rel, pr_repo, policy_trigger and not any(t.kind != "authority" for t in triggers))
        if where is None:
            out.not_touched.append(rel.id)
            continue
        direction, affected = where
        unique = sorted(set(triggers), key=Trigger.sort_key)
        out.entries.append(
            ImpactEntry(
                relationship_id=rel.id,
                baseline_id=rel.baseline_id,
                type=rel.type,
                impact=IMPACT_BY_TYPE[rel.type],
                direction=direction,
                affected_repos=tuple(sorted(set(affected))),
                triggers=tuple(unique),
                policies=tuple(p.meta.id for p in policies),
                note=""
                if policies
                else "declared; no policy evaluates this relationship, so the impact is potential only",
            )
        )
    out.entries.sort(key=lambda e: e.relationship_id)
    out.not_touched.sort()
    return out


def _anchor_triggers(rel: Relationship, pr_repo: str, paths: Sequence[str]) -> list[Trigger]:
    return [
        Trigger("anchor", pr_repo, p, a.path)
        for a in rel.anchors
        if a.repo == pr_repo
        for p in paths
        if _under(p, a.path)
    ]


def _request_triggers(engine: Engine, rel: Relationship, pr_repo: str, paths: Sequence[str]) -> list[Trigger]:
    found: list[Trigger] = []
    for req in engine.relationship_requests(rel):
        if req.repo != pr_repo:
            continue
        if req.path is not None:
            found.extend(Trigger("request", pr_repo, p, req.path) for p in paths if p == req.path)
        elif req.glob is not None:
            regex = glob_regex(req.glob)
            found.extend(Trigger("request", pr_repo, p, req.glob) for p in paths if regex.match(p))
    return found


def _listing_triggers(engine: Engine, rel: Relationship, pr_repo: str, paths: Sequence[str]) -> list[Trigger]:
    """A path added, removed or renamed below a directory a policy lists (skills, counted ADR or pattern directories)."""
    return [
        Trigger("listing", pr_repo, p, directory)
        for repo, directory in engine.relationship_listings(rel)
        if repo == pr_repo
        for p in paths
        if _under(p, directory)
    ]


def _reference_triggers(
    engine: Engine, rel: Relationship, pr_repo: str, paths: Sequence[str], read_document: TextReader
) -> list[Trigger]:
    """Link targets inside the PR repository, taken from a living document of the other endpoint (P5 contracts)."""
    found: list[Trigger] = []
    other = rel.target if pr_repo == rel.source else rel.source
    for src in rel.contract.get("references") or []:
        if src.get("role") != "living" or src.get("repo") != other:
            continue  # a claimant inside the PR repository is an anchor: a changed document is already a trigger
        document = f"{src['repo']}:{src['path']}"
        try:
            text = read_document(src["repo"], src["path"])
        except FootprintUnavailable as exc:
            found.append(Trigger("unresolved", src["repo"], src["path"], f"{document} could not be read ({exc})"))
            continue
        slug = engine.graph.repositories[pr_repo].slug
        targets = sorted({link.path for link in extract.github_links(text, slug)})
        found.extend(Trigger("reference", pr_repo, p, t) for t in targets for p in paths if _under(p, t))
    return found


def _authority_triggers(engine: Engine, rel: Relationship, pr_repo: str, paths: Sequence[str]) -> list[Trigger]:
    found: list[Trigger] = []
    for policy in engine.relationship_policies(rel.id):
        for ref in engine.authority_refs(policy.meta.id):
            if ref.repo == pr_repo:
                found.extend(Trigger("authority", pr_repo, p, ref.path) for p in paths if p == ref.path)
    for exc in engine.decl.exceptions:
        if rel.id in exc.relationships and exc.authority.repo == pr_repo:
            found.extend(Trigger("authority", pr_repo, p, exc.authority.path) for p in paths if p == exc.authority.path)
    for auth in engine.decl.repository_authorities:
        if rel.id in auth.relationships and auth.authority.repo == pr_repo:
            found.extend(
                Trigger("authority", pr_repo, p, auth.authority.path) for p in paths if p == auth.authority.path
            )
    return found


# ---- changed values ------------------------------------------------------------------------------------------------


def value_changes(base: Sequence[Outcome], head: Sequence[Outcome]) -> list[dict[str, object]]:
    """Extracted values that differ between base and head, per (policy, relationship, subject)."""

    def key(o: Outcome) -> dict[str, object]:
        c = o.comparison
        return {
            "status": o.status.value,
            "left": to_jsonable(c.left) if c else None,
            "right": to_jsonable(c.right) if c else None,
            "result": c.result if c else None,
        }

    b = {o.subject_key: o for o in base if o.comparison is not None}
    h = {o.subject_key: o for o in head if o.comparison is not None}
    rows: list[dict[str, object]] = []
    for k in sorted(set(b) | set(h)):
        before = key(b[k]) if k in b else None
        after = key(h[k]) if k in h else None
        if before == after:
            continue
        rows.append({"policy_id": k[0], "relationship_id": k[1], "subject": k[2], "base": before, "head": after})
    return rows
