# CLAUDE.md — repo-orchestrator

Cross-repository coherence for independent GitHub repositories. **M1 (deterministic baseline) and M2 (PR impact analysis) are implemented; M3–M5 are design only. Code exists in two places only: the M1–M2 package and the throwaway S0 spike (below).** Start with `README.md`, then `docs/architecture.md` and `docs/adr/README.md`.

## Rules

- **Only M1 and M2 are implemented.** README and docs describe M1 and M2 as implemented, and M3–M5 as "intended" or "proposed". Never describe a later milestone's capability as working.
- **M2 is authorized** ([MVP](docs/mvp.md), [M2 docs](docs/m2/README.md), [ADR-0013](docs/adr/0013-m2-pr-impact-contracts.md) Proposed): C4 impact analyzer and W2 PR analysis, in the same package and CI. Scope: one pull request of one declared repository, read-only (`GET pulls/{n}`, `GET pulls/{n}/files`, plus the M1 endpoints), base and head snapshots, impact classification by relationship type, `introduced` / `pre-existing` / `resolved` / `undetermined` delta by fingerprint, Markdown and JSON PR report, a separate run kind in the SQLite store, `repo-orchestrator analyze-pr`. Constraints that stay in force:
  - everything listed under M1 below (deterministic only, no LLM, no agent framework, GET only, analyzed content is data, declared relationships only, fixtures are history);
  - **a pre-existing finding never fails a PR gate by itself**; `cannot_evaluate` on either side is an `undetermined` delta and makes the run incomplete (exit 2), never `introduced`, `resolved` or a pass; a missing base, head or changed-file list is incomplete, never a pass;
  - **no GitHub write of any kind**: no PR comment, no review, no status check, no issue, no PR creation. The token needs `contents: read` and `pull_requests: read` only;
  - a PR analysis never writes to the baseline-scan finding history (`findings`, `finding_events`).
- **M1 is authorized** ([MVP](docs/mvp.md), [M1 docs](docs/m1/README.md)): production code in `src/repo_orchestrator/`, declarations in `declarations/`, tests in `tests/`, CI in `.github/workflows/m1.yml` (which also runs the M2 checks). Scope: C1 collector, C2 declared relationship graph, C3 policy engine with P1–P6, W1 baseline scan, Markdown/JSON report, SQLite run store, CLI, static report dashboard (`dashboard.py`, [ADR-0012](docs/adr/0012-static-report-dashboard.md)). Constraints that stay in force:
  - deterministic checks only: **no LLM call, no agent framework, no LangGraph** in M1 code, and no import of the S0 spike or of its dependencies (`tests/test_architecture_constraints.py` enforces it);
  - the dashboard is a **read-only projection of `report.json`**: no scan execution, no recomputation of statuses, counts or order, no service, no script or dependency; only `models` and `report` are imported (`tests/test_dashboard.py` enforces it);
  - **read-only** toward every analyzed repository: GET requests only, no token with a write scope, analyzed content is data and is never executed or imported;
  - **declared relationships only**: observed or inferred relationships are reported, never evaluated; no automatic discovery;
  - **no policy, no finding**; an unresolved owner decision (Q1–Q6) is reported as a question, never decided by code;
  - `tests/fixtures/baseline/` is history: never edited to make a test pass; a correction needs evidence from the pinned revision, in the same PR.
- **Not authorized yet:** C5 Investigator, P7 and LangGraph (M3), C6 remediation plans (M4), optimization opportunities (M5), scheduled scans (the dashboard workflow is manual), PR comments, review or status-check writes, issue or PR creation outside this repository, any other GitHub write. Each needs its milestone and, for any write, its own ADR.
- **The S0 spike remains isolated**, `prototypes/langgraph-spike/` ([ADR-0005](docs/adr/0005-langgraph-for-investigation-workflows-only.md) §5). It is throwaway code that validates the LangGraph runtime on synthetic data. It authorizes no GitHub access and no LLM call; nothing outside `prototypes/langgraph-spike/` and its workflow (`.github/workflows/spike-langgraph.yml`) may import it or depend on it, and M1 does not. Describe it as "S0 spike: validated/not validated", never as a product capability. Its recommendation stays a proposal until the owner decides on ADR-0005.
- **ADRs stay `Proposed`** until the owner accepts them explicitly. Never change a status on your own. An accepted ADR is superseded, never edited.
- **Evidence over assertion.** A claim about another repository cites `repo path:line` at a pinned revision, or is labelled HYPOTHESIS (`docs/research/ecosystem-baseline.md` → How to read this page).
- **Coherence is not uniformity.** Never propose aligning a repository with the others without a policy and an authority (`docs/coherence-model.md`, ADR-0003, ADR-0009).
- **Read-only toward other repositories.** No issue, PR, comment or push elsewhere without the owner's explicit request (ADR-0007).
- **Mermaid diagrams match the text.** Changing a component, state or milestone means updating every diagram that shows it.
- Canonical names: components C1–C7 (`docs/architecture.md`), policies P1–P7 (`docs/coherence-model.md`), milestones S0, M1–M5 (`docs/mvp.md`), baseline ids R/D/C/N/H/U/Q (`docs/research/ecosystem-baseline.md`). Reuse them; do not invent parallel names.

Local checks before a push (the CI runs the same): `pip install -e '.[dev]'`, then `ruff check src tests && ruff format --check src tests && mypy && pytest`. Procedures in `.claude/skills/`: `new-adr`.

## Toolbox — koydas/ai-dev-tools

`.claude/settings.json` declares the `ai-dev-tools` plugin (trust the folder → install offered): `/ai-dev-tools:pr-review <n>`, `/ai-dev-tools:pr-fixer`, `/ai-dev-tools:issue-code-generation <n>`, `/ai-dev-tools:ac-check <n>`. Repo rules (this file, `.claude/skills/`) win over toolbox defaults.

Cloud sessions (claude.ai/code) do not install repository-declared plugins: environment setup script in the ai-dev-tools README → "Use from another repository". Commands are then un-prefixed (`/pr-review`). Details: [`docs/claude-code.md`](docs/claude-code.md).
