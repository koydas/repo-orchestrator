"""Policy behaviour beyond the baseline: variants produced by in-memory mutations of the pinned snapshots."""

from __future__ import annotations

import json

import pytest

from repo_orchestrator.collector.overlay import add_file, delete_path, rename_path, replace_text
from repo_orchestrator.models import Cause, Status
from support import find, load_decl, one, scan, with_contract

ADL, ADT, AP, PROFILE, HOMELAB, CHAT = (
    "koydas/autonomous-dev-loop", "koydas/ai-dev-tools", "koydas/agent-patterns", "koydas/koydas",
    "koydas/gitops-homelab", "koydas/ollama-chat",
)  # fmt: skip
HL_SHA = "a8a9d7db5e725fd47061e8ae7fbfa70d81be3603"


# ---- P1 ---------------------------------------------------------------------------------------------------------


class TestP1:
    def test_correcting_the_claim_makes_it_consistent(self):
        r = scan([replace_text(PROFILE, "README.md", "59 ADRs", "65 ADRs")])
        assert one(r, "P1", "R1.adl", "claim:adrs").status is Status.CONSISTENT

    def test_a_claim_the_regex_cannot_find_is_cannot_evaluate(self):
        r = scan([replace_text(PROFILE, "README.md", "17 multi-agent patterns", "seventeen multi-agent patterns")])
        o = one(r, "P1", "R1.ap", "claim:patterns")
        assert o.status is Status.CANNOT_EVALUATE and o.cause == Cause.LOCATOR_NO_MATCH and r.exit_code == 2

    def test_two_matches_on_the_claim_line_are_ambiguous_not_guessed(self):
        r = scan(
            [
                replace_text(
                    PROFILE,
                    "README.md",
                    "17 multi-agent patterns indexed",
                    "17 multi-agent patterns indexed (18 multi-agent patterns)",
                )
            ]
        )
        assert one(r, "P1", "R1.ap", "claim:patterns").cause == Cause.LOCATOR_AMBIGUOUS

    def test_upstream_that_disagrees_with_both_names_both_fix_sites(self):
        r = scan([replace_text(ADL, "README.md", "[59 ADRs]", "[60 ADRs]")])
        f = one(r, "P1", "R1.adl", "claim:adrs")
        assert f.detail["attribution"] == "both" and [s.repo for s in f.fix_sites] == ["adl", "profile"]

    def test_an_unreadable_upstream_does_not_hide_the_finding_or_invent_attribution(self):
        f = one(scan([delete_path(ADL, "README.md")]), "P1", "R1.adl", "claim:adrs")
        assert f.status is Status.INCONSISTENT and f.detail["attribution"] == "claimant_upstream_unreadable"
        assert [s.repo for s in f.fix_sites] == ["profile"]

    def test_a_claim_equal_to_an_alternative_counting_rule_is_a_question_not_a_finding(self):
        # 64 numbered files, 65 index entries, the profile says 65: which rule is right is Q2, an owner decision
        r = scan(
            [
                delete_path(ADL, "docs/adr/0065-adversarial-protocol-by-coverage.md"),
                replace_text(PROFILE, "README.md", "59 ADRs", "65 ADRs"),
            ]
        )
        o = one(r, "P1", "R1.adl", "claim:adrs")
        assert o.status is Status.POLICY_CLARIFICATION and o.open_questions == ("Q2",) and "open (Q2)" in o.reason

    def test_a_heading_count_is_a_deterministic_count_of_h3_under_the_declared_h2(self):
        r = scan(
            [
                replace_text(
                    HOMELAB,
                    "docs/runbook.md",
                    "## Symptom → Probable Cause",
                    "### One more incident\n\n## Symptom → Probable Cause",
                )
            ]
        )
        f = one(r, "P1", "R1.homelab", "claim:incidents")
        assert f.status is Status.INCONSISTENT and (f.comparison.left, f.comparison.right) == (13, 14)

    def test_a_missing_runbook_section_is_cannot_evaluate(self):
        r = scan([replace_text(HOMELAB, "docs/runbook.md", "## Incidents Hit While Building This", "## Incidents")])
        assert one(r, "P1", "R1.homelab", "claim:incidents").cause == Cause.LOCATOR_NO_MATCH

    def test_an_authority_that_does_not_resolve_stops_the_policy_for_the_run(self):
        r = scan([replace_text(PROFILE, "CLAUDE.md", "must match the repos' own READMEs", "should resemble")])
        outs = find(r, "P1", "R1.adl")
        assert {o.status for o in outs} == {Status.CANNOT_EVALUATE} and outs[0].cause == Cause.AUTHORITY_UNRESOLVED
        assert not [
            o for o in r.result.outcomes if o.policy_id == "P1" and o.status is Status.INCONSISTENT
        ]  # no policy, no finding


# ---- P2 ---------------------------------------------------------------------------------------------------------


class TestP2:
    def test_a_key_the_consumer_reads_but_the_producer_no_longer_emits_breaks_it(self):
        r = scan([replace_text(ADL, "scripts/lib/findings_export.mjs", "    gaps: (diagnosis", "    gapz: (diagnosis")])
        f = one(r, "P2", "R2", "key_set")
        assert f.status is Status.INCONSISTENT and f.detail["consumer_keys_missing_in_producer"] == ["gaps"]
        assert (
            f.detail["producer_only_keys"] == ["gapz"]
            and one(r, "P2", "R2", "schema_version").status is Status.CONSISTENT
        )

    def test_extra_producer_keys_are_additive_and_not_a_finding(self):
        r = scan(
            [
                replace_text(
                    ADL,
                    "scripts/lib/findings_export.mjs",
                    "    gaps: (diagnosis",
                    "    extra: 1,\n    gaps: (diagnosis",
                )
            ]
        )
        o = one(r, "P2", "R2", "key_set")
        assert o.status is Status.CONSISTENT and o.detail["producer_only_keys"] == ["extra"]

    def test_the_emitted_version_is_the_one_in_the_object_not_the_unused_constant(self):
        r = scan(
            [
                replace_text(
                    ADL,
                    "scripts/lib/findings_export.mjs",
                    "schema_version: FINDINGS_SCHEMA_VERSION",
                    "schema_version: 2",
                )
            ]
        )
        f = one(r, "P2", "R2", "schema_version")
        assert f.status is Status.INCONSISTENT and (f.comparison.left, f.comparison.right) == (2, 1)
        assert any(e.locator == "emitted schema_version" and "schema_version: 2" in e.quote for e in f.evidence)

    def test_an_emitted_version_that_is_not_the_declared_constant_cannot_be_evaluated(self):
        r = scan(
            [
                replace_text(
                    ADL,
                    "scripts/lib/findings_export.mjs",
                    "schema_version: FINDINGS_SCHEMA_VERSION",
                    "schema_version: OTHER_VERSION",
                )
            ]
        )
        o = one(r, "P2", "R2", "schema_version")
        assert o.status is Status.CANNOT_EVALUATE and o.cause == Cause.LOCATOR_NO_MATCH and "OTHER_VERSION" in o.reason
        assert one(r, "P2", "R2", "key_set").status is Status.CONSISTENT

    def test_the_consumer_version_is_what_its_guard_accepts_not_its_returned_object(self):
        guard = "raw.schema_version !== SUPPORTED_SCHEMA_VERSION"
        rejects_v1 = scan([replace_text(ADT, "scripts/loop-insights.mjs", guard, "raw.schema_version !== 2")])
        f = one(rejects_v1, "P2", "R2", "schema_version")
        assert f.status is Status.INCONSISTENT and (f.comparison.left, f.comparison.right) == (1, 2)
        assert any(e.locator == "accepted schema_version" and "!== 2" in e.quote for e in f.evidence)
        # the returned property is only a normalized copy: changing it does not change what is accepted
        returned = scan(
            [
                replace_text(
                    ADT, "scripts/loop-insights.mjs", "schema_version: SUPPORTED_SCHEMA_VERSION", "schema_version: 7"
                )
            ]
        )
        assert one(returned, "P2", "R2", "schema_version").status is Status.CONSISTENT

    def test_a_guard_not_connected_to_the_declared_constant_or_missing_cannot_be_evaluated(self):
        other = scan(
            [
                replace_text(
                    ADT,
                    "scripts/loop-insights.mjs",
                    "raw.schema_version !== SUPPORTED_SCHEMA_VERSION",
                    "raw.schema_version !== LEGACY",
                )
            ]
        )
        o = one(other, "P2", "R2", "schema_version")
        assert o.status is Status.CANNOT_EVALUATE and "LEGACY" in o.reason
        gone = scan(
            [replace_text(ADT, "scripts/loop-insights.mjs", "raw.schema_version !== SUPPORTED_SCHEMA_VERSION", "false")]
        )
        assert one(gone, "P2", "R2", "schema_version").cause == Cause.LOCATOR_NO_MATCH

    def test_peer_contracts_must_agree_on_the_key_set_and_either_side_may_be_fixed(self):
        r = scan(
            [
                replace_text(
                    ADT,
                    "scripts/build-dashboard.mjs",
                    "generated_at: m.date, status, headline }",
                    "generated_at: m.date, status, headline, extra: 1 }",
                )
            ]
        )
        f = one(r, "P2", "R3", "key_set")
        assert (
            f.status is Status.INCONSISTENT
            and f.detail["only_in_second"] == ["extra"]
            and set(f.affected_repos) == {"adl", "adt"}
        )

    def test_peer_version_in_an_object_literal_must_be_an_integer(self):
        r = scan(
            [replace_text(ADT, "scripts/build-dashboard.mjs", "schema_version: 1, app", "schema_version: '1', app")]
        )
        assert one(r, "P2", "R3", "schema_version").cause == Cause.PARSE_ERROR

    def test_a_constant_that_became_an_expression_is_cannot_evaluate_for_that_subject_only(self):
        r = scan(
            [
                replace_text(
                    ADL,
                    "scripts/lib/findings_export.mjs",
                    "FINDINGS_SCHEMA_VERSION = 1;",
                    "FINDINGS_SCHEMA_VERSION = computeVersion();",
                )
            ]
        )
        assert one(r, "P2", "R2", "schema_version").cause == Cause.LOCATOR_NO_MATCH
        assert one(r, "P2", "R2", "key_set").status is Status.CONSISTENT

    def test_a_spread_in_the_literal_is_unsupported_syntax_not_a_guess(self):
        r = scan(
            [
                replace_text(
                    ADL,
                    "scripts/lib/findings_export.mjs",
                    "    schema_version: FINDINGS_SCHEMA_VERSION,",
                    "    ...base,",
                )
            ]
        )
        assert one(r, "P2", "R2", "key_set").cause == Cause.UNSUPPORTED_SYNTAX


# ---- P3 ---------------------------------------------------------------------------------------------------------


class TestP3:
    def test_unparseable_provider_yaml_is_cannot_evaluate_for_every_tag(self):
        r = scan([add_file(HOMELAB, "apps/ollama/application.yaml", "spec: [unclosed\n")])
        out = find(r, "P3", "R7")
        assert len(out) == 2 and {o.cause for o in out} == {Cause.PARSE_ERROR}

    def test_a_provider_list_that_moved_is_cannot_evaluate(self):
        r = scan(
            [add_file(HOMELAB, "apps/ollama/application.yaml", "spec:\n  source:\n    helm:\n      valuesObject: {}\n")]
        )
        assert {o.cause for o in find(r, "P3", "R7")} == {Cause.LOCATOR_NO_MATCH}

    def test_a_non_string_list_is_refused(self):
        yaml = "spec:\n  source:\n    helm:\n      valuesObject:\n        ollama:\n          models:\n            pull: [1, 2]\n"
        assert {o.cause for o in find(scan([add_file(HOMELAB, "apps/ollama/application.yaml", yaml)]), "P3", "R7")} == {
            Cause.PARSE_ERROR
        }

    def test_a_tag_that_is_only_mentioned_in_a_comment_is_not_pulled(self):
        text = '"llava:7b"'
        r = scan([replace_text(HOMELAB, "apps/ollama/application.yaml", f"- {text}", f"# {text}")])
        o = one(r, "P3", "R7", "const:VISION_MODEL")
        assert o.status is Status.INCONSISTENT and o.fix_sites[0].repo == "homelab"

    def test_a_commented_out_model_constant_is_not_the_live_one(self):
        old = "export const TEXT_MODEL = 'llama3.1:8b-instruct-q4_0'"
        r = scan(
            [
                replace_text(
                    CHAT,
                    "src/lib/conversations.js",
                    old,
                    f"/*\n{old}\n*/\nexport const TEXT_MODEL = `missing-model:latest`",
                )
            ]
        )
        o = one(r, "P3", "R7", "const:TEXT_MODEL")
        assert o.status is Status.CANNOT_EVALUATE and o.cause == Cause.LOCATOR_NO_MATCH

    def test_the_consumer_constant_must_be_a_plain_string_literal(self):
        r = scan(
            [
                replace_text(
                    CHAT,
                    "src/lib/conversations.js",
                    "TEXT_MODEL = 'llama3.1:8b-instruct-q4_0'",
                    "TEXT_MODEL = pick('x')",
                )
            ]
        )
        assert one(r, "P3", "R7", "const:TEXT_MODEL").cause == Cause.LOCATOR_NO_MATCH

    def test_a_historical_adr_naming_another_model_is_not_an_input(self):
        # chat ADR-0009 (accepted, point in time) says VISION_MODEL = 'qwen2.5vl:3b': living code decides
        assert one(scan(), "P3", "R7", "const:VISION_MODEL").status is Status.CONSISTENT


# ---- P4 ---------------------------------------------------------------------------------------------------------


SERVICE = 'kind: Service\nmetadata:\n  annotations:\n    metallb.io/loadBalancerIPs: "{}"\n'


class TestP4:
    def test_two_new_claims_of_one_address_collide_even_inside_one_repository(self):
        r = scan(
            [
                add_file(HOMELAB, "apps/a/service.yaml", SERVICE.format("192.168.1.246")),
                add_file(HOMELAB, "apps/b/service.yaml", SERVICE.format("192.168.1.246")),
            ]
        )
        o = one(r, "P4", "R8", "address:192.168.1.246")
        assert o.status is Status.INCONSISTENT and o.affected_repos == ("homelab",)

    def test_comma_separated_addresses_are_all_claims(self):
        r = scan([add_file(HOMELAB, "apps/a/service.yaml", SERVICE.format("192.168.1.246, 192.168.1.244"))])
        assert one(r, "P4", "R8", "address:192.168.1.244").status is Status.INCONSISTENT
        assert one(r, "P4", "R8", "address:192.168.1.246").status is Status.CONSISTENT

    def test_an_unreadable_claim_source_prevents_declaring_other_addresses_safe(self):
        r = scan([add_file(HOMELAB, "apps/a/service.yaml", "kind: [broken\n")])
        sites = [o for o in find(r, "P4", "R8") if o.subject.startswith("claim-site:")]
        assert sites and sites[0].cause == Cause.PARSE_ERROR
        addresses = [o for o in find(r, "P4", "R8") if o.subject.startswith("address:")]
        assert addresses and all(o.status is Status.CANNOT_EVALUATE for o in addresses)
        assert r.exit_code == 2

    def test_a_collision_is_still_reported_when_another_source_is_unreadable(self):
        r = scan(
            [
                add_file(HOMELAB, "apps/a/service.yaml", "kind: [broken\n"),
                add_file(HOMELAB, "apps/b/service.yaml", SERVICE.format("192.168.1.244")),
            ]
        )
        assert one(r, "P4", "R8", "address:192.168.1.244").status is Status.INCONSISTENT

    def test_a_range_or_hostname_is_unsupported_syntax(self):
        r = scan([add_file(HOMELAB, "apps/a/service.yaml", SERVICE.format("192.168.1.246-192.168.1.247"))])
        assert [o.cause for o in find(r, "P4", "R8") if o.subject.startswith("claim-site:")] == [
            Cause.UNSUPPORTED_SYNTAX
        ]

    @pytest.mark.parametrize(
        ("pool", "cause"),
        [
            ("[]", None),
            ("[a-b]", Cause.UNSUPPORTED_SYNTAX),
            ("[192.168.1.250-192.168.1.240]", Cause.PARSE_ERROR),
            ("nope", Cause.PARSE_ERROR),
        ],
    )
    def test_pool_definitions_are_validated(self, pool, cause):
        yaml = f"kind: IPAddressPool\nspec:\n  addresses: {pool}\n"
        r = scan([add_file(HOMELAB, "apps/metallb-config/ipaddresspool.yaml", yaml)])
        out = find(r, "P4", "R8")
        if cause is None:  # an empty pool puts every claim outside of it
            assert all(o.status is Status.INCONSISTENT for o in out)
        else:
            assert len(out) == 1 and out[0].subject == "pool" and out[0].cause == cause

    def test_a_cidr_pool_is_understood(self):
        yaml = "kind: IPAddressPool\nspec:\n  addresses:\n    - 192.168.1.240/28\n"
        r = scan([add_file(HOMELAB, "apps/metallb-config/ipaddresspool.yaml", yaml)])
        assert all(o.status is Status.CONSISTENT for o in find(r, "P4", "R8"))

    @pytest.mark.parametrize(
        "claim",
        [
            "spec:\n  loadBalancerIP: 192.168.1.243\n",
            "metadata:\n  annotations:\n    metallb.universe.tf/loadBalancerIPs: 192.168.1.243\n",
        ],
    )
    def test_other_claim_forms_are_never_silently_skipped(self, claim):
        r = scan([add_file(CHAT, "k8s/service.yaml", "kind: Service\n" + claim)])
        assert [o.cause for o in find(r, "P4", "R8") if o.subject.startswith("claim-site:")] == [
            Cause.UNSUPPORTED_SYNTAX
        ]
        assert all(o.status is Status.CANNOT_EVALUATE for o in find(r, "P4", "R8") if o.subject.startswith("address:"))
        assert r.exit_code == 2

    def test_an_aliased_claim_counts_at_every_use_site(self):
        yaml = (
            "kind: List\nitems:\n"
            "  - kind: Service\n    metadata:\n      name: a\n      annotations: &ip {metallb.io/loadBalancerIPs: '192.168.1.246'}\n"
            "  - kind: Service\n    metadata:\n      name: b\n      annotations: *ip\n"
        )
        o = one(scan([add_file(CHAT, "k8s/alias.yaml", yaml)]), "P4", "R8", "address:192.168.1.246")
        assert o.status is Status.INCONSISTENT and o.comparison.result == "duplicate"

    def test_yaml_merge_keys_are_resolved_to_the_effective_claim(self):
        def services(second: str) -> str:
            return (
                "kind: List\nitems:\n"
                "  - kind: Service\n    metadata:\n      name: a\n      annotations: &ip {metallb.io/loadBalancerIPs: '192.168.1.246'}\n"
                f"  - kind: Service\n    metadata:\n      name: b\n      annotations:\n        <<: *ip\n{second}"
            )

        overridden = scan(
            [add_file(CHAT, "k8s/merged.yaml", services("        metallb.io/loadBalancerIPs: '192.168.1.247'\n"))]
        )
        assert one(overridden, "P4", "R8", "address:192.168.1.246").status is Status.CONSISTENT
        assert one(overridden, "P4", "R8", "address:192.168.1.247").status is Status.CONSISTENT
        inherited = scan([add_file(CHAT, "k8s/merged.yaml", services("        other: x\n"))])
        assert one(inherited, "P4", "R8", "address:192.168.1.246").comparison.result == "duplicate"

    HELM = (
        "apiVersion: argoproj.io/v1alpha1\nkind: Application\nspec:\n  source:\n    helm:\n      values: |\n"
        "        controller:\n          service:\n            annotations:\n"
        '              metallb.io/loadBalancerIPs: "{ip}"\n'
    )

    def test_claims_inside_embedded_helm_values_are_read_with_outer_file_evidence(self):
        r = scan([add_file(HOMELAB, "apps/other/application.yaml", self.HELM.format(ip="192.168.1.243"))])
        o = one(r, "P4", "R8", "address:192.168.1.243")
        assert o.status is Status.INCONSISTENT and o.comparison.result == "duplicate"
        embedded = next(e for e in o.evidence if e.path == "apps/other/application.yaml")
        assert embedded.line_start == 10 and embedded.quote.strip().startswith("metallb.io/loadBalancerIPs")
        fresh = scan([add_file(HOMELAB, "apps/other/application.yaml", self.HELM.format(ip="192.168.1.246"))])
        assert one(fresh, "P4", "R8", "address:192.168.1.246").status is Status.CONSISTENT

    @pytest.mark.parametrize(
        "text",
        [
            "spec:\n  values: >\n    metallb.io/loadBalancerIPs: '192.168.1.243'\n",
            "spec:\n  values: 'metallb.io/loadBalancerIPs: 192.168.1.243'\n",
            "spec:\n  values: |\n    broken: [unclosed\n    metallb.io/loadBalancerIPs: x\n",
        ],
    )
    def test_embedded_text_that_cannot_be_read_as_yaml_is_never_skipped(self, text):
        r = scan([add_file(HOMELAB, "apps/other/application.yaml", text)])
        sites = [o for o in find(r, "P4", "R8") if o.subject.startswith("claim-site:")]
        assert sites and sites[0].status is Status.CANNOT_EVALUATE and r.exit_code == 2

    def test_overlapping_claim_source_globs_read_each_file_once(self):
        decl = with_contract(
            load_decl(),
            "R8",
            lambda c: c["allocation"]["claim_sources"].extend(
                [
                    {"repo": "homelab", "glob": "apps/whisper/service.yaml"},
                    {"repo": "homelab", "glob": "apps/ingress-nginx/*.yaml"},
                ]
            ),
        )
        r = scan(decl=decl)
        assert all(o.status is Status.CONSISTENT for o in find(r, "P4", "R8")) and len(find(r, "P4", "R8")) == 3

    def test_an_empty_legacy_field_claims_nothing(self):
        r = scan([add_file(CHAT, "k8s/extra.yaml", 'kind: Service\nspec:\n  loadBalancerIP: ""\n')])
        assert all(o.status is Status.CONSISTENT for o in find(r, "P4", "R8"))

    def test_a_non_string_annotation_value_is_refused(self):
        r = scan(
            [
                add_file(
                    HOMELAB, "apps/a/service.yaml", "metadata:\n  annotations:\n    metallb.io/loadBalancerIPs: [1]\n"
                )
            ]
        )
        assert [o.cause for o in find(r, "P4", "R8") if o.subject.startswith("claim-site:")] == [
            Cause.UNSUPPORTED_SYNTAX
        ]

    def test_no_claims_at_all_is_stated_not_assumed(self):
        r = scan(
            [
                delete_path(HOMELAB, "apps/whisper/service.yaml"),
                delete_path(HOMELAB, "apps/ingress-nginx"),
                delete_path(CHAT, "k8s/service.yaml"),
            ]
        )
        (o,) = find(r, "P4", "R8")
        assert o.subject == "claims" and "no address claims" in o.reason


# ---- P5 ---------------------------------------------------------------------------------------------------------


class TestP5:
    def test_a_branch_that_is_not_the_pinned_snapshot_cannot_be_evaluated(self):
        r = scan([replace_text(PROFILE, "README.md", "blob/main/docs/adr/0052", "blob/feature-x/docs/adr/0052")])
        o = one(r, "P5", "R1.adl", "ref:blob:adl:docs/adr/0052-isolated-grader-for-executable-evals.md")
        assert o.status is Status.CANNOT_EVALUATE and o.cause == Cause.REF_NOT_PINNED

    def test_a_link_pinned_to_the_exact_commit_is_checked_at_that_commit(self):
        r = scan([replace_text(PROFILE, "README.md", "blob/main/docs/adr/0011", f"blob/{HL_SHA}/docs/adr/0011")])
        assert (
            one(r, "P5", "R1.homelab", "ref:blob:homelab:docs/adr/0011-ollama-q4-quantization.md").status
            is Status.CONSISTENT
        )

    def test_directory_links_use_tree_and_a_blob_link_to_a_directory_is_dangling(self):
        line = "\n[adrs](https://github.com/koydas/gitops-homelab/tree/main/docs/adr) [bad](https://github.com/koydas/gitops-homelab/blob/main/docs/adr)\n"
        r = scan(
            [
                add_file(PROFILE, "README.md", "x")
                if False
                else replace_text(PROFILE, "README.md", "### Current focus", f"### Current focus\n{line}")
            ]
        )
        assert one(r, "P5", "R1.homelab", "ref:tree:homelab:docs/adr").status is Status.CONSISTENT
        bad = one(r, "P5", "R1.homelab", "ref:blob:homelab:docs/adr")
        assert bad.status is Status.INCONSISTENT and bad.detail["found_type"] == "tree"

    def test_a_historical_source_is_not_an_operational_contract(self):
        broken = [rename_path(ADT, "docs/adr/ADR-001-human-as-gate.md", "docs/adr/ADR-001-renamed.md")]
        live = scan(broken)
        assert one(live, "P5", "R9", "ref:blob:adt:docs/adr/ADR-001-human-as-gate.md").status is Status.INCONSISTENT
        decl = with_contract(
            scan().result and __import__("support").load_decl(),
            "R9",
            lambda c: c["references"][0].update(role="historical"),
        )
        historical = scan(broken, decl=decl)
        assert not find(historical, "P5", "R9")  # skipped, not failed and not a finding
        assert not [
            o for o in historical.result.outcomes if o.status is Status.INCONSISTENT and o.relationship_id == "R9"
        ]

    def test_a_source_document_that_disappeared_is_cannot_evaluate(self):
        r = scan([delete_path(ADL, "docs/autonomy-levels.md")])
        out = find(r, "P5", "R9")
        assert len(out) == 1 and out[0].cause == Cause.NOT_FOUND

    def test_a_document_without_links_is_reported_as_zero_references_not_skipped(self):
        o = one(scan(), "P5", "R1.adt", "source:profile:README.md")
        assert o.status is Status.CONSISTENT and o.detail["references"] == 0

    def test_repeated_links_are_one_subject_with_every_line_recorded(self):
        link = "https://github.com/koydas/ai-dev-tools/blob/main/docs/adr/ADR-001-human-as-gate.md"
        r = scan(
            [
                replace_text(
                    ADL, "docs/autonomy-levels.md", "## Primitive mapping", f"## Primitive mapping\n\nsee {link}\n"
                )
            ]
        )
        o = one(r, "P5", "R9", "ref:blob:adt:docs/adr/ADR-001-human-as-gate.md")
        assert o.status is Status.CONSISTENT and len(o.detail["lines"]) == 2


# ---- P6 ---------------------------------------------------------------------------------------------------------


KEYS = {
    "extraKnownMarketplaces": {"ai-dev-tools": {"source": {"source": "github", "repo": "koydas/ai-dev-tools"}}},
    "enabledPlugins": {"ai-dev-tools@ai-dev-tools": True},
}


def settings(**changes) -> str:
    return json.dumps({**KEYS, **changes}, indent=2) + "\n"


class TestP6:
    def test_an_in_scope_repository_without_settings_is_a_finding(self):
        f = one(scan([delete_path(AP, ".claude/settings.json")]), "P6", "R5.ap", "plugin_keys")
        assert f.status is Status.INCONSISTENT and f.fix_sites[0].path == ".claude/settings.json"

    def test_true_is_not_one(self):
        r = scan(
            [
                replace_text(
                    AP, ".claude/settings.json", '"ai-dev-tools@ai-dev-tools": true', '"ai-dev-tools@ai-dev-tools": 1'
                )
            ]
        )
        f = one(r, "P6", "R5.ap", "plugin_keys")
        assert f.status is Status.INCONSISTENT and f.detail["differing_keys"] == ["enabledPlugins"]

    def test_a_different_marketplace_repo_is_a_finding(self):
        r = scan([replace_text(AP, ".claude/settings.json", "koydas/ai-dev-tools", "someone/else")])
        assert one(r, "P6", "R5.ap", "plugin_keys").detail["differing_keys"] == ["extraKnownMarketplaces"]

    def test_json_formatting_never_changes_the_outcome(self):
        minified = json.dumps(KEYS, separators=(",", ":"))
        for text in (
            minified,
            "{" + minified[1:],
            json.dumps({**KEYS, "permissions": {"allow": []}}, separators=(",", ":")),
        ):
            r = scan([add_file(AP, ".claude/settings.json", text)])
            assert one(r, "P6", "R5.ap", "plugin_keys").status is Status.CONSISTENT
            assert one(r, "P6", "R5.ap", "adopter_table").status is Status.CONSISTENT
            assert not [o for o in r.result.outcomes if o.status is Status.FAILED]

    def test_malformed_settings_are_cannot_evaluate(self):
        r = scan([add_file(AP, ".claude/settings.json", "{ not json")])
        assert one(r, "P6", "R5.ap", "plugin_keys").cause == Cause.PARSE_ERROR
        r = scan([add_file(AP, ".claude/settings.json", "[1]")])
        assert one(r, "P6", "R5.ap", "plugin_keys").cause == Cause.PARSE_ERROR

    def test_unrelated_repository_specific_settings_never_fail_the_plugin_keys_check(self):
        r = scan([add_file(AP, ".claude/settings.json", settings(permissions={"allow": ["Bash(ls)"]}))])
        assert one(r, "P6", "R5.ap", "plugin_keys").status is Status.CONSISTENT
        assert one(r, "P6", "R5.ap", "file_identity").status is Status.POLICY_CLARIFICATION

    def test_the_owner_decision_on_q3_changes_the_outcome_through_a_parameter_only(self):
        from support import with_policy_params

        mut = [add_file(AP, ".claude/settings.json", settings(permissions={}))]
        assert (
            one(
                scan(mut, decl=with_policy_params(load_decl(), "P6", file_identity="exact_file")),
                "P6",
                "R5.ap",
                "file_identity",
            ).status
            is Status.INCONSISTENT
        )
        assert (
            one(
                scan(mut, decl=with_policy_params(load_decl(), "P6", file_identity="plugin_keys_only")),
                "P6",
                "R5.ap",
                "file_identity",
            ).status
            is Status.CONSISTENT
        )

    @pytest.mark.parametrize("old,new", [("| Repo |", "| Repository |"), ("| Skills |", "| Procedures |")])
    def test_an_unsupported_table_layout_is_not_evidence_of_a_missing_adopter(self, old, new):
        r = scan([replace_text(ADT, "docs/consumer-repos.md", old, new)])
        outs = find(r, "P6", "R5.ap", "adopter_table")
        assert [o.status for o in outs] == [Status.CANNOT_EVALUATE] and outs[0].cause == Cause.LOCATOR_NO_MATCH
        assert not [o for o in r.result.outcomes if o.policy_id == "P6" and o.status is Status.INCONSISTENT]

    def test_an_adopter_missing_from_the_table_is_a_finding_at_the_registry(self):
        row = "| `agent-patterns` | `new-pattern` | `docs/claude-code.md` |\n"
        f = one(scan([replace_text(ADT, "docs/consumer-repos.md", row, "")]), "P6", "R5.ap", "adopter_table")
        assert f.status is Status.INCONSISTENT and f.fix_sites[0].repo == "adt" and "not listed" in f.reason

    def test_skills_that_the_table_does_not_list_are_a_finding(self):
        f = one(
            scan([add_file(AP, ".claude/skills/extra/SKILL.md", "---\nname: extra\n---\n")]),
            "P6",
            "R5.ap",
            "adopter_table",
        )
        assert f.status is Status.INCONSISTENT and f.comparison.right == ["extra", "new-pattern"]

    def test_a_missing_adopter_table_is_cannot_evaluate(self):
        r = scan([delete_path(ADT, "docs/consumer-repos.md")])
        assert one(r, "P6", "R5.ap", "adopter_table").status is Status.CANNOT_EVALUATE

    def test_an_undetermined_scope_with_correct_keys_is_simply_consistent(self):
        r = scan([add_file(CHAT, ".claude/settings.json", settings())])
        assert one(r, "P6", "R5.chat", "plugin_keys").status is Status.CONSISTENT

    def test_an_undetermined_scope_with_wrong_keys_is_still_only_a_question(self):
        r = scan([add_file(CHAT, ".claude/settings.json", settings(enabledPlugins={}))])
        assert one(r, "P6", "R5.chat", "plugin_keys").status is Status.POLICY_CLARIFICATION
        assert not [o for o in find(r, "P6", "R5.chat") if o.status is Status.INCONSISTENT]
