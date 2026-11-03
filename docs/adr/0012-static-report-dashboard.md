# ADR-0012: A static, read-only report dashboard generated from `report.json`

- **Date:** 2026-11-03
- **Status:** Proposed

## Context

M1 writes `report.md` and `report.json` per scan and records runs in a local SQLite file ([ADR-0011](0011-m1-outcome-contracts-and-reproducible-reports.md), [ADR-0008](0008-git-declarations-and-local-sqlite-state.md)). Both are good for a CI log and for a reviewer who knows the vocabulary; neither lets someone browse several runs, see at a glance which outcomes are confirmed inconsistencies and which are unknowns, or open the pinned evidence from a phone.

The two sibling repositories already publish dashboards with one visual identity ([`ai-dev-tools` ADR-021](https://github.com/koydas/ai-dev-tools/blob/main/docs/adr/ADR-021-uniform-dashboards.md), `autonomous-dev-loop` `scripts/lib/dashboard_ui.mjs`): generated static HTML, no script, tokens for a light and a dark theme, a header with a companion link, a phone tab bar, one-screen overviews, and a `status.json`. Both are Node scripts in Node repositories. This repository is a pure-Python package with one runtime dependency.

The decision must not weaken M1's contracts: a report has a *semantic* part (reproducible, digest-sealed) and a volatile *execution* part; seven outcome statuses of which only `inconsistent` is a confirmed finding; a run is `complete` or `partial`; a finding absent from a partial run is not resolved.

## Decision

1. **A static generator, no service.** `repo-orchestrator dashboard --reports DIR --out DIR` turns `report.json` files into HTML: Overview, Reports, one Report detail per run, plus a `status.json` in the sibling shape. Standard library only, no script, no external asset, no timestamp of its own: identical reports give byte-identical pages. It is a *consumer* of C7's output, not a new component; it imports `models` and `report` only (never the engine, collector, policies or store), enforced by a test.
2. **`report.json` is the contract; nothing is recomputed.** Counts, statuses, run status and finding order are displayed as the report states them. The generator **validates** before drawing: the M1 structure, that each section holds only its own status (a `cannot_evaluate`, `policy_*` or `excepted` outcome inside `findings` makes the file invalid), that `summary` equals what the sections list, that `result_digest` matches the semantic content (using `models.digest`, not a copy), and that a recorded `run_key` matches the inputs. A file that fails is skipped **visibly** (a notice on the pages, a warning on stderr), never silently and never aborting the other reports.
3. **Run identity and deduplication use the run store's formula** (`digest([digest(inputs), result_digest])[:32]`, ADR-0011 §5), computed from the report, so a report written with `--no-store` has the same identity as a stored one. Duplicates keep the earliest execution. Order: execution time (else the `as_of` date), newest first, ties by run key.
4. **History is the set of published reports.** The SQLite store is not read: it is local, and it does not hold the semantic report (coverage, authorities, evidence context). Each built site republishes the original JSON of every report as `reports/<run_key>.json` with a manifest `reports/index.json`. The Pages workflow restores that manifest's files from the live site before building, adds the report of a manual run (`baseline` or `live`), and redeploys. The workflow is `workflow_dispatch` only (plus a rebuild when the generator changes): scheduled scans are not authorized.
5. **Visual family.** Tokens, header, companion links (`Dev loop ⇄`, `Tools ⇄`), phone tab bar and `prefers-color-scheme` theme are copied from the siblings (ADR-021 accepts copying about 15 values). Three views only; no sidebar, no theme toggle, no charts. The run status (outlined: neutral for `complete`, amber for `partial`) is visually distinct from outcome statuses (green `consistent`, red `inconsistent` only, amber for questions, conflicts and failures, neutral for unknowns and exceptions).
6. **Safety.** Every report string is HTML-escaped. GitHub links are built from validated parts (`owner/name`, a 40-hex commit, a path without `..`, empty or control segments), at the commit the run pinned; a URL found in a report is never used, and an invalid part degrades to plain text.
7. **Not shown, by design:** any coherence percentage or score, trend, "run scan" action, remediation plan, or generated explanation. The mockup's equivalents are listed in [the dashboard documentation](../m1/dashboard.md#the-mockup-is-a-reference-not-a-specification).

## Rationale

- A report viewer needs no state beyond the reports. Static files on GitHub Pages cost nothing to run, cannot be down, and cannot diverge from the data they show.
- Python keeps one toolchain (`ruff`, `mypy --strict`, `pytest`, one CI job) and lets the generator reuse `models.digest` instead of re-implementing the digest that seals a report. A copy of that algorithm in JavaScript would be exactly the kind of cross-repository convention this project exists to detect.
- Validating against the contract, rather than rendering whatever parses, is what makes "never show `cannot_evaluate` as a confirmed finding" a property of the generator and not of the discipline of its inputs.
- Carrying history on the published site needs no new store, no write token (`contents: read`), and no branch; the original JSON is the history.

## Alternatives considered

- **A Node generator like the siblings.** Rejected: a second toolchain and CI job in a Python repository, and a re-implementation of the digest check. Kept as the cost of the copy: the shell and tokens drift by hand, as in ADR-021.
- **React/Vue SPA, charting library, CSS framework.** Rejected: nothing here needs client-side state; it adds a build chain, a dependency to audit and a second place to implement report semantics.
- **A service over the SQLite store.** Rejected: a backend and a deployment for read-only browsing ([ADR-0008](0008-git-declarations-and-local-sqlite-state.md) §5), and the store lacks the semantic report.
- **Reading `state.sqlite` for the run list.** Rejected for the reasons in decision 4; the per-run `execution.history` transitions already travel in `report.json`.
- **Persisting reports on a `reports` branch** (as `autonomous-dev-loop` does with `metrics`). Viable; rejected for now because it needs `contents: write`. Revisit if the Pages carrier proves fragile.
- **Rendering `report.md`.** Rejected: it is a lossy rendering of the semantic part (no execution, no machine-readable evidence).
- **A coherence percentage, as in the mockup.** Rejected: outcomes are not commensurable, and a ratio over a partial run would turn unknowns into an implied score.

## Consequences

- ✅ Reports are browsable, linkable and readable on a phone; evidence opens at the pinned commit.
- ✅ Partial and unknown results are shown as such, on every page that summarizes a run.
- ✅ No infrastructure, no new dependency, no change to the analysis engine or the report contract.
- ⚠️ The published site is the history: a failed restore silently shortens it (the build logs which files were not restored; the Reports page counts what it has). Deleting the site loses history that was not also kept as an artifact.
- ⚠️ The site grows by about 300 KB per distinct run (the original JSON plus its page) and has no retention policy; manual runs keep that small.
- ⚠️ Reports written with `--no-store` (as the workflow does) carry no `execution.history`: the pages say "not recorded" rather than infer a transition.
- ⚠️ The manifest and file names are a (small) new published contract; the manifest is validated by name before use.
- ⚠️ Tokens and shell are copies of the siblings'; a palette change there is repeated here by hand.

## Open questions

- Should the workflow also attach each run's `report.json` as a build artifact, so history survives a deleted Pages site?
- Should the siblings' dashboards link here (and `status.json` be read by the companion switch)? ADR-021 leaves reading the other's status out.
