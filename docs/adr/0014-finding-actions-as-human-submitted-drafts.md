# ADR-0014: Finding actions on the dashboard are human-submitted drafts

- **Date:** 2026-11-03
- **Status:** Proposed

## Context

A reader of a finding on the [report dashboard](0012-static-report-dashboard.md) has three natural next steps: file an issue, hand the fix to an agent, open a pull request. [ADR-0007](0007-read-only-operation-and-human-gated-remediation.md) puts a draft issue or PR "posted only after explicit per-item approval" at autonomy level 2, which needs its own ADR, and requires any write path to run under a distinct identity, mark what it creates as machine-originated and stay out of the triggers of the pipelines it touches. [ADR-0012](0012-static-report-dashboard.md) forbids scripts, services and write tokens, and lists remediation actions under "not shown".

## Decision

1. **Three actions per finding, and only per finding** (confirmed `inconsistent` outcomes; never the other statuses):
   - **Open issue**: a link to `https://github.com/<owner>/<repo>/issues/new` with `title` and `body` pre-filled.
   - **Create prompt**: the finding as text plus fixed instructions, in a `<details>` block to select and paste into an agent session.
   - **Create PR**: a link to `https://github.com/<owner>/<repo>/compare/repo-orchestrator/<fingerprint>?expand=1` with `title` and `body` pre-filled.
2. **The dashboard writes nothing.** The links are plain GET `href`s; GitHub shows its own form and a human submits it with their own account. No script, no form, no token, no request from the page or the generator. ADR-0012's constraints are unchanged.
3. **One target repository per finding**: the repository of the first fix site, else the first affected repository, resolved through the run's pinned revisions. If none resolves, no action is shown.
4. **The three actions share one brief**, built from the report only (policy, relationship, subject, severity, impact, fingerprint, reason, pinned revisions, fix sites with links at the pinned commit, investigate list). Every string is cleaned of control characters, URL-encoded, and clipped so the link stays under 5 000 encoded characters.
5. **Create PR depends on a branch that must already exist.** The prompt instructs the agent to commit on `repo-orchestrator/<fingerprint>`, push it, and **not** open the PR nor touch another repository; the link is the human step that follows. The link is dropped when the fingerprint is not a safe branch name.
6. **Machine origin is stated in the text** ("Finding reported by repo-orchestrator (run …)") and in the branch prefix. The issue link requests exactly one label, the neutral `repo-orchestrator` (traceability, filtering, duplicate search). It is never a label a pipeline reacts to: `ready-for-dev` and the review labels of `autonomous-dev-loop` are never requested, and adding one by hand is the human's decision to enter that pipeline (ADR-0007 §4). The PR link requests no label. GitHub ignores a label that does not exist in the target repository.
7. **No write path before M4.** Creating the issue or the PR from the dashboard (a distinct identity with write scope, or an agent producing the patch) is deferred to M4 and its own ADR; this ADR covers drafts only.

## Rationale

- A pre-filled form keeps the gate where ADR-0007 puts it, at the submit button, with no write identity to manage.
- A prompt block needs no clipboard script; `user-select: all` makes it one click to select.
- Branch name from the fingerprint makes the PR link deterministic and ties the PR to the finding without any state.

## Alternatives considered

- **Buttons that call the GitHub API.** Rejected: needs a script and a write token, i.e. ADR-0007 level 2/3 with a distinct identity, and breaks ADR-0012 §1.
- **A "copy" button.** Rejected: needs JavaScript.
- **Create PR with a generated patch.** Rejected: M4 (remediation plans); the report carries no diff.
- **No label at all.** Rejected: no way to filter or deduplicate by origin. **A pipeline label** (`ready-for-dev`). Rejected: finding → issue → code → PR with no human between, the re-entry ADR-0007 describes.

## Consequences

- ✅ One click from a finding to a reviewed draft; no new permission, dependency or service.
- ⚠️ The issue and PR texts are not updated if the finding changes; they quote the run they came from.
- ⚠️ Create PR 404s until the branch is pushed; the note under the buttons says so.
- ⚠️ Nothing prevents duplicate issues for the same fingerprint; the fingerprint in the body is what to search for.
- ⚠️ The prompt is advice to an agent, not a guarantee; its output is reviewed in the target repository like any change.

## Open questions

- Should a published issue be recognised on the next run (fingerprint search) and shown as "already filed"? That needs a read of the target repository and belongs with the opportunity lifecycle ([ADR-0009](0009-optimization-opportunities-as-first-class-output.md)).
