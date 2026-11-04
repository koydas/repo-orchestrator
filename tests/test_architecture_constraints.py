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


INV = SRC / "investigation"
FRAMEWORK = {"langgraph", "langchain", "langchain_core", "pydantic"}


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(SRC)))
def test_no_dependency_on_the_s0_prototype_or_any_llm_sdk(path):
    """Nothing imports the S0 spike or a model SDK / HTTP library (ADR-0014: the provider uses the standard library)."""
    forbidden = {"s0_spike", "anthropic", "openai", "requests", "httpx", "httpx2"}
    assert not imports(path) & forbidden, f"{path.name} imports {imports(path) & forbidden}"
    text = path.read_text()
    assert "prototypes" not in text and "s0_spike" not in text


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(SRC)))
def test_only_the_workflow_module_imports_langgraph(path):
    """ADR-0016 §1: LangGraph (and its stack) is confined to ``investigation/workflow.py``."""
    found = imports(path) & FRAMEWORK
    if path == INV / "workflow.py":
        assert "langgraph" in found
    else:
        assert not found, f"{path.relative_to(SRC)} imports {found}"


def test_m1_and_m2_code_never_imports_the_investigation_package():
    """The deterministic core is LLM-free and framework-free by construction: no module outside ``investigation/`` imports
    it, except the CLI, and the CLI only inside functions (lazily), so the default commands never load it."""
    for path in FILES:
        if INV in path.parents:
            continue
        tree = ast.parse(path.read_text())
        top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        for node in top_level:
            mod = getattr(node, "module", None) or ""
            names = [a.name for a in node.names]
            assert "investigation" not in mod.split(".") and "investigation" not in names, (
                f"{path.name} imports the investigation package at module level"
            )
        if path.name != "cli.py":
            assert "investigation" not in path.read_text().replace("investigation.yaml", ""), path.name


def test_only_the_provider_module_touches_the_model_provider_and_the_key():
    for path in FILES:
        text = path.read_text()
        if path == INV / "provider.py":
            assert "ANTHROPIC_API_KEY" in text and "api.anthropic.com" in text
            assert "print(" not in text and "logging" not in text, "the provider never prints or logs"
            continue
        assert "api.anthropic.com" not in text and "x-api-key" not in text, path.name
        if path.name != "cli.py":
            assert "ANTHROPIC_API_KEY" not in text, path.name


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


def test_only_the_collector_package_and_the_provider_perform_network_io():
    for path in FILES:
        if "collector" in path.parts or path == INV / "provider.py":
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


# ---- M3 (CLAUDE.md: exactly one LLM-calling component; the Investigator reads only through the pinned reader) --------------


def test_investigation_modules_do_no_io_beyond_their_declared_one():
    """store.py owns SQLite, workflow.py the checkpoint connection, provider.py the network; the rest is pure or reads
    through the PinnedReader (no file, no socket, no database, no process)."""
    allowed_db = {INV / "store.py", INV / "workflow.py"}
    for path in sorted(INV.glob("*.py")):
        found = imports(path)
        assert not found & {"subprocess", "socket", "shutil", "tempfile", "os.path", "ssl", "http"}, path.name
        if path != INV / "provider.py":
            assert not found & {"urllib.request", "urllib.error"}, path.name
        if path not in allowed_db:
            assert "sqlite3" not in found, path.name
        if path not in allowed_db | {INV / "pipeline.py", INV / "provider.py"}:
            text = path.read_text()
            assert "open(" not in text and "read_text" not in text and "write_text" not in text, path.name


def test_the_investigator_has_no_write_shell_or_network_tool():
    from repo_orchestrator.investigation import prompt

    assert set(prompt._TOOL_ARGS) == {"read", "search", "list"}
    for name in ("tools.py", "reader.py", "prompt.py", "verify.py"):
        path = INV / name
        assert not imports(path) & {
            "os",
            "pathlib",
            "subprocess",
            "socket",
            "urllib",
            "shutil",
            "sqlite3",
            "importlib",
        }, name
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"open", "print", "input"}, f"{name}:{node.lineno}"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert not node.func.attr.startswith("write"), f"{name}:{node.lineno}"


def test_the_optional_dependencies_are_pinned_and_the_runtime_dependency_stays_pyyaml():
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    extra = project["optional-dependencies"]["investigation"]
    assert extra and all("==" in d for d in extra), "the investigation extra is pinned (S0 constraint 6)"
    assert any(d.startswith("langgraph==") for d in extra)
