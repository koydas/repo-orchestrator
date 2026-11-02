# Policies

Six deterministic policies. A policy is a plain Python module with typed metadata and four functions; there is no DSL and nothing is evaluated from configuration text. **No policy, no finding.**

## Interface

```python
# src/repo_orchestrator/policies/base.py
PolicyMeta(id, version, title, aspect, scope: Scope, impact: Impact, intent: Intent)

Policy(
    meta,
    prepare:  (Declarations, Graph) -> Config      # parse and validate this policy's declarations (ConfigError)
    applies:  (Relationship) -> bool               # which declared relationships it evaluates
    requests: (Config, Relationship) -> [ArtifactRequest]   # the artifacts it needs: the collection plan
    evaluate: (Config, EvalContext, Relationship) -> [Outcome]
)
```

`EvalContext` gives read access to the frozen snapshot (`text`, `artifact`, `entries`, `glob`, `sha`) and builds outcomes (`consistent`, `inconsistent`, `clarify`, `cannot`). Anything the policy cannot read or decide is raised as `CannotEvaluate(cause, detail)`; the engine turns it into a `cannot_evaluate` outcome. A policy that raises anything else becomes a `failed` outcome and the run continues.

### Adding a policy

1. State the authority (an artifact someone wrote) and the impact class; add its parameters and `authority` to `declarations/policies.yaml`.
2. Create `policies/pN_name.py` exposing `POLICY`; register it in `policies/__init__.py`.
3. Declare what it reads in the relevant relationships' `contract` block (and validate it in `prepare`).
4. Use only the extractors of `extract.py` / locator kinds of `locators.py`; add a new kind there (with tests) rather than parsing inline.
5. Cover: consistent, inconsistent, each `cannot_evaluate` cause it can raise, and a mutation of the pinned baseline; add the dataset case to `tests/test_baseline_eval.py`.
6. Document it here and in the [coherence model](../coherence-model.md#mvp-policy-catalog), and bump `version` when behaviour changes (it is part of every finding and of the run digest).

Severity is derived, never chosen per finding: `breaks` → high, `falsifies` → medium, `outdates` → low, except a failing `tooling_adoption` policy → medium ([coherence model](../coherence-model.md#prioritization)).

## Catalog as implemented

| Policy (v1.0.0) | Scope | Relationships | Impact | Subjects |
|---|---|---|---|---|
| **P1** Documentary counts | repository (`profile`) | R1.* | falsifies → medium | `claim:<id>` |
| **P2** JSON contracts | relationship_type `runtime_contract` | R2, R3 | breaks → high | `schema_version`, `key_set` |
| **P3** Model tags | relationship (R7) | R7 | breaks → high | `const:<NAME>` |
| **P4** Shared resource allocation | relationship_type `shared_resource` | R8 | breaks → high | `address:<ip>`, `pool`, `claim-site:…` |
| **P5** Cross-repository references | global | R1.*, R9 | falsifies → medium | `ref:<blob\|tree>:<repo>:<path>`, `source:…` |
| **P6** Tooling adoption | relationship_type `tooling_adoption` | R5.* | outdates → medium | `plugin_keys`, `file_identity`, `adopter_table` |

R4, R6, R10 (declared, no M1 policy), R11 and `R5.fullstack-pilot` / `R5.sync-engine` (outside the six repositories) are listed in the report's coverage table with the reason they are not evaluated.

### P1 — documentary counts

Reads the number a claimant (the profile README) states on the line selected by `line_regex`; counts the described repository by the declared rule; compares for equality.

- Rules: `numbered_adr_files` (files directly under the directory named `NNNN-*.md` or `ADR-NNN-*.md`: both formats, `README.md` excluded), `pattern_dirs` (directories directly under `patterns/`), `h3_under_h2` (`###` headings under a named `##`, fenced code ignored), `tests_lower_bound` (always `cannot_evaluate`, cause `not_checkable_deterministically`, shown but not an incomplete run).
- **Attribution (D2).** If the claim differs from the count, the described repository's own statement is read: equal to the claim → `attribution: upstream`, fix sites `[described README, claimant]` (the claimant is never alone blamed); equal to the count → `claimant`; neither → `both`; unreadable → `claimant_upstream_unreadable`.
- **Q2.** `numbered_adr_files` is provisional. A mismatch names Q2; and if the claim equals the count under the declared alternative (`adr_index_entries`) the outcome is `policy_clarification`.

### P2 — JSON contracts

Extracts each side's `schema_version` (a constant, or a key of an object literal). For a constant, it also reads what the document-building object literal actually carries under `schema_version`: the declared constant (connected), an integer literal (that literal is the emitted version) or anything else (`cannot_evaluate`: the declared constant is no longer what is emitted). and the top-level key set of the object literal that builds the document. `producer_consumer`: versions equal and the consumer's keys ⊆ the producer's (extra producer keys are additive, listed, not failed). `peer`: versions equal and key sets equal. A failure names the consumer as affected and orders the fix sites consumer-first. Nested key sets (for example the fields of one finding) are not compared.

### P3 — model tags

Every `js_const_string` the consumer declares must be a member of the string list at the provider's `yaml_path`. Exact string match. Living code and manifests only: accepted ADRs, which record past values, are never an input (baseline N4).

### P4 — shared resource allocation

Parses the pool ranges (`a-b`, CIDR or single IPv4), then every `metallb.io/loadBalancerIPs` annotation found anywhere in the YAML of the declared glob sources (comma-separated lists allowed). Per address: outside the pool, or claimed more than once (also within one repository) → finding. YAML aliases and merge keys are resolved as YAML defines them (an aliased claim counts at each use; an explicit key overrides a merged one) and `helm.values: |` blocks are read. If a claim source cannot be read or parsed, **no other address is declared safe** (they become `cannot_evaluate`), but a collision already found is still reported.

### P5 — cross-repository references

In the `living` sources of a relationship, finds `https://github.com/<other>/(blob|tree)/<ref>/<path>` links to the other endpoint and checks the path exists with the right type in the target's pinned tree. `<ref>` must be the target's declared default branch or its pinned SHA, else `cannot_evaluate` (`reference_not_pinned`). Sources declared `historical` are skipped. A living source with no such link yields one `consistent` outcome stating zero references.

### P6 — tooling adoption

For each adopter (target of an `R5.*` edge): (1) `plugin_keys`: `extraKnownMarketplaces` and `enabledPlugins` equal the declared values exactly (JSON `true` is not `1`); (2) `file_identity`: other top-level keys such as `permissions` and `hooks` are reported per the `file_identity` parameter, and with the baseline value `undecided` as a `policy_clarification` (Q3, baseline N2); (3) `adopter_table`: the adopter appears in the registry table of `ai-dev-tools` with exactly the skills found under `.claude/skills/*/SKILL.md`. Edges with `applicability: undetermined` (`ollama-chat`, `gitops-homelab`, Q1/H1) can only produce a `policy_clarification` or `consistent`, never a finding, and are not checked against the table.
