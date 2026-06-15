<!-- mode: index -->
# Architecture decisions

Every load-bearing call in `agentm` is recorded as an Architecture Decision Record (ADR): the context, the decision with explicit *"why X, and why not Y"* reasoning, and the consequences with re-audit triggers so a stale assumption surfaces later instead of rotting silently.

This page is the index. The homepage links here once instead of listing every ADR.

## Records

- [ADR 0001 — Phase-gated workflow](0001-phase-gated-workflow)
- [ADR 0002 — Documentation convention](0002-documentation-convention)
- [ADR 0003 — ProjectsV2 ownership and linking](0003-ProjectsV2-Ownership-And-Linking)
- [ADR 0004 — Diátaxis documentation spec](0004-diataxis-documentation-spec)
- [ADR 0005 — Drop Codex support; three-adapter scope](0005-drop-codex-support)
- [ADR 0006 — Split customizations into `crickets`](0006-crickets-split)
- [ADR 0007 — Auto-context into harness phases](0007-auto-context-into-harness-phases)
- [ADR 0008 — Project surface split](0008-project-surface-split)
- [ADR 0009 — On-host state-mode config](0009-on-host-state-mode-config)
- [ADR 0010 — Vault internal taxonomy](0010-vault-internal-taxonomy)
- [ADR 0011 — V5 unbundling: slim the dev loop + migrate-to-diataxis](0011-v5-unbundling-dev-loop)
- [ADR 0012 — The vault-write protocol (R4 Phase-0 concurrency floor)](0012-vault-write-protocol)
- [ADR 0013 — The memory↔storage seam: backend selection fails loud, never demotes](0013-storage-seam-fail-loud-selection)
- [ADR 0014 — The Tier-2 gate: don't fork the loop through the Agent SDK](0014-tier-2-sdk-fork-gate)

## See also

- [Home](Home) — the wiki landing page.
- [Explanation](Explanation) — the narrative rationale behind these decisions.
- [Designs](Designs) — the design docs the larger decisions trace back to.
