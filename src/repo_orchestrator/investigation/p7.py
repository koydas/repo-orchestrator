"""P7 (M3): qualitative claims whose truth code cannot compute, plus the intent reviews of undetermined outcomes.

A ``reasoning_assisted`` policy (ADR-0004 §1, docs/coherence-model.md): a deterministic **pre-filter** locates the claim
and the statements it must agree with in the pinned snapshots and produces a :class:`Candidate`; the Investigator only
sees what the pre-filter selected. Declared like P1-P6 (parameters in ``declarations/investigation.yaml``, versioned);
the checks that decide a candidate are in ``verify.py``. No declaration, no candidate.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..collector.snapshot import Missing
from ..declarations import field_of, read_yaml
from ..engine import Engine
from ..models import Cause, ConfigError, Evidence, Impact, Outcome, RelType, Status, digest
from .domain import KIND_CLAIM, KIND_INTENT, OPS, Candidate, Entailment, Operand, candidate_key
from .reader import PinnedReader

POLICY_ID = "P7"
DEFAULT_IMPACT = Impact.FALSIFIES
_BREAKING_TYPES = {RelType.RUNTIME_CONTRACT, RelType.BUILD_DEPENDENCY, RelType.DEPLOYMENT, RelType.SHARED_RESOURCE}
EXTRACTOR = "p7_locator@1"


@dataclass(frozen=True)
class Locator:
    repo: str
    path: str
    line_regex: str
    phrase_regex: str | None = None


@dataclass(frozen=True)
class Claim:
    id: str
    baseline: str
    relationship: str
    subject: str
    question: str
    claimant: Locator
    upstream: tuple[Locator, ...]
    impact: Impact
    entailment: Entailment | None


@dataclass(frozen=True)
class IntentReview:
    id: str
    baseline: str
    policy: str
    relationships: tuple[str, ...]
    statuses: tuple[Status, ...]
    question: str


@dataclass(frozen=True)
class Claims:
    version: str
    claims: tuple[Claim, ...]
    intent_reviews: tuple[IntentReview, ...]
    digest: str


def _regex(v: Any, where: str) -> str:
    if not isinstance(v, str) or not v:
        raise ConfigError(f"{where}: expected a regular expression")
    try:
        re.compile(v)
    except re.error as exc:
        raise ConfigError(f"{where}: invalid regex ({exc})") from exc
    return v


def _locator(raw: Any, where: str, repos: set[str], phrase: bool) -> Locator:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: must be a mapping")
    repo, path = field_of(raw, "repo", str, where), field_of(raw, "path", str, where)
    if repo not in repos:
        raise ConfigError(f"{where}: unknown repository {repo!r}")
    if path.startswith("/") or ".." in path.split("/"):
        raise ConfigError(f"{where}: invalid path")
    allowed = {"repo", "path", "line_regex"} | ({"phrase_regex"} if phrase else set())
    if set(raw) - allowed:
        raise ConfigError(f"{where}: unknown field(s) {sorted(set(raw) - allowed)}")
    return Locator(
        repo, path, _regex(raw.get("line_regex"), f"{where}.line_regex"),
        _regex(raw["phrase_regex"], f"{where}.phrase_regex") if phrase and "phrase_regex" in raw else None,
    )  # fmt: skip


def _operand(raw: Any, where: str, n_upstream: int) -> Operand:
    if (
        not isinstance(raw, dict)
        or not {"name", "source", "regex"} <= set(raw)
        or set(raw) - {"name", "source", "regex", "index"}
    ):
        raise ConfigError(f"{where}: an operand is {{name, source: claimant|upstream, regex, index?}}")
    name, source, index = raw["name"], raw["source"], raw.get("index", 0)
    if not isinstance(name, str) or not re.fullmatch(r"[a-z_]{1,24}", name) or source not in ("claimant", "upstream"):
        raise ConfigError(f"{where}: operand name is [a-z_]{{1,24}} and source is claimant or upstream")
    if (
        not isinstance(index, int)
        or isinstance(index, bool)
        or index < 0
        or (source == "claimant" and index)
        or (source == "upstream" and index >= n_upstream)
    ):
        raise ConfigError(f"{where}: operand {name}: index {index!r} does not name a declared evidence line")
    regex = _regex(raw["regex"], f"{where}.{name}.regex")
    if re.compile(regex).groups != 1:
        raise ConfigError(f"{where}: operand {name}: the pattern needs exactly one capture group (the number)")
    return Operand(name, source, regex, index)


def _entailment(ent: Any, where: str, n_upstream: int) -> Entailment | None:
    if ent is None:
        return None
    if (
        not isinstance(ent, dict)
        or set(ent) != {"kind", "left", "op", "right"}
        or ent["kind"] != "numeric_relation"
        or ent["op"] not in OPS
    ):
        raise ConfigError(
            f"{where}: entailment must be {{kind: numeric_relation, left: <operand>, op: {'|'.join(OPS)}, right: <operand>}}"
        )
    left, right = (
        _operand(ent["left"], f"{where}.entailment.left", n_upstream),
        _operand(ent["right"], f"{where}.entailment.right", n_upstream),
    )
    if left.name == right.name:
        raise ConfigError(f"{where}: the two operands need distinct names")
    return Entailment(str(ent["kind"]), left, str(ent["op"]), right)


def load_claims(directory: Path, engine: Engine) -> Claims:
    """Read ``investigation.yaml``; every reference is validated against the loaded declarations (ConfigError → exit 3)."""
    path = directory / "investigation.yaml"
    if not path.exists():
        return Claims("", (), (), digest({}))
    doc = read_yaml(path)
    repos = set(engine.graph.repositories)
    rels = {r.id: r for r in engine.graph.trusted()}
    policy = field_of(doc, "policy", dict, "investigation.yaml")
    if policy.get("id") != POLICY_ID or not isinstance(policy.get("version"), str):
        raise ConfigError(f"investigation.yaml: policy must be {{id: {POLICY_ID}, version: <string>}}")
    claims: list[Claim] = []
    for i, raw in enumerate(doc.get("claims") or []):
        where = f"investigation.yaml:claims[{i}]"
        if not isinstance(raw, dict):
            raise ConfigError(f"{where}: must be a mapping")
        cid = field_of(raw, "id", str, where)
        where = f"investigation.yaml:{cid}"
        known = {
            "id",
            "baseline",
            "relationship",
            "subject",
            "question",
            "claimant",
            "upstream",
            "impact",
            "entailment",
        }
        if set(raw) - known:
            raise ConfigError(f"{where}: unknown field(s) {sorted(set(raw) - known)}")
        rid = field_of(raw, "relationship", str, where)
        if rid not in rels:
            raise ConfigError(f"{where}: unknown or untrusted relationship {rid!r}")
        try:
            impact = Impact(str(raw.get("impact", DEFAULT_IMPACT.value)))
        except ValueError as exc:
            raise ConfigError(f"{where}: {exc}") from exc
        if impact is Impact.BREAKS and rels[rid].type not in _BREAKING_TYPES:
            raise ConfigError(
                f"{where}: a 'breaks' claim needs a runtime, build, deployment or shared-resource relationship"
            )
        entailment = _entailment(raw.get("entailment"), where, len(raw.get("upstream") or []))
        upstream = tuple(
            _locator(u, f"{where}.upstream[{j}]", repos, False) for j, u in enumerate(raw.get("upstream") or [])
        )
        claims.append(
            Claim(
                cid, str(raw.get("baseline", "")), rid, str(raw.get("subject") or f"claim:{cid}"),
                field_of(raw, "question", str, where).strip(), _locator(raw.get("claimant"), f"{where}.claimant", repos, True),
                upstream, impact, entailment,
            )
        )  # fmt: skip
    if len({c.id for c in claims}) != len(claims):
        raise ConfigError("investigation.yaml: duplicate claim ids")
    identities = [(c.relationship, c.subject) for c in claims]
    if len(set(identities)) != len(
        identities
    ):  # the candidate key is derived from them: two claims would be one candidate
        raise ConfigError(
            "investigation.yaml: two claims share a relationship and a subject (give one a distinct 'subject')"
        )
    reviews: list[IntentReview] = []
    for i, raw in enumerate(doc.get("intent_reviews") or []):
        where = f"investigation.yaml:intent_reviews[{i}]"
        if not isinstance(raw, dict) or set(raw) - {
            "id",
            "baseline",
            "policy",
            "relationships",
            "statuses",
            "question",
        }:
            raise ConfigError(
                f"{where}: must be a mapping with id, baseline, policy, relationships, statuses, question"
            )
        pol = field_of(raw, "policy", str, where)
        if pol not in engine.policies:
            raise ConfigError(f"{where}: unknown policy {pol!r}")
        rids = tuple(str(x) for x in field_of(raw, "relationships", list, where))
        if any(r not in rels for r in rids):
            raise ConfigError(f"{where}: unknown relationship(s) {[r for r in rids if r not in rels]}")
        try:
            statuses = tuple(Status(str(s)) for s in field_of(raw, "statuses", list, where))
        except ValueError as exc:
            raise ConfigError(f"{where}: {exc}") from exc
        if Status.INCONSISTENT in statuses:
            raise ConfigError(f"{where}: a confirmed finding is never re-judged by the Investigator")
        reviews.append(
            IntentReview(
                str(raw["id"]),
                str(raw.get("baseline", "")),
                pol,
                rids,
                statuses,
                field_of(raw, "question", str, where).strip(),
            )
        )
    for i, a in enumerate(reviews):
        for b in reviews[i + 1 :]:
            if (
                a.policy == b.policy
                and set(a.relationships) & set(b.relationships)
                and set(a.statuses) & set(b.statuses)
            ):
                raise ConfigError(f"investigation.yaml: intent reviews {a.id} and {b.id} select the same outcomes")
    return Claims(str(policy["version"]), tuple(claims), tuple(reviews), digest(doc))


def _locate(reader: PinnedReader, loc: Locator, role: str) -> tuple[Evidence | None, tuple[str, str] | None]:
    """The single line matching ``line_regex`` (and ``phrase_regex``) as verified evidence, or a (cause, detail) failure."""
    got = reader.text(loc.repo, loc.path)
    if isinstance(got, Missing):
        return None, (got.cause, f"{role} {loc.repo}:{loc.path}: {got.detail}")
    art, text = got
    lines = text.split("\n")
    hits = [n for n, ln in enumerate(lines, 1) if re.search(loc.line_regex, ln)]
    if loc.phrase_regex:
        hits = [n for n in hits if re.search(loc.phrase_regex, lines[n - 1])]
    if not hits:
        return None, (Cause.LOCATOR_NO_MATCH, f"{role} {loc.repo}:{loc.path}: no line matches the declared locator")
    if len(hits) > 1:
        return None, (
            Cause.LOCATOR_AMBIGUOUS,
            f"{role} {loc.repo}:{loc.path}: {len(hits)} lines match the declared locator",
        )
    n = hits[0]
    ev = Evidence(
        role, art.repo, art.slug, art.sha, art.path, f"line matching /{loc.line_regex}/", "text", lines[n - 1],
        art.content_sha256, art.blob_sha, n, n, EXTRACTOR,
    )  # fmt: skip
    return ev, None


def claim_candidates(
    claims: Claims, engine: Engine, reader: PinnedReader, scope: Sequence[str]
) -> tuple[list[Candidate], list[dict[str, str]]]:
    """Pre-filter: one candidate per declared claim whose relationship is inside the run's scope. Returns (candidates,
    skipped) — a skipped claim is reported with its reason, never dropped silently."""
    rels = {r.id: r for r in engine.graph.trusted()}
    out: list[Candidate] = []
    skipped: list[dict[str, str]] = []
    for claim in claims.claims:
        rel = rels[claim.relationship]
        if rel.source not in scope or rel.target not in scope:
            skipped.append({"id": claim.id, "reason": "a repository of the relationship is outside the selected scope"})
            continue
        key = candidate_key(POLICY_ID, rel.id, claim.subject)
        evidence: list[Evidence] = []
        failure: tuple[str, str] | None = None
        claimant, failure = _locate(reader, claim.claimant, "claimant")
        if claimant is not None:
            evidence.append(claimant)
            for loc in claim.upstream:
                ev, failure = _locate(reader, loc, "upstream")
                if ev is not None:
                    evidence.append(ev)
                else:
                    break
        out.append(
            Candidate(
                key, POLICY_ID, claims.version, rel.id, claim.subject, KIND_CLAIM, claim.question,
                claimant.quote if claimant else "", tuple(evidence), claim.impact, rel.type.value,
                tuple(sorted({rel.source, rel.target, claim.claimant.repo, *(u.repo for u in claim.upstream)})),
                claim.entailment, (), failure,
            )
        )  # fmt: skip
    return out, skipped


def intent_candidates(
    claims: Claims, engine: Engine, outcomes: Sequence[Outcome], scope: Sequence[str]
) -> list[Candidate]:
    """Intent reviews: undetermined deterministic outcomes (e.g. H1) the Investigator may propose an intent for. The
    outcome itself is never changed (ADR-0016 §4)."""
    rels = {r.id: r for r in engine.graph.trusted()}
    out: list[Candidate] = []
    for review in claims.intent_reviews:
        for o in outcomes:
            if (
                o.policy_id != review.policy
                or o.relationship_id not in review.relationships
                or o.status not in review.statuses
            ):
                continue
            rel = rels[o.relationship_id]
            if rel.source not in scope or rel.target not in scope:
                continue
            subject = f"intent:{o.policy_id}/{o.subject}"
            out.append(
                Candidate(
                    candidate_key(POLICY_ID, rel.id, subject), POLICY_ID, claims.version, rel.id, subject, KIND_INTENT,
                    review.question, o.reason, o.evidence, Impact.OUTDATES, rel.type.value,
                    tuple(sorted({rel.source, rel.target})), None, o.open_questions, None, o.status.value,
                )
            )  # fmt: skip
    return out


def build_candidates(
    claims: Claims, engine: Engine, reader: PinnedReader, outcomes: Sequence[Outcome], scope: Sequence[str]
) -> tuple[list[Candidate], list[dict[str, str]]]:
    cands, skipped = claim_candidates(claims, engine, reader, scope)
    cands += intent_candidates(claims, engine, outcomes, scope)
    keys = [c.key for c in cands]
    if len(set(keys)) != len(keys):  # a collision would investigate one candidate twice and lose the other silently
        dup = sorted({f"{c.relationship_id}/{c.subject}" for c in cands if keys.count(c.key) > 1})
        raise ConfigError(f"investigation.yaml: candidates with the same identity: {dup}")
    return sorted(cands, key=lambda c: (c.relationship_id, c.subject)), skipped


def claims_summary(claims: Claims) -> Mapping[str, Any]:
    return {"policy": {"id": POLICY_ID, "version": claims.version}, "declarations_digest": claims.digest}
