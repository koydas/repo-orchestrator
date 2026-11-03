"""Declarations are validated at load time, before anything is collected."""

from __future__ import annotations

import shutil
from datetime import date

import pytest

from repo_orchestrator.declarations import load_declarations
from repo_orchestrator.engine import Engine
from repo_orchestrator.graph import Graph
from repo_orchestrator.models import ConfigError, Origin, Relationship, RelType, Repository
from support import DECLARATIONS, load_decl


def decls(tmp_path, **edits):
    """Copy the real declarations and apply ``{file: (old, new)}`` text edits."""
    target = tmp_path / "declarations"
    shutil.copytree(DECLARATIONS, target)
    for name, (old, new) in edits.items():
        path = target / f"{name}.yaml"
        text = path.read_text()
        assert old in text, f"{name}: {old!r} not found"
        path.write_text(text.replace(old, new, 1) if old else new)
    return target


def test_the_shipped_declarations_load_and_hold_the_baseline_relationships():
    d = load_decl()
    assert {r.baseline_id for r in d.relationships} >= {f"R{n}" for n in range(1, 12)}
    assert len([r for r in d.repositories if r.analyzed]) == 6
    assert all(r.origin is Origin.DECLARED for r in d.relationships)
    assert d.pins["baseline"].revisions["adl"] == "1607f6107b1f50f4e9baa1916a187c7bc16ca18a"
    assert d.digest == load_declarations(DECLARATIONS).digest


def test_the_declaration_digest_ignores_formatting_but_not_content(tmp_path):
    a = load_declarations(decls(tmp_path / "a")).digest
    reformatted = load_declarations(decls(tmp_path / "b", repositories=("{id: adl,", "{ id: adl ,"))).digest
    changed = load_declarations(decls(tmp_path / "c", repositories=("scope: primary}", "scope: secondary}"))).digest
    assert a == reformatted and a != changed


@pytest.mark.parametrize(
    ("file", "old", "new", "message"),
    [
        ("repositories", "version: 1", "version: 2", "unsupported or missing version"),
        ("repositories", "- {id: adl,", "- [id: adl,", "invalid YAML"),
        ("repositories", "repositories:", "repositories: 5\nx:", "'repositories' must be"),
        ("repositories", "slug: koydas/autonomous-dev-loop", "slug: not a slug", "invalid slug"),
        ("repositories", "{id: adt,", "{id: adl,", "duplicate repository id"),
        ("repositories", "slug: koydas/ai-dev-tools", "slug: koydas/autonomous-dev-loop", "duplicate repository slug"),
        ("relationships", "type: documentary", "type: friendship", "friendship"),
        (
            "relationships",
            'origin: declared\n    description: "Profile README describes autonomous',
            'origin: guessed\n    description: "Profile README describes autonomous',
            "guessed",
        ),
        (
            "relationships",
            'source: adl\n    target: profile\n    origin: declared\n    description: "Profile README describes autonomous',
            'source: nobody\n    target: profile\n    origin: declared\n    description: "Profile README describes autonomous',
            "unknown repository 'nobody'",
        ),
        ("relationships", "- id: R1.adt", "- id: R1.adl", "duplicate relationship id"),
        (
            "relationships",
            "anchors: [{repo: profile, path: README.md, role: claimant}]\n    contract:\n      counts: {claims: []}",
            "anchors: [{repo: homelab, path: README.md, role: x}]\n    contract:\n      counts: {claims: []}",
            "is not an endpoint",
        ),
        (
            "relationships",
            "applicability: undetermined\n    open_question: Q1",
            "applicability: undetermined",
            "must name its open question",
        ),
        ("relationships", "rule: pattern_dirs", "rule: guess", "unknown counting rule"),
        ("relationships", "value_regex: '(\\d+) multi-agent patterns'", "value_regex: '(unclosed'", "invalid regex"),
        (
            "relationships",
            "kind: js_const_int, repo: adl, path: scripts/lib/findings_export.mjs, name: FINDINGS_SCHEMA_VERSION",
            "kind: eval, repo: adl, path: scripts/lib/findings_export.mjs, name: X",
            "unknown locator kind",
        ),
        (
            "relationships",
            "kind: js_const_int, repo: adt, path: scripts/loop-insights.mjs, name: SUPPORTED_SCHEMA_VERSION",
            "kind: js_const_int, repo: adt, path: ../escape.mjs, name: X",
            "invalid 'path'",
        ),
        (
            "relationships",
            "kind: js_const_int, repo: adt, path: scripts/loop-insights.mjs, name: SUPPORTED_SCHEMA_VERSION",
            "kind: js_const_int, repo: nope, path: a.mjs, name: X",
            "'repo' must be one of",
        ),
        (
            "relationships",
            "kind: js_const_int, repo: adt, path: scripts/loop-insights.mjs, name: SUPPORTED_SCHEMA_VERSION",
            "kind: js_const_int, repo: adt, path: a.mjs",
            "'name' is required",
        ),
        ("relationships", "mode: producer_consumer", "mode: chaos", "mode must be"),
        ("relationships", "role: living", "role: sometimes", "role must be"),
        ("pins", "adl: 1607f6107b1f50f4e9baa1916a187c7bc16ca18a", "adl: main", "not a 40-hex commit SHA"),
        ("policies", "file_identity: undecided", "file_identity: whatever", "file_identity must be"),
        ("policies", "plugin_keys:", "plugin_keysX:", "plugin_keys must declare exactly"),
    ],
)
def test_invalid_declarations_fail_fast_with_the_file_and_field(tmp_path, file, old, new, message):
    with pytest.raises(ConfigError, match=message):
        Engine(load_declarations(decls(tmp_path, **{file: (old, new)})))


def test_duplicate_yaml_keys_are_rejected_rather_than_silently_overridden(tmp_path):
    with pytest.raises(ConfigError, match="duplicate key"):
        load_declarations(decls(tmp_path, pins=("version: 1", "version: 1\nversion: 1")))


def test_oversized_and_non_mapping_files_are_rejected(tmp_path):
    with pytest.raises(ConfigError, match="top level"):
        load_declarations(decls(tmp_path / "a", questions=("", "- just\n- a\n- list\n")))
    with pytest.raises(ConfigError, match="larger than"):
        load_declarations(decls(tmp_path / "b", questions=("", "version: 1\n# " + "x" * 600_000)))
    with pytest.raises(ConfigError, match="cannot read"):
        load_declarations(tmp_path / "does-not-exist")


EXC = """exceptions:
  - id: X1
    policy: P1
    relationships: [R1.ap]
    justification: "because"
    authority: {repo: ap, path: README.md}
    owner: me
    review_by: 2027-01-01
"""


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("owner: me", "owner: ''", "must not be empty"),
        ('justification: "because"', "justification: ' '", "must not be empty"),
        ("review_by: 2027-01-01", "review_by: soon", "must be a date"),
        ("review_by: 2027-01-01\n", "", "must be a date"),
        ("relationships: [R1.ap]", "relationships: ['*']", "no wildcard"),
        ("relationships: [R1.ap]", "relationships: []", "no wildcard"),
        ("authority: {repo: ap, path: README.md}", "authority: {repo: ap}", "missing 'path'"),
        ("policy: P1", "policy: P9", "unknown policy"),
        ("relationships: [R1.ap]", "relationships: [R99]", "unknown relationship"),
    ],
)
def test_invalid_exceptions_are_refused_at_load(tmp_path, old, new, message):
    text = EXC.replace(old, new)
    with pytest.raises(ConfigError, match=message):
        Engine(load_declarations(decls(tmp_path, exceptions=("exceptions: []\n", text))))


def test_a_valid_exception_loads(tmp_path):
    d = load_declarations(decls(tmp_path, exceptions=("exceptions: []\n", EXC)))
    (exc,) = d.exceptions
    assert exc.review_by == date(2027, 1, 1) and exc.relationships == ("R1.ap",)
    Engine(d)


def test_graph_trusts_only_declared_relationships_and_keeps_proposals_apart():
    repos = [Repository("a", "o/a", "primary"), Repository("b", "o/b", "primary")]
    rels = [
        Relationship("E1", "E1", RelType.RUNTIME_CONTRACT, "a", "b", Origin.DECLARED, "d"),
        Relationship("E2", "E2", RelType.RUNTIME_CONTRACT, "b", "a", Origin.INFERRED, "an LLM guess"),
        Relationship("E3", "E3", RelType.CONCEPTUAL, "a", "b", Origin.OBSERVED, "a url"),
    ]
    g = Graph(repos, rels)
    assert [r.id for r in g.trusted()] == ["E1"] and [r.id for r in g.proposals()] == ["E2", "E3"]
    with pytest.raises(ConfigError, match="same repository"):
        Graph(repos, [Relationship("S", "S", RelType.CONCEPTUAL, "a", "a", Origin.DECLARED, "d")])


def test_inferred_relationships_are_reported_never_evaluated(tmp_path):
    import dataclasses

    from support import scan

    d = load_decl()
    r2 = next(r for r in d.relationships if r.id == "R2")
    guess = dataclasses.replace(r2, id="R2-guess", origin=Origin.INFERRED)
    report = scan(decl=dataclasses.replace(d, relationships=(*d.relationships, guess)))
    assert not [o for o in report.result.outcomes if o.relationship_id == "R2-guess"]
    assert {c.relationship_id: c.status for c in report.result.coverage}["R2-guess"] == "proposal"


def test_every_trusted_relationship_has_a_coverage_entry_and_an_explicit_reason_when_unevaluated():
    from support import scan

    report = scan()
    cov = {c["relationship_id"]: c for c in report.semantic["coverage"]}
    assert set(cov) == {r.id for r in load_decl().relationships}
    for rel in ("R4.adl", "R4.adt", "R6", "R10"):
        assert cov[rel]["status"] == "no_policy" and cov[rel]["note"]
    for rel in ("R5.fullstack-pilot", "R5.sync-engine", "R11"):
        assert cov[rel]["status"] == "not_analyzed"
    assert cov["R7"]["status"] == "evaluated" and cov["R7"]["policies"] == ["P3"]
