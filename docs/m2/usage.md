# Usage

Everything from [M1 usage](../m1/usage.md) applies (Python ≥ 3.11, PyYAML only, run from the repository root).

```bash
repo-orchestrator analyze-pr koydas/ollama-chat#12
repo-orchestrator analyze-pr https://github.com/koydas/ollama-chat/pull/12
```

The other repositories are read at the revisions given by `--revisions` (default: default-branch HEAD, resolved **once** per run); the pull request's own repository is read at the PR **base** and **head**.

## `analyze-pr` options

| Option | Default | Meaning |
|---|---|---|
| `--declarations DIR` | `declarations` | Declaration files. |
| `--revisions head\|<set>` | `head` | Revisions of the *other* repositories: `head`, or a pin set (`baseline`). A pin set's entry for the PR repository is ignored. |
| `--pin ID=SHA` | — | Pin another repository (repeatable). Pinning the PR repository is a configuration error. |
| `--head-sha SHA` | — | Offline: choose one recorded head when a pull request has several. |
| `--output-dir DIR` | `reports/` | Where `pr-impact.md` and `pr-impact.json` are written (a scan's `report.md` is never overwritten). |
| `--format md,json` | both | |
| `--cache-dir DIR` | `.repo-orchestrator/cache` | Immutable artifacts and PR records. |
| `--offline` | off | Read only from the cache; needs pinned revisions and a recorded pull request. |
| `--state-db FILE` / `--no-store` | `.repo-orchestrator/state.sqlite` | Run store; the analysis is a separate run kind. |
| `--as-of YYYY-MM-DD` | today (UTC) | Date exceptions are judged at; an input of the run. |
| `--max-requests N` | 400 | GitHub API request budget. |
| `--quiet` | off | No console summary. |

```bash
# live, read-only (bounded by --max-requests; set GITHUB_TOKEN for repeated runs)
repo-orchestrator analyze-pr koydas/gitops-homelab#<number> --no-store
# offline replay of a recorded pull request against the pinned baseline
repo-orchestrator analyze-pr koydas/ollama-chat#<number> --revisions baseline --offline \
    --cache-dir <cache with pulls/<number>/<head>.json and the head tree> --as-of 2026-11-02 --no-store
```

A live run records the pull request under `<cache>/<owner>/<name>/pulls/<n>/<head sha>.json` next to the trees and files it fetched, which is also what makes it replayable offline.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Evaluation complete and **no introduced finding**. Pre-existing findings and resolved findings do not raise the code. |
| 1 | Evaluation complete, at least one **introduced** finding. |
| 2 | Evaluation **incomplete**: the pull request, its base or head, its changed-file list or some evidence could not be collected or decided, or a delta is undetermined. Takes precedence over 1: an incomplete analysis cannot claim to have listed every introduced finding. Never a pass. |
| 3 | Configuration or runtime failure: PR repository not declared (or declared but not analyzed), unparseable reference, `--pin` of the PR repository, invalid declarations, unknown pin set, determinism violation. |

## The gate rule

**A pre-existing finding never fails a PR gate by itself.** A finding present on base and head is not caused by the pull request; it is listed (so it is not forgotten) and does not change the exit code. Only an *introduced* finding gives exit 1, and only a *complete* analysis can give 0. A baseline scan (`repo-orchestrator scan`) is the tool that fails on state; the PR analysis fails on change.

`policy_clarification` outcomes never change the exit code ([ADR-0012](../adr/0012-m2-pr-impact-contracts.md) §12).

## GitHub access

Same client as M1, same rules: `GET` only, bounded retries and budget, no write scope. M2 adds two endpoints, `GET /repos/{slug}/pulls/{n}` and `GET /repos/{slug}/pulls/{n}/files?per_page=100&page=k`. On public repositories no token is needed (60 requests per hour); a fine-grained token needs **`contents: read`** and **`pull_requests: read`** on the analyzed repositories, nothing else. No comment, review, status check, label or issue is ever written. The optional live check in CI is a manual `workflow_dispatch` step ([CI](../m1/README.md#ci)).
