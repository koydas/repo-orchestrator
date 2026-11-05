"""Static report dashboard (ADR-0012): real M1 reports in, safe deterministic HTML out. No network, no browser."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from repo_orchestrator import cli
from repo_orchestrator.collector.overlay import replace_text
from repo_orchestrator.dashboard import (
    BRANCH_PREFIX,
    LOOP_DASHBOARD_URL,
    SK_NAV,
    TOOLS_DASHBOARD_URL,
    Report,
    ReportError,
    _gh_url,
    build_dashboard,
    load_reports,
    run_key,
    status_json,
    validate_report,
)
from repo_orchestrator.models import ConfigError, digest
from repo_orchestrator.runner import ScanReport
from repo_orchestrator.store import RunStore
from support import ROOT, scan
from test_engine import authority, exception, with_exceptions

XSS = '<img src=x onerror="alert(1)"><script>alert(2)</script>'


def doc(r: ScanReport, started: str | None = "2026-11-03T09:42:10Z") -> dict[str, Any]:
    execution = json.loads(json.dumps(r.execution))
    execution["started_at"] = started
    return {"semantic": json.loads(json.dumps(r.semantic)), "execution": execution}


def forge(report: dict[str, Any], edit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """A report edited *and* re-sealed (digest recomputed): only the structural checks can catch what it says."""
    r = copy.deepcopy(report)
    edit(r["semantic"])
    r["semantic"]["result_digest"] = digest({k: v for k, v in r["semantic"].items() if k != "result_digest"})
    r["execution"]["run_key"] = None
    return r


def write(directory: Path, name: str, report: object) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report if isinstance(report, str) else json.dumps(report), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def base() -> dict[str, Any]:
    return doc(scan())


@pytest.fixture(scope="module")
def partial() -> dict[str, Any]:
    mutation = replace_text("koydas/koydas", "README.md", "17 multi-agent patterns", "seventeen multi-agent patterns")
    return doc(scan([mutation]), "2026-11-02T16:15:00Z")


@pytest.fixture(scope="module")
def policy() -> dict[str, Any]:
    return doc(scan(decl=with_exceptions(exception(), authorities=[authority()])), "2026-11-02T10:30:00Z")


def built(tmp_path: Path, **reports: object) -> tuple[Path, Any]:
    src = tmp_path / "in"
    src.mkdir(exist_ok=True)
    for name, report in reports.items():
        write(src, f"{name}.json", report)
    out = tmp_path / "out"
    return out, build_dashboard(src, out)


def page(out: Path, name: str) -> str:
    return (out / name).read_text(encoding="utf-8")


def detail(out: Path, key: str) -> str:
    return page(out, f"reports/{key}.html")


def section(html: str, start: str, end: str) -> str:
    return html.split(start, 1)[1].split(end, 1)[0]


# ---- rendering valid M1 reports ---------------------------------------------------------------------------------


def test_a_valid_report_produces_the_three_views_a_json_copy_a_manifest_and_a_status(tmp_path, base):
    out, result = built(tmp_path, a=base)
    key = run_key(base["semantic"])
    assert result.reports == 1 and not result.skipped
    assert set(result.files) == {
        "index.html",
        "reports.html",
        "status.json",
        f"reports/{key}.html",
        f"reports/{key}.json",
        "reports/index.json",
    }
    assert json.loads(page(out, f"reports/{key}.json"))["semantic"] == base["semantic"]
    assert json.loads(page(out, "reports/index.json")) == {"schema_version": 1, "reports": [f"{key}.json"]}


def test_overview_shows_the_report_numbers_and_no_invented_metric(tmp_path, base):
    out, _ = built(tmp_path, a=base)
    html = page(out, "index.html")
    assert "2 confirmed inconsistencies" in html and "Run complete" in html and ">33<" in html
    assert f"reports/{run_key(base['semantic'])}.html" in html
    assert "%" not in re.sub(r"<style>.*?</style>", "", html, flags=re.S)  # no coherence percentage, anywhere
    assert "Run scan" not in html and "<button" not in html


def test_detail_lists_findings_with_ids_evidence_fix_sites_and_open_questions(tmp_path, base):
    out, _ = built(tmp_path, a=base)
    html = detail(out, run_key(base["semantic"]))
    for f in base["semantic"]["findings"]:
        assert (
            f'id="f-{f["fingerprint"]}"' in html and f["relationship_id"] in html and f["reason"].split(";")[0] in html
        )
    assert "Where to fix, in order" in html and "Open questions" in html and "Evidence · 2 sources" in html
    assert "Evidence verified" in html and "Conclusion validated by check" in html  # two distinct statements
    assert "Technical details" in html and "<details" in html


def test_findings_keep_the_report_order_without_re_sorting(tmp_path, base):
    shuffled = forge(base, lambda s: s["findings"].reverse())
    out, _ = built(tmp_path, a=shuffled)
    html = section(detail(out, run_key(shuffled["semantic"])), 'id="findings"', 'id="other"')
    fingerprints = [f["fingerprint"] for f in shuffled["semantic"]["findings"]]
    assert [html.index(fp) for fp in fingerprints] == sorted(html.index(fp) for fp in fingerprints)


# ---- status classification --------------------------------------------------------------------------------------


def test_only_inconsistent_outcomes_are_confirmed_findings(tmp_path, policy):
    sem = policy["semantic"]
    assert sem["policy_conflicts"] and sem["policy_clarifications"] and sem["insufficient_evidence"]
    out, _ = built(tmp_path, a=policy)
    html = detail(out, run_key(sem))
    findings = section(html, 'id="findings"', 'id="other"')
    assert f"Findings ({len(sem['findings'])})" in findings
    for label in ("Policy conflict", "Policy clarification", "Cannot evaluate", "Excepted", "Failed", "Consistent"):
        assert label not in findings, label  # a finding card carries a severity, never another outcome status
    other = html.split('id="other"', 1)[1]
    assert (
        "Policy conflicts (1)" in other
        and "Policy clarifications (3)" in other
        and "Insufficient or undecidable" in other
    )


def test_a_report_that_files_a_non_finding_under_findings_is_rejected(base, policy):
    def move(sem: dict[str, Any]) -> None:
        sem["findings"].append(sem["policy_clarifications"].pop())
        sem["summary"]["by_status"]["policy_clarification"] -= 1  # even with consistent counts

    with pytest.raises(ReportError, match="does not belong in 'findings'"):
        validate_report(forge(policy, move), "x")


def test_red_is_reserved_for_confirmed_inconsistencies(tmp_path, base, policy, partial):
    out, _ = built(tmp_path, a=base, b=policy, c=partial)
    for path in out.rglob("*.html"):
        for text in re.findall(r'<span class="pill bad"[^>]*>(.*?)</span>', path.read_text(encoding="utf-8")):
            labelled = re.search(r"[Ii]nconsistent|finding", text) or (path.name == "reports.html" and text.isdigit())
            assert labelled, (path.name, text)  # reports.html: a bare count under the "Inconsistent" column
    html = detail(out, run_key(policy["semantic"]))
    assert 'class="pill warn" title="A wider policy contradicts' in html  # a conflict is amber, not red


# ---- complete vs partial ----------------------------------------------------------------------------------------


def test_a_partial_run_says_it_may_be_missing_findings_and_never_implies_resolution(tmp_path, partial):
    assert partial["semantic"]["run_status"] == "partial"
    out, _ = built(tmp_path, a=partial)
    html = detail(out, run_key(partial["semantic"]))
    assert "Partial run." in html and "is <strong>not</strong> resolved" in html and "Run partial" in html
    index = page(out, "index.html")
    assert "Run partial" in index
    assert json.loads(page(out, "status.json"))["status"] == "bad"  # the finding wins; partial is in the headline
    assert "partial run" in json.loads(page(out, "status.json"))["headline"]


def test_a_complete_run_is_not_presented_as_all_clear_when_it_has_findings(tmp_path, base):
    out, _ = built(tmp_path, a=base)
    html = detail(out, run_key(base["semantic"]))
    assert "Complete run." in html and "Run complete" in html
    assert "Partial run." not in html and "No confirmed inconsistency" not in html


def test_run_status_and_outcome_status_use_different_vocabularies(tmp_path, base, partial):
    out, _ = built(tmp_path, a=base, b=partial)
    html = page(out, "reports.html")
    assert 'class="run neutral"' in html and 'class="run warn"' in html  # run: outlined, never green or red
    assert 'class="pill bad"' in html  # outcome counts use the pill


def test_status_json_follows_the_sibling_dashboards_shape(base, partial):
    assert json.loads(status_json([])) == {
        "schema_version": 1,
        "app": "repo-orchestrator",
        "generated_at": None,
        "status": "warn",
        "headline": "no report published",
    }

    def as_report(d: dict[str, Any]) -> Report:
        return Report(run_key(d["semantic"]), d["semantic"], d["execution"], ("x",))

    assert json.loads(status_json([as_report(forge(base, _clear_findings))]))["status"] == "ok"
    assert json.loads(status_json([as_report(partial)]))["status"] == "bad"


def _clear_findings(sem: dict[str, Any]) -> None:
    sem["summary"]["by_status"]["inconsistent"] = 0
    sem["summary"]["outcomes"] -= len(sem["findings"])
    sem["findings"].clear()


# ---- malformed or missing data ----------------------------------------------------------------------------------


def test_malformed_files_are_skipped_visibly_and_valid_ones_still_render(tmp_path, base):
    src = tmp_path / "in"
    write(src, "good.json", base)
    write(src, "broken.json", "{not json")
    write(src, "scalar.json", "42")
    write(src, "no-semantic.json", {"execution": {}})
    write(src, "old.json", {"semantic": {"schema_version": 0}, "execution": {}})
    tampered = copy.deepcopy(base)
    tampered["semantic"]["findings"][0]["reason"] = "edited by hand"
    write(src, "tampered.json", tampered)
    write(src, "nested/ignored-manifest/index.json", {"schema_version": 1, "reports": []})
    reports, skipped = load_reports(src)
    assert len(reports) == 1
    assert {s.file for s in skipped} == {"broken.json", "scalar.json", "no-semantic.json", "old.json", "tampered.json"}
    assert "result_digest" in next(s.reason for s in skipped if s.file == "tampered.json")
    out = tmp_path / "out"
    build_dashboard(src, out)
    assert "5 report files could not be used" in page(out, "index.html")
    assert "5 report files could not be used" in page(out, "reports.html")


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda s: s.pop("summary"), "summary"),
        (lambda s: s["summary"]["by_status"].update(consistent=1), "by_status.consistent"),
        (lambda s: s["summary"].update(outcomes=1), "summary.outcomes"),
        (lambda s: s.update(run_status="done"), "run_status"),
        (lambda s: s["inputs"].update(as_of="yesterday"), "as_of"),
        (lambda s: s["findings"][0].pop("evidence"), "evidence"),
        (lambda s: s["findings"][0].update(severity="critical"), "severity"),
        (lambda s: s["findings"][0].update(fingerprint=None), "fingerprint"),
        (lambda s: s["findings"][0]["evidence"][0].update(line_start="1"), "line_start"),
    ],
)
def test_structural_violations_are_rejected_even_when_the_digest_is_valid(base, edit, message):
    with pytest.raises(ReportError, match=message):
        validate_report(forge(base, edit), "r")


@pytest.mark.parametrize("stats", [["invalid"], "x", 3])
def test_malformed_execution_stats_skip_the_file_and_never_abort_the_build(tmp_path, base, stats):
    bad = copy.deepcopy(base)
    bad["execution"]["stats"] = stats  # execution is outside the digest: only the structural check can catch it
    with pytest.raises(ReportError, match="stats"):
        validate_report(bad, "r")
    src = tmp_path / "in"
    write(src, "good.json", base)
    write(src, "bad.json", bad)
    result = build_dashboard(src, tmp_path / "out")
    assert result.reports == 1 and [s.file for s in result.skipped] == ["bad.json"]


def test_a_run_key_that_contradicts_the_inputs_is_rejected(base):
    bad = copy.deepcopy(base)
    bad["execution"]["run_key"] = "0" * 32
    with pytest.raises(ReportError, match="run_key"):
        validate_report(bad, "r")


def test_a_missing_reports_directory_is_a_configuration_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        build_dashboard(tmp_path / "nope", tmp_path / "out")
    assert cli.main(["dashboard", "--reports", str(tmp_path / "nope"), "--out", str(tmp_path / "o")]) == cli.FAILURE


# ---- safe rendering ---------------------------------------------------------------------------------------------


def hostile(base: dict[str, Any]) -> dict[str, Any]:
    def edit(sem: dict[str, Any]) -> None:
        for o in sem["findings"] + sem["policy_clarifications"] + sem["consistent"][:3]:
            o["reason"], o["subject"] = XSS, XSS
            o["fix_sites"] = [{"repo": XSS, "path": XSS, "reason": XSS}]
            o["investigate"] = [XSS, f"{XSS}:{XSS}"]
            o["detail"]["note"] = XSS
            for ev in o["evidence"]:
                ev["quote"], ev["locator"], ev["path"] = XSS, XSS, XSS
        sem["open_questions"][0]["text"] = XSS
        sem["coverage"][0]["note"] = XSS
        sem["inputs"]["revisions"][0]["slug"] = 'evil"><script>x</script>/repo'

    return forge(base, edit)


def test_every_report_derived_string_is_escaped(tmp_path, base):
    report = hostile(base)
    out, _ = built(tmp_path, a=report)
    for name in ("index.html", "reports.html", f"reports/{run_key(report['semantic'])}.html"):
        html = page(out, name)
        assert "<script" not in html and "<img" not in html and 'onerror="' not in html, name
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in detail(out, run_key(report["semantic"]))


def test_hrefs_are_only_relative_or_github_or_the_sibling_dashboards(tmp_path, base, policy):
    report = hostile(base)
    out, _ = built(tmp_path, a=report, b=policy)
    allowed = ("https://github.com/", LOOP_DASHBOARD_URL, TOOLS_DASHBOARD_URL)
    sites = {s["url"] for s in SK_NAV["sites"]}  # the shared shell (site-kit) links the five sites, exactly these URLs
    for path in out.rglob("*.html"):
        for href in re.findall(r'href="([^"]*)"', path.read_text(encoding="utf-8")):
            assert not href.lower().startswith(("javascript:", "data:", "vbscript:")), (path.name, href)
            assert href.startswith(allowed) or href in sites or not re.match(r"^[a-z][a-z0-9+.-]*:|^//", href), (
                path.name,
                href,
            )


def test_pages_carry_no_script_and_no_external_asset(tmp_path, base):
    out, _ = built(tmp_path, a=base)
    for path in out.rglob("*.html"):
        html = path.read_text(encoding="utf-8")
        assert "<script" not in html and "<link" not in html and "@import" not in html
        assert not re.search(r'(src|srcset)="', html) and "url(" not in html


# ---- GitHub evidence links --------------------------------------------------------------------------------------

SHA = "c43e48bbb4ce6b70ae70e8652ef05f64a23fcadb"


def test_github_urls_are_built_from_validated_parts_only():
    assert _gh_url("koydas/koydas", "commit", SHA) == f"https://github.com/koydas/koydas/commit/{SHA}"
    assert _gh_url("koydas/koydas", "blob", SHA, "docs/adr/a b.md", (3, 5)) == (
        f"https://github.com/koydas/koydas/blob/{SHA}/docs/adr/a%20b.md#L3-L5"
    )
    assert _gh_url("koydas/koydas", "blob", SHA, "README.md", (11, 11)).endswith("#L11")
    assert _gh_url("koydas/koydas", "tree", SHA, "docs").endswith("/tree/" + SHA + "/docs")
    for slug in ("koydas", 'a"b/c', "a/b/c", "../x", "koydas/..", "http://evil/x", "javascript:alert(1)//x"):
        assert _gh_url(slug, "repo") is None, slug
    for sha in ("main", SHA[:7], SHA.upper(), "", None):
        assert _gh_url("koydas/koydas", "commit", sha) is None, sha
    for path in ("", "/etc/passwd", "../x", "a/../b", "a//b", "a/./b", "a\\b", "a\nb"):
        assert _gh_url("koydas/koydas", "blob", SHA, path) is None, path


def test_evidence_links_point_at_the_pinned_commit_and_lines(tmp_path, base):
    out, _ = built(tmp_path, a=base)
    html = detail(out, run_key(base["semantic"]))
    for f in base["semantic"]["findings"]:
        for ev in f["evidence"]:
            kind = "tree" if ev["kind"] == "tree_listing" else "blob"
            assert f'href="https://github.com/{ev["slug"]}/commit/{ev["sha"]}"' in html
            assert f'href="https://github.com/{ev["slug"]}/{kind}/{ev["sha"]}/' in html
    ev = next(x for f in base["semantic"]["findings"] for x in f["evidence"] if x["kind"] == "text")
    assert f"/blob/{ev['sha']}/{ev['path']}#L{ev['line_start']}" in html
    fix = base["semantic"]["findings"][0]["fix_sites"][0]
    assert f"/{fix['path']}" in html and "Where to fix" in html


def test_an_invalid_slug_or_sha_degrades_to_plain_text_not_a_link(tmp_path, base):
    def edit(sem: dict[str, Any]) -> None:
        for ev in sem["findings"][0]["evidence"]:
            ev["slug"], ev["sha"] = "not a slug", "main"

    report = forge(base, edit)
    out, _ = built(tmp_path, a=report)
    html = section(detail(out, run_key(report["semantic"])), 'id="findings"', 'id="other"')
    assert "not a slug@main" in html and 'href="https://github.com/not' not in html


# ---- ordering, dedup, identity ----------------------------------------------------------------------------------


def test_reports_are_listed_newest_first_with_a_deterministic_tiebreak(tmp_path, base, partial, policy):
    out, result = built(tmp_path, z=policy, a=base, m=partial)
    html = page(out, "reports.html")
    keys = json.loads(page(out, "reports/index.json"))["reports"]
    assert keys == [f"{run_key(d['semantic'])}.json" for d in (base, partial, policy)]
    assert [html.index(k[:-5]) for k in keys] == sorted(html.index(k[:-5]) for k in keys)
    assert result.reports == 3


def test_a_report_without_a_start_time_sorts_by_its_as_of_date(tmp_path, base):
    old = doc(scan(as_of=date(2026, 10, 31)), None)
    out, _ = built(tmp_path, a=old, b=base)
    assert json.loads(page(out, "reports/index.json"))["reports"][0] == f"{run_key(base['semantic'])}.json"
    assert "As of Oct 31, 2026" in page(out, "reports.html")


def test_the_same_run_in_several_files_is_listed_once_keeping_the_earliest_execution(tmp_path, base):
    later = copy.deepcopy(base)
    later["execution"]["started_at"] = "2026-11-09T08:00:00Z"
    out, result = built(tmp_path, a=later, b=base)
    write(tmp_path / "in", "sub/copy.json", base)
    reports, _ = load_reports(tmp_path / "in")
    assert len(reports) == 1 and reports[0].started_at == "2026-11-03T09:42:10Z"
    assert reports[0].files == ("a.json", "b.json", "sub/copy.json")
    assert result.reports == 1 and page(out, "reports.html").count('class="stack"') == 1


def test_run_identity_is_the_run_stores_with_or_without_a_store(tmp_path):
    store = RunStore(":memory:")
    stored = scan(store=store)
    assert stored.execution["run_key"] == run_key(stored.semantic)
    assert run_key(scan().semantic) == run_key(stored.semantic)  # --no-store gives the same identity
    validate_report(doc(stored), "stored")  # and the recorded key passes the cross-check


def test_a_different_result_for_the_same_inputs_is_a_different_run(tmp_path, base, partial):
    assert run_key(base["semantic"]) != run_key(partial["semantic"])


def test_generation_is_deterministic_byte_for_byte(tmp_path, base, partial, policy):
    src = tmp_path / "in"
    for name, d in (("a", base), ("b", partial), ("c", policy)):
        write(src, f"{name}.json", d)
    build_dashboard(src, tmp_path / "one")
    build_dashboard(src, tmp_path / "two")
    one = {p.relative_to(tmp_path / "one"): p.read_bytes() for p in (tmp_path / "one").rglob("*") if p.is_file()}
    two = {p.relative_to(tmp_path / "two"): p.read_bytes() for p in (tmp_path / "two").rglob("*") if p.is_file()}
    assert one == two and len(one) == 3 * 2 + 4


def test_a_built_site_can_be_fed_back_as_history(tmp_path, base, partial):
    out, _ = built(tmp_path, a=base)
    again = tmp_path / "again"
    reports, skipped = load_reports(out / "reports")  # the published copies are valid input, the manifest is ignored
    assert len(reports) == 1 and not skipped
    write(out / "reports", "new.json", partial)
    build_dashboard(out / "reports", again)
    assert json.loads(page(again, "reports/index.json"))["reports"].__len__() == 2


# ---- run store history ------------------------------------------------------------------------------------------


def test_without_a_store_the_page_says_history_was_not_recorded(tmp_path, base):
    out, _ = built(tmp_path, a=base)
    html = detail(out, run_key(base["semantic"]))
    assert "Not recorded" in html and "--no-store" in html and "In the run store" not in html


def test_with_a_store_the_findings_carry_their_transition_and_not_evaluated_is_not_resolved(tmp_path):
    report = doc(scan(store=RunStore(":memory:")))
    assert report["execution"]["history"]["new"]
    report["execution"]["history"]["not_evaluated"] = ["deadbeefdeadbeef"]
    out, _ = built(tmp_path, a=report)
    html = detail(out, run_key(report["semantic"]))
    assert "In the run store" in html and "not evaluated: <code>deadbeefdeadbeef" in html
    assert "not resolved" in html or "is not resolved" in html


# ---- empty states -----------------------------------------------------------------------------------------------


def test_with_no_report_every_view_says_so_and_invents_nothing(tmp_path):
    out, result = built(tmp_path)
    assert result.reports == 0
    for name in ("index.html", "reports.html"):
        html = page(out, name)
        assert "No report yet" in html and "repo-orchestrator scan" in html
        assert "Latest findings" not in html and ">0<" not in html
    assert json.loads(page(out, "status.json"))["status"] == "warn"
    assert json.loads(page(out, "reports/index.json"))["reports"] == []


def test_with_only_unusable_files_the_empty_state_names_them(tmp_path):
    out, result = built(tmp_path, bad="{oops")
    assert result.reports == 0 and len(result.skipped) == 1
    assert "No report yet" in page(out, "index.html") and "bad.json" in page(out, "index.html")


def test_a_report_with_no_finding_says_so_without_claiming_more(tmp_path, base):
    clean = forge(base, _clear_findings)
    out, _ = built(tmp_path, a=clean)
    assert "No confirmed inconsistency" in page(out, "index.html")
    assert "No confirmed inconsistency in this run." in detail(out, run_key(clean["semantic"]))
    partial_clean = forge(clean, lambda s: s.update(run_status="partial"))
    out2 = tmp_path / "out2"
    write(tmp_path / "p", "a.json", partial_clean)
    build_dashboard(tmp_path / "p", out2)
    assert "the run is partial" in page(out2, "index.html") and "not proof" in page(out2, "index.html")


# ---- CLI, deployment and architecture constraints ---------------------------------------------------------------


def test_the_dashboard_command_builds_and_reports(tmp_path, base, capsys):
    src = tmp_path / "reports"
    write(src, "r.json", base)
    write(src, "bad.json", "{")
    assert cli.main(["dashboard", "--reports", str(src), "--out", str(tmp_path / "site")]) == cli.OK
    captured = capsys.readouterr()
    assert "1 report(s), 1 skipped" in captured.out and "skipped bad.json" in captured.err
    assert (tmp_path / "site" / "index.html").exists()


def test_the_dashboard_module_reads_reports_only(tmp_path):
    import ast

    tree = ast.parse((ROOT / "src" / "repo_orchestrator" / "dashboard.py").read_text())
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    relative = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 1}
    assert not {m.split(".")[0] for m in imported} & {"yaml", "urllib", "http", "socket", "subprocess", "sqlite3"} - {
        "urllib"
    }
    assert "urllib.request" not in imported
    assert relative == {"models", "report"}  # no engine, collector, policy or store: a read-only projection


def test_the_dashboard_workflow_is_manual_read_only_and_keeps_history_on_pages():
    wf = (ROOT / ".github" / "workflows" / "dashboard.yml").read_text()
    assert "schedule:" not in wf and "cron" not in wf  # scheduled scans are not authorized (CLAUDE.md)
    assert "workflow_dispatch" in wf and "contents: read" in wf and "contents: write" not in wf
    assert "--no-store" in wf and "repo-orchestrator dashboard" in wf and "deploy-pages" in wf
    assert "^[0-9a-f]{32}\\.json$" in wf  # the manifest is data: file names are validated before use
    assert "pull_request" not in wf


# ---- M3: an additive `investigation` section must not break the dashboard, and must not read as an all-clear ----------


def _investigated(base, **by):
    states = {
        "confirmed": 0,
        "needs_validation": 0,
        "insufficient_evidence": 0,
        "dismissed": 0,
        "policy_question": 0,
        **by,
    }
    out = copy.deepcopy(base)
    sem = out["semantic"]
    sem.pop("result_digest")
    sem["investigation"] = {"summary": {"by_state": states}, "candidates": []}
    sem["result_digest"] = digest(sem)  # a real investigated report carries a digest that covers its section
    return out


def test_a_report_with_an_investigation_section_still_validates_and_renders(tmp_path, base):
    raw = _investigated(base, needs_validation=1, insufficient_evidence=2)
    validate_report(raw)
    folder = tmp_path / "r"
    folder.mkdir()
    (folder / "report.json").write_text(json.dumps(raw))
    out = tmp_path / "site"
    build_dashboard(folder, out)
    html = "\n".join(p.read_text() for p in out.rglob("*.html"))
    assert "1 awaiting a human validation" in html and "2 with insufficient evidence" in html


def test_an_undecided_investigation_never_shows_the_all_clear_state():
    base_doc = doc(scan(), None)
    base_doc["semantic"]["findings"] = []
    base_doc["semantic"]["summary"]["by_status"]["inconsistent"] = 0
    undecided = _investigated(base_doc, needs_validation=1)
    from repo_orchestrator.dashboard import _status_line

    r = Report("k", undecided["semantic"], undecided["execution"], ())
    assert "left claims undecided" in _status_line(r)


def test_a_malformed_investigation_section_is_rejected_by_name(base):
    bad = copy.deepcopy(base)
    sem = bad["semantic"]
    sem.pop("result_digest")
    sem["investigation"] = {"summary": {"by_state": {"confirmed": "many"}}}
    sem["result_digest"] = digest(sem)
    with pytest.raises(ReportError, match="investigation"):
        validate_report(bad)


def test_a_report_without_an_investigation_section_has_no_investigation_notice(tmp_path, base):
    folder = tmp_path / "r"
    folder.mkdir()
    (folder / "report.json").write_text(json.dumps(base))
    out = tmp_path / "site"
    build_dashboard(folder, out)
    assert "Investigation:" not in "\n".join(p.read_text() for p in out.rglob("*.html"))


# ---- finding actions (ADR-0014) ------------------------------------------------------------------------------------


def _first_finding_html(tmp_path, report):
    out, _ = built(tmp_path, a=report)
    html = detail(out, run_key(report["semantic"]))
    start = html.index('class="actions"')
    return html[start : html.index("</article>", start)]


def test_each_finding_offers_an_issue_link_a_prompt_and_a_pr_link_on_its_fix_repository(tmp_path, base):
    f = base["semantic"]["findings"][0]
    html = _first_finding_html(tmp_path, base)
    assert "Open issue</a>" in html and "Create prompt</summary>" in html and "Create PR</a>" in html
    assert 'href="https://github.com/koydas/koydas/issues/new?labels=repo-orchestrator&amp;title=' in html
    assert "ready-for-dev" not in html
    assert (
        f'href="https://github.com/koydas/koydas/compare/{BRANCH_PREFIX}{f["fingerprint"]}?expand=1&amp;title=' in html
    )
    assert f"branch {BRANCH_PREFIX}{f['fingerprint']} and push it" in html and "Do not open a pull request" in html


def test_actions_create_nothing_the_dashboard_stays_scriptless_and_posts_no_form(tmp_path, base):
    html = _first_finding_html(tmp_path, base)
    assert "<form" not in html and "<script" not in html and "onclick" not in html and "<button" not in html
    assert "nothing is created until you submit it on GitHub" in html


def test_only_findings_get_actions_not_other_outcomes(tmp_path, base, policy):
    out, _ = built(tmp_path, a=policy)
    html = detail(out, run_key(policy["semantic"]))
    assert html.count('class="actions"') == len(policy["semantic"]["findings"])


def test_a_finding_without_a_resolvable_repository_has_no_actions(tmp_path, base):
    def edit(sem):
        sem["findings"][0]["fix_sites"] = []
        sem["findings"][0]["affected_repos"] = ["unknown-id"]

    forged = forge(base, edit)
    out, _ = built(tmp_path, a=forged)
    html = detail(out, run_key(forged["semantic"]))
    first = html.index(f'id="f-{base["semantic"]["findings"][0]["fingerprint"]}"')
    assert 'class="actions"' not in html[first : html.index("</article>", first)]


def test_an_unsafe_fingerprint_drops_the_pr_link_but_keeps_issue_and_prompt(tmp_path, base):
    def edit(sem):
        sem["findings"][0]["fingerprint"] = "../x y"

    html = _first_finding_html(tmp_path, forge(base, edit))
    assert "Create PR</a>" not in html and "Open issue</a>" in html and "Create prompt</summary>" in html


def test_issue_and_pr_links_stay_within_the_url_budget_and_hostile_text_is_inert(tmp_path, base):
    def edit(sem):
        sem["findings"][0]["reason"] = XSS + " x" * 20000
        sem["findings"][0]["subject"] = "s\n" + XSS

    html = _first_finding_html(tmp_path, forge(base, edit))
    for href in re.findall(r'href="(https://github.com/[^"]*(?:issues/new|compare)/?[^"]*)"', html):
        assert len(href.replace("&amp;", "&")) < 7000
    assert "<script" not in html and "<img" not in html


def test_fit_terminates_and_returns_empty_when_the_budget_cannot_hold_the_ellipsis():
    from urllib.parse import quote

    from repo_orchestrator.dashboard import _fit

    assert _fit("abc", -5) == "" and _fit("abc", 0) == "" and _fit("abc", 2) == ""
    assert _fit("abc", 9) == "abc" and len(quote(_fit("x" * 500, 9), safe="")) <= 9
    assert len(quote(_fit("é" * 500, 40), safe="")) <= 40


def test_an_oversized_policy_id_is_clipped_in_titles_and_never_hangs_the_build(tmp_path, base):
    def edit(sem):
        sem["findings"][0]["policy_id"] = "P" * 20000

    html = _first_finding_html(tmp_path, forge(base, edit))
    for href in re.findall(r'href="(https://github.com/[^"]*(?:issues/new|compare)/?[^"]*)"', html):
        assert len(href.replace("&amp;", "&")) < 7000


def test_a_confirmed_investigation_finding_is_never_shown_as_a_clean_run():
    from repo_orchestrator.dashboard import _status_line

    clean = doc(scan(), None)
    clean["semantic"]["findings"] = []
    clean["semantic"]["summary"]["by_status"]["inconsistent"] = 0
    confirmed = _investigated(clean, confirmed=1)
    line = _status_line(Report("k", confirmed["semantic"], confirmed["execution"], ()))
    assert "1 confirmed inconsistency" in line and "tone-bad" in line and "No confirmed inconsistency" not in line
