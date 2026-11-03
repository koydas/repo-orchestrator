"""Engine behaviour: precedence, same-scope conflicts, exceptions, repository authorities, evidence and fingerprints."""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from repo_orchestrator.collector.snapshot import ArtifactRequest
from repo_orchestrator.declarations import AuthorityRef, ExceptionDecl, RepositoryAuthority
from repo_orchestrator.engine import Engine, fingerprint
from repo_orchestrator.evidence import text_evidence, verify
from repo_orchestrator.extract import Extracted
from repo_orchestrator.models import Comparison, ConfigError, Impact, Intent, ScopeKind, Status
from repo_orchestrator.policies import REGISTRY
from repo_orchestrator.policies.base import EvalContext, Policy, PolicyMeta, Scope
from support import AS_OF, find, load_decl, one, scan


def synthetic(pid, *, aspect="x", kind=ScopeKind.RELATIONSHIP, rels=("R2",), evaluate=None, version="1", values=None):
    if values is None:  # scope values that really cover R2 (a runtime_contract from adl to adt)
        values = {
            ScopeKind.RELATIONSHIP: rels,
            ScopeKind.RELATIONSHIP_TYPE: ("runtime_contract",),
            ScopeKind.REPOSITORY: ("adl",),
            ScopeKind.GLOBAL: (),
        }[kind]

    def default_eval(cfg, ctx: EvalContext, rel):
        cmp = Comparison("equal", 1, 2, "not_equal")
        return [ctx.inconsistent(rel, f"s-{pid}", f"{pid} says so", cmp, [], [f"{pid}:loc"], [rel.target], [])]

    return Policy(
        PolicyMeta(pid, version, pid, aspect, Scope(kind, tuple(values)), Impact.BREAKS, Intent.ACCIDENTAL),
        lambda d, g: None,
        lambda rel: rel.id in rels,
        lambda cfg, rel: [],
        evaluate or default_eval,
    )


def engine_with(*policies, decl=None):
    d = decl or load_decl()
    return Engine(dataclasses.replace(d, policy_params={}), policies=policies)


def run(engine):
    from support import baseline_revisions, baseline_source

    return engine.run(
        baseline_source(),
        __import__("repo_orchestrator.engine", fromlist=["RunOptions"]).RunOptions(
            revisions=baseline_revisions(engine.decl), as_of=AS_OF
        ),
    )


# ---- precedence and conflicts -----------------------------------------------------------------------------------


def test_two_policies_of_one_aspect_and_scope_are_a_configuration_error_before_anything_is_collected():
    with pytest.raises(ConfigError, match="same-scope policy conflict on R2"):
        engine_with(synthetic("PA"), synthetic("PB"))


def test_the_narrowest_scope_wins_and_the_loser_is_recorded_as_superseded():
    narrow = synthetic("PN", kind=ScopeKind.RELATIONSHIP)
    wide = synthetic("PW", kind=ScopeKind.RELATIONSHIP_TYPE)
    result = run(engine_with(wide, narrow))
    assert [o.policy_id for o in result.outcomes] == ["PN"]
    assert [(s.relationship_id, s.policy, s.by) for s in result.superseded] == [("R2", "PW", "PN")]


def test_policies_of_different_aspects_both_apply():
    result = run(engine_with(synthetic("PA", aspect="a"), synthetic("PB", aspect="b")))
    assert {o.policy_id for o in result.outcomes} == {"PA", "PB"}


def test_duplicate_policy_ids_are_refused():
    with pytest.raises(ConfigError, match="duplicate policy ids"):
        engine_with(synthetic("PA", aspect="a"), synthetic("PA", aspect="b"))


def test_parameters_for_an_unknown_policy_are_refused():
    d = load_decl()
    with pytest.raises(ConfigError, match="unknown policy 'P9'"):
        Engine(dataclasses.replace(d, policy_params={**d.policy_params, "P9": {}}))


# ---- fingerprints -------------------------------------------------------------------------------------------------


def test_duplicate_fingerprints_are_never_merged_silently():
    def twice(cfg, ctx, rel):
        cmp = Comparison("equal", 1, 2, "not_equal")
        return [
            ctx.inconsistent(rel, "same", "first", cmp, [], ["loc"], [], []),
            ctx.inconsistent(rel, "same", "second", cmp, [], ["loc"], [], []),
        ]

    out = run(engine_with(synthetic("PA", evaluate=twice))).outcomes
    assert [o.status for o in out] == [Status.INCONSISTENT, Status.FAILED]
    assert out[1].cause == "fingerprint_collision" and out[0].fingerprint and not out[1].fingerprint


def test_fingerprints_ignore_values_lines_and_shas_but_not_subject_or_locators():
    base = scan().result
    d1 = one(scan(), "P1", "R1.ap", "claim:patterns")
    from support import ROOT  # noqa: F401

    muted = scan(
        [
            __import__("repo_orchestrator.collector.overlay", fromlist=["x"]).replace_text(
                "koydas/koydas", "README.md", "17 multi-agent", "16 multi-agent"
            )
        ]
    )
    assert one(muted, "P1", "R1.ap", "claim:patterns").fingerprint == d1.fingerprint
    assert fingerprint(dataclasses.replace(d1, subject="claim:other")) != d1.fingerprint
    assert fingerprint(dataclasses.replace(d1, locators=(*d1.locators, "extra"))) != d1.fingerprint
    assert base is not None


# ---- evidence ------------------------------------------------------------------------------------------------------


def test_evidence_that_is_not_authentic_at_the_pinned_revision_turns_the_outcome_into_a_failure():
    def forged(cfg, ctx, rel):
        art = ctx.artifact("adl", "README.md")
        fake = text_evidence("producer", art, Extracted("x", "this text is not in the file", 1, 1, "t@1"), "forged")
        return [ctx.consistent(rel, "s", "claims success", None, [fake], ["l"])]

    class Needs(Policy):
        pass

    p = dataclasses.replace(
        synthetic("PA", evaluate=forged), requests=lambda cfg, rel: [ArtifactRequest("adl", "README.md")]
    )
    (o,) = run(engine_with(p)).outcomes
    assert o.status is Status.FAILED and o.cause == "evidence_unverifiable"


def test_evidence_verification_checks_sha_hash_and_listing_entries():
    from support import baseline_revisions, baseline_source

    d = load_decl()
    engine = Engine(d)
    from repo_orchestrator.engine import RunOptions

    result = engine.run(baseline_source(), RunOptions(revisions=baseline_revisions(d), as_of=AS_OF))
    ev = one(scan(), "P1", "R1.adl", "claim:adrs").evidence
    text_ev = next(e for e in ev if e.kind == "text")
    listing = next(e for e in ev if e.kind == "tree_listing")
    snap = result.snapshot
    assert verify(text_ev, snap) and verify(listing, snap)
    assert not verify(dataclasses.replace(text_ev, quote="invented"), snap)
    assert not verify(dataclasses.replace(text_ev, sha="0" * 40), snap)
    assert not verify(dataclasses.replace(text_ev, content_sha256="0" * 64), snap)
    assert not verify(dataclasses.replace(listing, quote=listing.quote + "\nnot-an-adr.md"), snap)
    assert not verify(
        dataclasses.replace(text_ev, repo="chat", path="README.md", sha=snap.repo("chat").sha), snap
    )  # different file, same sha


def test_a_policy_that_crashes_is_a_reported_failure_not_a_crashed_run_and_not_a_pass():
    def boom(cfg, ctx, rel):
        raise RuntimeError("bug in a policy")

    out = run(engine_with(synthetic("PA", evaluate=boom), synthetic("PB", aspect="b"))).outcomes
    failed = next(o for o in out if o.policy_id == "PA")
    assert failed.status is Status.FAILED and failed.cause == "internal_error" and "RuntimeError" in failed.reason
    assert next(o for o in out if o.policy_id == "PB").status is Status.INCONSISTENT  # the rest of the run continued


def test_requesting_an_out_of_scope_repository_is_a_configuration_error():
    engine = Engine(load_decl())
    for bad in ("fullstack-pilot", "nope"):
        with pytest.raises(ConfigError):
            engine.selected_repos([bad])


# ---- exceptions ---------------------------------------------------------------------------------------------------


def exception(**kw):
    base = dict(
        id="X1", policy="P1", relationships=("R1.ap",), subject="claim:patterns", justification="the profile lags on purpose until the next release",
        authority=AuthorityRef("ap", "README.md", "20 patterns"), owner="koydas", review_by=date(2027, 1, 1),
    )  # fmt: skip
    return ExceptionDecl(**{**base, **kw})


def with_exceptions(*excs, authorities=()):
    return dataclasses.replace(load_decl(), exceptions=tuple(excs), repository_authorities=tuple(authorities))


def test_a_valid_exception_marks_the_finding_excepted_and_intentional_but_keeps_it_listed():
    r = scan(decl=with_exceptions(exception()))
    o = one(r, "P1", "R1.ap", "claim:patterns")
    assert o.status is Status.EXCEPTED and o.intent is Intent.INTENTIONAL and o.exception_id == "X1" and o.fingerprint
    assert "the profile lags on purpose" in o.reason and r.semantic["excepted"][0]["subject"] == "claim:patterns"
    assert r.exit_code == 1  # D2 is still a finding
    assert [(e.id, e.state, e.matched) for e in r.result.exceptions] == [("X1", "applied", ("R1.ap/claim:patterns",))]


def test_an_expired_exception_stops_applying_and_the_finding_returns_to_confirmed():
    r = scan(decl=with_exceptions(exception(review_by=date(2026, 11, 1))))
    o = one(r, "P1", "R1.ap", "claim:patterns")
    assert o.status is Status.INCONSISTENT and o.detail["expired_exceptions"] == ["X1"]
    assert [(e.id, e.state) for e in r.result.exceptions] == [("X1", "expired")]


def test_the_review_date_itself_is_still_valid():
    assert (
        one(scan(decl=with_exceptions(exception(review_by=AS_OF))), "P1", "R1.ap", "claim:patterns").status
        is Status.EXCEPTED
    )


def test_the_same_exception_expires_by_as_of_alone():
    decl = with_exceptions(exception(review_by=date(2026, 12, 1)))
    assert one(scan(decl=decl, as_of=date(2026, 11, 2)), "P1", "R1.ap", "claim:patterns").status is Status.EXCEPTED
    assert one(scan(decl=decl, as_of=date(2026, 12, 2)), "P1", "R1.ap", "claim:patterns").status is Status.INCONSISTENT


@pytest.mark.parametrize(
    "authority",
    [
        AuthorityRef("ap", "README.md", "a quote that is not there"),
        AuthorityRef("ap", "docs/nowhere.md"),
        AuthorityRef("nobody", "README.md"),
    ],
)
def test_an_exception_without_a_resolvable_authority_is_not_applied(authority):
    r = scan(decl=with_exceptions(exception(authority=authority)))
    assert one(r, "P1", "R1.ap", "claim:patterns").status is Status.INCONSISTENT
    assert [e.state for e in r.result.exceptions] == ["authority_unresolved"]


def test_an_exception_only_covers_what_it_names():
    r = scan(
        decl=with_exceptions(
            exception(
                subject="claim:adrs", relationships=("R1.adl",), authority=AuthorityRef("adl", "README.md", "59 ADRs")
            )
        )
    )
    assert one(r, "P1", "R1.adl", "claim:adrs").status is Status.EXCEPTED
    assert one(r, "P1", "R1.ap", "claim:patterns").status is Status.INCONSISTENT  # untouched
    r = scan(decl=with_exceptions(exception(subject="claim:something-else")))
    assert [e.state for e in r.result.exceptions] == ["unused"]


def test_exceptions_never_hide_missing_evidence():
    from repo_orchestrator.collector.overlay import replace_text

    mutation = replace_text("koydas/koydas", "README.md", "17 multi-agent patterns", "seventeen multi-agent patterns")
    r = scan([mutation], decl=with_exceptions(exception()))
    assert one(r, "P1", "R1.ap", "claim:patterns").status is Status.CANNOT_EVALUATE
    assert [e.state for e in r.result.exceptions] == ["unused"]


# ---- repository authority (policy conflict) -------------------------------------------------------------------------


def authority(**kw):
    base = dict(
        id="A1", repo="ap", policy="P1", relationships=("R1.ap",), subject="claim:patterns",
        statement="agent-patterns README is the single source of its pattern count", authority=AuthorityRef("ap", "README.md", "20 patterns"),
    )  # fmt: skip
    return RepositoryAuthority(**{**base, **kw})


def test_a_wider_policy_that_contradicts_a_repository_own_authority_is_a_conflict_for_a_human():
    r = scan(decl=with_exceptions(authorities=[authority()]))
    o = one(r, "P1", "R1.ap", "claim:patterns")
    assert o.status is Status.POLICY_CONFLICT and o.detail["repository_authority"] == "A1"
    assert any(e.side == "repository_authority" for e in o.evidence) and "single source" in o.reason
    assert r.semantic["policy_conflicts"] and r.exit_code == 1  # D2 is a finding; the conflict is not one


def test_an_unverifiable_repository_authority_never_suppresses_a_finding():
    r = scan(
        decl=with_exceptions(authorities=[authority(authority=AuthorityRef("ap", "README.md", "not in the file"))])
    )
    assert one(r, "P1", "R1.ap", "claim:patterns").status is Status.INCONSISTENT


def test_unknown_references_in_exceptions_and_authorities_are_refused_at_load():
    with pytest.raises(ConfigError):
        Engine(with_exceptions(authorities=[authority(policy="P9")]))
    with pytest.raises(ConfigError):
        Engine(with_exceptions(authorities=[authority(relationships=("R99",))]))


# ---- planning ---------------------------------------------------------------------------------------------------------


def test_the_collection_plan_contains_only_declared_artifacts_and_authorities():
    engine = Engine(load_decl())
    plan = engine.plan(set(engine.graph.analyzed_ids()))
    paths = {(r.repo, r.path or r.glob) for r in plan}
    assert ("chat", "src/lib/conversations.js") in paths and ("homelab", "apps/*/service.yaml") in paths
    assert ("profile", "CLAUDE.md") in paths  # P1 authority
    assert len(plan) < 60 and not any(p and "docs/adr/0015" in p for _, p in paths)
    assert find(scan(), "P1", "R1.adl")  # sanity


def test_scope_selection_plans_only_the_selected_repositories():
    engine = Engine(load_decl())
    plan = engine.plan({"adl", "adt"})
    assert {r.repo for r in plan} <= {"adl", "adt"}


def test_every_policy_in_the_registry_has_a_version_scope_authority_and_unique_id():
    d = load_decl()
    assert [p.meta.id for p in REGISTRY] == ["P1", "P2", "P3", "P4", "P5", "P6"]
    for p in REGISTRY:
        assert p.meta.version.count(".") == 2 and p.meta.check == "deterministic" and p.meta.scope.kind
        assert d.policy_params[p.meta.id]["authority"], f"{p.meta.id} has no declared authority"


# ---- review findings: declared scope values, undetermined applicability ---------------------------------------------------


def test_a_policy_is_selected_only_where_its_declared_scope_values_cover_the_relationship():
    elsewhere = synthetic("PA", kind=ScopeKind.RELATIONSHIP, values=("R99",))  # applies() says yes, scope says no
    assert run(engine_with(elsewhere)).outcomes == []
    covering = synthetic("PB", kind=ScopeKind.RELATIONSHIP, values=("R2",))
    assert [o.policy_id for o in run(engine_with(covering)).outcomes] == ["PB"]


def test_same_scope_kind_on_different_repositories_neither_conflicts_nor_supersedes():
    on_adl = synthetic("PA", kind=ScopeKind.REPOSITORY, values=("adl",))
    on_homelab = synthetic("PB", kind=ScopeKind.REPOSITORY, values=("homelab",))
    result = run(
        engine_with(on_adl, on_homelab)
    )  # no ConfigError; only the policy whose repository is an endpoint runs
    assert [o.policy_id for o in result.outcomes] == ["PA"] and result.superseded == []


def test_scope_values_of_the_shipped_policies_cover_every_relationship_they_evaluate():
    from repo_orchestrator.policies.base import in_scope

    engine = Engine(load_decl())
    for rel in engine.graph.trusted():
        for p in engine.plan_by_rel[rel.id][0]:
            assert in_scope(p.meta.scope, rel), (p.meta.id, rel.id)


def test_an_undetermined_relationship_can_only_produce_a_question_never_a_finding():
    from repo_orchestrator.collector.overlay import replace_text
    from support import scan as _scan

    d = load_decl()
    rels = tuple(
        dataclasses.replace(
            r,
            applicability=__import__("repo_orchestrator.models", fromlist=["x"]).Applicability.UNDETERMINED,
            open_question="Q1",
        )
        if r.id == "R7"
        else r
        for r in d.relationships
    )
    mutation = replace_text(
        "koydas/ollama-chat",
        "src/lib/conversations.js",
        "TEXT_MODEL = 'llama3.1:8b-instruct-q4_0'",
        "TEXT_MODEL = 'nope:1b'",
    )
    r = _scan([mutation], decl=dataclasses.replace(d, relationships=rels))
    o = one(r, "P3", "R7", "const:TEXT_MODEL")
    assert (
        o.status is Status.POLICY_CLARIFICATION
        and o.open_questions == ("Q1",)
        and o.fingerprint is None
        and o.severity is None
    )
    assert not [x for x in r.result.outcomes if x.relationship_id == "R7" and x.status is Status.INCONSISTENT]
    assert one(r, "P3", "R7", "const:VISION_MODEL").status is Status.CONSISTENT  # consistent results are untouched
