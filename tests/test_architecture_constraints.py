"""Hard constraints of M1 (CLAUDE.md, ADR-0004, ADR-0005, ADR-0007), checked on the production source itself."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from support import ROOT

SRC = ROOT / "src" / "repo_orchestrator"
FILES = sorted(SRC.rglob("*.py"))


def imports(path: Path) -> set[str]:
    """Every imported module, dotted (``urllib.request``) and by top-level package (``urllib``)."""
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        else:
            continue
        for name in names:
            found |= {name, name.split(".")[0]}
    return found


def test_there_is_production_code_to_check():
    assert len(FILES) > 20


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(SRC)))
def test_no_dependency_on_the_s0_prototype_langgraph_or_any_llm_sdk(path):
    forbidden = {
        "s0_spike",
        "langgraph",
        "langchain",
        "langchain_core",
        "anthropic",
        "openai",
        "pydantic",
        "requests",
        "httpx",
    }
    assert not imports(path) & forbidden, f"{path.name} imports {imports(path) & forbidden}"
    text = path.read_text()
    assert "prototypes" not in text and "s0_spike" not in text


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(SRC)))
def test_no_dynamic_execution_shell_or_unsafe_deserialization(path):
    assert not imports(path) & {"subprocess", "pickle", "marshal", "shelve", "ctypes", "importlib", "runpy"}
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"eval", "exec", "compile", "__import__"}, (
                f"{path.name}:{node.lineno} calls {node.func.id}"
            )
    text = path.read_text()
    assert "unsafe_load" not in text and "full_load" not in text and "os.system" not in text
    for line in text.splitlines():  # the one yaml.load call uses a SafeLoader subclass
        if "yaml.load(" in line:
            assert "Loader=_UniqueKeyLoader" in line


def test_the_collector_can_only_read_from_github():
    text = (SRC / "collector" / "github.py").read_text()
    assert 'method="GET"' in text
    for verb in ("POST", "PUT", "PATCH", "DELETE"):
        assert f'"{verb}"' not in text and f"'{verb}'" not in text
    assert "https://api.github.com" in text and "http://" not in text.replace("http://api", "")


def test_only_the_collector_package_performs_network_io():
    for path in FILES:
        if "collector" in path.parts:
            continue
        assert not imports(path) & {"urllib.request", "urllib.error", "http", "socket", "ssl", "ftplib", "smtplib"}, (
            path.name
        )


def test_policies_and_engine_never_touch_the_filesystem_or_network():
    """Policy evaluation runs on a fixed snapshot (ADR-0008): no I/O primitives in the evaluation layers."""
    for path in [
        *sorted((SRC / "policies").glob("*.py")),
        SRC / "engine.py",
        SRC / "evidence.py",
        SRC / "extract.py",
        SRC / "locators.py",
    ]:
        text = path.read_text()
        assert not imports(path) & {
            "socket",
            "urllib.request",
            "urllib.error",
            "http",
            "sqlite3",
            "shutil",
            "tempfile",
        }, path.name
        assert "open(" not in text and "read_text" not in text and "write_text" not in text, path.name


def test_the_only_runtime_dependency_is_pyyaml():
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert [d.split(">")[0].split("=")[0].lower() for d in project["dependencies"]] == ["pyyaml"]


def test_the_s0_workflow_is_isolated_from_the_m1_package():
    s0 = (ROOT / ".github" / "workflows" / "spike-langgraph.yml").read_text()
    assert (
        "prototypes/langgraph-spike/**" in s0
        and "src/" not in s0
        and "working-directory: prototypes/langgraph-spike" in s0
    )
    for path in (ROOT / "prototypes" / "langgraph-spike").rglob("*.py"):
        assert "repo_orchestrator" not in path.read_text(), f"{path} must not depend on the M1 package"


# ---- M2 (CLAUDE.md: M2 is authorized, no GitHub write of any kind, no new network path inside evaluation) -------------

M2_PURE = ["impact.py", "delta.py", "lifecycle.py", "pr_report.py", "pr_runner.py"]


@pytest.mark.parametrize("name", M2_PURE)
def test_the_m2_analysis_layers_do_no_io_of_their_own(name):
    """C4, the delta, the PR report and the PR pipeline work on what a source hands them: no socket, no file, no database.
    (Persistence is ``store.py``; the network is the collector package.)"""
    path = SRC / name
    text = path.read_text()
    assert not imports(path) & {
        "socket", "urllib.request", "urllib.error", "http", "sqlite3", "shutil", "tempfile", "subprocess", "os", "pathlib",
    }, name  # fmt: skip
    assert "open(" not in text and "read_text" not in text and "write_text" not in text, name


def test_no_github_write_endpoint_exists_anywhere_in_the_source():
    """No PR comment, review, status check, issue, label or PR creation: the only endpoints are the read-only ones."""
    forbidden = (
        "/comments",
        "/reviews",
        "/statuses",
        "/check-runs",
        "/issues",
        "/labels",
        "/merge",
        "/dispatches",
        "/forks",
    )
    for path in FILES:
        text = path.read_text()
        for fragment in forbidden:
            assert fragment not in text, f"{path.name} mentions {fragment}"


def test_the_client_requests_exactly_the_documented_read_endpoints():
    import re

    text = (SRC / "collector" / "github.py").read_text()
    templates = set(re.findall(r'f"(/repos/[^"]+)"', text))
    assert templates == {
        "/repos/{slug}",
        "/repos/{slug}/commits/{target}",
        "/repos/{slug}/git/trees/{sha}?recursive=1",
        "/repos/{slug}/git/blobs/{entry.blob_sha}",
        "/repos/{slug}/pulls/{number}",  # M2; its /files sub-resource is built from it
    }
    assert 'method="GET"' in text and text.count("urllib.request.Request(") == 1


def test_only_the_cli_and_the_runners_construct_the_github_client():
    for path in FILES:
        if path.name in {"cli.py", "github.py", "__init__.py"}:
            continue
        assert "GitHubSource(" not in path.read_text(), path.name
