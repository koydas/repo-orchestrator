# Declarations and configuration

All declarations are YAML in `declarations/`, changed by pull request, validated at load (a typo fails the command with the file and field, before anything is collected). Duplicate keys are rejected. Their normalized content digest is part of every report.

| File | Contents |
|---|---|
| `repositories.yaml` | `id`, `slug`, `scope`, optional `analyzed: false` (declared in relationships, never collected) and `default_branch` (used to read `blob/main/…` links) |
| `relationships.yaml` | Typed, directed edges R1–R11 (one edge per repository pair; `R1.adl`, `R1.ap`, … carry baseline id `R1`) |
| `policies.yaml` | Per policy: `authority` artifacts and parameters; `repository_authorities` |
| `exceptions.yaml` | Intentional exceptions |
| `questions.yaml` | The open owner decisions Q1–Q6 and their effect on the run |
| `pins.yaml` | Named revision sets |

## Relationships

```yaml
- id: R7
  baseline: R7                  # id in docs/research/ecosystem-baseline.md
  type: runtime_contract        # runtime_contract | build_dependency | deployment | shared_resource |
                                # tooling_adoption | documentary | conceptual
  source: homelab               # the repository whose change can affect `target`
  target: chat
  origin: declared              # only `declared` edges are evaluated; observed / inferred are listed as proposals
  applicability: in_scope       # or `undetermined` + open_question: Q1  (policies may then only ask)
  description: ...
  evidence: "chat src/lib/conversations.js:16-17; homelab apps/ollama/application.yaml:49-61"
  anchors: [{repo: chat, path: src/lib/conversations.js, role: consumer}]   # each anchor repo must be an endpoint
  contract:                     # one block per policy that reads it
    model_tags: {...}
```

**Direction.** `source` is the origin of the impact. For `documentary` edges the *described* repository is the source and the repository making the claim is the target (R1: source `adl`, target `profile`). The baseline map draws some documentary and conceptual arrows the other way; see [ADR-0011](../adr/0011-m1-outcome-contracts-and-reproducible-reports.md) §3.

**Validation.** Unique ids, known endpoints, endpoints distinct, anchors on endpoints, `undetermined` applicability naming its question, valid types and origins.

## Contract blocks

| Block | Read by | Fields |
|---|---|---|
| `counts.claims[]` | P1 | `id`, `line_regex` (selects the claim line), `value_regex` (group 1 = the number), `rule` (`numbered_adr_files`, `pattern_dirs`, `h3_under_h2`, `tests_lower_bound`) and its parameters (`dir`; `path` + `section`), optional `upstream` (the described repository's own statement) and `cross_checks` (`adr_index_entries`) |
| `json` | P2 | `document`, `mode` (`producer_consumer` with `producer`/`consumer`, or `peer` with `a`/`b`); each side has a `version` and a `keys` locator |
| `model_tags` | P3 | `consumer` (list of `js_const_string` locators), `provider` (a `yaml_path` locator yielding a list of strings) |
| `allocation` | P4 | `pool` (a `yaml_path` locator to a list of ranges), `annotation`, `claim_sources` (`repo` + `glob`) |
| `references[]` | P5 | `repo`, `path`, `role: living \| historical` |

## Locators

A closed set; there is no expression language. Each names a `repo` and `path` and is validated at load (regexes compiled, paths relative).

| Kind | Parameters | Yields |
|---|---|---|
| `js_const_int` | `name` | the integer of `const NAME = 3;` (exactly one declaration) |
| `js_const_string` | `name` | the string of `const NAME = 'x';` |
| `js_object_keys` | `after` (regex, one match), `literal` (regex ending at `{`) | top-level keys of the object literal after the anchor; a spread, computed key or method is `unsupported_syntax` |
| `js_object_value` | + `key` | the raw expression of that key |
| `yaml_path` | `yaml_path` (dotted) | the value at that path (exact line span as evidence) |

JavaScript is read as text; it is never run.

## Policy parameters and authorities

`policies.<Pn>.authority` lists artifacts (`repo`, `path`, optional verbatim `quote`) resolved at the pinned revision **before** the policy runs. If one does not resolve, the policy is `cannot_evaluate` for the run (no policy, no finding). `repo: orchestrator` names this repository and is accepted as declared.

P6 also takes `plugin_keys` (the exact value of the two keys of ADR-018), `file_identity` (`undecided`, `exact_file`, `plugin_keys_only`: how to read "the file is identical", Q3), `settings_path`, `skills_dir` and `adopter_table`. P1 takes `provisional_rules` (counting rules whose choice is an open question: `numbered_adr_files: Q2`).

## Exceptions

```yaml
exceptions:
  - id: X1
    policy: P1
    relationships: [R1.ap]        # explicit ids; no wildcard
    subject: claim:patterns       # optional; omitted = every subject of the policy on those relationships
    justification: "one paragraph"
    authority: {repo: ap, path: README.md, quote: "20 patterns"}
    owner: koydas
    review_by: 2027-01-01         # the exception applies up to and including this date
```

All fields are required. After `review_by` (judged at `--as-of`), or when the authority does not resolve, the exception is not applied and the finding is `inconsistent` again; the report lists the exception as `expired` or `authority_unresolved`. An exception only ever changes a confirmed finding: it never turns missing evidence into a pass. None is declared at the baseline.

## Repository authorities and precedence

```yaml
repository_authorities:
  - id: A1
    repo: ap
    policy: P1
    relationships: [R1.ap]
    subject: claim:patterns
    statement: "agent-patterns README is the single source of its pattern count"
    authority: {repo: ap, path: README.md, quote: "20 patterns"}
```

When a policy would report a finding on a subject governed by a **verified** repository authority, the outcome is `policy_conflict` for a human, not a finding. An authority whose quote does not verify never suppresses anything. None is declared at the baseline.

A policy is selected for a relationship only if its `applies` holds **and** the relationship is covered by the policy's declared scope values (repository ids, relationship ids or baseline ids, relationship types); candidates are then grouped by `aspect`. On a relationship with `applicability: undetermined`, any failing check is reported by the engine as a `policy_clarification` naming the open question, whatever the policy. Within an aspect the narrowest scope wins (`repository` > `relationship` > `relationship_type` > `global`) and the others are listed as superseded; two policies of the same aspect and scope on one relationship are a configuration error at load.

## Pins and open questions

`pins.yaml` maps a set name to a 40-hex SHA per repository. `questions.yaml` records Q1–Q6 with their `effect` on the run; the engine never answers a question. The owner's answer arrives as a declaration change (for example `file_identity: plugin_keys_only` for Q3).
