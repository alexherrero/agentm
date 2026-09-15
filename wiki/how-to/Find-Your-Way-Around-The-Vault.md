# How to find your way around the vault

> [!NOTE]
> **Goal:** Find where something lives in the vault, and confirm the CLI, the daemon, and the consistency gates all read the layout the same way.
> **Prereqs:** A configured vault ([Choose a storage backend](Choose-A-Storage-Backend)). Shell access to the machine it lives on.

The vault is five lowercase root spaces beside two root notes. Every space is spelled the way the code spells it, since the root casing migration (agentm-vault plan 08 — see [Run the root casing migration](Run-The-Root-Casing-Migration)). Read every name on this page exactly, case included: the disk is case-insensitive, so a stale Title Case directory still opens if you type its name by hand — that's the only thing the case-insensitivity hides. Every exact-string comparison in the codebase, and Linux CI's case-sensitive disk, see the mismatch and refuse it.

## Steps

1. **List the root.** `ls <vault>` prints the five spaces — `agent`, `calendar`, `personal`, `projects`, `standards` — and the two root notes, `index.md` and `Ideas.md`. Both are generated now; neither is hand-kept.

2. **Look inside `agent/`, the agent's own half.**

   | Directory | Holds |
   |---|---|
   | `agent/memory/` | The six memory classes: `semantic/`, `procedural/`, `episodic/`, `entities/`, `crystallized/`, `mocs/`. |
   | `agent/diagnostics/` | Scorecards and digests. |
   | `agent/archive/` | Not yet there: the retention plan (agentm-vault plan 11) creates it, mirroring `memory/`'s own tree. Today `agent/` holds the two directories above. |

   Nothing else sits loose in `agent/` except the data runs' dot-named markers, which the shape gate tolerates.

3. **Look inside `calendar/`.** One `YYYY/` directory per year, holding facet notes, plus a generated `moc-calendar-YYYY.md` beside each year. `_daily-template.md` sits at the calendar root — it stays there until plan 10 wires the diary facet.

4. **Look inside `personal/`.** Your own filing, Title Case inside and untouched by the root casing migration — that migration renamed only the four root spaces themselves, never anything below them. `personal/Home/`, `personal/Church/`, and the rest of your tree read exactly as you left them.

5. **Look inside `projects/`.** One tree per project: `charter.md`, `tracker.md`, `followups.md`, `decisions/`, `designs/`, `research/`, `drafts/`, `tasks/`, and a generated `moc-<slug>.md` (the design's skeleton; a project that predates the projects migration, agentm-vault plan 10, still carries its `_harness/` tree and whatever else it grew). A finished project's tree lives under `projects/completed/` instead.

6. **Look inside `standards/`.** The four always-load files, plus `voice/` for the on-demand voice rules. Nothing else lives here.

7. **Confirm the memory root.** The agent's own tree begins at `<vault>/agent`, not at the vault root itself. Two config keys say so:

   ```bash
   python3 scripts/agentm_config.py --get memory_root
   python3 scripts/agentm_config.py --get daemon.spaces
   ```

   The first prints `agent`. The second prints the vault-relative directory for every logical space the daemon writes, `{"memory": "agent/memory", "projects": "projects"}` among them. If you export `MEMORY_VAULT_PATH` by hand anywhere, confirm it ends in `/agent` — that's the memory root it names, never the vault root.

8. **Confirm every tool agrees.** Four different readers of the same layout, checked against each other:

   ```bash
   ls <vault>
   agentmd status                                  # the "vault" line
   python3 scripts/check-memory-root-consistency.py
   python3 scripts/check-no-title-case-roots.py
   ```

   `ls` is the filesystem's own answer. `agentmd status` prints a `vault` line naming the path the daemon resolved at its last start — compare it against `ls` by eye. `check-memory-root-consistency.py` confirms the Go daemon's `daemon.spaces` and the Python stack's own space config resolve to the same directories. `check-no-title-case-roots.py` confirms nothing in the codebase still names a root space by its retired Title Case spelling. A tool that disagrees with the other three is the one that's wrong.

## Verify

- `ls <vault>` lists exactly `agent`, `calendar`, `personal`, `projects`, `standards`, `index.md`, and `Ideas.md` at the top level.
- `agentmd status`'s `vault` line names the vault root, not `<vault>/agent`.
- `check-memory-root-consistency.py` and `check-no-title-case-roots.py` both read clean.

## Troubleshooting

- **A root space shows up Title Case.** Something wrote to the old path after the root casing migration ran. See [Run the root casing migration § Troubleshooting](Run-The-Root-Casing-Migration#troubleshooting).
- **`agentmd status`'s `vault` line and `ls <vault>` disagree.** The daemon resolved a stale `vault_path` — check what set it (a plist, a shell profile) and restart the daemon once it's fixed.
- **A directory opens fine by hand but the tools act like it's missing.** You're looking at the case-insensitive disk hiding a case mismatch. Check the exact spelling with `ls`, not by opening the folder.

## Related

- [AgentM Vault design](../designs/agentm-vault) — "The root spaces are lowercase," the ruling this layout follows.
- [Run the root casing migration](Run-The-Root-Casing-Migration) — the migration that got the vault here.
- [Set up Obsidian on the vault](Use-Obsidian-With-The-Vault) — reading this same layout through Obsidian's graph and backlinks.
- [CI gates reference](CI-Gates) — `check-no-title-case-roots` and `check-memory-root-consistency`, the two gates step 8 runs by hand.
