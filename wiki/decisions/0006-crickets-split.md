# ADR 0006: Split customizations into `crickets` sibling repo

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-05-12

## Context

Through v1.x, `agentm` shipped four skills bundled into the harness repo: `dependabot-fixer`, `doctor`, `migrate-to-diataxis`, and `ship-release`. Each was duplicated across three adapter directories (`adapters/claude-code/skills/`, `adapters/antigravity/skills/`, plus delivery to `.agents/skills/` for Gemini's reuse), and the parity invariant enforced by `scripts/check-parity.sh` required every skill to ship in all three places.

Two pressures surfaced as the customization catalog was projected forward:

- **Parity tax scales linearly with the skill count.** Every new skill cost three adapter wrappers + a `CANON_SKILLS` entry + a `SHARED_SKILLS` entry. The mechanism is fine at four skills; it becomes friction at twenty.
- **Harness identity at risk.** [`README.md`](https://github.com/alexherrero/agentm/blob/main/README.md) pitches "a small, opinionated harness — not a 150-agent supermarket." The phase workflow (`/setup`, `/plan`, `/work`, `/review`, `/release`, `/bugfix`) is the protagonist; skills are background characters. A growing skill catalog would eventually re-shape the README to lead with skills, weakening the workflow story.

A separate observation: a realistic personal-customizations layer holds more than just skills. The user's roadmap includes slash commands, sub-agents, hooks, MCP server configs, status-line snippets, output styles, Antigravity workflows, Antigravity rules, instruction-snippet fragments, and settings-fragments — eleven primitive types. The harness's structure has no place for most of them.

Three options reviewed:

1. **Lower parity in place, expand `harness/skills/`.** Keeps one repo. Every README change must balance two audiences (harness users vs. customization users). Parity invariant either stays (taxes skill growth) or weakens (taxes harness coherence).
2. **Two surfaces, one repo** — `personal-skills/` alongside `harness/`. Cleaner than option 1 but the README still mixes two stories.
3. **Split repos** — new `crickets` repo, share `lib/install/` via copy-the-lib with byte-identity CI gate. Two repos to maintain, but each gets a focused identity.

## Decision

**Create `crickets` as a sibling public GitHub repo** alongside `agentm`. The two repos:

- Have independent release cycles.
- Share `lib/install/` byte-identically — install primitives (`cp_managed`, `cp_user`, `cp_managed_dir`, `ensure_boundary_src`, `sync_managed_parents`) live in one place; the other repo holds a verbatim copy. `scripts/sync-lib.sh` enforces the copy + regenerates SHA-256 checksums in both repos. CI in each repo gates self-consistency.
- Are expected to live as sibling working trees (e.g. `~/Antigravity/agentm/`, `~/Antigravity/crickets/`). The PATH-CLI sugar that would make the layout invisible is deferred to a future `dev-machine-setup` plan.

**Skill ownership** (v2.0.0 of this repo, paired with `crickets v0.1.0`):

| Skill | New home | Why |
|---|---|---|
| `dependabot-fixer` | `crickets` | Host-cross-cutting; no harness shape. Earns its keep in any repo with Dependabot + CI. |
| `ship-release` | `crickets` | Broadly useful for any git/GitHub project. Phase 05-release references it as a graceful-skip suggestion (not a hard dependency). |
| `doctor` | `agentm` (stays) | Harness-setup-specific. Verifies the harness install is correctly wired; not portable. |
| `migrate-to-diataxis` | `agentm` (stays) | Encodes ADR 0004's wiki convention. One-shot conversion specific to harness's documentation. |

The migration is a breaking change for harness users — `dependabot-fixer` and `ship-release` no longer ship from `agentm`. Hence v2.0.0.

**Customization primitive types** the toolkit recognizes (declared via `kind` in each customization's YAML frontmatter): `bundle`, `skill`, `command`, `agent`, `hook`, `mcp-server`, `status-line`, `output-style`, `workflow`, `rule`, `snippet`, `settings-fragment`. The toolkit's installer reads each customization's manifest and dispatches per `supported_hosts` to host-native paths. No more adapter-tree duplication.

**Public-with-PII-guardrails** for the toolkit. Three enforcement layers ship from day one: `scripts/check-no-pii.sh` regex scanner, `skills/pii-scrubber/` agent-interactive layer, `templates/hooks/pre-push` mandatory enforcer. CI runs both `check-no-pii.sh --all` and the official gitleaks-action. Rationale: a public repo holding personal customizations is the high-risk shape for PII leaks; structural defense beats convention.

## Consequences

**Positive**

- **Parity invariant simplifies in this repo.** `CANON_SKILLS` drops from 4 entries to 2 (just `doctor` + `migrate-to-diataxis`); `SHARED_SKILLS` in `check-references.py` matches; `install.sh` + `install.ps1` shared-skills loops shrink. Net: ~80 lines removed across scripts + installers + smoke-test expectation lists. New skills in the harness become unusual (existing four are it for the foreseeable future); new customizations grow in the toolkit instead.
- **Harness README stays focused on workflow.** The phase commands are still the protagonist. Toolkit gets a one-paragraph mention in the README + a "Related" reference in `wiki/reference/Repo-Layout.md`; that's it.
- **Customization catalog can grow without retrofitting.** Adding a new skill in toolkit is a single dir under `skills/<name>/` with a manifest. No adapter copies, no parity entries.
- **Cross-repo byte-identity discipline is exercised.** The lib/install/ sync flow is small (`scripts/sync-lib.sh` is ~100 lines) but real — it caught four cross-platform bugs during task 4's CI debugging (LC_ALL sort, $host PowerShell collision, sha256sum vs shasum, CRLF + binary-mode output). The discipline of "edit once, sync, regenerate checksums in both" is now a load-bearing pattern.
- **PII guardrails travel with the toolkit.** Anyone who installs `crickets` into their project gets a pre-push hook that scans for emails, personal paths, API key shapes, phone numbers — for free.

**Negative**

- **Two repos to keep in sync** for any `lib/install/` change. Mitigation: `sync-lib.sh` makes it a one-command operation; CI byte-identity gate catches drift before merge.
- **Breaking change for v1.x users** of the harness. Anyone who relied on `dependabot-fixer` or `ship-release` from harness must install crickets alongside. Graceful-skip patterns in `harness/phases/{03-work,05-release}.md` + `harness/skills/doctor.md` mean the harness doesn't crash without the toolkit, just suggests-then-skips the missing skills. Documented in v2.0.0 release notes.
- **PATH-CLI sugar deferred.** Long-term, both repos should be invokable as `harness` and `crickets` on `$PATH` (via `dev-machine-setup`). Today, users need to know the path to each installer. Adds friction. Tracked as a future `dev-machine-setup` plan.
- **Documentation cross-references.** Some content lives in the toolkit's wiki (e.g. installer flags, manifest schema); some in the harness's (e.g. phase specs). Readers may need to cross-reference. The harness's `wiki/reference/Repo-Layout.md` calls out the sibling repo; the toolkit's `wiki/explanation/Purpose-And-Scope.md` does the reverse.

**Load-bearing assumptions** (re-check on every major-version bump)

- The harness keeps its two retained skills (`doctor`, `migrate-to-diataxis`) and doesn't grow the catalog beyond them. If a third harness-shaped skill emerges (one that's tightly bound to the harness's phase model), it stays in harness. If it's host-cross-cutting, it goes to toolkit.
- `lib/install/` byte-identity holds. If the two repos drift, `check-lib-parity.sh` fails CI; `sync-lib.sh` is the recovery path. If drift becomes chronic, we revisit submodules or extract a third repo for the shared lib.
- Toolkit's manifest schema is stable enough to grow on. Today's schema (`name`, `description`, `kind`, `supported_hosts`, `version`, optional `install_scope` + `deprecated`) is forward-compatible; new optional fields are additive.

## Related

- [crickets ADR 0001](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0001-crickets-purpose.md) — the sibling decision in the toolkit repo, focused on the toolkit-side framing (purpose, scope, public-with-PII-guardrails).
- [ADR 0001 — Phase-gated workflow](0001-phase-gated-workflow) — the workflow story this repo keeps focused on after the split.
- `lib/install/CONTRACT.md` — the shared-lib invariants both repos depend on.
- `scripts/sync-lib.sh` — the byte-identity enforcement mechanism.
