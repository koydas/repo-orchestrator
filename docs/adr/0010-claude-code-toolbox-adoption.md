# ADR-0010: Adopt the ai-dev-tools toolbox and the ecosystem's Claude Code conventions

- **Date:** 2026-11-02
- **Status:** Proposed

## Context

This repository is worked on with Claude Code but had none of the conventions the other `koydas` repositories follow: no `CLAUDE.md`, no `.claude/settings.json` declaring the `ai-dev-tools` plugin, no `docs/claude-code.md`, no repository skill, no LICENSE. `ai-dev-tools` ADR-018 §1 requires every repository worked on with Claude Code to declare the plugin, and §5 asks each consumer to document its adoption and, where it keeps ADRs, to record the decision. This is the same check this project proposes to automate (policy P6 in the [coherence model](../coherence-model.md#mvp-policy-catalog)), applied to itself.

## Decision

1. `.claude/settings.json` declares the plugin with exactly the two keys of `ai-dev-tools` ADR-013 §2, identical to the other adopters.
2. `CLAUDE.md` holds the invariants of the design phase (nothing implemented, ADRs stay `Proposed`, evidence over assertion, coherence is not uniformity, read-only toward other repositories) and the standard `## Toolbox` section.
3. One repository skill, `new-adr`, holds the ADR procedure; it links to `CLAUDE.md` instead of restating its rules.
4. `docs/claude-code.md` documents the adoption; `ai-dev-tools/docs/consumer-repos.md` lists the repository among the adopters.
5. `LICENSE`: MIT, same text and holder as `ai-dev-tools`, `agent-patterns` and `autonomous-dev-loop`.

## Rationale

- The repository that will check other repositories against shared conventions should pass those checks itself.
- Following ADR-018 costs four small files and gives sessions here the same toolbox as in the other repositories.

## Alternatives considered

- **No adoption until code exists.** Rejected: the design phase already runs reviews and fixes (PR #1), and the toolbox's reviewer reads `CLAUDE.md` for the repository's rules.
- **Restate the design rules in each skill.** Rejected: ADR-018 §3 keeps invariants in `CLAUDE.md` and procedures in skills.
- **A different license.** Rejected: no reason to diverge from the other public repositories.

## Consequences

- ✅ The repository satisfies ADR-018 and appears in the adopter table.
- ✅ Design-phase rules (no implementation claims, `Proposed` status) are loaded in every session.
- ⚠️ `settings.json` is now duplicated once more (accepted cost in ADR-018).
- ⚠️ `CLAUDE.md` must be revised when implementation starts (S0, M1): the "nothing is implemented" rule becomes a rule about what is implemented.

## Open questions

- Which repository skills become worth adding once code exists (for example a policy-addition procedure for P-series policies)?
