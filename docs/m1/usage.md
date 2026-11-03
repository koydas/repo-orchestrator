# Usage

Python ≥ 3.11. The only runtime dependency is PyYAML.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'          # package + pytest, ruff, mypy
repo-orchestrator validate       # load and validate declarations/, collect nothing
repo-orchestrator scan           # baseline scan of the six repositories at their default-branch HEAD
```

`python -m repo_orchestrator …` is equivalent. Run from the repository root (the default `--declarations` is `./declarations`).

## `scan` options

| Option | Default | Meaning |
|---|---|---|
| `--repos adl,adt` | every analyzed repository | Restrict the scope. A relationship is evaluated only when both endpoints are selected; the others are listed as `outside_scope`. |
| `--revisions head\|<set>` | `head` | `head` reads each default branch HEAD. A named set from `declarations/pins.yaml` pins every repository (`baseline` = the ecosystem baseline). |
| `--pin ID=SHA` | — | Pin one repository (repeatable), overriding the set. |
| `--output-dir DIR` | `reports/` | Where `report.md` and `report.json` are written. |
| `--format md,json` | both | |
| `--cache-dir DIR` | `.repo-orchestrator/cache` | Immutable artifact cache keyed by repository, SHA and path. |
| `--offline` | off | Read only from the cache; requires pinned revisions. |
| `--state-db FILE` / `--no-store` | `.repo-orchestrator/state.sqlite` | Run store (SQLite); `--no-store` skips it. |
| `--as-of YYYY-MM-DD` | today (UTC) | The date exceptions are judged at. It is an input of the run. |
| `--max-requests N` | 400 | GitHub API request budget. |
| `--quiet` | off | No console summary. |

```bash
# Reproduce the evaluation baseline from the committed fixtures (no network):
repo-orchestrator scan --revisions baseline --offline --cache-dir tests/fixtures/baseline \
    --as-of 2026-11-02 --no-store --output-dir /tmp/m1
# A real scan of today's HEADs (about 50 API requests):
repo-orchestrator scan --output-dir reports/
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Evaluation complete, no confirmed finding |
| 1 | Evaluation complete, at least one finding. An inconsistency is a result, not an execution error. |
| 2 | Evaluation **incomplete**: evidence could not be collected or decided (or an engine fault). Takes precedence over 1: a partial scan cannot claim to have listed every finding. Policy questions and claims that are not checkable by design do **not** raise the code. |
| 3 | Configuration or runtime failure (invalid declarations, unknown pin set or repository, unreadable file, determinism violation) |

## GitHub access

Public repositories work without a token (60 requests per hour); a scan needs about 50, so set a token for repeated runs. `GITHUB_TOKEN` or `GH_TOKEN` is read if present and sent as a bearer header. **No write scope is needed or used**: a fine-grained token with *Contents: read* on the analyzed repositories is enough. The client issues `GET` requests only, to four endpoints (`/repos/{slug}`, `/repos/{slug}/commits/{ref}`, `/repos/{slug}/git/trees/{sha}?recursive=1`, `/repos/{slug}/git/blobs/{id}`). Transient failures (network errors, 5xx, 429, rate-limit 403) are retried up to 4 attempts with exponential backoff, honouring `Retry-After` up to 30 s; permanent errors are not retried. A private repository is refused (baseline Q6).

The cache directory is safe to delete; it only saves requests.
