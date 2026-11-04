# M3: usage

## Install

```bash
pip install -e '.[dev,investigation]'      # the pinned LangGraph set is an optional extra
```

## Investigate during a scan

```bash
# offline, from a recording (no key, no network for the model; --offline also reads the snapshots from the cache)
repo-orchestrator scan --revisions baseline --offline --cache-dir tests/fixtures/baseline --as-of 2026-11-02 \
  --state-db .repo-orchestrator/state.sqlite --investigate \
  --replay tests/fixtures/investigation/scripted-baseline-recording.json

# live: the key comes from the environment, never from an option
export ANTHROPIC_API_KEY=...           # read once, sent in one header, never logged, stored or reported
repo-orchestrator scan --investigate --record recording.json
```

`--investigate` is the only switch: without it the command is exactly the M1 scan. `--replay FILE` and `--record FILE` need `--investigate` and exclude each other. With neither a key nor `--replay`, the command stops with exit 3 (no silent skip). `analyze-pr` has no investigation in M3.

The command prints the usual summary, then the investigation: states, quotes verified and unverified, observations, tokens, estimated cost and the exit code. It writes `report.md` (the M1 report plus the M3 sections) and `report.json` (`semantic.investigation`, `execution.investigation`). Repeating the command is idempotent: the run id is derived from the inputs, so it continues, finishes or re-reports the same run without investigating twice.

## `scan` options added by M3

| Option | Meaning |
|---|---|
| `--investigate` | also investigate the declared reasoning candidates |
| `--replay FILE` | answer from this recording only (offline) |
| `--record FILE` | write the model responses used by the run to this file |

## Exit codes

Without `--investigate` the codes are exactly M1's. With it:

| Code | Meaning |
|---|---|
| 0 | complete; nothing to report and no human decision pending |
| 1 | complete; at least one finding: a deterministic one or a reasoning candidate `confirmed` by a check or a human |
| 2 | incomplete evaluation, **including any `insufficient_evidence`** (it is never a pass) |
| 3 | configuration or runtime failure (no key and no recording, unreadable recording, declarations invalid, snapshot changed on resume, refused decision) |
| 4 | complete, but a human decision is pending (`needs_validation`); only with `--investigate` |

Precedence: 3 > 2 > 1 > 4 > 0. A pending validation never hides a finding (1) or an incomplete evaluation (2). On the pinned baseline the two documented M1 findings keep the code at 1.

## Human workflow (W4)

```bash
repo-orchestrator investigations list
repo-orchestrator investigations show <run> [--json]
repo-orchestrator investigations decide <run> <candidate> --action validate --reason "read both READMEs" --actor alice
repo-orchestrator investigations approve <run> --reviewer carol
repo-orchestrator investigations resume <run>        # after an interrupted run; offline, nothing is re-pinned
```

| `--action` | Applies to | Effect |
|---|---|---|
| `validate` | `needs_validation` | `confirmed`, `validated_by_human` |
| `reject` | `needs_validation` | `dismissed` |
| `dismiss` | `insufficient_evidence`, `needs_validation`, `policy_question` | `dismissed` |
| `acknowledge` | `confirmed` | marks it acknowledged |

A reason and an actor are required. A human cannot validate a candidate that is not in `needs_validation` (an unverified or undetermined claim cannot be promoted). Decisions are stored in `inv_decisions`, bound to the evidence digest, and applied by the next `scan --investigate` report; `decide` also resumes a run paused at triage once nothing is left to triage, and `approve` accepts the pause that follows. `--state-db` selects the store (default `.repo-orchestrator/state.sqlite`; checkpoints live next to it in `investigations.sqlite`).

## Manual live check

The workflow `.github/workflows/m1.yml` has a `workflow_dispatch` input `live_investigation` (off by default): a read-only scan with the bounded investigation using the `ANTHROPIC_API_KEY` repository secret. It costs money and is never part of the default gate; its recording is uploaded so the run can be replayed offline.
