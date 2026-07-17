# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v8.2.0] — 2026-07-17 — Minor: the observability digest ladder gets fixed, then made visible

**MINOR.** This release closes out a story a design amendment opened earlier the same day: the digest ladder had been silently discovering zero jobs from its own background scheduler since the runner was first built, and even once a digest generates, nobody had a reliable way to see it. Both halves are fixed together, diagnosed independently by two sessions running in parallel and composed cleanly: [#320](https://github.com/alexherrero/agentm/pull/320) traces and fixes the runner-side root cause (plus two more real bugs two independent adversarial reviewers caught before merge), and [#321](https://github.com/alexherrero/agentm/pull/321) fixes the delivery-visibility side the design's own re-audit trigger predicted would need attention. A third, unrelated fix ([#318](https://github.com/alexherrero/agentm/pull/318)) rounds out the window: `Ideas.md`'s default path assumed the wrong storage layout for anyone on Drive-synced vault storage.

### Added

- **`/console` gains a "Runner jobs" section, and SessionStart gets a digest-stall deadman line** (`52b82af`, #320) — a job that's been silently re-anchored past its lookback window without actually running now reads differently from a real completion, in both places a human or a session would look.
- **A real, visible session-start line for the digest ladder** (`29d3552`, #321) — `scripts/health/session_brief.py` reads the digest ladder's delivered artifacts and emits one line from the already-visible `harness-context-session-start` hook, instead of being buried in a multi-KB always-load dump nobody reads. Honest-quiet when the ladder never ran; an anti-fatigue cooldown means `/clear` or a same-window resume doesn't repeat it.
- **`wiki/reference/Known-Issues.md`** — a new reference page for durable, easy-to-reintroduce gotchas, opened with the launchd-`cd`-breaks-CWD-relative-defaults lesson this release's own bug left behind.

### Fixed

- **The observability digest ladder had never once run from its actual background scheduler** (`52b82af`, #319/#320) — `agentm-runner.sh` `cd`s into `scripts/` for a sibling import, but `runner.cli`'s job-manifest path defaults are CWD-relative; every launchd-triggered cycle since the runner was first built (2026-07-05) silently discovered zero jobs, exit 0, no error. Not a regression from #316/#317 — it predated them by 10 days. Live-verifying the fix surfaced a second bug (no `MEMORY_VAULT_PATH` from the launchd plist, so a job could silently write into the repo checkout instead of the vault), which two independent adversarial reviewers then traced one layer deeper to the actual root cause inside `inbox_digest.py` itself. Confirmed working end-to-end on a real machine, not only in tests: a genuine digest note landed in the vault for the first time since 2026-07-13.
- **`Ideas.md`'s default path assumed the wrong storage layout** (`8db5797`, #318) — `ideas_surface.py`/`ideas_promote.py` defaulted to `~/Obsidian/Ideas.md`, but the real ledger lives as a sibling of the vault directory itself on Drive-synced storage. Now derived from `$MEMORY_VAULT_PATH`'s parent; an unresolvable vault raises instead of silently falling back to a wrong literal.

### Internal

- `wiki/designs/agentm-runner.md`'s As-built note corrected — it had called the host-trigger wiring "fully wired" on a verification pass that only ever proved the trigger fires, not that a fired cycle finds any jobs.
- 28 new regression tests land with #320 (`test_runner.py`, `test_console.py`, `test_orchestration_briefing.py`, `test_inbox_digest.py`) and 31 more with #321 (`test_session_brief.py`) — every one confirmed failing before its fix, passing after.

## [v8.1.0] — 2026-07-16 — Minor: the overnight loop stops grading its own homework

**MINOR.** This release cuts the work that piled up on `main` during the V8 proving window (`PROVING-REPORT.md` residual 5) — a routine cadence cut, not a new arc. The headline is proving-ledger item 19's payload: `n1_run.py`'s unattended overnight run used to decide "done" by its own say-so; `goal_contract.decide()` now makes that call instead, and the run can never self-certify. Getting an unattended dispatch far enough to prove that also surfaced and fixed three real bugs along the way (wrong dispatch `cwd`, a permission wall with no human present to click through it, and a second permission wall one step later at the final `gh pr merge`) — the full retest narrative, including the wall each fix uncovered, is in `PROVING-LEDGER.md` item 19. Alongside it: the health scorecard moves off a nightly commit-back workflow entirely, a scoped permission allowlist unblocks unattended fleet dispatch, and every `wiki/reference/` page not already rewritten this arc got a cross-model plain-English pass (pairs with [crickets v3.29.0](https://github.com/alexherrero/crickets/releases/tag/v3.29.0)).

### Added

- **Doctor detects the unattended merge-on-green permission gap** (`fdad4b5`, #314) — the retest that proved goal-contract wiring got an unattended dispatch all the way to the terminal `gh pr merge`, then stalled: a global permission-config entry blocks that command with no human present to click through the prompt. `machinery_doctor.py` gains `check_unattended_merge_gate`, which warns (with a copy-pasteable remedy) whenever `n1-overnight` is registered and that command would block — detection only, per doctor's own "reports gaps, never mutates state" contract. A new opt-in `scripts/enable-unattended-merge.sh` does the fix for anyone without the operator's own dotfiles provisioning.
- **Health scorecard moves off the nightly commit-back workflow** (`a8d022b`, #308) — `health_score.py` now appends to a vault-resolved history ledger and renders `~/.cache/agentm/telemetry/scorecard.html` locally (via the `health-pass` runner job), instead of a CI job opening and merging its own PR every night. CI still runs both check tiers as the clean-runner regression signal and uploads the scorecard as a build artifact, but no longer writes back to the repo. `Health-Scorecard.md` is now a static explainer; `/console` and the vault's own `Home.md` link the live local render.
- **Scoped permission allowlist for unattended N1 fleet dispatch** (`52d94b2`, #311) — found live during the same proving retest: a `claude --bg` session with no human present blocks forever on its first tool-permission prompt. Covers exactly what a fleet-dispatched work item needs (file edits, `git`/`gh`/`python3`, standard read-only shell tools) — not a blanket bypass; anything outside that list stays gated by the normal ask/deny behavior.
- **Morning Brief gets a real home + self-cleaning batches** (`73baf0f`, #303) — digest and window-park notes move from `personal/_inbox/` (where auto-triage was silently expiring them within hours of being written) to a dedicated `_briefs/` home neither inbox triage nor dreaming's staging walk ever touches. A resolved dream batch's staging directory now self-deletes once its 14-day revert window lapses, independent of the revert log itself, so undo capability survives cleanup.

### Changed

- **`n1_run`'s overnight loop can never self-certify done** (`9170bda`, #313 — proving-ledger item 19). `goal_contract.decide()` had no caller outside its own test since it was built; `n1_run.run_n1_sequence()` now consults it for the run's own done determination — a done-check is fingerprinted before dispatch and re-checked at decide time, `gates_green` comes from real dispatch exit codes, and `cold_review_confirmed` must be passed in explicitly rather than assumed. `--done-check`/`--cold-review-confirmed` CLI flags make this reachable from an unattended overnight invocation.
- **Every `wiki/reference/` page not already rewritten this arc gets a cross-model plain-English pass** (`6b0c114`, #315; pairs with crickets #199) — scale-up of an operator-approved pilot: each page ran through a real cross-model call (`agy`, Gemini 3.1 Pro) for a style-only rewrite, mechanically truth-guarded against the pre-rewrite text (every command, flag, path, count, date, and version number diffed and confirmed unchanged). A handful of pages that the rewrite couldn't cleanly reproduce without drift were left on their original text rather than shipped wrong.

### Fixed

- **Scheduled-job runner no longer silently swallows a job's real failure** (`756bd53` + `f3fd62a`, #316 + #317). A launchd-invoked runner process gets no `LANG`/`LC_ALL`, so Python's own startup locale coercion corrupts macOS system bash's handling of non-ASCII output next to a shell variable — this raised an uncaught decode error inside the runner, reported only as an opaque `exit_code=-1` with no diagnostic. #316 stopped the silent failure but shipped the wrong fix (`en_US.UTF-8`), which turned out to reproduce the identical corruption under the real failure shape rather than avoid it — caught only because someone ran the actual `health-pass` job by hand afterward. #317 corrects it (`LC_ALL=C`, confirmed against the real failure shape, not a simplified stand-in) and rewrites the regression test to actually prove it.
- **`n1_run` fleet dispatch lands at the project root, not `scripts/`** (`5e4c8ed` + `f1bc8ac`, #309 + #310) — a work item with no `cwd` of its own fell through to wherever the runner process happened to be running from; both real overnight work items dispatched to the wrong directory before this was caught and fixed.
- **The 17 reference pages the first truth-audit pass hadn't independently re-checked** (`5b4bca2`, #312; crickets #198) — every remaining `wiki/reference/` page's checkable claims verified mechanically against live source; 12 of 17 had real drift, each fixed with the live-source citation that corrected it.
- **Publish-time frontmatter/asset-link stripping** (`76a159a`, #304) — the wiki-sync publish transform now strips YAML frontmatter and rewrites tree-relative asset links to wiki-raw URLs consistently.

### Internal

- Three fixes to the nightly scorecard's CI dispatch/merge reliability (#297, #299, #301) are superseded by this release's own scorecard localization (#308) — the workflow they were patching no longer exists.
- Doc fixes: stale CONS-1 gate names in `AGENTS.md` (#307), the reference tree reconciled against the live post-V5-3 kernel (#306), a top-note length gate + a sweep of bloated design notes (#305), the project-archival lifecycle convention documented (#300), docs-drift's dry-run-to-live promotion threshold named (#298), the app-vault/machine-vault boundary documented in the Obsidian how-to (#296).
- crickets' `testing-strategy` skill gains an explicit Prove-It Pattern sub-rule prompted directly by the #316/#317 case above — a regression test that only ever passes was never proven to test anything (crickets #200).

## [v8.0.0] — 2026-07-12 — Major: the Autonomy era is declared complete

**MAJOR.** This release declares the V8 / Autonomy era complete: the observability ledger + console, the thin control plane on host Agent View, and the board-tracking-model decision — all shipped already, in v5.14.0, and proven once with a real overnight acceptance run ($0.4607 spend, matched against the raw event log). What's different this time is the sequencing, by explicit operator decision (2026-07-11): this declaration precedes a second, deeper proving report rather than waiting on it. The standard sequencing — code ships, then a real-use proving window runs, then the version declares the era complete once that proof holds — is what governed v7.0.0's own hold on this exact version bump two days ago. This release inverts the order: the operator judged the code, and the first acceptance run, sufficient to declare now, with the Consolidation arc's proving window (dreaming cycles, an overnight N1 run, the digest cadence, a week of daily `/console` use) continuing in parallel and reporting **post-release**, against the same rubric and health families the Day-7 gate would have used. This is a versioning-doctrine call, not a claim that the proof already landed — the report follows, as a published document, not a version gate.

Alongside the declaration, this release carries a full batch of follow-up work from the same Consolidation arc — six PRs (#289–#294), plus everything they surfaced along the way. v9 still opens whenever FRIDAY ships; that hasn't moved.

### Added

- **The long-promised `_inbox/` bulk-review pass** (`a688db3`, #292) — `/memory inbox --bulk-review` (`harness/skills/memory/scripts/inbox_triage.py`). A bulk-review command was named as required follow-up as far back as the original MemoryVault design docs and never built; `personal/_inbox/` had grown to 1,565 notes (55% of the whole vault), of which only 2 had ever transitioned out of `status: inbox`. The new pass proposes promote (reinforced entries graduate to a canonical curated note), merge (near-duplicates, reusing dreaming's own similarity stage), or expire (stale, archived in place, never deleted) for every still-untriaged entry, staged through dreaming's existing confirm/revert-log machinery unmodified. It shipped confirm-gated for its one-time first run over the pre-existing backlog; the operator personally reviewed and confirmed that entire run — 635 proposals across 1,565 entries, zero errors — and directed that confirm-gating retire going forward (see Changed, #293).
- **Machinery-integrity doctor check** (`bf9f650`, #291) — `scripts/machinery_doctor.py` answers "is this repo's own dev-loop machinery — its hooks, its scheduled jobs, its cross-repo bridges — actually wired on this machine right now," closing the exact class of gap that let the session-cost-capture hook and crickets' cross-review Gemini fallback go unnoticed for weeks. Wired into `/doctor` as a new structural check.
- **Vault-lint on a schedule** (`bf9f650`, #291) — `templates/jobs/vault-lint.yaml` registers `vault_lint.py`'s existing audit mode as a weekly runner job, dry-run-first like every sibling job template (corrected to match that pattern in `810cc2b`, #294, after an initial live-by-default departure was caught in review).
- **Console composition** (`bf9f650`, #291) — `/console` grows a machinery summary line, a live vault-doctor section, a vault-lint freshness line, the nightly health-scorecard's last-run age, a dreaming auto-expire line, and an always-printed rich-view (HTML) link at the end of every terminal run.

### Changed

- **Dreaming's expire (compression) action applies automatically; promote (dedup) and link (contradiction-triage) stay confirm-gated** (`849ea98`, #290) — an explicit, narrow operator ruling, not a general autonomy expansion: compression retires notes via a relationship the vault already declared (a `supersedes:` chain), never a freshly-inferred one, and every mutation still runs through the same revertible journal a manual confirm uses. Standing cadence set to weekly with a 25-note batch cap, replacing the proving window's temporary alternate-day/100-note artifact.
- **Inbox triage's confirm-gate retires: every disposition now auto-applies** (`7cfaa90`, #293) — after personally confirming the full first-run backlog with zero errors, the operator ruled confirm-gating unnecessary going forward. Promote, merge, and expire all auto-apply by default now, regardless of an entry's age; the manual `--confirm`/`--reject`/`--list` CLI paths and a `--no-auto-apply` opt-out remain available for anyone who wants to inspect something by hand.
- **README/wiki Home lead and the explanation landing page say what AgentM does in plain terms** (`fe4c7e2`, #289) — adopts the plainer, teaching-grade explanation of what this project is and does, in place of more abstract prior phrasing.

### Fixed

- **`vault-lint.yaml` corrected to ship dry-run-first** (`810cc2b`, #294) — its first registration shipped live (`dry_run: false`) by a unilateral judgment call that departed from the dry-run-first-then-supervised-promotion pattern every other scheduled job in this arc followed; caught in review and corrected.

Pairs with [crickets v3.28.0](https://github.com/alexherrero/crickets/releases/tag/v3.28.0), the companion release for the crickets-side half of this same follow-up window.

## [v7.0.0] — 2026-07-10 — Major: the version catches up — this repo contains the finished V6 and V7 eras

**MAJOR.** This release does not ship one new feature — it corrects a version number that had fallen years behind the work. Per the Consolidation arc's versioning-repair ruling (Ruling 4 of the `CONSOLIDATION-VERDICT.md`, ratified 2026-07-10), this repo contains the finished V6 era (the memory engine: retrieval, the knowledge graph, dreaming, the experience pipeline) and the finished V7 era (self-maintenance: the scheduled-job substrate, dream mode, auto-maintenance) — both complete, but shipped disguised inside a run of v5.x tags because the harness's own release cadence never tracked the roadmap's own era numbering. From this release forward, agentm's major version number equals the roadmap era it has completed. **v8.0.0 (the Autonomy era) is deliberately not cut here** — its code shipped already, in v5.14.0, but the operator's own standard is that the version is earned by proven adoption, not by code merely existing, and that proof is the job of this arc's still-open proving window; v8.0.0 cuts at arc exit if that proof holds. v9 opens whenever FRIDAY ships. Alongside the version correction, this release carries everything merged since v5.14.0: two pre-arc items (#270, #274) and three of the Consolidation arc's own Wave 1/2 lanes plus one follow-up fix (#279, #280, #281, #282) — all listed below.

### The roadmap-era ↔ release-tag decoder

This mapping reflects the roadmap-era reconciliation completed during this same Consolidation arc's versioning-repair pass. A full combined-timeline rebuild of `wiki/reference/Completed-Features.md` (Ruling 5) publishes the equivalent decoder as its own page — see that page for the definitive, ongoing version once it lands.

| Roadmap era | What shipped | Tag(s) it shipped inside |
|---|---|---|
| **V6** — "Memory that maintains itself" (core) | Typed-edge knowledge graph, RRF hybrid retrieval, paragraph-aware chunking, time-weighted decay, episodic→semantic consolidation | [v5.14.0](https://github.com/alexherrero/agentm/releases/tag/v5.14.0) (AG Wave E) |
| **V6-11** | Extended `entry_meta` + hybrid `--filter` recall path | [v5.12.0](https://github.com/alexherrero/agentm/releases/tag/v5.12.0) (AG Wave B leader 3/5) |
| **V6-15 / V6-18** | Typed-object schema registry + browse-first MOC generator | **This release** — PR #274 was previously unreleased; v7.0.0 is its first tag |
| **V7** — scheduled-sidecar framework | The AG Wave-B runner (job manifest, due-decision cycle, dry-run-until-promoted, throttle-pause-stop watchdog) | [v5.12.0](https://github.com/alexherrero/agentm/releases/tag/v5.12.0) |
| **V7** — dream mode + the revert-log | The dreaming arc (revert-log primitive, `/dream`, confirm/expire cycle, scheduled job) | [v5.14.0](https://github.com/alexherrero/agentm/releases/tag/v5.14.0) |
| **V7** — the external-scan | The forward-learning coda (approved-source pipeline, crystallization) | [v5.14.0](https://github.com/alexherrero/agentm/releases/tag/v5.14.0) |
| **V7** — auto-maintenance / security vuln-watch sidecar | crickets' maintenance primitives + the Planner (TPM) persona / `cve-security-patch` | crickets-side — no agentm tag |
| **V8 / Autonomy** (code shipped, era not yet declared complete) | Observability ledger + console, control plane, board-tracking-model decision | [v5.14.0](https://github.com/alexherrero/agentm/releases/tag/v5.14.0) — **v8.0.0 waits for the proving window**, per the ladder |

### Added

- **V6-15 / V6-18 — typed-object schema registry + browse-first MOC generator** (`395fc17`, `cd5bee4`, `904a24c`, #274). A kind-taxonomy audit + schema registry (`kind_registry.py`, 13 tests), a check-only frontmatter validator (`frontmatter_validator.py`, 13 tests) that excludes the vault's internal state directories, and a browse-first MOC generator with a `Home.md` "Browse by kind" backlink section.
- **CONS-7 — the Console v1** (`6e3ed4a`, #280). A new `/console` skill (`harness/skills/console/`) composing five existing, already-shipped observability surfaces — the health index, queue-status-lite, the board-drift detector, the spend rollup, and memory activity — behind one terminal-first report, plus an `--html` mode. Builds nothing new underneath; finishes wiring that was already designed but never turned on.

### Changed

- **CONS-1 — agentm slim** (`a3d3f9f`, #279). `bash scripts/check-all.sh` goes from 38 named local gates to 33 without weakening any gate's protection: five vendored-parity gates merge into one parametrized `check-vendored-parity.sh`, three one-way import-direction checks merge into one, and seven confirmed-dead scripts are deleted (`list-plans.sh`/`.ps1`, `memory_mcp_probe.py`, `rename-vault-personal-projects.{sh,ps1}`, `rename-vault-root.sh`, `migrate-adr.py`) along with their companion tests.
- **CONS-3 — agentm prose restoration** (`c1c7613`, #281). `check-slop.py` now blocks at warning-tier and above in `check-all.sh` (previously report-only), wired with `--wiki-root wiki` so the per-repo overlay hook resolves. The surviving `load-bearing`/`first-class` term-of-art usage gets a documented carve-out in the existing rule-pack overlay rather than a false positive.

### Fixed

- **Retire-invariant guard tests ignore stale worktree checkouts** (`d692870`, #270). The three regression tests guarding against retired code reappearing now enumerate the tree via `git ls-files` instead of a hardcoded exclusion list, so a stale `.claude/worktrees/` checkout left behind after a merge — routine under the worktree-native flow — can no longer false-fail the full gate battery by matching the guard's own retired-path text in its docstring.
- **`orchestration_briefing.py` vault-path mismatch for inbox/incubator counters** (`7f85605`, #282). `count_inbox()` was reading `<vault>/_inbox` instead of the real `<vault>/personal/_inbox`; `count_incubator_pending()` had the opposite-direction mismatch, reading `personal/_idea-incubator` instead of the real root-level `_idea-incubator`. Both silently undercounted in production, so the SessionStart pending-state briefing undercounted these signals. Corrected, with negative-direction regression tests added.

## [v5.14.0] — 2026-07-09 — Minor: Autonomy arc lands — observability ledger + console + control plane

**MINOR.** The Autonomy arc (recast V8) ships end to end: a device-local telemetry ledger with a scheduled runner aggregator, a static observability console (dashboard, digest ladder, window-park artifact, morning report), and a control plane on top (substrate decision, dispatch, board+handoff wiring, launch-time grade statement) closed out by a real N1 acceptance run. Alongside it, AG Wave E closes out — the V6 retrieval engine (typed-edge graph, RRF hybrid retrieval, chunking, time-weighted decay, consolidation), the dreaming arc, and the experience pipeline all land — and an AA5 consolidation pass reconciles personas/opinions to as-built, lights the efficiency health-axis, and re-measures the V6 retrieval stack with the vector stream actually live, honestly reversing a previously-shipped baseline number. Pairs with [crickets v3.26.0](https://github.com/alexherrero/crickets/releases/tag/v3.26.0), which ships the crickets half of the observability ledger and the board tracking-model decision.

### Added

- **Observability ledger** (`da23e7e`, #249; crickets #174) — a device-local spend/run event log folded on a schedule into a SQLite rollup by plan/task/model/window (`scripts/runner/aggregator.py`).
- **Observability console** (`7963a7c`, #250) — a static dashboard, a digest ladder into the vault `_inbox/`, a window-park artifact for a mid-run rate-limit stop, and a morning report naming what a run did, spent, and why it ended.
- **Observability residue trio** (`ec2ebe5`, #258) — attribution tags on ledger events, `morning_report.py --out` persistence, and digest dogfood.
- **Autonomy control plane** (`8f5eb9e`, `7d0c808`, #251/#252) — `scripts/control_plane/`: `dispatch.py`, `board_sync.py`, `grade.py`, `handoff.py`, `n1_run.py`. Resolves the fleet-substrate decision (Agent View, not Agent Teams, re-verified live against Claude Code 2.1.193) and runs a real N1 acceptance demo (two live Agent View background sessions, $0.4607 spend, matched against the raw event log).
- **AG Wave E — V6 retrieval engine** (#247) — deterministic typed-edge knowledge graph (`graph.py`), RRF hybrid retrieval replacing the old weighted-sum merge, paragraph-aware chunking + time-weighted decay scoring, and episodic-to-semantic consolidation (`consolidate.py`).
- **AG Wave E — dreaming arc** (#244) — a revert-log primitive, a thin `/dream` pass, a confirm/expire cycle, and a scheduled job wiring it onto the runner.
- **AG Wave E — experience pipeline** (#246) — forward-learning's approved-source screen, crystallization, and the accumulate-loop spec.
- **AG Wave E — scheduled surfaces** (#243) — the runner substrate (job manifest, due-decision cycle, dry-run-until-promoted, throttle-pause-stop watchdog) the above jobs register against, plus the goal contract's anti-gaming Decide-step guard.
- **feat(personas): workflow-step persona resolver** (#240) — the four new manifests' `triggers:` now carry the intended workflow-step names.

### Changed

- **Board tracking model decided** (#254; crickets #175) — `board_sync.py` repurposes the board's `Track` field for dispatch tier rather than adding a by-agent axis, since no agent-identity concept exists in the dispatch substrate to track by.
- **Vector-inclusive re-measurement of the V6 retrieval stack** (#265) reverses the previously-shipped vector-less baseline — the eval sandbox couldn't load `sqlite-vec` at all before (a stock-Python limitation, not a `sqlite-vec` problem); re-running the pinned eval with the vector stream actually live is a genuine, honest regression against this project's own merge gate (`accuracy_regressed=True`), recorded in [agentm-memory-system.md](wiki/designs/agentm-memory-system.md)'s amendment log rather than smoothed over.
- **Efficiency health-axis lit + dark-check registry cleaned** (#257).
- **Silent-dark family of checks closed out** — verification honesty plus docs/voice health (#264).
- **Personas + opinion-registry reconciled to as-built** (G12 AG close-out sweep) — all 11 roster manifests confirmed shipped.

### Internal

- **fix: runner budget gate fails closed + scorecard never fabricates a bare-install score** (#256).
- **fix(scripts): resolve crickets sibling checkout worktree-aware** (#245); **route `docs_drift_job`'s crickets-sibling lookup through `sibling_repo_root`** (#248); **add `agentm/autonomy` to `check-governs-index`'s area taxonomy**.
- **health(nightly): extend cold-install job with B3's two acceptance criteria** (#255); append the G12 Lane-2 close-out scorecard run (Health Index 100.0/100).
- **Two stale merged-PR worktrees cleaned up** (`v6-c8-vector-remeasure`, `efficiency-health-axis`) — the same `check-all.sh` false-positive class flagged as a known follow-up in v5.12.0's entry below (`test_diataxis_author_retired.py`'s file-walker doesn't exclude nested `.claude/worktrees/` checkouts); still not root-fixed, tracked as a follow-up.

## [v5.13.0] — 2026-07-06 — Minor: AG Wave D persona roster — 9 new manifests, activation-axes retrofit, content-refresh's first consumer

**MINOR.** The persona roster from `agentm-personas.md` goes from 2 authored manifests to 11: the four activation axes (`tier:`/`opinions:`/`modes:`/`triggers:`) are retrofitted onto `brain` and `team-coordinator`, and the 9 remaining designed-only rows (Architect, Designer, Tech-Lead, Engineer, Reviewer, Operator, Troubleshooter/SRE, Researcher, Maintainer) are authored for the first time. A fresh-session re-audit confirms agent-def `effort:` frontmatter is a real, currently-documented dispatch-time binding — closing the P12 Task 2b open question. `content-refresh` (already shipped in crickets) gets its first named consumer: a re-pin entrypoint for the model-effort-routing chart's pinned model-id strings.

### Added

- **9 new `kind: persona` manifests** (`personas/{architect,designer,tech-lead,engineer,reviewer,operator,troubleshooter,researcher,maintainer}.md`). Each carries the four activation axes matching the roster table in `agentm-personas.md` — `tier:` per its declared T0-T4 rung, `opinions:` mirroring its "Leans on" column (verified against real `opinions/*.md` names), `enhances:` mapping its "Composes" column to real crickets capability names (verified against each plugin's `group.yaml`), and `modes:` matching its declared launch-mode set exactly, including the Reviewer's single locked `sub-agent`-only mode for cold adversarial independence.
- **`scripts/model_effort_routing_refresh.py`** — the agentm-side re-pin entrypoint for `agentm-model-effort-routing.md`'s five pinned model-id strings, delegating into crickets' already-shipped `content_refresh.py` engine via the same sibling-checkout pattern `check-slop.py` already uses. Bypasses the not-yet-built weekly model-drift-detector scheduler, per design — this is the consumer contract only.

### Changed

- **`brain.md` and `team-coordinator.md` gain the four activation axes** (`tier:`/`opinions:`/`modes:`/`triggers:`) that `agentm-persona-activation.md:135` had named as the one still-open item from the Wave B build.
- **`agentm-model-effort-routing.md`'s stale doc-drift citation is corrected** — the design's "still name `claude-sonnet-4-6`" claim about three crickets agent-defs is now verified stale; all three already carry `model: claude-sonnet-5`. A new amendment-log entry records the fresh-session `effort:`-binding re-audit outcome (confirmed BINDS, per Claude Code's own current subagent-frontmatter documentation).

### Internal

- **`test_check_personas.py`** gains a real-manifest assertion that `brain`/`team-coordinator` carry non-vacuous activation axes (not just gate-shape-valid).
- **`test_persona_resolve.py`** gains a real-tree test adopting each of the 9 new personas through its own first-declared mode, plus a dedicated Reviewer cold-sub-agent-only assertion.
- **`test_model_effort_routing_refresh.py`** covers the mechanical re-pin path, the judgment-bound new-model surfacing path, a real-tree checklist/chart-citation sanity check, and the missing-sibling-checkout loud-failure path.
- **Workflow-step automatic-adoption (plan task 3) is deferred, not shipped.** `agentm-persona-activation.md`'s own locked design call states the phase-command prose itself is the source of truth for a workflow step (`triggers:` feeds only sub-agent routing, never a competing workflow-step selector) — the actual wiring is a crickets-side change to `/plan.md`/`/work.md`/`/review.md`/`/bugfix.md`, outside this release's repo scope. The four new manifests' `triggers:` already carry the intended workflow-step names (`plan-phase`, `work-phase`, `review-phase`, `bugfix-phase`) for whichever side picks this up next.

## [v5.12.0] — 2026-07-06 — Minor: AG Wave B lands — runner, opinions, memory metadata, persona activation, storage convergence

**MINOR.** All five Architecture-Governance Wave B leader features ship in this release: the AgentM runner core, the request-by-name Opinion registry, extended memory metadata with a hybrid `--filter` recall path, the persona-tier activation pipeline, and storage convergence routing entries and MCP through the storage seam. Alongside them, per-plan CI hardens with a nightly main-HEAD full-matrix backstop, a batch of Windows portability fixes surfaces bugs actual CI runs caught that local `check-all.sh` couldn't (fastmcp isn't installed locally), and the always-on persona's manifest file catches up to the name its designs have used since late June.

### Added

- **The AgentM runner core** (`scripts/runner/`, Wave B leader 1/5). Job manifest schema and loader, a one-cycle due-decision loop (schedule/lookback/gate), state markers with orphan-start crash recovery, dry-run-until-promoted safety, T2/T3 ownership-tier write routing through `vault_lock.py` as the vault's third writer, a daily-USD budget ceiling, and a throttle-pause-stop watchdog.
- **The Opinion registry** (Wave B leader 2/5). `opinion_resolver.py` + `agentm-opinion.sh` resolve a name to its coded base plus learned supplement (served/base-only/no-opinion/error, never raising), mirroring `governs_resolver.py`'s shape. Ships the nine-name catalog as thin stubs (`done`, `good`, `efficient`, `how-we-engineer`, `recoverable`, `private`, `ready`, `simple`, `worth-knowing`), each pointing at the artifact that already carries the standard. New gates `check-opinion-resolver-one-way` (no plugin imports) and `check-opinion-honesty` (no orphan persona `opinions:` references).
- **V6-11 — extended `entry_meta` + hybrid filter + failure-incident scrub** (Wave B leader 3/5). `vec_index.py` gains an additive `entry_meta` migration (kind/status/slug/project/created/tags/group_name/fingerprint) with indexes on kind/project/status; `recall.py` gains a `--filter` path (e.g. `tag=security AND project=sherwood`) that compiles to a SQL `WHERE` joined with the vector match, with the grep pass staying as the graceful fallback. `save.py` + new `privacy_scrub.py` mandatorily scrub failure-incident writes — self-contained agentm-native redaction (never a call into crickets code, which would invert the one-way capability bridge) — and refuse loudly rather than write unscrubbed if the scrubber is ever unimportable.
- **Persona-tier activation pipeline** (Wave B leader 4/5). `persona_resolve.py`'s `adopt(name, mode)` pipeline (select, gate, load, resolve bindings, compose); `persona_compile.py` + `install.sh` do the per-host launch compile — Claude Code gets an agent-def (`triggers` → description, `tier` → `model:`), Antigravity gets a `SKILL.md` wrap. Four new `check-personas.py` manifest-axis checks (`tier` in T0-T4, `modes` subset of `{sub-agent,interactive,loop,goal}`, `triggers` non-empty, `opinions` shape-only).
- **`fingerprint` as an optional frontmatter field** (#234). `save.py` now writes the key `vec_index.py` already parsed, closing the gap where the recall ladder's Layer-1 exact-match join key had no write path.
- **Nightly main-HEAD CI backstop** (#236). Since per-plan CI moved the full 3-OS matrix to `pull_request`-only, a push straight to `main` no longer triggers it on its own; a new `nightly-main.yml` (cron + `workflow_dispatch`) reruns the same battery a PR would against `main` HEAD, folding its own conclusion into the health scorecard. Advisory only — never a required check.

### Changed

- **Storage convergence (V5-14) routes entries and MCP through the storage seam** (Wave B leader 5/5). `save.py`, `evolve.py`, and `recall.py` now write through `DeviceLocalBackend`'s seam verbs instead of calling `vault_lock.atomic_write` or `os.walk` directly; `memory_mcp_tools.py`'s two remaining direct violations (`_find_by_idem_tag`'s raw `rglob`, `memory_forget`'s direct `atomic_write`) are fixed the same way. `storage_seam.py` + `storage_device_local.py` are vendored into the memory skill's scripts dir (mirroring the existing `vault_lock.py` vendoring convention), with a new `check-storage-seam-vendor-parity` gate keeping the two copies byte-identical.
- **CI gates the full 3-OS matrix on `pull_request`, not on push to `main`.** Push to `main` now runs only the light Linux syntax job; the nightly backstop above covers direct-to-main pushes. `tests-{linux,mac,windows}.yml` + `ci-all.yml` also gain `paths-ignore: [wiki/**, **/*.md]`, skipping the full matrix only when every changed file in a diff is docs/wiki — a diff mixing one code file with any number of doc files still runs it.
- **The always-on persona's manifest file is renamed `rememberer.md` → `brain.md`**, matching the "the brain" name its own designs (`persona-tier.md`, `agentm-persona-activation.md`, `agentm-personas.md`) have used since 2026-06-26. Frontmatter `name:`, test fixtures, and reference pages that named the old filename move with it; historical dated log entries are left untouched as frozen history.

### Internal

- **Windows portability fix batch (V5-14 follow-up).** Four real bugs surfaced by actually running CI (fastmcp isn't installed locally, which had been silently hiding some of this): a concurrent-`mkdir` race in `DeviceLocalBackend.__init__` where Windows can return `ERROR_ACCESS_DENIED` instead of `ERROR_ALREADY_EXISTS` under contention; two spots emitting backslash-separated IDs via `str(Path)` instead of `.as_posix()`; a test that invoked `bash` directly and could resolve to the WSL launcher on Windows runners; and a CRLF `write_text` fixture that silently broke `recall.py`'s frontmatter-boundary check, making a whole filter-test file return empty results.
- **`fix(tests)`: pin `TestClient`'s `Host` to `localhost` in MCP Origin-validation tests.** CI had been red since before this wave's work, unrelated to it — an unpinned `fastmcp` dependency default change meant Starlette's `TestClient` sent a `Host: testserver` header that fastmcp's DNS-rebinding check doesn't recognize as safe, 403ing every request regardless of `Origin`. Invisible locally because the whole suite gracefully skips when `fastmcp` isn't installed.
- **Gitleaks allowlist fix** (#237). The nightly backstop's full-history scan (unlike a PR-scoped diff scan) flagged `test_privacy_scrub.py`'s fake GitHub PAT fixture as a real secret; allowlisted the file by path, the same principle as the existing self-referential `.gitleaks.toml` entry.
- **Wiki-authorship passes** flipped the V6-11, V5-14, opinion-registry, persona-activation, and runner design docs from `[PENDING-IMPL]` to as-built, each reconciled against the actually-shipped code (e.g. `requires:` is validated at the gate only, not re-resolved through `capability_resolver.py` as one design's Dependencies section had implied).
- **Voice/vocabulary sweep** (PLAN-r3-voice-mechanism) thinned "load-bearing" hits and redundant antithesis phrasing across the living-design corpus; a new `check-slop.py` delegator hands off to crickets' anti-slop gate (`$CRICKETS_REPO_ROOT` override, else `../crickets`) rather than duplicating its rule pack, graceful-skipping when the sibling repo isn't checked out.
- A known, pre-existing local artifact: `check-all.sh` reports 35/36 when run from a checkout with a nested git worktree present (e.g. under `.claude/worktrees/`) — `test_diataxis_author_retired.py`'s file-walker doesn't exclude that gitignored path and self-matches on its own needle string. Confirmed absent from CI (fresh checkout, no nested worktree); tracked as a follow-up fix, not a release blocker.

## [v5.11.0] — 2026-07-01 — Minor: relicensing, ship-release retirement, the Architecture-Governance track lands

**MINOR.** AgentM relicenses from MIT to a medium-matched split — code under Apache-2.0, documentation and prompts under CC-BY-4.0, with a new `TRADEMARK.md` covering the "agentm" / "AgentM" name — and retires its local `ship-release` copy in favor of crickets' unified `releasing-conventions` skill (pairs with [crickets v3.23.0](https://github.com/alexherrero/crickets/releases/tag/v3.23.0)). The release also lands the agentm half of the Architecture-Governance track: thirteen new living designs (personas, opinions, memory-system, experience-and-dreaming, model-effort-routing, the runner, the goal contract, and more) lifted into `wiki/designs/`, all twenty historical ADRs folded into their governing designs' amendment logs via the new deterministic `migrate-adr.py`, and `wiki/decisions/` retired outright now that the wiki runs a six-section taxonomy. A full wiki-landing-page rework and a README/CONTRIBUTING split round out the release; none of the governance/docs work changes runtime behavior.

### Changed

- **`LICENSE`** is now Apache-2.0 (was MIT) — the explicit patent grant + `NOTICE` attribution file strengthen credit at zero cost to openness.
- **`ship-release` is now purely crickets-provided.** agentm's local mechanical release-executor copy is deleted; crickets' `releasing-conventions:ship-release` skill now owns both discipline and mechanics in one place. agentm treats `ship-release` like `dependabot-fixer` and `pii-scrubber` — a crickets-provided, graceful-skip skill, still recommended by the `R-changelog` detection rule. Deletes `wiki/how-to/Cut-A-Release.md`; callers repointed at crickets' [Releasing Conventions](https://github.com/alexherrero/crickets/wiki/Releasing-Conventions) reference page. A companion fix a few commits earlier repointed broken cross-repo ADR links in the MemoryVault design parts to crickets' living designs, cleaning up references that predated the retirement.
- **The always-on persona is renamed "the brain"** (was "rememberer") across the launched persona designs and diagrams — a naming-only change; the code rename (`personas/rememberer.md` → `brain.md`) is deferred to the forthcoming persona-activation build.

### Added

- **Thirteen new living designs lifted into `wiki/designs/`** (all `status: launched` unless noted): `agentm-foundations-hld`, `agentm-hld` (succeeding the retired `memory-os-architecture.md`), `agentm-experience-and-dreaming`, `agentm-memory-system`, `agentm-model-effort-routing`, `agentm-opinions-and-gates`, `agentm-personas`, `memory-storage-seam`, `persona-tier`, `agentm-runner`, `agentm-opinion-registry`, `agentm-persona-activation`, `agentm-goal-contract`, `agentm-memory-index` — plus `agentm-vault-storage-presentation`, which stays **`status: proposed`** (governs no code yet; the git transport is the crickets `vault-git` plugin, itself proposed-only in this cycle). Three landmark HLDs (`agent-memory-evolution.md`, `memory-os-architecture.md`, `device-wide-architecture.md`) are vault-archived with historical banners, not deleted; content folds into `agentm-hld` / `agentm-foundations-hld`.
- **`LICENSE-CONTENT`** — CC-BY-4.0 for documentation, prompts, agent instructions, and skill / command / workflow definitions (the prose where the contribution lives). Boundary rule: a prompt embedded as a string literal inside a code file is content (CC-BY-4.0).
- **`NOTICE`** — Apache attribution notice + the code/content license map.
- **`TRADEMARK.md`** — brand policy for the "agentm" / "AgentM" name and logos.
- **New Supported Configurations reference page** (install scope, vault storage, state mode, hooks, hosts), plus a new "Install machine-wide (recommended)" how-to (Drive-vault + `install.sh --hooks --scope user` + crickets-bootstrap flow, with a doctor-check step and troubleshooting table) and a new PII Guardrail reference mirroring crickets' page.

### Internal

- **The ADR model is fully retired in agentm.** All 20 agentm ADRs fold into the living designs they governed — 9 in one pass, the 3 remaining held ADRs individually, and the seam sextet folded into the new `wiki/designs/memory-storage-seam.md`. `wiki/decisions/` is deleted; `Decisions.md` becomes a redirect stub pointing at Design-Governance and the living designs. The executor behind every fold is the new deterministic `scripts/migrate-adr.py` — dry-run by default, rewrites inbound links, prunes index/sidebar entries, reports ungated prose mentions for manual reconcile, never blind-rewrites semantics. New governance substrate: `scripts/governs_resolver.py` (frontmatter `governs:`/`area:` resolver, most-specific-pattern-wins, fail-loud on overlap) plus `wiki/reference/Design-Governance.md` documenting the convention.
- **The wiki taxonomy retires its "Decisions" section, dropping from seven sections to six** (How-To · Reference · Architecture · Designs · Explanation · Operational), machine-wide across agentm and crickets, now that decisions live in living-design amendment logs. `harness/documentation.md`'s Template 3 "ADR" becomes "Decision record"; `check-wiki.py` drops `decisions` from its folder-mode set.
- **Two extensive adversarial settle-sweeps** fixed cross-reference residue, dead links, and graph/index disagreements between `_Sidebar.md` and `Designs.md` across the design corpus, plus a stale "chief-of-staff" naming renamed to **Planner (TPM)**. A repo-wide voice/prose conformance sweep applied Plain English Directness to the design corpus — run-ons split, and/or-chains decomposed into bullets, "this, not that" flourishes rephrased where they weren't load-bearing.
- **README now mirrors `wiki/Home.md`** instead of duplicating a long-form, partially-stale copy. The "Why AgentM?" comparison table moved to `wiki/explanation/Product-Intent.md`, its natural home. **CONTRIBUTING.md becomes the real contributor guide**; README's Contributing section shrinks to one line pointing at it. Every top-level wiki landing page was reworked (Home, Architecture, Designs, Explanation, Reference, How-To) for plainer, why-first prose. A repo-wide brand-name sweep standardized "Agent M" → "AgentM" across wiki prose and the banner alt text (preserving the distinct names `AgentMemory` / `Agent Memory` / `Agent Manager`).
- **`persona-tier` build-part 3 (on-demand load + surfacing path) is explicitly marked `[PENDING-IMPL]`** — a correction from an earlier overstatement that all four build parts had shipped; only parts 1–2 + 4 are built. The `agentm-persona-activation` design specifies the not-yet-built surfacing mechanism. `memory-storage-seam.md`'s T1 user-space write (a separate, explicit seam call) is likewise `[PENDING-IMPL]`, deferred to the future tier work.
- A no-op commit triggered a repo metadata refresh (GitHub `mentionableUsers` recompute) — the tail of the long-running `claude` contributor-chip cleanup.

## [v5.10.0] — 2026-06-19 — Backend-aware harness state via the storage seam

**MINOR.** Harness state I/O is backend-aware again. `harness_state_dir` / `read_state_file` / `write_state_file` route to `<vault>/projects/<slug>/_harness/` when a *live synced backend* is active and gracefully degrade to device-local `<project_root>/.harness/` otherwise — reversing [ADR 0018](wiki/decisions/0018-v5-3-storage-cutover.md) DC-1's device-local-only state I/O while keeping every other V5-3 decision (DC-2–DC-7) intact. The discriminator is `backend.capabilities.sync` on the backend `resolve_project` already returns (duck-typed — the kernel never imports the vault plugin), *not* the presence of a `vault_path` config key. Precedence: a `.harness/.project-mode=local` opt-out wins, then a synced backend → vault, else device-local. This is not a return to the pre-V5-3 `vault_path()` probe-and-prefer tier — it routes through the V5-6 `resolve_project` seam. It fixes the surfacing regression where an operator on a synced backend kept their PLAN/ROADMAP/progress in the vault but the kernel read the empty device-local `.harness/`, so the SessionStart hook never injected them. Storage-seam no-Path-leak and import-direction gates remain green (the three state functions are outside the gate's routing-function set by design).

### Changed

- **Backend-aware harness state — `harness_state_dir` / `read_state_file` / `write_state_file` (ADR 0020, reverses ADR 0018 DC-1).** New private helper `_state_backend_target(resolution)` is the single routing decision: returns `(backend, harness_locator, backend_root)` when a synced backend should serve state, else `None` (device-local). Gated by `_read_project_mode(resolution) == "local"` (opt-out wins) → `resolution["backend"].capabilities.sync` (any exception → `None`, graceful degradation). The three public functions consult it: `harness_state_dir` returns the vault `_harness` path or the device-local `.harness/`; `read_state_file` reads via `backend.read(locator)` (FileNotFoundError → `""`) or the device-local file; `write_state_file` writes via `backend.write(locator, content)` (preserving the VaultBackend's `vault_mutex` + content-hash CAS + atomic write) or the device-local file. Vault-absent / fresh-install / selection-raises all collapse to device-local through the existing `resolve_project` graceful path.

- **Session-start hooks co-locate `progress.md` with the resolved `PLAN.md` (`.sh` + `.ps1`).** `harness-context-session-start.{sh,ps1}` previously hardcoded `progress.md` to the device-local `<cwd>/.harness/`, which blocked the singleton injection on a synced backend even after the kernel fix. They now derive `progress.md` as the sibling of whatever `_harness/` the bridge resolved `PLAN.md` into (vault when synced, device-local otherwise), falling back to the device-local path only when no singleton plan resolved.

### Internal

- **ADR 0020 — Backend-aware harness state.** New decision record (`wiki/decisions/0020-backend-aware-harness-state.md`) with DC-1–DC-6, full Consequences (positive / negative / load-bearing assumptions with re-audit triggers) and Related. [ADR 0018](wiki/decisions/0018-v5-3-storage-cutover.md) gains an `Amendment — 2026-06-19` section recording that DC-1 is reversed while DC-2–DC-7 stand. `Decisions.md` index and `_Sidebar.md` list the new ADR.

- **Tests — backend routing coverage.** `test_harness_memory.py` gains `TestStateBackendRouting` (7 cases: vault dir resolution, read-routes-to-vault-and-wins-over-repo, missing-vault-file → empty, write-routes-to-vault, write→read roundtrip, device-local-backend degrades to repo, `.project-mode=local` overrides a synced backend) and the CLI test now asserts vault-wins-over-stale-repo with `OBSIDIAN_VAULT_SCRIPTS` pinned for deterministic plugin selection. `test_queue_status_lite.py` `HarnessStateDirResolution` gains `test_synced_backend_returns_vault`. Several V5-3-era tests reframed/renamed to assert the `vault_path`-key-alone-does-not-route invariant rather than device-local-only.

## [v5.9.1] — 2026-06-19 — Vault-path hygiene: `check-no-hardcoded-vault-path` gate + convention doc

**PATCH.** A new CI gate prevents absolute vault-path literals from re-entering the codebase, paired with a prose lock of the runtime-resolve convention in `AGENTS.md`. No behavior changes; no new runtime code; gate count 21→22.

### Added

- **`check-no-hardcoded-vault-path` gate — 22nd gate in `check-all.sh` (`034a846`).** Fails if any non-test tracked file embeds an absolute `…/Library/CloudStorage/…` path literal or the retired pre-V5-3 vault root name `…/Obsidian/AgentMemory` as a path component. Shell tilde/variable expansions (`~/Library/CloudStorage`, `$HOME/Library/CloudStorage`) and placeholder notation (`<…>`, `…`) are allowed. Fourteen unit tests in `scripts/test_check_no_hardcoded_vault_path.py`. Gate row added to `wiki/reference/CI-Gates.md`.

### Internal

- **`AGENTS.md` — `§ Vault-path convention — resolve, don't recall` (`306cbf0`).** New subsection under Conventions: canonical resolver (`harness_memory.vault_path()`), `$MEMORY_VAULT_PATH` escape hatch, and the "why" (machine-specific path segments become silently stale across installs). Running-the-checks paragraph updated to list the 22nd gate.

## [v5.9.0] — 2026-06-19 — V5-5: `auto_orchestration` trigger split — orchestration bridge formalized

**MINOR.** The `auto_orchestration` push-surface is split into its three natural owners without changing behavior or raising autonomy. `phase_dispatch()` in `harness_memory` is formalized as the write-capable sibling bridge (non-blocking, graceful-skip, kernel-single-writer, `_BRIDGE_PHASES = frozenset({"post-work", "post-release"})`). `auto_orchestration.py` is declared the sole writer of `_meta/auto-orchestration-state.json`, gate-checked by a static assertion in `verify-v4.sh`. Session-start hooks delegate plan-file discovery through a new `list_plan_files()` public function + `list-plans` CLI verb (V5-6 `state_mode`/Locator-aware). `verify-v4.sh` fractured to kernel-only (A+E+G, 162→85 lines); new `verify-orchestration-briefing.sh` holds the PM-half (B+C+D); session-marker + discover-skills scenarios relocated to `verify-phases.sh`; `check-all.sh` 20→21 gates. Import-direction gate extended to assert no bridge back-edge. PM-half trigger remains kernel-side until the crickets PM-trigger plan ships (gate lifted — `github-projects` exists). Gates: 21/21, CI green across Linux/Mac/Windows.

### Added

- **V5-5 — `phase_dispatch()` orchestration bridge formalized (`a65f901`).** `_BRIDGE_PHASES = frozenset({"post-work", "post-release"})` constant. `phase_dispatch(phase, project_root=None, dry_run=False)` docstring codifies four contract properties: non-blocking (fires-and-returns), graceful-skip (unknown phase → ValueError; vault absent → skip), kernel-single-writer (calls into `auto_orchestration` core; never touches state file directly), write-capable sibling (distinct from the read-only process seam). `ValueError` on unrecognized phase. CLI `phase-dispatch` verb gains `choices=_BRIDGE_PHASES`. 21 contract tests in `test_orchestration_bridge.py`.

- **V5-5 — `list_plan_files(harness_dir)` + `list-plans` CLI verb (`a7e3bee`).** Canonical enumeration of active `PLAN*.md` files: singleton first, then named sorted, excluding conflict copies and archives. `list-plans` CLI verb prints each plan path + `active-binding=<slug>` when `.harness/active-plan` is set; routes through `harness_state_dir()` to respect V5-6 `state_mode`/Locator chain. Session-start hooks (`harness-context-session-start.{sh,ps1}`) delegate plan discovery to `list-plans`; broken `vault-state-path` call dead since V5-3 removed. 13 new tests.

### Internal

- **V5-5 — single-writer invariant declared + gate-checked (`15da187`).** `auto_orchestration.py` module and `save_state()` docstrings explicitly state that `auto_orchestration.py` is the sole writer of `<vault>/_meta/auto-orchestration-state.json`. Static assertion in `verify-v4.sh` segment G: greps `orchestration_*.py` for `^def save_state` and asserts 0 matches.

- **V5-5 — `verify-v4.sh` fractured; `verify-orchestration-briefing.sh` added; `verify-phases.sh` extended (`8e4b170`).** `verify-v4.sh` rewritten to kernel-only (config-seed A + idle-chain E + emit-gating/atomic-state G); 162→85 lines. New `verify-orchestration-briefing.sh` holds the PM-half (briefing signals B, staged-adapt C, nudges D — travels to crickets when the PM-trigger plan ships). Session-marker scenarios and discover-skills chain check relocated to `verify-phases.sh` (Developer-half). `check-all.sh` 20→21 gates.

- **V5-5 — bridge back-edge gate (`0fcdfb0`).** `check-process-seam-import-direction.sh` extended: asserts that no file under `harness/skills/memory/scripts/` imports `harness_memory` (the bridge back-edge). Test files excluded by design. 3 new tests in `ImportDirectionGate`.

- **V5-5 — PM-half hand-off + Orchestration-Bridge reference (`9dffa4d`, `4d3622c`, `365fc00`).** `Auto-Orchestration.md` gains a "Trigger ownership (V5-5)" section (three owners, bridge entry point, PM-half forward-reference status). New `wiki/reference/Orchestration-Bridge.md` (chains, API, CLI, single-writer guarantee, one-way direction). CI-Gates.md + ADR 0011 DC-1 re-audit trigger updated: baked-in orchestration call in the Developer plugin can now be retired via a separate crickets plan.

## [v5.8.0] — 2026-06-19 — V5-7 config-plane: plugin-namespaced vault_path + explicit backend selection

**MINOR.** The kernel no longer owns obsidian-vault's config. `vault_path` moves from the flat kernel key `"vault_path"` to `"plugins.obsidian-vault.vault_path"` — the first plugin-namespaced config key. Existing operators self-heal on first use: `_read_config_vault_path()` detects the legacy flat key, atomically writes both the new key and `storage.backend=vault`, and emits a one-time deprecation warning — no manual migration required. `choose_protocol()` loses its implicit config-based vault-inference step: vault selection is now always explicit. The resolution chain is now: (1) explicit `storage.backend` in config, (2) `$MEMORY_VAULT_PATH` env var set → vault (the env-based escape hatch), (3) else → `device-local`. `harness_memory.vault_path()` public API is unchanged; only what it reads from config changes. Gates: 20/20, CI green across Linux/Mac/Windows.

### Added

- **V5-7 — `harness_memory` plugin-namespaced vault_path read + first-read migration (`6b6dd17`).** `_read_config_vault_path()` reads `plugins.obsidian-vault.vault_path` first; falls back to legacy flat `"vault_path"` key; on first read of an accessible legacy-key vault, atomically writes `plugins.obsidian-vault.vault_path` + `storage.backend=vault` (guards: preserves an existing explicit non-vault backend). Idempotent. `_PLUGIN_VAULT_PATH_KEY` constant. `_reset_warn_state()` resets migration flag for test isolation. 6 new migration tests.

- **V5-7 — `agentm_config` writes plugin-namespaced key + `storage.backend` (`07e3293`).** `--vault-path <dir>` now writes `plugins.obsidian-vault.vault_path` + `storage.backend=vault` instead of flat `vault_path`. `--get vault_path` reads plugin key first with legacy fallback. `--unset vault_path` removes both keys. `_PLUGIN_VAULT_PATH_KEY` constant mirrors `harness_memory`. 3 new backward-compat tests.

- **V5-7 — `choose_protocol()` vault_root parameter removed + implicit config inference eliminated (`31f1ba9`).** `vault_root` removed from `choose_protocol()` signature entirely; the config-based `if vault_root is not None: return _VAULT` implicit step removed. Same removal in `_check_vault_protocol_preview()`. `select_backend()` no longer passes `vault_root` to `choose_protocol()`. Error messages updated to reference `plugins.obsidian-vault.vault_path`. `test_choose_protocol_with_vault_root_is_vault` replaced by `test_choose_protocol_explicit_vault_backend_is_vault`.

### Internal

- **V5-7 — `choose_protocol()` env-var escape hatch fix (`bab0cfd`).** V5-7's implicit-inference removal also accidentally broke `$MEMORY_VAULT_PATH` — the env-based escape hatch. Setting `$MEMORY_VAULT_PATH` IS explicit vault selection (not config inference); re-added as step 2 of the resolution chain in both `choose_protocol()` and `_check_vault_protocol_preview()`. Regression test `test_choose_protocol_memory_vault_path_env_is_vault` added. `verify-phases` vault pass was failing in CI (no config file) but passing locally (config had `storage.backend=vault`).

- **V5-7 — ADR 0013 amendment + docs sweep (`4615f45`, `a864465`).** ADR 0013 DC-4 section documents the step-2 removal. `wiki/reference/Storage-Seam.md` resolution chain updated to 3 steps (restored); `choose_protocol()` signature row updated. `wiki/designs/memory-os-architecture.md` all 3 V5-7 tasks marked shipped. `wiki/reference/Installer-CLI.md` `--vault-path` row updated to describe plugin-namespaced key behavior. `agentm_config.py` backward-compat dispatch lines annotated with inline comments so `grep -rn '"vault_path"'` returns zero primary-path hits.

- **V5-6 post-release wiki sweep (`5ca7251`).** `Home.md` latest-release block, `_Sidebar.md`, and `Completed-Features.md` updated to record the v5.7.0 V5-6 routing-plane de-vaulting release.

## [v5.7.0] — 2026-06-18 — V5-6 routing-plane de-vaulting

**MINOR.** The kernel's routing layer is now backend-agnostic. Three mechanisms that previously built `vault_path() / …` filesystem paths — `resolve_project` / `_vault_projects_dir`, `repo_registry`, and the `state_mode` resolver — now speak `Locator`s to the V5-1 storage seam. On the `obsidian-vault` backend behavior is byte-identical (LC-1). A fresh install with only `device-local` can now host a project, its harness state, and the repo registry without a vault. `state_mode: vault` in device config and `.project-mode` markers is aliased to `state_mode: backend` at read time — no operator migration required (LC-5). Gate extensions enforce the no-Path-leak and one-way-import-direction invariants on the routing layer. `AGENTM_DEVICE_LOCAL_ROOT` env-var override added for test isolation in CI environments without the obsidian-vault plugin. This is the third and final leg of the V5 de-vaulting arc. Gates: 20/20, CI green across Linux/Mac/Windows.

### Added

- **V5-6 — `resolve_project` / `_vault_projects_dir` via seam (`b762987`).** `harness_memory._vault_projects_dir` signature changed from `(vault: Path) -> Path` to `(backend: StorageBackend) -> Locator`; `resolve_project` returns `{slug, project_locator, backend, project_root, layout}` instead of `{slug, vault_path, project_root, layout}`. Callers in `process_seam.py` and `memory_mcp_tools.py` updated to use `project_locator.key`. 10 new tests. LC-7 parallel-run: `VaultBackend` `Locator` resolves to same on-disk path.

- **V5-6 — `repo_registry` onto the seam (`3af7fa6`).** `registry_path(vault) -> Path` replaced by `registry_locator(backend) -> Locator`; `_vault_or_none()` replaced by `_backend_or_none()` (lazy-imports `select_backend()`); all five public functions (`read_registry`, `write_registry`, `register_repo`, `unregister_repo`, `list_repos`) now take `StorageBackend`. `_mutate_registry` drops `vault_mutex` (held internally by `VaultBackend.write()`); `write_registry` adds an explicit content-hash CAS. CLI graceful-skip fires when `select_backend()` raises, not merely when `MEMORY_VAULT_PATH` is unset. 10 new/rewritten tests.

- **V5-6 — `state_mode: vault` → `backend` alias (`b62e378`).** `_read_config_state_mode` and `_read_project_mode` alias `"vault"` → `"backend"` at read time — one-line guard, no rewrite, no operator migration (LC-5). `agentm_config._STATE_MODES` adds `"backend"` as the canonical value; `cmd_set_state_mode` normalizes `"vault"` → `"backend"` at write time. 5 new/updated tests.

- **V5-6 — gate extensions + conformance suite for routing layer (`51b1a32`).** `check-storage-seam-no-path-leak.py` Pass 2 checks 8 named routing functions in `harness_memory.py` and `repo_registry.py` for `pathlib.Path` return annotations. `check-process-seam-import-direction.sh` LC-8 block scans routing files for `storage_vault` imports. `storage_conformance.py` gains `check_routing_repo_registry()` and `ROUTING_CHECKS`; `ConformanceSuite.test_routing_repo_registry()` is now inherited by all conformance subclasses. 8 new tests across three gate test files.

- **V5-6 — ADR 0019 + wiki sweep (`e185946`).** ADR 0019 records the three-leg de-vaulting arc completion, all locked design calls (LC-1/4/5/6/7/8), and load-bearing assumptions with re-audit triggers. `Decisions.md` + `decisions/_Sidebar.md` updated. `device-wide-architecture.md` v1.0 entry finalized. `Storage-Seam.md` routing layer NOTE updated to complete. `Single-Repo-State-Mode.md` updated: `state_mode` value `"vault"` → `"backend"` with backward-compat note.

### Internal

- **CI test isolation — `AGENTM_DEVICE_LOCAL_ROOT` + backend mocks (`4a28b1c`).** `storage_device_local._default_root()` reads `$AGENTM_DEVICE_LOCAL_ROOT` to redirect the device-local root in CI without the vault plugin. 2 subprocess CLI tests updated to use this env var; 2 in-process tests updated to patch `backend_selection.select_backend` directly.

- **CI `verify-phases.sh` vault pass fix (`d055423`).** Set `OBSIDIAN_VAULT_SCRIPTS=$REPO/scripts` so the vault pass can load the kernel `VaultBackend` as a stand-in when the crickets obsidian-vault plugin is absent in CI. `verify-phases: 32/32` (was 31/32).

## [v5.6.0] — 2026-06-18 — V5-7 capability-request matching

**MINOR.** `select_backend()` now accepts a `required: Capabilities | None = None` keyword parameter. When provided, the resolved backend's `.capabilities` must satisfy every `True` flag — a subset check. Mismatch raises `CapabilityMismatchError` (new `StorageSelectionError` subclass) naming the backend protocol and all unsatisfied fields; existing `except StorageSelectionError` catch sites cover it automatically. Operators can pre-flight requirements without code via `doctor --requires <cap1,cap2>`. Zero existing callers updated (default `None` preserves prior behavior). Gates: 20/20, CI green across Linux/Mac/Windows.

### Added

- **V5-7 — `CapabilityMismatchError` (`03aceb5`).** New `StorageSelectionError` subclass in `scripts/backend_selection.py`. Constructor `(protocol, unsatisfied)`; `str(e)` → `"backend '<proto>' does not satisfy required capabilities: <fields>"`. Added to `__all__`.

- **V5-7 — `select_backend(required=)` (`03aceb5`).** Keyword-only `required: Capabilities | None = None` parameter. After resolving the backend, iterates `dataclasses.fields(required)` and raises `CapabilityMismatchError` on any `True` requirement the backend's `.capabilities` doesn't satisfy. 8 new tests in `TestCapabilityMatching`.

- **V5-7 — `doctor --requires` flag (`5a6f94c`).** `_doctor_main()` gains `--requires CAPS` (comma-separated capability names). Unknown field names are validated against `dataclasses.fields(Capabilities)` before any backend is constructed — invalid names print to stderr and exit 1. Valid names print `PASS: backend '<proto>' satisfies required capabilities: <caps>` or `FAIL: <str(e)>`. 5 new tests in `TestDoctorRequires`. `_doctor_main()` also gains injection params (`device_local_root`, `vault_lock_root`, `vault_plugin_scripts`) matching `select_backend()` for test isolation.

### Internal

- **Closed V5-7 deferral marker in `backend_selection.py` module docstring.** Updated the "capability-request matching is V5-7" placeholder to describe the shipped implementation.
- **ADR 0013 amendment.** Subset-only matching rationale (mismatch is always an error, never a silent downgrade/reroute) recorded as an amendment to ADR 0013; `CapabilityMismatchError` as a `StorageSelectionError` subclass is the DC-4 fail-loud extension for capability checks. See `wiki/explanation/decisions/0013-storage-seam-fail-loud-selection.md`.

## [v5.5.0] — 2026-06-18 — V5-3 storage cutover: device-local is canonical

**MINOR.** The kernel no longer contains a built-in vault backend. State (`harness_state_dir`, `read_state_file`, `write_state_file`) is device-local only; `phase_recall` returns `""`; `resolve_documenter_context` returns `None`. The vault is reachable only through the `obsidian-vault` plugin; a config-file `storage.backend=vault` with no vault accessible now raises `StorageBackendNotInstalledError` — never a silent demotion to device-local. Four locked design calls in ADR 0018. Gates: 20/20, CI green across Linux/Mac/Windows.

### Added

- **V5-3 — lock A2 index invariant (`53f96fa`).** `scripts/test_a2_index_invariant.py` — new gate confirming the vector index directory stays under the device-local `~/.cache/agentm/` path, never inside the synced vault root.

- **V5-3 — vault-root rename tooling (`cd78cdd`, `1358e1d`).** `scripts/rename-vault-root.sh` — Phase A: backup + runbook (dry-run only, operator-reviewed); Phase B: string sweep across 36 files in `scripts/` and `harness/`, CHANGELOG, README, wiki. Guarded against zero-match grep exit under `pipefail`. Used to execute the `AgentMemory → Agent` rename on this device.

- **V5-3 — group rename: personal-private → personal (`053217b`).** 3-part lockstep across kernel `_ALWAYS_LOAD_REL`, plugin `vault_probe.py`, and 10 skill scripts. `memory_mcp_tools.py` group default confirmed correct as part of the V5-9 ↔ V5-3 reconciliation fast-follow.

- **V5-3 — delete vault backend from kernel; device-local state only (`8cb2b48`).** `harness_memory.py` loses the `_vault` state routing; `harness_state_dir`, `read_state_file`, `write_state_file` are unconditional `<project_root>/.harness/`. `phase_recall` frozen to return `""` (replaced by V5-9 MCP recall). `resolve_documenter_context` returns `None` (wiki-maintenance dispatches directly).

- **V5-3 — `vault_path()` fail-loud guard (`05e63d3`).** `StorageBackendNotInstalledError` (new `RuntimeError` subclass, LC-6). `vault_path()` raises when `storage.backend=vault` is configured in `.agentm-config.json` but no vault path is accessible — never silent demotion. `$MEMORY_VAULT_PATH` env override remains a graceful-skip per-session escape hatch (DC-5 asymmetry). Six targeted tests in `TestVaultPathGuard`; `TestBackendSelection` regression updated.

- **V5-3 — ADR 0018; HLD v0.9 lifecycle entry; ADR 0010/0013 amendments (`8ce8824`).** ADR 0018 records all seven V5-3 design calls. `device-wide-architecture.md` v0.9 entry added. ADR 0010 Amendment 2026-06-18 closes re-audit trigger #1 (personal-private rename) and notes `resolve_documenter_context` supersession. ADR 0013 two negative bullets annotated `[Resolved in V5-3 / ADR 0018]`.

### Internal

- **Wiki sweep: `Memory-Storage-Seam.md`, `Storage-Seam.md`, `Choose-A-Storage-Backend.md` updated.** Removed "engine cutover is a separate, later step beyond V5-1" language; updated all three to reflect that the cutover shipped in V5-3.

## [v5.4.0] — 2026-06-17 — V5-9 memory MCP server

**MINOR.** The memory engine is now reachable from any MCP host — Claude Code, Cursor, Goose, Claude Desktop — through a single local HTTP daemon. Four snake_case tools (`memory_search`, `memory_recall`, `memory_append`, `memory_forget`), singleton streamable-HTTP broker, static bearer auth, mandatory Origin-validation, and soft-delete. The daemon is writer #2 alongside the CLI, routing all MCP-host writes through the V5-0 `vault_lock` protocol. Five locked design calls in ADR 0017. Gates: 20/20, CI green across Linux/Mac/Windows.

### Added

- **V5-9 Part 1 — server skeleton, liveness probe, contract tests (`cfda095`).** `scripts/memory_mcp_server.py` — FastMCP 3.x singleton daemon binding `127.0.0.1:7821`; `/health` liveness endpoint returning `{"status":"ok"}`; initial contract tests.

- **V5-9 Part 2 — four-tool MCP surface (`ccdcb98`).** `memory_search` (semantic similarity, top_k, cursor pagination), `memory_recall` (budgeted phase-aware bundle, idempotency_key), `memory_append` (soft-write, idempotency dedup), `memory_forget` (soft-delete — status flip + deleted_at, file never unlinked). In-memory FastMCP test client for offline CI.

- **V5-9 Part 3 — writer-routing + vault-source-resolution (`fb4c4ef`).** `memory_recall` resolves its source through V5-1 `backend_selection` — vault-primary when the `vault`/`obsidian-vault` backend is configured, device-local otherwise; never a hardcoded repo allowlist. Fail-loud when backend is misconfigured; the daemon is writer #2 composing the full V5-0 `vault_mutex`/CAS/`atomic_write` stack.

- **V5-9 Part 4 — security layer (`cd8d358`).** `TokenVerifier` for static bearer auth (env-injected `AGENTM_MCP_TOKEN`, never literal in config). ASGI `_OriginValidator` middleware: 403 on any non-loopback `Origin:` header (DNS-rebinding defense). Engine-side path-traversal validation; `_SECURITY_DOC` surface contract.

- **V5-9 Part 5 — stdio shim + host configs (`e895cef`).** `scripts/memory_mcp_stdio_shim.py` — proxies stdio ↔ `http://127.0.0.1:7821/mcp` for Claude Desktop (which requires stdio transport); the daemon stays the sole writer. Host config snippets for Claude Code (`.claude/mcp.json`), Cursor (Settings → MCP), Goose (`~/.config/goose/config.yaml`), Claude Desktop (`claude_desktop_config.json`).

- **V5-9 Part 6 — operations & docs (`c9d79dc`).** `scripts/memory_mcp_doctor.py` — four health checks (`liveness`, `token_env`, `origin_guard`, `index_root_safe`), 13 unit tests, stdlib-only. `scripts/com.agentm.memory-mcp-server.plist` — launchd template (`ProcessType Standard`, `RunAtLoad`, `KeepAlive`, stderr-only log; stdout unredirected for stdio purity). `install.sh --mcp-server <project>` generates a filled-in plist and prints the three bootstrap commands. `harness/skills/doctor.md` + `adapters/claude-code/skills/doctor/SKILL.md` extended with check `4e` (graceful-skip pre-V5-9; default mode = liveness+token_env; `--live` adds origin_guard+index_root_safe). ADR 0017 (five locked calls: DC-1 singleton HTTP broker, DC-2 four snake_case tools, DC-3 soft-delete, DC-4 loopback-first remote deferred, DC-5 FastMCP >=3,<4). `wiki/how-to/Stand-Up-Memory-MCP-Server.md` and `wiki/reference/Memory-MCP-Tools.md` published.

### Internal

- **Fix: `check-lib-parity` / `sync-lib` exclude `__pycache__` from checksums (`67bf8a7`).** CI runners don't generate `.pyc` files; stale `__pycache__` entries in `.checksums.txt` caused the parity gate to fail cross-platform. Both scripts updated; checksums regenerated.
- **Fix: normalize lock-root path to POSIX before sync-marker check (`1e11426`).** `check_index_root_safe` used `str(Path.resolve())` which produces backslash paths on Windows; forward-slash markers (`/CloudStorage/` etc.) never matched. Changed to `Path.resolve().as_posix()`.

## [v5.3.0] — 2026-06-16 — V5-11 team-coordinator persona

**MINOR.** The first *composed* persona lands: the `team-coordinator` reads the vault, computes answers, and hands the operator decision-ready recommendations — where the team stands, which plans are safe to run together, and what order to merge in. Advisory only; zero execution authority. Built on the V5-12 persona tier substrate (shipped in v5.2.0). All answers are computed by plain code, never guessed; the model narrates on top. Gates: 20/20, CI green across Linux/Mac/Windows.

### Added

- **V5-11 — team-coordinator persona: standup, readiness, merge-order (`7966ac3`).** `personas/team-coordinator.md` (`kind: persona`, `requires: [queue_status_lite]`, `enhances: [developer-workflows, github-projects]`) — the first composed persona, built on the V5-12 substrate. Four new stdlib scripts, all read-only and fixture-tested with no live vault required:
  - `scripts/plan_graph.py` — shared map engine. Reads `_harness/` (active plans) and `queued-plans/` (staged plans), parses optional YAML frontmatter (`depends_on:`, `touches:`) from each plan, and returns a `list[PlanInfo]` consumed by the three capability scripts below. 17 unit tests; fixture tree at `scripts/fixtures/plan_graph/`.
  - `scripts/standup.py` — derives `worker_state` per active plan: `building` (tasks remain, recent progress), `mergeable` (all tasks done, not yet merged), or `idle` (no progress-log touch in >2h). `IDLE_THRESHOLD_HOURS = 2` is a named constant. 13 unit tests.
  - `scripts/readiness.py` — two-stage dispatch pre-flight: (1) dependency readiness — a queued plan is ready when every `depends_on` entry has `Status: done`; (2) file-overlap safe-to-run-together check using `fnmatch` on `touches:` glob lists. Plans without a `touches:` field are loudly degraded (never silently included, never guessed). 9 unit tests covering all four required cases.
  - `scripts/merge_order.py` — Kahn topological sort by `depends_on` edges, smallest-git-diff-stat tie-break among topologically equivalent plans, alphabetical fallback when git is unavailable. Cycle detection raises `ValueError`. 12 unit tests; determinism verified (same input → same output).

### Internal

- Wiki sweep: `persona-tier-schema.md`, `persona-tier.md`, `Completed-Features.md`, `Named-Plans.md` updated to reflect V5-11 (`8d021a1`).

## [v5.2.0] — 2026-06-16 — V5-8 capability-discovery resolver + V5-12 persona tier

**MINOR.** Two architectural additions land together. **V5-8** ships the `enhances:` capability-discovery resolver — the substrate-level soft-composition runtime that lets any agentm primitive declare optional capabilities it wants without hard-depending on them, degrading cleanly when absent. **V5-12** ships the persona tier, the third architectural classification ADR 0011's substrate/plugin binary had no slot for: a standing concern that composes capabilities it does not own, anchored on the neutral substrate, with hard deps (`requires:`) restricted to substrate-native primitives only. Both are additive — zero behavior change for existing installs; both are enforced by static gates (`check-capability-resolver-one-way` for V5-8, `check-personas` for V5-12). Gates: 20/20, CI green across Linux/Mac/Windows.

### Added

- **V5-8 — capability-discovery resolver: the `enhances:` runtime (ADR 0015, `e7b9139`).** `scripts/capability_resolver.py` implements the soft-composition runtime: a capability-keyed, graceful-degrade resolver that answers "is capability X present on this host?" by reading installed-plugin manifests as data (never importing plugin code). A primitive declares `enhances: [capability-name]` in its manifest; the resolver answers present/absent at load time and the caller degrades cleanly when absent. `enhances ∩ requires = ∅` is a hard invariant (no capability appears in both). `scripts/capability_version_match.py` implements the single-range version check used by the resolver. The `check-capability-resolver-one-way` gate asserts the resolver never imports plugin code — reads manifests as JSON only (ADR 0015 DC-5). Paired with the `agentm-capability` shell helper for host-adapter probing.
- **V5-12 — persona tier: `kind: persona` primitive, `check-personas` gate, rememberer (ADR 0016, `1234663`).** Names the missing third architectural tier — a *persona* is a standing concern that composes capabilities it does not own (arbitrating among them when it composes ≥2), is anchored on the neutral substrate, and whose hard deps (`requires:`) are restricted to substrate-native primitives only. Zero new runtime: the tier maps entirely onto shipped infra (positive-match `kind:` dispatch both hosts already tolerate, the V5-8 `enhances:` resolver for soft composition, the on-demand load path). Three deliverables: (1) `personas/` directory as the persona home; (2) `personas/rememberer.md` — the degenerate first persona (`requires: []`, `enhances: []`), naming the memory engine as the standing concern agentm already shipped; (3) `scripts/check-personas.py` — static gate asserting `requires ⊆ substrate-native` + no-always-load for every file under `personas/`, wired into `check-all.sh` and all three CI workflows. 11 unit tests including both required reject cases: (a) non-substrate `requires:` entry, (b) `always_load: true`. The chief-of-staff (the first *real* persona, V5-11) is gated behind this substrate and ships in a separate plan.

### Internal

- **ADR 0015 — Capability discovery: the `enhances:` runtime.** Decision record for the soft-composition vocabulary and the one-way resolver seam (`wiki/decisions/0015-capability-discovery.md`, `e7b9139`).
- **ADR 0016 — The persona tier: a third classification above the substrate/plugin binary.** Decision record for the persona tier, the inverted-dependency-direction test, the `check-personas` gate contract, and the honest residual (null hypothesis litigated) (`wiki/decisions/0016-persona-tier.md`, `1c42283` through `6354cdb`).
- **Wiki: `persona-tier-schema` reference page + `Soft-Composition` cross-reference.** `wiki/reference/persona-tier-schema.md` documents the `kind: persona` manifest fields and `check-personas` gate contract (pending → implemented). `wiki/explanation/Soft-Composition.md` adds the persona + `enhances:` note. `wiki/_Sidebar.md` wired (`1234663`).

### Cross-references

- [agentm v5.1.0](https://github.com/alexherrero/agentm/releases/tag/v5.1.0) — the prior release (V5-2 kernel-thinning + #46 token-efficiency).
- ADR [0015 (capability-discovery resolver)](https://github.com/alexherrero/agentm/blob/main/wiki/decisions/0015-capability-discovery.md).
- ADR [0016 (persona tier)](https://github.com/alexherrero/agentm/blob/main/wiki/decisions/0016-persona-tier.md).

## [v5.1.0] — 2026-06-14 — V5-2 parallel-run + token-efficiency: recall budget and heat-based floor curation

**MINOR.** Two threads land together. The **V5-2 kernel-thinning parallel-run** continues extracting Obsidian/Drive machinery from the storage-agnostic engine into crickets plugins: the GDrive conflict sweep moved to the `obsidian-vault` plugin (task 2), and backend-selection now discovers and fails loud on a misconfigured `obsidian-vault` backend (task 3). The **#46 token-efficiency batch (Hardening I)** lands two of its three floor-shrink targets: a configurable per-recall token budget (Part A task 3) that keeps recall injection ≤ N tokens highest-salience-first, and a heat-based always-load curation system (Part G) that demotes sustained-cold entries and promotes sustained-hot ones — principled floor shrinkage instead of silent alphabetic truncation. Gates: 18/18, CI green across Linux/Mac/Windows.

### Added

- **Per-recall token budget — `recall_token_budget` (#46 Part A task 3).** `recall.py` now enforces a configurable token budget (`DEFAULT_TOKEN_BUDGET = 20_000`, ~10% of a 200k Claude context) on both `session_start()` and `prompt_submit()`. Salience-ordered truncation (highest `combined = sim × 0.85 + keyword × 0.05` retained first) so budget enforcement never degrades recall quality. A visible `> [!NOTE] recall truncated: N entries omitted …` marker fires on any truncation — never silent. Configurable via `--token-budget` CLI arg → `RECALL_TOKEN_BUDGET` env → default; `0 = unlimited`. Token estimation is chars/4 (no new deps). Public API frozen (DC-7) — callers without the `token_budget=` kwarg get the default silently (`b91894a`).
- **Heat-based always-load curation — `heat_policy.py` (#46 Part G).** The always-load set now earns its place: `record_hit()` logs per-entry recall hits into a `<vault>/.heat.json` sidecar (`total_sessions` + per-entry `{hits, hit_sessions, last_hit}`); `run_policy()` demotes always-load entries with zero hits after ≥ 10 sessions and promotes on-demand entries with ≥ 3 hits across ≥ 2 sessions (spike guard) — conservative thresholds. A `heat_pin: true` frontmatter field marks any entry never-demote regardless of heat; `MIN_ALWAYS_LOAD = 5` ensures a working minimum is never undercut. Every demotion and promotion emits a visible DEMOTE/PROMOTE/PIN marker on stderr. The `heat-pin <slug>` subcommand pins and restores a currently-demoted entry. `save.py` gains `heat_pin` in `FRONTMATTER_FIELD_ORDER` to keep vault_lint clean. Part G hooks into the instrumentation point built by Part A task 3 (`92ad252`).
- **ADR 0014 — Tier-2 gate: don't fork the loop (#46 Part E).** Decision record for the Tier-2 SDK fork-gate constraint archived in `wiki/decisions/0014-tier-2-sdk-fork-gate.md` (`fe6ec37`).

### Changed

- **The GDrive vault conflict sweep left the kernel for the crickets `obsidian-vault` plugin (V5-2 task 2).** `detect_conflict_files`, `_infer_conflict_base_path`, and `default_lost_and_found_root` — plus the `conflict-merger-session-start` SessionStart hook — were removed from `harness_memory.py` / `harness/hooks/` and re-homed beside the backend they serve (the plugin's `scripts/vault_conflicts.py` + hook). The classifier `_conflict_family` **stays kernel-side** because `queue_status_lite.py` (the named-plan dashboard) consumes it; the plugin **imports** it rather than vendoring a copy (LC-3 — the plugin runs only under a present engine). The sweep's tests moved with it; a guard test (`test_harness_memory_named_plans.py::ReHomedSweepStaysGone`) asserts the three symbols stay absent from `harness_memory` so a re-introduction can't drift back in unnoticed. `vault_probe.py` + `migrate-harness-to-vault.{sh,ps1}` deliberately **stay** (wired into live install/onboarding surfaces; their move defers to V5-7). Paired with crickets `obsidian-vault` `0.1.0` (`dffb5f4`).
- **Backend-selection now discovers the `obsidian-vault` crickets plugin (V5-2 task 3).** When the configured backend is `obsidian-vault`, `backend_selection.py` probes for the crickets plugin before construction and fails loud (install-error message, non-zero exit) if absent — matching the fail-loud contract of V5-1 part 5. The `--doctor` preview now shows plugin-installed state for `obsidian-vault` alongside the existing `[OK]`/`[WARN]`/`[FAIL]` lines, byte-identical to the live refusal. Loader hardened to handle import failures gracefully (`dbcf739`, `a20eeb6`).

### Internal

- **Recall prefix-stability + floor-dedup guards (#46 Part A tasks 1–2).** Six-case guard suite (`test_recall_prefix_stability.py`) locks the properties found already-satisfied in the bugfix analysis: byte-identical back-to-back `session_start()` runs, a clock-skew test (frozen `time` module shim catching cross-session stamp divergence), deterministic `sorted()` entry order, no `\d{4,}`/`HH:MM` clock pattern in output, and a non-vacuous `prompt_submit()` dedup test proving always-load entries are never re-emitted per-turn (`b18e729`).
- **M3 Tier-types V6-reservation note + ML2 plan-name parity vectors (audit follow-up).** `storage_seam.py` gained a V6-reservation note; `test_resolve_active_plan.py` gained ML2 plan-name parity vectors (`d4fca3a`).
- **CLAUDE.md @AGENTS.md import (token-efficiency floor-trim).** Project CLAUDE.md imports `@AGENTS.md` so universal agent instructions are included once at the project level (`d13b71c`).

### Cross-references

- [crickets `obsidian-vault` 0.1.0](https://github.com/alexherrero/crickets) — paired with V5-2 task 2 (conflict sweep re-homed to the plugin).
- [agentm v5.0.1](https://github.com/alexherrero/agentm/releases/tag/v5.0.1) — the prior release (V5-1 follow-on: non-UTF-8 config readers).
- ADR [0014 (Tier-2 SDK fork gate)](https://github.com/alexherrero/agentm/blob/main/wiki/decisions/0014-tier-2-sdk-fork-gate.md).

## [v5.0.1] — 2026-06-13 — V5-1 follow-on: non-UTF-8 config readers honor their contract

**PATCH.** A correctness follow-on to the **V5-1 storage seam** shipped in v5.0.0. The four config readers each document a contract — `backend_selection.py::_configured_backend` is **fail-loud** (raise `StorageSelectionError` when the config is present but unparseable), and the three best-effort readers (`harness_memory.py::_read_config_vault_path`, `harness_memory.py::_read_config_state_mode`, `agentm_config.py::_read_config`) are **graceful-skip** (return `None`). Each guarded only `(json.JSONDecodeError, OSError)`. A non-UTF-8 config file makes `Path.read_text(encoding="utf-8")` raise `UnicodeDecodeError` — a `ValueError`, which is neither an `OSError` nor a `json.JSONDecodeError` — so it leaked past every guard and crashed the caller instead of honoring the documented contract. Surfaced by an adversarial review of the V5-1 part-5 selection code immediately after the v5.0.0 tag. Solo harness PATCH; no crickets pairing.

### Fixed

- **All four config readers now also catch `UnicodeDecodeError`** (`50cde80`). The fail-loud resolver raises `StorageSelectionError` (its documented contract) on a non-UTF-8 config rather than leaking the decode error; the three graceful readers return `None` (their documented contract). The realistic trigger is a Windows editor's UTF-16/BOM (`\xff\xfe…`) "Save As" on an otherwise-valid config. Five regression tests pin the behavior across all four sites — including the `doctor` storage preview reporting `[FAIL]` rather than crashing. The fix is the minimal contract-honoring widening (catch the decode error at the same site, route it to the docstring's promised behavior), not a broad `except Exception`; the fail-loud path stays loud — a corrupt config still refuses startup, never a silent demotion to `device-local`, per [ADR 0013](https://github.com/alexherrero/agentm/blob/main/wiki/decisions/0013-storage-seam-fail-loud-selection.md).

## [v5.0.0] — 2026-06-13 — Seams: pluggable storage, vault-write safety, and the dev-loop split completes

**MAJOR.** V5 is the *seam* release: the load-bearing parts of the harness — how memory data is stored, how concurrent vault writes stay safe, how the memory layer talks to the process layer, and how plans are addressed — are now formal, tested boundaries instead of inlined assumptions. The headline is the **memory↔storage seam (V5-1, five parts)**: a backend-agnostic storage contract with a named-protocol registry, a built-in `device-local` backend, a backend-agnostic conformance suite, the existing vault write-path wrapped behind the seam (moving **zero** data — the never-orphan invariant), and **fail-loud backend selection** that refuses a memory operation with an "install the plugin" error rather than silently demoting to `device-local`. It also lands the **V5-0 vault-write protocol** (a real cross-process lock + content-hash CAS + atomic writes + a broadened conflict-janitor), the **V5-4 process seam** (a one-way memory↔process read-only edge with an import-direction gate), **V5-10 named plans** (multiple concurrently-addressable plans + a read-only queue dashboard), and the **seven-section documentation convergence**. The **MAJOR** bump is earned by the breaking half: the dev-loop primitives and the `migrate-to-diataxis` / `documenter` / `diataxis-author` surfaces that used to be vendored in the harness are **retired** — they are now provided by [crickets](https://github.com/alexherrero/crickets) plugins, which the harness dispatches via graceful-skip. Single-repo release; many of these commits were already on `main` untagged — this is the bundled tag.

### Breaking

- **The dev-loop primitives are no longer vendored in the harness — they come from crickets plugins** (`3d2b328`). The harness used to carry its own copy of the `/setup`·`/plan`·`/work`·`/review`·`/release`·`/bugfix` loop primitives; those are retired here and provided by crickets's `developer-workflows` plugin instead. The harness dispatches them via the `wiki-maintenance` / capability-probe **graceful-skip** path — present → dispatch, absent → silently skip — so a standalone harness with no crickets installed still runs, just without the loop primitives.
- **`migrate-to-diataxis` and the vendored `documenter` / `diataxis-author` copies are retired → crickets** (`5e85b6b`, `8a3b238`, `0b831ab`, `53ec0b9`, `0344ad8`). The harness no longer ships the documentation sub-agents; it dispatches the crickets-provided `documenter` through the same graceful-skip probe. A retire-invariant gate (`0344ad8`) keeps the duplicate copy from creeping back. See [ADR 0011 — V5 unbundling](https://github.com/alexherrero/agentm/blob/main/wiki/decisions/0011-v5-unbundling.md) (`8f4adb6`) for the split rationale.

### Added

- **Memory↔storage seam — a pluggable, backend-agnostic storage contract (V5-1).** A verb-by-verb `StorageBackend` protocol with a `Locator` abstraction (no raw `Path` leaks across the seam, enforced by a gate), a **named-protocol `BackendRegistry`** (`protocol → backend`, resolve-as-absent), and a three-tier source/derived contract (`Tier` / `TierLayout` + named V6 ops) (`5598e95`, `21312ba`, `a287478`). Ships a built-in **`device-local` backend** over the seam with a named conflict-strategy slot and a scope guard (`214979e`, `eb48524`, `745b86d`). The existing vault write path is **wrapped behind the seam moving zero data** — the never-orphan invariant, proven by test (`05a7ce1`, `f870aab`, `4b9c397`, `298861d`).
- **Fail-loud backend selection + a `doctor` storage preview (V5-1 part 5).** A `storage.backend` config key (set via `agentm_config.py --storage-backend`) and a selection resolver (explicit `storage.backend` → existing `vault_path` → fresh `device-local`); if the named backend's plugin is **not** installed, the engine **refuses with an "install the plugin" error and never silently demotes** to `device-local` (`208bca5`, `c9ec6e3`, `bd3a9e6`, `53a7982`). A read-only `doctor` storage check (`python3 scripts/backend_selection.py --doctor`) previews the selected backend and its plugin-installed state **before** any memory operation could refuse — `[OK]`/`[WARN]`/`[FAIL]` with the `[FAIL]` line byte-identical to the engine's live refusal, and *without* constructing a backend (construction would `mkdir` the root) (`8264bb4`). See [ADR 0013 — storage-seam fail-loud selection](https://github.com/alexherrero/agentm/blob/main/wiki/decisions/0013-storage-seam-fail-loud-selection.md) and the [Choose a storage backend](https://github.com/alexherrero/agentm/wiki/Choose-A-Storage-Backend) how-to.
- **Vault-write protocol — safe concurrent vault writes (V5-0).** A real cross-process `vault_lock` (the Phase-0 protocol), content-hash CAS for replace-style writes routed through `atomic_write`, the `harness_memory` state writers + `/memory save`+`evolve` routed through a `vault_mutex`, and a conflict-janitor broadened to four marker families plus the Windows DriveFS `lost_and_found` (`0d6d2fd`, `64d24b4`, `a39019e`, `04ae3f3`, `6362bab`). See [ADR 0012 — vault-write protocol](https://github.com/alexherrero/agentm/blob/main/wiki/decisions/0012-vault-write-protocol.md) (`0e04117`).
- **Named plans — multiple concurrently-addressable plans (V5-10).** A named-plan resolver contract + `resolve_active_plan` binding with a loud-error guard, a read-only `queue_status_lite` multi-plan dashboard, a named-plan naming gate, named-plan-aware session-start hooks + `doctor`, and a `resolve-active-plan` CLI verb bridging the crickets writers (`dfdcf8f`, `7f6baa5`, `0f27f8f`, `454108c`, `d76a2d1`, `543bb3c`). Includes a worktree slug-resolution safety probe (`da25ca5`).
- **Process seam — a one-way memory↔process edge (V5-4).** A read-only `process_seam` memory↔process client seam, with a `check-process-seam-import-direction` gate enforcing the one-way dependency (`3358c6b`, `6a4d5b5`, `04ae267`).

### Changed

- **Documentation converged on the seven-section taxonomy.** The wiki was rebuilt to a seven-section frame (Architecture manifest + six pillar overview pages, How-to / Reference / Explanation / Decisions), `check-wiki.py` swapped to the seven-section linter, `documentation.md` and `templates/wiki/` reshaped to match, and ADR 0004 amended to the new taxonomy (`265420e`, `3488346`, `a0f3328`, `1cefcb3`, `9e5322c`, `16842b6`, `51f77a9`, `deb6ef4`, `b0f3747`).
- **Documenter dispatch rewired to crickets graceful-skip** across the harness and the Claude Code adapter surfaces (`0b831ab`, `53ec0b9`) — present → dispatch the crickets `documenter`, absent → skip silently, no hard dependency.
- **`/work` runs the full task list autonomously**, gated by a per-task safety pre-check, rather than one task per session (`51574f4`).

### Internal

- **Backend-agnostic conformance suite (V5-1).** A storage-backend conformance suite + a `derived_maintenance` accessor, run against `device-local` on the cross-OS gate, with negative + positive fixtures proving the suite actually bites (`da01599`, `ef16985`, `36d0102`, `d0712c3`).
- **Storage-seam hardening (audit follow-ups).** Reject backslash + NUL keys in `normalize_key` (a Windows traversal hole), a mutex + bounded retry on the registry CAS, a Windows DriveFS `lost_and_found` probe, and a documented "mtime is a hint, not a total order on synced backends" clarification (`380d171`, `cc2887f`, `394ee84`, `fbf4459`, `b2442c0`).
- **Gate + CI breadth.** `check-workflow-parity` mirroring the dogfood workflows, a wiki-sync dupe-guard fix applied to the template twin + reserved-special-files exclusion, an agentm pre-push PII guardrail template, and a Windows CI regression fix from the V5-0 + V5-10 push (`68039c4`, `91f813c`, `6ba8ba9`, `ce041b3`, `6230b34`).
- **Design-doc + memory plumbing.** Adopted the Agent-M architecture design docs from crickets, codified the design-doc authoring conventions, added an `external/<slug>` vault directory, and taught the documenter-context to read the relocated `_global` wiki-style store (`936e391`, `9bec04e`, `d650d9e`, `90262d0`, `8776f03`).

### Cross-references

- [crickets](https://github.com/alexherrero/crickets) — the sibling toolkit that now **provides** the dev-loop primitives, `documenter`, and `diataxis-author` the harness retired here (via the `developer-workflows` and `wiki-maintenance` plugins).
- [agentm v4.15.0](https://github.com/alexherrero/agentm/releases/tag/v4.15.0) — the prior release (Hardening I: single-repo first-class + e2e breadth), whose tested single-repo / device-local path this builds the pluggable storage seam on top of.
- ADRs [0011 (V5 unbundling)](https://github.com/alexherrero/agentm/blob/main/wiki/decisions/0011-v5-unbundling.md) · [0012 (vault-write protocol)](https://github.com/alexherrero/agentm/blob/main/wiki/decisions/0012-vault-write-protocol.md) · [0013 (storage-seam fail-loud selection)](https://github.com/alexherrero/agentm/blob/main/wiki/decisions/0013-storage-seam-fail-loud-selection.md).

## [v4.15.0] — 2026-06-03 — Hardening I: single-repo first-class + e2e breadth

**MINOR.** The harness now runs on a **single repo with zero Obsidian / Google Drive / vault dependency** — opt in explicitly with `install.sh --local-state` (or `agentm_config.py --state-mode local`), and harness state lives in `<repo>/.harness/` instead of a MemoryVault. This is the first front of **Hardening I** ("know when we break things"): single-repo mode is now a *tested, first-class* path, the cross-repo (crickets) install residue that produced the "3 fragments skipped" boot error is gone, and a first substantial batch of end-to-end tests exercises whole phases, every memory hook, and the memory engine round-trip in **both** state modes — so a regression in any of these surfaces before release. Single-repo release.

### Added

- **First-class repo-local (vault-less) state — `install.sh --local-state` / `install.ps1 -LocalState`.** Writes `"state_mode": "local"` to the on-host `.agentm-config.json` and skips vault wiring; every phase write then lands in `<repo>/.harness/` with no `ValueError`. A single-repo adopter needs no MemoryVault at all. A per-repo `<repo>/.harness/.project-mode` marker overrides the device default for one repo.
- **`agentm_config.py --state-mode {local,vault}`.** The post-install / `/setup` way to flip the device-level run mode without re-running the installer (mirrors `--vault-path`: idempotent, validated, preserves other config fields).

### Changed

- **State-mode configuration is on-host only (DC-8).** `.agentm-config.json` is the single source of truth for *how agentm runs* (a new `"state_mode"` key, preserved across re-persist like `vault_path`); the vault holds *data*, never configuration. Resolution is two on-host layers — repo-local `.project-mode` marker → device `state_mode` — and the mode is **never** inferred from a missing `vault_path` (an absent vault is ambiguous between never-configured and transiently-unreachable; inferring would split-brain state). Removed the former in-vault `.project-mode` marker and a dead `harness_state_mode` field from the vault repo-registry; `migrate-harness-to-vault.sh`/`.ps1` now write the repo-local marker.
- **Retired the `install-state-sync` SessionStart hook.** In standalone agentm its only unique job — re-merging changed `settings.json` hook registrations — is redundant (symlinked customizations + clone-resolved scripts auto-track a `git pull`; `agentm-update` covers registration refresh), couldn't unregister removed hooks, and was the live source of the *"N fragment(s) skipped"* boot error (3 dead crickets control hooks). Retiring it also ripped out the biggest remaining chunk of crickets install coupling (`--crickets` detection across `install.sh`/`.ps1` + `install_state.py` + `install_symlinks.py`; the 4 memory hooks' source-mode fallback repointed crickets→agentm).

### Internal

- **End-to-end test breadth (the regression net).** Three new hermetic e2e scripts mirroring the `verify-v4.sh` PASS/FAIL skeleton: `verify-phases.sh` drives the deterministic seams of `/setup→/plan→/work→/release` against a fixture project **twice — vault-resident and repo-local**; `verify-memory-roundtrip.sh` round-trips the memory engine (embed→save→recall→reflect→vec-index→lint) on a fixture vault. Plus three subprocess **hook-firing** tests (`memory-recall-session-start`, `memory-reflect-stop`, `memory-reflect-idle`) proving each hook actually fires on a synthetic event + graceful-skips when inputs are absent + never blocks (the V4 #39 "lands but silently no-ops" class of bug). All wired into `scripts/check-all.sh` (now 11 gates) + the Linux/Mac CI workflows (Windows skips bash-native, per convention).

### Cross-references

- [agentm v4.14.0](https://github.com/alexherrero/agentm/releases/tag/v4.14.0) — the prior release (decouple from crickets), whose single-repo *decouple* + `verify-v4.sh`/`check-all.sh` down-payment this builds the *first-class tested mode* + broader e2e on top of.

## [v4.14.0] — 2026-06-02 — Decouple from crickets: agentm stands alone

**MINOR.** agentm and crickets are now fully decoupled at install time. crickets v3.0 retired its bespoke per-host installer (`install.sh`/`.ps1` + `lib/install/`) in favor of **native Claude Code / Antigravity plugins**; this is agentm's side of that clean break. The harness is now a self-contained standalone install — it no longer clones or bootstraps crickets, and owns its install library outright. crickets remains the optional toolkit, installed separately via its native plugin path. Single-repo release. Also folds in the V4 verification battery (internal tooling).

### Changed

- **The installer no longer bootstraps crickets.** `install.sh` / `install.ps1` dropped the crickets-sibling auto-detect + clone + invoke-`crickets/install.sh` block (that installer no longer exists). Operators install crickets separately — its one-line `bootstrap.sh` or `claude plugin install`. The two repos are decoupled at install time.
- **`sync-lib.sh` is now local-only.** It was the cross-repo byte-sync that kept `agentm/lib/install/` and `crickets/lib/install/` byte-identical; crickets no longer ships `lib/install/`, so the script now just regenerates agentm's own `lib/install/.checksums.txt` (+ a `--verify` mode), with no `../crickets/` targeting. `check-lib-parity` de-staled to match — **agentm owns `lib/install/` outright**. Closes ADR 0006's lib-sync re-audit trigger.
- **Docs point crickets install at native plugins.** The README "install Crickets" step uses crickets's native one-line installer; `/release`'s `ship-release` graceful-skip message points at `crickets/bootstrap.sh` / `claude plugin marketplace add` instead of the deleted `crickets/install.sh`.

### Internal

- **V4 verification battery.** New `scripts/verify-v4.sh` — a one-shot scratch-vault integration check for the #23 auto-orchestration push surface (29 checks; hermetic; self-cleaning; never touches a real vault). New `scripts/check-all.sh` — the standard local gate battery (unit suite + every `check-*` gate + verify-v4 in one command, with a PASS/FAIL table). verify-v4 wired into the Linux + Mac CI workflows; `wiki/reference/CI-Gates.md` + `AGENTS.md` document the battery + how to grow it.

### Cross-references

- **crickets v3.0 #40** (native-plugins consolidation) — the cross-repo effort this is agentm's half of; paired with crickets's clean break (deleting its bespoke install machinery in favor of native plugins).
- [agentm v4.13.1](https://github.com/alexherrero/agentm/releases/tag/v4.13.1) — the prior release (auto-orchestration fast-follows).

## [v4.13.1] — 2026-06-01 — Auto-orchestration fast-follows: concurrency + the adapt loop

**PATCH.** Three fast-follows on the v4.13.0 push-surface, found in a post-release sweep before moving on.

### Fixed

- **Atomic state writes (concurrency-safety).** `auto_orchestration.save_state` wrote the **shared** `<vault>/_meta/auto-orchestration-state.json` with a plain `write_text` — but that file is read+written by every repo/agent against the one shared vault, and the operator runs concurrent agents. A reader could catch a torn/partial write and degrade to an empty shape (a lost cooldown → a spurious re-fire). Now writes to a pid-unique temp + `os.replace` (atomic on POSIX **and** Windows), so a reader only ever sees a complete file. Verified under 4 concurrent writers — 0 torn reads.

### Added

- **The idle chain's staged adapt candidates now surface.** The idle chain stages Pass-1 candidates under `_meta/skill-discovery-cache/adapt-state/` — but nothing told you they were there, so the discover→adapt→**evaluate** loop staged into a black hole (Pass-2 never prompted). The SessionStart briefing now reports *"N skill candidate(s) staged for adapt-evaluation (`/memory adapt-skills`)"*, counting only candidates without a watchlist entry yet so the count **clears as you evaluate**. The `adapt-evaluator` contract now deletes the consumed Pass-1 scratch JSON after judging — so LOW-dropped candidates clear too, and a latent "re-judge every LOW candidate each run" inefficiency is closed.

### Changed

- **`memory-reflect-stop/hook.md`** brought in line with the `.sh`. The doc still described the long-superseded task-3 "mines but does NOT save" scaffold; in reality the hook has *routed* (HIGH → canonical paths, MEDIUM/LOW/ideas → `_inbox/`) since task 5, and gained the V4 #23 `.reflected` phase-dispatch **dedup guard** so a session is never reflected twice.

### Internal

- +6 tests (auto-orchestration 27 · briefing 36); full suite **556** green, 4-OS. Adversarial review: NO ISSUES FOUND (empirically verified the atomic write under 4 concurrent writers + the staged-count shifted-state snapshot transitions).

### Cross-references

- Fast-follows on [agentm v4.13.0](https://github.com/alexherrero/agentm/releases/tag/v4.13.0) — V4 #23 auto-orchestration.

## [v4.13.0] — 2026-06-01 — Auto-orchestration: the memory push-surface (V4 #23)

**MINOR.** The AgentM memory skills were a *pull surface* — you had to remember to run recall, reflect, discover-skills, adapt-skills, and the watchlist by hand, so pending work piled up unseen. This release turns them into a **push surface**: open a session and the system already tells you what needs attention; during idle time it runs the right memory chains itself; at phase boundaries it reflects and refreshes. It never blocks a session, never nags (cooldowns plus a "only when state shifted since you last saw it" guard), and never acts on its own — every adoption or write stays operator-gated. Hook/file-based and cross-host (DC-1), entirely **agentm-native** (DC-3: crickets carries zero AgentM crossover now). This is the last open V4 item — the foundation finish. Single-repo release. The default thresholds and cooldowns are a first guess; the real-use dogfood on the operator's own vault calibrates them.

### Added

- **SessionStart pending-state briefing** (`orchestration_briefing.py`) — on session boot, a tight 1–3 line block surfaces what's piled up: `_inbox/` over threshold · `_skill-watchlist/` HIGH-pending count · `_idea-incubator/` entries in research · GC-eligible idea-ledger items. Emits ONLY when something shifted since it was last shown AND the cooldown allows; appended to the `memory-recall-session-start` hook after the always-load recall; non-blocking (any error → empty briefing, never wedges boot).
- **Idle-time orchestration chain** (`orchestration_idle.py`) — the `memory-reflect-idle` hook fires a cooldown-gated chain during idle time: reflect-corpus (≤5 unseen sessions, `--max-batches 1`) → discover-skills (cadence-checked) → adapt-skills Pass-1 (stages ≤3 candidates, `--limit 3`). Run **detached** so it never blocks or gets killed at the hook's 30s SessionStart timeout; results surface the next session via the briefing. Pass-2 (adapt-evaluator) stays operator-gated — a hook can't dispatch a sub-agent.
- **Phase-integration auto-dispatch** (`orchestration_phase.py`, via a new `harness_memory.py phase-dispatch` subcommand) — after `/work` commits a task, reflect the just-finished session (**dedup-guarded** against the `memory-reflect-stop` hook via the `.reflected` session marker, so a transcript is never reflected twice; works cross-host including Antigravity, which has no Stop hook); after `/release`, refresh the skill surfaces (index-skills + discover-skills, cadence-checked). Wired into the `/work` and `/release` phase specs (§9b), config-gated and non-blocking — extends, doesn't duplicate, the V4 #8 phase context dispatcher.
- **Two SessionStart nudges**, riding the same briefing block (one consolidated notice): **promote-suggest** — an idea surfaced ≥3× in the Ideas ledger → "consider `/memory promote`"; **stale-promotion safety-rail** — `_skill-watchlist/` entries marked `promoted` for >30 days without action → "author the skill or dismiss."
- **Operator-tunable config + runtime state** (`auto_orchestration.py`, stdlib-only) — `<vault>/personal-private/auto-orchestration-config.md` (thresholds · cooldowns · per-chain toggles in a ` ```settings ` fence; auto-seeded once, a re-seed never clobbers operator edits) and `<vault>/_meta/auto-orchestration-state.json` (per-chain last-fire cooldowns + the last-shown snapshot that drives the anti-fatigue guard).

### Changed

- **`adapt-evaluator` sub-agent moved crickets → agentm** (`harness/agents/`), completing the memory-surface consolidation begun in V4 #36 — the whole push-surface is now agentm-native. The paired crickets removal + stale-catalog cleanup already landed on crickets `main` (ships with crickets' next release).
- **`memory-reflect-stop` hook** gained a dedup skip-guard (`.sh` + `.ps1`): if a phase-dispatch already reflected the session (the `.reflected` marker is present), Stop skips — the two cooperate so a session is reflected exactly once.

### Internal

- New scripts: `auto_orchestration.py`, `orchestration_briefing.py`, `orchestration_idle.py`, `orchestration_phase.py` + the `harness_memory.py phase-dispatch` bridge; `adapt_skills.py` gained `--limit N` (bounds per-pass GitHub-enrichment cost). **84 tests across 4 new test files** (auto-orchestration 25 · briefing 31 · idle 12 · phase 16); full suite 549 green, 4-OS. Every code task passed an adversarial review that caught a real defect — a `UnicodeDecodeError`-escapes-catch crashing the hook on non-UTF-8 state/config, a clear-then-refill anti-fatigue suppression hiding live pending work, a wrong-session-under-concurrency reflect that burned the shared cooldown — each fixed and regression-tested.

### Deferred

- **Pass-2 auto-dispatch from the idle chain** — the adapt-evaluator sub-agent stays operator-gated (hooks can't dispatch sub-agents); the idle chain stages candidates + surfaces the count, and the evaluate hand-off rides the phase-dispatch / nudge where dispatch is operator-gated.
- **The Anthropic Workflow / mid-conversation-system-message hybrid** (DC-5) — a post-V4 research follow-up: analyze whether hooks-for-triggers + Workflow-for-in-session-fan-out optimizes the orchestration.

### Cross-references

- ROADMAP-V4 item **#23** — auto-orchestration (the last open V4 item; foundation finish).
- [agentm v4.12.0](https://github.com/alexherrero/agentm/releases/tag/v4.12.0) — the immediately prior release (cross-surface vault access).

## [v4.12.0] — 2026-06-01 — Cross-surface AgentM vault access (V4 #22)

**MINOR.** Until now your AgentMemory vault was only readable natively by Claude Code (via its SessionStart hooks). This release makes the vault readable from **every agent surface you use** — Claude.ai, Claude Desktop, and Antigravity — so each one already knows your conventions, projects, and decisions without you re-explaining them every session. The mechanism is **configure-don't-build**: one canonical, paste-anywhere context payload plus thin per-surface wiring; no new MCP server, API, or daemon. Read-only v1 for the chat surfaces; the filesystem working agents you run (Claude Code, Antigravity) may write. Single-repo release; crickets untouched. Every surface is operator-dogfood-validated — Antigravity confirmed on **both** the Antigravity CLI and the Antigravity IDE.

### Added

- **Canonical context payload** (`templates/agentmemory-context.md`) — the one paste-anywhere doc that teaches any surface how to use the vault: per-surface path resolution, the folder map, read-priority order, the entry-reading conventions, and the surface-scoped read/write posture. A self-describing copy lives at `<vault>/_meta/how-to-use-agentmemory.md` so an agent that reaches the vault finds its own usage instructions.
- **Claude.ai** reads the vault via the Google Drive connector (whole-Drive *search*; the payload scopes it to `AgentMemory/`). Dogfood-validated: a fresh chat searched Drive, read `_always-load/`, and answered a convention question unprimed.
- **Claude Desktop** reads the vault via a local **filesystem MCP server** pointed at the vault (Claude-Code-grade navigation, no Drive dependency). Dogfood-validated.
- **Antigravity — per-project rule.** `adapters/antigravity/rules/agentmemory-context.md` (`trigger: always_on`) installs into a project's `.agents/rules/` and loads vault context every session — no manual paste.
- **Antigravity — global rule (user-scope).** `install.sh --scope user` (and `install.ps1 -Scope user`) now idempotently merge the payload into **`~/.gemini/GEMINI.md`** (Antigravity 2.0's global rules file) as a marker-delimited managed section, so Antigravity picks up the vault in **every** workspace with no per-project install — parity with how `--scope user` installs the Claude Code adapter to `~/.claude/`. New `scripts/merge-managed-section.py` does the idempotent create/append/replace-in-place merge, preserving the operator's own GEMINI.md content; gated on `~/.gemini/` already existing.

### Changed

- **Antigravity adapter migrated `.agent/` → `.agents/`** — the Antigravity 2.0 workspace default (per the official rules-workflows docs). Both installers now lay the adapter (rules/workflows/skills) under `.agents/`, and `--update` wipes any legacy `.agent/` tree. `.agents/rules` is doc-confirmed; `.agents/workflows` is inferred from the dir-wide rename.
- **`doctor` is now host-aware.** The canonical doctor detects the host from disk (claude-code `.claude/` · antigravity `.agents/` · gemini `.gemini/`), checks that host's paths, and skips hook/SessionStart checks on the hookless hosts. The Antigravity adapter no longer duplicates the shared `doctor`/`migrate-to-diataxis` skills — it reuses the shared `.agents/skills/` delivery, exactly like Gemini (one host-aware source, no collision).
- **Read/write posture is surface-scoped** (replacing blanket read-only): chat surfaces (Claude.ai, Claude Desktop) never write — they suggest entries for the operator to paste; the filesystem working agents the operator runs (Claude Code, Antigravity) may write, following the vault conventions.

### Internal

- `scripts/merge-managed-section.py` + 9 unit tests (idempotency, no-clobber on both sides, position-preserve, frontmatter strip). `check-parity.sh` updated for the deduped Antigravity skill set + the two always-on Antigravity rules; path-asserting gates (`smoke-install-*`, `check-integrity-*`) follow the `.agents/` migration. Wiki: consolidated cross-surface how-to + payload reference + per-surface dogfood results.

### Deferred (not in v1)

- **Gemini, ChatGPT, Codex** — chat-only bots with no live file/search access to the vault (a plain Gemini chat confirmed it *"can't access or browse your live Google Drive files"*); revisit when they gain agentic Drive/file access. Codex deferred until FRIDAY lands.
- **Antigravity dynamic session-start *recall* hook** (vs. the static rule) + the full hook-porting re-audit + the ADR→living-design / documenter-pattern overhaul — folded into a future **crickets** roadmap item (Antigravity 2.0's new file-based JSON hook surface supersedes the old "no hook surface" finding).
- **Connector-based write-back for the chat surfaces** — a distinct v2 problem.

### Cross-references

- ROADMAP-V4 item **#22** — cross-surface AgentM vault access (read-only v1).
- [agentm v4.11.1](https://github.com/alexherrero/agentm/releases/tag/v4.11.1) — the immediately prior release (conflict-merger hotfix), carried forward in this release's history.

## [v4.11.1] — 2026-05-31 — Fix: conflict-merger hook inert on user-scope installs

**PATCH.** The `conflict-merger-session-start` SessionStart hook — the operator-facing half of V4 #26's cross-agent / cross-device conflict detection — was structurally installed and wired but **functionally inert** on the canonical user-scope install. It read the vault location only from the `MEMORY_VAULT_PATH` env var, which Claude Code does not inject into the hook environment on user-scope installs (and which isn't exported by shell profiles or `settings.json`), so the hook silently exited 0 on every real session boot and never ran `detect_conflict_files()`. Surfaced by a targeted `/doctor` probe during operator dogfood. Single-repo release; crickets untouched. Isolated hotfix — cherry-picked off v4.11.0, independent of the in-flight V4 #22 work on `main`.

### Fixed

- **`conflict-merger-session-start` now resolves the vault via `env → .agentm-config.json::vault_path → none`** (bash hook + pwsh twin), porting the `_resolve_vault_path()` fallback already used by `memory-recall-session-start`. With the env var unset, the hook now reads `vault_path` from the on-device install-state config and detects Google Drive `(conflicted copy …)` files at session boot as designed. This is the second hook to hit the env-injection gap; the resolution order is now a standing convention for vault-aware SessionStart hooks (recorded as an ADR 0007 amendment on `main`).

### Internal

- **Regression test `scripts/test_conflict_merger_hook.py`** drives the bash hook as a subprocess with `MEMORY_VAULT_PATH` unset + a fixture `.agentm-config.json` carrying `vault_path`; it fails against the pre-fix hook (silent exit 0, empty stderr) and passes with the fallback. Companion cases cover env-wins, no-vault-anywhere graceful-skip, clean-vault no-notice, and `HARNESS_CONFLICT_MERGER_MODE=off`. Adversarial `/review`: NO ISSUES FOUND. 435 → 440 tests.

### Cross-references

- [agentm v4.11.0](https://github.com/alexherrero/agentm/releases/tag/v4.11.0) — the release this hotfix branches from.
- ROADMAP-V4 item #26 — the cross-agent conflict-detection feature this restores to working order.

## [v4.11.0] — 2026-05-30 — Opt-in `--apply` for personal-notes link-discovery (V4 #43 follow-up)

**MINOR.** v4.10.0's link-discovery audit was strictly read-only — it surfaced suggestions and left the operator to paste `[[wikilinks]]` by hand. This adds an explicit, opt-in `--apply` mode that writes the safe suggestions in for you, used to dogfood the operator's own ~390-note vault (143 links across 64 notes, plus a one-time rename of 25 bracketed-date filenames so those pairs became linkable). The tool stays read-only by default; `--apply` is the operator-directed escape hatch (A3 is satisfied because the operator asks for it). Single-repo release; crickets untouched.

### Added

- **`--apply` mode (`notes_link_discovery.py`).** Writes the suggested links into a marked `## Related` section at the end of each source note. **Backs the whole corpus up first** to `<vault>/_meta/notes-backup-<date>.tar.gz` and refuses to apply if the backup fails; prints the `tar xzf …` revert command. **Idempotent** — re-running merges into the one marked section, never duplicates it. Only writes wikilink-safe targets (skips bracketed/`|`/`#`/`^` names) the note doesn't already link, in both directions. `plan_apply` / `apply_links` / `backup_corpus` / `_split_related` are the pieces; +9 tests.

### Internal

- **Feedback-loop fix.** The TF-IDF / embedding scoring body now excludes the agent's own `## Related` section, so injected `[[link]]` text can't inflate similarity and surface ever-more pairs on the next run — without this, `--apply` was not idempotent on clustered note series (`note.links` is still parsed from the full body, so dedup still sees applied links).
- **Adversarial review caught destructive data loss.** `_split_related` originally detected the agent block by a bare substring search for the marker, so a note that merely *mentioned* the tool in prose — or carried a human-authored `## Related` — had its prose truncated and the human section clobbered on `--apply`. Now the agent block is anchored to its actual `%% … %%` Obsidian-comment line; a prose mention or a human `## Related` is preserved and the new section appended below. Regression-tested.
- **+9 tests** (`scripts/test_notes_link_discovery.py`): read-only-by-default, backup-before-write, idempotent/no-double-section, merge-into-existing, skip-already-linked, no-feedback-loop, prose-mention-not-clobbered, split-related-ignores-prose-marker. 426 → 435.

### Cross-references

- [agentm v4.10.0](https://github.com/alexherrero/agentm/releases/tag/v4.10.0) — the read-only audit this adds an opt-in writer to.
- ROADMAP-V4 item #43.

## [v4.10.0] — 2026-05-30 — Personal-notes link-discovery audit (V4 #43)

**MINOR.** The read-only complement to v4.9.0's vault lint. Where `vault_lint.py` checks the agent-shaped `AgentMemory/` entries and **skips** the operator's free-form personal notes, this audits *those skipped notes* for **missing connections between them** — "these two notes look related but aren't `[[linked]]`." The personal-notes corpus is richly written but essentially ungraphed (a handful of ~390 notes carry tags, one has a wikilink, frontmatter is just `title`/`created`/`updated`), so relatedness is content-based. It is strictly **read-only** (DC-1) and strictly **personal↔personal** (DC-2) — a personal note is never link-suggested to an `AgentMemory/` entry, enforced by excluding `AgentMemory/` from the corpus as both source and target. The operator applies suggestions by hand (A3 — these are *his* notes). Single-repo release; crickets untouched.

### Added

- **`harness/skills/memory/scripts/notes_link_discovery.py` — read-only missing-link audit.** Two independent relatedness signals over the personal-notes corpus (the Obsidian root excluding `AgentMemory/`, `.obsidian/`, `.trash`, `.git`): **TF-IDF** lexical overlap (always on) and **embedding** semantic similarity (opt-in `--embeddings`). The TF-IDF path hand-rolls sublinear-tf × IDF over title+body (title double-weighted), L2-normalizes, and cosine-scores via an inverted index (never an O(n²) cross product); the embedding path embeds each note with the memory skill's local BGE model (`embed.py`) and full-pairwise-cosines cached vectors. Already-`[[linked]]` pairs (by stem or relative path) are excluded; CLI `--vault`, `--format json|text`, `--top`, `--min-score`, `--embeddings`, `--mode`, `--embed-min-score`.
- **`--report` mode.** Writes an operator-review markdown report to `<vault>/_meta/notes-links-<date>.md` (mirroring `vault-lint-<date>.md`) — ranked pairs with folder/title, the top shared distinctive terms (the *why*), the score, and **paste-ready bidirectional `[[wikilinks]]`**. The report renders **both signals**: a "Shared-vocabulary (TF-IDF)" section flagging pairs embeddings also confirm (`✓ also semantically related`), plus a "Semantically related (embedding — TF-IDF missed these)" section for the new coverage. The report file is the *only* write the audit ever makes; it refuses any `--out` outside the agent-controlled vault or onto a personal note.
- **Separate personal-notes embedding cache** at `<vault>/_meta/notes-embeddings.json` — content-hash keyed so re-runs only re-embed changed notes (live: ~27s cold → ~2s warm on 392 notes), deliberately **never** the AgentMemory `vec-index.db` (DC-2). Graceful-skips to TF-IDF-only when `sentence-transformers` is absent.

### Internal

- **Clip-noise cleaning (live-dogfood driven).** The personal notes are largely pasted HTML, so the first dogfood saw hex colors (`fffaa5`), CSS tokens (`serif`), and image refs (`image1`) dominating the shared-terms. The tokenizer now strips `<style>`/`<script>` blocks, HTML tags, `{…}` CSS rules, `#hex` colors, image embeds, and URLs, drops hex-id/media tokens, and carries a compact **Spanish** stopword set (the corpus is bilingual) — kept conservative so common words that double as CSS keywords (`family`/`width`/`color`/`times`) are *not* stopworded and a family-history corpus keeps its own vocabulary.
- **Three adversarial reviews, three real fixes.** (1) A `max_df`-band collapse that silently dropped a genuine pair's shared terms on tiny corpora; (2) a severe `--out` hole that would have *overwritten a personal note* (destructive DC-1 violation) — now guarded; (3) a stale-dimension embedding-cache reuse (the documented `EMBEDDING_DIM` 384→1024 upgrade / model swap) that `_cosine_unit`'s `zip` would truncate into false-positive scores — now re-embeds stale-dim entries so the vector set is always uniform (mirrors `vec_index.py`'s dim-rebuild). Each fix shipped with a regression test.
- **Live dogfood (392 notes):** 40 TF-IDF + 16 embedding-only suggestions; 25/40 cross-confirmed. Standout embedding-only finds TF-IDF *structurally cannot* make: the same Stake Conference talk in `[SP]` and `[EN]` (cross-language, cosine 0.988) and a person's Birth/Baptism certificates (same person, different docs).
- **+36 tests** (`scripts/test_notes_link_discovery.py`; the engine lives in the skill dir, tests in `scripts/` so CI's `unittest discover` runs them). 406 → 426. The how-to page runs 14 words over the 600-word soft ceiling — kept intact as a load-bearing worked recipe (two optional signals + the read-only/paste-by-hand flow).

### Cross-references

- [agentm v4.9.0](https://github.com/alexherrero/agentm/releases/tag/v4.9.0) — prior release (the `AgentMemory/`-entry lint this complements).
- ROADMAP-V4 item #43 (bucket ① V4-finish). Deferred follow-ups: real-time / watcher re-embedding (re-run is fine at this corpus size); packaging into the crickets **personal-notes bundle** (V4 ④).

## [v4.9.0] — 2026-05-29 — Agent Memory vault cleanup audit (V4 #33)

**MINOR.** A read-only lint over the MemoryVault + a one-time audit pass. As the vault grew across the V4 arc (~150 agent entries), entries drift off-spec — broken `[[wikilinks]]`, malformed frontmatter, dangling supersede references, unsanctioned keys. `vault_lint.py` surfaces all of it with a suggested fix per finding, and **never touches the vault** — it reports; the operator applies (A3, locked DC-1). Auto-repair stays deferred to V5-5. Single-repo release; crickets untouched.

### Added

- **`harness/skills/memory/scripts/vault_lint.py` — read-only vault lint.** A registry of 9 checks over *agent-shaped* entries (those carrying the `kind`+`status`+`created` frontmatter trio; the operator's intermixed free-form notes are skipped): `required-field`, `kebab-case`, `field-order`, `slug-filename`, `date-format`, `placeholder-value` (an unfilled `a | b | c` option-list left in frontmatter), `schema-drift` (unknown keys), `wikilink-resolution`, `supersede-integrity`. Wikilinks resolve against the **whole Obsidian vault root** (the dir with `.obsidian/`), not just `AgentMemory/`, so cross-vault references (e.g. `[[Ideas]]`) don't false-positive. CLI: `--format json|text`, `--scope`.
- **`--audit` report mode.** Runs all checks and writes a grouped operator-review report (to `--out` or `<vault>/_meta/vault-lint-<date>.md`) — findings collapse by severity → check → identical message, so a key like `domain` across 8 entries is one line + an entry list, not 8 repeats. The report file is the *only* write the lint ever makes; it never mutates an entry.

### Changed

- **`save.py` is the lint's schema source of truth (DC-2).** Added `FRONTMATTER_FIELD_ORDER` / `REQUIRED_FRONTMATTER_FIELDS` constants the lint reuses (a test pins `_build_frontmatter` to the order), and **widened the `group` regex** from a single optional sub-segment to any depth (`^[a-z0-9-]+(/[a-z0-9-]+)*$`) — the live vault had outgrown it with deep groups like `projects/<slug>/decisions`. Backward-compatible.

### Internal

- **Live-dogfood calibration.** Running the lint against the real vault drove three fixes that took findings from 131 → 39 (no false-positive floods): Obsidian-root wikilink resolution, normalizing markdown-escaped `\]]` brackets, and exempting `_index`/`_summary` anchor slugs from the kebab check. An adversarial review then caught a contained false-negative (the supersede "still active" warn not resolving a stem reference) — fixed + regression-tested.
- **+26 tests** (`scripts/test_vault_lint.py`; the lint lives in the skill dir but its tests live in `scripts/` so CI's `unittest discover` runs them). 380 → 406.

### Cross-references

- [agentm v4.8.0](https://github.com/alexherrero/agentm/releases/tag/v4.8.0) — prior release.
- ROADMAP-V4 item #33. Deferred follow-ups: auto-fix / self-healing (→ V5-5); idea-incubator `_summary.md` + `Ideas.md` bespoke-shape lint; scheduled/unattended runs (→ V6); and a personal-notes link-discovery audit (→ V4 #43).

## [v4.8.0] — 2026-05-29 — Auto-detect + auto-configure on first session (V4 #32)

**MINOR.** The capstone of the global-install arc (#30 → #35 → #39): the first conversation in a repo the harness hasn't seen now configures itself instead of needing a manual setup script. A quiet SessionStart nudge offers to configure an unconfigured project; on request a deterministic engine scans the repo against 10 rules and proposes a **default-all-enabled** config with a per-skill/per-hook *rationale* (why each is relevant to THIS repo); on approval the enablement block is written to `project.json` — **not** `features.json`, which stays the governed verification ledger (locked DC-1). Detection never gates which skills/hooks are present; it surfaces why each is on so the operator can make an informed opt-out. Single-repo release; crickets unaffected.

### Added

- **`scripts/detect_project.py` — deterministic auto-detect engine.** 10 side-effect-free rule functions `(cwd) -> Optional[RuleMatch]` over a default-all-enabled baseline: `R-wiki`→diataxis-author, `R-changelog` (CHANGELOG + a language manifest)→ship-release, `R-dependabot`→dependabot-fixer, `R-pii` (`.env*`)→pii-scrubber, `R-tests`→evidence-tracker, `R-harness` (`harness/phases/`)→**bypass verdict**, `R-pkg-scripts`→kill-switch+steer, `R-vault-content`→memory + memory hooks, `R-design`→design, `R-non-coding`→V5 stub. CLI `--format json|text` (text renders the operator-facing a/b/c propose-config block).
- **`/setup` detect → propose → approve → write flow.** New §0 of the setup phase spec (`harness/phases/01-setup.md`) runs detection first; on approval writes the enablement block to `project.json` + registers the repo + creates the vault `_index.md` + offers an `AGENTS.md` `vault_slug:` line; on skip writes `.agentm-no-register`. Mirrored as constraint 0 across all three setup adapters (claude-code / antigravity / gemini).
- **SessionStart configure-nudge.** `harness-context-session-start.{sh,ps1}` gains an else-branch nudge: when vault state doesn't resolve and the cwd is an unconfigured git repo (gated by `project_config.py should-nudge`), it emits a one-line "New project — run /setup --detect" prompt instead of staying silent. Fires until the repo is registered or `.agentm-no-register` is dropped.

### Changed

- **`project.json` gains an additive enablement block** (`type`/`skills`/`hooks`/`registered_at`/`registered_via`/`operator_overrides`/`last_redetect_at`). The merge-writer preserves the pre-existing `vault_project`/`github`/`env` keys and routes through the `.project-mode`-aware `write_state_file` so it never clobbers vault state on local-mode projects. `features.json` is untouched — it stays the verification ledger flipped only at `/release` (DC-1).

### Internal

- **`scripts/project_config.py`** — pure functions (`build_enablement_block`, `merge_enablement`, `apply_override`, `is_registered`) + I/O (`load_project_json`, `write_config`, `register`) + CLIs (`is-registered`, `should-nudge`, `register`). `should-nudge` encapsulates the whole nudge gate in testable Python; the hook only emits.
- **Adversarial-review fix (pre-release).** `write_config` originally wrote unconditionally to the vault path while `load_project_json` reads through the `.project-mode`-aware path — a data-loss bug that dropped `github`/`env` on local-mode projects. Routed the write through `write_state_file` so read and write share one target; `should-nudge` also now accepts a `.git` **file** (git worktree/submodule), not just a dir.
- **+40 tests** (340 → 380): `test_detect_project.py` (23), `test_project_config.py` (13 incl. 2 regression), `test_harness_context_hook.py` (+4 nudge cases).

### Cross-references

- [agentm v4.7.0](https://github.com/alexherrero/agentm/releases/tag/v4.7.0) — prior release; v4.8.0 builds the auto-detect capability on top of the hardened user-scope install.
- ROADMAP-V4 item #32; design-prep `07b-auto-detect-rules.md`; HLD [device-wide-architecture.md](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/device-wide-architecture.md) § "Auto-detect bootstrap on first session".

## [v4.7.0] — 2026-05-29 — Installer + hooks robustness batch (orphan-symlink reaping, cross-scope path resolution)

**MINOR.** Bundles the eight installer/hooks-hardening commits accumulated since v4.6.1, surfaced and closed during a `crickets` dogfood `/doctor` audit. The marquee change is **orphan-symlink reaping** on user-scope source-mode installs (a `feat:`, hence the minor bump); the rest are robustness fixes — cross-install-scope memory-skill path resolution (the V4.7 silent-broken `memory-recall` shape), Windows path-normalization for the reaper, user-scope `telemetry.sh` seeding, and a fix so the installer stops symlinking loose canonical specs under `harness/skills/` as inert skills. Ships alongside the companion [crickets v2.1.2](https://github.com/alexherrero/crickets/releases/tag/v2.1.2) (gitignores `.harness/` so the session-marker can't leak into that public repo).

### Added

- **Orphan-symlink reaping on user-scope source-mode install** ([`8c5af42`](https://github.com/alexherrero/agentm/commit/8c5af42)) — `install.sh` now reaps dangling symlinks under `<prefix>/{agents,commands,skills,hooks,scripts}/` that point into a source clone whose target file was deleted. Intentionally narrow: only touches symlinks (never operator-placed real files), only those pointing into a known clone, only when the target is genuinely absent — live symlinks and external/operator symlinks are left alone.

### Changed

- **Installer stops symlinking loose `.md` specs under `harness/skills/`** ([`3f0f56c`](https://github.com/alexherrero/agentm/commit/3f0f56c)) — `symlink_targets_for_clone` treated loose `<name>.md` siblings (e.g. `doctor.md`, `migrate-to-diataxis.md`) as installable "single-file skills" and symlinked them into targets, but Claude Code loads skills as `<name>/SKILL.md`, so a top-level `skills/<name>.md` is inert and only littered installs with duplicate spec symlinks. Now maps dir bundles only; the loose specs stay as repo docs.
- **`memory-recall` hook resolves `recall.py` across install scopes** ([`a79f9f6`](https://github.com/alexherrero/agentm/commit/a79f9f6)) — pre-fix the hook hardcoded a project-scope relative path and assumed `MEMORY_VAULT_PATH` was injected into the hook env; neither held on user-scope installs, so the hook exited 0 emitting nothing despite always-load entries in the vault. Now resolves the memory-skill path across project/user scopes.
- **`_reap_orphan_symlinks` path normalization for Windows** ([`b2a922d`](https://github.com/alexherrero/agentm/commit/b2a922d)) — the reaper's broken-symlink-target comparison used naive `str.startswith`, which silently no-op'd on Windows (extended-path prefix asymmetry: `\\?\C:\…` on the clone root vs plain `C:\…` on the resolved target). Added `_normalize_path_str` / `_path_under` so orphans actually get reaped cross-platform.
- **`telemetry.sh` seeded at user scope** ([`f8a356d`](https://github.com/alexherrero/agentm/commit/f8a356d)) — `install.sh` now seeds `<prefix>/scripts/telemetry.sh` (the multi-project usage scanner) on user-scope installs, and a stale doctor Task-9 stub was dropped.

### Internal

- **Regression + cross-platform test coverage** ([`73ae161`](https://github.com/alexherrero/agentm/commit/73ae161), [`7023340`](https://github.com/alexherrero/agentm/commit/7023340), [`3f0f56c`](https://github.com/alexherrero/agentm/commit/3f0f56c)) — neutral Windows path fixtures (avoid the `C:\Users\<name>` shape the PII guard flags), a "tests-are-sacred" rule-5 clarification, and a new `HarnessSkillsMappingTests` asserting loose `.md` siblings under `harness/skills/` are not mapped while dir bundles are.
- **`.checksums.txt` regenerated** ([`b086207`](https://github.com/alexherrero/agentm/commit/b086207)) — lib-parity sync to crickets.

### Cross-references

- [crickets v2.1.2](https://github.com/alexherrero/crickets/releases/tag/v2.1.2) — companion release; gitignores `.harness/` (the `memory-recall-session-start` marker carries a personal transcript path, and crickets is public).
- [agentm v4.6.1](https://github.com/alexherrero/agentm/releases/tag/v4.6.1) — the prior release; v4.7.0 continues its user-scope-install hardening.

## [v4.6.1] — 2026-05-28 — Installer hook-wiring repair + harness-context hook + doctor wiring probe (V4 #39)

**PATCH.** Single-repo release; crickets stays at v2.1.0 (it received a byte-identical `lib/install/python/install_state.py` sync via [`ebf92fa`](https://github.com/alexherrero/crickets/commit/ebf92fa) but **no release tag** — lib-parity only). Fixes a v4.5.1/v4.6.0 **installer regression**: `install.sh --scope user` dropped hook dirs into `~/.claude/hooks/<name>/` but never merged their `settings-fragment-bash.json` into `~/.claude/settings.json`, so `settings.json` had no `hooks` block and **none of the 10 installed hooks fired on any device**. (Surfaced when the agent missed a vault-resident `PLAN.md` because no SessionStart hook was wired to surface it.) The semver bump is PATCH — the regression fix is the load-bearing change; the new hook + doctor probe ride along as bundled improvements.

### Added

- **`harness-context-session-start` hook** ([`db5e6e0`](https://github.com/alexherrero/agentm/commit/db5e6e0)) — new user-scope SessionStart hook. Reads the event's `cwd`, resolves the active project's vault `PLAN.md` + `progress.md` via `harness_memory.py vault-state-path`, and injects a 4-line context block at session boot — **only when both files exist** (silent no-op otherwise; 500ms budget; `set -uo pipefail`, never blocks boot). Surfaces "this project's plan lives at `<vault path>`" automatically in every project, closing the gap that motivated the release. pwsh twin included.

### Changed

- **`install.sh --scope user` now merges hook fragments + absolutizes paths** ([`0baf142`](https://github.com/alexherrero/agentm/commit/0baf142)) — the user-scope install walks the installed `<prefix>/hooks/*/` dirs and merges each `settings-fragment-bash.json` into `<prefix>/settings.json`, rewriting the command to the absolute user-scope dir layout `bash <prefix>/hooks/<name>/<name>.sh`. `scripts/merge-settings-fragment.py` gained a `--command` override for the absolutization (idempotent re-merge by the rewritten command).
- **`/doctor` hook-wiring check** ([`110fe9d`](https://github.com/alexherrero/agentm/commit/110fe9d)) — replaced the false-clean "absent hooks block is OK — `--hooks` opt-in" with a 7-row truth table: hook dirs on disk + no `hooks` block now reports **`[FAIL] N hooks installed but not wired — re-run install.sh`** (the regression), plus broken-command-path / partial-merge / missing-config / missing-fragments cases. Adds a `--live` synthetic SessionStart probe. Mirrored in the canonical `harness/skills/doctor.md`.

### Internal

- **`.agentm-config.json` gains an additive `fragments: [{path, sha256}]` field** ([`0baf142`](https://github.com/alexherrero/agentm/commit/0baf142)) — records each merged settings fragment for `install-state-sync` drift detection. No `schema_version` bump (schema v2 stays valid; field optional). `lib/install/python/install_state.py` `persist` exposed via a new `--fragments-file` CLI flag (synced byte-identical to crickets).
- **+18 unit tests** (312 → 330 per OS workflow): `test_merge_settings_fragment.py` (8), `test_install_state_fragments.py` (6), `test_harness_context_hook.py` (4).

### Backward-compat

- Existing `.agentm-config.json` files without `fragments` keep working; `install.sh` adds the field on the next (re-)install.
- **Operators must re-run `bash install.sh --scope user`** to pick up the fix — the regression left `settings.json` without a `hooks` block, so hooks stay dormant until the installer re-runs (then they fire on the next session restart).

### Cross-references

- [crickets `ebf92fa`](https://github.com/alexherrero/crickets/commit/ebf92fa) — byte-identical `install_state.py` lib-sync (no crickets release).
- [agentm v4.6.0](https://github.com/alexherrero/agentm/releases/tag/v4.6.0) — the release this patches.

## [v4.6.0] — 2026-05-28 — Documenter vault-context resolution (V4 #35)

**MINOR.** Single-repo release; crickets ships the paired HLD update [`5c49095`](https://github.com/alexherrero/crickets/commit/5c49095) (the one crickets-side touchpoint) but stays at v2.1.0 (no crickets release tag). ROADMAP-V4 item **#35** — the documenter-side closure of the V4 #26 state migration. Post-V4 #26 the harness's per-project state lives at `<vault>/projects/<slug>/_harness/`, but the doc-touching customizations still re-derived operator conventions + project decisions from the repo on every invocation. v4.6.0 teaches them to read that context from the vault instead: a new `documenter` recall phase + the `documenter-context` CLI feed a recall bundle (operator conventions + project decisions + locked design calls) to the `documenter` sub-agent and the `wiki-author` / `diataxis-author` skills before they write — so wiki authoring respects the operator's `_always-load/` conventions + the project's `decisions/` without the operator repeating themselves at each doc edit. **This release also folds in v4.5.2** — an installer-probe bugfix surfaced during the task-5 dogfood (see below); no separate v4.5.2 tag was cut.

### Added

- **`documenter` recall phase + `resolve_documenter_context(slug)` + `documenter-context` CLI** ([`da63046`](https://github.com/alexherrero/agentm/commit/da63046)) — `harness_memory.py` gains a `documenter` recall pseudo-phase (`_PHASE_PROJECT_DIRS["documenter"] = ("_index.md", "decisions", "wiki-style")`, added to `_VALID_PHASES` / `_DEFAULT_BUDGETS` / `_RECALL_QUERIES`). `resolve_documenter_context(slug)` returns a structured bundle `{slug, registered, operator_conventions, project_decisions, project_anchor, wiki_style}` (`None` when the vault is unavailable). The `documenter-context` subcommand renders it — `--slug`, `--budget`, `--format text|json`; exit codes `0` (bundle) / `1` (vault unavailable) / `2` (slug not registered).
- **`scripts/vault_probe.py`** ([`158e02b`](https://github.com/alexherrero/agentm/commit/158e02b)) — installer vault-detection ranking + refinement (the v4.5.2-folded fix). `rank_candidates()` ranks `_meta/repos.json` markers above `.obsidian` and suppresses `.obsidian` roots that wrap a repos root; `find_nested_vault()` descends a candidate one level into a nested MemoryVault. Stdlib-only.

### Changed

- **Three doc-touching primitives consume the bundle** ([`fbb5b89`](https://github.com/alexherrero/agentm/commit/fbb5b89)) — the `documenter` sub-agent (canonical `harness/agents/documenter.md` + `adapters/claude-code/agents/documenter.md`) runs a `documenter-context` pre-flight before scanning `wiki/`; the `wiki-author` skill surfaces the bundle in its preview-before-write step; the `diataxis-author` skill routes its operator-convention read through the resolver. All three graceful-skip on rc 1 (vault unreachable) with a one-warn stderr notice + repo-local fallback.
- **Installer first-run vault detection fixed (v4.5.2-folded)** ([`158e02b`](https://github.com/alexherrero/agentm/commit/158e02b) + [`2aac617`](https://github.com/alexherrero/agentm/commit/2aac617)) — `install.sh`'s `_agentm_vault_first_run_prompt` previously used a flat `find -maxdepth 5` that treated the `_meta/repos.json` and `.obsidian` markers equally; on a Google-Drive-shortcut vault the repos.json marker sits below the depth cap while the parent Obsidian app-vault's `.obsidian` matched, so the wrapper was selected — splitting harness state across two roots. The probe now pipes its find output through `vault_probe.py` (rank + refine), keeping the find shallow while recovering a vault nested inside an Obsidian app-vault.
- **Documenter recall budget 4k → 10k + project-first ordering** ([`6090fc4`](https://github.com/alexherrero/agentm/commit/6090fc4)) — the task-5 dogfood showed the 4k budget truncated away the project decisions (31 always-load conventions ~27k tokens). Raised to 10k (overrideable via `HARNESS_RECALL_BUDGET_DOCUMENTER`); the documenter recall now emits project context before always-load via a new `phase_recall(project_first=True)` flag so project decisions survive truncation.
- **ADR 0007 amended** ([`2dccf31`](https://github.com/alexherrero/agentm/commit/2dccf31)) — a `## Amendment 2026-05-28` block documents the documenter phase. It was authored BY the `documenter` sub-agent through the new resolver — a dogfood of the feature it documents.

### Internal

- **+37 unit tests** (275 → 312 per OS workflow): `scripts/test_harness_memory_documenter.py` (19 — resolver + CLI + project-first ordering) + `scripts/test_vault_probe.py` (18 — marker ranking + nested-vault refinement, including the operator's exact bug scenario).
- **Operator-machine vault-root reconciliation** — the dogfood revealed the operator's `vault_path` had been mis-set by the v4.5.1 probe (the parent Obsidian dir vs the nested `AgentMemory/` MemoryVault), which had split harness state across two roots + blinded recall. Corrected on-device (`vault_path` fixed via `agentm_config.py`; split state reconciled into the canonical `_harness/`). The shipped probe bugfix prevents recurrence for fresh installs.
- **HLD updates** — crickets [`5c49095`](https://github.com/alexherrero/crickets/commit/5c49095) adds the V4.7 milestone subsection (`agent-memory-evolution.md`) + the v0.7 Lifecycle entry (`device-wide-architecture.md`), tying the probe fix back to the device-wide doc's "First-run vault detection" design.

### Backward-compat

- **Graceful-skip on vault-unreachable** — all three primitives fall back to pre-v4.6.0 repo-local behavior + a one-warn-per-session stderr notice when the vault isn't mounted (CI, fresh devices). No hard failure.
- **`documenter-context` budget overrideable** via `HARNESS_RECALL_BUDGET_DOCUMENTER`.

### Cross-references

- [crickets `5c49095`](https://github.com/alexherrero/crickets/commit/5c49095) — paired HLD update (V4.7 / v0.7).
- [ADR 0007](wiki/explanation/decisions/0007-auto-context-into-harness-phases.md) + its Amendment 2026-05-28 — the auto-context dispatcher this extends (Q1 budgets + Q3 graceful-skip).
- [agentm v4.1.0](https://github.com/alexherrero/agentm/releases/tag/v4.1.0) — V4 #26 state migration, whose documenter side this closes.

## [v4.5.1] — 2026-05-28 — On-device agentm config + first-run vault detection (V4 #30 follow-up)

**PATCH.** Single-repo release; crickets ships a paired byte-identical [`fe37a96`](https://github.com/alexherrero/crickets/commit/fe37a96) for `lib/install/python/install_state.py` propagation but stays at v2.1.0 (no crickets release tag — lib parity sync only). Closes the V4 #30 promised-but-not-shipped "first-run vault detection" gap surfaced during the v4.5.0 dogfood: `MEMORY_VAULT_PATH` had no source-of-truth file backing it on disk, so every vault-aware script silently graceful-skipped when the env var wasn't exported in a given shell. After v4.5.1, `vault_path` lives in `~/.claude/.agentm-config.json` (the renamed install-state file, schema v2) and the resolver consults it as a fallback when the env is unset. Backward-compat preserved: `$MEMORY_VAULT_PATH` env still wins as override; pre-v4.5.1 installs auto-migrate the legacy `.agentm-install-state.json` filename on first interaction (read-side via the SessionStart hook OR write-side via the next `install.sh` run).

### Fixed

- **Vault path now resolves from on-device config when env is unset** — closes the V4 #30 promised behavior. `harness_memory.py::vault_path()` resolution order: `$MEMORY_VAULT_PATH` env (override) → `<install-prefix>/.agentm-config.json::vault_path` (new — on-device source of truth) → `None` (graceful-skip). Env still wins even when set to a broken path, matching documented "env wins" contract per locked DC-2.
- **6 vault-aware shell scripts** now consult the config-file fallback (previously env-only, refusing to run if `MEMORY_VAULT_PATH` wasn't exported in the current shell): `list-plans.{sh,ps1}`, `rename-vault-personal-projects.{sh,ps1}`, `migrate-harness-to-vault.sh`, `recent-wiki-changes.{sh,ps1}`. Each gained a one-line `agentm_config.py --get vault_path` fallback between the env-check and the error-emit. `repo_registry.py` auto-inherited via `_vault_or_none() → hm.vault_path()`.

### Added

- **`scripts/agentm_config.py`** — new stdlib-only CLI (~190 LOC) for reading/writing fields in `~/.claude/.agentm-config.json` without re-running the full installer. Four mutually-exclusive operations: `--vault-path PATH` (writes after validating target is an existing dir; rc=2 + stderr error on refusal; idempotent silent no-op on same value); `--get FIELD` (prints value to stdout; rc=0 if present, rc=1 silent if absent); `--list` (dumps full JSON); `--unset FIELD` (clears field; refuses `schema_version` as structural). Honors `--install-prefix` CLI → `$AGENTM_INSTALL_PREFIX` env → `~/.claude`. Atomic writes via tmp+`os.replace()`.
- **`install.sh` first-run vault detection** — new `_agentm_vault_first_run_prompt` function invoked after `persist_install_state` succeeds in the `--scope user` path. macOS-only auto-detect via bounded `find -L ~/Library/CloudStorage -maxdepth 5` looking for `*/_meta/repos.json` (V4 #30 plan 1's repo_registry marker) or `*/.obsidian` markers; `.shortcut-targets-by-id` left reachable so Google Drive shortcut-linked vaults resolve. 10s hard timeout via gtimeout/timeout (graceful no-op if neither installed). Prune list: `.Trash*`, `.tmp`, `.fseventsd`, `.Spotlight-V100`. Presents numbered candidate list + manual-entry + skip; reads from `/dev/tty` for pipe compatibility. Hands off to `agentm_config.py --vault-path` for validation + atomic write. Skip conditions (each with one-line stderr notice): `CI=true` env, non-Darwin host, `vault_path` already set without `--force-vault-prompt`.
- **`install.ps1` first-run vault detection skeleton** — new `-ForceVaultPrompt` param + idempotency check via `agentm_config.py --get vault_path`. CI skip parity. Auto-detect deferred for pwsh hosts (Windows + macOS-pwsh): operators see the manual `agentm_config.py --vault-path` instruction.
- **`--force-vault-prompt` / `-ForceVaultPrompt` flag** added to both installers' arg parsing. Re-fires the prompt when `vault_path` is already set; useful after moving the vault to a new mount path.

### Changed

- **Config file renamed `.agentm-install-state.json` → `.agentm-config.json`** + schema v1 → v2. Hard cutover per locked DC-1 (single-operator setup; no breadcrumb stub). Migration is automatic + idempotent:
  - **Read-side** (`scripts/install_state_sync.py::_read_state()` from task 1 / commit [`1f31e62`](https://github.com/alexherrero/agentm/commit/1f31e62)): atomic `os.replace()` of legacy → new on first read.
  - **Write-side** (`lib/install/python/install_state.py::persist_install_state()` from task 4 / commit [`96e566d`](https://github.com/alexherrero/agentm/commit/96e566d)): reads pre-existing `vault_path` from new OR legacy file + preserves across re-install; removes legacy file on persist; writes `schema_version: 2` (replaces `version: 1`) + always-present `vault_path` field (null when unset).
  - Both `agentm-update` launchers (bash + pwsh) + `migrate-to-user-scope.{sh,ps1}` read new filename first, fall back to legacy on pre-v4.5.1 installs.
- **Doctor skill ([`adapters/claude-code/skills/doctor/SKILL.md`](adapters/claude-code/skills/doctor/SKILL.md) + canonical spec)** taught V4 #26 vault-state + V4 #30 user-scope reality (commit [`506007a`](https://github.com/alexherrero/agentm/commit/506007a)). Install-scope detection (project / user / mixed); state-file resolution via `harness_memory.py vault-state-path` ladder; expected sets include `recent-wiki-changes` + `wiki-author`; harness compound skills + crickets primitives become graceful-skip. Output contract gains `scope:` + `state mode:` rows.

### Internal

- **CI auto-discover** — all three OS workflow YAMLs ([`tests-{linux,mac,windows}.yml`](.github/workflows/)) switched from hardcoded `python3 scripts/test_{vault_project,harness_memory}.py` to `(cd scripts && python3 -m unittest discover -p 'test_*.py')`. Closes pre-v4.5.1 CI gap where `test_install_migrate.py` (26 tests from V4 #30 plan 3) and the new `test_install_state_sync.py` + `test_agentm_config.py` were invisible to the OS matrix. **Coverage delta**: 214 → 275 tests run per OS workflow.
- **Test sandbox** — `scripts/test_harness_memory.py` gains module-level `setUpModule` / `tearDownModule` setting `AGENTM_INSTALL_PREFIX` to a tmp dir. Otherwise tests that assert "MEMORY_VAULT_PATH unset → vault_path() == None" would fail on operators whose real `~/.claude/.agentm-config.json` has `vault_path` set (the post-task-4 dogfood state). Hermetic without re-architecting every test.
- **Probe-filter iterations from operator smoke** — task 4's interactive vault prompt got two follow-up patches based on real-vault dogfood. Commit [`eed4f6c`](https://github.com/alexherrero/agentm/commit/eed4f6c) added prune predicates for `.Trash*` / `.tmp` / `.fseventsd` / `.Spotlight-V100` to suppress false-positive candidates. Commit [`7c57085`](https://github.com/alexherrero/agentm/commit/7c57085) walked back the `.shortcut-targets-by-id` prune (it's where Google Drive serves shortcut targets — pruning it made shortcut-linked vaults invisible) and added `-L` to `find` for general symlink following.
- **PII guardrail saves** — pre-push hooks caught two operator-path leaks before they landed on remote: commit [`8546e7a`](https://github.com/alexherrero/agentm/commit/8546e7a) scrubbed an operator-home test-fixture path (replaced with `/srv/test-vault`); a `/Users/<name>/Obsidian/MyVault`-shaped docstring example in `install_state.py` got genericized to `/path/to/Obsidian/MyVault` mid-task-4.
- **Windows tilde-expansion test skipped** ([commit `758aa01`](https://github.com/alexherrero/agentm/commit/758aa01)) — `os.path.expanduser` uses `USERPROFILE` on Windows, not `HOME`, so the env-override pattern two bonus tests used (in `test_agentm_config.py` + `test_harness_memory.py`) is POSIX-only. Production code is unaffected — the resolver delegates to `os.path.expanduser()` which handles the platform difference internally; the gap is only in test setup. Skipped via `@unittest.skipIf(os.name == "nt", ...)`.
- **lib parity propagation** — `bash scripts/sync-lib.sh` after `install_state.py` v2 update; crickets received the byte-identical copy via [`fe37a96`](https://github.com/alexherrero/crickets/commit/fe37a96).

### Backward-compat

- **`$MEMORY_VAULT_PATH` env still wins** as override — operators who already export the var upstream of their sessions see ZERO behavior change.
- **Legacy filename auto-migrates** on first interaction. No operator action required for pre-v4.5.1 installs.
- **`read_install_state()`** reads new path first + falls back to legacy without migration on disk (the migration happens on the next `persist_install_state()` call or via the SessionStart hook).
- **`agentm-update` launchers** (bash + pwsh) read new path, legacy fallback. Pre-v4.5.1 installs continue to update cleanly until their next `install.sh` run migrates them.
- **Hard cutover, no breadcrumb** per locked DC-1: when the new filename is created, the legacy filename is unconditionally removed. No `.deprecated-renamed-to-config-json.txt` stub left at the old path. Single-operator setup; no contributor onboarding case to coddle.

### Cross-references

- [crickets `fe37a96`](https://github.com/alexherrero/crickets/commit/fe37a96) — paired byte-identical `lib/install/python/install_state.py` sync
- [agentm v4.5.0](https://github.com/alexherrero/agentm/releases/tag/v4.5.0) — V4 #30 plan 3 close-out; this patch closes the trio's promised-but-not-shipped first-run vault detection
- [agentm v4.3.0](https://github.com/alexherrero/agentm/releases/tag/v4.3.0) — V4 #30 plan 1 introduced the install-state file this patch renames + extends
- ADR 0001 (stdlib-only Python preserved)
- ADR 0012 § 6 (dev-setup invisibility policy preserved)

### Deferred (out of scope for this patch)

- **Env-var consolidation for other AGENTM_* / HARNESS_* knobs** (`AGENTM_INSTALL_PREFIX`, `AGENTM_WIKI_RECENT_DAYS`, `HARNESS_RECALL_BUDGET_*`, `HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD`) — they all degrade gracefully with their defaults; the vault path was the only load-bearing env that lacked an on-device source of truth.
- **Windows + Linux auto-detect** for the installer first-run prompt — macOS-only per locked DC-7. Non-macOS operators get the manual `agentm_config.py --vault-path` instruction.
- **Slash-command wrapper** around `agentm_config.py` (e.g. `/config vault-path <path>`) — deferred to v4.6.x if operator usage shows the script-only path is friction.
- **Auto-promote `$MEMORY_VAULT_PATH` env into config file on first-seen** — adds state-mutation surface; defer until a real need appears. Operators can run `agentm_config.py --vault-path "$MEMORY_VAULT_PATH"` once if they want to migrate from env-only to config.
- **HLD subsection** — per `[[hld-evolution-update-on-major-release]]`, PATCH releases don't earn HLD updates. v4.6.x HLD subsections will fold v4.5.1's existence into their narrative naturally.

## [v4.5.0] — 2026-05-27 — Migration tooling + opt-out documentation (V4 #30 plan 3 of 3 — closing)

**MINOR.** ROADMAP-V4 item #30 (plan 3 of 3 — **CLOSING**). Single-repo release; crickets unaffected at v2.1.0 (lib/install propagates byte-identical via `sync-lib.sh` but no crickets release this plan). **Closes the V4 #30 trio**: plan 1 ([v4.3.0](https://github.com/alexherrero/agentm/releases/tag/v4.3.0) paired with [crickets v2.1.0](https://github.com/alexherrero/crickets/releases/tag/v2.1.0)) shipped `--scope user` install + `repo_registry` vault-backed primitive + auto-stay-in-sync; plan 2 ([v4.4.0](https://github.com/alexherrero/agentm/releases/tag/v4.4.0)) shipped wiki I/O codification + cross-repo views; **plan 3 (this release)** ships the automated + reversible migration tooling for non-operator users + opt-out documentation for the legitimate `--scope project` cases. See [HLD V4.6 subsection](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/agent-memory-evolution.md#v4-release-milestones) for the architectural arc + trio close-out narrative.

### Added

- **`lib/install/python/install_migrate.py`** — new stdlib-only primitive (~530 LOC) that powers the migration tool. Pairs with `install_symlinks.py` (forward direction; from plan 1) to provide the REVERSE direction: detect what's under `<target>/.claude/{skills,hooks,agents,commands}/` + classify each entry against source-clone canonical paths via SHA256 compare. Four classifications (DC-4): `safe_to_migrate` (byte-identical → safe to remove from per-project); `already_symlinked` (target is symlink → no-op); `operator_edited` (SHA differs from source → conflict; skip-with-warn by default, `--force` migrates with backup); `unrecognized` (no source mapping → operator content, leave alone). Five public functions: `classify()` · `apply()` · `rollback()` · `cleanup()` · `inverse_mapping_for_clones()`. Dir bundles (skill bundles, hook bundles) hashed via sorted `(rel_path, file_sha256)` line concatenation with **dotfile-skip policy** (mandatory for macOS parity — Finder `.DS_Store` would otherwise leak into bundle hash + force every macOS user into false `operator_edited` classifications). `.agentm-migrate-record.json` schema v1 at `<target>/` (NOT under `.claude/` — survives cleanup); three action kinds (`safe_to_migrate`, `force_migrated` with `backup_path` under `.agentm-migrate-backup/`, `operator_edited_skipped` with optional `backup_collision: true` flag); atomic JSON write via tmp+replace; merge-on-rerun keyed by `(rel_path, kind)` tuple.

- **`scripts/migrate-to-user-scope.sh`** + **`scripts/migrate-to-user-scope.ps1`** — operator-facing CLI (bash ~265 LOC + pwsh twin ~280 LOC). Preview-by-default; full flag surface: positional `<target>` (default cwd) · `--apply` / `--rollback` / `--cleanup` (mutually exclusive) · `--force` · `--no-register` · `--registry-slug NAME` · `--agentm PATH` / `--crickets PATH` · `--yes` / `-y` · `--ci-override` · `--help`. Apply chain: classify → confirm (unless `--yes`) → `install_migrate apply` → `bash install.sh --scope user` (idempotent `~/.claude/` populate) → `repo_registry register <slug>` (unless `--no-register`; slug inferred from `<target>/.harness/project.json` `vault_project` / `slug` field, falls back to `basename`). **CI guard**: refuses when `$CI=true` env detected unless `--ci-override` passed — CI runners use per-project installs by design per locked DC-10. **4-state detection** inside both CLI scripts: `no-claude` / `pre-v4.3` / `explicit-project` / `already-user`; state 1+4 early-exit bypassed when `--rollback` or `--cleanup` is set.

- **`scripts/test_migrate_fixture.sh`** — end-to-end smoke runner (~200 LOC bash). `mktemp -d` fixture auto-cleaned via trap; populated from real source clones with 2 agents + 2 commands + 2 skill bundles + 1 hook bundle + 1 deliberately operator-edited file + 1 operator-only file. **Exercises 8 lifecycle steps**: preview · apply (skip operator_edited + unrecognized) · idempotent re-apply · rollback · `--apply --force` migrates with backup · rollback restores backup preserving operator-edit marker · fresh apply for cleanup setup · `--cleanup` removes empty install subdirs after shape-agnostic verification. All 8 steps pass locally. **Fixture-only per locked DC-8** — operator's 3 repos already migrated in plan 1 task 11; no mid-build operator-machine dogfood this plan.

- **`scripts/test_install_migrate.py`** — 26 unit tests covering classify (×6) · apply (×6) · rollback (×6) · cleanup (×4) · inverse-mapping round-trip (×2) · dotfile-noise SHA stability (×1) · backup-collision rerun semantics (×1). Brings project total to **212 unit tests** (186 baseline + 26 new).

- **`wiki/how-to/Use-Per-Project-Install.md`** — new Diátaxis how-to (NOTE block with Goal + Prereqs; numbered Steps). Documents when to deliberately stay on `--scope project`: CI runners (ephemeral environments); shared dev environments (multi-user host); multi-developer dotfiles patterns (per-repo `.claude/` checked into git). Step-by-step: invoke `bash install.sh --scope project <target>` explicitly; verify install-state.json shows `mode=project`; document the choice in project AGENTS.md to prevent future-operator reflex-migration.

- **`wiki/reference/Migration-Tool.md`** — new Diátaxis reference (Quick Reference table; tables-first). Full flag-by-flag for `migrate-to-user-scope.{sh,ps1}` · 4-state matrix · classification matrix with apply / apply-force behavior columns · `.agentm-migrate-record.json` schema v1 with field reference + action-kind table · exit code table.

- **`wiki/Home.md` + `wiki/_Sidebar.md`** updated with surface entries for both new pages.

### Changed

- **`lib/install/python/install_symlinks.py`** — `_symlink_targets_for_clone` renamed to public `symlink_targets_for_clone` (drop leading underscore + extended docstring). Single source of truth for the install-prefix ↔ source-clone mapping consumed by BOTH `install_symlinks.symlink_customizations` (forward direction) AND `install_migrate.inverse_mapping_for_clones` (inverse direction; computed at call time — no parallel table, two directions can never drift). Pure refactor; behavior identical.

- **`lib/install/.checksums.txt`** bumped 6 → 7 entries to include the new `python/install_migrate.py`. `bash scripts/sync-lib.sh` propagated byte-identical to crickets sibling (crickets stays at v2.1.0; lib parity preserved).

### Internal

- **4 defects caught + fixed pre-commit via 2 adversarial-reviewer passes** on `install_migrate.py`:
  1. **`cleanup()` walker shape-bias** (HIGH severity) — the per-classification walker `_walk_target()` only emits files matching known shapes (`.md` extension for agents/commands; dirs + `.md` for skills/hooks); cleanup inherited the blindness + silently `rmtree`'d operator-dropped `.py` / `.txt` / no-extension files. FIX: cleanup uses a shape-agnostic walk under each install subdir; ANY non-symlink, non-dotfile child refuses cleanup.
  2. **`_sha256_dir` polluted by macOS `.DS_Store`** (MEDIUM) — Finder sprinkles `.DS_Store` into visited directories; without filtering, every macOS user would see false `operator_edited` on dir bundles. FIX: dotfile-component skip on rel-path split.
  3. **`--apply --force` rerun silently overwrote backup + kept stale `target_sha_before`** (MEDIUM) — re-running force on the same `rel_path` overwrote `.agentm-migrate-backup/<rel>` with newer content while merge-dedup kept the original `target_sha_before`. Record became a lie + original unrecoverable. FIX: detect `backup_path.exists()` collision → record `operator_edited_skipped` with `backup_collision: true`; change dedup key to `(rel_path, kind)` tuple so distinct-kind re-attempts survive merge.
  4. **`rollback()` file-branch missing `dest.exists()` guard** (MEDIUM) — dir-branch refused to overwrite when dest existed; file-branch did not, silently clobbering operator content the user re-staged between apply + rollback. FIX: symmetric refusal across `safe_to_migrate` + `force_migrated` file-and-dir branches.

- **`HLD V4.6 subsection`** added to both crickets HLDs (`agent-memory-evolution.md` + `device-wide-architecture.md` v0.6 update history entry) — closes the V4 #30 trio narrative.

- **CHANGELOG cross-link convention preserved** — v4.5.0 references v4.4.0 (plan 2 of 3) + the v4.3.0 + crickets v2.1.0 pair (plan 1 of 3). V4.6 HLD subsection cross-references back to v4.5.0.

### Backward-compat

- **`--scope user` default does NOT flip this release** per locked DC-1. Operator default in `install.sh` stays `--scope project` after v4.5.0; the default-flip is queued for a separate v4.5.x or v4.6.x release. Smaller blast radius per release; operators who want plan 3's tooling can run `migrate-to-user-scope.sh` without surprise default change.
- **`--scope project` mode preserved as legitimate first-class install path** per locked DC-10 (carried forward from plan #22). The new `wiki/how-to/Use-Per-Project-Install.md` documents WHEN to deliberately keep this mode (CI runners; shared dev hosts; multi-developer dotfiles).
- **Migration tool is opt-in** — never auto-runs. Operators must explicitly invoke `bash scripts/migrate-to-user-scope.sh <target>` (preview by default; `--apply` to execute).
- **Reversibility is a release gate** per locked DC-2 — `--rollback` reverses every apply step from `.agentm-migrate-record.json`; mirrors V4 #26's `migrate-harness-to-vault.sh` pattern.

### Cross-references

- [Use-Per-Project-Install how-to](wiki/how-to/Use-Per-Project-Install.md) — when to deliberately keep `--scope project`
- [Migration-Tool reference](wiki/reference/Migration-Tool.md) — full CLI + schema documentation
- [HLD V4.6 — Migration tooling + opt-out docs](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/agent-memory-evolution.md#v4-release-milestones) — architectural arc + trio close-out
- [agentm v4.4.0](https://github.com/alexherrero/agentm/releases/tag/v4.4.0) — V4 #30 plan 2 of 3 (wiki I/O foundation; supports the new how-to + reference docs)
- [agentm v4.3.0](https://github.com/alexherrero/agentm/releases/tag/v4.3.0) + [crickets v2.1.0](https://github.com/alexherrero/crickets/releases/tag/v2.1.0) — V4 #30 plan 1 of 3 (foundation primitives reused via DC-7: `install_state` + `install_symlinks` + `install_copy` + `repo_registry`)
- ADR 0001 (stdlib-only Python preserved)
- ADR 0012 § 6 (dev-setup invisibility policy preserved)

### Deferred (out of scope for this plan)

- **`--scope user` default-flip in installer** (DC-1 lock) — separate v4.5.x or v4.6.x release. Smaller blast radius per release; can ship the tooling here + the flip when operator real-use validates.
- **Removing `--scope project` mode entirely** — DC-10 preservation; per-project mode stays as a legitimate first-class install path.
- **V4 #38 wiki bundle** — first sub-item of opinionated capability bundles meta; lands after V4 #30 trio close (this release). Pickup signal: *"let's build the wiki bundle"*.
- **Auto-migration on first session** — operator must run the migration tool explicitly. SessionStart auto-surface for "you have a pre-V4.3 install; run migrate-to-user-scope" deferred (could land as follow-up if operators surface real need).
- **Migration of pre-V4.0 installs** — those need `bash install.sh --update` to reach v4.x baseline first; migration tool assumes v4.x-shaped per-project install at start.
- **CI runner integration for `test_migrate_fixture.sh`** — fixture smoke is runnable as standalone CI step but not wired into `.github/workflows/` yet; can be added in a future plan if CI surface needs an explicit migrate-tool gate.

## [v4.4.0] — 2026-05-27 — Wiki I/O codification + cross-repo views

**MINOR.** ROADMAP-V4 item #30 (plan 2 of 3). Single-repo release; crickets unaffected at v2.1.0. Builds on plan 1's `repo_registry` vault-backed primitive to ship the wiki I/O foundation that V4 #38 wiki bundle (first sub-item of opinionated capability bundles meta) will later build on. Codifies what the agent reads/writes under `<repo>/wiki/` on top of the existing Diátaxis spec from ADR 0004; ships an operator-facing `wiki-author` skill that auto-triggers on imperative phrases ("update the wiki", "document this in the wiki", "update <slug>'s wiki" for cross-repo) + dispatches the existing `documenter` sub-agent under the new cross-repo write contract; ships `/recent-wiki-changes` slash command for cross-repo wiki visibility. See [HLD V4.5 subsection](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/agent-memory-evolution.md#v4-release-milestones) for the architectural arc.

### Added

- **`harness/skills/wiki-author/SKILL.md`** — new operator-facing dispatcher skill (v0.1.0; claude-code-only). Auto-fires on imperative wiki-write phrases; resolves cwd vs cross-repo via `repo_registry.list_repos()` from V4 #30 plan 1; loads per-repo `.diataxis-conventions.md` override if present; determines Diátaxis mode (preserve for existing pages; derive or ask for new pages); emits unified diff preview; dispatches the `documenter` sub-agent for the actual write under its hard-boundary scope. Pure SKILL.md instructions (no Python helper); matches lightweight `pii-scrubber` pattern. **5 trigger phrases**: "update the wiki" / "document this in the wiki" / "add a wiki page about X" / "update <slug>'s wiki" (cross-repo) / "create a how-to/reference/etc in the wiki for X" (mode hint). **5 non-triggers** explicitly documented in SKILL body: "the wiki page mentions" (descriptive) / "I saw it on Wikipedia" (unrelated) / "walk the wiki/ tree" (path reference) / "wiki articles vs documentation" (meta-discussion) / "docs in wiki/explanation" (observational). Trigger/non-trigger matrix lives in SKILL body for future-operator reviewability.

- **`scripts/recent-wiki-changes.{sh,ps1}`** — cross-repo "show me all my recent wiki changes" surface. Bash + pwsh twins; walks `repo_registry.list_repos()` for each registered repo's `<root>/wiki/`; finds files modified within `AGENTM_WIKI_RECENT_DAYS` (default 7 days; CLI overrides). Emits SLUG/MODE/PAGE/MODIFIED aligned table sorted by mtime desc. Mode classification via first path segment under wiki/ (tutorials/how-to/reference/explanation; "—" for top-level Home.md/_Sidebar.md). CLI flags: `--repo <slug>` filter, `--days N` override, `--limit N` (default 50), `--vault-path` override. Graceful-skip JSON marker when `MEMORY_VAULT_PATH` unset.

- **`/recent-wiki-changes` slash command** at `adapters/claude-code/commands/recent-wiki-changes.md` — Claude-code-only slash command surface for the script. Frontmatter description + body with usage examples + output format + graceful-skip cases + companion-surface cross-refs.

- **`scripts/check-parity.sh` extended** with `CANON_UTIL_COMMANDS=(recent-wiki-changes)` array. Utility slash commands are claude-code-only (not cross-host parity-enforced); Antigravity + Gemini operators invoke the underlying script directly. Pattern leaves room for future utility commands (e.g. list-plans may follow) without disturbing cross-host parity.

- **ADR 0004 Amendment 2026-05-27** — `wiki/explanation/decisions/0004-diataxis-documentation-spec.md` gains an Amendment section codifying 3 wiki I/O conventions on top of the original spec: (a) preview-before-write mandatory for ALL writes (per-repo + cross-repo; per-write gate, not per-batch); (b) per-repo `.diataxis-conventions.md` override honored; (c) cross-repo write target resolved via `repo_registry.list_repos()` from V4 #30 plan 1.

### Changed

- **`harness/agents/documenter.md`** extended with "Cross-repo write contract (V4 #30 plan 2 — 2026-05-27)" subsection under the existing "Write scope (hard boundary)" block. Three locked constraints when documenter writes to wiki/ in another registered repo: (1) target repo must be in `repo_registry.list_repos()`; (2) target wiki path = `<registered_root>/wiki/`; honors per-repo `.diataxis-conventions.md` override; (3) preview-before-write is mandatory PER cross-repo write (every edit gates on operator approval; not per-batch).

- **Plan #22 dogfood cleanup** (1 small commit ahead of v4.3.0): `git rm` of 8 tracked files in `.claude/{agents,commands}` that were rm'd from disk during the V4 #30 plan 1 task 11 mid-build dogfood but never committed. The customizations remain at their source (harness/agents/ + adapters/claude-code/) + symlinked into `~/.claude/` by the v4.3.0 `--scope user` installer.

### Internal

- **No crickets-side change** this release — wiki I/O contract + skill + cross-repo views all live in agentm post-V4 #36 reorg. Crickets stays at v2.1.0; lib/install/.checksums.txt unchanged (no new shared Python helpers).
- **No new unit tests** — `wiki-author` skill is pure SKILL.md instructions (no script to test); `recent-wiki-changes.{sh,ps1}` is end-to-end-smoke-validated against operator's real vault (no fixture-based unit tests needed for shell + walk-and-format logic).
- **CHANGELOG cross-link convention preserved** — v4.4.0 references v4.3.0's release page; the V4.5 HLD subsection references back to v4.4.0.

### Backward-compat

- **`wiki-author` skill is additive** — auto-trigger fires only on its 5 documented phrase patterns; existing operator workflows unaffected. Skill is `install_scope: user`; lands in `~/.claude/skills/` via v4.3.0's `--scope user` installer (or per-repo via legacy `--scope project`).
- **`/recent-wiki-changes` slash command is additive** — new surface; no existing slash command behavior changed.
- **documenter spec extension is additive** — original write-scope hard boundary (wiki/** + Home.md/_Sidebar.md/README.md/.diataxis + .harness/project.json at /setup) preserved; the new cross-repo subsection extends; doesn't replace.
- **ADR 0004 amendment is additive** — original spec preserved; the Amendment adds 3 conventions on top.
- **Existing documenter dispatches** (single-repo, no `wiki-author` skill involvement) unchanged.

### Cross-references

- [HLD V4.5 subsection](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/agent-memory-evolution.md#v4-release-milestones) — architectural arc.
- [device-wide-architecture v0.5](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/device-wide-architecture.md#lifecycle) — update history entry.
- [ADR 0004 Amendment 2026-05-27](https://github.com/alexherrero/agentm/blob/main/wiki/explanation/decisions/0004-diataxis-documentation-spec.md#amendment-2026-05-27) — codified conventions.
- [`documenter` sub-agent cross-repo write contract](https://github.com/alexherrero/agentm/blob/main/harness/agents/documenter.md) — write-scope hard boundary extension.
- [V4 #30 plan 1 (v4.3.0)](https://github.com/alexherrero/agentm/releases/tag/v4.3.0) — `repo_registry` primitive that this plan's cross-repo views walk.
- ROADMAP-V4 #38 — opinionated capability bundles meta-item; the wiki bundle (first sub-item) builds on this plan's I/O foundation; lands after plan 2 closes.
- ADR 0001 (stdlib-only Python — preserved; no third-party deps).
- ADR 0006 (crickets/agentm split — unchanged).

### Deferred

- **V4 #30 plan 3 of 3** — migration tooling for non-operator users + opt-out documentation (next per V4 execution order).
- **V4 #38 wiki bundle** — first sub-item of opinionated capability bundles meta; pickup signal: *"let's build the wiki bundle"*.
- **SessionStart auto-surface** for cross-repo wiki views — locked DC-2 on-demand-only this plan; defer if real-use signals real demand.
- **Real-time wiki file watcher** — mtime-on-walk + `/recent-wiki-changes` slash command is sufficient; ADR 0001 stdlib-only preserved (no `watchdog` / `inotify`).
- **`wiki-author` skill antigravity support** — v0.1.0 ships claude-code only; Antigravity skill-triggering semantics need to stabilize before extending `supported_hosts`.
- **Pwsh launcher + hook test coverage** — bash + Python primitives have full coverage; pwsh twin recently shipped + validated end-to-end but lacks dedicated unit tests.
- **Per-repo `.diataxis-conventions.md` operator-paced authoring** — file format documented in ADR 0004 amendment; operator writes per-repo as conventions surface real divergence between projects.

## [v4.3.0] — 2026-05-27 — Global install + `--scope user` (paired with crickets v2.1.0)

**MINOR.** ROADMAP-V4 item #30 (plan 1 of 3). Paired pair #12 with [crickets v2.1.0](https://github.com/alexherrero/crickets/releases/tag/v2.1.0). The first install-model overhaul: the per-project `<project>/.claude/{skills,hooks,agents,commands}/` footprint becomes optional (legacy `--scope project` mode); the new `--scope user` flag installs once to `~/.claude/` and every operator-repo on the device draws customizations from that shared location. Default scope stays `project` for v4.3.0 + v2.1.0 backward compat; flips to `user` in a future release once dogfood (this plan's task 11) validates the new path. The operator-stated insight from 2026-05-24: "the only thing repos need is to be aware of them and how to interact/write/read plans from them" — anything else (skills, hooks, agents, commands) can live globally. **Crickets paired** (toolkit-first ordering — crickets v2.1.0 ships first, agentm v4.3.0 references its release URL). See [HLD V4.4 subsection](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/agent-memory-evolution.md#v4-release-milestones) for the architectural arc.

### Added

- **`lib/install/python/` subdir** — 3 new cross-repo Python helpers byte-identical between agentm + crickets (sync via `scripts/sync-lib.sh`; parity enforced via `lib/install/.checksums.txt`):
  - `install_state.py` — probe canonical source-clone paths (`~/Antigravity/agentm` + `~/Antigravity/crickets` via `.git/`+`harness/` or `.git/`+`skills/` heuristic); persist `{version, mode, source_clones, installed_at, harness_version, installer_source?, installed_shas?, fragments?}` to `<install-prefix>/.agentm-install-state.json`. Atomic tmp+rename writes. CLI: `detect`/`persist`/`read`.
  - `install_symlinks.py` — source-mode primitive. Symlinks the customizations subset per locked DC-7: skill dirs (crickets/skills/, agentm/harness/skills/ + adapters/claude-code/skills/) + agent .md (crickets/agents/, agentm/harness/agents/ + adapters/claude-code/agents/) + command .md (agentm/adapters/claude-code/commands/) + hook bundles (crickets/hooks/ + agentm/harness/hooks/). Settings.json fragments + pre-push template stay as copies per DC-8. 5-state classification (absent / symlink-correct / symlink-wrong / symlink-broken / real-conflict); idempotent; `--force` for conflict replacement. Cross-platform: `os.symlink` first; Windows junction fallback via `cmd /c mklink /J` when symlink permission fails for directories.
  - `install_copy.py` — release-mode primitive. SHA256-aware copy with conservative divergence detection: byte-identical = skip; source-changed + target-matches-prior-install-sha = update; source-changed + target-also-diverged = conflict (skip with warn unless `--force`). Never silently overwrites operator-edited files.

- **`install.sh` + `install.ps1` `--scope user|project` dispatch** — when `--scope user`, install prefix = `$AGENTM_INSTALL_PREFIX` or `$HOME/.claude`; probes mode; source mode → `install_symlinks.py` with detected agentm + crickets clones; release mode → `install_copy.py` from this harness's own source tree; persists install-state with `installer_source` recorded; copies `templates/bin/agentm-update` launcher to `~/.local/bin/`; skips per-project install entirely. Backward-compat: `--scope project` (default) → existing per-project install unchanged.

- **`scripts/repo_registry.py`** — vault-backed registry primitive at `<vault>/_meta/repos.json`. Tracks operator's known agent-aware repos: `{slug, root_path, wiki_path?, harness_state_mode?}`. Cross-device-portable via POSIX path normalization (`Path(x).as_posix()`). Uses V4 #26 `safe_write_replace_style()` for atomic write + mtime-check concurrency. CLI subcommands: `list`/`register`/`unregister`. Graceful-skip when `MEMORY_VAULT_PATH` unset (exit 1 with skip-JSON; primitives raise `FileNotFoundError`).

- **`scripts/install_state_sync.py` + `harness/hooks/install-state-sync/` hook bundle** — SessionStart hook (claude-code, non-blocking). SHA256-digest-aware re-merge of settings.json fragments per `install-state.json`'s `fragments` field. Also runs release-mode upstream-version-check (release mode only — source operators get live updates via symlinks). Single hook covers both modes per locked DC-8 + auto-stay-in-sync semantics. Graceful-skip on missing state; exit 0 always (non-blocking contract).

- **`scripts/upstream_version_check.py`** — fetches latest GitHub release tags for `alexherrero/agentm` + `alexherrero/crickets` via stdlib `urllib.request` (no `requests` dep per ADR 0001). 24h cache at `<install-prefix>/.upstream-version-check-cache.json`. SemVer comparison + one-line stderr notice when newer version available. Never auto-applies per locked DC-3 — operator runs `agentm-update` explicitly. Uses `calendar.timegm()` for canonical UTC-parse (DST-safe).

- **`templates/bin/agentm-update` (+ .ps1 twin)** — global PATH launcher. Reads recorded `installer_source` from install-state.json; invokes installer with `--update --scope user`; pass-through args (`--force-version-check`, `--rollback`). Installed to `~/.local/bin/agentm-update` by `--scope user` installer.

- **crickets-sibling auto-detect in `install.sh` + `install.ps1`** (FOLLOWUPS-bundled; ~50 LOC): probes for `~/Antigravity/crickets/install.{sh,ps1}` + `~/.claude/skills/pii-scrubber/`; clones + dispatches crickets installer with matching `--scope` flag if neither found. `AGENTM_NO_CRICKETS_BOOTSTRAP=1` opt-out. Idempotent.

- **+78 new unit tests** in `scripts/test_harness_memory.py` across 12 new test classes (108 baseline → 186 total).

- **`feat-global-install-default` feature** in `features.json` (passes: false until `/release` flips it).

### Changed

- **`install.sh` + `install.ps1`** invoke `lib/install/python/install_state.py persist` at end-of-install (post version-record block). Silent best-effort; records install-state.json with `installer_source` for the `agentm-update` launcher to find later.

- **`scripts/check-no-pii.sh`** SELF_SKIP_PATHS includes `lib/install/.checksums.txt` (generated SHA256 file; hex substrings false-positive on phone-us regex).

- **Wiki sweep — dev-setup mentions** — agentm/README.md "Latest" callout + Status paragraph rephrased from "operator's three target repos (agentm + sherwood + dev-setup)" → "operator's target repos". ADR 0006 + Completed-Features.md historical entries preserved per FOLLOWUPS exemption.

### Internal

- **Helpers relocated to `lib/install/python/`** via `git mv` (was `scripts/`): `install_state.py`, `install_symlinks.py`, `install_copy.py`. `install_state_sync.py` + `upstream_version_check.py` stay in `scripts/` (agentm-side hook helpers; not used by crickets).

- **Cross-repo lib parity** — `scripts/sync-lib.sh` now propagates `lib/install/python/` byte-identically to crickets. `scripts/check-lib-parity.sh` verifies 6 files (was 3).

- **Mid-build dogfood findings from task 11** (operator-machine `--scope user` migration):
  1. `install_symlinks` mapping for agentm slug missed `harness/skills/` (4 dir bundles + 2 file skills) + `harness/hooks/` (7 dir bundles) — caught at real-vault smoke when `~/.claude/hooks/` had only 3 of 10 expected. Unit tests didn't catch because fixture vaults didn't include those paths (test-coverage gap deferred).
  2. Windows path-separator bug in `repo_registry.register_repo()` (`str(Path(...))` used native sep) — broke cross-device vault portability; switched to `Path(value).as_posix()`.
  3. Windows UNC-prefix bug in `install_symlinks._classify_existing()` — `Path.resolve()` returns `//?/C:/...` form on Windows symlinks; switched to `os.path.samefile()`.
  4. Bash-launcher + bash-hook unit tests failed on Windows CI (Git Bash) — marked `@unittest.skipIf(platform.system() == "Windows")`; pwsh twins exist but lack dedicated tests (follow-up).

- **Snapshot pattern transferred from V4 #26** — belt-and-braces `~/.claude.pre-v4-30-snapshot-<ts>` taken pre-migration on operator's machine (recovery never invoked but reduced risk-cost).

### Backward-compat

- **Default scope = `project`** for v4.3.0. Existing per-project install paths unchanged; existing operators see zero behavior change unless they explicitly run `--scope user`. The default flip is queued for a future release once real-use validates.

- **`install_state.persist_install_state()` schema** is additive — `installer_source`, `installed_shas`, `fragments` are all optional; absence preserves backward compat with pre-v4.3 install-state.json files.

- **Per-repo cleanup** during operator dogfood preserved `.claude/settings.json` + `.claude/settings.local.json` + `.harness/hooks/`. Only `.claude/{skills,agents,commands}` removed (these are file-discovery surfaces that fall back to user-scope `~/.claude/`).

### Cross-references

- Paired with [crickets v2.1.0](https://github.com/alexherrero/crickets/releases/tag/v2.1.0) (toolkit-first ordering — crickets shipped first).
- [HLD V4.4 subsection](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/agent-memory-evolution.md#v4-release-milestones) covers the architectural arc.
- [device-wide-architecture v0.4](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/device-wide-architecture.md#lifecycle) updates the device-wide HLD.
- ADR 0001 (stdlib-only Python — preserved; no third-party deps).
- ADR 0012 § 6 (dev-setup invisibility — preserved; the policy ADR + historical entries are exempt from the sweep).
- FOLLOWUPS 2026-05-27 — auto-stay-in-sync default-on (no `--dev` flag); background primitives reserved but unused this plan.

### Deferred

- **Full `--scope user` default flip** — v4.4.x or later, after real-use validates the new path.
- **Pwsh `-Scope user` dispatch in `crickets/install.ps1`** — minimal scaffold only this release. Full pwsh dispatch in follow-up if needed.
- **Settings.json hook-registration migration** to user-scope — per-repo `.harness/hooks/` references intact so safe to defer.
- **Pwsh launcher + hook test coverage** — bash twins have full coverage; pwsh ones lack dedicated tests.
- **Bundle-walk unit test fixture** — `install_symlinks` mapping gap surfaced only at real-vault smoke; unit-test fixture vaults missing `harness/skills/` + `harness/hooks/` paths.
- **Plan 2 of 3 (V4 #30)** — wiki I/O codification + cross-repo views.
- **Plan 3 of 3 (V4 #30)** — migration tooling for non-operator users.

## [v4.2.0] — 2026-05-27 — Vec-index drift detection + workflow-uses-vault completion

**MINOR.** ROADMAP-V4 item #37. Closes two coupled gaps surfaced by the 2026-05-27 adversarial review of the memory skill's `sqlite-vec` + markdown architecture and by plan #20 task 7's scope adjustment. **(A) Silent vec-index drift**: when an operator edited a `.md` file directly in Obsidian (not via `/memory save` or `/evolve`), the vec-index drifted silently — grep-side recall stayed current (re-reads files at query time) but vec-side returned stale semantic matches pointing at old content. This release adds opportunistic mtime-check at recall time, a `vec_index.py full-sync` operator-invoked drift sweep, and `memory-reflect-idle` hook integration so drift-detection rides the existing idle cooldown. **(B) Workflow-uses-vault gap**: plan #20 migrated state files to `<vault>/projects/<slug>/_harness/` but slash commands + phase specs still pointed at legacy `<project>/.harness/<file>` paths. This release exposes `harness_memory.py` dispatcher subcommands (`read-state` / `write-state` / `vault-state-path`) + rewrites all 6 phase specs to invoke them explicitly + cleans up legacy `.harness/` state files on operator-target repos. The pieces couple because (A) drift-detection only matters once (B) the workflow actually reads from the live vault. **Backward-compat preserved as a release gate**: pre-#37 indexes migrate transparently (ALTER TABLE ADD COLUMN; one-line stderr notice; idempotent); pre-#37 indexes work after migration without operator action. **Crickets unaffected** (stays at v2.0.0). Single-repo release. Cross-references the V5 design (V5-10 chunking + V5-11 SQL hybrid + V5-12 time-weighted retrieval) that this V4 mitigation complements — the V5 layer formalizes hybrid retrieval, this V4 drift detector keeps the index honest while V5 ships. See plan #20 task 7 scope-adjustment narrative for the workflow-uses-vault gap origin.

### Added

- **`vec_index.py` schema extension** — `entry_meta` companion table gains `indexed_at INTEGER NOT NULL DEFAULT 0` (Unix epoch seconds; populated at drain time via `int(time.time())` alongside the existing `updated_at` ISO TEXT). `_has_column()` PRAGMA helper + `_migrate_pre_v37()` ALTER TABLE migrator (idempotent; one-line stderr notice on first migration); `_open_index()` calls migration after CREATE TABLE IF NOT EXISTS. `rebuild_index()` CREATE TABLE also includes the column for full-rebuild consistency.

- **`vec_index.py` drift primitives** — `is_entry_drifted(vault_path, entry_relative, db_path=None) -> bool` reads source mtime + sqlite indexed_at; returns True if mtime > indexed_at + 1s tolerance OR row missing OR source file missing OR sqlite stat-fail; False if matched OR sqlite-vec unavailable (graceful-skip). `find_drifted_entries(vault_path) -> dict` returns `{drifted, up_to_date, not_indexed}` lists; walks `personal-private/`, projects-dir (V4 #26 dual-path), `_idea-incubator/`; excludes `_archive/` dirs + `PLAN.archive.*.md` + `_meta/`. `_extract_embed_text_from_file()` inline-parses YAML frontmatter (slug + tags) + 500-char body — mirrors save.py's `{slug} [tags]\n\n{first_para}` format for re-embed consistency.

- **`vec_index.py full-sync [--rebuild]` subcommand** — operator-invoked drift sweep. Default: reports summary JSON (drifted / up-to-date / not-indexed counts + lists). `--rebuild`: enqueues drifted + not-indexed entries to `embedding-queue.jsonl` (existing drain path consumes). Graceful-skip when sqlite-vec absent (everything appears not_indexed; enqueue still works since queue is JSONL append).

- **`recall.py` per-hit drift-check** — `_drift_check_vec_hits()` helper between `_vec_search()` and merge step. For each vec-result path: lazy-imports vec_index; calls `is_entry_drifted()`; on drift → enqueues for re-embed + drops the entry from vec results so merge uses keyword-only (grep-only) score per locked DC-3. Budget-aware: aborts if deadline elapses mid-pass + emits transparency line. Single `[recall] N entries flagged for re-embed` stderr line if any drift detected. Defensive try/except never breaks recall on drift-check failure.

- **`memory-reflect-idle` hook drift sweep** (bash + pwsh twins) — runs `vec_index.py full-sync` (read-only; no `--rebuild`) on the existing idle-pass cooldown. Captures JSON summary; emits stderr transparency line `[memory-reflect-idle] vec-index drift sweep: <N> drifted + <M> not-indexed (run vec_index.py full-sync --rebuild to enqueue for re-embed)` when drift is detected. Graceful-skip on missing `MEMORY_VAULT_PATH` / `vec_index.py` / sqlite-vec / JSON parse fail; never blocks the idle-pass.

- **`scripts/harness_memory.py` CLI subcommands** — `read-state <filename>` (resolves project from cwd; reads via dispatcher with vault-first / legacy-fallback / one-warn semantics; emits to stdout; exit 0 always per graceful-silent contract); `write-state <filename> [--content-file -]` (reads stdin or file; resolves project; writes via dispatcher honoring `.project-mode=local` opt-out; emits written path); `vault-state-path <filename>` (resolves project; emits resolved path; exit 1 if no resolution; for shell-level path computation).

- **6 phase specs strengthened** — all of `harness/phases/01-setup.md` through `05-release.md` + `harness/pipelines/bugfix.md` updated the `> [!NOTE]` resolver-callout (added in plan #20 task 7) with explicit dispatcher CLI invocations: `python3 scripts/harness_memory.py read-state PLAN.md` for reads, `echo "$CONTENT" | python3 scripts/harness_memory.py write-state PLAN.md` for writes, `python3 scripts/harness_memory.py vault-state-path PLAN.md` for path resolution. Each spec's callout tailored to its phase context.

- **31 new unit tests** in `scripts/test_harness_memory.py` across 6 new classes — TestVecIndexSchemaMigration × 6 + TestIsEntryDrifted × 6 + TestFindDriftedEntries × 8 + TestExtractEmbedTextFromFile × 4 + TestFullSync × 3 + TestDriftCheckVecHits × 5 + 5 new TestCLI cases for the dispatcher subcommands. Uses `_MockConn` stand-in to bypass the sqlite-vec extension load for entry_meta-only tests (drift primitives only touch the companion table, not vec0). Total: 108 tests in 0.388s.

### Changed

- **`recall.py` query path** may surface fewer vec-derived hits when entries are detected as drifted — drifted entries drop their vec score + the grep-keyword score takes over for that hit. Net effect: stale-content matches replaced with current-content matches via grep-side re-read. Per-query overhead bounded by recall budget.

- **`memory-reflect-idle` transparency line** gains drift-count surfacing. When the idle-pass sees drifted-or-not-indexed entries, an additional stderr line surfaces the count + the `--rebuild` recipe. Pre-#37 idle passes were silent on drift; post-#37 they have visibility.

- **Cleanup of legacy `<repo>/.harness/<file>` paths** on operator's three target repos (agentm + sherwood + dev-setup). After byte-identical-or-sync confirmation against vault canonical, the legacy state files were removed; legacy `.harness/` directories retain only operator-preserved files (`designs/`, `hooks/`, `scripts/`, pre-V4 archives) + `.evidence-reads` per DC-1. Workflow-uses-vault verified operational end-to-end via `vault-state-path PLAN.md` from each repo. Belt-and-braces snapshot `~/Antigravity/agentm/.harness.pre-v4-26-20260527/` preserved as rollback option.

### Internal

- **Pre-#37 schema migration is transparent** — first read/write against a pre-#37 vec-index triggers `_migrate_pre_v37()` ALTER TABLE ADD COLUMN automatically; one-line stderr notice; idempotent on re-run. Operator sees it once per vault. Existing rows get `indexed_at=0` default → appear "drifted" until next drain refreshes via natural drift → enqueue → drain flow.

- **DC-2 design call refined mid-implementation** — original plan locked "full rebuild" for pre-#37 migration; refined to ALTER TABLE ADD COLUMN as gentler path preserving embeddings. Same eventual outcome via natural drift → enqueue → drain.

- **Phase-spec rewrite scope adjustment** — original plan called for mass-sed-replace of 82 bare-`Read .harness/<file>` references across specs; pragmatic call to strengthen the existing resolver-semantics callouts (from plan #20 task 7) with explicit CLI examples instead — avoids bloating docs while directing the agent to the dispatcher.

- **Drift-check via lazy `import vec_index`** in `recall.py` required `sys.modules["vec_index"] = vec_index` registration in tests so mocks propagate to the helper's lookup.

### Backward-compat

- **Pre-#37 indexes auto-migrate transparently** — no operator action required. ALTER TABLE ADD COLUMN preserves existing embeddings + rowids; the `indexed_at=0` default makes existing rows appear drifted, which queues them for re-embed via the natural drift → drain flow. No tear-down + rebuild required.

- **`recall.py` drift-check is best-effort + non-breaking** — defensive try/except wraps the helper; on any drift-check failure (sqlite-vec import error, stat-fail, queue-write error) the original vec results are served unmodified.

- **`memory-reflect-idle` drift sweep is non-blocking + graceful-skip** — survives missing `MEMORY_VAULT_PATH`, missing `vec_index.py`, missing sqlite-vec, malformed JSON output. Idle-pass continues regardless of drift-sweep outcome.

- **Workflow-uses-vault cleanup** preserves legacy `<repo>/.harness/.evidence-reads` per DC-1 + any operator-preserved subdirectories (`designs/`, `hooks/`, `scripts/`). Cleanup is opt-in via `--cleanup` flag with byte-identical-or-conflict-abort default.

### Cross-references

- **HLD § "V4 release milestones"** at `crickets/wiki/explanation/designs/agent-memory-evolution.md` — V4.3 entry covers state migration (foundation for #37); no V4.4 subsection per `[[hld-evolution-update-on-major-release]]` (small additive MINOR; not architecturally load-bearing enough to warrant a new subsection — workflow-uses-vault closes a known gap from plan #20 task 7, not a new architectural call).

- **V5 design** (V5-10 chunking + V5-11 SQL hybrid + V5-12 time-weighted retrieval) that this V4 mitigation complements — V5 ships hybrid retrieval as a formalized layer; this V4 drift detector keeps the per-entry vector grain honest while V5 ships.

- **Plan #20 task 7 scope-adjustment narrative** — the workflow-uses-vault gap origin; plan #21 closes it by exposing the dispatcher via CLI + rewriting the phase specs.

- **2026-05-27 adversarial review** — surfaced the silent-drift gap; ROADMAP-V4 #37 added post-review.

### Deferred

- **Real-time file watcher** (`watchdog` / `pyinotify` / `fswatch`) — mtime-on-recall + idle-pass sweep is sufficient + cheaper. ADR 0001 stdlib-only Python constraint preserved. Real watcher deferred until measurable insufficiency.

- **Cross-device sync conflict resolution** — V4 #26 ships detection + the conflict-merger SessionStart hook; resolution stays operator-judgment per locked plan #20 scope.

- **Metadata-only frontmatter changes** that don't shift embeddings meaningfully — re-embed unconditionally on mtime drift; cost negligible at current vault size. Optimization deferred until measurable need.

- **V5-10 chunking + V5-11 SQL hybrid + V5-12 time-weighted retrieval** — V5 work.

- **V4 #30 global install** — next per V4 execution order: #37 ✅ → **#30** → #35 → #32 → #33 → #34 → #25 → #16.

## [v4.1.0] — 2026-05-27 — Vault-backed harness state + folder rename `personal-projects/` → `projects/`

**MINOR.** ROADMAP-V4 item #26. The first BUILD on top of the V4.0.0 reorganization. Per-project harness state — `PLAN.md`, `progress.md`, the four `ROADMAP*.md` files (slim + V4 + V5 + V6), `FOLLOWUPS.md`, `features.json`, `init.sh`, `known-migrations.md`, `verify.{sh,ps1}`, `.promoted-progress-cursor`, archived plans (`PLAN.archive.YYYYMMDD-*.md`), `designs/` subtree, and the deprecated `project.json` — all relocate from `<project>/.harness/` to `<vault>/projects/<slug>/_harness/`. The vault top-level folder `personal-projects/` renames to `projects/` in the same release. Backward-compat preserved as a release gate: legacy `<project>/.harness/<file>` reads still work via the resolver chain's tier-2 fallback with a one-warn-per-session-per-file deprecation notice; writes go only to vault unless `<vault>/projects/<slug>/_harness/.project-mode` reads `local` (the operator-opt-out escape hatch for reversibility). The reorg is *additive* — no breaking changes for v4.0.0 operators. **Crickets unaffected** (stays at v2.0.0). Single-repo release. See [HLD V4.3 subsections](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/agent-memory-evolution.md#v4-release-milestones) for the architectural arc.

### Added

- **`scripts/harness_memory.py`** — new resolver chain + dispatcher primitives:
  - `_vault_projects_dir(vault)` — dual-path helper preferring `<vault>/projects/`, falling back to `<vault>/personal-projects/` during the transition window.
  - `resolve_project(context)` → `{slug, vault_path, project_root, layout}` where `layout ∈ {"new", "legacy", "none"}` lets downstream callers decide warn-once behavior.
  - `vault_state_path(resolution, filename)` — pure path-construction; returns `<vault>/projects/<slug>/_harness/<file>` from a resolution dict.
  - `read_state_file(resolution, filename)` — tries vault first; falls back to legacy `<project>/.harness/<file>` with a one-warn-per-session-per-file deprecation notice; honors `.project-mode=local` opt-out per locked DC-3.
  - `write_state_file(resolution, filename, content)` — atomic tmp+rename to vault path; raises `ValueError` if resolution lacks vault_path; honors `.project-mode=local` redirect to legacy.
  - `warn_once(filename, source)` — module-level session-scoped set; idempotent stderr emission.
  - `safe_write_replace_style(path, new_content, *, expected_mtime=None)` — atomic write with optional concurrent-modification check; raises `ConcurrentModificationError` when mtime drifts.
  - `detect_conflict_files(vault_root)` — walks vault for GDrive `(conflicted copy …)` markers; returns `[{conflict, base, rel}]` list. Companion regex helper strips marker variants (with/without device suffix; with/without "from <device>") to infer the canonical base path.

- **`harness/hooks/conflict-merger-session-start/`** — new SessionStart hook (claude-code-only, non-blocking). Detects GDrive conflict files on session boot; surfaces operator-facing notice on stderr per pair; never freezes session start. Configurable via `HARNESS_CONFLICT_MERGER_MODE` env (`interactive` / `silent` / `off`).

- **`scripts/rename-vault-personal-projects.{sh,ps1}`** — vault-side renamer. `mv personal-projects/ projects/` + portable Python-driven in-place sed sweep across `_always-load/` entries, `_idea-incubator/` entries (wikilinks + forward-looking promotion destinations), `personal-private/**/*.md`, and project-tree `_index.md` frontmatter `group:` fields + wikilinks. `_meta/` deliberately excluded (historical narrative preserved). `--preview` mode; idempotent; post-run integrity check.

- **`scripts/migrate-harness-to-vault.{sh,ps1}`** — per-project state migration. Resolves slug via `vault_project.py`; layout-aware; per-file conflict detection (byte-identical → skip; different content → WARN + don't overwrite + advise operator-merge); writes `.migrated-from-pre-v4.1` marker with full provenance; sets `.project-mode=vault`; `--preview` / `--cleanup` / `--rollback` / `--yes` flags. Cleanup preserves `.evidence-reads` per DC-1 (runtime ephemeral).

- **`scripts/list-plans.{sh,ps1}`** — cross-repo convenience surface. Walks `<vault>/projects/*/_harness/PLAN.md`; parses title + Status + mtime; renders one-row-per-project table. Default shows planning + in-progress; `--all` includes done + complete. The "show me all in-flight plans" UX item from the V4 #26 design conversation.

- **23 new unit tests** in `scripts/test_harness_memory.py` across 5 test classes (TestVaultProjectsDir × 4, TestResolveProject × 5, TestVaultStatePath × 4, TestReadStateFile × 6, TestWriteStateFile × 4, TestSafeWriteReplaceStyle × 7, TestDetectConflictFiles × 8). Total: 71 tests in 0.24s.

### Changed

- **All 5 phase specs + `pipelines/bugfix.md`** gain a `> [!NOTE]` callout near the top declaring the resolver chain. Phase specs describe WHAT to read/write (logical file shortnames like `PLAN.md`); the dispatcher decides WHERE. Inline `.harness/<file>` path references survive as factual filename markers. 04-review's callout notes read-only semantics; 05-release's notes that CHANGELOG.md stays per-repo (publicly shipped, not state).

- **Folder-rename sweep** propagated through harness scripts + memory skill body + memory scripts + sub-agent specs + operator-facing docs. Residual `personal-projects/` references survive only as: (1) the `_VAULT_PROJECTS_REL_LEGACY` constant + dual-path resolver code; (2) backward-compat / pre-rename / legacy-alias narrative annotations; (3) the test fixture for the legacy resolver path. Historical CHANGELOG + PLAN.archive entries deliberately preserved.

### Internal

- **`harness/scripts/install-plugin.sh`** path references updated post-V4-#36 — `SRC_PLUGINS` now resolves to `$HARNESS_ROOT/harness/plugins`.
- **Pre-v3.1.0-rename leftover discovered** during dogfood: `agentm/.harness/project.json` carried `github.repo: alexherrero/agentic-harness` from the agentic-harness → agentm rename. Corrected to `alexherrero/agentm` + added explicit `vault_project: agentm` to avoid tier-2 ambiguity. Logged as a known-issue for operators upgrading from very-old installs: any `project.json` with a `github.repo` field that doesn't match the current GitHub repo name should be updated before running `migrate-harness-to-vault.sh`.

### Migration

For v4.0.0 → v4.1.0 operators with `<project>/.harness/<files>`:

```bash
# One-time vault rename (run from anywhere; uses MEMORY_VAULT_PATH env):
bash ~/Antigravity/agentm/scripts/rename-vault-personal-projects.sh --preview  # review
bash ~/Antigravity/agentm/scripts/rename-vault-personal-projects.sh             # live

# Per-project state migration:
bash ~/Antigravity/agentm/scripts/migrate-harness-to-vault.sh ~/path/to/repo

# Verify:
ls "$MEMORY_VAULT_PATH/projects/<slug>/_harness/"

# Optional cleanup once verified:
bash ~/Antigravity/agentm/scripts/migrate-harness-to-vault.sh --cleanup ~/path/to/repo

# Rollback at any time:
bash ~/Antigravity/agentm/scripts/migrate-harness-to-vault.sh --rollback ~/path/to/repo
```

Operators who pulled v4.0.0 but haven't run any migration command continue working unchanged — the resolver chain falls back to legacy `<project>/.harness/<file>` paths with a one-warn-per-session-per-file deprecation notice.

### Cross-references

- **HLD updates:**
  - [agent-memory-evolution.md § V4 release milestones](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/agent-memory-evolution.md#v4-release-milestones) — V4.3 subsection.
  - [device-wide-architecture.md § Update history](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/device-wide-architecture.md) — v0.3 entry.
- **Plan:** `.harness/PLAN.md` plan #20 (now vault-backed at `<vault>/projects/agentm/_harness/PLAN.md`).
- **Locked design calls** in plan #20: DC-1 (`.evidence-reads` stays per-cwd); DC-2 (warn-once-per-session-per-file); DC-3 (`.project-mode` lives vault-side at `<vault>/projects/<slug>/_harness/.project-mode`); DC-4 (marker file `.migrated-from-pre-v4.1` matches semver boundary).
- **Dogfood findings (plan #20 task 9):** 3 bugs caught + fixed mid-session (preview-sweep gap; `_idea-incubator/` wikilinks; stale `github.repo` in agentm's project.json). 6 watchlist items deferred to operator's real-use sessions post-release.

### Deferred to subsequent v4.x releases

- **Hard-cut deprecation** of legacy `<project>/.harness/` paths — ships at the eventual deprecation release (likely v4.2.0 or v4.3.0) once migration is fully dogfooded across operator's vault for ~weeks of real use.
- **V4 #37** (vec-index drift detection) — next plan after #26 closes; benefits from operating against migrated state.
- **V4 #30** (global-install harness + `--scope user` default + auto-stay-in-sync) — follows #37.
- **Mobile-readable summary head on `progress.md`** — deferred until real-use measurement confirms it's needed.
- **Recall-exclusion patterns for `<vault>/projects/<slug>/_harness/**`** — deferred until real `/memory search` against post-migration vault surfaces measurable noise.

---

## [v4.0.0] — 2026-05-27 — V4 device-wide era opens: compound surface absorbed from Crickets

**MAJOR.** V4 #36 reorganization. AgentM absorbs the compound skills (`memory`, `design`, `diataxis-author`, `ship-release`), the four memory hooks (`memory-recall-session-start`, `memory-recall-prompt-submit`, `memory-reflect-stop`, `memory-reflect-idle`), the `evidence-tracker` hook, the `memory-idea-researcher` sub-agent, and the `plugins/` tree (including `example-plugin` and the `install-plugin.sh` user-global plugin installer) — all of which previously shipped from Crickets v1.x. Per [ADR 0012](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0012-device-wide-by-default.md) (device-wide-by-default), AgentM is now the canonical home for agentic-memory primitives + compound flows that turn the harness into a learning environment; Crickets narrows to base primitives universal to any project. Paired with **Crickets v2.0.0** — see the [Crickets v2.0.0 release notes](https://github.com/alexherrero/crickets/releases/tag/v2.0.0). This release **opens the V4 era**: subsequent v4.x builds (state migration, global install scope, auto-detect bootstrap, vault-context documenter, etc.) operate against this cleanly-bounded repo layout.

### Added

- **`harness/skills/memory/`** — 14-script compound skill (`save.py`, `evolve.py`, `recall.py`, `reflect.py`, `embed.py`, `vec_index.py`, `permeable_boundary.py`, `ideas_surface.py`, `ideas_incubator.py`, `ideas_promote.py`, `index_skills.py`, `discover_skills.py`, `adapt_skills.py`, `watchlist_review.py`). The AgentM memory skill itself — `/memory save` / `evolve` / `reflect` / `search` / `index-skills` / `discover-skills` / `adapt-skills` / `watchlist` / `promote`.
- **`harness/skills/design/`** — Human-facing 10-section design pipeline → agent execution handoff.
- **`harness/skills/diataxis-author/`** — Diátaxis-wiki authoring + maintenance (5 sub-commands).
- **`harness/skills/ship-release/`** — Semver-driven release-cutting skill.
- **`harness/hooks/memory-recall-session-start/`** — SessionStart event: load always-load vault entries (~500ms budget).
- **`harness/hooks/memory-recall-prompt-submit/`** — UserPromptSubmit: keyword + vector recall (~300ms; never blocks).
- **`harness/hooks/memory-reflect-stop/`** — Stop event: mine session for durable-knowledge candidates; auto-route HIGH to canonical, MEDIUM/LOW + ideas to `_inbox/`.
- **`harness/hooks/memory-reflect-idle/`** — Crash-recovery for orphan reflection markers from previous sessions.
- **`harness/hooks/evidence-tracker/`** — Default-FAIL evidence enforcement on `/work` task closeouts. Blocks `[ ]` → `[x]` flips without prior evidence reads.
- **`harness/agents/memory-idea-researcher.md`** — Deep-research sub-agent for `_idea-incubator/` skeletons.
- **`harness/plugins/example-plugin/`** — Reference Antigravity 2.0 plugin (`plugin.md` manifest + nested `skills/<n>/SKILL.md`).
- **`scripts/install-plugin.sh`** — User-global plugin installer (target: `~/.gemini/config/plugins/<n>/`). Modes: install, `--uninstall`, `--list`. Moved from `crickets/scripts/` in this release.
- **`requirements.txt`** — `pyyaml` + `sqlite-vec` + `sentence-transformers`. The memory skill's embedding stack. Installed by `install.sh` / `install.ps1` by default; opt out with `--no-python-deps`.
- **`scripts/manifest-info.py` + `scripts/merge-settings-fragment.py`** — Helper scripts copied from Crickets. The installer dispatcher invokes `merge-settings-fragment.py` to idempotently merge hook settings fragments into `.claude/settings.json`.

### Changed

- **Installer surgery (`install.sh` + `install.ps1`)** — new manifest-walking dispatcher block that walks `harness/skills/<dir>/SKILL.md`, `harness/hooks/<dir>/hook.md`, `harness/agents/<file>.md`. Reads `kind:` + `supported_hosts:` from YAML frontmatter (awk in bash; line-walk in pwsh — no pyyaml dep at install time). claude-code skills → `.claude/skills/<n>/`; antigravity → `.agents/skills/<n>/`. claude-code hooks → `.claude/hooks/<n>.sh` (+ `.py` helpers like `evidence_tracker.py`). Antigravity has no first-class hook surface per [ADR 0009](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0009-evidence-tracker-hook.md) — silently no-op'd. Legacy single-file agentm skills (`doctor.md`, `migrate-to-diataxis.md`) and legacy sub-agents (`adversarial-reviewer.md` etc.) without frontmatter flow through the existing `adapters/` pipeline; the dispatcher only operates on crickets-shape manifests.
- **`BOUNDARY_ROOTS`** extended with `harness/{skills,hooks,agents}` so the primitives lib's `ensure_boundary_src` accepts the new sources.
- **`MANAGED_PARENTS`** extended with `.claude/hooks` so `--update` wipes the new dir before recreate.
- **`.gitignore`** — adds `__pycache__/` + `*.pyc`. The reorg import accidentally bundled two `.pyc` files from a prior local test run; deleted in this release + ignored going forward.

### Internal

- **`harness/skills/memory/scripts/__pycache__/*.pyc`** — deleted (bundled-by-accident in the V4 #36 import commit; now gitignored).
- **`scripts/install-plugin.sh`** — path references updated post-relocation: `SRC_PLUGINS` now resolves to `$HARNESS_ROOT/harness/plugins` (was `$TOOLKIT_ROOT/plugins`); banners say "agentm plugins" not "crickets plugins"; provenance note in top comment.
- **HLD V4.2 subsections added** to both `crickets/wiki/explanation/designs/agent-memory-evolution.md` (Architecture § "V4 release milestones") and `crickets/wiki/explanation/designs/device-wide-architecture.md` (Lifecycle § v0.2). Both HLDs are cross-linked from this CHANGELOG entry.
- **CI hotfix** — `pii-guardrails` job's `actions/checkout@v4` step bumped to `fetch-depth: 0`. gitleaks's parent-walk (`git log <base>^..<head>`) was failing on the default shallow checkout.

### Migration

**For v3.x users:** No vault-side migration required. Re-install agentm + crickets:

```bash
cd ~/Antigravity/agentm && git pull && git checkout v4.0.0
cd ~/Antigravity/crickets && git pull && git checkout v2.0.0
bash ~/Antigravity/crickets/install.sh <target-project>
bash ~/Antigravity/agentm/install.sh <target-project>
```

Crickets ships first (base primitives + 3 evaluator sub-agents + 3 operator-control hooks); AgentM ships second (compound skills + memory hooks + memory-idea-researcher + plugins layer). The compound skills + memory hooks land at the same `.claude/skills/`, `.claude/hooks/`, `.agents/skills/` destinations Crickets v1.x used; your vault content is untouched.

**Legacy `<project>/.harness/` paths stay supported** in v4.0.0. The hard-cut deprecation (vault-as-canonical-context: `<vault>/projects/<slug>/_harness/`) moves to whichever v4.x release ships state migration (ROADMAP-V4 #26). Deprecation banners + read fallback stay in v4.0.0.

**Plugin users (`install-plugin.sh`):** the script moved from `crickets/scripts/` to `agentm/scripts/`. Update any local automation that referenced the old path.

### Cross-references

- **Paired sibling release:** [Crickets v2.0.0](https://github.com/alexherrero/crickets/releases/tag/v2.0.0) — Catalog narrowed to base primitives.
- **HLD updates:**
  - [agent-memory-evolution.md § V4 release milestones](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/agent-memory-evolution.md) — V4.1 + V4.2 retroactively captured per `hld-evolution-update-on-major-release`.
  - [device-wide-architecture.md § Lifecycle v0.2](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/device-wide-architecture.md) — V4.2 documented as the foundational V4 build.
- **ADRs referenced:** [ADR 0012 — device-wide-by-default](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0012-device-wide-by-default.md) (locked the decision; this release implements it on the AgentM side).
- **Plan:** `.harness/PLAN.md` plan #19 — coordinated paired release pair #11. Toolkit-first per `coordinated-release-order`.

### Deferred to subsequent v4.x releases

- **State migration** + hard-cut deprecation of `<project>/.harness/` paths — ROADMAP-V4 #26 (the next BUILD plan).
- **Global install scope** (`--scope user`, ~/.claude/`) — ROADMAP-V4 #30.
- **Auto-detect bootstrap** (first-conversation-detection in a project that hasn't been configured) — ROADMAP-V4 #32.
- **Documenter vault-context resolution** (resolver chain for which vault dir docs draft from) — ROADMAP-V4 #35.
- **Vault folder rename** `personal-projects/` → `projects/` — ROADMAP-V4 #26 (paired with the state migration).
- **First-run vault detection in installer** — ROADMAP-V4 #30.
- **Crickets-sibling auto-detect + clone** in agentm installer — operator-friendly UX nicety; not a correctness gap. Currently both repos require independent installer invocations.

---

## [v3.2.0] — 2026-05-25 — Doctor probes for Antigravity 2.0 + Antigravity CLI primitives

Minor — **harness-side doctor probes for the new Antigravity 2.0 + Antigravity CLI (`agy`) host surface**. Paired with `crickets` v1.2.0 (the toolkit-side support) — see [crickets v1.2.0 release notes](https://github.com/alexherrero/crickets/releases/tag/v1.2.0). Together with crickets v1.2.0, ships ahead of the 2026-06-18 Gemini CLI consumer sunset.

### Changed

- **`harness/skills/doctor.md`** — added 3 new live probes for Antigravity 2.0:
  - **Probe 7**: agy v1.0.2+ discoverability (`agy --version` + `~/.gemini/config/plugins/` exists).
  - **Probe 8**: skill discovery at `.agents/skills/` (plural — v1.2.0 crickets convention per crickets ADR 0011). Detects the v1.0.x `.agent/` (singular) path as a migration trigger and emits a clear fail signal pointing at `bash install.sh --update <project>`.
  - **Probe 9**: plugin discovery via `install-plugin.sh --list` + `plugin.json` validation against the standard schema (matches the 5 official Google-shipped plugins observed in `~/.gemini/config/plugins/`).
- **Adapter detection** for Antigravity updated from `.agent/workflows/` → `.agents/workflows/` (plural) per the v1.2.0 path migration. Adapter table now reads "Antigravity 2.0 / agy CLI".
- **Invocation table** updated: removed the legacy Gemini row; Antigravity 2.0 + agy share a single invocation path reading from `.agents/skills/doctor/SKILL.md`.

### Cross-references

- Paired sibling release: [crickets v1.2.0](https://github.com/alexherrero/crickets/releases/tag/v1.2.0) — `kind: plugin` + `.agents/` (plural) dispatch + Antigravity 2.0 host support.
- Plan #16 task 15 — doctor probe additions (`agentm/.harness/PLAN.md` operator-local).
- [crickets ADR 0011](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0011-antigravity-2-host-support.md) — the host-support decision driving these probes.

### Why the small footprint

The Antigravity 2.0 customization surface is owned by `crickets` (the toolkit ships into target projects); the harness's role is to verify customizations are wired up correctly. The doctor skill is the natural integration point — 3 new probes cover the new surfaces. No phase-spec changes; no adapter changes (the harness's own adapter dirs already follow the right convention via the underlying installer); no breaking changes.

## [v3.1.0] — 2026-05-25 — Repo rename agentic-harness → agentm + cross-ref sweep

Minor — **repo rename release**. The GitHub repo for this project is now `alexherrero/agentm` (was `alexherrero/agentic-harness`). Brand name (AgentM) was always the operator-facing label; the rename brings the URL slug + clone path in line with the brand. Paired with `alexherrero/crickets` v1.1.0 (the customization toolkit, was agent-toolkit) — see [crickets v1.1.0 release notes](https://github.com/alexherrero/crickets/releases/tag/v1.1.0).

GitHub installs HTTP redirects from the old URLs to the new ones automatically — existing clones, links, and bookmarks keep working without action. New clones should use `https://github.com/alexherrero/agentm.git`.

### Changed

- **Repo URL** github.com/alexherrero/agentic-harness → github.com/alexherrero/agentm. Old URL 301-redirects to new permanently.
- **Recommended local sibling-clone path** ~/Antigravity/agentic-harness/ → ~/Antigravity/agentm/. Operators with the old path can `mv` locally + `git remote set-url origin` to migrate.
- **All cross-references** to the old names swept across the repo: README.md, wiki/ pages (including wiki/Home.md, wiki/reference/Compatibility.md, all ADRs + how-tos), AGENTS.md, CLAUDE.md, CONTRIBUTING.md, harness/ canonical phase specs + skills, adapters/ (including legacy gemini adapter dir), scripts/, lib/, templates/, .github/workflows/, CHANGELOG.md historical entries, extensionless files (templates/wiki/.diataxis, .gitleaks.toml). ~595+ occurrences across ~115 files; zero remaining post-sweep verification.
- **One ADR file renamed**: 0006-agent-toolkit-split.md → 0006-crickets-split.md (via `git mv`).
- **One .harness/ archive renamed**: PLAN.archive.20260512-agent-toolkit-split.md → PLAN.archive.20260512-crickets-split.md (operator-local).
- **CI badge URLs** still resolve correctly (point at new URL + GitHub's auto-redirect handles the old).
- **Sibling repo references** updated throughout to point at alexherrero/crickets instead of alexherrero/agent-toolkit.
- **Phase-gated workflow** + **all behavior** unchanged. Pure rename + cross-reference sweep; no API surface or installer behavior changes.

### Internal

- Mass sed sweep `s/agentic-harness/agentm/g` + `s/agent-toolkit/crickets/g` across all text files in both repos (extensions + extensionless).
- Local sibling-clone dir renamed from ~/Antigravity/agentic-harness/ → ~/Antigravity/agentm/; git remote updated.
- Vault dirs renamed: personal-projects/agentic-harness/ → agentm/ and personal-projects/agent-toolkit/ → crickets/. All vault entries swept (~43 files, 157 occurrences).
- Operator-local ~/.claude/CLAUDE.md (symlinked from dev-setup/configs/claude/CLAUDE.md) imports updated to @~/Antigravity/agentm/AGENTS.md + @~/Antigravity/crickets/AGENTS.md.
- Sibling `dev-setup` repo also swept (~45 files).
- `alexherrero/alexherrero` profile README updated to point at new repo URLs.

### Cross-references

- Paired sibling release: [crickets v1.1.0](https://github.com/alexherrero/crickets/releases/tag/v1.1.0) — the customization toolkit (was agent-toolkit)
- Plan #15 task 11 — README refresh closing task (final task of the plan)

## [v3.0.1] — 2026-05-24 — AgentM logo hero + brand asset set (harness-only PATCH)

Patch — **first visual brand iteration**. Adds the AgentM logo asset set and refreshes `README.md` with a centered logo hero, italic tagline, and reorganized badge layout per the new [[personal-comms-style]] public-surface conventions. Designed in Claude.ai Artifacts.

**No behavior changes.** Pure docs + asset additions; harness behavior unchanged. **Not paired with Crickets this round** — Crickets assets land separately when those arrive; this is a solo harness PATCH. Plan #15 task 1 close-out (Wave 1 of the README refresh).

### Added

- **`assets/agent-m/`** — AgentM primary mark in 4 treatments (standard / clean / transparent / clean-transparent) × multiple PNG sizes (16 / 32 / 48 / 64 / 128 / 256 / 512 / 1024 / 2048) + SVG wrappers. ~27 asset files.
- **`assets/m-monogram/`** — secondary "M" letter mark in true-vector SVG + transparent variants + PNG sizes (16 → 2048). ~14 asset files.
- **`assets/index.html`** — brand-asset preview page showing all variants on light / dark / checkered backgrounds. Includes brand palette swatch (`--ink: #0a0a0a` + `--paper: #f4efe6`) and typography choices (Inter Tight + JetBrains Mono).

### Changed

- **`README.md` hero** — centered logo hero (`assets/agent-m/agent-m-clean-transparent-512.png` at displayed 256px), italic tagline (*"Persistent agentic memory + phase-gated engineering harness."*), and reorganized badge layout into two centered blocks (test/release/license + host-compat). H1 swapped from markdown `#` to `<h1 align="center">` for visual coherence with the centered hero. Rest of the README untouched.

### Internal

- 1 commit on this side: [`da206d6`](https://github.com/alexherrero/agentm/commit/da206d6) (assets + README) + this v3.0.1 release commit.
- **First visual asset commit since repo inception** — establishes `assets/` as the brand-asset convention going forward.
- **Crickets has no corresponding change this round** — Crickets assets will land in a separate `crickets` PATCH when those are designed.
- **Operator-review-gated** per [[docs-prose-style]] workflow; explicit approve-and-ship green-light received before push.

## [v3.0.0] — 2026-05-24 — AgentM V3 close-out (paired with toolkit v1.0.0 — Crickets 1.0)

Major — **AgentM V3 ships**. The harness version now matches the memory implementation V-versioning: V3 is the merged-Obsidian-and-GDrive vault with auto-recall in every harness phase + controlled write under the permeable A3 boundary + the full `/memory` skill surface on the Crickets side. Paired with [`crickets v1.0.0`](https://github.com/alexherrero/crickets/releases/tag/v1.0.0) which ships **Crickets 1.0** — the toolkit's 1.0 commitment to a stable public API surface.

**What AgentM V3 is** (in operator-facing terms):

- The system as a whole — this repo + Crickets + your AgentMemory vault folder, working together.
- Markdown-and-frontmatter knowledge layer that lives in a folder your agent reads at every session start, can write to under controlled conditions, and that the harness's phase commands hook into at natural boundaries (`/setup` / `/plan` / `/work` / `/review` / `/release` / `/bugfix`).
- Self-modulating offer-save (confidence-thresholded), cursor-tracked promotion, evidence-tracked task closeouts, quality-gates bundle for one-command install.

**What V3 doesn't yet do** (deferred to V4, on its own roadmap):

- Vectorized recall on top of markdown / dynamic retrieval during conversation
- Conversational surface ("open a project file for M" / "list my active projects")
- Multi-domain scope beyond dev (vacation planning, sourdough notebooks, workshop builds, research, learning)
- Cross-surface protocol (Claude.ai / Gemini / Antigravity reading the same vault)
- Vault-backed harness state (move `.harness/PLAN.md` + `progress.md` into the vault)
- FRIDAY-style natural-extension surface

V4 design space lives in `.harness/ROADMAP-AgentMemoryV4.md` (operator-local; `.harness/` is gitignored). Full V1→V4 evolution narrative in the new HLD on the Crickets side: [Agent Memory Evolution](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/agent-memory-evolution.md).

### What shipped across the V3 arc (v1.0.0 → v3.0.0)

13 paired releases over ~12 days. Plan-by-plan rollup:

| Plan | Theme | Versions |
|---|---|---|
| #0 | Codex-removal sweep | v1.0.0 |
| #1 + #2 | crickets repo split + install scope | v2.0.0 + toolkit v0.5.0 |
| #3 | Fresh-context evaluator sub-agent | v2.1.0 + toolkit v0.6.0 |
| #4 + #5 | Base hooks: kill-switch + steer + commit-on-stop | v2.2.0 + toolkit v0.7.0 |
| #6 | Design skill v1 | v2.3.0 + toolkit v0.8.0 |
| (patch) | External-review-handoff option | v2.3.1 + toolkit v0.8.1 |
| #15 | Gemini-CLI host removal | v2.4.0 + toolkit v0.9.0 |
| #18 | Local-only embeddings + BGE-large default | v2.4.1 + toolkit v0.9.2 |
| #7a + #7b | AgentM Core + Discovery + Mining | v2.4.2 + toolkit v0.10.0 (closed after 5/5 + 7/7 parts) |
| #13 | `diataxis-author` skill | v2.4.3 + toolkit v0.11.0 |
| #8 | Auto-context into harness phases | v2.5.0 + toolkit v0.11.1 |
| #9 | Evidence-tracking for `/work` | v2.6.0 + toolkit v0.12.0 |
| #10 | Quality-gates bundle | v2.6.1 + toolkit v0.13.0 |
| #12 + #27 + #31 | V3 close-out (retrospective + AgentM HLD + roadmap split + READMEs + 1.0/3.0 paired release) | **v3.0.0 + toolkit v1.0.0 (this release)** |

### Added

- **`README.md`** — AgentM brand-framed rewrite. Lead paragraph names AgentM, then a "What's where" table that names the four pieces (AgentM as the whole / harness this repo / Crickets the sibling toolkit / AgentMemory vault). Get-started section restored (clone both repos, point vault, install harness + Crickets bundle + memory skill, seed always-load, verify). Phases table preserved with auto-recall note. Architecture-history pointer goes to V3 retrospective + HLD on Crickets side.
- **`wiki/Home.md`** rewrite (shipped earlier in this arc, commit `ed5ab7b`) — AgentM-centric landing for the harness wiki.
- **`wiki/reference/Completed-Features.md`** v3.0.0 row.

### Changed

- **Brand**: the system is now **AgentM** in operator-facing prose. The `agentm` repo name + path literals (`AgentMemory/` vault folder, `harness_memory.py` script, `MEMORY_VAULT_PATH` env var) stay as code-side names. Per the locked branding convention.

### Internal

- **2 commits on this side** since v2.6.1: `ed5ab7b` (harness wiki Home rewrite as AgentM landing), `8c871c5` (AgentM README), plus this v3.0.0 release commit.
- **Paired-release ordering**: toolkit v1.0.0 tagged first; this release URL-links to it per `[[coordinated-release-order]]`.
- **8th consecutive paired-release pair** + first MAJOR-MAJOR pair. The harness's V-versioning (v3.0.0) now matches the memory implementation V-versioning (V3) explicitly.
- **Roadmap split** (operator-local, `.harness/` gitignored): main `ROADMAP.md` slimmed to non-V4 backlog (118 lines, was 658); new `ROADMAP-AgentMemoryV4.md` carries the 9 V4-line items; full pre-split V3-era snapshot preserved at `ROADMAP.archive.20260523-v3-complete.md`.

## [v2.6.1] — 2026-05-23 — quality-gates bundle (paired with toolkit v0.13.0)

Patch — **paired-doc-only**. Substantive change ships entirely on the toolkit side: new [`quality-gates` bundle](https://github.com/alexherrero/crickets/blob/main/bundles/quality-gates/bundle.md) one-command-installs the 4 base operator-control + verification primitives most agentm `/work` sessions want (`evaluator` sub-agent + `kill-switch` / `steer` / `commit-on-stop` / `evidence-tracker` hooks). 7th consecutive paired-release pair.

**What changes for harness users**: operators who already had the 4 primitives installed individually see no behavior change — bundle install is functionally identical to per-primitive install (same `.claude/` paths; same `settings.json` registrations). The new affordance is **one-command adoption**:

```bash
bash crickets/install.sh <target-project> --bundle quality-gates
```

instead of 5 separate `--hook <name>` / `--agent evaluator` invocations. Closes the "I forgot to install commit-on-stop and lost an hour" failure mode that surfaced repeatedly in real-world dogfood.

Triggered by [ROADMAP item #10](https://github.com/alexherrero/agentm/blob/main/.harness/ROADMAP.md). Decision rationale + 2 locked design calls Q1-Q2 + 4 load-bearing assumptions in toolkit-side [ADR 0010 — quality-gates bundle](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0010-quality-gates-bundle.md). Operator-facing how-to at [Use The Quality-Gates Bundle](https://github.com/alexherrero/crickets/blob/main/wiki/how-to/Use-The-Quality-Gates-Bundle.md).

### Added

- **`wiki/reference/Completed-Features.md`** v2.6.1 row.

### Changed

- None. Harness phase specs unchanged; harness behavior unchanged.

### Internal

- **0 commits on this side** before the release commit — bundle is pure toolkit packaging. This v2.6.1 release commit is the only harness change.
- **Notable from this plan**: operator-driven mid-plan pivot from COPY to sibling-reference (toolkit-side design call documented in ADR 0010). 2 cross-platform Python gotchas caught + fixed mid-plan in the toolkit installer (pwsh `Join-Path` doesn't `mkdir`; inline `python3 -c open()` uses cp1252 on Windows). Same family as prior plans' cross-platform-Python-gotcha pattern.
- **Paired-release ordering**: toolkit v0.13.0 tagged first; this release URL-links to it per `[[coordinated-release-order]]`.

## [v2.6.0] — 2026-05-23 — Evidence-tracking for /work (paired with toolkit v0.12.0)

Minor — second non-doc-only paired pair in the recent run (after v2.5.0). Harness ships the **`/work` §5b spec amendment** documenting the contract for the new `evidence-tracker` base hook in [`crickets v0.12.0`](https://github.com/alexherrero/crickets/releases/tag/v0.12.0). Default-FAIL evidence enforcement: every PLAN.md task starts with `evidence-met=false`; the agent must demonstrably READ relevant spec/test/evidence files before a `Write`/`Edit` that flips `[ ]` → `[x]` is allowed. Hook blocks otherwise.

**What changes for operators**:
- With `crickets` installed + the `evidence-tracker` hook in place: `/work` task closeouts gain a deterministic verification gate. Hook fires PreToolUse on `Read|Write|Edit`; records reads; blocks unmet-evidence flips with a helpful stderr message + 3 recovery paths.
- Without those prerequisites: **zero behavior change**. Hook absent → no enforcement → `/work` runs as it always has.

Triggered by [ROADMAP item #9](https://github.com/alexherrero/agentm/blob/main/.harness/ROADMAP.md). Decision rationale + 3 locked design calls Q1-Q3 + 4 load-bearing assumptions in the toolkit-side [ADR 0009 — evidence-tracker hook](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0009-evidence-tracker-hook.md). Operator-facing how-to at [Use The Evidence-Tracker Hook](https://github.com/alexherrero/crickets/blob/main/wiki/how-to/Use-The-Evidence-Tracker-Hook.md).

### Added

- **`harness/phases/03-work.md` §5b** "Evidence-tracking (graceful-skip if not installed)" inserted between §5 Run-deterministic-gates + §6 Iterate-on-failures (~75 lines). Documents: hook trigger table; 3 task-body conventions (default heuristic / `**Evidence:** <pattern>` override / `**Evidence:** none — <rationale>` opt-out); 3 recovery paths on block; 5 graceful-skip conditions.
- **`templates/PLAN.md`** task-block template gains optional `**Evidence:**` field hint cross-referencing §5b. Operators creating new plans see the hook surface in the template.
- **`wiki/reference/Completed-Features.md`** v2.6.0 row.

### Changed

- **4 wiki line-range anchors updated** in `wiki/explanation/GitHub-Projects-Integration.md` for the line-position shift from §5b insertion (`03-work.md#L194-L216 → #L241-L263`). Sub-letter pattern (§5b) preserves integer §-numbering — incoming wiki ref to §10 keeps citing "§10"; only line-range moves.

### Internal

- **1 commit on this side** (`2027bec`) + this v2.6.0 release commit. Toolkit-side ships substantive in 7 commits (`8c6419f` + `6e875d5` + `e6f4411` + `83fb3e7` + `a3100ab` + `4569c20` + `8793237` + `ecd8d6c` + `dfc802b`).
- **3 design calls locked at /plan time** (Q1-Q3 per ADR 0009 toolkit-side): hybrid evidence resolver / per-task PLAN.md flip gate only / explicit opt-out.
- **Sub-letter spec amendment pattern continues** — same §-numbering preservation as plan #8's §1b/§4c/§7b/§7c/§5b/§5c amendments. Wiki refs that cite "§N" stay valid across plan #9.
- **Self-hosting note**: this harness repo doesn't itself have the evidence-tracker hook installed; the spec describes the contract for projects that DO install it. First real-world dogfood happens in the next `/work` session in a project with both repos installed.
- **Paired-release ordering**: toolkit v0.12.0 tagged first; this release URL-links to it per `[[coordinated-release-order]]`.

## [v2.5.0] — 2026-05-22 — Auto-context into harness phases (paired with toolkit v0.11.1)

Minor — **first non-doc-only paired pair** in the recent run (after v2.4.0/v2.4.1/v2.4.2/v2.4.3 all doc-only on this side). Harness ships real new phase behavior: every phase command (`/setup`, `/plan`, `/work`, `/review`, `/release`, `/bugfix`) now auto-invokes MemoryVault at predictable boundaries without the agent or operator having to remember to call `/memory search` or `/memory save`. Paired with [`crickets v0.11.1`](https://github.com/alexherrero/crickets/releases/tag/v0.11.1) which ships the toolkit-side companion documentation (`Cross-Repo-Memory-Protocol.md`).

**What changes for operators**:
- With `MEMORY_VAULT_PATH` set + `crickets/skills/memory/` sibling-cloned: every phase auto-loads operator conventions + project-specific decisions + open-questions / known-issues (per phase) at its start; phases that surface durable items offer to save them at the end (self-modulating ask — high-confidence saves silently with stderr notice; low-confidence prompts).
- Without those prerequisites: **zero behavior change**. Every phase graceful-skips silently. Harness runs unchanged on systems where MemoryVault isn't adopted.

Triggered by [ROADMAP item #8](https://github.com/alexherrero/agentm/blob/main/.harness/ROADMAP.md). Decision rationale + 5 locked design calls (Q1–Q5) + 4 load-bearing assumptions in new [ADR 0007 — Auto-context into harness phases](wiki/explanation/decisions/0007-auto-context-into-harness-phases.md). Operator-facing how-to at [Use Auto-Context In Harness Phases](wiki/how-to/Use-Auto-Context-In-Harness-Phases.md).

### Added

- **`scripts/harness_memory.py`** (~520 lines, stdlib-only) — dispatcher with 4 sub-commands:
  - `recall --phase <P> --project <S>` — phase-scoped recall (loads `_always-load/` conventions + per-phase `personal-projects/<slug>/` subdirs per `_PHASE_PROJECT_DIRS` mapping); per-phase token cap via `HARNESS_RECALL_BUDGET_<PHASE>` env.
  - `offer-save --phase --project --kind --slug --content-file [--confidence] [--confidence-reason]` — self-modulating ask: `confidence ≥ HARNESS_AUTO_SAVE_CONFIDENCE_THRESHOLD` (default 0.8) silent-saves with `[auto-saved high-confidence]` stderr; below threshold prompts. `HARNESS_AUTO_SAVE_MODE` (ask/silent/off) outer envelope.
  - `plan-done-promotion --project-root . [--dry-run]` — cursor-tracked progress.md tail-scan via `.harness/.promoted-progress-cursor`. Shared between `/work` plan-done + `/release` triggers — single fire per plan-window.
  - `available` — exit 0/1 short-circuit for phase specs.
  - **3-tier toolkit discovery** (`HARNESS_MEMORY_TOOLKIT_PATH` env > sibling-clone > `~/Antigravity/crickets/`). Toolkit-absent path graceful-skips with stderr notice.
- **`scripts/vault_project.py`** (~200 lines, stdlib-only) — `read_vault_project()` with 3-tier fallback (explicit field > `github.repo` basename > git origin); `write_vault_project()` atomic merge-preserving. CLI wrapper.
- **33 unit tests** for `harness_memory.py` (`scripts/test_harness_memory.py`) across 7 classes — `TestAvailable` / `TestRecall` / `TestOfferSaveDecision` (pure logic) / `TestOfferSaveBehavior` (end-to-end with toolkit stub) / `TestOfferSaveToolkitAbsent` / `TestPlanDonePromotion` / `TestCLI`.
- **28 unit tests** for `vault_project.py` (`scripts/test_vault_project.py`) across 4 classes — read tier-1/2/3/none paths, atomic write merge-preserve + round-trip, URL-shape variety (https/ssh/file/no-.git), CLI exit codes.
- **CI step** added to all 3 OS workflows — `python3 scripts/test_vault_project.py` + `python3 scripts/test_harness_memory.py`. 61 total new test cases.
- **New [ADR 0007 — Auto-context into harness phases](wiki/explanation/decisions/0007-auto-context-into-harness-phases.md)** — full Status/Context/Decision-Q1-Q5/Consequences/Related shape matching toolkit ADRs 0007/0008. 5 design calls + 4 load-bearing assumptions with re-audit triggers.
- **New [how-to/Use-Auto-Context-In-Harness-Phases](wiki/how-to/Use-Auto-Context-In-Harness-Phases.md)** — per-phase boundary table + dispatcher CLI reference + 5-env-var matrix + 3 worked scenarios (offer-save fatigue tuning / recall budget tight / plan-done-promotion cursor confirmation) + 8 troubleshooting table rows. Length-justified inline.
- **`wiki/reference/Completed-Features.md`** v2.5.0 row.
- **Home.md + _Sidebar.md** references to new how-to + ADR.

### Changed

- **All 6 canonical phase/pipeline specs amended** via sub-letter convention (preserves integer §-numbers — incoming wiki refs that cite "§N" stay valid):
  - `harness/phases/01-setup.md` — new §1b (Auto-recall conventions + vault_project write) + §8b (Project index stub offer-save).
  - `harness/phases/02-plan.md` — new §1b (Auto-recall decisions + open-questions) + §4c (Open-questions offer-save).
  - `harness/phases/03-work.md` — new §1b (Auto-recall task-relevant decisions + known-issues) + §7b (Remember-this offer-save, 3-kind taxonomy decision/gotcha/workflow) + §7c (Plan-done-promotion on final task flip).
  - `harness/phases/04-review.md` — new §2b (Recall-only conventions — read-only by design; "a reviewer that writes biases toward confirming its own findings").
  - `harness/phases/05-release.md` — new §1c (Auto-recall decisions for changelog framing) + §5b (Decisions offer-save after CHANGELOG draft) + §5c (Progress.md tail-scan via plan-done-promotion — shared cursor with `/work` §7c).
  - `harness/pipelines/bugfix.md` — new §2b (Auto-recall known-issues at Analyze) + §4b (Gotcha offer-save at Verify; cap at 1 per /bugfix; ADR write operator-controlled not auto).
- **4 wiki line-range anchors updated** in `wiki/explanation/GitHub-Projects-Integration.md` + `wiki/explanation/decisions/0003-ProjectsV2-Ownership-And-Linking.md` for the line-position shifts (sub-letter pattern keeps §-numbers stable; only line ranges move).

### Internal

- **10 commits across plan #8** on this side, then this v2.5.0 release commit: `9ccc020` + `34d21e9` (vault_project helper + PII scrub) + `17b2061` (harness_memory dispatcher) + `aebf189` (01-setup) + `44d0ba1` (02-plan) + `b3f653b` (03-work) + `3a9fb32` (04-review) + `ddfb5c0` (05-release) + `d134c5a` (bugfix.md) + `132a42e` (docs pass: ADR 0007 + how-to + Completed-Features + 11 ADR-number fixes). Toolkit side: 1 commit (`9176bc2` Cross-Repo-Memory-Protocol.md + Home.md ref).
- **5 design calls locked at /plan time** (Q1–Q5 per ADR 0007): per-phase budget envs / vault_project 3-tier auto-detect / silent graceful-skip / self-modulating ask with confidence threshold / dual-trigger cursor-tracked promotion.
- **Operator clarifications mid-plan**: Q4 revised from flat "ask" default to **self-modulating ask** (agent confidence threshold gates silent-vs-prompt); Q5 revised from "release-only trigger" to **dual-trigger middle ground** (plan-done AND release; shared cursor). Both revisions baked into dispatcher implementation + spec amendments + ADR 0007.
- **ADR-number correction**: PLAN.md task 9 said ADR 0009 (conflated with toolkit numbering); harness ADRs go 0001–0006 so the new ADR landed as 0007. 11 inbound refs across the 6 amended spec files updated in task 9's docs commit.
- **PII scanner false-positive caught mid-plan** (task 1 commit `9ccc020`): canonical SSH-form URL + a personal-path-shaped file:// URL in test fixtures flagged as PII; scrubbed to `example.com` + neutral `/srv/git/` placeholders in `34d21e9`. (Same pattern hit again on this release commit `76d17a6` when I described the scrub in this entry — fixed forward via the next commit.)

## [v2.4.3] — 2026-05-22 — diataxis-author skill (paired with toolkit v0.11.0)

Patch — paired-doc-only release pairing with [`crickets v0.11.0`](https://github.com/alexherrero/crickets/releases/tag/v0.11.0). Substantive change ships entirely on the toolkit side: new `diataxis-author` skill with 5 sub-commands (`/diataxis author` + `check` + `repair` + `migrate` + `classify`) covering the full Diátaxis-wiki lifecycle. Subsumes harness's `migrate-to-diataxis` predecessor (deprecated 2026-05-22 in commit `d4d4adf`; predecessor file removal in a follow-up harness PATCH after dogfood). **4th consecutive paired-release-as-documentation pair** (after v2.4.0/v2.4.1/v2.4.2).

Harness-side changes for this release pair:

1. **`harness/skills/migrate-to-diataxis.md`** gains NOTE-WARNING deprecation block (shipped in commit `d4d4adf` 2026-05-22 alongside toolkit Part 4 push). Predecessor file stays through v1 dogfood; full removal lands in a follow-up harness PATCH release.
2. **No phase-spec or adapter changes** — the toolkit's `/diataxis` sub-commands are operator-invokable; harness `/release` documenter dispatch remains unchanged. Future harness PATCH could amend `/release` to call `/diataxis check` when the skill is installed (graceful-skip otherwise); deferred from v1 to keep change surface narrow.
3. **CHANGELOG + Completed-Features.md row** documenting the paired release.

Triggered by [ROADMAP item #13](https://github.com/alexherrero/agentm/blob/main/.harness/ROADMAP.md). Implemented as plan #13 (5 parts: scaffold + author-classify + check-repair + migrate-subsume + AgentMemory-docs-release). Decision rationale + 4 locked design calls + 4 load-bearing assumptions in [toolkit-side ADR 0008](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0008-diataxis-author.md). Parent design at [diataxis-author](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/designs/diataxis-author.md) (Status: launched as of this release).

**Why this matters for harness users**: operators with the crickets installed gain five new `/diataxis` sub-commands on next install. Drift detection (`/diataxis check`) becomes a regular auditing tool alongside `check-wiki.py --strict`. The `migrate-to-diataxis` predecessor still works through v1 dogfood for operators with existing installs, but new migrations should use `/diataxis migrate` for the additional capabilities (per-repo `.diataxis-conventions.md` auto-seed + delegation to `/diataxis repair` for mode-mixed splits + AgentMemory convention sync).

### Added

- **CHANGELOG.md v2.4.3 entry** + **Completed-Features.md row** for the paired release.

### Changed

- **`harness/skills/migrate-to-diataxis.md`** — NOTE-WARNING deprecation block + redirect to `/diataxis migrate` (committed `d4d4adf` 2026-05-22).

### Internal

- **Plan #13 close-out**: 5/5 parts shipped across 8 toolkit commits + 1 harness commit. Plan archived to `.harness/PLAN.archive.20260522-diataxis-author-part-5.md` (sibling archives for parts 1-4). ROADMAP item #13 moves to Completed.
- **Second real dogfood of `/design` skill** (after MemoryVault parent design closed 2026-05-20 + 2026-05-22). Parent design transitions `final → launched` automatically per `/design` lifecycle.
- **3 Windows-specific CI failures caught + fixed mid-plan** per `[[wake-on-ci-pattern]]`: Start-Process multi-word arg split (Part 2 `caf3c5a`); `git mv` cwd dependence + cp1252 stdout encoding crash on `→` arrow (Part 4 `c5b32fd` + `79cf283`). Pattern locked: cross-platform Python scripts must defensively configure encoding + line endings + invocation patterns. Same family of bugs as Part 4 of plan #18 (CRLF line endings).

## [v2.4.2] — 2026-05-22 — MemoryVault Discovery + Mining (paired with toolkit v0.10.0)

Patch — second MemoryVault roadmap item closes. Paired with [`crickets v0.10.0`](https://github.com/alexherrero/crickets/releases/tag/v0.10.0) which ships the substantive feature set: five new `/memory` sub-commands (`/memory index-skills` + `/memory reflect corpus` + `/memory discover-skills` + `/memory adapt-skills` + `/memory watchlist`) that turn the vault from a static curated store into a living surface.

Harness-side changes for this release pair are **doc-only** per the paired-release-as-documentation pattern established in v2.4.0 + v2.4.1. The harness hasn't owned customizations since the v2.0.0 split; discovery + mining lives entirely on the toolkit side. The harness's role in plan #7b is closing out the active plan + moving ROADMAP item #7b to Completed.

Triggered by [ROADMAP item #7b](https://github.com/alexherrero/agentm/blob/main/.harness/ROADMAP.md) which had been queued from plan #7a's design-skill output (plan #6 dogfood). Implemented as plan #7b (7 tasks across 8 toolkit commits). Decision rationale + 7 locked design calls live in [toolkit-side ADR 0007 — MemoryVault Discovery + Mining](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0007-memoryvault-discovery.md) — no new harness-side ADR (discovery + mining is a toolkit-side concern; harness inherits via its toolkit-customization dependency).

**Why this matters for harness users**: the harness itself is unchanged. Operators who installed the memory skill via the toolkit gain five new `/memory` sub-commands on next install. The personal-skills indexer auto-runs from `bash crickets/install.sh ~/their-project` (against the toolkit's own `skills/` + sibling `agentm/.claude/skills/`); the cadence-checked skill-discovery scan auto-fires from the existing `memory-reflect-idle` hook (no operator action required); the adapt-don't-import workflow + watchlist review are operator-invoked when ready. **Adapt-don't-import is architecturally enforced** — the `adapt-evaluator` sub-agent's write allowlist physically prevents auto-fork into `crickets/skills/`; the operator's manual authoring step is the only path to a real skill.

After this release pair ships, ROADMAP item #7b moves to Completed; the next ROADMAP item per the locked execution order is **#13 diataxis-author skill** (with smaller items #19-#22 queued in parallel: Ideas.md format redesign, transfer-context × AgentMemory integration, harness self-audit skill, cross-surface AgentMemory protocol).

### Added

- **`.harness/PLAN.archive.20260522-memoryvault-discovery-mining.md`** — archived plan #7b PLAN.md with 7/7 tasks `[x]` + full per-task narrative.
- **Completed-Features.md row** for plan #7b — overview entry + dated section narrative.

### Changed

- **`.harness/ROADMAP.md`** — item #7b moved from active table to the Completed section with full narrative (releases shipped + what shipped end-to-end + notable patterns established + deferred items).
- **`.harness/PLAN.md`** — promoted from queued-plans/ to active for the next roadmap item per the established `/release` lifecycle hook pattern.

### Internal

- **Plan close-out pattern**: plan #7b followed the same shape as plan #7a (5 substantive code tasks + 2 batch tasks for docs + release); pre-authorized autonomous-batch execution mode for tasks 5+6+7 (decided during task 1 close-out) saved 2 approval cycles for ~30% of remaining scope.
- **Per-task narratives** captured in `.harness/progress.md` between 2026-05-21 and 2026-05-22, one entry per task close-out + one end-of-plan summary.

## [v2.4.1] — 2026-05-20 — Local-only embeddings (paired with toolkit v0.9.2)

Patch — embedding-mode collapse paired with [`crickets v0.9.2`](https://github.com/alexherrero/crickets/releases/tag/v0.9.2). **Drops the Voyage/Anthropic API embedding mode from the toolkit's memory skill; local `sentence-transformers` is now the only production mode.** Default model upgraded `all-MiniLM-L6-v2` → `BAAI/bge-large-en-v1.5` (1024-d native; ~1.3GB on disk + ~1.5GB RAM at runtime; PyTorch MPS on Apple Silicon for acceleration).

Harness-side changes for this release pair are **doc-only** per the paired-release-as-documentation pattern established in v2.4.0. The harness hasn't owned customizations since the v2.0.0 split (when `dependabot-fixer` + `ship-release` migrated to `crickets`); the embedding-mode refactor happens entirely on the toolkit side. The harness's role in plan #18 is acknowledging the v0.9.2 toolkit shape in its docs + framing the paired release.

Triggered by [ROADMAP item #18](https://github.com/alexherrero/agentm/blob/main/.harness/ROADMAP.md) (added 2026-05-20 mid-flight of plan #7a part 5 / seed-pass; task 6 of seed-pass needed a worthwhile embedding model for sample-recall validation, which forced the embed-refactor work first). Implemented as plan #18 (7 tasks; this release pair is task 7). Decision rationale lives in [toolkit-side ADR 0001's 2026-05-20 amendment](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0001-crickets-purpose.md#amendment-2026-05-20) — no new harness-side ADR (the embedding-mode decision is a toolkit-side concern; harness inherits via its dependency on toolkit customizations).

**Why this matters for harness users**: the harness itself is unchanged. Operators who installed the memory skill via the toolkit see the embedding-mode change on next install (`bash crickets/install.sh ~/their-project` runs the new `install_python_deps()` step by default; `--no-python-deps` opts out). Existing 384-d vec-indexes invalidate due to the dim bump 384 → 1024 — the toolkit's new `vec_index.py rebuild` subcommand handles migration with a graceful-skip + clear stderr message on first invocation that detects the dim mismatch.

After this release pair ships, plan #7a part 5 (seed-pass) resumes at task 6 (validate via sample recalls) using the new BGE-large model. Plan-#18-driven detour is complete; the MemoryVault Core roadmap (#7a) resumes its sequential execution.

### Added

- **`wiki/reference/Completed-Features.md`** v2.4.1 overview row + full narrative section (What shipped / Why this shape / Doesn't do / Tracked as / Related — mirrors v2.4.0 format).

### Changed

- Adapter wrappers (`.claude/commands/*.md` + Antigravity adapter equivalents) untouched — canonical-reference inheritance: adapters point at `harness/phases/` specs which are themselves untouched in this release.
- No changes to harness phase specs (no embedding-related logic in the harness; embedding is wholly a toolkit-side concern via the memory skill).

### Internal

- **Paired-release-as-documentation pattern (continued from v2.4.0)**: this is the second consecutive paired release where the substantive change is toolkit-side and the harness ships doc-only. The pattern keeps version cadences readable for operators tracking changes across both repos — they don't have to wonder "why did toolkit ship a MINOR but harness didn't?"
- **First post-#18 install on harness side**: operators who run `bash crickets/install.sh ~/their-project` after this release pair will see the new `==> python deps` install step. Operators can opt out via `--no-python-deps` if they manage Python deps via virtualenv / conda / system packages, or accept the install (sentence-transformers + transitive deps total ~1.5GB+ on first pull; BGE-large model downloads lazily ~1.3GB on first `/memory save` or `embed.py --mode local`).
- **Plan #18 was inserted mid-flight of plan #7a part 5** (seed-pass) — first time a plan was inserted into the queue mid-execution rather than queued at the end. The mechanism: archive the active PLAN.md to `.harness/PLAN.paused.YYYYMMDD-<slug>.md`, write the new plan as the active PLAN.md, execute it, then restore the paused plan as the new active PLAN.md after the inserted plan completes. This pattern is captured in plan #18's "How to resume" section + this CHANGELOG entry as precedent for future mid-flight insertions.

## [v2.4.0] — 2026-05-17 — Gemini-CLI host removal (paired with toolkit v0.9.0)

Minor — host-scope reduction paired with [`crickets v0.9.0`](https://github.com/alexherrero/crickets/releases/tag/v0.9.0). **Drops standalone Gemini CLI as a supported host** across the personal-dev-env. Keeps Claude Code + Antigravity (Gemini-in-Antigravity is a different surface — IDE-level integration, not standalone CLI).

Harness-side changes for this release pair are **doc-only**. The harness hasn't owned customizations since the v2.0.0 split (when `dependabot-fixer` + `ship-release` migrated to `crickets`); the customization sweep happens entirely on the toolkit side. The harness's role in plan #15 is acknowledging the host-scope reduction in its docs + framing the paired release.

Triggered by [ROADMAP item #15](https://github.com/alexherrero/agentm/blob/main/.harness/ROADMAP.md) (added 2026-05-16 during plan #7a part 1 task 1 ship). Implemented as plan #15 (7 tasks; this release pair is task 7). Decision rationale lives in [toolkit-side ADR 0006](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0006-gemini-cli-host-removal.md) — no new harness-side ADR (the host-scope decision is a toolkit-side concern; harness inherits via its dependency on toolkit customizations).

### Added

- **`wiki/reference/Completed-Features.md`** v2.4.0 overview row + full narrative section (What shipped / Why this shape / Doesn't do / Tracked as / Related — mirrors v2.3.x format).

### Changed

- Adapter wrappers (`.claude/commands/*.md` + Antigravity adapter equivalents) untouched — canonical-reference inheritance: adapters point at `harness/phases/` specs which are themselves untouched in this release.
- No changes to harness phase specs (no host-related conditionals to update — the harness phases don't reference specific hosts by name; host scope is decided by toolkit-side manifests).

### Internal

- **Paired-release-as-documentation pattern**: when the substantive change lives entirely on one side of the toolkit/harness split, the other side still ships a paired release with framing-only content. This keeps the two repos' version cadences readable for operators tracking changes — they don't have to wonder "why did toolkit ship a MINOR but harness didn't?". v2.4.0 is the documentation-acknowledgement counterpart to v0.9.0.
- **First post-#15 install on harness side**: operators who run `bash crickets/install.sh` against an agentm install will see the legacy-cleanup prompt fire if `.agents/skills/` exists from a prior install. The harness's `--update` path (separate from toolkit's `--update`) is unaffected — harness doesn't manage `.agents/`.

## [v2.3.1] — 2026-05-16 — `/plan` external-review-handoff option (paired with toolkit v0.8.1)

Patch — additive only, no breaking changes. Adds an **external-review-handoff option** to the harness's `/plan` phase, mirroring the option added to `crickets`'s `/design` skill in [v0.8.1](https://github.com/alexherrero/crickets/releases/tag/v0.8.1). Operators can now hand off a drafted `.harness/PLAN.md` to Antigravity IDE for inline-comment review + Gemini-applies-comments revision, then resume in Claude Code with a diff-on-resume pass against a pre-handoff snapshot.

Dogfood-driven amendment from plan #6's first real design exercise (MemoryVault): the inline block-by-block walk pattern works but tires fast on long content. Antigravity's native inline-comment UI + Gemini-applies-comments pattern is dramatically better for review-style work; the new option lets operators reach for that workflow on long plans without leaving the harness.

Paired with [`crickets v0.8.1`](https://github.com/alexherrero/crickets/releases/tag/v0.8.1), which adds the same option to `/design author` Step 5 + Step 6 + `/design translate` Step 4. Shared template (`crickets/skills/design/templates/transfer-context.md`), shared workflow shape, shared cleanup discipline across both repos.

### Mechanics

When the operator picks "Hand off for external review" after the agent drafts `.harness/PLAN.md`:

1. **Pre-handoff snapshot** at `.harness/PLAN.pre-handoff-<YYYYMMDDhhmmss>.md` — full copy used on resume to diff against revised version.
2. **Transfer-context file** at `.harness/transfer/plan-<YYYYMMDDhhmmss>.md` — uses the toolkit-side template; `DOC_TYPE: plan` triggers plan-specific guardrails (harness PLAN.md shape per `templates/PLAN.md`; Status lifecycle `draft → in-progress → done` don't-transition; paragraph-long Status:[x] narratives required; Locked design calls section at the bottom is load-bearing). Inlines dev-flow conventions (Antigravity won't see device-global `~/.claude/CLAUDE.md`) + operator intent extracted from brief + Goal sections + recent decisions extracted from the plan's `## Locked design calls` section + most recent `.harness/progress.md` entries.
3. **Handoff prompt** output with explicit Antigravity steps: open `.harness/PLAN.md` + transfer-context file, add inline comments via Antigravity's native UI, ask Gemini to apply per the transfer-context. Gemini revises + writes change-summary log at `.harness/PLAN.diff.md`.
4. **Resume flow** (`/plan --resume-external-review` or natural "plan review complete"): harness reads revised PLAN.md + change-summary log, diffs against pre-handoff snapshot, surfaces findings (task list changes / verification spec changes / `## Locked design calls` modifications / Gemini's adjacent-issue suggestions), asks Accept / Iterate / Discard. Accept archives snapshot + transfer-context to `.harness/transfer/_archive/`; Iterate regenerates for another round; Discard restores from snapshot.

### Added

- **`harness/phases/02-plan.md` §4b "External-review handoff (optional, alternative to inline iteration — v2.3.1+)"** — new section between §4 (Write PLAN.md) and §5 (Update features.json) documenting the when-to-offer trigger, the pre-handoff snapshot write, the transfer-context generation (with `DOC_TYPE: plan` guardrails), the handoff prompt output, and the resume flow with diff-on-resume + Accept/Iterate/Discard. Cross-references the toolkit-side ADR 0004 amendment as the design rationale (shared design across both repos).

### Changed

- Adapter wrappers (`.claude/commands/plan.md` + Antigravity adapter) untouched — canonical-reference inheritance: adapters point at `harness/phases/02-plan.md` and pick up the new §4b automatically.

### Internal

- First cross-repo dogfood-driven amendment shipped as a coordinated patch pair. Pattern: ship v1 of both repos, dogfood on a real exercise (MemoryVault `/design author`), surface gaps that apply to both surfaces (the `/design` skill on toolkit + the `/plan` phase on harness), ship paired patches with the amendment captured in the toolkit-side ADR (one ADR for both since the design is shared).
- Implementation lives entirely in phase spec documentation. No script changes, no adapter changes, no template changes (the harness reuses the toolkit-side `transfer-context.md` template). Harness-side install validates toolkit presence; graceful-skip warning if toolkit is not installed (operator gets a message: "External-review handoff requires `crickets` v0.8.1+ installed alongside; toolkit not detected — inline review only").
- Re-audit triggers in the toolkit-side ADR 0004 amendment fire after the next 3-5 real external-review handoffs on either skill point — surfaces apply to both repos.

## [v2.3.0] — 2026-05-15 — `/release` + `/setup` integration for crickets's `/design` skill (additive)

Additive minor — no breaking changes. Two harness extensions that integrate with the new [`crickets v0.8.0`](https://github.com/alexherrero/crickets/releases/tag/v0.8.0) `/design` skill: a `/release` lifecycle hook that auto-promotes queued plans + transitions design Status `final → launched` + surfaces launched designs in the wiki; and a `/setup` scaffolding extension for the `wiki/explanation/designs/` landing dir. Plus a small `/work` Step 11 summary template enhancement that applies to any harness install with a ROADMAP-driven multi-plan project.

Paired with [`crickets v0.8.0`](https://github.com/alexherrero/crickets/releases/tag/v0.8.0), which ships the `/design` skill itself with three sub-commands (`author` / `translate` / `sequence`). The harness extensions in this release light up the integration points the toolkit-side skill writes to:

- `/design sequence` writes a first PLAN.md to `<project>/.harness/PLAN.md` + queues subsequent parts at `<project>/.harness/designs/<doc-slug>/queued-plans/<part-slug>.PLAN.md`. **v2.3.0's `/release` §1b consumes that queue** — auto-promoting the next plan when the active completes, or transitioning the parent design Status when the last part ships.
- `/design author --visibility published` routes design docs to `wiki/explanation/designs/<slug>.md`. **v2.3.0's `/setup` §7 extension scaffolds the `wiki/explanation/designs/` landing dir** so target projects have the destination ready before first design.

Without `crickets` installed alongside, both extensions silent-skip — the harness still works standalone exactly as it did in v2.2.0.

### Added

- **`harness/phases/05-release.md` §1b "Design-doc lifecycle check (crickets)"** — new section between §1 (Verify plan completion) and §2 (Re-run gates). Three cases handled:
  - **Case A — not design-sourced**: silent no-op; existing `/release` flow continues unchanged.
  - **Case B — design-sourced, more queued plans exist**: archive completed plan to `.harness/PLAN.archive.YYYYMMDD-<part-slug>.md`; promote next queued plan (alphabetical order — same deterministic ordering `/design sequence` uses) to `.harness/PLAN.md`; append parent design's Document History with the promotion entry; **halt /release** with operator-facing next-step message. No release to prepare yet — just a plan promotion.
  - **Case C — design-sourced, LAST queued plan**: archive completed plan; transition parent design Status `final → launched`; append Document History with launched-state entry; **if `visibility: published`** update `wiki/Home.md` + `wiki/_Sidebar.md` to surface the design in a "Designs" section (idempotent — re-runs are no-op); continue with §2-§9 — this IS a real release.
  - **Graceful-skip**: silent no-op when no design-doc origin signal present (`crickets` not installed, or plan was hand-authored).
- **`harness/phases/01-setup.md` §7 (Populate the wiki scaffold) extended** with a new bullet for `wiki/explanation/designs/` landing dir. Cross-refs the `crickets` `/design` skill how-to + the §1b `/release` lifecycle that transitions designs to launched.
- **`templates/wiki/explanation/designs/`** — NEW scaffold dir installed by `install.sh`'s per-file walk into target projects. Contents: `.gitkeep` (keeps dir tracked in git) + `README.md` (one-paragraph explanation of visibility routing rules, the Status lifecycle, the wiki surfacing trigger, and the toolkit dependency).
- **`scripts/check-references.py` `EXTERNAL_CUSTOMIZATIONS` extended** with `design` entry. Inline comment captures the current state honestly: phase specs use slash-command phrasing "the `/design` skill" with leading slash, which keeps it from matching `INVOKE_SKILL_RE` (regex char class `[A-Za-z0-9_-]` excludes `/`), so this exclusion is forward-compatibility documentation rather than currently load-bearing. If phase spec phrasing ever shifts to bare "`design`", the exclusion becomes load-bearing.
- **`/work` Step 11 summary template enhanced for ROADMAP-driven projects** (`harness/phases/03-work.md`). Opt-in via the `.harness/ROADMAP.md` signal — single-plan installs keep the existing minimal `≤5-bullet summary`; multi-plan projects get the richer template (roadmap context lead-in, ✅/⬜ chart, link block to `.harness/` state files, explicit handoff phrase, optional commit SHA / CI status / design calls detail). Applies to any harness install with a roadmap; not specific to the design skill.

### Changed

- **Adapter wrappers untouched.** All six `/release` + `/setup` adapter wrappers (claude-code/commands, antigravity/workflows, gemini/commands) reference their canonical phase spec exactly once; the new §1b + extended §7 inherit via the existing canonical-reference pattern. Same pattern as plan #3 task 3 (evaluator integration in /review) and plan #4 task 3 (hooks integration in /work + /release).

### Internal

- **Task 5 of plan #6** (design skill v1) — the only task in plan #6 that touches the harness. Tasks 1-4 + 6 land in `crickets`; task 7 is this paired release.
- **Negative test on `EXTERNAL_CUSTOMIZATIONS`** during implementation confirmed the `design` entry is **not currently load-bearing** — phase spec phrasing uses `` `/design` `` (slash-command form) which doesn't match `INVOKE_SKILL_RE`'s char class. Updated the inline comment to reflect this honestly; entry stays as forward-compatibility documentation.
- **`/work` Step 11 enhancement source**: came out of the dev-flow codification work (commit `ce86977`, 2026-05-14), separate from plan #6 but shipping in the same release window. Universal applicability — any harness install with a `ROADMAP.md` benefits.
- **All 8 harness gates green** on every commit in the `v2.2.0..v2.3.0` range.

[v2.3.0]: https://github.com/alexherrero/agentm/releases/tag/v2.3.0

## [v2.2.0] — 2026-05-14 — `/work` + `/release` augmentable with crickets's base hooks (additive)

Additive minor — no breaking changes. Two new optional sections in the harness phase specs document how to dispatch the three new base operator-control hooks (`kill-switch`, `steer`, `commit-on-stop`) shipped in [`crickets v0.7.0`](https://github.com/alexherrero/crickets/releases/tag/v0.7.0) alongside the existing phase workflow.

The three hooks are lifted from the cwc-long-running-agents pattern and give the operator precise control over long-running Claude Code sessions:

| Hook | Trigger | Effect |
|---|---|---|
| `kill-switch` | `PreToolUse` | `touch .harness/STOP` halts the next tool call; `rm` to resume |
| `steer` | `PreToolUse` | Write `.harness/STEER.md` for mid-run redirect (contents → agent context; file → `STEER.consumed-<ts>.md`) |
| `commit-on-stop` | `Stop` event | Dirty tree → `auto-save/<ts>` safety branch with commit; never modifies current branch; never pushes |

`/work` is the primary beneficiary — long-running iteration loops, mid-task redirects, and crashed sessions all become recoverable motions. `/release` benefits less from kill-switch + steer (release flows are typically short) but the `commit-on-stop` backstop reduces the cost of an interrupted release prep. Both new sections graceful-skip when `crickets` is absent; the phase contracts don't require the hooks.

Paired with [`crickets v0.7.0`](https://github.com/alexherrero/crickets/releases/tag/v0.7.0). The decision rationale for the hooks' design (per-repo file location, audit-trail rename for STEER, safety-branch not current-branch, Stop-event-only for v0.7.0, alphabetical-install-order hook ordering, claude-code-only host scope, Python helper for settings.json merge) lives in [crickets ADR 0003](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0003-base-operator-hooks.md). No new harness-side ADR — this release is the integration surface, not the design decision.

### Added

- **`/work` phase spec — new section "Long-running `/work` — operator-control hooks (crickets)"** (`harness/phases/03-work.md`). 20-line section between "When to invoke /review" and "Failure modes to avoid". Reference table for all three hooks (event + trigger + effect); when-they-earn-their-keep framing (runaway loop / mid-task redirect / crashed session); the alphabetical-ordering invariant (kill-switch fires before steer in PreToolUse — a halt always takes precedence over a steer); graceful-skip framing.
- **`/release` phase spec — new section "Optional: `commit-on-stop` safety net (crickets)"** (`harness/phases/05-release.md`). Shorter 4-line section between progress.md closeout and "Failure modes to avoid". Documents commit-on-stop as the safety net for interrupted release flows (mid-CHANGELOG-edit, mid-tag-prep); cross-references the `/work` section for the full hook lineup; notes kill-switch + steer provide less marginal value for typically-short release flows.
- **`scripts/check-references.py`** — `EXTERNAL_CUSTOMIZATIONS` set extended with `kill-switch`, `steer`, `commit-on-stop`. Inline-commented as forward-compatibility documentation: the existing `DISPATCH_AGENT_RE` and `INVOKE_SKILL_RE` regexes don't currently match the hooks' phase-spec phrasing (hooks fire from the host, not via agent dispatch — phase specs use markdown links + "the X hook" prose rather than `<name>` hook dispatch patterns), so the set entries don't trigger today; they're listed for the possibility of a future hook-reference regex.

### Changed

- **Adapter wrappers** (`adapters/claude-code/commands/{work,release}.md`, `adapters/antigravity/workflows/{work,release}.md`, `adapters/gemini/commands/{work,release}.toml`) — untouched. All six reference their respective canonical phase spec (`harness/phases/0{3,5}-*.md`) exactly once, so the new sections inherit via the existing canonical-reference pattern without per-adapter edits.

### Internal

- **Task 3 of plan #4** in `.harness/PLAN.md` (base operator-control hooks). Plan #4 is a 5-task project spanning both repos: tasks 1, 2, 4 land in `crickets` (installer + body + docs); task 3 is the harness-side wiring (this release); task 5 is the coordinated release pair (this release + crickets v0.7.0).
- **Design call deviation from plan**: did NOT add a new `INVOKE_HOOK_RE` regex. Hooks fire from the host, not via agent dispatch — there's no "the agent invokes a hook" semantics like there is for sub-agents/skills. Phase-spec phrasing uses markdown links + "the X hook" prose, neither of which matches a `<name>` hook dispatch pattern. EXTERNAL_CUSTOMIZATIONS entries for the three hook names are forward-compatibility documentation; future plans may add a hook-reference regex if needed.
- **Negative test confirmed** the exclusion isn't currently load-bearing: removing `kill-switch` from `EXTERNAL_CUSTOMIZATIONS` doesn't break `check-references` because no existing regex matches the phrasing. Acceptable shape; documented inline.

[v2.2.0]: https://github.com/alexherrero/agentm/releases/tag/v2.2.0

## [v2.1.0] — 2026-05-13 — `/review` augmentable with crickets's `evaluator` (additive)

Additive minor — no breaking changes. The `/review` phase spec gains a new optional **§3b "Optional: evaluator augmentation (crickets)"** documenting how to dispatch the [`evaluator`](https://github.com/alexherrero/crickets/blob/main/agents/evaluator.md) sub-agent (shipped in [crickets v0.6.0](https://github.com/alexherrero/crickets/releases/tag/v0.6.0)) alongside the existing `adversarial-reviewer` flow.

The two reviewers are **complementary, not competing**:

| | `adversarial-reviewer` (§3) | `evaluator` (§3b, crickets) |
|---|---|---|
| **Framing** | "the code contains bugs, find them" | "did this satisfy the rubric?" |
| **Output** | failing test / `file:line` defect / `NO ISSUES FOUND` | `PASS` / `NEEDS_WORK` + per-rubric-item PASS/FAIL |
| **Input** | the artifact + PLAN.md task | the artifact + an explicit rubric |
| **Best when** | rubric is loose; you want defect surfacing | rubric is precise; you want binary judgment |

Both can run in the same `/review` session — their outputs combine into a richer finding set. The harness still works standalone without the toolkit installed: §3b graceful-skips when `crickets` is absent (no `.claude/agents/evaluator.md` / `.agent/skills/evaluator/SKILL.md` / `.gemini/agents/evaluator.md` in the project), and the adversarial-reviewer-only flow continues to satisfy the phase contract.

Paired with [`crickets v0.6.0`](https://github.com/alexherrero/crickets/releases/tag/v0.6.0). The decision rationale for the evaluator's design (read-only allowlist, caller-supplied inline rubric, coexist with adversarial-reviewer not replace) is captured in [crickets ADR 0002](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0002-evaluator-design.md). No new harness-side ADR — this release is the integration surface, not the design decision.

### Added

- **`/review` phase spec §3b — Optional: evaluator augmentation (crickets)** (`harness/phases/04-review.md`). 54-line section between §3a Reconcile and §4 Validate format. Documents:
  - The complementary framing with a side-by-side comparison table vs. adversarial-reviewer.
  - When to add evaluator dispatch (PLAN.md Verification clause is a numbered list of falsifiable claims).
  - When to skip (vague rubric, or `crickets` not installed in the project — graceful-skip silently).
  - The dispatch prompt shape (`ARTIFACT:` + `RUBRIC:` labeled sections drawn from the PLAN.md Verification clause).
  - The output shape (PASS/NEEDS_WORK header + per-rubric-item PASS/FAIL line with citations + final Verdict line).
  - Treat-as-finding semantics: if NEEDS_WORK, the structured output is the `/review` exit artifact (counts as the executable artifact the phase requires).
  - Full-spec pointer to `crickets/agents/evaluator.md`.
- **Cross-repo agent references resolve** (`scripts/check-references.py`). Renamed `EXTERNAL_SKILLS` → `EXTERNAL_CUSTOMIZATIONS` to cover the new agent kind alongside the existing migrated skills. The exclusion now applies to both `DISPATCH_AGENT_RE` and `INVOKE_SKILL_RE` regexes — previously only the skill regex had the exclusion. Inline comments name each entry's `crickets` home (`skills/dependabot-fixer/`, `skills/ship-release/`, `agents/evaluator.md`).

### Changed

- **Adapter wrappers** (`adapters/claude-code/commands/review.md`, `adapters/antigravity/workflows/review.md`, `adapters/gemini/commands/review.toml`) — untouched. All three already reference `harness/phases/04-review.md` exactly once, so §3b inherits via the existing canonical-reference pattern without per-adapter edits.

### Internal

- **Task 3 of plan #3** in `.harness/PLAN.md` (fresh-context evaluator). Plan #3 is a 5-task project spanning both repos: tasks 1, 2, 4 land in `crickets` (installer + body + docs); task 3 is the harness-side wiring (this release); task 5 is the coordinated release pair (this release + crickets v0.6.0).
- **Negative test confirmed**: removing `evaluator` from `EXTERNAL_CUSTOMIZATIONS` immediately produces `FAIL: harness/phases/04-review.md: references` evaluator `sub-agent but harness/agents/evaluator.md is missing` — the exclusion is load-bearing.

[v2.1.0]: https://github.com/alexherrero/agentm/releases/tag/v2.1.0

## [v2.0.0] — 2026-05-12 — `crickets` repo split: `dependabot-fixer` + `ship-release` moved out

**BREAKING:** The `dependabot-fixer` and `ship-release` skills have moved out of this repo into the new sibling repo [`crickets`](https://github.com/alexherrero/crickets). Anyone who relied on them being installed by `agentm/install.sh` must additionally clone `crickets` as a sibling directory and run `bash ../crickets/install.sh <project>` to get those skills back. The harness itself still works on its own for the phase-gated workflow (setup / plan / work / review / release / bugfix); only the two migrated skills are affected.

**Migration:**

```bash
# Clone crickets as a sibling of agentm:
gh repo clone alexherrero/crickets ../crickets

# Refresh harness state (auto-cleans orphaned dependabot-fixer + ship-release paths
# from the v1.x install via the true-sync --update mechanism shipped in v1.0.0):
bash /path/to/agentm/install.sh --update /path/to/your-project

# Install the migrated skills into the same target:
bash ../crickets/install.sh /path/to/your-project
```

`doctor` and `migrate-to-diataxis` remain in this repo — they are harness-setup-specific and harness-shaped, not personal customizations. The harness's `/release` and `/work` phase specs already reference `ship-release` with graceful-skip framing ("install crickets to enable; otherwise cut release manually with `gh release create`"), so a v2.0.0 install without the toolkit still functions — it just falls back to manual release cuts.

Released alongside [`crickets v0.5.0`](https://github.com/alexherrero/crickets/releases/tag/v0.5.0). Decision rationale captured in two parallel ADRs: [agentm ADR 0006 — crickets split](https://github.com/alexherrero/agentm/blob/main/wiki/explanation/decisions/0006-crickets-split.md) (this repo, parity-tax + harness-identity framing) and [crickets ADR 0001 — crickets purpose](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0001-crickets-purpose.md) (toolkit side, sibling-repo purpose + scope).

### Removed

- **`dependabot-fixer` skill** — canonical spec (`harness/skills/dependabot-fixer.md`) + adapter copies (`adapters/claude-code/skills/dependabot-fixer/`, `adapters/antigravity/skills/dependabot-fixer/`). Now lives at [`crickets/skills/dependabot-fixer/`](https://github.com/alexherrero/crickets/tree/main/skills/dependabot-fixer).
- **`ship-release` skill** — canonical spec (`harness/skills/ship-release.md`) + adapter copies (`adapters/claude-code/skills/ship-release/`, `adapters/antigravity/skills/ship-release/`). Now lives at [`crickets/skills/ship-release/`](https://github.com/alexherrero/crickets/tree/main/skills/ship-release).
- Combined removal: 6 files. `scripts/check-parity.sh` `CANON_SKILLS`, `scripts/check-references.py` `SHARED_SKILLS`, and `scripts/validate-adapters.py` `SKILLS` all narrow from 4 entries (`dependabot-fixer`, `doctor`, `migrate-to-diataxis`, `ship-release`) to 2 (`doctor`, `migrate-to-diataxis`). `install.sh` + `install.ps1` shared-skills enumeration trims from 4 to 2. Cross-platform smoke-install + check-integrity scripts updated for the same narrowing.

### Added

- **[ADR 0006 — crickets split](https://github.com/alexherrero/agentm/blob/main/wiki/explanation/decisions/0006-crickets-split.md)** — captures Context (parity-tax scales linearly with personal customizations + harness identity at risk + 11-primitive scope is broader than skills), Decision (sibling repo + byte-identical `lib/install/` + skill-ownership table + public-with-PII-guardrails), Consequences (5 positive + 4 negative + load-bearing assumptions). Cross-references the toolkit's [ADR 0001](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0001-crickets-purpose.md).
- **`lib/install/` shared install plumbing.** Extracted ~80 lines of inline install primitives from `install.sh` + `install.ps1` into a new shared lib byte-identical with `crickets/lib/install/`. Files: `lib/install/bash/primitives.sh` (6 functions: `ensure_boundary_src`, `cp_user`, `cp_managed`, `cp_user_walk`, `cp_managed_dir`, `sync_managed_parents`), `lib/install/pwsh/primitives.ps1` (8 functions; pwsh equivalents + `Copy-AdapterFiles` / `Copy-AdapterDirs`), `lib/install/CONTRACT.md` (caller-contract docs + six behavior invariants), `lib/install/.checksums.txt` (SHA-256 manifest). Both repos consume the same code path; cross-repo edits flow through `scripts/sync-lib.sh` (canonical → sibling). `scripts/check-lib-parity.sh` asserts self-consistency in CI on every push.
- **PII guardrails in CI.** Added `scripts/check-no-pii.sh` (regex scanner, byte-copied from crickets) and `.gitleaks.toml` to this repo. New `pii-guardrails` job in all three per-OS test workflows runs both `check-no-pii.sh` and the official `gitleaks/gitleaks-action@v2`. Defense in depth for personal-path / API-key / email leaks even in the harness repo, which has grown reference examples touching ADR 0006 + the toolkit cross-references.
- **`lib-parity` CI gate.** New job in all three per-OS workflows runs `scripts/check-lib-parity.sh` to assert the committed SHA-256 manifest matches the actual `lib/install/` contents.
- **Graceful-skip framing for migrated skills.** `harness/phases/05-release.md` (ship-release suggestion) and `harness/phases/03-work.md` (feature-flip suggestion) now note "install crickets to enable; otherwise cut release manually with `gh release create`". `harness/skills/doctor.md` probes 3 + 5 (ship-release + dependabot-fixer) gain explicit "skip if not installed" framing — structural skill check now expects only `doctor` + `migrate-to-diataxis`. `harness/telemetry.md` notes the dependabot-fixer signal lives in crickets as of v2.0.0.
- **`check-references.py` `EXTERNAL_SKILLS` set** — `{"dependabot-fixer", "ship-release"}` exclusion lets phase specs reference the migrated skills as graceful-skip suggestions without asserting `harness/skills/<name>.md` exists.
- **Cross-repo docs.** `README.md` Skills section restructured to clearly delineate the two harness-shipped skills (`doctor`, `migrate-to-diataxis`) from the two migrated skills with links to their new toolkit homes. `AGENTS.md` gains a "Personal customizations" section pointing at crickets with sibling-clones layout guidance. `wiki/Home.md`, `wiki/_Sidebar.md`, `wiki/reference/Repo-Layout.md` all gain crickets cross-references; Repo-Layout's Quick Reference table gains rows for the sibling repo and `lib/install/`.

### Changed

- **`install.sh` + `install.ps1`** consume the shared `lib/install/` primitives. Behavior is preserved exactly — same outputs, same idempotence, same `--update` true-sync semantics. The cross-platform debugging journey to make `lib/install/` byte-identity work on Mac + Linux + Windows surfaced four real cross-platform bugs (locale-dependent `sort` collation on Mac, `$host` collision in PowerShell, missing `shasum` in Git Bash on Windows, autocrlf + binary-mode SHA-256 difference) — all fixed before this release tag. Fixes also landed in `.gitattributes` (forces LF on every platform regardless of `core.autocrlf`).
- **Shared-skill delivery narrows from 4 to 2.** `.agents/skills/` (read by Gemini per the Agent Skills standard) now ships only `doctor` and `migrate-to-diataxis` — the two skills that remain harness-owned. Anyone who needs `dependabot-fixer` or `ship-release` installs crickets on top.

### Internal

- **7-task plan (#1) completed.** Tracked in `.harness/PLAN.md`: task 1 (`crickets` repo scaffold + PII guardrails), task 2 (shared `lib/install/` extraction + byte-identity gate), task 3 (real toolkit installer + manifest validator + per-host paths), task 4 (toolkit CI matrix + PII gate in both repos), task 5 (migrate the two skills from harness to toolkit), task 6 (full Diátaxis wiki in toolkit + cross-repo ADRs), task 7 (this release pair). Each task closed with `PLAN.md` mark `[x]` + a `progress.md` append entry.
- **End-to-end byte-identity flow exercised.** Nine commits between the two repos during the plan included parallel commits cross-referencing each other's SHA; the `sync-lib.sh` helper was used for every `lib/install/` edit; `check-lib-parity.sh` ran in CI on every push and gated the parity invariant successfully across all three OSes.
- **CI green across all three per-OS workflows** on every commit in the v1.0.0..v2.0.0 range after the cross-platform fixes landed.

[v2.0.0]: https://github.com/alexherrero/agentm/releases/tag/v2.0.0

## [v1.0.0] — 2026-05-11 — Three-adapter scope; Codex dropped; 1.0.0 commitment

**BREAKING:** Codex adapter removed. Supported hosts narrow from four (Claude Code, Antigravity, Codex, Gemini CLI) to three (Claude Code, Antigravity, Gemini CLI). Anyone running agentm through Codex must migrate to one of the three remaining adapters — the phase-gated workflow itself is host-agnostic, so migration is install + relearn the host-specific invocation surface.

The version bump from 0.9.x to 1.0.0 reflects the breaking change *plus* a commitment: the harness has had enough churn (v0.1.0 → v0.9.0) to feel stable, and semver becomes firm going forward — major = breaking, minor = additive, patch = fixes. Future host removals, fundamental shape changes, or invariant inversions become explicit major-version events. Additive changes (new skills, the planned `crickets` repo split, ContextVault, design skill) become clear minor bumps. See [ADR 0005](https://github.com/alexherrero/agentm/blob/main/wiki/explanation/decisions/0005-drop-codex-support.md) for the full decision narrative.

### Removed

- **Codex adapter** (`adapters/codex/`, 15 files: README, 4 sub-agents in TOML, 10 skill dirs — 7 `harness-` prefixed phase-commands-as-skills + 4 shared skills).
- **Codex adapter research note** (`harness/agents/codex-adapter-research.md`, 294-line deep-dive on Codex-specific design — dead weight once the adapter is gone).
- **Codex-specific code in scripts**: `scripts/check-parity.sh`'s `== codex ==` block + divergence comments; `scripts/check-references.py`'s `CODEX_PHASE_PREFIX` constant + codex branch in `expected_canonical_for`; `scripts/validate-adapters.py`'s `validate_codex_agents()` function + codex skills-dir entry; codex expected-files lines in `scripts/smoke-install-{bash,pwsh}` and `scripts/check-integrity-{bash,pwsh}`.

### Added

- **[ADR 0005 — Drop Codex support; three-adapter scope](https://github.com/alexherrero/agentm/blob/main/wiki/explanation/decisions/0005-drop-codex-support.md)**: documents Context (5 reasons codex was dropped), Decision (7 concrete actions including the v1.0.0 framing), Consequences (5 positive + 4 negative), and load-bearing re-audit assumptions.
- **True-sync `--update` semantics.** `install.sh` and `install.ps1` now wipe twelve fully-harness-authored subdirs before recreating from source on `--update`. Orphan paths from previous versions (e.g. `.codex/` for users upgrading from v0.9.0) are automatically removed and reported as `removed legacy <path>/`. User state files at `.harness/` root, merged `settings.json` files, `wiki/**`, and root `AGENTS.md`/`CLAUDE.md` are deliberately preserved. The generalized mechanism means future host or skill removals also clean up automatically — codex is the first user, not a special case. Documented in [Update-Installed-Harness](https://github.com/alexherrero/agentm/blob/main/wiki/how-to/Update-Installed-Harness.md).
- **`no-Co-Authored-By` convention** added to `AGENTS.md` + `CLAUDE.md`. Host-agnostic rule: agents do not append `Co-Authored-By:` trailers naming the model or host. Sole-author-of-intent framing.

### Changed

- **Adapter parity narrows to three hosts.** `check-parity.sh` enforces the new canonical set; no more `harness-` prefix divergence (was only needed for Codex's collision with built-in `/plan` and `/review`). Removing the prefix requirement simplifies the parity invariant.
- **Shared-skill delivery becomes explicit.** `.agents/skills/` (read by Gemini per the Agent Skills standard) was previously delivered by the Codex install block as a side effect. Now `install.sh` explicitly enumerates the four shared skills (`dependabot-fixer`, `doctor`, `migrate-to-diataxis`, `ship-release`) and sources them from `adapters/claude-code/skills/` (parity-enforced identical content; cleanest source — `antigravity/skills/` would over-deliver because it mixes sub-agents-as-skills).
- **Repo-public surfaces scrubbed of Codex mentions.** README "works with" chip row drops Codex (now 3 chips); intro paragraph drops Codex from host list; Mermaid Host node collapses to single-line three-host listing; AGENTS.md intro tool-list and Co-Authored-By convention examples drop Codex; `harness/skills/doctor.md` and `ship-release.md` adapter tables drop Codex; `adapters/gemini/README.md` reframes 5 Codex-block references to actual `install.sh`/Agent Skills standard delivery; `harness/agents/gemini-adapter-research.md` 10 codex-comparison phrasings reframed to current state.
- **Wiki updated to three-adapter shape.** 9 wiki pages scrubbed across `explanation/` (Product-Intent, GitHub-Projects-Integration, How-The-Pieces-Fit including 2 ASCII diagrams), `how-to/` (Cut-A-Release, Install-Into-Project, Update-Installed-Harness with new v1.0.0 sync semantics section), `tutorials/01-First-Install.md`, and `reference/` (Repo-Layout with rewritten three-adapters table + Quick Reference + tree diagram, Installer-CLI with v1.0.0 sync details). Home.md and _Sidebar.md gain ADR 0005 row in Decisions section. Historical entries in CHANGELOG.md and `wiki/reference/Completed-Features.md` past-release sections deliberately preserved as history.
- **README polish (multi-commit run).** Mirrored install commands across MacOS/Linux and Windows sections; eliminated redundant license callout; QoL updates (badges, "works with" chip row, Mermaid architecture diagram); intro paragraph framing tightened around production-quality engineering posture.

### Internal

- **5-task Codex-removal sweep plan completed.** Tracked in `.harness/PLAN.md`: task 1 (adapter dir + canonical specs + scripts cleanup with the `codex-adapter-research.md` deletion decision); task 2 (installers + smoke tests — flagged Gemini's shared-skill delivery as the load-bearing risk and resolved it by sourcing from `claude-code/skills/`); task 2 amendment (true-sync `--update` generalized beyond codex cleanup per user direction "github is SoT for my personal setup"); task 3 (README + CONTRIBUTING + AGENTS + CLAUDE + adapter READMEs scrub); task 4 (wiki + ADR 0005); task 5 (release).
- **Stats**: 26 files changed task 1 (25 ins, 1204 del). 2 files changed task 2 (25 ins, 19 del). 2 files changed task 2 amendment (96 ins). 4 files changed task 3 (12 ins, 13 del). 12 files changed task 4 (99 ins, 27 del). Net: ~46 files touched, ~1300 lines removed.
- **CI green across all three per-OS workflows** on every commit in the range. Smoke tests intentionally not run as a gate during task 1 (install.sh was inconsistent post-adapter-removal); reintroduced as a gate in task 2 where the installer fix landed.

[v1.0.0]: https://github.com/alexherrero/agentm/releases/tag/v1.0.0

## [v0.9.0] — 2026-04-23 — Diátaxis documentation spec + `/doctor` skill

Two substantial threads landed together. First, the 7-task Diátaxis rollout (ADR 0004): wiki scaffold and dogfood wiki reshaped to the four-mode layout (tutorials/how-to/reference/explanation), `documenter` rewired to write to the new mode dirs, `scripts/check-wiki.py` shipped as a structural gate and flipped to `--strict` in CI, and a `migrate-to-diataxis` skill for one-shot migration of already-installed projects. Second, a new user-invocable `/doctor` skill — companion to `telemetry.sh` — that verifies the harness install is correctly wired up in the host, with an opt-in `--live` mode that actually dispatches each sub-agent and dry-runs each skill to prove end-to-end wiring.

### Added

- **`/doctor` skill** — verifies an installed harness is correctly wired up. Default mode runs structural discovery only: expected phase commands, sub-agents, skills, state files, and hooks present and parseable in the detected adapter (<5s, no tokens). `--live` adds six real probes: `explorer` dispatch on a trivial filesystem prompt, `adversarial-reviewer` dispatch requiring an executable artifact (not prose), `ship-release --dry-run`, `migrate-to-diataxis` preview on an already-migrated tree, `dependabot-fixer` no-match path, and a hook synthetic trigger. Probes stop at the first foundational failure and never mutate repo state. Canonical spec at [`harness/skills/doctor.md`](https://github.com/alexherrero/agentm/blob/main/harness/skills/doctor.md); adapter wrappers for claude-code, antigravity, and codex (Gemini reuses the Codex delivery). `check-parity.sh` CANON_SKILLS and `check-references.py` SHARED_SKILLS extended. First dogfood run caught a spec bug (phase-command frontmatter doesn't carry a `name:` field) and shipped the fix in the same release.
- **`migrate-to-diataxis` skill** — one-shot preview-first migration of an already-installed project's `wiki/` to the Diátaxis four-mode layout. Classifies each page (ADR, Status, How-to, Tutorial, Reference, Explanation, Mode-mixed), proposes a tree of `git mv`s to preserve blame, surfaces mode-mixed pages for manual split, and writes `wiki/.diataxis` to enable strict lint. Non-destructive; preview is always first.
- **Diátaxis wiki scaffold in the template** — `templates/wiki/` reshaped to `tutorials/`, `how-to/`, `reference/`, `explanation/`, with `wiki/.diataxis` marker, updated `_Sidebar.md`, and Diátaxis-shaped starter content. New installs land directly in the four-mode layout.
- **Mode-aware `documenter` writes** — the `documenter` sub-agent now dispatches with mode-specific write targets per phase. `/plan` → `wiki/explanation/` (feature pages) and `wiki/reference/` (subsystem pages), `/work` → `wiki/how-to/` (recipes), `/release` → `wiki/explanation/decisions/` (ADRs) and `wiki/reference/Completed-Features.md`, `/bugfix` → `wiki/reference/` (Known-Issues) and `wiki/explanation/decisions/`.
- **`scripts/check-wiki.py`** — Diátaxis structural lint with 11 rules (a–k): mode purity, ADR append-only + `Status: accepted|superseded|rejected`, orphan-link detection, globally-unique filenames, no banned-headings-per-mode. Shipped as warn-only in the same release; flipped to `--strict` (blocks PRs) in `tests-linux.yml`. Negative-test fixtures at `scripts/fixtures/check-wiki/` exercise each rule.
- **[ADR 0004](https://github.com/alexherrero/agentm/blob/main/wiki/explanation/decisions/0004-diataxis-documentation-spec.md)** — Diátaxis documentation spec. Supersedes ADR 0002's audience-based layout (`wiki/{development,operational,design,architecture}/`) with the four Diátaxis modes. Rationale, consequences, migration path all captured.
- **`CONTRIBUTING.md`** — newly extracted from the previous README's Contributing and Status sections. Documents the three-workflow per-OS CI matrix, the full "what CI verifies without an agent" bullet list, the installer-boundary invariant, and the local-gate command set (bash and pwsh).

### Changed

- **Harness phase specs retargeted to Diátaxis mode dirs.** `harness/phases/02-plan.md`, `03-work.md`, `05-release.md`, and `harness/pipelines/bugfix.md` previously dispatched `documenter` at the old audience dirs (`wiki/development/`, `wiki/operational/`, `wiki/design/`, `wiki/architecture/`); they now point at the correct Diátaxis equivalents. `harness/documentation.md` gains a new "Migrating an existing install" section pointing at the `migrate-to-diataxis` skill, and the Non-goals list acquires a "five-mode extensions" bullet.
- **Dogfood wiki reshaped to Diátaxis layout.** The agentm repo's own `wiki/` migrated file-by-file with `git mv` to preserve blame. ADRs moved to `wiki/explanation/decisions/`, feature pages to `wiki/explanation/`, how-to recipes to `wiki/how-to/`, reference tables to `wiki/reference/`. `Completed-Features.md` consolidated to `wiki/reference/Completed-Features.md`.
- **README simplified** — trimmed from 126 → 64 lines. Install section kept concise with a pointer to `wiki/how-to/Install-Into-Project.md`; the six-point Principles list collapsed to a one-sentence lead with a link to `harness/principles.md`; CI and contributing details extracted to the new `CONTRIBUTING.md`; Skills table gained `migrate-to-diataxis` and (later in the release) `doctor`.
- **`check-parity.sh` and `check-references.py` extended** with the two new shared skills (`doctor`, `migrate-to-diataxis`). Each ships in claude-code, antigravity, and codex; Gemini reuses the `.agents/skills/` delivery.

### Fixed

- **ProjectsV2 `/setup` flow regression** (pre-v0.9.0 drift) caught during the v0.8.7 cut — the v0.8.7 release note already covered the linkage fix, but the CHANGELOG wording understated how subtle the `@me`-vs-literal-owner gh-CLI quirk was. Documented in the ADR 0003 update for v0.8.7 readers who hit it during migration.
- **`doctor` frontmatter rubric** — the doctor skill's initial spec required a `name:` field on every surface's frontmatter. The first dogfood run revealed that Claude Code phase commands, Antigravity workflows, and Gemini TOML commands intentionally have no `name:` field (name is implicit from the filename). The spec and all three adapter wrappers now require `name:` match only on surfaces that actually carry the field (sub-agents + skills), preventing false-positive FAIL rows on every valid install.

### Internal

- **7-task Diátaxis rollout plan completed.** The full sequence was tracked in `.harness/PLAN.md` and released as a single coherent thread: task 1 (lint script), task 2 (template scaffold), task 3 (dogfood wiki reshape), task 4 (documenter mode-aware writes), task 5 (migrate-to-diataxis skill), task 6 (flip lint to `--strict`), task 7 (harness spec retargeting). Each task closed out with `PLAN.md` mark `[x]` and a `progress.md` append entry. Plan Status flipped to `done`.
- **First end-to-end exercise of `/doctor`.** Ran `/doctor` structurally against a scratch install (fresh `install.sh --hooks`) and the two highest-leverage `--live` probes (`explorer` + `adversarial-reviewer`) against the harness repo itself. The structural run surfaced the `name:` rubric bug; the live probes confirmed both sub-agents return within spec (explorer: 7.3s returning two absolute paths; adversarial: 10.8s returning a `file:line` pointer plus a failing pytest body, not prose).
- **CI green across all three per-OS workflows** on every commit in the range. Linux validate job reports `check-wiki: 0 structural issue(s), 0 soft warning(s)` after the dogfood reshape. Installer-boundary invariant holds (scratch install under smoke test never receives test infra).

[v0.9.0]: https://github.com/alexherrero/agentm/releases/tag/v0.9.0


## [v0.8.7] — 2026-04-21 — GitHub Projects integration (the Issues-lifecycle's deferred-work half) + documenter end-to-end dogfood

Closes the symmetric gap opened by v0.8.2: where `/bugfix` maintains a public GitHub Issue as bug posterity, now `/plan`, `/work`, `/review`, and `/release` each offer to file deferred-work items to a user- or org-owned ProjectsV2 board linked to the repo. Opt-in at `/setup`, preview-and-ask at every `gh` call, graceful-skip when the project isn't configured. Parallel track: the first end-to-end exercise of the `documenter` sub-agent's `/release` contract, flipping two feature flags and adding three new wiki pages + an ADR.

### Added

- **`gh project item-create` offer wired into every phase.** `/plan` proposes from the plan's `## Out of scope` section; `/work` from out-of-task-scope findings noticed while implementing; `/review` from deferred-rather-than-blocked findings; `/release` from cross-session themes. Each phase batches its proposals into a single preview, preview-and-ask on every invocation, graceful-skip when `.harness/project.json` is absent or `gh` is unavailable. Canonical blocks in `harness/phases/{02-plan,03-work,04-review,05-release}.md` with adapter-parity across all four adapters (claude-code, antigravity, codex, gemini — 20 adapter files touched). See the new [`wiki/design/features/GitHub-Projects-Integration.md`](https://github.com/alexherrero/agentm/blob/main/wiki/design/features/GitHub-Projects-Integration.md) for the feature page and [ADR 0003](https://github.com/alexherrero/agentm/blob/main/wiki/architecture/decisions/0003-ProjectsV2-Ownership-And-Linking.md) for the ownership-and-linking decision.

### Fixed

- **ProjectsV2 `/setup` flow now links the project to the repo.** The initial implementation created a user-scoped project that didn't appear under `github.com/<owner>/<repo>/projects`. ProjectsV2 has no repo-owned form — the fix is a two-step `gh project create` + `gh project link --repo <owner>/<repo>` flow at `/setup` step 8. `.harness/project.json` schema gains a `repo` field recording the linkage. Includes the `@me`-vs-literal-owner gh-CLI quirk as an inline code comment (passing `@me` to `gh project link --owner` sometimes fails with *"'<repo>' has different owner from '@me'"* even when they match). Rationale and consequences are in [ADR 0003](https://github.com/alexherrero/agentm/blob/main/wiki/architecture/decisions/0003-ProjectsV2-Ownership-And-Linking.md).
- **Dropped the "at most 1 per session" cap on Project-item proposals.** Early drafts capped at one item; in practice a single `/work` or `/review` session can legitimately surface multiple deferred findings, and silent misses are worse than a user seeing a three-item batched preview. Replaced with a quality-bar-plus-batching rule: propose one item per distinct finding, batch into a single preview at phase end, per-phase soft caps as reminders rather than hard limits. Applied uniformly across all 20 canonical + adapter files.

### Internal

- **First end-to-end exercise of the `documenter` sub-agent.** Invoked per its `/release` contract (`harness/agents/documenter.md §/release`) with plan-to-HEAD diff + the current `wiki/` tree. Returned the canonical structured report (FILES CREATED / EDITED / OPEN QUESTIONS / NO-OP CATEGORIES). Outputs: new Feature page for GitHub-Projects-Integration (Template 2, Status: implemented), new ADR 0003 (Template 3, Status: accepted), new `wiki/development/Completed-Features.md` (Template 1 with overview table), Home.md + _Sidebar.md updated for the new pages. All three OPEN QUESTIONS resolved without further docsub edits. Flipped `feat-documenter-subagent.passes` and `feat-gh-projects-integration.passes` to `true` in `features.json`.
- **README refreshed against v0.8.2 drift.** Stale "v0.1" Status block replaced with a CHANGELOG pointer; Skills table gained `ship-release` (which shipped in v0.8.0 but was never cross-linked); `/bugfix` Phases row expanded with the Issue-posterity lifecycle; `documenter` sub-agent named in the intro + Install "drops in" list; new bullet for the `wiki/` + `.github/workflows/wiki-sync.yml` pair. Install / Contributing / License untouched.
- **ADR 0002 updated** with the runtime installer-boundary guard shipped in v0.8.2. Section 4 split into Runtime-guard vs. Test-time-assertions subsections, Consequences bullet rewritten for copy-time enforcement. Matches the `ensure_boundary_src` / `Ensure-BoundarySrc` implementation in `install.sh` / `install.ps1`.
- **Windows boundary-guard test coverage.** Added `scripts/test-install.ps1` (PowerShell twin of `scripts/test-install.sh` with all 5 checks a–e). Wired into `.github/workflows/tests-windows.yml` install-smoke job. Ensures the installer-boundary regression class caught by Defect 2 of [#1](https://github.com/alexherrero/agentm/issues/1) is guarded on both OSes.

[v0.8.7]: https://github.com/alexherrero/agentm/releases/tag/v0.8.7

## [v0.8.2] — 2026-04-20 — First bugfix cycle + installer-boundary runtime guard

Three changes shipped together, themed around closing the loop on v0.8.0's documentation convention. (1) The wiki-sync workflow shipped in v0.8.0 as a template was never activated in the harness repo itself — this release activates it and adds a CI gate so the class of omission can't recur. (2) `/bugfix` now maintains a GitHub Issue as the public posterity record across all four phases, turning every bug's trajectory into a searchable narrative. (3) The installer boundary gains a runtime guard, with a test that proves it catches the exact regression scenario flagged by the adversarial reviewer.

### Fixed

- **`wiki/` not syncing to the GitHub Wiki** ([#1](https://github.com/alexherrero/agentm/issues/1)). Root cause: `.github/workflows/wiki-sync.yml` was missing from the harness repo — v0.8.0 shipped the template at `templates/.github/workflows/` but no one activated it in this repo's own `.github/workflows/`. Every push since v0.8.0 had skipped the sync. Fix: copied the template byte-identical to `.github/workflows/wiki-sync.yml`, added `workflow_dispatch:` for backfill + manual re-sync, and a new `dogfood-workflows` job in `tests-linux.yml` that loops every `templates/.github/workflows/*.yml` and asserts a byte-identical counterpart exists at the repo root — so the class of bug can't recur.

### Changed

- **`/bugfix` now maintains a GitHub Issue as the bug's posterity record.** Phase 1 (Report) opens the tracking issue with title + body preview; Phase 2 (Analyze) posts the Analysis; Phase 3 (Fix) posts the Fix summary with commit SHA; Phase 4 (Verify) posts the Verify summary and closes the issue with `gh issue close --reason completed`. Every `gh issue *` call is preview-and-ask per `harness/documentation.md` — no silent automation. Graceful-skip if `gh` is unavailable or the repo isn't on GitHub. Propagated to all four adapter `bugfix` specs (Claude Code / Antigravity / Codex / Gemini).

### Internal

- **Installer-boundary runtime guard.** `install.sh` and `install.ps1` now call `ensure_boundary_src` / `Ensure-BoundarySrc` inside every copy helper (`cp_user`, `cp_managed`, `cp_managed_dir` and their pwsh twins). The guard rejects source paths outside `$HARNESS_ROOT/templates/` or `$HARNESS_ROOT/adapters/` with a loud boundary-violation message. `scripts/test-install.sh` gains check (e) that mutates `install.sh` in place via `sed` — rewriting the wiki-sync `cp_managed` source to the source-repo mirror — runs the mutated installer, and asserts the guard fires with non-zero exit. Addresses Defect 2 from the [#1](https://github.com/alexherrero/agentm/issues/1) adversarial review: after `.github/workflows/wiki-sync.yml` became byte-identical to its template by design, a silent `install.sh` regression copying from the source-repo path would have been undetectable — the new guard makes it impossible.
- **`.gitignore`** — exclude `.claude/scheduled_tasks.lock` and `.claude/worktrees/` (local Claude Code artifacts).

[v0.8.2]: https://github.com/alexherrero/agentm/releases/tag/v0.8.2

## [v0.8.1] — 2026-04-20 — CI hardening + dogfood wiki

Follow-up to v0.8.0. Tightens the cross-platform CI gate suite, ships the agentm repo's own wiki as a worked example of the v0.8.0 documentation convention, and fixes a PowerShell parse regression in the verify.ps1 template.

### Fixed

- `templates/verify.ps1` — empty `switch` statement (all clauses commented out) failed to parse on pwsh hosts with "Missing condition in switch statement clause". Added a required `default { }` clause so the template parses as shipped. Caught by the cross-platform CI added in v0.8.0.

### Internal

- **Cross-platform harness-integrity CI** — beyond install-smoke, the three per-OS workflows now run `check-parity.sh`, `validate-adapters.py`, `check-references.py`, `check-syntax.{sh,ps1}`, and `check-integrity-{bash,pwsh}` against a scratch install on every push / PR. A POSIX path-separator bug in `check-references.py` surfaced as part of this work and was fixed.
- **Dogfood wiki** — `wiki/` at repo root now contains this project's own documentation under the v0.8.0 convention: Home, Sidebar, one page per subdir (Getting-Started / Runbook / Product-Intent / Overview), plus ADRs 0001 (phase-gated workflow) and 0002 (documentation convention). The installer boundary is preserved — `install.sh` still copies only from `templates/wiki/`, never from this repo's own `wiki/`.
- **Dedicated installer-boundary test** — `scripts/test-install.sh` runs `diff -r templates/wiki/ <scratch>/wiki/` byte-for-byte plus a SHA-256 hash-based leak detector for each file under `$HARNESS_ROOT/wiki/`, wired into Linux CI. Proves the boundary on every PR.

[v0.8.1]: https://github.com/alexherrero/agentm/releases/tag/v0.8.1

## [v0.8.0] — 2026-04-19 — Documentation convention, three new full-parity adapters, Windows support, release automation

This is the largest release in the project's history. Four themes: (1) a first-class
documentation convention with a dogfooded wiki scaffold and per-phase documenter
sub-agent; (2) two new full-parity adapters (Codex CLI, Gemini CLI) plus the
existing Antigravity adapter expanded from README-only to full parity with
Claude Code; (3) cross-platform support — `install.ps1` and PowerShell twins
of every Unix helper, with CI validating install + parity on Linux, macOS,
and Windows; (4) a new `ship-release` skill that automates this exact kind
of tag cut going forward.

### Added

- **Documentation convention** — `harness/documentation.md` specifies a
  four-section `wiki/` scaffold (architecture / development / features / operational)
  that `install.sh` drops into every project on `/setup`. A canonical
  `documenter` sub-agent (`harness/agents/documenter.md`) is dispatched post-gates
  during `/work` and does a full-pass sweep during `/release` — flipping
  `Status: pending → implemented` only when the diff proves it.
- **`wiki-sync` GitHub Action** — pushes to the default branch mirror `wiki/`
  content to the GitHub Wiki (collision-checked, graceful-skip when the wiki
  is disabled). Ships via `install.sh` per-file walk.
- **Antigravity adapter full parity** — expanded from README-only to 4 agents
  (adversarial-reviewer, adversarial-reviewer-cross, documenter, explorer),
  6 workflow commands, the `dependabot-fixer` skill, and an always-on rules
  file. Installed into `.agent/`.
- **Codex CLI adapter** — new full-parity adapter. 7 skills (6 phase commands
  prefixed `harness-` to avoid colliding with Codex built-ins, plus
  `dependabot-fixer`), 4 TOML sub-agents with `sandbox_mode` specs, README
  documenting divergences. Installed into `.agents/skills/` and `.codex/agents/`.
- **Gemini CLI adapter** — new full-parity adapter. 6 native TOML slash
  commands, 4 markdown sub-agents with YAML frontmatter + tool allowlists,
  `settings.json` wiring `AGENTS.md` via `context.fileName`. Shared skills
  (`dependabot-fixer`, `ship-release`) reused from the `.agents/skills/`
  delivery. Installed into `.gemini/`.
- **Windows cross-platform support** — `install.ps1` at repo root with
  semantic parity to `install.sh` (PowerShell 7+). PowerShell twins of
  `verify.sh`, `precompact.sh`, `session-start-compact.sh`, and
  `cross-review.sh` ship alongside the Unix versions. Hook JSON is factored
  into canonical `settings-fragment-{bash,pwsh}.json` fragments so each
  installer reads the correct shell invocation.
- **Harness-repo CI** — three per-OS workflows (`tests-linux.yml`,
  `tests-mac.yml`, `tests-windows.yml`) gate every PR. Linux runs
  `install-smoke` + `adapter-parity` + `validate`; macOS and Windows run
  install-smoke via bash and pwsh respectively. Each smoke test asserts
  the installer boundary — `tests-*.yml`, `scripts/*`, and repo-root `wiki/`
  never propagate to installed projects.
- **`ship-release` skill** — auto-sized semver releases from conventional
  commits (`feat!` / `BREAKING CHANGE` → major, `feat:` → minor,
  `fix:`/`perf:`/`refactor:` → patch, `docs:`/`chore:`/`ci:` → no-bump).
  Writes `CHANGELOG.md`, tags, pushes, creates the GitHub release.
  Aborts if the tree is dirty, the default branch isn't pushed, or the
  tag already exists. Canonical spec at `harness/skills/ship-release.md`
  with adapter SKILL.md in claude-code / antigravity / codex.
- **Parity + validation scripts** — `scripts/check-parity.sh` asserts each
  adapter ships the canonical set of phase-commands, sub-agents, and skills
  (with documented divergences). `scripts/validate-adapters.py` parses all
  TOML, YAML frontmatter, and JSON across every adapter.
- **Contributing section** in README documenting CI matrix + local
  invocation commands.

### Changed

- **Phase specs wired to `documenter`** — `/setup`, `/plan`, `/work`
  (post-gates), and `/release` now dispatch the documenter sub-agent;
  `/review` gets an explicit not-invoked note to prevent docs drift from
  biasing the critic. `/bugfix` dispatches documenter on resolution.
- **Phase specs suggest `ship-release`** — `/work` suggests it when a
  feature's `passes` flag flips true; `/release` recommends it as the
  post-merge follow-up to the pre-merge gate.
- **`install.sh` per-file walk semantics** for `wiki/` — user-edited pages
  never get clobbered; new scaffold pages merge in cleanly.
- **`install.sh` boundary comment** — clarifies which directories are
  harness-authored (refreshed on `--update`) vs. user-owned (`cp_user`).
- **Hook settings factored** — `templates/hooks/settings-fragment-bash.json`
  and `-pwsh.json` are now the canonical source both installers read,
  mitigating JSON drift between the two.

### Fixed

- **PowerShell `ConvertTo-Json` array unwrap bug** — `install.ps1` now
  uses `ConvertFrom-Json -AsHashtable` throughout and stores hook-event
  arrays as `List[object]`, preventing single-element array unwrap that
  would have broken Claude Code's hook loader schema on Windows.

### Internal

- Research notes for Codex CLI conventions (`harness/agents/codex-adapter-research.md`)
  and Gemini CLI conventions (`harness/agents/gemini-adapter-research.md`) —
  both answer the research questions + open questions that informed the
  final adapter layouts.
- Installer-boundary assertion is now load-bearing in CI — break-each-invariant
  reproducers verified: rogue file → parity fails, corrupt TOML → validate
  fails, renamed subagent → parity fails, broken `install.sh` → smoke fails.

[v0.8.0]: https://github.com/alexherrero/agentm/releases/tag/v0.8.0
[v0.5.1]: https://github.com/alexherrero/agentm/releases/tag/v0.5.1
[v0.5.0]: https://github.com/alexherrero/agentm/releases/tag/v0.5.0
[v0.4.0]: https://github.com/alexherrero/agentm/releases/tag/v0.4.0
[v0.3.0]: https://github.com/alexherrero/agentm/releases/tag/v0.3.0
[v0.2.0]: https://github.com/alexherrero/agentm/releases/tag/v0.2.0
[v0.1.0]: https://github.com/alexherrero/agentm/releases/tag/v0.1.0
