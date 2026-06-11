# ADR 0005: Drop Codex support; three-adapter scope

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-05-11

## Context

agentm shipped with four adapters: Claude Code, Antigravity (Google's coding-agent IDE), Codex (OpenAI's coding CLI), and Gemini CLI. The four-adapter parity model — every phase command, sub-agent, and skill mirrored across all four hosts — was set up in v0.5.x and held through v0.9.0. [ADR 0001](0001-phase-gated-workflow) describes the phase-gated workflow without constraining the host list; the four hosts were a product choice, not an architectural constraint.

In practice, maintaining the Codex adapter accrued real costs without much offsetting benefit:

- **Naming collisions with Codex built-ins.** Codex's built-in `/plan` and `/review` commands have different semantics from the harness's, so every Codex phase-command had to be wrapped as a skill with a `harness-` prefix. This is the only adapter that requires renaming — it adds a per-skill divergence to the parity invariant and creates a cognitive tax ("which is `/plan` again — the built-in or `harness-plan`?").
- **Indirect shared-skill delivery.** The shared skills (`dependabot-fixer`, `doctor`, `migrate-to-diataxis`, `ship-release`) were delivered to `.agents/skills/` by the Codex adapter block, with Gemini reading them from that same path per the Agent Skills standard. Removing Codex without thinking would have broken Gemini — the codex block was load-bearing for two hosts.
- **Personal-development-environment alignment.** The harness's primary user has narrowed their host scope to Claude Code, Antigravity, and Gemini CLI (with Claude Desktop as a future MCP-only integration, not a parity-tracked adapter). Codex is no longer in the workflow.
- **Surface-area cost.** `adapters/codex/` was 15 files; the `codex_prefixed` array in `check-parity.sh`, the `CODEX_PHASE_PREFIX` constant in `check-references.py`, the `validate_codex_agents()` function in `validate-adapters.py`, and the codex-expected lines in smoke / integrity scripts collectively added ~80 lines of code that the harness has to keep working. Every future change to canonical specs had to be propagated four times instead of three.
- **Research vs. delivery.** `harness/agents/codex-adapter-research.md` was a 294-line deep-dive on how the Codex adapter was designed. Useful when it was current; dead weight once the adapter is gone.

## Decision

**Drop Codex support entirely.** Adapter scope becomes three: Claude Code, Antigravity, Gemini CLI.

Concretely:

1. **`adapters/codex/`** removed (15 files: README, 4 TOML sub-agents, 10 skill dirs).
2. **`harness/agents/codex-adapter-research.md`** removed (294 lines of dead research).
3. **Scripts scrubbed** of codex-specific code: `check-parity.sh` (== codex == block + comment divergence), `check-references.py` (`CODEX_PHASE_PREFIX` + codex branch), `validate-adapters.py` (`validate_codex_agents()` + codex skills dir), `smoke-install-{bash,pwsh}` + `check-integrity-{bash,pwsh}` (codex expected-files lines).
4. **Shared-skill delivery reframed.** `install.sh` and `install.ps1` no longer wrap the `.agents/skills/` delivery inside a "codex block." Instead, an explicit per-name loop sources the four shared skills (`dependabot-fixer`, `doctor`, `migrate-to-diataxis`, `ship-release`) from `adapters/claude-code/skills/` (parity-enforced identical content; cleanest source — `antigravity/skills/` would over-deliver because it mixes sub-agents-as-skills). Gemini's read pattern is unchanged.
5. **True-sync `--update`.** The installer now wipes twelve fully-harness-authored subdirs before recreating from source, so existing pre-v1.0.0 installs get their orphaned `.codex/` paths automatically cleaned on first `--update`. See [Update-Installed-Harness](Update-Installed-Harness) for details.
6. **Repo-public surfaces** (README, AGENTS.md, adapter READMEs, wiki) scrubbed of Codex mentions. Historical references in `CHANGELOG.md` and `wiki/reference/Completed-Features.md` past-release entries are preserved as history.
7. **Version bump to v1.0.0.** The host-scope reduction is a breaking change; combined with the harness's maturity, this is the right moment to graduate from `0.x` to a firm-semver `1.0.0` floor.

## Consequences

**Positive**

- **Parity invariant simplifies.** Three adapters with one canonical naming scheme (no `harness-` prefix renaming) instead of four with a documented divergence. `scripts/check-parity.sh` drops ~30 lines.
- **Shared-skill delivery is explicit.** The "Codex block delivers `.agents/skills/` for Gemini's reuse" indirection is gone. Now `install.sh` names the four shared skills and their source directly. Clearer to read; easier to add a fifth shared skill.
- **`--update` semantics get a structural improvement.** Generalizing the codex cleanup into a `MANAGED_PARENTS`-wipe model means any future host or skill removal also cleans up automatically. Local trees stay in lockstep with GitHub source-of-truth without per-removal patches.
- **Surface area shrinks.** ~80 lines of code + 1200 lines of adapter files / research notes removed. Adding a new skill is now a 3-place edit instead of 4.
- **v1.0.0 commitment.** Future breaking changes (e.g. dropping Antigravity, or restructuring adapters) become explicit major-version events. Additive changes (new skills, new sub-agents, the `crickets` repo split planned next) become clear minor bumps under firm semver.

**Negative**

- **Users on Codex must migrate.** Anyone running agentm through Codex has no harness adapter post-v1.0.0. Migration path: use one of the three remaining adapters (Claude Code, Antigravity, or Gemini CLI). The harness's phase-gated workflow is host-agnostic, so the migration is install + relearn the Codex-specific invocation surface.
- **Codex's built-in `/plan` and `/review` will not be harness-aware.** A user invoking those in a Codex session against a harness-installed project will get Codex's native semantics, not the harness's. This is the same situation as any tool that hasn't been adapter-targeted; the user is responsible for knowing which host they're in.
- **Wider Codex ecosystem decoupling.** If Codex evolves a feature that matters (e.g. better tool-call surface, improved subagent isolation), the harness will be slower to incorporate it. Decision accepts that cost in exchange for the parity-and-surface-area wins.
- **One fewer cross-vendor review path.** `cross-review.sh` previously listed `codex` as an option for true cross-vendor review (shelling out from Gemini to Codex or Claude). Codex drops from that list; users wanting cross-vendor review can still edit the script to invoke `claude`.

**Load-bearing assumptions** (re-check on every model bump, per [principle 6](https://github.com/alexherrero/agentm/blob/main/harness/principles.md))

- Codex's native `/plan` and `/review` semantics remain incompatible with the harness's phase model (would need re-adding the `harness-` prefix to coexist).
- The user's primary workflow stays in Claude Code, Antigravity, and Gemini CLI. If Codex re-enters the workflow, this ADR gets superseded by a "re-add codex adapter" decision, not a partial-restoration patch.
- Future shared skills can continue to live in `adapters/claude-code/skills/` and be cross-delivered to `.agents/skills/` by `install.sh`. If `claude-code/skills/` ever needs Claude-Code-specific behavior that diverges from cross-host shared, we add a separate `adapters/_shared/skills/` directory; today the parity invariant makes this unnecessary.

## Related

- [How to refresh an installed harness](Update-Installed-Harness) — describes the v1.0.0+ true-sync semantics that auto-clean orphaned `.codex/` paths.
- [Repo layout](Repo-Layout) — reflects the three-adapter shape post-removal.
- [ADR 0001](0001-phase-gated-workflow) — the phase-gated workflow is host-agnostic; the host list is a product choice, not an architectural constraint. ADR 0005 reduces that list without touching 0001.
