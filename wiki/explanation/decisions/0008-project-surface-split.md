# ADR 0008 — Project surface split: separate agentm + crickets GitHub Projects

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-06-03

## Context

The operator maintains a roadmap that spans **two sibling repos** — `agentm` (memory/harness engine) and `crickets` (opinionated plugin catalog), carved apart by [ADR 0006](0006-crickets-split). For ~2 weeks (2026-05-18 → 2026-06-03) work across both repos was tracked in **one shared GitHub Project** at `alexherrero/projects/2`. That single Project was the "human-readable mirror" of the vault-side `ROADMAP-MASTER.md` per [V4 #41's design](../../../.harness/designs/v4-41-project-human-source.md): real GitHub Issues nested as Version → Roadmap-entry → Plan → Task; frozen 6-field schema (Track / Type / Priority / Start / Target / Status); Track values spanning V0 → V7 + FRIDAY + Hardening I/II + Crickets v3.0 + Crickets v3.x + Operator-personal.

Two pressures surfaced as the project layer matured:

- **The unified Project conflated repo identities.** Every Issue lived in `alexherrero/agentm` (the Project's home repo) — including 14 Crickets v3.x bundle entries (#19–#31) for work that would ship from the crickets repo. That worked while crickets was still install-coupled to agentm via `lib/install/` byte-sync, but [ADR 0014](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0014-install-decoupling.md) (#40 — retired the bespoke installer + lib-sync on 2026-06-03) made the two repos genuinely independent. The unified Project surface no longer matched the repo split.
- **The crickets-track buckets were becoming the bigger half.** Bucket ④ Crickets v3.x catalogs 12 opinionated bundles in four waves. Each bundle gets its own plans + tasks once active. The agentm Project would shortly carry more crickets-track items than memory-track items, drowning the V4/V5/V6 trajectory.

The split had been **pre-locked in `ROADMAP-MASTER.md`** at 2026-05-31 (deferred to a trigger), narrowed at 2026-06-01 (single gate: V4 ships fully = #23 closes + paired release lands). On 2026-06-03 the paired pair shipped — agentm `v4.14.0` (decouples from crickets) + crickets `v3.0.0` (native host plugins) — satisfying the trigger. This ADR records the resulting execution.

**Open questions the decision resolves:**

- Two Projects or one? If two: how do cross-repo dependencies render?
- Where does Operator-personal (bucket ⑨ — operator's vault content + Obsidian setup) live, given it touches both sides?
- What's the execution shape — purely mechanical move, or collaborative?
- How does the unified narrative survive a structurally split Project surface?

## Decision

### 1. Two separate user-level GitHub Projects, not one cross-repo

| Project | URL | Owns |
|---|---|---|
| **agentm** | https://github.com/users/alexherrero/projects/2 | V0 / V1 / V2 / V3 / V4 / V5 / V6 / FRIDAY / V7 + Hardening I + Hardening II + Operator-personal + anything agentm-specific. The memory/harness engine arc. |
| **crickets** (NEW) | https://github.com/users/alexherrero/projects/5 | Crickets v3.0 + Crickets v3.x (the bundle catalog) + Crickets v3.x+ (future evolution) + Backlog + Ideas + anything crickets-specific (including refining the operator's opinionated workflows). The plugin catalog. |

**Why not keep one unified Project:** the unified surface required the operator to scan past 14+ Crickets v3.x bundle entries (each with its own plans + tasks once active) to find V5/V6 trajectory. Per-repo Projects align with where issues live; each gets a focused identity (memory-arc story vs. plugin-catalog story). The unified-narrative role moves up a layer — to the vault-side `ROADMAP-MASTER.md`, which already played that role and continues to.

**Why not one Project at the repo level (e.g. `alexherrero/agentm/projects/N`):** repo-level Projects were attempted via V4 #41's earlier design and abandoned in favor of user-level for cross-repo issue visibility. User-level retains the ability to include cross-repo cards (see decision #3 below) without forcing one repo's Project to "own" another repo's work.

### 2. Surface ownership: Operator-personal stays in agentm

Bucket ⑨ Operator-personal (`#16` consolidation, `#29` PKM, `#34` vault aesthetic) lives in the agentm Project, not crickets, not cross-listed.

**Why agentm and not crickets:** the operator's vault content (the AgentMemory tree at `<vault>/projects/agentm/_harness/`) physically lives alongside agentm's per-project state. The work-track touches vault content, not plugin code. The plugin-side angle (any vault-shaped bundle UX) is covered by Crickets v3.x bundles #15 *Ideating* + #16 *Personal-notes* — those are *implementations* of how plugins serve operator-personal use cases. The work *itself* stays operator-content-curation, not plugin-development.

**Why not cross-listed:** GitHub Projects v2 lets any issue appear in multiple Projects, but comment-timeline ownership gets fuzzy. With a single maintainer (operator) the friction is low, but the principle still applies: pick one canonical home so future automation has a deterministic answer. Cross-listing reserves itself for genuine cross-repo dependencies (decision #3).

### 3. Cross-project dependencies ride the issue graph, not the Project layer

GitHub Projects v2 has **no native "Project A depends on Project B milestone" relationship**. Three mechanisms together cover the practical need:

1. **Cross-repo sub-issues.** `addSubIssue` mutation works across repos. The agentm V5 Version Issue can have `crickets#X` bundle sub-issues nested under it; the V5 Version's sub-issue progress field reflects them live.
2. **`Blocked by` references.** `Blocked by alexherrero/crickets#<n>` in an agentm Issue body auto-renders the cross-repo blocker as a linked card with live status.
3. **Cross-Project item-add.** Either Project can include any repo's Issues as visible cards. Reserved for cases where a bundle is genuinely load-bearing for an agentm milestone.

**Why not wait for Anthropic-of-GitHub to ship native cross-Project dependencies:** the issue graph already covers the substance; the missing feature is just a *rendering* of dependencies in the Project board UI, not the dependency mechanism itself. Sub-issue progress + `Blocked by` references already give us the live status without needing GitHub to ship anything new.

### 4. Execution = four collaborative + automated phases, not six mechanical steps

The split executed as:

- **(a) Review together what goes into crickets** — collaborative classification pass. Agent proposed; operator confirmed (with the Operator-personal call landing here). Output: a 17-item transfer inventory.
- **(b) Automated move** — agent ran: created crickets Project + mirrored the 6-field schema · transferred 17 Issues `alexherrero/agentm` → `alexherrero/crickets` via `gh issue transfer` (preserving history + comments + labels; numbers re-assigned 4-31 → 1-17) · added to crickets Project + restored field values from a pre-transfer snapshot · sub-issue parent-child relationships **survived the transfer natively** (no re-wiring required — Crickets v3.0 Version #1 retained its 2 children; Crickets v3.x Version #2 retained its 13 children) · removed the 17 transferred items from agentm Project · updated `ROADMAP-MASTER.md § Project surface split` with live URLs.
- **(c) Operator creates the views** — same 7-view recipe applied to the new crickets Project (manual web-UI; Projects v2 has no view-creation API).
- **(d) Stub all pending templates** — Issue Templates' `config.yml` in both repos updated to point at the post-split Project URLs.

**Why not pure mechanics:** step (a) is genuinely collaborative — the Operator-personal classification call cannot be derived from a rubric. Step (c) is irreducibly manual (the API doesn't expose view-creation mutations). A mechanical-only 6-step list would have skipped both touchpoints.

## Consequences

**Positive**

- **agentm Project is now the memory/harness engine surface.** The V0–V7 + FRIDAY + Hardening + Operator-personal trajectory renders without competing with 14 Crickets v3.x bundle entries. Post-split: 56 items.
- **crickets Project develops its own release cadence + status flow.** Bundle catalog work no longer waits on agentm's Project conventions or shares Status flips. Post-split: 17 items (3 Done from v3.0 close + 14 v3.x Todos).
- **Cross-repo sub-issues survived transfer natively.** The `addSubIssue` mutation preserves the parent-child graph when both endpoints transfer together. No manual re-wiring was needed for the 15 sub-issue relationships under Crickets v3.0 + v3.x Versions.
- **The github-projects bundle (Crickets v3.x #13) now has a real two-Project setup to dogfood against.** The meta-loop becomes viable: the bundle that maintains the projects can be developed against the projects it'll maintain. Synthesis-from-the-vault gets concrete targets.
- **Issue numbers re-assigned cleanly in crickets** (1–17 in transfer order). Project board sort order matches issue creation order in the new repo.
- **ROADMAP-MASTER stays unified.** The master roadmap doc in the vault keeps its role as single source of truth across both Projects; only the *rendering layer* split.

**Negative**

- **Two Project boards to navigate.** Operator now bookmarks two sets of filter URLs + maintains two sets of views (same 7-view recipe applied twice). Mitigation: the recipe is well-documented + the surface ownership is sharp (no ambiguity about which Project owns what).
- **Cross-Project filters require URL fan-out.** "Show me everything I'm working on across both Projects" doesn't have a single Projects-v2 query — operator filters each Project separately. Mitigation: live sub-issue progress on parent Version Issues gives a single-page rollup when the dependencies are wired.
- **Issue numbers diverged from the original V4 backlog numbering.** Project items #4 / #5 / #17 / #18 / #19–#31 in agentm renumbered to crickets#1–#17. Historical references (e.g. "V4 #42" → crickets#4) require a mental mapping. Mitigation: closeout comments + ROADMAP-MASTER references use the original V4 numbering for posterity; the new crickets numbering is for going-forward project tracking only.
- **No Project-level dependency rendering between repos.** Cross-repo sub-issues + `Blocked by` carry the relationship in the issue graph, but neither Project board renders "this V5 Issue is blocked by 3 crickets bundles" as a board-level filter. Operator must drill into the parent Version Issue to see cross-repo sub-issues.

**Load-bearing assumptions** (re-check on every major-version bump)

- **GitHub Projects v2 continues to support cross-Project item-add + cross-repo sub-issues** as first-class. If either is deprecated or restricted (e.g. moves behind enterprise tier), re-audit whether a single Project + label-based separation is preferable to the two-Project split.
- **`gh issue transfer` continues to preserve history + comments + labels + sub-issue parent-child relationships.** The mid-2026 verification (this ADR's execution) confirmed the behavior. If GitHub changes the transfer semantics (e.g. drops sub-issue preservation), future transfers will need manual re-wiring scripts.
- **Operator-personal (bucket ⑨) doesn't outgrow agentm-side classification.** If operator-personal grows into a third distinct product line (e.g. a separate operator-facing PKM tool with its own release surface), re-audit whether it needs its own Project rather than continuing to ride in agentm.
- **The vault-side `ROADMAP-MASTER.md` continues to play the unified-narrative role.** If the master roadmap ever migrates out of the vault (e.g. into one of the repos' wikis), the cross-Project unification mechanism needs to follow it. Today the vault is the asymmetric anchor.

**Re-audit triggers** (specific events that should fire a fresh look at this ADR)

- GitHub ships native Project-to-Project dependency relationships → re-audit the issue-graph approach.
- Crickets ever re-couples to agentm at install time (reverses [ADR 0014](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0014-install-decoupling.md)) → re-audit unified-Project value.
- Cross-repo sub-issue rendering proves cumbersome in practice (e.g. >5 cross-repo dependencies in a single Version Issue feels noisy) → re-audit whether a different bridge mechanism (cross-Project linked views? canonical aggregation issue?) is warranted.
- The **github-projects bundle (Crickets v3.x #13)** ships and starts auto-synthesizing Project entries from the vault → re-audit whether the manual operator-side classification step (step (a)) becomes redundant or can be automated.
- Operator picks up a fourth project surface (e.g. a `sherwood`-side or external-repo Project) → re-audit whether the two-Project pattern generalizes to N-Projects or needs a meta-layer.

## Related

- [ADR 0006 — Crickets split](0006-crickets-split) — the repo carve-up this ADR's Project carve-up follows.
- [crickets ADR 0014 — install-decoupling](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0014-install-decoupling.md) — the install-coupling retirement that made the Project split structurally clean.
- [crickets ADR 0016 — Project surface split (sibling)](https://github.com/alexherrero/crickets/blob/main/wiki/explanation/decisions/0016-project-surface-split.md) — the cricket-side mirror of this ADR.
- [V4 #41 design — Project as canonical human source](../../../.harness/designs/v4-41-project-human-source.md) — the underlying Project-as-human-mirror design this ADR amends with the two-surface clarification.
- `ROADMAP-MASTER.md § Project surface split` — the locked operator-stated framing this ADR records.
