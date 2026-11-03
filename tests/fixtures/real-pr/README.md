# Real pull requests, recorded for offline replay

Three **real** pull requests of the declared repositories, recorded in the offline cache format by
`tests/tools/record_real_pr.py` through the product's own `CachingSource` (what the analysis read is what is stored).
Repositories other than the PR repository come from `tests/fixtures/baseline` (the pinned 2026-10-31 revisions); what the
baseline already holds is not repeated here.

| PR | Base → head | What it is |
|---|---|---|
| `koydas/gitops-homelab#2` | `0f00ecd2` → `27097c54` | Merged by merge commit `9e0eb102`; changes `apps/ollama/application.yaml` (adds a model to the pulled list), adds ADR 0018 and edits two READMEs |
| `koydas/gitops-homelab#1` | `caa4db66` → `cedf083e` | Merged by merge commit `4752a863`; adds a GPU time-slicing ConfigMap and ADR 0017 |
| `koydas/ollama-chat#12` | `1693c3f7` → `2a05367e` | A `package-lock.json` change; the base is the baseline pin of `ollama-chat`; its state on GitHub is unknown to the recorder |

**Provenance, stated plainly.** These records were **not** read from the REST API (`pulls/{n}`): the environment that recorded
them could read the public repositories through git only. The head SHA is the commit behind `refs/pull/<n>/head`; the base
of a merged pull request is the first parent of its merge commit; the changed files come from
`git diff --name-status -M base head`. Each record says so in its `provenance` field. The REST client itself is covered by
scripted-transport tests; a live REST run is the manual `live_pr` workflow input.

**Reading the results.** The other repositories are at the baseline pins, which are *today's* state, while these merged pull
requests are old: the profile README states the current ADR and incident counts of `gitops-homelab`, and `ollama-chat`'s
`VISION_MODEL` is compared with the models the older `gitops-homelab` pulled. Those findings exist on base and on head, so
they are `pre-existing` (correctly not blamed on the pull request). An *open* pull request is analyzed against the present, where
this anachronism does not arise.

Like `tests/fixtures/baseline`, these files are history: never edited to make a test pass.
