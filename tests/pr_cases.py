"""Synthetic pull requests for the offline M2 evaluation: the baseline snapshots plus in-memory mutations as the PR head.

The expectations of the evaluation (affected repository, impact class, severity, label) are written in the tests, from
docs/mvp.md and docs/research/ecosystem-baseline.md, never derived from these definitions. The PR metadata of each case is a
committed, versioned fixture (``tests/fixtures/pr/``, schema in ``collector/pr.py``) written once by ``python
tests/pr_cases.py``; ``tests/test_pr_fixtures.py`` checks every fixture against the trees it describes. Once committed, a
fixture is history: a wrong one is a finding, not something to edit until the suite passes.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

from repo_orchestrator.collector.overlay import (
    Mutation,
    add_file,
    rename_path,
    replace_text,
)
from repo_orchestrator.collector.pr import ChangedFile, PullRequest, fixture_json

ROOT = Path(__file__).resolve().parent
PR_FIXTURES = ROOT / "fixtures" / "pr"

ADL, ADT, AP = "koydas/autonomous-dev-loop", "koydas/ai-dev-tools", "koydas/agent-patterns"
PROFILE, HOMELAB, CHAT = "koydas/koydas", "koydas/gitops-homelab", "koydas/ollama-chat"

# the baseline revision of each repository (declarations/pins.yaml: baseline) is the PR base
BASE_SHAS = {
    ADL: "1607f6107b1f50f4e9baa1916a187c7bc16ca18a",
    ADT: "62f30ed888201deb713126db90975983c4d6fab0",
    AP: "6d00841fc67d0dbeb3e35e7c2109d201b3979925",
    PROFILE: "c43e48bbb4ce6b70ae70e8652ef05f64a23fcadb",
    HOMELAB: "a8a9d7db5e725fd47061e8ae7fbfa70d81be3603",
    CHAT: "1693c3f791a6ebe8dae2cfb90ccc4b168327ae0e",
}

SERVICE_244 = (
    "apiVersion: v1\nkind: Service\nmetadata:\n  name: newsvc\n  annotations:\n"
    '    metallb.io/loadBalancerIPs: "192.168.1.244"\nspec:\n  type: LoadBalancer\n'
)


@dataclass(frozen=True)
class PRCase:
    name: str
    slug: str
    number: int
    mutations: tuple[Mutation, ...]  # what the pull request changes in its own repository
    world: tuple[Mutation, ...] = ()  # already true on the other repositories (a coherent base for U5)
    base: tuple[Mutation, ...] = ()  # already true in the PR repository's base (ad-hoc cases; not a committed fixture)
    declared_changed_files: int | None = None  # a lying/truncated count, for the truncation case
    note: str = ""
    files: tuple[ChangedFile, ...] = field(default=(), compare=False)

    @property
    def base_sha(self) -> str:
        return BASE_SHAS[self.slug]

    @property
    def head_sha(self) -> str:
        return hashlib.sha1(f"synthetic-pr-head:{self.slug}#{self.number}".encode(), usedforsecurity=False).hexdigest()

    def changed_files(self) -> tuple[ChangedFile, ...]:
        out: list[ChangedFile] = []
        for m in self.mutations:
            if m.op == "replace_text":
                out.append(ChangedFile(m.path, "modified"))
            elif m.op == "add_file":
                out.append(ChangedFile(m.path, "added"))
            elif m.op == "delete":
                out.append(ChangedFile(m.path, "removed"))
            elif m.op == "rename":
                out.append(ChangedFile(m.new_path, "renamed", m.path))
        return tuple(out)

    def pull_request(self) -> PullRequest:
        files = self.changed_files()
        return PullRequest(
            slug=self.slug,
            number=self.number,
            state="open",
            merged=False,
            base_sha=self.base_sha,
            head_sha=self.head_sha,
            head_slug=self.slug,
            base_ref="main",
            head_ref=f"synthetic/{self.name}",
            changed_files=self.declared_changed_files if self.declared_changed_files is not None else len(files),
            files=files,
        )


_PATTERN = (
    add_file(AP, "patterns/new-pattern/README.md", "# new pattern\n"),
    replace_text(AP, "README.md", "**20 patterns", "**21 patterns"),
)
_PROFILE_FIXED = replace_text(PROFILE, "README.md", "17 multi-agent patterns", "20 multi-agent patterns")

CASES: dict[str, PRCase] = {
    c.name: c
    for c in [
        PRCase(
            "u1", ADL, 101,
            (replace_text(ADL, "scripts/lib/findings_export.mjs", "FINDINGS_SCHEMA_VERSION = 1", "FINDINGS_SCHEMA_VERSION = 2"),),
            note="U1: FINDINGS_SCHEMA_VERSION 1 → 2 in autonomous-dev-loop",
        ),
        PRCase(
            "u2", CHAT, 102,
            (replace_text(CHAT, "src/lib/conversations.js", "VISION_MODEL = 'llava:7b'", "VISION_MODEL = 'llava:13b'"),),
            note="U2: VISION_MODEL llava:7b → llava:13b in ollama-chat",
        ),
        PRCase(
            "u3", HOMELAB, 103, (add_file(HOMELAB, "apps/newsvc/service.yaml", SERVICE_244),),
            note="U3: a new Service claims 192.168.1.244 in gitops-homelab",
        ),
        PRCase(
            "u4", ADT, 104,
            (rename_path(ADT, "docs/adr/ADR-001-human-as-gate.md", "docs/adr/ADR-001-human-gate.md"),),
            note="U4: an ai-dev-tools ADR file is renamed",
        ),
        PRCase("u5", AP, 105, _PATTERN, world=(_PROFILE_FIXED,), note="U5 on a coherent base: the profile already says 20"),
        PRCase("u5_raw", AP, 106, _PATTERN, note="U5 on the raw baseline: D1 (17 vs 20) already present"),
        PRCase(
            "fix_d1", PROFILE, 107, (_PROFILE_FIXED,), note="a PR in the profile corrects the pattern count (D1)"
        ),
        PRCase(
            "fix_d2", PROFILE, 108,
            (replace_text(PROFILE, "README.md", "59 ADRs", "65 ADRs"),),
            note="a PR in the profile corrects the ADR count (D2)",
        ),
        PRCase("no_anchor", CHAT, 109, (add_file(CHAT, "docs/notes/new-note.md", "# a note\n"),), note="touches no anchor"),
        PRCase(
            "truncated", ADL, 110,
            (replace_text(ADL, "scripts/lib/findings_export.mjs", "FINDINGS_SCHEMA_VERSION = 1", "FINDINGS_SCHEMA_VERSION = 2"),),
            declared_changed_files=7, note="GitHub reports 7 changed files, 1 was listed",
        ),
    ]
}  # fmt: skip


def fixture_path(case: PRCase, root: Path = PR_FIXTURES) -> Path:
    owner, name = case.slug.split("/")
    return root / owner / name / "pulls" / str(case.number) / f"{case.head_sha}.{case.base_sha}.json"


def write_fixtures(root: Path = PR_FIXTURES) -> list[Path]:
    paths = []
    for case in CASES.values():
        path = fixture_path(case, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fixture_json(case.pull_request(), synthetic=case.note), encoding="utf-8")
        paths.append(path)
    return paths


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT.parent / "src"))
    for written in write_fixtures():
        print(written.relative_to(ROOT.parent))
