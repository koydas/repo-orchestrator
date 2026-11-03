"""Offline evaluation against the pinned ecosystem baseline (docs/research/ecosystem-baseline.md).

Datasets: D1-D2 seeded positives, C1-C9 negative controls, N1-N4 look-alikes, U1-U5 mutations, Q1-Q6 open questions,
and collection failures. The expectations come from the baseline document, not from the implementation's output.
"""

from __future__ import annotations

import pytest

from repo_orchestrator.collector.overlay import add_file, replace_text
from repo_orchestrator.engine import Engine
from repo_orchestrator.models import Cause, Impact, Severity, Status
from support import AS_OF, classify, find, fingerprints, load_decl, one, scan, statuses

ADL, ADT, AP, PROFILE, HOMELAB, CHAT = (
    "koydas/autonomous-dev-loop", "koydas/ai-dev-tools", "koydas/agent-patterns", "koydas/koydas",
    "koydas/gitops-homelab", "koydas/ollama-chat",
)  # fmt: skip


@pytest.fixture(scope="module")
def base():
    return scan()


def _introduced(base, head):
    """Only the mutation is new: D1 and D2 stay pre-existing."""
    return {fp for fp, state in classify(base, head).items() if state == "introduced"}


# ---------------------------------------------------------------------------------------------------
# D1-D2: verified inconsistencies, detected and attributed
# ---------------------------------------------------------------------------------------------------


class TestSeededPositives:
    def test_the_baseline_has_exactly_the_two_documented_findings(self, base):
        findings = statuses(base, Status.INCONSISTENT)
        assert sorted((o.policy_id, o.relationship_id, o.subject) for o in findings) == [
            ("P1", "R1.adl", "claim:adrs"),
            ("P1", "R1.ap", "claim:patterns"),
        ]

    def test_d1_profile_says_17_patterns_agent_patterns_has_20(self, base):
        f = one(base, "P1", "R1.ap", "claim:patterns")
        assert (f.comparison.left, f.comparison.right, f.comparison.result) == (17, 20, "not_equal")
        assert f.detail["attribution"] == "claimant"
        assert [(s.repo, s.path) for s in f.fix_sites] == [("profile", "README.md")]
        assert f.impact is Impact.FALSIFIES and f.severity is Severity.MEDIUM
        assert set(f.affected_repos) == {"profile", "ap"}
        assert f.open_questions == ()

    def test_d2_profile_matches_the_adl_readme_but_the_root_cause_is_upstream(self, base):
        f = one(base, "P1", "R1.adl", "claim:adrs")
        assert (f.comparison.left, f.comparison.right) == (59, 65)
        assert f.detail["attribution"] == "upstream" and f.detail["upstream_value"] == 59
        # first fix site is the upstream README, then the profile that repeats it (never the profile alone)
        assert [(s.repo, s.path) for s in f.fix_sites] == [("adl", "README.md"), ("profile", "README.md")]
        assert "root cause is upstream" in f.reason

    def test_d2_names_the_open_counting_rule_and_cross_checks_the_index(self, base):
        f = one(base, "P1", "R1.adl", "claim:adrs")
        assert f.open_questions == ("Q2",)
        assert f.detail["alternative_counts"] == {"adr_index_entries": 65}

    @pytest.mark.parametrize("subject", ["claim:adrs", "claim:patterns"])
    def test_findings_carry_the_adr_0006_fields(self, base, subject):
        rel = "R1.adl" if subject == "claim:adrs" else "R1.ap"
        f = one(base, "P1", rel, subject)
        assert f.policy_version == "1.0.0" and f.fingerprint and len(f.fingerprint) == 16
        sides = {e.side for e in f.evidence}
        assert {"claimant", "counted", "upstream"} <= sides  # both repositories on both sides of the relationship
        assert {e.repo for e in f.evidence} >= {"profile"} and len({e.repo for e in f.evidence}) >= 2
        assert all(len(e.sha) == 40 and e.path and e.locator and e.content_sha256 for e in f.evidence)
        assert f.investigate and f.reason and f.severity and f.impact and f.intent

    def test_every_confirmed_finding_in_the_report_is_validated_by_check(self, base):
        for f in base.semantic["findings"]:
            assert f["status"] == "inconsistent" and f["pinned"] and all(f["pinned"].values())

    def test_d1_and_d2_fingerprints_are_stable_and_distinct(self, base):
        assert len(fingerprints(base)) == 2
        assert fingerprints(base) == fingerprints(scan())


# ---------------------------------------------------------------------------------------------------
# C1-C9: negative controls
# ---------------------------------------------------------------------------------------------------


class TestNegativeControls:
    def test_c1_homelab_has_25_adrs(self, base):
        o = one(base, "P1", "R1.homelab", "claim:adrs")
        assert o.status is Status.CONSISTENT and (o.comparison.left, o.comparison.right) == (25, 25)

    def test_c2_homelab_has_13_incidents(self, base):
        o = one(base, "P1", "R1.homelab", "claim:incidents")
        assert o.status is Status.CONSISTENT and o.comparison.right == 13

    def test_c3_findings_json_versions_agree(self, base):
        o = one(base, "P2", "R2", "schema_version")
        assert o.status is Status.CONSISTENT and (o.comparison.left, o.comparison.right) == (1, 1)
        assert one(base, "P2", "R2", "key_set").status is Status.CONSISTENT

    def test_c4_status_json_keys_are_equal_on_both_sides(self, base):
        keys = one(base, "P2", "R3", "key_set")
        assert keys.status is Status.CONSISTENT
        assert (
            keys.comparison.left
            == keys.comparison.right
            == ["app", "generated_at", "headline", "schema_version", "status"]
        )
        assert one(base, "P2", "R3", "schema_version").status is Status.CONSISTENT

    def test_c5_both_model_tags_are_pulled(self, base):
        for const, tag in (("TEXT_MODEL", "llama3.1:8b-instruct-q4_0"), ("VISION_MODEL", "llava:7b")):
            o = one(base, "P3", "R7", f"const:{const}")
            assert o.status is Status.CONSISTENT and o.comparison.left == tag

    def test_c6_metallb_claims_are_unique_and_inside_the_pool(self, base):
        out = find(base, "P4", "R8")
        assert {o.subject for o in out} == {f"address:192.168.1.{n}" for n in (243, 244, 245)}
        assert all(o.status is Status.CONSISTENT for o in out)

    def test_c7_the_three_adt_adrs_linked_from_adl_exist(self, base):
        out = find(base, "P5", "R9")
        assert len(out) == 3 and all(o.status is Status.CONSISTENT for o in out)

    def test_c8_adopter_table_matches_skills_for_every_in_scope_adopter(self, base):
        for rel in ("R5.adl", "R5.ap", "R5.profile"):
            assert one(base, "P6", rel, "adopter_table").status is Status.CONSISTENT
            assert one(base, "P6", rel, "plugin_keys").status is Status.CONSISTENT

    def test_c9_a_lower_bound_test_count_is_not_checkable_and_not_an_incomplete_run(self, base):
        o = one(base, "P1", "R1.adl", "claim:tests")
        assert o.status is Status.CANNOT_EVALUATE and o.cause == Cause.NOT_CHECKABLE
        assert base.semantic["summary"]["incomplete"] == 0 and base.semantic["run_status"] == "complete"

    def test_profile_links_into_other_repositories_resolve(self, base):
        for rel, subject in (
            ("R1.adl", "ref:blob:adl:docs/adr/0052-isolated-grader-for-executable-evals.md"),
            ("R1.homelab", "ref:blob:homelab:docs/adr/0011-ollama-q4-quantization.md"),
        ):
            assert one(base, "P5", rel, subject).status is Status.CONSISTENT

    def test_no_outcome_is_ever_a_failure_on_the_baseline(self, base):
        assert not statuses(base, Status.FAILED) and not statuses(base, Status.POLICY_CONFLICT)


# ---------------------------------------------------------------------------------------------------
# N1-N4: divergence that is not drift
# ---------------------------------------------------------------------------------------------------


class TestLookAlikes:
    def test_n1_both_adr_naming_formats_are_read_and_neither_is_reported(self, base):
        # adl/homelab use NNNN-title.md, adt uses ADR-NNN-title.md: P1 counts the former, P5 resolves the latter
        assert one(base, "P1", "R1.homelab", "claim:adrs").status is Status.CONSISTENT
        assert find(base, "P5", "R9", "ref:blob:adt:docs/adr/ADR-001-human-as-gate.md")[0].status is Status.CONSISTENT
        assert not [
            o for o in statuses(base, Status.INCONSISTENT) if "adr" in o.subject.lower() and o.policy_id != "P1"
        ]

    def test_n2_extra_settings_keys_are_a_policy_question_not_a_finding(self, base):
        o = one(base, "P6", "R5.adl", "file_identity")
        assert o.status is Status.POLICY_CLARIFICATION and o.open_questions == ("Q3",)
        assert o.detail["extra_keys"] == ["hooks", "permissions"]
        assert one(base, "P6", "R5.adl", "plugin_keys").status is Status.CONSISTENT  # the two plugin keys agree exactly
        assert not [o for o in statuses(base, Status.INCONSISTENT) if o.relationship_id == "R5.adl"]

    def test_n3_duplicated_new_adr_skills_are_not_reported_and_no_opportunity_exists_in_m1(self, base):
        assert "opportunities" not in base.semantic
        assert not [o for o in base.result.outcomes if "new-adr" in o.reason and o.status is Status.INCONSISTENT]

    def test_n4_accepted_adrs_are_point_in_time_and_never_read_for_comparison(self):
        engine = Engine(load_decl())
        planned = {(r.repo, r.path) for r in engine.plan(set(engine.graph.analyzed_ids()))}
        assert ("homelab", "docs/adr/0015-ollama-chat-git-source-application.md") not in planned
        assert ("chat", "docs/adr/0007-dedicated-metallb-ip.md") not in planned

    def test_n4_the_living_claim_of_244_is_consistent(self, base):
        assert one(base, "P4", "R8", "address:192.168.1.244").status is Status.CONSISTENT


# ---------------------------------------------------------------------------------------------------
# U1-U5: mutations applied in memory to the pinned snapshots
# ---------------------------------------------------------------------------------------------------


class TestMutations:
    def test_u1_schema_version_bump_breaks_the_consumer(self, base):
        head = scan(
            [
                replace_text(
                    ADL, "scripts/lib/findings_export.mjs", "FINDINGS_SCHEMA_VERSION = 1", "FINDINGS_SCHEMA_VERSION = 2"
                )
            ]
        )
        f = one(head, "P2", "R2", "schema_version")
        assert f.status is Status.INCONSISTENT and f.impact is Impact.BREAKS and f.severity is Severity.HIGH
        assert (f.comparison.left, f.comparison.right) == (2, 1)
        assert f.affected_repos == ("adt",)
        assert [s.repo for s in f.fix_sites] == ["adt", "adl"]  # consumer accepts both first, then the producer
        assert one(head, "P2", "R2", "key_set").status is Status.CONSISTENT
        assert _introduced(base, head) == {f.fingerprint}

    def test_u2_vision_model_change_is_not_pulled_by_homelab(self, base):
        head = scan(
            [replace_text(CHAT, "src/lib/conversations.js", "VISION_MODEL = 'llava:7b'", "VISION_MODEL = 'llava:13b'")]
        )
        f = one(head, "P3", "R7", "const:VISION_MODEL")
        assert f.status is Status.INCONSISTENT and f.impact is Impact.BREAKS and f.affected_repos == ("chat",)
        assert f.comparison.left == "llava:13b" and "llava:7b" in f.comparison.right
        assert one(head, "P3", "R7", "const:TEXT_MODEL").status is Status.CONSISTENT
        assert _introduced(base, head) == {f.fingerprint}

    def test_u3_a_new_service_claiming_244_collides_with_ollama_chat(self, base):
        service = (
            "apiVersion: v1\nkind: Service\nmetadata:\n  name: newsvc\n  annotations:\n"
            '    metallb.io/loadBalancerIPs: "192.168.1.244"\nspec:\n  type: LoadBalancer\n'
        )
        head = scan([add_file(HOMELAB, "apps/newsvc/service.yaml", service)])
        f = one(head, "P4", "R8", "address:192.168.1.244")
        assert f.status is Status.INCONSISTENT and f.impact is Impact.BREAKS and f.severity is Severity.HIGH
        assert f.comparison.result == "duplicate" and set(f.affected_repos) == {"homelab", "chat"}
        sites = {(d["repo"], d["path"]) for d in f.detail["claimed_by"]}
        assert sites == {("homelab", "apps/newsvc/service.yaml"), ("chat", "k8s/service.yaml")}
        assert _introduced(base, head) == {f.fingerprint}

    def test_u3_variant_an_address_outside_the_pool_is_reported(self):
        service = 'kind: Service\nmetadata:\n  annotations:\n    metallb.io/loadBalancerIPs: "192.168.1.251"\n'
        f = one(scan([add_file(HOMELAB, "apps/newsvc/service.yaml", service)]), "P4", "R8", "address:192.168.1.251")
        assert f.status is Status.INCONSISTENT and f.comparison.result == "out_of_pool"

    def test_u4_renaming_an_adt_adr_dangles_the_link_in_adl(self, base):
        from repo_orchestrator.collector.overlay import rename_path

        head = scan([rename_path(ADT, "docs/adr/ADR-001-human-as-gate.md", "docs/adr/ADR-001-human-gate.md")])
        f = one(head, "P5", "R9", "ref:blob:adt:docs/adr/ADR-001-human-as-gate.md")
        assert f.status is Status.INCONSISTENT and f.impact is Impact.FALSIFIES and f.severity is Severity.MEDIUM
        assert f.affected_repos == ("adl",) and f.detail["found_type"] is None and f.detail["lines"] == [51]
        assert f.fix_sites[0].repo == "adl" and f.fix_sites[0].path == "docs/autonomy-levels.md"
        assert len(statuses(head, Status.INCONSISTENT)) == len(statuses(base, Status.INCONSISTENT)) + 1

    def _u5(self, extra_readme: bool = True):
        muts = [add_file(AP, "patterns/new-pattern/README.md", "# new pattern\n")]
        if extra_readme:
            muts.append(replace_text(AP, "README.md", "**20 patterns", "**21 patterns"))
        return muts

    def test_u5_on_a_coherent_base_a_new_pattern_makes_the_profile_stale_again(self):
        corrected = [replace_text(PROFILE, "README.md", "17 multi-agent patterns", "20 multi-agent patterns")]
        base = scan(corrected)
        assert find(base, "P1", "R1.ap", "claim:patterns")[0].status is Status.CONSISTENT  # the correction fixed D1
        head = scan(corrected + self._u5())
        f = one(head, "P1", "R1.ap", "claim:patterns")
        assert f.status is Status.INCONSISTENT and (f.comparison.left, f.comparison.right) == (20, 21)
        assert (
            f.detail["attribution"] == "claimant" and f.fix_sites[0].repo == "profile"
        )  # a defect in the profile, not in ap
        assert classify(base, head)[f.fingerprint] == "introduced"

    def test_u5_on_the_raw_baseline_the_finding_is_pre_existing_not_introduced(self, base):
        head = scan(self._u5())
        f = one(head, "P1", "R1.ap", "claim:patterns")
        d1 = one(base, "P1", "R1.ap", "claim:patterns")
        assert (f.comparison.left, f.comparison.right) == (17, 21)
        assert f.fingerprint == d1.fingerprint  # same inconsistency as D1
        assert classify(base, head)[f.fingerprint] == "pre-existing"

    def test_u5_without_updating_the_ap_readme_the_root_cause_moves_upstream(self):
        corrected = [replace_text(PROFILE, "README.md", "17 multi-agent patterns", "20 multi-agent patterns")]
        f = one(scan(corrected + self._u5(extra_readme=False)), "P1", "R1.ap", "claim:patterns")
        assert f.detail["attribution"] == "upstream" and [s.repo for s in f.fix_sites] == ["ap", "profile"]


# ---------------------------------------------------------------------------------------------------
# Q1-Q6: unresolved decisions are reported, never decided
# ---------------------------------------------------------------------------------------------------


class TestOpenQuestions:
    def test_q1_homelab_and_chat_are_asked_about_never_accused(self, base):
        for rel in ("R5.homelab", "R5.chat"):
            o = one(base, "P6", rel, "plugin_keys")
            assert o.status is Status.POLICY_CLARIFICATION and o.open_questions == ("Q1",)
            assert not [x for x in find(base, "P6", rel) if x.status is Status.INCONSISTENT]

    def test_q2_is_named_on_the_adr_count_finding(self, base):
        assert one(base, "P1", "R1.adl", "claim:adrs").open_questions == ("Q2",)

    def test_q3_is_a_parameter_whose_value_the_owner_decides(self):
        from support import with_policy_params

        decl = load_decl()
        exact = scan(decl=with_policy_params(decl, "P6", file_identity="exact_file"))
        f = one(exact, "P6", "R5.adl", "file_identity")
        assert f.status is Status.INCONSISTENT and f.open_questions == ()
        lenient = scan(decl=with_policy_params(decl, "P6", file_identity="plugin_keys_only"))
        assert one(lenient, "P6", "R5.adl", "file_identity").status is Status.CONSISTENT

    def test_q4_q5_no_m1_policy_depends_on_them(self, base):
        by_id = {q["id"]: q for q in base.semantic["open_questions"]}
        assert by_id["Q4"]["referenced_by"] == [] and by_id["Q5"]["referenced_by"] == []
        assert all(q["status"] == "open" for q in by_id.values())  # the engine never answers a question

    def test_q1_q2_q3_are_referenced_by_the_outcomes_they_affect(self, base):
        by_id = {q["id"]: q for q in base.semantic["open_questions"]}
        assert "P1/R1.adl/claim:adrs" in by_id["Q2"]["referenced_by"]
        assert "P6/R5.adl/file_identity" in by_id["Q3"]["referenced_by"]
        assert {"P6/R5.homelab/plugin_keys", "P6/R5.chat/plugin_keys"} <= set(by_id["Q1"]["referenced_by"])

    def test_q6_a_private_repository_is_out_of_scope(self):
        from repo_orchestrator.collector.source import SourceError
        from support import baseline_source

        class PrivateChat:
            def __init__(self, inner):
                self.inner = inner

            def resolve(self, slug, ref):
                if slug == CHAT:
                    raise SourceError(Cause.PRIVATE_REPOSITORY, "private")
                return self.inner.resolve(slug, ref)

            def tree(self, slug, sha):
                return self.inner.tree(slug, sha)

            def read(self, slug, sha, entry):
                return self.inner.read(slug, sha, entry)

        report = scan(source=PrivateChat(baseline_source()))
        causes = {
            o.cause
            for o in report.result.outcomes
            if o.status is Status.CANNOT_EVALUATE and o.relationship_id in {"R7", "R8"}
        }
        assert Cause.PRIVATE_REPOSITORY in causes and report.exit_code == 2
        assert not [o for o in find(report, "P3", "R7") if o.status is Status.CONSISTENT]


# ---------------------------------------------------------------------------------------------------
# Collection failures: incomplete evidence is reported, never a pass
# ---------------------------------------------------------------------------------------------------


class TestCollectionFailures:
    def test_a_repository_that_cannot_be_collected_makes_every_dependent_policy_cannot_evaluate(self):
        from repo_orchestrator.collector.source import SourceError
        from support import baseline_source

        inner = baseline_source()

        class Gone:
            def resolve(self, slug, ref):
                if slug == HOMELAB:
                    raise SourceError(Cause.REPOSITORY_NOT_FOUND, "404")
                return inner.resolve(slug, ref)

            def tree(self, slug, sha):
                return inner.tree(slug, sha)

            def read(self, slug, sha, entry):
                return inner.read(slug, sha, entry)

        report = scan(source=Gone())
        assert report.exit_code == 2 and report.semantic["run_status"] == "partial"
        for policy, rel in (("P3", "R7"), ("P4", "R8")):
            out = find(report, policy, rel)
            assert out and all(
                o.status is Status.CANNOT_EVALUATE and o.cause == Cause.REPOSITORY_NOT_FOUND for o in out
            )
        assert not [o for o in find(report, "P1", "R1.homelab") if o.status is Status.CONSISTENT]
        assert report.semantic["collection_failures"]
        # findings that do not depend on the missing repository are still reported
        assert one(report, "P1", "R1.ap", "claim:patterns").status is Status.INCONSISTENT

    def test_a_missing_artifact_is_cannot_evaluate_not_a_pass(self):
        from repo_orchestrator.collector.overlay import delete_path

        report = scan([delete_path(CHAT, "src/lib/conversations.js")])
        out = find(report, "P3", "R7")
        assert {o.status for o in out} == {Status.CANNOT_EVALUATE} and {o.cause for o in out} == {Cause.NOT_FOUND}
        assert report.exit_code == 2

    def test_unreadable_scope_selection_limits_what_is_evaluated(self):
        report = scan(scope=("adl", "adt"))
        rels = {o.relationship_id for o in report.result.outcomes}
        assert rels == {"R2", "R3", "R5.adl", "R9"}
        statuses_by_rel = {c["relationship_id"]: c["status"] for c in report.semantic["coverage"]}
        assert statuses_by_rel["R7"] == "outside_scope" and statuses_by_rel["R5.fullstack-pilot"] == "not_analyzed"


def test_as_of_is_part_of_the_declared_inputs():
    assert scan().semantic["inputs"]["as_of"] == AS_OF.isoformat()
