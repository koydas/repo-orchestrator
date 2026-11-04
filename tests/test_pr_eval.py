"""M2 offline evaluation (docs/mvp.md → M2 exit criteria): U1-U5 replayed as pull requests, plus the cases where evidence is
missing. Expectations come from docs/mvp.md and docs/research/ecosystem-baseline.md, not from the implementation's output.

Every case runs against committed fixtures (``tests/fixtures/baseline`` for the snapshots, ``tests/fixtures/pr`` for the
pull-request metadata) and never touches the network.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from pr_cases import ADL, ADT, AP, CASES, CHAT, PROFILE, PRCase
from repo_orchestrator.collector.overlay import add_file, delete_path, rename_path, replace_text
from repo_orchestrator.collector.source import SourceError
from repo_orchestrator.models import ConfigError
from repo_orchestrator.pr_report import render_pr_markdown
from repo_orchestrator.store import RunStore
from support import PRWorld, analyze, load_decl, scan


def findings(a, label):
    return a.semantic["findings"][label]


def only(a, label):
    rows = findings(a, label)
    assert len(rows) == 1, [(r["policy_id"], r["relationship_id"], r["subject"]) for r in rows]
    return rows[0]


def entry(a, relationship_id):
    return next(e for e in a.semantic["impact"] if e["relationship_id"] == relationship_id)


def adhoc(name, slug, mutations, **kw):
    """A case without a committed fixture: its PR metadata is built from the mutations."""
    case = PRCase(name, slug, 900 + len(name), tuple(mutations), **kw)
    return case, PRWorld(case, pr=case.pull_request())


# ---------------------------------------------------------------------------------------------------
# U1-U5: affected repository, impact class, severity and `introduced`
# ---------------------------------------------------------------------------------------------------


class TestMutationsReplayedAsPullRequests:
    def test_u1_a_schema_version_bump_in_adl_breaks_adt(self):
        a = analyze(CASES["u1"])
        f = only(a, "introduced")
        assert (f["policy_id"], f["relationship_id"], f["subject"]) == ("P2", "R2", "schema_version")
        assert (f["impact"], f["severity"]) == ("breaks", "high")
        e = entry(a, "R2")
        assert e["affected_repos"] == ["adt"] and e["impact"] == "breaks" and e["type"] == "runtime_contract"
        assert (f["comparison"]["left"], f["comparison"]["right"]) == (2, 1)
        assert a.semantic["summary"]["affected_repositories"] == ["adt"]
        assert a.exit_code == 1 and a.semantic["gate"]["result"] == "fail"

    def test_u2_a_vision_model_change_in_chat_is_not_pulled_by_homelab(self):
        a = analyze(CASES["u2"])
        f = only(a, "introduced")
        assert (f["policy_id"], f["relationship_id"], f["subject"]) == ("P3", "R7", "const:VISION_MODEL")
        assert (f["impact"], f["severity"]) == ("breaks", "high")
        assert entry(a, "R7")["affected_repos"] == ["homelab"] and entry(a, "R7")["impact"] == "breaks"
        assert f["comparison"]["left"] == "llava:13b"
        assert a.exit_code == 1

    def test_u3_homelab_claiming_244_collides_with_chat(self):
        a = analyze(CASES["u3"])
        f = only(a, "introduced")
        assert (f["policy_id"], f["relationship_id"], f["subject"]) == ("P4", "R8", "address:192.168.1.244")
        assert (f["impact"], f["severity"]) == ("breaks", "high")
        assert entry(a, "R8")["affected_repos"] == ["chat"]
        assert {t["kind"] for t in entry(a, "R8")["triggers"]} == {"request"}  # the new file matches a claim glob
        assert a.exit_code == 1

    def test_u4_renaming_an_adt_adr_dangles_the_link_in_adl(self):
        a = analyze(CASES["u4"])
        f = only(a, "introduced")
        assert (f["policy_id"], f["relationship_id"]) == ("P5", "R9")
        assert f["subject"] == "ref:blob:adt:docs/adr/ADR-001-human-as-gate.md"
        assert (f["impact"], f["severity"]) == ("falsifies", "medium")
        e = entry(a, "R9")
        assert e["affected_repos"] == ["adl"] and e["impact"] == "falsifies" and e["direction"] == "downstream"
        # the renamed file is a link target of an adl document: found through the reference footprint, not an anchor
        assert {t["kind"] for t in e["triggers"]} == {"reference"}
        assert a.exit_code == 1

    def test_u5_on_a_coherent_base_the_profile_is_stale_again(self):
        a = analyze(CASES["u5"])
        f = only(a, "introduced")
        assert (f["policy_id"], f["relationship_id"], f["subject"]) == ("P1", "R1.ap", "claim:patterns")
        assert (f["impact"], f["severity"]) == ("falsifies", "medium")
        assert (f["comparison"]["left"], f["comparison"]["right"]) == (20, 21)
        assert entry(a, "R1.ap")["affected_repos"] == ["profile"] and entry(a, "R1.ap")["impact"] == "falsifies"
        assert a.exit_code == 1

    def test_the_u5_coherent_base_really_is_coherent_for_that_relationship(self):
        """The case's `world` mutation is the same correction the M1 evaluation applies: 20 patterns claimed, 20 found."""
        from support import find

        base = scan([m for m in CASES["u5"].world])
        assert find(base, "P1", "R1.ap", "claim:patterns")[0].status.value == "consistent"

    def test_every_u_case_introduces_exactly_one_finding_and_nothing_else_changes(self):
        for name in ("u1", "u2", "u3", "u4", "u5"):
            a = analyze(CASES[name])
            counts = a.semantic["summary"]["deltas"]
            assert counts == {"introduced": 1, "pre-existing": 0, "resolved": 0, "undetermined": 0}, name
            assert a.semantic["run_status"] == "complete" and not a.semantic["problems"], name


# ---------------------------------------------------------------------------------------------------
# pre-existing, resolved, no impact
# ---------------------------------------------------------------------------------------------------


class TestPreExistingResolvedAndNoImpact:
    def test_u5_on_the_raw_baseline_is_pre_existing_never_blamed_on_the_pr(self):
        a = analyze(CASES["u5_raw"])
        f = only(a, "pre_existing")
        assert (f["policy_id"], f["relationship_id"], f["subject"]) == ("P1", "R1.ap", "claim:patterns")
        assert not findings(a, "introduced")
        d1 = scan().result.outcomes
        d1_fp = next(o.fingerprint for o in d1 if o.relationship_id == "R1.ap" and o.subject == "claim:patterns")
        assert f["fingerprint"] == d1_fp  # the same inconsistency as D1 (17 vs 20), now 17 vs 21
        assert (f["comparison"]["left"], f["comparison"]["right"]) == (17, 21)
        assert a.exit_code == 0 and a.semantic["gate"]["result"] == "pass"  # pre-existing never fails the gate

    def test_a_pr_that_fixes_d1_resolves_it(self):
        a = analyze(CASES["fix_d1"])
        f = only(a, "resolved")
        assert (f["policy_id"], f["relationship_id"], f["subject"]) == ("P1", "R1.ap", "claim:patterns")
        assert not findings(a, "introduced")
        assert a.exit_code == 0
        assert entry(a, "R1.ap")["direction"] == "self"  # the profile's own claim changed

    def test_a_pr_that_fixes_d2_resolves_it(self):
        a = analyze(CASES["fix_d2"])
        f = only(a, "resolved")
        assert (f["policy_id"], f["relationship_id"], f["subject"]) == ("P1", "R1.adl", "claim:adrs")
        assert not findings(a, "introduced") and a.exit_code == 0

    def test_a_fix_does_not_hide_the_other_pre_existing_finding(self):
        a = analyze(CASES["fix_d1"])
        assert [r["relationship_id"] for r in findings(a, "pre_existing")] == ["R1.adl"]  # D2 is still there
        assert a.semantic["gate"]["introduced"] == 0

    def test_a_pr_that_touches_no_anchor_has_no_impact(self):
        a = analyze(CASES["no_anchor"])
        s = a.semantic
        assert s["impact"] == [] and s["summary"]["affected_repositories"] == []
        assert s["summary"]["deltas"] == {"introduced": 0, "pre-existing": 0, "resolved": 0, "undetermined": 0}
        assert s["run_status"] == "complete" and a.exit_code == 0
        assert "R7" in s["not_touched"]  # incident relationships are listed, not silently dropped

    def test_only_the_touched_relationships_are_evaluated(self):
        a = analyze(CASES["u1"])
        assert {e["relationship_id"] for e in a.semantic["impact"]} == {"R2", "R3"}
        # the rest of the repository's relationships were never evaluated: no outcome, no finding, no cannot_evaluate
        assert a.semantic["unevaluated"] == [] and "R1.adl" in a.semantic["not_touched"]


# ---------------------------------------------------------------------------------------------------
# missing evidence is never a pass, never a false introduced / resolved
# ---------------------------------------------------------------------------------------------------


class TestMissingEvidence:
    def test_base_unavailable_is_incomplete_never_a_pass(self):
        case = CASES["u1"]
        a = analyze(
            case, world=PRWorld(case, fail={"base": SourceError("revision_unresolved", "base commit not found")})
        )
        assert (
            a.exit_code == 2 and a.semantic["run_status"] == "partial" and a.semantic["gate"]["result"] == "incomplete"
        )
        assert [(p["side"], p["cause"]) for p in a.semantic["problems"]] == [("base", "revision_unresolved")]
        assert not findings(a, "introduced") and not findings(a, "resolved")
        assert entry(a, "R2")["impact"] == "breaks"  # the potential impact is still reported from the file list

    def test_head_unavailable_is_incomplete_never_a_pass(self):
        case = CASES["u1"]
        a = analyze(
            case, world=PRWorld(case, fail={"head": SourceError("revision_unresolved", "head commit not found")})
        )
        assert a.exit_code == 2 and [(p["side"], p["cause"]) for p in a.semantic["problems"]] == [
            ("head", "revision_unresolved")
        ]
        assert a.semantic["gate"]["exit_code"] == 2

    def test_base_or_head_unavailable_is_incomplete_even_when_no_anchor_is_touched(self):
        case = CASES["no_anchor"]
        for side in ("base", "head"):
            a = analyze(case, world=PRWorld(case, fail={side: SourceError("fetch_failed", "network error")}))
            assert a.exit_code == 2, side

    def test_a_pull_request_that_cannot_be_read_is_incomplete(self):
        case = CASES["u1"]
        a = analyze(case, world=PRWorld(case, fail={"pull_request": SourceError("pull_request_not_found", "404")}))
        assert a.exit_code == 2 and a.semantic["inputs"]["pull_request"]["head_sha"] is None
        assert [(p["side"], p["cause"]) for p in a.semantic["problems"]] == [("pull_request", "pull_request_not_found")]
        assert a.semantic["impact"] == []

    def test_a_truncated_changed_file_list_is_incomplete_but_findings_stay_visible(self):
        a = analyze(CASES["truncated"])
        assert a.exit_code == 2  # takes precedence over the introduced finding it still lists
        assert [(p["side"], p["cause"]) for p in a.semantic["problems"]] == [("change_set", "pr_files_truncated")]
        assert len(findings(a, "introduced")) == 1 and a.semantic["gate"]["result"] == "incomplete"

    def test_a_file_list_that_misses_a_changed_file_is_a_mismatch_not_a_pass(self):
        case = CASES["u1"]
        pr = dataclasses.replace(case.pull_request(), files=(), changed_files=0)
        a = analyze(case, world=PRWorld(case, pr=pr))
        assert a.exit_code == 2 and [p["cause"] for p in a.semantic["problems"]] == ["pr_file_list_mismatch"]
        assert len(findings(a, "introduced")) == 1  # the trees are the evidence: the change is still found

    def test_cannot_evaluate_on_base_never_becomes_introduced(self):
        case, world = adhoc(
            "base_cannot_evaluate", ADL,
            [replace_text(ADL, "scripts/lib/findings_export.mjs", "FINDINGS_SCHEMA_VERSION = compute()", "FINDINGS_SCHEMA_VERSION = 2")],
            base=(replace_text(ADL, "scripts/lib/findings_export.mjs", "FINDINGS_SCHEMA_VERSION = 1", "FINDINGS_SCHEMA_VERSION = compute()"),),
        )  # fmt: skip
        a = analyze(case, world=world)
        f = only(a, "undetermined")
        assert (f["policy_id"], f["relationship_id"], f["subject"]) == ("P2", "R2", "schema_version")
        assert f["delta"]["base_status"] == "cannot_evaluate" and f["delta"]["head_status"] == "inconsistent"
        assert not findings(a, "introduced") and a.exit_code == 2

    def test_cannot_evaluate_on_head_never_becomes_resolved(self):
        """D1 and D2 are on base; the PR deletes the profile README, so nothing can be evaluated on head."""
        case, world = adhoc("head_cannot_evaluate", PROFILE, [delete_path(PROFILE, "README.md")])
        a = analyze(case, world=world)
        assert {(r["relationship_id"], r["subject"]) for r in findings(a, "undetermined")} == {
            ("R1.adl", "claim:adrs"),
            ("R1.ap", "claim:patterns"),
        }
        assert not findings(a, "resolved") and not findings(a, "introduced") and a.exit_code == 2
        assert all(r["delta"]["head_status"] == "cannot_evaluate" for r in findings(a, "undetermined"))

    def test_a_deleted_consumer_is_incomplete_even_with_no_finding_on_either_side(self):
        case, world = adhoc("consumer_deleted", ADT, [delete_path(ADT, "scripts/loop-insights.mjs")])
        a = analyze(case, world=world)
        assert a.exit_code == 2 and a.semantic["summary"]["head_unevaluated"] >= 1
        assert not findings(a, "introduced") and a.semantic["gate"]["result"] == "incomplete"

    def test_a_link_removed_by_the_pr_resolves_its_dangling_finding(self):
        """Base already has a dangling link (the baseline ADR is renamed on the world side); head removes the link: the
        subject is gone while P5 completed, which is `resolved`."""
        from repo_orchestrator.collector.overlay import rename_path

        renamed = rename_path(ADT, "docs/adr/ADR-001-human-as-gate.md", "docs/adr/ADR-001-human-gate.md")
        link = "https://github.com/koydas/ai-dev-tools/blob/main/docs/adr/ADR-001-human-as-gate.md"
        case = PRCase(
            "link_removed", ADL, 950, (replace_text(ADL, "docs/autonomy-levels.md", link, "ADR-001"),), world=(renamed,)
        )
        a = analyze(case, world=PRWorld(case, pr=case.pull_request()))
        # the claimant (adl) is the PR repository; the other repositories carry the rename
        assert [r["subject"] for r in findings(a, "resolved")] == ["ref:blob:adt:docs/adr/ADR-001-human-as-gate.md"]
        assert not findings(a, "introduced") and a.exit_code == 0


# ---------------------------------------------------------------------------------------------------
# configuration errors
# ---------------------------------------------------------------------------------------------------


class TestConfiguration:
    def test_a_repository_outside_the_declared_set_is_a_configuration_error(self):
        case = PRCase("stranger", "someone/else", 1, ())
        with pytest.raises(ConfigError, match="not a declared repository"):
            analyze_with(case)

    def test_a_declared_but_not_analyzed_repository_is_a_configuration_error(self):
        case = PRCase("self", "koydas/repo-orchestrator", 1, ())
        with pytest.raises(ConfigError, match="outside the analyzed scope"):
            analyze_with(case)

    def test_slugs_are_matched_case_insensitively_like_github(self):
        case = CASES["no_anchor"]
        a = analyze(case, world=PRWorld(case))
        from repo_orchestrator.engine import Engine
        from repo_orchestrator.pr_runner import analyze_pull_request
        from support import pr_options

        b = analyze_pull_request(Engine(load_decl()), PRWorld(case), CHAT.upper(), case.number, pr_options())
        assert (
            b.semantic["inputs"]["pull_request"]["slug"] == CHAT
            and b.semantic["result_digest"] == a.semantic["result_digest"]
        )


def analyze_with(case):
    from repo_orchestrator.engine import Engine
    from repo_orchestrator.pr_runner import analyze_pull_request
    from support import pr_options

    return analyze_pull_request(Engine(load_decl()), PRWorld(CASES["u1"]), case.slug, case.number, pr_options())


# ---------------------------------------------------------------------------------------------------
# determinism, persistence, reports
# ---------------------------------------------------------------------------------------------------


class TestDeterminismAndPersistence:
    @pytest.mark.parametrize("name", sorted(CASES))
    def test_repeated_runs_give_an_identical_semantic_report(self, name):
        first, second = analyze(CASES[name]), analyze(CASES[name])
        assert first.semantic == second.semantic
        assert json.dumps(first.semantic, sort_keys=True) == json.dumps(second.semantic, sort_keys=True)
        assert render_pr_markdown(first.semantic) == render_pr_markdown(second.semantic)
        assert first.execution["run_id"] != second.execution["run_id"]  # volatile data is outside the semantic part

    def test_the_semantic_report_holds_nothing_volatile(self):
        text = json.dumps(analyze(CASES["u1"]).semantic)
        for key in ("run_id", "started_at", "duration_ms", "api_requests", '"state"', "head_ref"):
            assert key not in text

    def test_the_report_distinguishes_the_inputs_that_change_the_result(self):
        u1, u2 = analyze(CASES["u1"]), analyze(CASES["u2"])
        assert u1.semantic["result_digest"] != u2.semantic["result_digest"]
        assert u1.semantic["inputs"]["pull_request"]["head_sha"] != u2.semantic["inputs"]["pull_request"]["head_sha"]

    def test_the_run_is_recorded_idempotently_and_leaves_the_baseline_history_untouched(self):
        store = RunStore(":memory:")
        scan(store=store)
        history_before = (store.findings(), store.run_count())
        first = analyze(CASES["u1"], store=store)
        second = analyze(CASES["u1"], store=store)
        assert first.record.new and not second.record.new and first.record.run_key == second.record.run_key
        assert len(store.pr_runs()) == 1 and store.pr_run(first.record.run_key)["executions"] == 2
        assert (store.findings(), store.run_count()) == history_before  # baseline-scan history is not read or written
        deltas = store.pr_deltas(first.record.run_key)
        assert [(d["label"], d["policy_id"], d["relationship_id"], d["subject"]) for d in deltas] == [
            ("introduced", "P2", "R2", "schema_version")
        ]

    def test_a_pr_analysis_never_creates_finding_history(self):
        store = RunStore(":memory:")
        analyze(CASES["u3"], store=store)
        assert store.findings() == [] and store.run_count() == 0 and len(store.pr_runs()) == 1

    def test_two_pull_requests_are_two_runs_and_reread_by_slug_and_number(self):
        store = RunStore(":memory:")
        analyze(CASES["u1"], store=store)
        analyze(CASES["u2"], store=store)
        assert [r["pr_number"] for r in store.pr_runs()] == [101, 102]
        assert [r["gate_result"] for r in store.pr_runs(CHAT, 102)] == ["fail"]


class TestReports:
    def test_markdown_states_the_gate_rule_and_labels_every_group(self):
        md = render_pr_markdown(analyze(CASES["u5_raw"]).semantic)
        assert "Pre-existing findings never fail a PR gate by themselves" in md
        assert "## Pre-existing (present on base and head; not blamed on this pull request, never fails the gate)" in md
        assert "gate **pass** (exit 0)" in md

    def test_markdown_lists_the_affected_repository_impact_and_changed_value(self):
        md = render_pr_markdown(analyze(CASES["u2"]).semantic)
        assert "| R7 | runtime_contract | **breaks** | `homelab` | bilateral |" in md
        assert "consistent: 'llava:7b' vs [" in md and "inconsistent: 'llava:13b' vs [" in md
        assert "- Delta **introduced**: on base this subject was consistent" in md

    def test_json_report_is_versioned_and_self_describing(self):
        s = analyze(CASES["u1"]).semantic
        assert s["schema_version"] == 1 and s["kind"] == "pr_impact"
        assert set(s["findings"]) == {"introduced", "undetermined", "pre_existing", "resolved"}
        assert s["gate"]["exit_code"] == 1 and "Pre-existing findings never fail" in s["gate"]["rule"]

    def test_the_m1_scan_report_is_unchanged_by_the_m2_code(self):
        s = scan().semantic
        assert "kind" not in s and "gate" not in s and s["schema_version"] == 1  # the M1 report keeps its own shape


class TestRenamedSubjectKeepsBaseEvidenceInTheReport:
    def test_a_resolved_finding_is_reported_with_its_base_identity_and_evidence(self):
        """A Service that duplicates an address is renamed by the PR: the duplicate persists under a new fingerprint (locators
        move), so the old finding is resolved and a new one introduced. The report must show the old fingerprint with the
        *base* evidence for the resolved row, and the new one with head evidence for the introduced row."""
        from pr_cases import HOMELAB, SERVICE_244

        world_dup = (add_file(HOMELAB, "apps/oldsvc/service.yaml", SERVICE_244),)
        case = PRCase(
            "renamed_dup", HOMELAB, 960, (rename_path(HOMELAB, "apps/oldsvc/service.yaml", "apps/newsvc/service.yaml"),),
            base=world_dup,
        )  # fmt: skip
        a = analyze(case, world=PRWorld(case, pr=case.pull_request()))
        resolved, introduced = findings(a, "resolved"), findings(a, "introduced")
        assert len(resolved) == 1 and len(introduced) == 1 and not findings(a, "pre_existing")
        assert resolved[0]["fingerprint"] != introduced[0]["fingerprint"]
        assert any("apps/oldsvc/service.yaml" in e["path"] for e in resolved[0]["evidence"])
        assert any("apps/newsvc/service.yaml" in e["path"] for e in introduced[0]["evidence"])
        assert all(e["sha"] == case.base_sha for e in resolved[0]["evidence"] if e["repo"] == "homelab")
        assert all(e["sha"] == case.head_sha for e in introduced[0]["evidence"] if e["repo"] == "homelab")


def test_the_scan_dashboard_skips_a_pr_report_instead_of_misreading_it(tmp_path):
    """The dashboard (M1) consumes scan reports; a PR report in the same directory is skipped with a reason, never rendered."""
    from repo_orchestrator.dashboard import build_dashboard

    a = analyze(CASES["u1"])
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "pr-impact.json").write_text(json.dumps({"semantic": a.semantic, "execution": a.execution}))
    result = build_dashboard(reports, tmp_path / "site")
    assert result.reports == 0 and [s.file for s in result.skipped] == ["pr-impact.json"]


class TestListingDependencies:
    def test_a_skill_added_to_an_adopter_is_evaluated_and_introduced_like_a_scan_finds_it(self):
        """Codex finding: P6 reads the adopter's skills directory. A scan of the mutated snapshot finds the adopter-table
        inconsistency; the PR analysis of the same change must too, not report 'no impact, exit 0'."""
        from repo_orchestrator.models import Status
        from support import find
        from support import scan as scan_mutation

        add = add_file(AP, ".claude/skills/new-skill/SKILL.md", "# a new skill\n")
        oracle = find(scan_mutation([add]), "P6", "R5.ap", "adopter_table")
        assert oracle and oracle[0].status is Status.INCONSISTENT  # independent of the PR analysis
        case = PRCase("new_skill", AP, 970, (add,))
        a = analyze(case, world=PRWorld(case, pr=case.pull_request()))
        f = only(a, "introduced")
        assert (f["policy_id"], f["relationship_id"], f["subject"]) == ("P6", "R5.ap", "adopter_table")
        assert "listing" in {t["kind"] for t in entry(a, "R5.ap")["triggers"]} and a.exit_code == 1
