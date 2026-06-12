---
title: Seven-Section Wiki Convergence Design
status: launched
visibility: published
author: Alex Herrero
contributors: []
created: 2026-06-11
updated: 2026-06-11
last_major_revision: 2026-06-11
prd:
project:
---

<!--
  Authored 2026-06-11 via /design author from the cross-repo
  four-mode/seven-section investigation. Lightweight shape: the
  load-bearing sections (Context · Design · Alternatives · Dependencies
  · Migrations · Risks) are filled; the N/A-for-this-change sections
  (most Quality Attributes, all Operations) are omitted with their one
  load-bearing point folded into the section that owns it, per the
  operator convention (2026-06-09).
  status: draft → review → final → launched.
-->

# Seven-Section Wiki Convergence Design

## Context

### Objective

agentm's documentation spec ([ADR 0004](0004-diataxis-documentation-spec)) and its `diataxis-author` tooling still describe a four-mode Diátaxis layout (tutorials · how-to · reference · explanation), but agentm's own wiki and its `scripts/check-wiki.py` gate have already moved to the seven-section frame crickets standardized — How-to · Reference · Architecture · Designs · Explanation · Decisions · Operational. This design converges agentm onto that seven-section model as its one documentation taxonomy: it amends ADR 0004, updates the harness spec and the `diataxis-author` scripts, reshapes the `templates/wiki/` scaffold, and resolves the now-duplicated four-mode copy of the skill against crickets' canonical seven-section one. The goal is a single taxonomy across spec, tooling, gate, and content — no surface left asserting four modes.

### Background

crickets shipped the seven-section taxonomy (its `wiki-section-taxonomy` design + ADR 0020) and dogfooded it onto its own wiki. agentm's content followed: its `wiki/` already uses `how-to/`, `reference/`, `architecture/`, `designs/`, `decisions/`, `explanation/` plus an `architecture.yml`, and its `scripts/check-wiki.py` gate already enforces the seven-folder map. But agentm's **spec and tooling never caught up** — ADR 0004 still defines four mode-dirs, `harness/documentation.md` still calls four modes "the Diátaxis contract" (a stated Non-goal even blocks five-mode extensions), and the five `diataxis-author` scripts hard-code four modes. agentm's own wiki would fail a literal reading of its own ADR 0004.

The environment that shapes the fix: agentm and crickets are siblings ([ADR 0006](0006-crickets-split) split the harness from the customization toolkit). The `diataxis-author` skill was born in crickets, yet a four-mode copy also lives in `agentm/harness/skills/` — so the same skill now exists in two repos at two different mode-counts. ADR 0004 is append-only once `accepted` (its own rule: changes add a dated `## Amendment` block, never rewrite), so convergence must land as an amendment. And the seven-section frame introduces two **conditional** sections the four-mode model has no concept of: Architecture (gated on a per-repo `wiki/architecture.yml`) and Operational (gated on non-public visibility); the other five are always present.

The cost realities keep this bounded: this is a **spec + tooling reconciliation, not a content migration**. agentm's wiki content is already seven-section, so no pages move. The work is editing the lagging surfaces to agree with reality and retiring the duplicate skill copy toward crickets' canonical plugin. The one sharp edge is `migrate.py`, which today treats `architecture/` and `operational/` as **legacy** dirs to migrate *away from* — a direction the new frame would otherwise have to invert, and which disposition (b) instead removes by retiring agentm's copy (§3).

## Design

### Overview

The seven-section frame becomes agentm's single documentation taxonomy, identical to crickets'. (For a cold reader: the frame is a fixed, ordered set of seven top-level wiki folders — How-to, Reference, Architecture, Designs, Explanation, Decisions, Operational — replacing the older four-mode Diátaxis split; two of the seven appear only under a condition, the rest always.)

Concretely, the change touches four surfaces. Amend ADR 0004 with a dated amendment that adopts the seven-section taxonomy and supersedes its four-mode decision. Update `harness/documentation.md` to describe the seven sections plus the two conditional gates and to point at crickets for the authoring tooling. Retire agentm's duplicate four-mode `diataxis-author` copy (SKILL.md + its five scripts) toward crickets' canonical seven-section plugin, with graceful-skip when crickets is absent — which also consolidates agentm's wiki checker and dissolves the four-mode `migrate.py`. And reshape `templates/wiki/` from four mode-dirs to the seven-section scaffold.

### Infrastructure

*N/A: no new infrastructure.* This amends one ADR, edits one harness spec, retires the duplicate skill (SKILL.md + its five scripts), and reshapes one scaffold template — all on agentm's existing test + `check-wiki` battery. The one existing-infra note worth stating: agentm already ships a **seven-folder `scripts/check-wiki.py`**, so the CI gate side needs no change — the spec lags, and the skill-side checker (`harness/skills/diataxis-author/scripts/check.py`) retires with the duplicate copy rather than being re-aligned.

### Detailed Design

The work lands across four surfaces; each maps to a translate part.

#### 1. Amend ADR 0004 (the spec of record)

Per ADR 0004's own immutability rule — an `accepted` ADR gets a dated `## Amendment` block, never a rewrite — convergence is a new `## Amendment <date>` section that:

- Adopts the seven-section frame (How-to · Reference · Architecture · Designs · Explanation · Decisions · Operational) as the documentation taxonomy, superseding §1 ("Four top-level subdirs = four modes") and §2 (four templates).
- Records the two conditional sections (Architecture gated on `wiki/architecture.yml`; Operational gated on non-public visibility) and that the other five are always present.
- Notes Tutorials fold into How-to and the audience-tag remnants are retired.
- Cross-references crickets ADR 0020 and the crickets `wiki-section-taxonomy` design as the upstream source.

The original four-mode body stays intact below the amendment (immutability); the amendment is the operative spec.

#### 2. `harness/documentation.md` (the runtime spec)

Update the four-mode passages — the mode list, the mode-dir descriptions, and the "four modes is the Diátaxis contract" Non-goal — to the seven-section frame plus the conditional gates. The Non-goal that blocks "five-mode extensions" inverts: seven sections is now the contract. The load-bearing I/O properties carry over unchanged (preview-before-write, the per-repo `.diataxis-conventions.md` override, documenter dispatch).

#### 3. Retire the duplicate `diataxis-author` copy → crickets (disposition (b))

`agentm/harness/skills/diataxis-author/` is a four-mode copy — SKILL.md plus five scripts (`check.py`, `classify.py`, `author.py`, `repair.py`, `migrate.py`) — of a skill crickets now owns canonically at seven-section (`src/wiki-maintenance/`). **Resolved: disposition (b)** — retire agentm's copy and defer to crickets' canonical plugin, the single-source-of-truth move ADR 0006 already made for `dependabot-fixer` and `ship-release`. Reshaping the copy's four-mode constants in place (disposition (a)) was the considered alternative; (b) was chosen so there is one seven-section authority, not two.

The retire:

- **Remove** `agentm/harness/skills/diataxis-author/` (SKILL.md + the five scripts).
- **Rewire** the live references the liveness scan found — `install.sh`, the README, the sibling skills that call it (`wiki-author`, `doctor`, `migrate`), and `detect_project.py` — to defer to crickets' `wiki-maintenance` plugin with the **graceful-skip** pattern from ADR 0006: suggest-then-skip when crickets is not installed, never hard-fail.
- **Document** the new dependency in `harness/documentation.md` (§2) + agentm's release notes.

Two consequences fall out cleanly, both simplifications relative to disposition (a):

- **The two-checker problem dissolves.** agentm's wiki validation consolidates on `scripts/check-wiki.py` (already seven-folder); the skill's own `check.py` retires with the copy, so the duplicate checker is gone rather than merely re-aligned.
- **The `migrate.py` inversion stops being agentm's problem.** The sharp edge — agentm's copy treating `architecture/`/`operational/` as legacy-to-remove — disappears with the copy. crickets' canonical `migrate.py` (already seven-section from its taxonomy launch) is the migrator agentm defers to; agentm lands no inversion.

#### 4. `templates/wiki/` (the scaffold)

Reshape from `tutorials/how-to/reference/explanation` to the seven-section frame, mirroring crickets' `wiki_init` scaffold: the five always-present sections (how-to, reference, designs, explanation, decisions) plus the two conditional (architecture when a manifest is declared, operational when non-public). Update the `.diataxis` marker content and the `Home`/`_Sidebar` starter templates. One sub-decision for the review pass, sharpened by disposition (b)'s single-source logic: whether agentm's `/setup` should defer wiki scaffolding to crickets' `wiki_init.py` outright (consistent with retiring the skill) or keep a lighter agentm-flavoured starter.

## Alternatives Considered

- **Diverge — keep agentm four-mode, crickets seven-section.** Rejected by the operator (this design *is* the converge path). It would leave agentm's spec/tooling permanently contradicting agentm's own wiki + gate, and keep two mode-counts of the same skill.
- **Update agentm's `diataxis-author` copy in place** (disposition (a) — keep it as a self-contained harness skill, reshaped four→seven). Rejected in favour of (b): a second seven-section copy preserves two-repo drift risk and a redundant second checker, where retiring toward crickets makes it single-source. (b)'s cost — a crickets-installed dependency for harness wiki-authoring — is bounded by graceful-skip (Risks §1).
- **Partial fix — revert agentm's `scripts/check-wiki.py` back to four-mode** to match the tooling. Rejected — agentm's wiki content is already seven-section, so reverting the gate would fail the real wiki; that "fixes" consistency by breaking the content.
- **Rewrite ADR 0004 in place** instead of amending. Rejected — violates agentm's own ADR-immutability rule (accepted ADRs are append-only via dated amendments). The amendment *is* the mechanism.
- **Do nothing.** Rejected — the inconsistency is load-bearing: the four-mode `migrate.py` actively treats `architecture/`/`operational/` as legacy-to-remove, so a future `/diataxis migrate` on an agentm-flavoured wiki would try to delete the new sections.

## Dependencies

- **crickets' canonical seven-section taxonomy** (ADR 0020 + the `wiki-section-taxonomy` design + `crickets/src/wiki-maintenance/`) — the upstream this design adopts. agentm tracks it; convergence should adopt the frame, not re-derive it.
- **agentm's existing `scripts/check-wiki.py`** (already seven-folder) — the gate the rest is brought into line with.
- No external/library dependencies; no team coordination beyond the operator.

## Migrations

- **Spec amendment, not content migration.** agentm's wiki is already seven-section; no pages move. The change is to spec + tooling only.
- **No `migrate.py` inversion in agentm.** Under disposition (b), agentm retires its `diataxis-author/scripts/migrate.py` rather than inverting it; crickets' canonical `migrate.py` (already seven-section) is the migrator agentm defers to. The direction-flip the four-mode copy carried — treating `architecture/`/`operational/` as legacy-to-remove — is removed with the copy. A project that previously ran the old four-mode `migrate.py` is unaffected: its wiki still lints under the shared seven-folder gate (the five core sections are common), and a future migrate via crickets' tooling adds the conditional sections only when their gates apply.
- **`templates/wiki/` scaffold change** affects only **new** projects agentm scaffolds via `/setup`. Already-installed projects keep their wiki; `install.sh --update` does not touch user-owned `wiki/` content (the ADR 0002/0004 boundary).
- **`.diataxis` marker** semantics are unchanged (still the lint-gate marker); only its scaffolded content updates to the seven-section starter.

## Technical Debt & Risks

1. **Harness wiki-authoring gains a crickets dependency (the active cost of disposition (b)).** After retiring agentm's copy, authoring or maintaining Diátaxis docs in the harness requires crickets' `wiki-maintenance` plugin installed. *Mitigation: the graceful-skip pattern from ADR 0006 — sibling skills suggest-then-skip when crickets is absent, never hard-fail; the new dependency is documented in release notes + `harness/documentation.md`. Re-audit if agentm must author wikis where crickets cannot be installed.*
2. **The retire must rewire every live reference.** Removing the copy without updating all of `install.sh` / README / sibling skills (`wiki-author`, `doctor`, `migrate`) / `detect_project.py` would leave dangling references that break at runtime. *Mitigation: the liveness scan already enumerated the call sites; the retire part's verification is a grep proving no reference to `harness/skills/diataxis-author/` survives, plus a fixture exercising the graceful-skip path with crickets absent.*

**Resolved by disposition (b)** (recorded for the auditor): three risks that disposition (a) would have carried collapse with the copy — the **two-copy drift** risk (no second copy left to re-diverge), the **two-checker** debt (the skill's `check.py` retires; agentm consolidates on `scripts/check-wiki.py`), and the **`migrate.py` inversion** sharp edge (agentm owns no four-mode migrator after the retire; crickets' canonical seven-section one is authoritative).

## Quality Attributes

<!-- Only the real attributes are kept; Security / Data Integrity / Privacy /
     Scalability / Latency / Abuse / Accessibility / i18n / Compliance are N/A
     for a documentation-taxonomy spec change (no runtime, no data paths, no new
     surface) and are omitted per the operator convention. -->

### Reliability

Under disposition (b) the failure-sensitive piece is the **retire's reference-rewiring**: removing the copy while leaving a dangling reference in `install.sh` / the README / a sibling skill / `detect_project.py` would break wiki-authoring at runtime. Reliability rests on the liveness scan having enumerated the call sites, a graceful-skip fixture that exercises the crickets-absent path, and the standing preview-before-write gate in crickets' canonical tooling. (The four-mode `migrate.py` direction-flip risk that disposition (a) carried is removed with the copy, not mitigated.)

### Testability

Deterministic by construction. The retire is verified by three checks landing with it: a grep proving no reference to `harness/skills/diataxis-author/` survives the rewire; a fixture exercising the graceful-skip path with crickets absent (suggest-then-skip, no hard-fail); and agentm's own wiki passing the surviving seven-folder `scripts/check-wiki.py` with zero new issues. The ADR amendment, `documentation.md`, and scaffold changes are pure text/scaffold edits checked by the existing `scripts/test_*.py` + `check-wiki` battery.

## Project management

### Documentation Plan

- **ADR 0004** — amended (the spec of record; dated `## Amendment` block).
- **`harness/documentation.md`** — updated to the seven-section frame + conditional gates, and pointed at crickets' `wiki-maintenance` plugin for the authoring tooling.
- **`agentm/harness/skills/diataxis-author/`** — retired (SKILL.md + the five scripts removed); a deprecation / cross-reference note in the README + harness docs points harness users to crickets' canonical plugin.
- agentm's own wiki needs no content change (already seven-section); *this* design doc is registered at finalization in the root + `designs/` `_Sidebar.md` and the `Designs.md` index (under a new *Documentation & tooling* group). `Home.md` is left curated — `check-wiki` rule (j) exempts it from completeness.

### Launch Plans

TBD — not yet sequenced. Lands via agentm's `/work` after this design finalizes and translates. The release notes cross-link crickets' `wiki-section-taxonomy` launch (the upstream this converges to).

## Operations

*N/A — a spec + tooling change with no runtime service, no SLAs, no monitoring or rollback surface beyond standard git revert.*

## Document History

| Date | Change | Status |
|---|---|---|
| 2026-06-11 | Initial draft created via `/design author` — agentm seven-section convergence (amend ADR 0004 · update `harness/documentation.md` · reshape the five `diataxis-author` scripts · reshape `templates/wiki/` · resolve the duplicate four-mode skill copy). Authored from the cross-repo investigation; lightweight shape (load-bearing sections filled, N/A sections omitted per operator convention). | draft |
| 2026-06-11 | Author signaled ready for review. Duplicate-skill fork resolved to **disposition (b)** — retire agentm's `diataxis-author` copy toward crickets' canonical plugin (graceful-skip when absent). The decision was folded in before the review pass: five surfaces → four, and the `migrate.py` inversion + two-checker debt + two-copy drift risks all dissolve with the copy. | review |
| 2026-06-11 | Approved as final via `/design author` (direct review → final — summary reviewed, the one open fork already resolved to (b)). Unblocks `/design translate`. | final |
| 2026-06-11 | Translated to 4 parts via `/design translate`: `amend-adr-0004` · `retire-diataxis-author` · `harness-documentation` · `templates-wiki`. Parts written to the vault (`projects/agentm/_harness/designs/seven-section-convergence/parts/`) per the vault-redirect convention, not the skill's default `wiki/designs/parts/` (which would pollute the published wiki + trip `check-wiki`); `parent_design` points back to the repo path. | final |
| 2026-06-11 | Sequenced into 4 plans via `/design sequence` (topo order `amend-adr-0004` → `retire-diataxis-author` → `harness-documentation` → `templates-wiki`); first plan active at the **vault** `_harness/PLAN.md` (`amend-adr-0004`), 3 queued at `_harness/designs/seven-section-convergence/queued-plans/` — vault-redirected, not repo `.harness/`. Next: `/work` task 1. | final |
| 2026-06-11 | **Launched** — all 4 parts shipped: `amend-adr-0004` (`51f77a9`+`deb6ef4`), `retire-diataxis-author` (`07f7ddc`), `harness-documentation` (`9e5322c`), `templates-wiki` (final part). Every surface that asserted four modes — ADR 0004, `harness/documentation.md`, the retired `diataxis-author` copy, the `templates/wiki/` scaffold — now agrees with the seven-section frame the gate (`check-wiki.py`) already enforced. Formal version-tagged release deferred per the Launch Plans (sequencing TBD); the release notes will cross-link crickets' `wiki-section-taxonomy` launch (the upstream this converges to). Note: converging the **documenter** sub-agent's own prose (still four-mode in both repos) + retiring agentm's duplicate copy toward crickets' canonical `wiki-maintenance:documenter` is a **separate** effort, not one of this design's four parts. | launched |
