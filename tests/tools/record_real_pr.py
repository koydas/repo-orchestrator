"""Dev tool (not product code, never imported by ``src/``): record a REAL pull request into the offline cache format.

The REST ``pulls`` endpoints cannot be reached from every environment (this one could read public repositories through git
only). This tool builds the same facts from a local clone of the PR repository: the head SHA, the base SHA given on the
command line (a merged pull request's base is the first parent of its merge commit), and the changed files from
``git diff --name-status -M``. It then runs the product's own analysis through the product's own
:class:`~repo_orchestrator.collector.cache.CachingSource`, so exactly what the analysis reads is recorded in
``<out>/<owner>/<name>/<sha>/`` and ``<out>/<owner>/<name>/pulls/<n>/<head>.json``. Repositories other than the PR repository
come from the committed baseline fixtures; their already-committed directories are removed from the capture.

Only ``git`` read commands run, on a clone the caller made; nothing from the repository is executed or imported.

    python tests/tools/record_real_pr.py --clone ~/koydas/gitops-homelab --slug koydas/gitops-homelab \
        --number 2 --base <sha> --head <sha> --merged --out tests/fixtures/real-pr
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from repo_orchestrator.collector.cache import CachingSource  # noqa: E402
from repo_orchestrator.collector.pr import ChangedFile, PullRequest  # noqa: E402
from repo_orchestrator.collector.source import SourceError, Tree, TreeEntry  # noqa: E402
from repo_orchestrator.declarations import load_declarations  # noqa: E402
from repo_orchestrator.engine import Engine, RunOptions  # noqa: E402
from repo_orchestrator.pr_runner import analyze_pull_request  # noqa: E402

STATUS = {"A": "added", "M": "modified", "D": "removed", "T": "changed"}


def git(clone: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(clone), *args], check=True, capture_output=True).stdout


class GitRepoSource:
    """SnapshotSource + PullRequestSource for one repository, read from a local clone with git plumbing."""

    def __init__(self, clone: Path, slug: str, pr: PullRequest, fallback: CachingSource) -> None:
        self.clone, self.slug, self.pr, self.fallback = clone, slug, pr, fallback

    def resolve(self, slug: str, ref: str | None) -> str:
        if slug != self.slug:
            return self.fallback.resolve(slug, ref)
        assert ref is not None
        git(self.clone, "cat-file", "-e", f"{ref}^{{commit}}")
        return ref

    def tree(self, slug: str, sha: str) -> Tree:
        if slug != self.slug:
            return self.fallback.tree(slug, sha)
        entries = []
        for line in git(self.clone, "ls-tree", "-r", "-t", "-l", sha).decode("utf-8").splitlines():
            meta, _, path = line.partition("\t")
            _mode, kind, blob, size = meta.split()
            if kind in ("blob", "tree"):
                entries.append(TreeEntry(path, kind, None if size == "-" else int(size), blob))
        return Tree(sha, tuple(sorted(entries, key=lambda e: e.path)))

    def read(self, slug: str, sha: str, entry: TreeEntry) -> bytes:
        if slug != self.slug:
            return self.fallback.read(slug, sha, entry)
        return git(self.clone, "cat-file", "blob", entry.blob_sha)

    def pull_request(self, slug: str, number: int, head_sha: str | None = None) -> PullRequest:
        if (slug, number) != (self.pr.slug, self.pr.number):
            raise SourceError("pull_request_not_found", f"{slug}#{number}")
        return self.pr


def changed_files(clone: Path, base: str, head: str) -> tuple[ChangedFile, ...]:
    out = []
    for line in git(clone, "diff", "--name-status", "-M", base, head).decode("utf-8").splitlines():
        parts = line.split("\t")
        code = parts[0][0]
        if code == "R":
            out.append(ChangedFile(parts[2], "renamed", parts[1]))
        elif code in STATUS:
            out.append(ChangedFile(parts[1], STATUS[code]))
        else:
            raise SystemExit(f"unsupported git status {line!r}")
    return tuple(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clone", type=Path, required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--number", type=int, required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--merged", action="store_true")
    ap.add_argument("--note", default="")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    base, head = args.base.lower(), args.head.lower()
    files = changed_files(args.clone, base, head)
    pr = PullRequest(args.slug, args.number, "closed" if args.merged else "unknown", args.merged, base, head, args.slug,
                     "main", f"pull/{args.number}/head", len(files), files)  # fmt: skip
    baseline = CachingSource(None, ROOT / "tests" / "fixtures" / "baseline")
    capture = args.out
    source = CachingSource(GitRepoSource(args.clone, args.slug, pr, baseline), capture)  # type: ignore[arg-type]
    decl = load_declarations(ROOT / "declarations")
    pins = dict(decl.pins["baseline"].revisions)
    from datetime import date

    analysis = analyze_pull_request(
        Engine(decl), source, args.slug, args.number, RunOptions(revisions=pins, as_of=date(2026, 11, 2))
    )  # type: ignore[arg-type]
    # what the baseline fixtures already hold is not part of this capture
    owner_dirs = [p for p in capture.iterdir() if p.is_dir()]
    for owner in owner_dirs:
        for name in [p for p in owner.iterdir() if p.is_dir()]:
            for sha_dir in [p for p in name.iterdir() if p.is_dir() and p.name != "pulls"]:
                committed = ROOT / "tests" / "fixtures" / "baseline" / owner.name / name.name / sha_dir.name
                if committed.is_dir():
                    shutil.rmtree(sha_dir)
        for name in [p for p in owner.iterdir() if p.is_dir()]:
            if not any(name.iterdir()):
                name.rmdir()
    record = capture / args.slug / "pulls" / str(args.number) / f"{head}.json"
    data = json.loads(record.read_text())
    data["provenance"] = "recorded from a local git clone, not from the REST API: " + (
        args.note or "head and base given by the recorder; changed files from git diff --name-status -M"
    )
    record.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"exit": analysis.exit_code, "gate": analysis.semantic["gate"]["result"], "deltas": analysis.semantic["summary"]["deltas"],
                      "touched": [e["relationship_id"] for e in analysis.semantic["impact"]]}))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
