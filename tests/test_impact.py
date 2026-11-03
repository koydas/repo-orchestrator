"""C4: anchoring changed paths to relationships, direction and impact class, changed values."""

from __future__ import annotations

import pytest

from repo_orchestrator.engine import Engine
from repo_orchestrator.impact import IMPACT_BY_TYPE, FootprintUnavailable, analyze, value_changes
from repo_orchestrator.models import Comparison, Impact, Outcome, RelType, Status
from support import load_decl


@pytest.fixture(scope="module")
def engine():
    return Engine(load_decl())


def no_read(repo, path):
    """A linking document with no link to the PR repository: the footprint stays static."""
    return ""


def touched(engine, repo, paths, reader=no_read):
    return {e.relationship_id: e for e in analyze(engine, repo, paths, reader).entries}


class TestAnchoring:
    def test_a_directory_anchor_covers_everything_below_it(self, engine):
        t = touched(engine, "adl", ["docs/adr/0100-new.md"])
        assert {"R1.adl"} <= set(t) and "anchor" in {x.kind for x in t["R1.adl"].triggers}

    def test_a_file_anchor_matches_exactly_and_not_by_prefix(self, engine):
        assert "R2" in touched(engine, "adl", ["scripts/lib/findings_export.mjs"])
        assert "R2" not in touched(engine, "adl", ["scripts/lib/findings_export.mjs.bak"])

    def test_a_claim_glob_matches_one_segment_only(self, engine):
        assert "R8" in touched(engine, "homelab", ["apps/anything/service.yaml"])
        assert "R8" in touched(engine, "homelab", ["apps/anything/application.yaml"])
        assert "R8" not in touched(engine, "homelab", ["apps/anything/deep/service.yaml"])
        assert "R8" not in touched(engine, "homelab", ["apps/anything/values.yaml"])

    def test_both_names_of_a_rename_count(self, engine):
        old, new = "docs/adr/ADR-001-human-as-gate.md", "docs/adr/ADR-001-renamed.md"
        reader = lambda repo, path: open_doc(engine)  # noqa: E731
        assert "R9" in touched(engine, "adt", [old], reader) and "R9" not in touched(engine, "adt", [new], reader)
        assert "R9" in touched(engine, "adt", [old, new], reader)

    def test_a_path_that_belongs_to_no_footprint_touches_nothing(self, engine):
        a = analyze(engine, "chat", ["docs/notes/new-note.md"], no_read)
        assert a.entries == [] and "R7" in a.not_touched

    def test_a_declared_deployment_with_no_policy_is_reported_as_potential_impact_only(self, engine):
        t = touched(engine, "chat", ["k8s/hpa.yaml"])
        r6 = t["R6"]
        assert (r6.impact, r6.affected_repos, r6.policies) == (Impact.BREAKS, ("homelab",), ())
        assert not r6.evaluated and "potential only" in r6.note
        assert t["R8"].policies == ("P4",)  # the same file is also a P4 claim source

    def test_only_relationships_incident_to_the_repository_are_traversed(self, engine):
        # one hop: a change in adl never reaches R7 (homelab ↔ chat) even though both are two edges away
        assert not {"R7", "R8", "R6"} & set(
            touched(engine, "adl", ["scripts/lib/findings_export.mjs", "docs/adr/x.md"])
        )


def open_doc(engine):
    return "[x](https://github.com/koydas/ai-dev-tools/blob/main/docs/adr/ADR-001-human-as-gate.md)\n"


class TestReferences:
    def test_a_link_target_of_the_other_repository_is_a_footprint(self, engine):
        calls = []

        def reader(repo, path):
            calls.append((repo, path))
            return open_doc(engine)

        t = touched(engine, "adt", ["docs/adr/ADR-001-human-as-gate.md"], reader)
        assert {x.kind for x in t["R9"].triggers} == {"reference"}
        assert ("adl", "docs/autonomy-levels.md") in calls

    def test_a_tree_link_covers_everything_below_the_directory(self, engine):
        doc = "[d](https://github.com/koydas/ai-dev-tools/tree/main/docs/adr)\n"
        assert "R9" in touched(engine, "adt", ["docs/adr/ADR-099-new.md"], lambda r, p: doc)

    def test_a_file_that_nothing_links_does_not_touch_the_relationship(self, engine):
        assert "R9" not in touched(engine, "adt", ["docs/adr/ADR-777-unlinked.md"], lambda r, p: open_doc(engine))

    def test_an_unreadable_linking_document_makes_the_relationship_touched_not_skipped(self, engine):
        def reader(repo, path):
            raise FootprintUnavailable("artifact_not_found")

        r9 = touched(engine, "adt", ["README.md"], reader)["R9"]
        assert [t.kind for t in r9.triggers] == ["unresolved"] and "artifact_not_found" in r9.triggers[0].matched

    def test_a_claimant_inside_the_pr_repository_is_an_anchor_and_is_not_read(self, engine):
        def reader(repo, path):
            assert (repo, path) != ("adl", "docs/autonomy-levels.md"), (
                "the claimant in the PR repository must not be read"
            )
            return ""

        t = touched(engine, "adl", ["docs/autonomy-levels.md"], reader)
        assert "anchor" in {x.kind for x in t["R9"].triggers} and t["R9"].direction == "self"


class TestAuthorities:
    def test_changing_a_policy_authority_touches_every_relationship_the_policy_governs(self, engine):
        t = touched(engine, "chat", ["docs/adr/0009-fixed-chat-mode-automatic-model-routing.md"])
        r7 = t["R7"]
        assert {x.kind for x in r7.triggers} == {"authority"} and r7.direction == "policy"
        assert r7.affected_repos == ("chat", "homelab")

    def test_an_authority_in_a_repository_that_is_not_an_endpoint_still_counts(self, engine):
        t = touched(engine, "profile", ["CLAUDE.md"])  # P1's authority: governs R1.* where profile is the target
        assert {"R1.adl", "R1.ap", "R1.homelab"} <= set(t)
        assert all(
            {x.kind for x in e.triggers} == {"authority"} for e in t.values() if e.relationship_id.startswith("R1")
        )

    def test_an_authority_trigger_next_to_an_anchor_keeps_the_directional_semantics(self, engine):
        t = touched(engine, "adt", ["docs/adr/ADR-018-consumer-repo-adoption.md", "docs/consumer-repos.md"])
        assert {"anchor", "authority"} <= {x.kind for x in t["R5.adl"].triggers}
        assert t["R5.adl"].direction == "downstream"


class TestDirectionAndImpactClass:
    @pytest.mark.parametrize(
        ("rel_type", "impact"),
        [
            (RelType.RUNTIME_CONTRACT, Impact.BREAKS),
            (RelType.BUILD_DEPENDENCY, Impact.BREAKS),
            (RelType.DEPLOYMENT, Impact.BREAKS),
            (RelType.SHARED_RESOURCE, Impact.BREAKS),
            (RelType.DOCUMENTARY, Impact.FALSIFIES),
            (RelType.TOOLING_ADOPTION, Impact.OUTDATES),
            (RelType.CONCEPTUAL, Impact.OUTDATES),
        ],
    )
    def test_the_impact_class_follows_the_relationship_type(self, rel_type, impact):
        assert IMPACT_BY_TYPE[rel_type] is impact  # docs/domain-model.md → Relationship types

    def test_every_relationship_type_has_a_class(self):
        assert set(IMPACT_BY_TYPE) == set(RelType)

    def test_bilateral_contracts_affect_the_other_endpoint_from_either_side(self, engine):
        assert touched(engine, "adl", ["scripts/lib/findings_export.mjs"])["R2"].affected_repos == ("adt",)
        assert touched(engine, "adt", ["scripts/loop-insights.mjs"])["R2"].affected_repos == ("adl",)

    def test_unilateral_relationships_flow_from_source_to_target_and_a_target_only_affects_itself(self, engine):
        assert (
            touched(engine, "ap", ["patterns/x/README.md"])["R1.ap"].direction,
            touched(engine, "ap", ["patterns/x/README.md"])["R1.ap"].affected_repos,
        ) == ("downstream", ("profile",))
        own = touched(engine, "profile", ["README.md"])["R1.ap"]
        assert (own.direction, own.affected_repos, own.impact) == ("self", ("profile",), Impact.FALSIFIES)

    def test_tooling_adoption_outdates_the_adopter(self, engine):
        e = touched(engine, "adt", ["docs/consumer-repos.md"])["R5.adl"]
        assert (e.impact, e.affected_repos, e.direction) == (Impact.OUTDATES, ("adl",), "downstream")


class TestValueChanges:
    def o(self, subject, left, right, status=Status.CONSISTENT, result="equal"):
        return Outcome("P2", "1", "R2", subject, status, "", comparison=Comparison("equal", left, right, result))

    def test_only_changed_values_are_reported(self):
        base = [self.o("a", 1, 1), self.o("b", 5, 5)]
        head = [self.o("a", 2, 1, Status.INCONSISTENT, "not_equal"), self.o("b", 5, 5)]
        rows = value_changes(base, head)
        assert [r["subject"] for r in rows] == ["a"]
        assert rows[0]["base"] == {"status": "consistent", "left": 1, "right": 1, "result": "equal"}
        assert rows[0]["head"]["left"] == 2

    def test_added_and_removed_subjects_are_changes_too(self):
        rows = value_changes([self.o("gone", 1, 1)], [self.o("new", 1, 1)])
        assert [(r["subject"], r["base"] is None, r["head"] is None) for r in rows] == [
            ("gone", False, True),
            ("new", True, False),
        ]

    def test_outcomes_without_a_comparison_carry_no_value(self):
        assert value_changes([Outcome("P1", "1", "R", "s", Status.CANNOT_EVALUATE, "")], []) == []


class TestEveryListingAPolicyReadsIsAFootprint:
    """A policy that lists a directory depends on what is *in* it, not only on the files it reads: C4 must see a file added or
    removed there. The test records every ``ctx.entries`` call of a real evaluation and requires it to be declared."""

    def test_every_directory_listing_read_during_a_scan_is_declared_by_its_policy(self, engine, monkeypatch):
        from repo_orchestrator.policies.base import EvalContext
        from support import scan

        recorded: set[tuple[str, str, str]] = set()
        original = EvalContext.entries

        def spy(self, repo, prefix):
            recorded.add((self.meta.id, repo, prefix.strip("/")))
            return original(self, repo, prefix)

        monkeypatch.setattr(EvalContext, "entries", spy)
        scan()
        declared = {
            (p.meta.id, repo, directory)
            for rel in engine.graph.trusted()
            for p in engine.relationship_policies(rel.id)
            for repo, directory in p.listings(engine.configs[p.meta.id], rel)
        }
        assert {r[0] for r in recorded} >= {"P1", "P5", "P6"}  # the spy really observed the listing policies
        undeclared = {r for r in recorded - declared if not (r[0] == "P5" and r[2] == "")}
        # P5 lists the whole target tree to resolve links: its footprint is the link targets (reference triggers)
        assert not undeclared, f"policies list directories that C4 does not watch: {sorted(undeclared)}"

    def test_a_skill_added_in_an_adopter_touches_the_adoption_relationship(self, engine):
        t = touched(engine, "ap", [".claude/skills/new-skill/SKILL.md"])
        assert "listing" in {x.kind for x in t["R5.ap"].triggers} and t["R5.ap"].affected_repos == ("ap",)

    def test_an_adr_added_to_a_counted_directory_touches_the_count_relationship(self, engine):
        t = touched(engine, "homelab", ["docs/adr/0099-new.md"])
        assert "listing" in {x.kind for x in t["R1.homelab"].triggers}
        assert "R1.adl" not in touched(engine, "homelab", ["docs/adr/0099-new.md"])  # another repository's count

    def test_a_listing_of_another_repository_is_not_a_footprint_of_this_one(self, engine):
        assert "R5.ap" not in touched(engine, "adl", [".claude/skills/new-skill/SKILL.md"])
