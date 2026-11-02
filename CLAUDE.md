# CLAUDE.md — repo-orchestrator

Cross-repository coherence for independent GitHub repositories. **Design phase: documentation and Proposed ADRs only; the one code exception is the S0 spike (below).** Start with `README.md`, then `docs/architecture.md` and `docs/adr/README.md`.

## Rules

- **Nothing of the product is implemented.** Never describe a capability as working; README and docs say "intended" or "proposed".
- **One authorized exception: the S0 spike**, `prototypes/langgraph-spike/` ([ADR-0005](docs/adr/0005-langgraph-for-investigation-workflows-only.md) §5, [MVP](docs/mvp.md)). It is throwaway code that validates the LangGraph runtime on synthetic data. It does not authorize production code, GitHub access, an LLM call, a policy P1–P7, a service or any other implementation; nothing outside `prototypes/langgraph-spike/` and its workflow (`.github/workflows/spike-langgraph.yml`) may import it or depend on it. Describe it as "S0 spike: validated/not validated", never as a product capability. Its recommendation stays a proposal until the owner decides on ADR-0005.
- **ADRs stay `Proposed`** until the owner accepts them explicitly. Never change a status on your own. An accepted ADR is superseded, never edited.
- **Evidence over assertion.** A claim about another repository cites `repo path:line` at a pinned revision, or is labelled HYPOTHESIS (`docs/research/ecosystem-baseline.md` → How to read this page).
- **Coherence is not uniformity.** Never propose aligning a repository with the others without a policy and an authority (`docs/coherence-model.md`, ADR-0003, ADR-0009).
- **Read-only toward other repositories.** No issue, PR, comment or push elsewhere without the owner's explicit request (ADR-0007).
- **Mermaid diagrams match the text.** Changing a component, state or milestone means updating every diagram that shows it.
- Canonical names: components C1–C7 (`docs/architecture.md`), policies P1–P7 (`docs/coherence-model.md`), milestones S0, M1–M5 (`docs/mvp.md`), baseline ids R/D/C/N/H/U/Q (`docs/research/ecosystem-baseline.md`). Reuse them; do not invent parallel names.

Procedures in `.claude/skills/`: `new-adr`.

## Toolbox — koydas/ai-dev-tools

`.claude/settings.json` declares the `ai-dev-tools` plugin (trust the folder → install offered): `/ai-dev-tools:pr-review <n>`, `/ai-dev-tools:pr-fixer`, `/ai-dev-tools:issue-code-generation <n>`, `/ai-dev-tools:ac-check <n>`. Repo rules (this file, `.claude/skills/`) win over toolbox defaults.

Cloud sessions (claude.ai/code) do not install repository-declared plugins: environment setup script in the ai-dev-tools README → "Use from another repository". Commands are then un-prefixed (`/pr-review`). Details: [`docs/claude-code.md`](docs/claude-code.md).
