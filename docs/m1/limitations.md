# Known limitations

What M1 does not do, and where its answers can be wrong. Nothing below is hidden: where it matters, the report says so.

## Scope

- **Six repositories, eleven declared relationships.** Recall is bounded by the declarations ([ADR-0002](../adr/0002-explicit-typed-relationship-model.md)): an undeclared relationship produces nothing, and absence of a finding is not evidence of coherence. Automatic relationship discovery is outside M1.
- **R4, R6, R10, R11 have no M1 policy** (conceptual or deployment edges). They are declared and listed in the coverage table as `no_policy` / `not_analyzed`. `fullstack-pilot`, `sync-engine` and this repository are declared but never collected.
- **No PR analysis (M2)**: no base/head comparison, no `introduced` / `pre-existing` label in the product. The evaluation suite classifies by fingerprint in the test code only. The "manifest comment naming the consumer becomes false" part of baseline U2 is not detected.
- **No P7, no investigation (M3), no remediation plans (M4), no opportunities (M5).** P7 (qualitative claims such as "12 extracted from the two repos above", baseline H2) is not evaluated; the profile's "100+ ADRs across these repos" and "11 integration failure modes" claims are not declared.
- **Public repositories only** (Q6). A private repository is refused with `private_repository_out_of_scope`.

## Extractors are narrow on purpose

- JavaScript is read as text. Object literals are parsed by bracket and string scanning; a spread, computed key, method, or an unusual regex literal containing quotes produces `unsupported_syntax` / `parse_error`, never a guess. Only **top-level** keys are compared (P2): a nested field removed from a finding object is not seen.
- A schema version must be an integer constant or an integer in an object literal; anything computed is `locator_no_match`.
- Counting claims need a declared line and number pattern; the rule is a parameter, not an inference. "N+ tests" claims are not checkable.
- P3 compares exact strings: `llava` and `llava:latest` are different tags.
- P4 recognises the `metallb.io/loadBalancerIPs` annotation (single addresses, comma-separated lists) and IPv4 pools. `spec.loadBalancerIP` (non-empty), the legacy `metallb.universe.tf/loadBalancerIPs` annotation, ranges in annotations and IPv6 are `unsupported_syntax`: the file is `cannot_evaluate` and no other address is declared safe. Claim keys inside a Helm `values: |` literal block are parsed as YAML (one level deep, evidence cites the outer file's lines); a claim key inside any other string (folded, quoted, nested or unparseable) is `unsupported_syntax`. A file matched by several globs is read once. Claims are searched only in the declared globs (`apps/*/service.yaml`, `apps/*/application.yaml`, `k8s/*.yaml`): a claim elsewhere is invisible. Prose allocations (for example `.240`–`.242` in comments) are not claims.
- P5 checks links of the form `github.com/<repo>/(blob|tree)/<main|sha>/<path>`; relative links, anchors, and links to a moving branch other than the declared default are not checked (the last is reported `reference_not_pinned`).
- P6 reads `.claude/settings.json` as JSON and the adopter table as a Markdown pipe table; a different table layout is `cannot_evaluate`.

## Collection

- One tree listing per repository. GitHub truncates very large trees; M1 then reports `tree_truncated` for anything that depends on a listing or on absence, rather than assuming.
- File size (1 MiB), files per repository (300), glob matches (60), total bytes (16 MiB) and API requests (400 by default) are bounded; hitting a bound is a `cannot_evaluate`, not a partial pass.
- Requests are sequential (about 15 s for a live scan). Without a token the public rate limit (60/hour) allows one scan per hour.
- The artifact cache is keyed by `(repository, SHA, path)` and verified against git blob ids; it is never invalidated because its content cannot change.

## Persistence and reproducibility

- **One machine owns the state** (SQLite file). Sharing finding history between machines or CI runs needs a decision ([ADR-0008](../adr/0008-git-declarations-and-local-sqlite-state.md)).
- Replaying a run with identical inputs never changes history (by design); a regression is observable on a later input only.
- `--as-of` defaults to today, so two scans on different days have different digests even when nothing else changed. Pass it to compare.
- The finding age (`first_seen`) of the coherence model's ordering is in the store, not in the reproducible report.

## Open owner decisions that shape results

Q1 (plugin adoption scope), Q2 (ADR counting rule), Q3 (what "identical file" means) change outcomes through declarations; the engine only reports them. Q4, Q5 affect no M1 policy. Q6 is enforced as a refusal. A finding that depends on a provisional rule says so (D2 names Q2).

## Security notes

- Repository content is data: no code is executed, imported or built; YAML is composed with a safe loader and a 200 000-node expansion limit; JSON depth errors are caught.
- The GitHub client has no write path (`GET` only). Tokens are never written to reports, logs or the store.
- Reports quote repository content verbatim; for public repositories that is public data.
- The exit code 2 rule means a scan with any unresolved collection problem should be treated as "not passed" by automation.
