# How to archive a finished project

> [!NOTE]
> **Status: implemented** — the `completed/` moves landed in agentm-vault plan 11. The night does the moving; this page is the two things it waits on and the one thing it never touches.
> **Goal:** Retire a finished project without moving anything by hand, and without losing what other work still depends on.
> **Prereqs:** Write access to the project's tracker; `scripts/repo_registry.py` if the project has a registered repo.

Since plan 11 you do not move a finished project. You close its tracker, and the night's next pass moves it to `projects/completed/<slug>/`. What you still do is the part a pass cannot: decide what was learned, and say when the work is actually over.

## Steps

1. **Write the Outcome, then set `status: done`.** The night moves a project only when its tracker reads `done` **and** its `## Outcome` section has something in it. A `done` tracker with an empty Outcome is a session mid-edit, and moving the project out from under the session writing it is the one thing the pass must not do. So the Outcome is the gate, not a formality: it is also the raw material the weekly crystallize phase reads for what the arc taught.

2. **Mark the arc, if it was one.** If the work was an arc rather than a single project — a roadmap item that closed, a series that ended, a version line that shipped — add its name to `arc_closed:` on the project's own tracker. The next crystallize run writes the arc's synthesis at `memory/crystallized/<project>-<arc>.md` from every Outcome in the project. The field accumulates; a second closed arc adds a line rather than replacing the first.

3. **Harvest only what must rank at full weight.** Everything under `completed/` is still on disk, still linked, still returned by search — at ×0.30, the same weight every archive-class path carries. That is usually right: finished work should be findable and should not outrank live work. It is wrong for the rare note other *active* projects depend on — a convention, a decision other designs still cite. Check with `grep -rl "<slug>" <vault>` from outside the project, and move anything load-bearing to where that kind of note lives, keeping its filename so `[[wikilinks]]` keep resolving.

4. **Let the night do the rest.** On its next pass it moves the project directory to `projects/completed/<slug>/`. Individual tasks are handled separately and earlier: when a task's tracker closes, that task's research bundles move to `<slug>/completed/research/`, and a superseded decision moves to `<slug>/completed/decisions/`. **Task directories never move at all** — the sequence of a project's tasks is the history of the project as you read it, and a pass does not rewrite that.

5. **Unregister the repo, if there is one.** `python3 scripts/repo_registry.py unregister <slug>`. Check the registry's own list first; most vault-only projects were never registered.

6. **Nothing to update by hand.** `moc-root.md` and the nightly `moc-projects.md` list whatever project folders exist outside `completed/` and `_archive/`. The move is the whole mechanism.

## Why this shape

- **The Outcome is the gate.** It is the one thing only the closing session knows, it is what the crystallize phase reads, and requiring it is what keeps the pass from moving a project someone is still writing.
- **Moving, not hiding.** Obsidian resolves links by basename, so a moved project is still found by every link that named it. What the move buys is the eyeline: the projects space holds what is live, and one folder holds what is not.
- **×0.30, not invisible.** A finished project's decisions are often the best answer to a question about why something is the way it is. Demoted, they are still there when they are the only answer.
- **Tasks stay put.** They are numbered in the order the work happened. Re-filing them would destroy the one thing their arrangement says.

## Verify

- `go test ./internal/dreaming/` from `daemon/` — the closed-task bundles, the superseded decision, the whole-project move, the Outcome gate, and that no task directory moves.
- `agentmdream run -force` — prints what the next pass would move, and writes nothing.

## Troubleshooting

- **The project did not move.** Its tracker's `status:` is not `done`, or its `## Outcome` is empty. Both are checked; the pass reports what it read.
- **I need it back.** Move the directory out of `projects/completed/` and set the tracker's `status:` to something other than `done`. Nothing moves it back on its own — reopening a project is a decision, not a state.
- **A note I needed dropped down the results.** It is in `completed/`, at ×0.30. If it is genuinely still live work, step 3 is the fix: move that one note out, rather than the project back.

## See also

- [AgentM Vault design § Projects and tasks](../designs/agentm-vault) — the tracker schema and the `completed/` rules.
- [Memory daemon reference § crystallize](Memory-Daemon#crystallize-the-weekly-phase) — what the weekly phase does with the Outcome you write, and how `arc_closed:` is read.
- [Tune the archive](Tune-The-Archive) — the memory classes' own lifecycle, which is a different mechanism on a different clock.
- [Audit the vault](Audit-The-Vault) — run before a harvest pass if you are unsure what is safe to leave behind.
