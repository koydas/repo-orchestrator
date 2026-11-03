"""P6 Tooling adoption: repositories in the plugin-adoption scope declare the two plugin keys of ai-dev-tools ADR-018
exactly, and appear in the adopter table with their skills.

Separated on purpose: *exact plugin-key agreement* (checked), *unrelated repository-specific settings* such as
``permissions`` and ``hooks`` (a question about how to read "the file is identical", baseline Q3, never a finding unless
the owner decides so), and *scope* (which repositories ADR-018 covers, baseline Q1, never a finding while undetermined).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .. import extract
from ..collector.snapshot import ArtifactRequest, Missing
from ..declarations import Declarations
from ..extract import Extracted, ExtractionError
from ..graph import Graph
from ..models import (
    Applicability,
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
    id="P6",
    version="1.0.0",
    title="Plugin adoption: exact plugin keys, adopter table, scope",
    aspect="plugin_adoption",
    scope=Scope(ScopeKind.RELATIONSHIP_TYPE, ("tooling_adoption",)),
    impact=Impact.OUTDATES,
    intent=Intent.UNDETERMINED,
)

FILE_IDENTITY = ("undecided", "exact_file", "plugin_keys_only")
PLUGIN_KEYS = ("extraKnownMarketplaces", "enabledPlugins")


@dataclass(frozen=True)
class TableSpec:
    repo: str
    path: str
    heading: str
    repo_column: str
    skills_column: str


@dataclass(frozen=True)
class Config:
    settings_path: str
    skills_dir: str
    plugin_keys: dict[str, Any]
    file_identity: str
    table: TableSpec
    authority_path: tuple[str, str] | None


def prepare(decl: Declarations, graph: Graph) -> Config:
    p = decl.policy_params.get("P6", {})
    keys = p.get("plugin_keys")
    if not isinstance(keys, dict) or sorted(keys) != sorted(PLUGIN_KEYS):
        raise ConfigError(f"policies.yaml:P6.plugin_keys must declare exactly {PLUGIN_KEYS}")
    identity = p.get("file_identity", "undecided")
    if identity not in FILE_IDENTITY:
        raise ConfigError(f"policies.yaml:P6.file_identity must be one of {FILE_IDENTITY}")
    t = p.get("adopter_table") or {}
    if t.get("repo") not in graph.repositories:
        raise ConfigError("policies.yaml:P6.adopter_table.repo is not a declared repository")
    table = TableSpec(*(str(t[k]) for k in ("repo", "path", "heading", "repo_column", "skills_column")))
    auth = (p.get("authority") or [{}])[0]
    authority = (str(auth["repo"]), str(auth["path"])) if auth.get("repo") in graph.repositories else None
    return Config(
        str(p.get("settings_path", ".claude/settings.json")),
        str(p.get("skills_dir", ".claude/skills")),
        keys,
        str(identity),
        table,
        authority,
    )


def applies(rel: Relationship) -> bool:
    return rel.type is RelType.TOOLING_ADOPTION


def requests(cfg: Config, rel: Relationship) -> Sequence[ArtifactRequest]:
    out = [ArtifactRequest(rel.target, cfg.settings_path), ArtifactRequest(cfg.table.repo, cfg.table.path)]
    if cfg.authority_path:
        out.append(ArtifactRequest(*cfg.authority_path))
    return out


def strict_equal(a: Any, b: Any) -> bool:
    """JSON equality where ``true`` is not ``1``."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(strict_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(strict_equal(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


def _key_line(text: str, key: str) -> Extracted:
    return extract.json_top_level_key(text, key)


def _skills(ctx: EvalContext, cfg: Config, repo: str) -> tuple[list[str], Evidence]:
    entries = ctx.entries(repo, cfg.skills_dir)
    prefix = cfg.skills_dir.strip("/") + "/"
    names = sorted(
        {
            e.path[len(prefix) :].split("/")[0]
            for e in entries
            if e.path.endswith("/SKILL.md") and e.path.count("/") == prefix.count("/") + 1
        }
    )
    return names, ctx.listing_ev(
        "adopter", repo, cfg.skills_dir, names, f"skill directories under {cfg.skills_dir}/", "skills_listing@1"
    )


def _authority_ev(ctx: EvalContext, cfg: Config) -> list[Evidence]:
    if cfg.authority_path is None:
        return []
    repo, path = cfg.authority_path
    try:
        art, text = ctx.text(repo, path)
    except CannotEvaluate:
        return []
    marker = "extraKnownMarketplaces"
    idx = text.find(marker)
    if idx < 0:
        return []
    line = text.count("\n", 0, idx) + 1
    return [
        ctx.text_ev(
            "authority",
            art,
            Extracted(marker, text.splitlines()[line - 1], line, line, "quote@1"),
            "ADR-018 §1: the two plugin keys",
        )
    ]


def evaluate(cfg: Config, ctx: EvalContext, rel: Relationship) -> list[Outcome]:
    undetermined = rel.applicability is Applicability.UNDETERMINED
    question = rel.open_question or ""
    repo = rel.target
    out: list[Outcome] = []
    authority = _authority_ev(ctx, cfg)
    locator = f"{repo}:{cfg.settings_path}"

    def ask(
        subject: str, reason: str, evidence: list[Evidence], q: str, comparison: Comparison | None = None
    ) -> Outcome:
        return ctx.clarify(rel, subject, reason, q, evidence, [locator, subject], comparison)

    settings = ctx.snapshot.artifact(repo, cfg.settings_path)
    skills_names: list[str] = []
    skills_ev: Evidence | None = None
    try:
        skills_names, skills_ev = _skills(ctx, cfg, repo)
    except CannotEvaluate as exc:
        if undetermined or (isinstance(settings, Missing) and settings.cause != Cause.NOT_FOUND):
            out.append(ctx.cannot(rel, "skills", exc, [f"{repo}:{cfg.skills_dir}"]))

    # ---- plugin keys -----------------------------------------------------------------------------------------
    if isinstance(settings, Missing):
        if settings.cause != Cause.NOT_FOUND:
            out.append(ctx.cannot(rel, "plugin_keys", CannotEvaluate(settings.cause, settings.detail), [locator]))
            return out
        evidence = ([skills_ev] if skills_ev else []) + authority
        if undetermined:
            sk = f"has {len(skills_names)} repository skill(s) but " if skills_names else ""
            out.append(
                ask(
                    "plugin_keys",
                    f"{ctx.slug(repo)} {sk}declares no {cfg.settings_path}; whether ADR-018 covers it is undecided ({question})",
                    evidence,
                    question,
                )
            )
        else:
            out.append(
                ctx.inconsistent(
                    rel,
                    "plugin_keys",
                    f"{ctx.slug(repo)} is in the adoption scope but has no {cfg.settings_path}",
                    Comparison("plugin_keys_equal", None, cfg.plugin_keys, "not_equal"),
                    evidence,
                    [locator],
                    affected=[repo],
                    investigate=[locator],
                    fix_sites=[FixSite(repo, cfg.settings_path, "declare the two plugin keys")],
                )
            )
        return out
    try:
        text = extract.decode(settings.data)
        data = extract.json_loads(text)
        if not isinstance(data, dict):
            raise ExtractionError(Cause.PARSE_ERROR, "settings.json is not an object")
    except ExtractionError as exc:
        out.append(
            ctx.cannot(rel, "plugin_keys", CannotEvaluate(exc.cause, f"{cfg.settings_path}: {exc.detail}"), [locator])
        )
        return out
    present = [k for k in PLUGIN_KEYS if k in data]
    evidence = [ctx.text_ev("adopter", settings, _key_line(text, k), f"top-level key {k}") for k in present] + authority
    actual = {k: data.get(k) for k in PLUGIN_KEYS}
    keys_ok = all(strict_equal(data.get(k), cfg.plugin_keys[k]) for k in PLUGIN_KEYS)
    comparison = Comparison("plugin_keys_equal", actual, cfg.plugin_keys, "equal" if keys_ok else "not_equal")
    if keys_ok:
        out.append(
            ctx.consistent(
                rel,
                "plugin_keys",
                f"{ctx.slug(repo)} declares exactly the two plugin keys",
                comparison,
                evidence,
                [locator, "plugin_keys"],
            )
        )
    elif undetermined:
        out.append(
            ask(
                "plugin_keys",
                f"{ctx.slug(repo)} does not declare the plugin keys of ADR-018; whether it is in scope is undecided ({question})",
                evidence,
                question,
                comparison,
            )
        )
    else:
        wrong = [k for k in PLUGIN_KEYS if not strict_equal(data.get(k), cfg.plugin_keys[k])]
        out.append(
            ctx.inconsistent(
                rel,
                "plugin_keys",
                f"{ctx.slug(repo)} {cfg.settings_path}: {', '.join(wrong)} differ from the keys of ADR-018",
                comparison,
                evidence,
                [locator, "plugin_keys"],
                affected=[repo],
                investigate=[locator],
                fix_sites=[FixSite(repo, cfg.settings_path, f"correct {', '.join(wrong)}")],
                detail={"differing_keys": wrong},
            )
        )

    # ---- extra, repository-specific settings (Q3) ----------------------------------------------------------------
    extras = sorted(k for k in data if k not in PLUGIN_KEYS)
    if extras and keys_ok:
        extra_ev = [
            ctx.text_ev("adopter", settings, _key_line(text, k), f"top-level key {k}") for k in extras
        ] + authority
        detail = {"extra_keys": extras}
        cmp_ = Comparison("file_identity", sorted(data), list(PLUGIN_KEYS), "extra_keys", detail)
        if cfg.file_identity == "undecided":
            out.append(
                ctx.clarify(
                    rel,
                    "file_identity",
                    f"{ctx.slug(repo)} {cfg.settings_path} carries {', '.join(extras)} besides the two plugin keys; ADR-018 says 'the file is identical across repos' (Q3: file or plugin keys?)",
                    "Q3",
                    extra_ev,
                    [locator, "file_identity"],
                    cmp_,
                    detail=detail,
                )
            )
        elif cfg.file_identity == "exact_file":
            out.append(
                ctx.inconsistent(
                    rel,
                    "file_identity",
                    f"{ctx.slug(repo)} {cfg.settings_path} is not identical to the plugin declaration: extra {', '.join(extras)}",
                    cmp_,
                    extra_ev,
                    [locator, "file_identity"],
                    affected=[repo],
                    investigate=[locator],
                    detail=detail,
                )
            )
        else:
            out.append(
                ctx.consistent(
                    rel,
                    "file_identity",
                    f"extra keys {', '.join(extras)} are repository-specific (plugin_keys_only)",
                    cmp_,
                    extra_ev,
                    [locator, "file_identity"],
                    detail=detail,
                )
            )
    elif keys_ok:
        out.append(
            ctx.consistent(
                rel,
                "file_identity",
                "the file holds only the two plugin keys",
                None,
                evidence,
                [locator, "file_identity"],
            )
        )

    # ---- adopter table -----------------------------------------------------------------------------------------
    if not undetermined:
        out.append(_table(cfg, ctx, rel, skills_names, skills_ev, authority))
    return out


def _table(
    cfg: Config,
    ctx: EvalContext,
    rel: Relationship,
    skills: list[str],
    skills_ev: Evidence | None,
    authority: list[Evidence],
) -> Outcome:
    repo, t = rel.target, cfg.table
    slug_name = ctx.slug(repo).split("/")[1]
    locators = [f"{t.repo}:{t.path}#{t.heading}", f"{repo}:{cfg.skills_dir}"]
    try:
        art, text = ctx.text(t.repo, t.path)
        table = extract.md_table(text, t.heading)
    except ExtractionError as exc:
        return ctx.cannot(rel, "adopter_table", CannotEvaluate(exc.cause, f"{t.path}: {exc.detail}"), locators)
    except CannotEvaluate as exc:
        return ctx.cannot(rel, "adopter_table", exc, locators)
    header = table.detail["header"]
    missing = [c for c in (t.repo_column, t.skills_column) if c not in header]
    if missing:  # an unreadable layout is not evidence that the adopter is unlisted
        return ctx.cannot(
            rel, "adopter_table",
            CannotEvaluate(Cause.LOCATOR_NO_MATCH, f"{t.path}: table '{t.heading}' has no column {missing} (columns: {header})"),
            locators,
        )  # fmt: skip
    if skills_ev is None:
        return ctx.cannot(
            rel, "adopter_table", CannotEvaluate(Cause.NOT_FOUND, f"{cfg.skills_dir} could not be listed"), locators
        )
    rows = [r for r in table.value if r.get(t.repo_column, "").strip("` ") == slug_name]
    lines = text.splitlines()
    if not rows:
        ex = Extracted(slug_name, table.quote.splitlines()[0], table.line_start, table.line_start, "md_table@1")
        return ctx.inconsistent(
            rel,
            "adopter_table",
            f"{ctx.slug(repo)} adopts the plugin but is not listed under '{t.heading}' in {t.path}",
            Comparison("row_present", slug_name, None, "missing"),
            [ctx.text_ev("registry", art, ex, f"table '{t.heading}'"), skills_ev, *authority],
            locators,
            affected=[t.repo],
            investigate=[f"{t.repo}:{t.path}"],
            fix_sites=[FixSite(t.repo, t.path, f"add a row for {slug_name}")],
        )
    cell = rows[0].get(t.skills_column, "")
    listed = sorted(re.findall(r"`([^`]+)`", cell))
    row_line = next(
        (n for n, line in enumerate(lines, start=1) if line.lstrip().startswith(f"| `{slug_name}`")), table.line_start
    )
    row_ev = ctx.text_ev(
        "registry",
        art,
        Extracted(cell, lines[row_line - 1], row_line, row_line, "md_table@1"),
        f"row {slug_name} of '{t.heading}'",
    )
    comparison = Comparison("set_equal", listed, skills, "equal" if listed == skills else "not_equal")
    if listed == skills:
        return ctx.consistent(
            rel,
            "adopter_table",
            f"{t.path} lists exactly the {len(skills)} skill(s) of {ctx.slug(repo)}",
            comparison,
            [row_ev, skills_ev],
            locators,
        )
    return ctx.inconsistent(
        rel,
        "adopter_table",
        f"{t.path} lists skills {listed or '[]'} for {ctx.slug(repo)}, the repository has {skills or '[]'}",
        comparison,
        [row_ev, skills_ev, *authority],
        locators,
        affected=[t.repo],
        investigate=[f"{t.repo}:{t.path}", f"{repo}:{cfg.skills_dir}"],
        fix_sites=[FixSite(t.repo, t.path, "align the skills column")],
    )


def listings(cfg: Config, rel: Relationship) -> Sequence[tuple[str, str]]:
    """The adopter's skills directory: a skill added or removed changes the adopter table comparison."""
    return [(rel.target, cfg.skills_dir.strip("/"))]


POLICY = Policy(META, prepare, applies, requests, evaluate, listings)
