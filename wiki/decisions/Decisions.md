<!-- mode: index -->
# Architecture decisions

Every load-bearing call in `agentm` is recorded as an Architecture Decision Record (ADR): the context, the decision with explicit *"why X, and why not Y"* reasoning, and the consequences with re-audit triggers so a stale assumption surfaces later instead of rotting silently.

This page is the index. The homepage links here once instead of listing every ADR.

## Records

- [ADR 0001 — Phase-gated workflow](0001-phase-gated-workflow)
- [ADR 0003 — ProjectsV2 ownership and linking](0003-ProjectsV2-Ownership-And-Linking)
- [ADR 0005 — Drop Codex support; three-adapter scope](0005-drop-codex-support)
- [ADR 0006 — Split customizations into `crickets`](0006-crickets-split)
- [ADR 0007 — Auto-context into harness phases](0007-auto-context-into-harness-phases)
- [ADR 0008 — Project surface split](0008-project-surface-split)
- [ADR 0010 — Vault internal taxonomy](0010-vault-internal-taxonomy)
- [ADR 0011 — V5 unbundling: slim the dev loop + migrate-to-diataxis](0011-v5-unbundling-dev-loop)
- [ADR 0014 — The Tier-2 gate: don't fork the loop through the Agent SDK](0014-tier-2-sdk-fork-gate)
- [ADR 0015 — Capability discovery: the `enhances:` runtime](0015-capability-discovery)
- [ADR 0016 — The persona tier: a third classification above the substrate/plugin binary](0016-persona-tier)
- [ADR 0017 — MCP server design: singleton-HTTP broker, four tools, loopback-first](0017-mcp-server-design)

## See also

- [Home](Home) — the wiki landing page.
- [Explanation](Explanation) — the narrative rationale behind these decisions.
- [Designs](Designs) — the design docs the larger decisions trace back to.
