# Known limitations

What M2 does not do, and where its answers can be wrong. Nothing below is hidden: where it matters, the report says so. The [M1 limitations](../m1/limitations.md) (extractors, collection bounds, public repositories only, one-machine state) apply unchanged.

## Scope

- **One pull request, one declared repository, six repositories.** A PR in a repository outside the declared, analyzed set is a configuration error (exit 3). Two pull requests that are each fine and interact when both merge are not analyzed together.
- **Declared footprints only.** A relationship is touched only through its declared anchors, the artifacts its policies read, the files a living document links (P5) and its authority artifacts. A dependency nobody declared is invisible: **no impact reported is not evidence of coherence**. R4, R10, R11 have no anchors and are never touched; R6 has anchors but no policy, so it is reported as *potential impact only*.
- **No P7, no investigation (M3), no remediation plans (M4), no opportunities (M5).** The reports list findings and impact; they propose no fix order. The "manifest comment naming the consumer becomes false" part of baseline U2 is not detected.
- **The static dashboard ([M1 dashboard](../m1/dashboard.md)) renders scan reports only.** Pointed at a directory that also holds a `pr-impact.json`, it skips that file with a warning (`semantic.inputs.scope` is missing) and exits 0; a PR report is never shown as a scan. Rendering PR reports is not implemented.
- **No acknowledgement, dismissal or PR comment.** The report is two local files; the analysis never writes to GitHub (no comment, review, status check, label or issue).

## Impact classification

- **The impact class is by relationship type, not by what changed.** Any edit of an anchored file touches the relationship and reports its class (for example `breaks` for R3 after any edit of the producer file), even when no extracted value changed. The changed values are listed separately (`value_changes`) and the exit code depends on findings only.
- **One hop.** Only relationships incident to the PR repository are traversed, plus those governed by a changed authority artifact. A change that reaches a third repository through two relationships is not followed.
- **"Affected repository" means two things.** The report's `affected_repos` is the repository the pull request can break, falsify or outdate; a finding's own `affected_repos` (M1 semantics) names the sides of the inconsistency (U2: `homelab` and `chat`).
- **Direction `self`.** A PR in the repository that makes a claim (the profile) can only stale its own claim; the report names the PR repository as affected.

## Delta labels

- **Identity is the fingerprint (policy, relationship, subject, locators), not the value.** A pre-existing finding that a pull request makes *worse* (D1 at 17 vs 20 becomes 17 vs 21: U5 on the raw baseline) is still `pre-existing` and does not fail the gate. The report shows both values (`value_changes`, the comparison on the finding), so a human can see it; the gate does not.
- **A finding whose fingerprint changes** (its locators move) is reported as one resolved and one introduced.
- **`undetermined` stops the gate** (exit 2) until the missing evidence is available; it is a deliberate over-report. A base-side `cannot_evaluate` that has no head finding behind it is listed (`base_unevaluated`) and does not by itself make a run incomplete.
- **Exceptions** are judged at `--as-of` on both sides, like in M1. A PR that removes the authority of an exception turns an `excepted` finding into `introduced`.

## Snapshots and the change set

- **The base is taken as GitHub reports it** (`base.sha`). When it is not the merge base (the base branch moved after the PR was opened and the PR was not updated), the trees differ in paths the PR did not change: that is `pr_file_list_mismatch`, an incomplete run, not a guess. Rebase or merge the base into the PR, or analyze it after merge. Using the merge base would need the compare endpoint (open question in ADR-0013).
- **The file list is capped at 3000 files** by GitHub; a PR at the cap, or whose list is shorter than GitHub's own count, is `pr_files_truncated` (incomplete). The trees remain the evidence for the paths they show, but the run is not a pass.
- **A truncated tree** (very large repositories) cannot be cross-checked: `tree_truncated`, incomplete.
- **Fork heads** are read through the base repository. If GitHub does not serve the commit there, the head is unavailable (incomplete).
- **The other repositories are read at one revision per run.** With `--revisions head` that is the default-branch HEAD at the time of the run, so two analyses of the same pull request at different times can differ; pin them (`--revisions`, `--pin`) to compare.
- **A merged pull request** is analyzed from its recorded base and head, not as a merge result: the analysis says what the change did against the base it was written on.
- **Only the inputs recorded when the analysis ran.** A live run always asks GitHub for the current head and base and records them as `<head>.<base>.json`, so a base that moved without a new head does not overwrite the earlier record; an offline replay chooses among several with `--head-sha` / `--base-sha`.

## Evaluation

- **The offline evaluation replays synthetic pull requests** (the baseline snapshots plus in-memory mutations, with committed PR records flagged `synthetic`). They prove the mechanics against the baseline's expectations; they are not a sample of real pull requests. The live read-only analysis of a real pull request, or why it could not be done, is recorded in the [evaluation](evaluation.md#live-read-only-analysis).
- **The request cost of a live analysis** is bounded by `--max-requests` and measured only where a live run was possible (see the evaluation).

## Security notes

- Same rules as M1: repository content is data (no code executed or imported), `GET` only, tokens never written to reports, logs or the store. PR metadata (titles, branch names) is not rendered into the report beyond the head and base refs kept in the volatile `execution` part. File paths from the API are validated (no absolute path, no `..`, no backslash) before they are used as cache keys or matched.
- The PR reference given to the CLI is validated against `owner/repo#number` or a `github.com` pull request URL; the optional CI live check passes it through the environment, never through the shell command line.
