---
title: AgentM Rescope — Principles
status: proposed
kind: design
scope: architecture
area: agentm
seeded: 2026-08-02
---

# AgentM Rescope — Principles

## Context

Four months into a project scoped for two, agentm still breaks in small ways every week. The instinct was that the system had drifted too far into prescriptive instructions and excessive determinism — thousands of tests, dozens of CI gates, a cross-platform install matrix — for a personal tool with one user. That instinct was half right.

The measured reality, checked against the live machine rather than estimated: of agentm's roughly 98,000 lines of Python, only about 26,500 is the memory engine itself. The rest is orchestration briefing, idle dispatch, phase nudges, dreaming, ideas incubation, opinion routing, health scorecards, watchlists, arc registries, and personas — machinery about the harness, not memory. Test code runs to 47,796 lines against that 26,500-line engine, and 2,745 tests were green the entire time recall was returning nothing.

Because it was. Per-prompt recall runs on a 300ms budget (`PROMPT_SUBMIT_BUDGET_MS`). A cold embedding-model load costs roughly 4,385ms, and every hook is a fresh process, so the vector search branch takes a deterministic early-return skip on every single prompt — not a race, an `if` that always trips. The lexical fallback has no persistent index; it re-walks the corpus on every call and discards the result outright if the walk doesn't finish inside budget. The vault holds 8,030 markdown files; the code's own benchmark comment says 300ms reaches about 70 of them. And of the 6,046 files under `personal/`, 4,933 sit in `_inbox/` — a staging directory recall excludes by default. A fix exists and is unplugged: a FastMCP daemon runs under launchd on port 7821, health-checks clean, and nothing is wired to it.

Two explanations were on the table for how this happened. The operator's own framing was prescriptive-instruction drift. A second, adversarially-obtained framing called it framework addiction — infrastructure built and tested while the actual product went unverified. A third-party arbitration, given both framings and read access to the repos, ruled that neither is the load-bearing mechanism. **The real cause was Goodhart's law applied to the harness's own founding doctrine.** That doctrine ranks deterministic checks as cheap and truthful and treats LLM judgment as expensive and unreliable — a reasonable trade in 2024. But live recall quality cannot be a unit test, so it was never anyone's definition of done, and four months of agent effort optimized what the gates could see instead of what the product needed to do. The gates were not too strict. They were pointed at the wrong variable.

That ruling is why principle 3 below is phrased as an outcome, not a procedure, and why it outranks every other principle in this document.

## What FRIDAY means here

The product goal, unchanged since it was first stated: a disk-based universal memory that knows everything the operator has told it, recalls it from his laptop in about a second, and requires him to never think about its own files. The laptop is the primary machine and must be excellent before anything else — mobile and multi-device access are explicitly secondary, allowed to be worse, and come after.

Two working spaces belong to the assistant. **FRIDAY's memory** is its durable knowledge. **FRIDAY's projects** is how it organizes work it does on the operator's behalf — plans, roadmaps, working drafts — laid out however serves the work, not however a human would file it.

Two belong to the operator. **His projects** are outputs the two of them have agreed on — designs, deliverables — organized the way he asks for, in directories he names. **His personal files** are his own, untouched.

A file crossing from FRIDAY's projects into the operator's projects is a joint decision, never a unilateral one. FRIDAY always knows which space a thing lives in and how it got there. The mechanism for that door, and the directory layout on both sides, is intentionally not decided in this document — see `agentm-rescope-topology.md`.

## The five principles

Each principle exists because something specific broke without it. A principle that names no failure is a slogan and does not belong in this list — an earlier ten-principle draft of this document had three that failed that test, and they were cut or merged, not softened.

### 1. Two halves, nothing else

AgentM is a memory — capture, file, recall — and a resident process that keeps it good and shows its state: dreaming, self-improvement, maintenance, a dashboard. Nothing outside those two halves is agentm.

**Paid for by:** three-quarters of the current repo is neither half. Personas, opinions, orchestration briefing, idle dispatch, phase nudges, ideas incubation, health scorecards, watchlists, and arc registries all sit outside this line, and none of them made memory work better.

### 2. Files are truth, git is undo, every index is a deletable cache

Markdown plus frontmatter on disk is canonical. A private git repository is sync, backup, and history in one. Any index — FTS5, vector, graph — is a cache that can be deleted and rebuilt from the files without loss. Obsidian is a viewer with no authority over what's real.

**Paid for by:** the vault lives on a Google-Drive-synced mount today, with a documented history of sync-conflict files and a `vault-doctor` skill that exists only to find them. A database on a synced path is a known corruption pattern. There is no undo story for a bad write on that mount; git makes every state revertible by construction.

### 3. Nothing is saved until a fresh session can ask and get it back — that round trip is the definition of done

Save a fact. Start a new session. Ask about it sideways, not verbatim. Get it back. That is the only test that counts, and it runs on the real corpus, on a schedule, as a number that can go down. Deterministic tests may block a merge. They may never mark a milestone done.

**Paid for by:** 2,745 green tests, 39 CI gates, a three-OS matrix — and a memory system that returned zero results on every interactive prompt for the length of the project. Not one of those tests asked the question this principle asks.

### 4. One resident service, near-zero dependencies, judgment is delegated and its absence is loud

One process holds the warm index, serves the dashboard, runs dreaming and maintenance, and is the only thing clients talk to. It may supervise child processes it owns — an embedding sidecar, for instance — without that counting as a second resident thing. What it may not have is a second, independently-managed daemon running alongside it. Nothing requiring judgment runs inside the service itself; judgment is shelled out to whichever agent CLIs are installed (`claude`, and `agy` for cross-model review), and every one of them is optional. A missing agent degrades capability visibly. It never degrades silently.

**Paid for by:** the orphaned FastMCP daemon on port 7821, running and healthy and wired to nothing. A second, unrelated MCP server answering under a confusingly similar name. A 1.3GB embedding checkpoint required for a capability that was unreachable on the operator's own machine for the project's entire life, because the hook interpreter couldn't load the SQLite extension it needed — a silent failure for over a year before anyone measured it.

### 5. The operator's files change only through the joint door, and anything destructive is procedural and reviewed

Objectives govern taste and structure — prefer a sentence in a prompt over a script, a check the model performs over a gate CI enforces. But destructive operations and anything crossing into the operator's spaces stay procedural: second-model reviewed before landing, and always committed in a form that can be reverted. This is the one principle that is deliberately not simplified further, because it is insurance, and insurance earns its keep by being boring.

**Paid for by:** nothing yet, on purpose. This principle's cost is paid in advance, not after an incident.

## What this replaces, and what it doesn't yet

This document does not supersede `agentm-auto-organization.md`, `agentm-autonomy.md`, `agentm-capture.md`, or `agentm-experience-and-dreaming.md`. Those remain the accurate description of the system currently running. They get formally retired or rewritten once the daemon described in `agentm-rescope-topology.md` has shipped, passed its own week-one experiment, and been lived in for two weeks under principle 3's own test — not before, and not by implication.

Crickets' phase-loop logic (plan/work/review/release/bugfix), its worktree flow, code review, and developer-safety tooling are unaffected by this document. Its dependency on agentm narrows sharply under this rescope — see the seam contract in `agentm-rescope-topology.md` — but crickets' own simplification is out of scope here and follows on its own schedule.

## Related

- `agentm-rescope-week1-experiment.md` — the retrieval experiment principle 3 requires before any storage or language decision is acted on.
- `agentm-rescope-topology.md` — where the truth lives, how the phone stays working, the four-space directory layout, and the crickets seam contract.
