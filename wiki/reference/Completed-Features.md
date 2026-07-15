# Completed Features

> [!NOTE]
> **Historical record.** Entries before 2026-06-24 that reference "ADR NNNN" point at decisions now folded into the living designs under [Designs](Designs). The ADR nomenclature in older rows is preserved as written, since it's what actually shipped at the time.

This page is the combined build timeline for agentm and its sibling toolkit [crickets](https://github.com/alexherrero/crickets) — one row per shipped feature, in the order it landed, so "what did we build, when" has a single answer instead of a scattered one. Read the table top-to-bottom for the story; read the mapping section below it to see which release tag actually carries a given roadmap era, since several major roadmap milestones — the whole V6 memory engine, most of V7, and the bulk of V8/Autonomy — shipped disguised inside ordinary "Minor" point releases rather than getting their own major version bump. The authoritative version history stays [`CHANGELOG.md`](https://github.com/alexherrero/agentm/blob/main/CHANGELOG.md) (agentm) and its [crickets counterpart](https://github.com/alexherrero/crickets/blob/main/CHANGELOG.md); this page exists so you can understand what shipped and why without reading every commit.

**Coverage note.** Every row from 2026-06-18 forward (agentm v5.6.0 / crickets v3.20.0 onward) covers both repos at feature granularity — this is the window a dedicated release-archaeology pass (E4, 2026-07-10) drafted feature-by-feature from the git/PR history of both repos. Rows before that date come from agentm's own release history one release at a time; crickets' releases before its v3.20.0 aren't independently itemized here, because no feature-level source covers that earlier window yet. If crickets' pre-window history gets its own archaeology pass later, its rows can backfill above the line without disturbing anything below it.

## Timeline

| Date | Feature (plain English) | Release | Roadmap id |
|---|---|---|---|
| 2026-07-12 | V8 / Autonomy declared complete, ahead of its proving report by explicit operator decision (report follows post-release); carries the Consolidation arc's follow-ups batch — inbox bulk-review triage (built, backlog cleared, confirm-gate retired), dreaming's expire action auto-applying, the machinery-integrity doctor check, scheduled vault-lint, console composition, and a visitor-wording pass | agentm v8.0.0 + crickets v3.28.0 | V8 / Autonomy declaration; Consolidation arc follow-ups |
| 2026-07-10 | Version numbers catch up to the work: agentm jumps v5.14.0 → v7.0.0 (declaring the V6 and V7 eras complete, publishing this page's own mapping table in the release notes); crickets cuts v3.27.0 (the Consolidation arc's repo-slimming, prose-restoration, and process-codification lanes) | agentm v7.0.0 + crickets v3.27.0 | Consolidation arc, ruling 4 |
| 2026-07-10 | V6-15 typed-object schema-registry (kind audit, frontmatter validator) + V6-18 browse-first MOC generator | agentm v7.0.0 (PR #274) | V6-15 / V6-18 |
| 2026-07-10 | `plan_goal` KeyError fix on a plan-less progress entry | crickets v3.27.0 (PR #180) | V8 (bugfix) |
| 2026-07-09 | Real-vault lint triage (587→561 findings after fixes), PARA/Zettel design-call note, Obsidian graph colorize by `kind` | vault-only, no repo tag | #16 / #34 |
| 2026-07-09 | Autonomy arc lands end-to-end; AG Wave E closes out; AA5 consolidation pass | agentm v5.14.0 | V8, V6, AG close-out |
| 2026-07-09 | AG Waves C+D formally close out; crickets half of the Autonomy arc lands | crickets v3.26.0 | AG close-out |
| 2026-07-09 | Task attribution wired through the observability ledger | crickets v3.26.0 (PR #176) | V8-13 |
| 2026-07-09 | Vector-inclusive re-measurement of the V6 retrieval stack — reverses the previously-shipped vector-less baseline, an honest regression against the project's own merge gate | agentm v5.14.0 (PR #265, AA5 C8) | V6-20 eval |
| 2026-07-09 | Silent-dark family closed: verification-honesty + docs/voice-health families score live or explicit-dark, never silent zero | agentm v5.14.0 (PR #264, AA5 C7) | health scorecard |
| 2026-07-09 | Observability residue trio: attribution tags, morning-report persistence, digest dogfood | agentm v5.14.0 (PR #258) | V8-13 |
| 2026-07-09 | Efficiency health-axis lit, dark-check registry cleaned | agentm v5.14.0 (PR #257, AA5 C3) | #46 token-efficiency |
| 2026-07-08 | AA4 acceptance pass: fabricated bare-install score and a fail-open budget gate both found and fixed same day | agentm v5.14.0 (PR #255/#256) | Hardening II |
| 2026-07-08 | Board tracking model decided: repurpose `Track` field for dispatch tier | agentm v5.14.0 (PR #254) + crickets v3.26.0 (PR #175) | V8 |
| 2026-07-08 | Autonomy control plane: fleet-substrate decision, dispatch, board+handoff wiring, launch-time grade statement — real acceptance run, $0.4607 spend | agentm v5.14.0 (PR #251/#252) | V8-15 / V8-16 |
| 2026-07-08 | Observability console: static dashboard, digest ladder, window-park artifact, morning report | agentm v5.14.0 (PR #250) | V8-13 |
| 2026-07-08 | Observability ledger: device-local spend/run event log, split agentm-half/crickets-half | agentm v5.14.0 (PR #249) + crickets v3.26.0 (PR #174) | V8-13 |
| 2026-07-07 | 9-name persona manifest set completed; agentm-side companion close-out | agentm v5.14.0 (PR #239/#240/#242) | AG Wave D |
| 2026-07-07 | Scheduled surfaces: health passes, docs-drift loop, goal-contract anti-gaming guard, `/status` operator surface | agentm v5.14.0 (PR #243) | AG Wave E |
| 2026-07-07 | Experience pipeline: forward-learning generalized, crystallization schema, accumulate-loop spec | agentm v5.14.0 (PR #246) | V6-6 |
| 2026-07-07 | Dreaming arc: revert-log primitive, thin `/dream`, confirm/expire, scheduled job | agentm v5.14.0 (PR #244) | V7-2 |
| 2026-07-07 | V6 retrieval engine: typed-edge knowledge graph, RRF hybrid retrieval, chunking + time-weighting, consolidation — real-vault recall accuracy roughly doubled | agentm v5.14.0 (PR #247) | V6-1/2/3/4/10/12 |
| 2026-07-07 | Wave C's last box: forward-learning + codebase-improvement watchlist scanner | crickets (PR #173) | V7-4 |
| 2026-07-07 | Persona roster: workflow-step resolver + opinion-consumer conformance sweep | crickets (PR #163/#170) | AG Wave D |
| 2026-07-07 | Idea-search over the recall engine | crickets (PR #156) | AG Wave C |
| 2026-07-07 | Dependabot-fixer + `/bugfix` cross-wired onto the shared diagnose engine | crickets (PR #168) | AG Wave D |
| 2026-07-07 | Opinion request-by-name wiring proven on two real bindings | crickets (PR #162/#167) | AG Wave D |
| 2026-07-07 | Session-cost capture, `privacy-review` skill, Semgrep taint pack, `scrub_text.py` surface — the single largest PR of the window | crickets (PR #166) | AG Wave D |
| 2026-07-07 | github-projects doc reconciliation + Planner (TPM) depth-maintainer | crickets (PR #164) | AG Wave D |
| 2026-07-07 | AG Wave C design-and-conventions cluster: `coauthor-guard`, homeless-conventions migration, 2 new `/design` rungs | crickets (PR #160) | AG Wave C |
| 2026-07-06 | 4 new maintenance primitives (deps-currency, cve-security-patch, tech-debt-inventory, content-refresh) | crickets (PR #157) | V7-5/6 |
| 2026-07-06 | Diagnostics engine: fingerprint normalizer, 2-layer recall ladder, scrubbed failure-incident writer | crickets (PR #153, → v3.26.0) | AG Wave C |
| 2026-07-06 | Persona roster grows from 2 to 11 manifests; `content-refresh` gets its first consumer | agentm v5.13.0 | AG Wave D |
| 2026-07-06 | `/work`/`/bugfix` spawn worktrees via the host's own primitive; the bespoke spawn/integrate scripts are deleted outright | crickets v3.25.0 | AG process fix |
| 2026-07-06 | V5-14 storage convergence — `save`/`evolve`/`recall` route through the storage seam instead of direct vault calls | agentm v5.12.0 | V5-14 |
| 2026-07-06 | Persona-tier activation pipeline | agentm v5.12.0 | AG Wave B |
| 2026-07-06 | V6-11 extended memory metadata + hybrid `--filter` recall + mandatory failure-incident PII scrub | agentm v5.12.0 | V6-11 |
| 2026-07-06 | Request-by-name Opinion registry (9-name catalog, base⊕supplement compose) | agentm v5.12.0 | AG Wave B |
| 2026-07-06 | AgentM runner core: job manifests, due-decision loop, budget ceiling, throttle watchdog | agentm v5.12.0 | V7-1 |
| 2026-07-05 | Anti-slop voice gate, CI path-filtered matrices, per-dispatch model/effort routing land as backlog from already-completed plans | crickets v3.24.0 | R3 / efficiency automation |
| 2026-07-05 | 7 crickets plugins rename to their settled capability nouns via declared-both-names, no alias map | crickets v3.24.0 | AG Wave A |
| 2026-07-01 | crickets relicenses to Apache-2.0/CC-BY-4.0; all 17 crickets ADRs fold into living-design amendment logs; combined Architecture+Reference page for all 13 plugins | crickets v3.23.0 | AG Phase 1-4 |
| 2026-07-01 | `/work` executor moves from Opus to Sonnet for cheaper long build stretches | crickets v3.23.0 | model+effort routing |
| 2026-07-01 | agentm relicenses to Apache-2.0/CC-BY-4.0 with a trademark policy; local `ship-release` retires in favor of crickets' unified skill | agentm v5.11.0 | AG governance |
| 2026-07-01 | 13 new living designs lifted; all 20 agentm ADRs fold into amendment logs; wiki taxonomy narrows from seven sections to six | agentm v5.11.0 | AG Phase 1-4 |
| 2026-06-19 | `auto_orchestration` splits into its three natural trigger owners with no behavior change | agentm v5.9.0 | V5-5 |
| 2026-06-18 | `resolve_plan.py` migrates to the designed process-seam bridge instead of an informal reach-in | crickets v3.22.0 | V5-4 |
| 2026-06-19 | The `vault_path` config key migrates fully into the `obsidian-vault` plugin's own settings | agentm v5.8.0 | V5-7 |
| 2026-06-19 | CI gate blocking hardcoded vault-path literals from re-entering the codebase | agentm v5.9.1 | Hardening I |
| 2026-06-18 | Capability-request matching lands: a backend can now refuse to serve a capability it doesn't have, rather than silently degrading | agentm v5.6.0 | V5-7 |
| 2026-06-18 | The routing plane (project resolution, repo registry, state mode) fully de-vaults onto the V5-1 storage seam | agentm v5.7.0 | V5-6 |
| 2026-06-18 | A proactive PII stand-in rule ships so the reactive scrubber never has to catch a real value | crickets v3.21.0 | developer-safety |
| 2026-06-18 | `recoverability` skill: the reversibility-first autonomy doctrine becomes a standing per-session instruction | crickets v3.20.0 | developer-safety |
| 2026-06-17 | Memory exposed as an MCP server — four tools, bearer-token security, host configs for Claude Code / Cursor / Goose / Claude Desktop | agentm v5.4.0 | V5-9 |
| 2026-06-16 | Team-coordinator persona: the first real composed persona, with plan-graph, standup, readiness, and merge-order tooling | agentm v5.3.0 | V5-11 |
| 2026-06-16 | Capability-discovery resolver (the `enhances:` runtime) and the persona tier substrate both land together | agentm v5.2.0 | V5-8 / V5-12 |
| 2026-06-13 | Non-UTF-8 config readers stop crashing instead of failing loud — a correctness follow-on to the storage seam | agentm v5.0.1 | V5-1 |
| 2026-06-13 | AgentM V5 ships: the memory↔storage seam, the vault-write protocol, the process seam, named plans, and the retirement of the vendored dev loop in favor of crickets plugins | agentm v5.0.0 | V5 |
| 2026-05-25 | Repo rename `agentic-harness` → `agentm`, paired with the toolkit's rename to `crickets` | agentm v3.1.0 | — |
| 2026-05-24 | First visual brand iteration: the AgentM logo asset set and a redesigned README hero | agentm v3.0.1 | — |
| 2026-05-24 | AgentM V3 ships: the merged Obsidian+GDrive vault with auto-recall in every harness phase, and the two repos adopt the AgentM/crickets brand framing | agentm v3.0.0 | V3 |
| 2026-05-23 | Quality-gates bundle: one-command install for evaluator, kill-switch, steer, commit-on-stop, and evidence-tracker together | agentm v2.6.1 | #10 |
| 2026-05-23 | Evidence-tracking for `/work`: a task can't flip done without demonstrably reading the relevant spec first | agentm v2.6.0 | #9 |
| 2026-05-22 | Auto-context lands in all five harness phases — the first non-doc-only paired release in the run | agentm v2.5.0 | #8 |
| 2026-05-22 | `diataxis-author` skill ships in the toolkit, covering the full Diátaxis-wiki lifecycle | agentm v2.4.3 | — |
| 2026-05-22 | MemoryVault discovery + mining: five new `/memory` sub-commands turn the vault from a static store into a living surface | agentm v2.4.2 | #7b |
| 2026-05-20 | Embedding mode collapses to local-only sentence-transformers, dropping the hosted API option | agentm v2.4.1 | — |
| 2026-05-17 | Gemini CLI dropped from supported hosts, keeping Claude Code + Antigravity | agentm v2.4.0 | — |
| 2026-05-16 | `/plan` gains an external-review-handoff option for long plans | agentm v2.3.1 | — |
| 2026-05-15 | `/release` + `/setup` integrate with crickets' `/design` skill | agentm v2.3.0 | — |
| 2026-05-14 | `/work` + `/release` become augmentable with crickets' base operator-control hooks | agentm v2.2.0 | — |
| 2026-05-13 | `/review` becomes augmentable with crickets' `evaluator` | agentm v2.1.0 | — |
| 2026-05-12 | The `crickets` repo splits off: `dependabot-fixer` + `ship-release` move out of agentm | agentm v2.0.0 | — |
| 2026-05-11 | Three-adapter scope settles, the Codex adapter drops, and the project commits to 1.0.0 semver discipline | agentm v1.0.0 | — |
| 2026-04-23 | Diátaxis documentation spec ships alongside the new `/doctor` skill | agentm v0.9.0 | — |
| 2026-04-21 | GitHub Projects wiring lands, dogfooded end-to-end by the `documenter` sub-agent | agentm v0.8.7 | — |

## Roadmap era ↔ release mapping

Semver and the roadmap's version line (V4 → V5 → V6 → V7 → FRIDAY → V8) are two independent numbering schemes that happen to share digits — they don't otherwise correspond. This table is the decoder: which tag actually carries which era's work. (Full evidence and the six colliding vocabularies found in the window are in the `E4-release-archaeology` evidence memo behind the Consolidation-arc review.)

| Roadmap era | What it means | agentm tags | crickets tags |
|---|---|---|---|
| V3 | Merged Obsidian+GDrive vault, auto-recall in every phase | v3.0.0 → v3.1.0 | v1.0.0 → v1.1.0 |
| V4 — "Recall, everywhere" | Cross-surface vault access, auto-orchestration push surface | v4.0.0 → v4.15.0 | — |
| Hardening I (#44–#46) | Single-repo-first-class, end-to-end test matrix, token-efficiency-by-default | v4.15.0, v5.9.1 | v3.24.0 (voice gate, CI matrices) |
| V5 — "the unbundling" | Storage-agnostic memory OS + plugin host; the dev loop moves to crickets | v5.0.0 → v5.9.0 (core), v5.12.0 (V5-14 storage convergence) | v3.1.0 → v3.22.0 |
| Architecture-Governance (AG, cross-cutting) | Classification spine, ADR retirement into living designs, the grounded review/plan loop, altitude structure | v5.11.0 (design lift) → v5.13.0 (Wave B/D) | v3.23.0 → v3.26.0 (Wave A/C/D) |
| V6 — "Memory that maintains itself" | Typed-edge knowledge graph, RRF hybrid retrieval, consolidation, the typed-object schema registry | v5.14.0 (core engine), v7.0.0 (V6-15/V6-18) | — |
| V7 — "Memory that works while you sleep" | Absorbed into AG — shipped disguised as the runner, the dreaming arc, and the maintenance primitives | v5.12.0 (runner), v5.14.0 (dreaming) | v3.26.0 (maintenance primitives) |
| V8 / the Autonomy arc — "A team of agents, one memory" | Observability ledger + console, the thin control plane, the board tracking-model decision — code shipped and proven once (a real overnight acceptance run); **era declared complete 2026-07-12, ahead of the Consolidation arc's deeper proving report by explicit operator decision** — the report follows post-release, against the same rubric, rather than gating the version | v5.14.0, v8.0.0 | v3.26.0, v3.28.0 |
| Consolidation (process, not a roadmap era) | The version ladder itself catches up: agentm's major version now equals its completed roadmap era; this page rebuilt as the combined timeline; ROADMAP-MASTER.md rewritten era-by-era; all 13 window release bodies + 56 window PRs rewritten plain-English | v7.0.0 | v3.27.0 |
| Consolidation follow-ups batch (process, not a roadmap era) | The arc's own tail: inbox bulk-review triage built and the real 1,565-note backlog cleared, dreaming's expire action auto-applies, a machinery-integrity doctor check, scheduled vault-lint, `/console` composition, a visitor-facing wording pass, and process fixes caught in operator review (a public-content-edit inconsistency, a dry-run-first departure) | v8.0.0 | v3.28.0 |
| FRIDAY | Held — gate substantively met 2026-07-07, operator ruled hold 2026-07-08; not started | — | — |

**Reading this honestly:** three separate roadmap eras — the last slice of AG, the entire V6 engine, and the whole V8/Autonomy arc — all landed inside the single agentm `v5.14.0` "Minor" release (eleven distinct features, one tag). That compression is exactly why this table exists: the semver number alone tells you almost nothing about which roadmap milestones a release actually completed.

Maintained by the `ship-release` skill, which appends a new timeline row (and, when a release closes out a roadmap era, a mapping-table update) at every cut.
