# Architecture Decision Records

One file per decision, numbered sequentially as `<NNNN>-<slug>.md` (e.g. `0001-postgres-for-persistence.md`).

ADRs document the *why* behind architectural choices that aren't obvious from the code. They use "Template 3 — ADR" from the [wiki README](https://github.com/alexherrero/agentic-harness/blob/main/templates/wiki/README.md): a short Context / Decision / Consequences triple. Once a decision is accepted, its body is append-only — supersede it by recording a new ADR that references the old one, not by editing the original.

The `documenter` sub-agent proposes new ADRs during `/release` when a non-obvious decision surfaced during the cycle. Humans may write ADRs at any time.

## Convention

- **Filename:** `0001-short-slug.md`, `0002-...`, etc. Numbers never reused.
- **Statuses:** `proposed` → `accepted` → `superseded-by-<NNNN>`.
- **Amendments** to accepted ADRs use `## Amendment YYYY-MM-DD` subheadings — never edit the original body.

The agentic-harness wiki has worked examples: [ADR 0002 — Documentation convention](https://github.com/alexherrero/agentic-harness/blob/main/wiki/architecture/decisions/0002-documentation-convention.md) and [ADR 0004 — Diátaxis documentation spec](https://github.com/alexherrero/agentic-harness/blob/main/wiki/architecture/decisions/0004-diataxis-documentation-spec.md).
