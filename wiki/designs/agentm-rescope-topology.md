---
title: AgentM Rescope — Storage Topology & Crickets Seam
status: proposed
kind: design
scope: architecture
area: agentm
parent: agentm-rescope-principles.md
seeded: 2026-08-02
---

# AgentM Rescope — Storage Topology & Crickets Seam

## Where the truth lives

The vault moves off its current Google-Drive-synced mount onto local disk, inside a private git repository. This is the direct consequence of principle 2 (`agentm-rescope-principles.md`) — files are truth, git is undo — and it was the first of three decisions only the operator could make, because it relocates the canonical home of everything FRIDAY will ever be told, for years forward.

**The condition attached to that decision:** Android Obsidian has to keep working. The resolution is that the phone never runs git. Syncthing keeps the phone's working tree in step with the laptop's, with `.git` itself excluded from the sync set — the phone sees and edits plain files, exactly as it does today, and Obsidian on Android notices nothing about the transport underneath it. The daemon (below) is the only thing that ever runs `git commit`. When a file changes because the phone edited it, the daemon picks that up like any other file-system change and commits it with an attribution noting the edit came from the phone.

This needs one validation spike before cutover: confirm Syncthing plus Obsidian actually behaves cleanly on the operator's Android device — conflict handling, sync latency, background-sync reliability. Half a day, not a week. Nothing in the week-1 experiment or the daemon's first build depends on this spike landing first; it can run in parallel.

Backup and multi-machine access fall out of this for free once it's git, and the concrete shape is decided (2026-08-08): the Mac holds the working repository, a bare repository on the primary Unraid NAS (git installed via plugin) receives a daily push, and the backup NAS's existing monthly cold mirror carries a third, air-gapped copy — contingent on the git share sitting inside its mirror scope, a standing TO CONFIRM in the homelab notes. Tailscale covers a push from outside the house when wanted, inside the homelab's minimal-attack-surface lock. All three copies live in the house by choice; the amendment log records the trade-off.

An external cloud remote is a different question, and it is settled by the vault's most sensitive content rather than by convenience. The moment the operator's own spaces joined this repository, `Personal/` put scanned documents and financial records inside it; a standing email ingest would add distilled facts about other people on top of that. There is no path-level escape, either — memory shards by capture date, so a note about a person and a note about SQLite land in the same directory and no `.gitignore` can tell them apart. The policy is therefore whole-vault: a bare remote on the home network, and no external cloud remote unless the repository is encrypted at rest before it leaves the house. The encrypted-off-site option was weighed against this policy on 2026-08-08 and declined — the in-house copies are redundancy enough for the operator's tastes, so the policy stands unexercised rather than repealed.

## What FRIDAY is allowed to overhear

The second operator decision: ambient mining of everything, plus everything explicitly handed to it through capture and ingest — the phone's Capture project, the ingest pipeline, anything dropped in on purpose. Nothing is off by default.

This changes what the capture pipeline has to be. The current system's 4,933-item inbox landfill was not caused by ambient listening being on — it was caused by the thing doing the listening being a regex pattern-matcher. Of that pile, 3,028 entries are LOW-confidence fragments a pattern-matcher staged and then nothing ever triaged. Keeping ambient mining on while fixing quality means the distiller has to become a judgment call, not a pattern match: sessions get summarized into well-formed candidate memories by an actual model call, not scraped by string matching.

Because the producer stays high-volume by the operator's own choice, the loud-queue mechanism below is not an optional nicety here — it's the thing standing between "ambient capture" and "landfill, again, under a new name."

## Capture is instant; filing is not

Two operations, cleanly separated, because collapsing them is what caused both of the previous system's failures — the version that filed synchronously blocked capture on network availability, and the version that filed nothing left 82% of what it captured invisible to recall.

**Capture** is one daemon transaction: write the file, upsert it into the FTS5 index, done. No model call, no network dependency, works with the laptop offline. This is what makes capture ambient-safe at any volume — the mechanism that makes something exist and findable never waits on judgment.

**Filing** — which space something belongs in, what it links to, whether it's a duplicate — is async, and is where the `claude -p` distillation from the previous section runs. It can take minutes or hours without anything being lost, because principle 3's round-trip (save it, ask for it, get it back) is already satisfied the moment capture finishes. Filing lag is a staleness cost, not a loss.

What prevents the filing queue from becoming the old inbox: it is loud, in three parts.

1. **Two numbers on the status surface at all times:** how many items are waiting to be filed, and the age of the oldest one. Both are a query rather than a folder — there is no inbox, so "how many are waiting" is a `SELECT` over frontmatter status.
2. **Red thresholds that page.** Past a queue-age or queue-size threshold, the daemon emails through the operator's own relay (`plugins.autonomy.email_to` + `email_smtp_url`), at most once a day for the same set of conditions and again when a different one goes red. The age threshold is three days and does the work; the size backstop is a thousand and exists only for the case age cannot see, a producer that wrote thousands of fresh items at once. A queue does not silently reach five thousand items; it reaches a threshold and says so.
3. **A daily self-probe.** The daemon captures a synthetic test item over its own MCP surface, asks for it back in words the note's prose does not contain, confirms the round trip inside its expected time, and alerts if it fails. The probe note is marked synthetic in frontmatter — `probe: self-probe`, read by anything that must not count it in a measurement — rather than identified by where it sits, because capture shards by date and any path rule would quietly stop matching. This is principle 3 running as a live process against itself, every day, not just as a one-time experiment.

## The four spaces

FRIDAY's memory and FRIDAY's projects are ID-stable: a `space` field and a `status` field in frontmatter, no directory that has to match. Reorganizing FRIDAY's own filing is a metadata edit, never a file move, so nothing that links to a FRIDAY-owned note can ever go stale by virtue of FRIDAY tidying up after itself.

The operator's projects and personal files are real directories, named and laid out the way he wants, because he browses them — in Obsidian, in a file browser — and a generated or virtual view of a person's own files is a database's abstraction wearing a filesystem's clothes. What he sees has to be what's actually there.

**All four spaces live in one git repository, not four.** The reason is the promotion door below: moving a file across the boundary, rewriting the links that pointed at its old location, and recording why, all have to land as one atomic, revertible commit. That's a same-repo operation.

The layout, decided jointly and grounded in the filing the operator already keeps:

```
Obsidian/                        ← vault root = git repo root
├── index.md                     — OKF entry point
├── Filing.md                    — the declared address space
├── Ideas.md
│
├── Agent/                       — spaces 1+2 · readable anytime, writable never
│   ├── memory/                  — space 1 · ID-stable · OKF frontmatter
│   └── desk/                    — space 2 · everything in flight
│       ├── projects/<slug>/     — plans, roadmaps, progress, drafts
│       └── briefs/              — daily digests
│
├── Projects/                    — space 3 · promoted artifacts only
├── Calendar/                    — daily notes: brief window and capture inbox
└── Personal/                    — Church · Home · Other · Tech
```

The repository root is the whole Obsidian vault rather than a subdirectory of it. `Agent/` sits as a peer to the operator's existing top-level folders, and wikilinks resolve across all of them, so a link-integrity check after a promotion has to be able to see every space. Scoping the repo to `Agent/` alone would let the door pass its check while silently breaking a link from the operator's side.

**Nothing exists in `Projects/`, `Calendar/`, or `Personal/` that FRIDAY put there without asking.** Either the operator made it, or it came through the door. That makes the promotion door the single write path into the operator's half of the vault — one mechanism to build, one to audit, one to revert — and turns principle 5 from a policy FRIDAY has to remember into a structural fact about the layout.

A project's working life happens entirely in `Agent/desk/projects/<slug>/` — every plan, every revision, every draft. `Projects/<slug>/` stays empty until something is worth keeping, and fills only at junctures: a design goes final, a deliverable ships. The cost accepted on purpose is that one project lives in two places. What it buys is a `Projects/` tree that records what mattered instead of mirroring everything in flight.

`Personal/` is the operator's existing filing, moved one level down and otherwise untouched: life-area domains, Title Case names, a `resources/` folder beside each category, `_Archive/` local to the area that needs one. It is close enough to PARA to be described as one — Projects, Areas, Archives, arrived at independently over years — with Resources folded into Areas rather than kept as a fifth top-level folder.

Three things this layout adds to what the operator already had:

1. **`Calendar/`** — the time axis his filing never covered. FRIDAY's daily brief is transcluded into the day's note by date, so it renders live without any file crossing into the operator's space, and the same note below it is where quick capture lands. Raw briefs live and die in `Agent/desk/briefs/`; one promotes only if it turns out to be worth keeping.
2. **`Filing.md`** — a declared, finite list of legal destinations that the door validates against and refuses to file outside of. Adding an area is an edit to this file, not a `mkdir`. It is operator-edit-only: FRIDAY proposes changes in conversation and never applies them, because an agent that can widen its own address space does not have one.
3. **OKF frontmatter** for `Agent/memory/`, plus `index.md` as the entry point at the root and in each desk project. The convention is convergent with what the vault already does, and it makes the memory portable across agents.

Archival differs by side, deliberately. FRIDAY's spaces have no `_Archive/` at all — a superseded memory gets a status change and sinks in ranking, staying exactly where it is, because spaces 1–2 are ID-stable and files there never move. Dreaming ages memory out; hard deletion stays the rare reviewed operation. Desk *scratch* is a different thing from memory: a consumed dream-staging batch is exhaust, and the daemon may plainly delete it, since git makes that recoverable. The operator's spaces keep `_Archive/`, local to whichever area needs one, and archiving there is a browsing operation rather than a preservation one — git is the real archive, so a file moves to declutter a view, never to keep it safe.

Two operational consequences follow. `Agent/` is excluded from phone sync, because desk churn and memory writes buy nothing on a phone and capture still works through the daily note, which syncs and gets picked up by the laptop's daemon. And when the operator moves a file himself in a file browser, the daemon repairs the inbound links and commits the repair with attribution — Obsidian only rewrites links for moves made inside Obsidian, so without this the link-integrity guarantee would cover FRIDAY's half of the vault and not the operator's.

Two items are deferred on purpose. `Projects/` and `Personal/Home/Projects/` share a name while meaning opposite things — one a promoted-only destination, the other a folder the operator scribbles in freely — and he will migrate that by hand rather than let the cutover decide it. The internal organization of `memory/` and `desk/` is its own session.

## The promotion door

The door is the only way anything reaches the operator's spaces, and it knows three verbs: **place**, when a file crosses for the first time; **update**, when a file already on the operator's side changes; and **retire**, when one moves into an `_Archive/`. All three run the same daemon-executed machinery, never a plain `mv` — write the file at its agreed destination, rewrite every inbound link that pointed at the old path, append a provenance note recording where it came from and when, carry the note's `resources/` attachments across with it so its images don't break, run a link-integrity check across the vault, and refuse to land if that check comes back red. All of it lands as one commit.

Review scales with how autonomous the write was, rather than applying uniformly. When the operator is in the conversation and approves the change, he *is* the review — a diff and a commit, nothing further. An unattended write, whether from dreaming or scheduled filing or anything else he is not watching happen, gets the second model's review principle 5 describes, the same gate covering any other destructive operation. What separates the two is who was watching, not how large the change was: a typo fix requested live and a typo fix made at 3am are different operations even when the diff is identical.

`update` earns its place because the common case is not a file arriving. It is the operator asking FRIDAY to fix something already sitting in his space, and a door that could only place files would have to answer that by copying out to desk, editing there, and promoting back over the original — ceremony for a typo, with him sitting right there having just asked for it.

## The daemon

One resident process, written in Go. The case for Go over Rust rests entirely on not needing to run a model inside the binary: `modernc.org/sqlite` gives pure-Go SQLite with FTS5 built in, no cgo; a static binary cross-compiles for any machine on the home network with `CGO_ENABLED=0`; the standard library covers HTTP and the dashboard with `//go:embed`; `go-git` is mature. Rust's one structural advantage — in-process ML inference via `candle` — only matters if the daemon runs a model directly, and it doesn't.

The week-1 experiment ran on 2026-08-06 and its rule came back FTS5-only, so v0 ships no vector sidecar and the daemon stays a single process with no model anywhere near it. The shape stands recorded for the one case that would reopen it — a driver weaker than Opus calling `memory_search`, a small local model above all, which is where lexical-only retrieval degrades. Should that happen, the sidecar is not Python and not a CGO bridge: it's a small GGUF embedding model served by `llama.cpp`, run as a child process the daemon spawns, health-checks, and restarts — reachable over localhost, reported on the dashboard as part of the daemon's own status. A supervised child, not a second independently-managed resident process, and principle 4 is written to make that distinction explicit rather than something a future build discovers by accident.

Adding it later stays cheap by construction, which is what makes shipping without it safe. The driver talks to the daemon over `memory_search`, so swapping drivers is a client-side change with no daemon work at all, and the sidecar would be additive — a child process plus a fusion step, with FTS5 unchanged underneath. The one genuinely one-way cost is backfill: embeddings for the existing corpus have to be computed once before search can fuse them.

The daemon exposes exactly two MCP tools to any connected client: `memory_search` and `memory_capture`. Sessions call `memory_search` and iterate — the week-1 experiment's whole premise is that an agent searching a warm index in a loop beats a single blind lookup — instead of receiving a fixed dump at session start. The current ~80KB always-load broadcast shrinks to a pointer a session can follow if it wants to, not a wall of text every session pays for whether it's relevant or not.

Three index details come out of week 1's miss lists rather than taste. The tokenizer is FTS5's porter stemmer, so morphological variants stop counting as vocabulary misses. Ranking is column-weighted — title, aliases, and tags above body — and applies the designed status-and-shape penalty: miner fragments and `unfiled` items sort below every non-penalized match without disappearing, which attacks the junk competition that held paraphrase P@5 to 0.14. The penalty's exact form is validated by re-running the gold set on the old stack before it's baked into Go. And `memory_search` takes optional `after:`/`before:` bounds on capture date, because episodic questions — the second-weakest stratum — are time questions the driver couldn't previously express.

Existing to retire on cutover: the orphaned FastMCP daemon on port 7821, and the `agentmemory` MCP entry that currently resolves to the stock filesystem server pointed at the vault's parent directory — a coincidence of naming, not a relationship, and it goes away when the real daemon takes that name.

## The 8,030 existing files

Index everything, immediately, in place. The index is a cache — reading every existing file into FTS5 costs nothing structurally and satisfies "nothing is invisible" on day one, landfill included. Migrate nothing as a separate step; there is no big-bang move.

The 4,933-item inbox specifically: don't exclude it from search, and don't run it through an LLM triage project either — a month of judgment tokens for a one-time cleanup that produces no lasting capability. Instead, rank-penalize it. Items with `status: promoted`, `status: superseded`, or `status: expired`, and unreviewed LOW-confidence fragments, sort lower in search results without disappearing from them. The old system's actual sin was exclusion, not the pile's existence — don't repeat the sin while cleaning up its evidence. Draining the pile for real is dreaming's job, on dreaming's own schedule, once dreaming exists.

## The crickets seam

Crickets depends on agentm through one consolidated file, `agentm_bridge.py` — six verbs, 25 call sites across the repo — and that bridge was already built to survive agentm's absence gracefully: every verb has a documented skip exit code, and the bridge's own contract states it plainly — never an error, never a hang. That design choice, made before this rescope existed, is most of what makes this transition safe to do incrementally.

Three verbs die because the thing they query no longer exists: `capability` (the capability/tier resolver — gone under principle 1), `workflow-persona` (personas — gone under principle 1), and `phase-dispatch` (orchestration nudges — gone under principle 1). Three verbs simplify to near-nothing: `process-seam state-path` becomes a fixed rule — plan and progress files live at `Agent/desk/projects/<slug>/`, no backend selection required; `repo-registry list` becomes a JSON file read, the file itself moving into the vault repo; `governing-design` becomes a glob over `wiki/designs/`, which never needed agentm-specific machinery in the first place.

The `obsidian-vault` crickets plugin — vault-doctor, the conflict-merger session-start hook, `vault_conflicts.py` — is retired outright. It exists to manage a failure mode, Google-Drive sync conflicts, that a local-disk-plus-git store doesn't have. `check-cross-repo-script-parity` and `check-hook-parity` retire alongside it for the same reason principle 1 kills them on the agentm side: they guard vendored copies, and one-copy-of-everything removes the copies they were guarding.

Two things worth noticing rather than just accepting as migration cost. Project phase state — `Agent/desk/`, space 2 — gets indexed by the daemon exactly like everything else, so "what did we decide in the crickets v3 plan" becomes an ordinary memory query instead of a separate lookup path; the harness state stops being a parallel filing system and folds into memory itself. And the direction of context flow inverts: today hooks push context at sessions whether they need it or not; tomorrow a session pulls what it needs by calling `memory_search`. That's an edit to a handful of crickets command files — add a sentence asking them to search memory before drafting — not new code. The pull instruction should also teach the search pattern week 1 measured the absence of: try lexically diverse phrasings before concluding, don't stop on the first plausible hit, and answer "nothing found" only after distinct vocabularies fail — the two drivers split exactly along that behavior, persistent-but-credulous against calibrated-but-quick, and the pattern is a sentence, not a system.

Crickets' own simplification, beyond what dies automatically here, is out of scope for this document and follows as its own arc once the daemon has earned its two weeks of live use.

**The compatibility bar for this seam:** a `/work` session, with the daemon running, can resolve its plan's path, save a project memory through `memory_capture`, and a fresh session started afterward can recall that memory through `memory_search`. That's principle 3's round trip, applied to crickets specifically, and it's the only test this seam needs to pass.

## Build sequence

**Week 1 — done 2026-08-06.** Ran the retrieval experiment (`agentm-rescope-week1-experiment.md`) on the existing Python stack, no daemon code. Delivered the 60-question gold set, four scorecards, and the sidecar call: FTS5-only. Arm A scored R@5 0.725 under Opus, the driver in use.

**Weeks 2–3 — done 2026-08-08.** Daemon v0 shipped in Go, in `daemon/`: it watches the vault, maintains one FTS5 index file (deletable, rebuildable), serves `memory_search` and `memory_capture` over MCP, and captures in a single transaction per the section above. A cold index of all 8,864 files takes 2.6 seconds off the current mount; an unchanged pass takes 39ms. The orphaned port-7821 daemon is retired — its launchd job stopped, its plist archived. No llama.cpp sidecar, per week 1's rule. Reference: `wiki/reference/Memory-Daemon.md`.

Three things the build learned that this document had assumed otherwise, each corrected in the body above: the vault is not a git repository yet, so commit attribution is built and tested but reports itself degraded until the transport migration runs; almost no note in the existing corpus carries a `captured` field, so the temporal bounds are mtime-derived for everything the daemon did not write itself; and the filesystem notifier is an accelerator rather than the mechanism — a periodic reconcile pass is what makes the index's correctness independent of it.

**Week 4 — done 2026-08-09.** The daily self-probe, the two queue numbers and their red thresholds, email alerting on the existing notifier config, and the corpus-write gate. `agentmd status` reports the queue, index freshness, git state, and the last probe result, and exits non-zero when any of them is red. The probe captures a marked synthetic note over the daemon's own MCP surface once a day, asks for it back by two words its prose does not contain, and records the result where status reads it. Reference: `wiki/reference/Memory-Daemon.md`.

**The gate, which replaces an ordering sentence with a precondition.** `agentmd gate corpus-write` fails while git is degraded, and **no corpus-wide write job — migration, backfill, reclassification, dreaming's future drain — may start unless it passes.** The job asks; a person does not remember. It refuses on two conditions, both of which mean the same thing: there is no repository, or the worktree already carries uncommitted changes, so undoing the job and undoing whatever else is in flight would be one command. On a pass it hands back the commit the job would be reverted to. It fails closed — a gate that cannot decide refuses, and there is no override flag, because what is being checked is whether an undo exists at all. `alias_backfill.py`'s `run` and `reapply` are wired to it; `revert` deliberately is not.

This is what the earlier ordering sentence — git-transport lands before dreaming's first corpus-wide pass — was reaching for. The alias backfill ran 1,930 edits with a homemade revert journal as its only undo, and the missing repository was the binding constraint twice in one session. Dreaming's inbox drain is larger, and it now cannot repeat that arrangement rather than being asked not to.

Three threshold decisions are worth stating where the numbers live, and all three are the same decision: an alert that is always red is an alert nobody reads.

Degraded git is reported on every surface and blocks the gate, but it does not page — the vault is not a repository until the migration runs, and a daily email about a deferred migration is how an alert channel teaches its reader to delete it unread. The queue counts only `unfiled` and `inbox`; `superseded` and `expired` are rank-penalized for a different reason and are not waiting on anything, so counting them would put a note retired in 2020 at the head of the queue. And the daemon records a **queue baseline** on its first run, because the first status read against the real vault was 4,349 unfiled items with the oldest 29 days old — the landfill this document already decided to rank-penalize rather than drain. Items captured before the baseline are reported on every surface, with their own count and their own age, and are not paged about; the thresholds read what accumulated since. There is a second reason and it is the stronger one: almost nothing in the existing corpus carries a `captured` field, so those ages come from filesystem mtime, which the sync client can and evidently did rewrite — the age threshold is applied where the number it reads is trustworthy. A four-day-old item captured after the baseline pages even with thousands of inherited items behind it, which is what keeps the split from being a mute button.

**Then two weeks of just living in it** before anything else is allowed to start — dreaming, self-improvement, the rest of the dashboard, the promotion door's mechanism (its policy — FRIDAY doesn't write into the operator's spaces — is already in effect from day one; the door's tooling waits), git-transport migration and the Syncthing spike, and any crickets simplification beyond the seam above. The Python stack freezes for new feature work starting now and keeps running as today's system until the daemon replaces it.

## Related

- `agentm-rescope-principles.md` — the five principles this topology exists to satisfy, each with the failure it was paid for by.
- `agentm-rescope-memory.md` — what happens inside `Agent/`: the memory layout, the two lifecycle classes, capture doctrine, dreaming's job list, and the autonomy dial the promotion door's review rule is one instance of.
- `agentm-rescope-week1-experiment.md` — the experiment gating the vector-sidecar decision referenced above.

## Amendment log

- **2026-08-09 · the queue baseline, added after the first real-vault reading.** The loud queue as specified went red immediately and permanently on the operator's own machine: 4,349 unfiled items, oldest 29 days. Both facts were already known and already decided — the pile is rank-penalized and drained by dreaming later — so the alert would have been noise from its first hour. The daemon now records a baseline on first run and applies the thresholds to what accumulated after it, while reporting the inherited count, its age, and the baseline date on every status surface. *Why not just raise the thresholds:* the numbers are correct; what was wrong was measuring a backlog with a stall detector. *Why not exclude the pile from the count:* that is the previous system's sin in a new costume — it stays counted and visible, it simply does not page. *Why the split is defensible beyond convenience:* inherited ages are mtime-derived and a sync client rewrites mtimes, so the pre-baseline half of the queue has no trustworthy age to threshold on. *Re-audit trigger:* dreaming's inbox drain landing, after which the inherited count should fall toward zero — if it does not move, the baseline is hiding a drain that never ran.
- **2026-08-09 · week 4 shipped, and the build sequence's ordering sentence became a gate.** The loud queue, the daily self-probe, and `agentmd gate corpus-write` are in the daemon. The ordering constraint recorded here on 2026-08-08 — git-transport before dreaming's first corpus-wide pass — is now a machine-readable precondition that job scripts ask rather than a rule people remember, and it refuses on a dirty worktree as well as on a missing repository, because both mean the same thing: no nameable state to revert to. *Why the gate refuses rather than warns:* a warning on a job that rewrites thousands of notes is read once and then filtered, and the thing being checked is not a preference but whether undo exists. *Why degraded git does not page:* it is a known deferred migration, and a daily email about it would train the operator to ignore the channel before it ever carries news. *Re-audit trigger:* the git-transport migration landing, at which point the gate's git-degraded branch stops being the one that runs and the dirty-worktree branch becomes the whole of it — worth re-measuring then, because a vault the daemon commits continuously should be clean almost always, and if it is not, the gate will be the thing that discovers it.
- **2026-08-08 · backup topology decided — everything stays in the house.** The working repository lives on the Mac; a bare repository on the primary Unraid NAS (plugin-installed git) takes a daily push; the backup NAS's existing monthly cold mirror inherits the third copy, contingent on the git share being inside its mirror scope; Tailscale is the optional away-path. No off-site leg. *Why not the encrypted-cloud alternative (restic or git-remote-gcrypt):* the operator judged an always-on NAS at daily cadence plus an air-gapped cold box at monthly cadence redundant enough for his tastes, and declined the standing key-custody cost against a house-level-loss risk he accepts. *Re-audit triggers:* the daily push breaking silently — with one live backup leg, a dead push is zero redundancy, so last-push-age joins week 4's loud numbers alongside the unfiled-queue age; the homelab notes' mirror-scope TO CONFIRM resolving negative; or the vault taking on content whose loss would be unacceptable (email ingest's third-party facts, scanned originals with no other home).
- **2026-08-08 · daemon v0 shipped; three assumptions corrected.** The index, the rank penalty, and the two MCP tools are as specified. Corrected against what the machine actually is: git commits are built and verified but degraded, because the vault is still on the synced mount and the daemon will not initialize a repository on its own initiative — that is the operator's migration to run, and a silent `git init` under 8,864 files is the unilateral move the build sequence defers. Capture dates fall back to filesystem mtime for 8,845 of 8,864 notes, so `after:`/`before:` are exact for daemon-written notes and approximate for the rest; the index pins the first date it observes so an edit cannot move it. And the vault layout stayed `personal/`/`projects/` with the space mapping in config, so the `Agent/memory` + `Agent/desk` migration is two config keys rather than a rewrite. *Why not init the repo and backfill the dates:* both rewrite the operator's vault to make a design document true, and the design's own build sequence puts that migration after two weeks of living in the daemon. *Re-audit trigger:* the git-transport migration landing — at which point the degraded-git path stops being the one that runs, and the mtime fallback should be re-measured against a corpus the daemon has been writing into for a month.
