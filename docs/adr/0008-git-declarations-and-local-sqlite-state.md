# ADR-0008: Declarations live in Git; runtime state lives in one local SQLite file

- **Date:** 2026-10-31
- **Status:** Proposed

## Context

What actually needs persisting, derived from the workflows:

| Data | Changes | Needs review | Needs durability across runs |
|---|---|---|---|
| Repository list, relationships, contracts, policies, exceptions | Rarely, by a human | Yes | Yes |
| Repository snapshots (file contents at a SHA) | Every run | No | No: recomputable from Git |
| Findings and their lifecycle history | Every run | No | Yes: needed for `resolved` / `regressed` |
| Investigation checkpoints (paused for a human) | During a run | No | Yes, until the run ends |
| Run Records ([ADR-0006](0006-evidence-provenance-uncertainty-and-reproducibility.md)) | Every run | No | Yes, for audit and replay |
| Reports | Every run | Read by humans | Yes, as files |

No requirement calls for concurrent writers, multiple users, or a network service.

## Decision

1. **Declarations** (repositories, relationships, contracts, policies' parameters, exceptions) are versioned files in this repository, changed by pull request. Their revision is recorded in every Run Record.
2. **Runtime state** (findings and history, Run Records, LangGraph checkpoints) is a **single local SQLite file**. The LangGraph SQLite checkpointer uses the same file, or a sibling file, if the S0 spike leads to adopting LangGraph ([ADR-0005](0005-langgraph-for-investigation-workflows-only.md)).
3. **Snapshots** are a content-addressed cache on disk keyed by `(repository, SHA, path)`; deleting the cache loses nothing.
4. **Reports** are files (Markdown for humans, JSON for machines) written per run.
5. No server, no hosted database, no vector store in the MVP.

## Rationale

- Declarations are decisions; Git gives them review, history and authorship for free.
- SQLite covers findings history, run records and checkpoints with zero operations.
- Content addressing makes snapshots safe to discard and cheap to share between runs.

## Alternatives considered

- **PostgreSQL.** Rejected: operational cost with no concurrent-writer requirement.
- **Append-only JSONL (as `autonomous-dev-loop` ADR-0013 does for metrics).** Viable for Run Records; rejected as the only store because findings lifecycle queries (latest state per fingerprint) and checkpoints need indexed updates.
- **GitHub issues or labels as the finding store.** Rejected: requires write access ([ADR-0007](0007-read-only-operation-and-human-gated-remediation.md)) and causes re-entry.
- **Graph database for relationships.** Rejected: tens of edges, rebuilt in memory per run.
- **Vector store for retrieval over documents.** Rejected for the MVP: the corpus of six repositories is small; targeted search over pinned snapshots is deterministic and sufficient.

## Consequences

- ✅ Zero infrastructure to run the MVP.
- ✅ Declarations are auditable and reviewable alongside the documentation that justifies them.
- ⚠️ One machine owns the state; sharing findings history between machines or a CI runner needs a decision (export the file, or move to a service) when it becomes a requirement.
- ⚠️ A scheduled CI execution (future) would need the SQLite file persisted between runs (artifact or branch, as `autonomous-dev-loop` does with its `metrics` branch).

## Open questions

- Format of declaration files (YAML is the default assumption, consistent with `ai-dev-tools/configs/` and `autonomous-dev-loop/config/`).
