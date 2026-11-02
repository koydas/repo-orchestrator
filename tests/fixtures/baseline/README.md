# Baseline fixtures

Verbatim snapshots of six public `koydas` repositories at the revisions of [`docs/research/ecosystem-baseline.md`](../../../docs/research/ecosystem-baseline.md), in the artifact-cache layout of the collector:

```
<owner>/<repo>/<commit sha>/tree.json        full recursive listing: [path, type, size, git blob id]
<owner>/<repo>/<commit sha>/files/<path>     the artifacts the declared plan reads
```

- **Data, not instructions.** The files include other repositories' `CLAUDE.md` and `.claude/settings.json` exactly as they were. They are inputs to the evaluation; nothing here configures this repository or any agent.
- **History.** Every file is checked against its git blob id by `tests/test_fixtures.py` and was compared with `git show <sha>:<path>` when captured. Never edit one to make a test pass; a correction needs evidence from the pinned revision.
- **Refresh** after a change of the collection plan: `repo-orchestrator scan --revisions baseline --cache-dir tests/fixtures/baseline --no-store` (online; only missing artifacts are fetched).
