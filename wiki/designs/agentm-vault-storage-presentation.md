---
title: Vault Storage & Presentation Design
status: proposed
kind: design
scope: feature
area: agentm/storage
parent: memory-storage-seam.md
---

> [!NOTE]
> **Proposed** (lifted to tracked `wiki/designs/` 2026-06-28). A full design under the [Memory↔Storage Seam](memory-storage-seam) in the `agentm/storage` area; it governs no code yet, so it stays out of the governing-design resolver until a transport ships. The two how-to deliverables are [Back the vault with Google Drive](Back-The-Vault-With-Drive) and [Set up Obsidian on the vault](Use-Obsidian-With-The-Vault); the git transport is the crickets [vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git) plugin.

# Vault Storage & Presentation Design

## Context

### Objective

This design defines **where the vault can be stored, and how it is replicated and made available across your devices.** The storage seam leaves both open on purpose; this design fills them in with a small, deliberate menu of backing styles that provide version history and rollback, off-device backup, smooth cross-device and mobile sync, and a path for chat assistants to read the vault and propose changes.

### Background

The [Memory↔Storage Seam](memory-storage-seam) owns *how the engine reads and writes* the vault, behind a small set of verbs, and is deliberately blind to how the markdown reaches each device. This design owns that outer layer — the transport that moves files between machines, and the way you read and edit them — composing above the seam and reusing its verbs.

**Outcomes it provides:**
* granular, durable per-commit history and rollback
* off-device backup the operator controls
* an additional access path for chat assistants like Claude.ai or Gemini

**Constraints:**
* the vault must remain reachable from a desktop agent, a phone, and a chat surface
* one sync mechanism is enough — running several adds cost without adding value

Note: the complexity of dueling sync technologies (Drive vs git) pushes toward picking a single transport rather than running two or more at once — though you may still run more than one if you accept the added complexity.

## Design

### Overview

Everything decomposes into **two orthogonal axes:**

| Layer | Question | Components |
|---|---|---|
| **Transport** | How do files propagate across devices? | Git/GitHub · Google Drive |
| **Presentation** | How do you read and edit the vault? | Obsidian · raw filesystem + CLI · chat connector |

The recommendation: **pick one transport** — running both is possible but strongly discouraged (the reasoning is in [Alternatives](#alternatives-considered)). Presentation rides on top of whichever transport you choose. The three components — two transports (normally one at a time) plus one optional presentation layer — are each specified in a child design. Only the git transport is a crickets plugin; Drive and Obsidian are how-tos.

### Infrastructure

N/A: no new agentm infrastructure. The git transport is a crickets plugin operating *under* the seam's existing `vault` backend (`SOURCE` tier); the Drive transport and Obsidian presentation are operator-side how-tos over it. The design reuses seam primitives only — `Capabilities.sync` / `conflict_files`, the `LOCAL_INDEX` never-sync tier, and `vault_lock`. The only runtime additions are operator-side (a git remote, or Drive sync), owned by the children.

### Detailed Design

#### The three components

Each is fully specified in its child; here is what it is, how it fits, and where it touches the sibling repo.

- **Git transport — the recommended primary.** A private GitHub repo as the vault's sole transport; every device is a clone. Serves durability and reach directly: per-commit history, off-device backup, and a chat read-path that becomes propose-via-PR. Cost: mobile friction, git's weak surface. Realized as the crickets `vault-git` plugin. → [vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git).

- **Drive transport — the simple mode.** Google Drive syncing the vault folder: invisible background sync and smooth mobile access, with no commands to run. Realized as a **how-to** (git is the tooled path; Drive is invisible, so a plugin would have nothing to drive — see [Back the vault with Google Drive](Back-The-Vault-With-Drive)); the one health check folds into the existing `doctor`. → [Back the vault with Google Drive](Back-The-Vault-With-Drive).

- **Obsidian presentation — the optional layer.** Obsidian-flavored structure (templates, MOCs, recommended plugins, a health check) on either transport. The thinnest component, because the storage and writing-convention halves already live in the seam and the memory engine. Realized as a **how-to + templates pack**, not a plugin. The "no Obsidian at all" profile (plain repo + CLI + chat) stays first-class. → [Set up Obsidian on the vault](Use-Obsidian-With-The-Vault).

#### Choosing a transport, and why only one

| You need… | Pick |
|---|---|
| Version history + rollback, off-device backup, chat propose-via-PR, team merge | **Git** |
| Invisible mobile sync, simplest setup, and you do not need versioning | **Drive** |

Normally you pick exactly one; running both at once is possible but strongly discouraged — see [Alternatives](#alternatives-considered). Switching later is cheap (next subsection).

#### How it composes — dependency arrows (one-way)

The children point **up** at this design; this design points up at the [Memory↔Storage Seam](memory-storage-seam). Both transports and presentation point **down** into the seam: transport delivers `SOURCE`-tier markdown *under* the `vault` backend; presentation renders *over* it. None is a new seam backend. Reused primitives: `Capabilities.sync` / `conflict_files` (under git, `conflict_files` means merge markers, not Drive copies); the `LOCAL_INDEX` never-sync tier (the vector index and `.obsidian/workspace*` are never committed and never Drive-synced — a replicated binary index is a corruption pattern in either clothing); and `vault_lock` for one machine's concurrent agent writes (cross-person arbitration in the team case is git merge, a different layer).

## Alternatives Considered

- **Run git and Drive together (the hub / hybrid) — strongly discouraged.** Git and Drive are each a complete sync engine with its own conflict model, and they are blind to each other. Any topology where one file can be mutated through both paths between reconciliations produces *double-conflicts*: a Drive `(conflicted copy).md` sibling **and** git `<<<<<<<` markers in the same file — inherent in running two sync engines over one tree. The hub model (mobile→Drive, desktop→git, a bridge reconciles) does not remove the hazard; it **centralizes** it into a bridge daemon the operator must keep running, a single point of failure that still hits an unresolvable conflict whenever a Drive-side and a git-side edit touch the same file between cycles. The only coherent "additive Drive" is a one-way, **read-only** Drive mirror for mobile *reading* (git stays the sole write path); it costs mobile write convenience and is cut from v1.

- **Keep Drive-only, do nothing — rejected as the default, kept as a mode.** Drive-only forgoes the three drivers (history, backup, chat-write). It stays a *supported* simple mode for operators who value invisible mobile sync over versioning, but it is not the recommended default.

- **Model transport as a new seam backend (`git-vault`) — rejected.** Git here is transport under the existing `vault` backend, not a new set of seam verbs. A backend would buy nothing the plugin can't, and cost a conformance pass + selection wiring. The seam stays untouched.

## Dependencies

- **Parent:** [Memory↔Storage Seam](memory-storage-seam) — the `SOURCE` / `LOCAL_INDEX` tiers, `Capabilities.sync` / `conflict_files`, `vault_lock`. This design composes under it, never replaces it.
- **Children:** [vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git) · [Back the vault with Google Drive](Back-The-Vault-With-Drive) · [Set up Obsidian on the vault](Use-Obsidian-With-The-Vault) — the three children that implement the components (one crickets plugin, `vault-git`, plus the Drive and Obsidian how-tos).
- **Up the chain:** inherits [Foundations](agentm-foundations-hld) (durability, control) by link.
- **Forward-ref (not a dependency of v1):** team-vault / multi-root recall — a separate agentm-substrate engine change (the team-vault / multi-root-recall item sub-idea 2).

## Migrations

Switching transports is a **behavioral migration, not a data migration** — the vault is the same markdown under either. Drive→git: stop Drive sync on the folder, `git init` + private remote + push, set up clones. Git→Drive: stop committing, point Drive at the folder. **Rollback compatibility is total** because neither transport rewrites content; the markdown is identical, so backing out is just re-pointing sync. The one rule a hybrid migrator must honor — never let Drive sync `.git/` — is enforced by the `vault-git` plugin's hybrid mode and `doctor`.

## Technical Debt & Risks

- **Mobile is git's weak surface.** isomorphic-git struggles with large/binary-heavy vaults; iOS needs Working Copy + Obsidian. A real, accepted cost of the recommended primary, reduced but still present.
- **Media bloats git history and doesn't merge.** Attachment-heavy vaults need `git-lfs` or a thin-media discipline — flagged, owned by [vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git).
- **All three children are unbuilt.** The git setup recipe is verified operator knowledge; everything else is proposal.
- **Open calls — all resolved 2026-06-27:** (1) one-way read-only Drive mirror → **cut from v1** (revisit only if real mobile-write demand appears). (2) `vault-obsidian` plugin vs how-to → **how-to + templates** (no maintained dashboards). (3) `obsidian-vault`/`vault-obsidian` naming collision → **dissolved**: with Obsidian shipping as a how-to, no *capability* named `vault-obsidian` exists to collide with the `obsidian-vault` backend, and the design moves to `area: agentm/storage` (it governs no crickets code). (4) children `area:` → only `vault-git` is a crickets plugin (folds under `crickets/obsidian-vault` or its own area, decided at lift); the two how-tos govern no crickets code. (Drive mode settled earlier — documented-and-frozen how-to.)

## Quality Attributes

### Security

The git repo, if chosen, **must stay private.** The vault holds PII by assumption — project names, preferences, fixes that name real systems — so a public repo would expose private information, and any secret that slips in. This is the one unrecoverable mistake here: a public vault repo is **forbidden, not merely discouraged**, because once history is pushed publicly it cannot be unpublished. Two safeguards back the rule: `vault-git init` refuses to commit on detected secrets/`.env` before the first push (the vault is private, but PII ≠ credentials), and the vault repo is a *separate* private repo, never agentm's, so vault PII never routes through agentm's CI (`check-no-pii`, `gitleaks`).

### Reliability

The dominant failure mode is a sync conflict. Under git it is an ordinary merge conflict surfaced as a clear "resolve these N files" report by `vault-git sync`, never silent markers. Under Drive it is a `(conflicted copy)` the seam already tolerates. The single-writer-mostly assumption keeps both rare.

**Multi-writer scenarios** — most importantly a shared **team vault**, where several people write the same vault — are tolerated *only* under git. Git is a real multi-writer merge system: concurrent edits to different files merge cleanly, and same-file collisions surface as ordinary, reviewable conflicts. Drive has no merge — concurrent writers just produce `(conflicted copy)` siblings with no resolution path — so **git is the only viable transport for a team vault.** (The team case also needs multi-root recall, a separate engine change — see the team note in [vault-git](https://github.com/alexherrero/crickets/wiki/crickets-vault-git).)

### Data Integrity

Corruption resistance is the core attribute, and it reduces to one rule reused from the seam: only `SOURCE`-tier markdown is committed and synced; the derived, machine-local tier (`*.sqlite*`, `.obsidian/workspace*`) stays on the machine. A replicated binary index is the classic corruption pattern, and keeping it local sidesteps it. Recovery under git is a per-commit diff or rollback; under Drive it is the provider's file-restore window.

### Privacy

Vault contents are PII by assumption (project names, preferences, fixes naming real systems), so a private repo is the correct home — private content kept private. In the team case, `personal-private/` lives only in each member's own vault, so the folder layout draws the privacy boundary on its own.

## Project management

### Documentation Plan

- `wiki/how-to/Back-The-Vault-With-Git.md` — the git operator recipe (the tooled, primary path).
- `wiki/how-to/Back-The-Vault-With-Drive.md` — the Drive simple-mode recipe (setup + trade-offs + the keep-`.git`-out-of-the-synced-tree rule for the hybrid), plus a small `vault_drive_cleanup.py` helper (conflict-copy + stray-index cleanup). Both are `vault-drive`'s deliverables.
- `wiki/how-to/Use-Obsidian-With-The-Vault.md` — the Obsidian presentation recipe (recommended plugins, settings, a templates pack). This is `vault-obsidian`'s deliverable.
- `vault-git` is the one sub-design (lifts to crickets `wiki/designs/`); `vault-drive` and `vault-obsidian` are how-tos (lift to `wiki/how-to/`).
- Chat payload update — the GitHub read-path + propose-via-PR posture in `templates/agentmemory-context.md` and its reference page.

### Work estimates

`vault-git` — **M** (skill + hook + templates + doctor). `vault-drive` — **S** (a how-to + a `vault_drive_cleanup.py` helper + one health check in the existing `doctor`). `vault-obsidian` — **S** (how-to + templates). Team-vault/multi-root — **L**, separate item, out of scope here.

### Launch Plans

Pilot **git-only on a single machine** before any cross-machine or autosync automation. Sequence the children: `vault-git` first (the primary, the only plugin), then the Drive how-to, then the Obsidian how-to.

## Operations

### Rollback Strategy

Backing out any transport choice is re-pointing sync, not a data operation (see [Migrations](#migrations)) — content is never rewritten, so rollback is always clean. No service SLAs / monitoring / logging apply: this is operator-side transport, not a running service.

## Amendment log

**2026-06-28 — Context reframed positive + forward-looking.** Reworded the Objective / Background / Outcomes and the Drive component to say what the design *provides* rather than what it improves on the current setup, and dropped the "what we have today" framing (the Drive-folder current-state narrative). Carried the positive-framing pass (Tonal lesson 2) into Technical Debt ("reduced but still present"), Data Integrity, and Privacy, and swapped a lingering "load-bearing" → "critical" in an amendment entry per the word ban.

**2026-06-27 — Obsidian settled as a how-to; all open calls resolved; Security/Reliability sharpened.** `vault-obsidian` ships as a **how-to + templates pack**, not a plugin (no maintained dashboards) — so `vault-git` is the *only* crickets plugin; Drive and Obsidian are how-tos. Resolved the remaining open calls: the one-way read-only Drive mirror is **cut from v1**, and the `obsidian-vault`/`vault-obsidian` naming collision **dissolves** (no `vault-obsidian` capability exists once it's a how-to; its design moves to `area: agentm/storage`). Strengthened Security — **a private repo is mandatory**, a public vault repo is the one unrecoverable mistake. Added the multi-writer / team-vault case to Reliability — **git is the only viable transport for a team vault** (Drive has no merge). Reframed Objective/Background/Overview toward plain English and did a grammar + visual pass on the operator's edits. **Re-audit trigger:** the team-vault path needs multi-root recall, a separate engine change.

**2026-06-27 — Drive mode settled: documented-and-frozen, a how-to not a plugin.** Resolved open call (1). *Decision:* git is the **tooled** path (a maintained `vault-git` plugin); Drive is the **documented** path (a how-to), supported but frozen — not deprecated. *Why not a maintained Drive plugin:* git is explicit (the agent drives commits/pulls/pushes → real verbs worth building), Drive is invisible (the OS/Drive client syncs in the background, the agent never drives it → no verbs to build), and the one critical Drive safety step (selective-sync exclusion of `.git`/the derived tier) lives in the Drive client UI that no plugin can automate — so a plugin would only be a how-to with a doctor check. That single check folds into the existing agentm `doctor`. *Re-audit trigger:* revisit *deprecating* Drive if git-on-mobile improves enough to erase its seamless-mobile advantage. See [Back the vault with Google Drive](Back-The-Vault-With-Drive).

**2026-06-27 — authored (draft); reparented under the seam.** Specified vault backing as two orthogonal layers (transport × presentation) with the core call: support **git-only** and **Drive-only** as mutually-exclusive transports, **reject the hub/hybrid**, and keep Obsidian an optional presentation layer. *Structural decision (operator, 2026-06-27):* this is a **full design under the [Memory↔Storage Seam](memory-storage-seam)** (a child in `agentm/storage`), **not a peer HLD** — *why not the peer-HLD:* the seam already owns `agentm/storage`, and transport+presentation is genuinely a sub-area of storage rather than its own arc, so a child design avoids a second owner in one area and a needless `area-taxonomy` amendment. *Why not the hub:* two independent sync engines over one tree produce double-conflicts inherently; a bridge daemon centralizes rather than removes the hazard. Conformed to the full 10-section template with AG living-design adaptations (N/A sections omitted: Abuse, Accessibility, Scalability, Latency, i18n, Compliance, SLAs, Monitoring, Logging). **Re-audit triggers:** revisit the one-way read-only Drive mirror if git-on-mobile stays weak *and* real mobile-write demand appears; resolve the five open calls before lift; split out team-vault as its own substrate design when picked up.
