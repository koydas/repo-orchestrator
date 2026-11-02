# CLAUDE.md — koydas (profile repo)

`README.md` is the GitHub profile; `site/` is the Vite/React/TypeScript site deployed by `.github/workflows/deploy-site.yml`. Repo descriptions in the README must match the repos' own READMEs (counts of ADRs/tests/patterns drift).

Procedures in `.claude/skills/`: `site-change`.

## Toolbox — koydas/ai-dev-tools

`.claude/settings.json` declares the `ai-dev-tools` plugin (trust the folder → install offered): `/ai-dev-tools:pr-review <n>`, `/ai-dev-tools:pr-fixer`, `/ai-dev-tools:issue-code-generation <n>`, `/ai-dev-tools:ac-check <n>`. Repo rules (this file, `.claude/skills/`) win over toolbox defaults.

Cloud sessions (claude.ai/code) do not install repository-declared plugins: environment setup script in the ai-dev-tools README → "Use from another repository". Commands are then un-prefixed (`/pr-review`). Details: [`docs/claude-code.md`](docs/claude-code.md).
