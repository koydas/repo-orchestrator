"""Shared site shell (koydas/koydas site-kit, ADR-0018): the copy inlined in dashboard.py declares a version and a hash,
and these tests fail when the block drifts from them. The dashboard stays static HTML and CSS: no script, no dependency."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pytest

from repo_orchestrator import dashboard
from support import ROOT, scan
from test_dashboard import built, doc, page

SOURCE = (ROOT / "src" / "repo_orchestrator" / "dashboard.py").read_text(encoding="utf-8")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_source() -> str:
    """The renderer fragment, verbatim from the block (render.py of the kit): the part after SK_FILES."""
    block = SOURCE.split("# BEGIN site-kit", 1)[1].split("# END site-kit", 1)[0]
    start = block.index("# site-kit renderer, Python twin")
    return block[start:].split("# fmt: on", 1)[0].rstrip("\n") + "\n"


@pytest.fixture(scope="module")
def base() -> dict[str, Any]:
    return doc(scan())


def test_the_block_matches_the_version_and_hash_it_declares():
    files = {**{name: sha(text) for name, text in dashboard.SK_FILES.items()}, "render.py": sha(render_source())}
    assert files == dashboard.SK_FILE_HASHES
    assert sha("".join(f"{name} {files[name]}\n" for name in sorted(files))) == dashboard.SK_HASH
    assert dashboard.SK_PROFILE == "python"
    assert re.fullmatch(r"\d+\.\d+\.\d+", dashboard.SK_VERSION)


def test_a_single_marked_block_and_nothing_imported_for_it():
    assert SOURCE.count("# BEGIN site-kit") == 1 and SOURCE.count("# END site-kit") == 1
    # test_dashboard.py pins the relative imports to {models, report}: the kit is inlined, not imported
    assert not re.search(r"^\s*(from|import) .*site_kit", SOURCE, re.M)


def test_pages_carry_the_shared_top_bar_footer_and_current_site(tmp_path: Path, base):
    out, result = built(tmp_path, a=base)
    for name in (
        "index.html",
        "reports.html",
        *[f.removeprefix("") for f in result.files if f.startswith("reports/") and f.endswith(".html")],
    ):
        html = page(out, name)
        bar, header = html.index('class="sk-bar"'), html.index("<header>")
        footer_note, kit_footer = html.index("<footer>"), html.index('class="sk-footer"')
        assert bar < header and footer_note < kit_footer, name
        for part in (html[bar:header], html[kit_footer:]):
            assert part.count('aria-current="true"') == 1
            assert '<a href="https://koydas.github.io/repo-orchestrator/" aria-current="true">' in part
            for site in ("koydas/", "agent-patterns/", "autonomous-dev-loop/", "ai-dev-tools/"):
                assert f'https://koydas.github.io/{site}"' in part, (name, site)
        assert f'data-site-kit="{dashboard.SK_VERSION}"' in html
        assert 'href="https://github.com/koydas/repo-orchestrator">GitHub<' in html


def test_the_shell_adds_no_script_no_link_no_import_no_handler(tmp_path: Path, base):
    out, result = built(tmp_path, a=base)
    for name in result.files:
        if name.endswith(".html"):
            html = page(out, name)
            assert not re.search(r"<script|<link|@import|\son[a-z]+=|javascript:", html, re.I), name
            assert "sk-bar" in html


def test_only_the_overview_opens_with_the_page_header_and_has_one_h1(tmp_path: Path, base):
    out, result = built(tmp_path, a=base)
    overview = page(out, "index.html")
    assert re.search(
        r'<p class="sk-eyebrow">Repo Orchestrator · Dashboard</p>\s*<h1 class="sk-title">Overview</h1>', overview
    )
    assert overview.count("<h1") == 1
    for name in ("reports.html", *[f for f in result.files if f.startswith("reports/") and f.endswith(".html")]):
        html = page(out, name)
        assert 'class="sk-pagehead"' not in html and html.count("<h1") == 1, name


def test_the_dashboards_own_sticky_parts_sit_below_the_bar():
    assert "header { position: sticky; top: var(--sk-bar-h, 0px);" in dashboard.STYLE
    assert "top: calc(64px + var(--sk-bar-h, 0px))" in dashboard.STYLE


def test_an_empty_build_has_the_shell_too(tmp_path: Path):
    out = tmp_path / "out"
    (tmp_path / "in").mkdir()
    dashboard.build_dashboard(tmp_path / "in", out)
    html = page(out, "index.html")
    assert 'class="sk-bar"' in html and 'class="sk-pagehead"' in html and html.count("<h1") == 1


def test_an_unknown_site_is_rejected_by_the_header_and_the_footer():
    for render in (dashboard.sk_header, dashboard.sk_footer):
        with pytest.raises(KeyError, match="unknown site"):
            render("nope")
