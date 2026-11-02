---
name: new-adr
description: Procedure for adding or superseding an ADR in repo-orchestrator — next number, template, Proposed status, index row and dependency diagram, and the design documents to cross-check. Use when a design decision is added or changed.
---

1. **Is it an ADR?** Only for a decision that constrains several components or milestones, or is hard to reverse. A wording fix or a clarification goes into the existing document, not a new ADR.
2. **Number:** `ls docs/adr/[0-9][0-9][0-9][0-9]-*.md | sort | tail -1`, increment, four digits. Never reuse a number.
3. **File** `docs/adr/NNNN-kebab-title.md`, matching the existing records:
   - `# ADR-NNNN: Title`, then `- **Date:**` and `- **Status:** Proposed`;
   - sections: Context, Decision, Rationale, Alternatives considered (each with why it was rejected or kept), Consequences (✅ / ⚠️), Open questions.
4. **Status:** always `Proposed`. Only the owner moves it to `Accepted` (`CLAUDE.md`). To change an accepted ADR, write a new one and set the old one to `Superseded by ADR-NNNN`.
5. **Index:** add the row to `docs/adr/README.md` and the node and edges to its dependency diagram.
6. **Cross-check** the documents the decision touches and keep their canonical names (C1–C7, P1–P7, S0/M1–M5): `README.md` (capabilities, milestones, ADR count), `docs/architecture.md`, `docs/domain-model.md`, `docs/coherence-model.md`, `docs/workflows.md`, `docs/mvp.md`. Every Mermaid diagram showing a changed element is updated in the same change.
7. **Claims about other repositories** cite `repo path:line` at a revision, or are marked HYPOTHESIS, as in `docs/research/ecosystem-baseline.md`.
