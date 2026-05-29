# How to configure a new project on first session

> [!NOTE]
> **Status:** implemented
> **Plan:** `.harness/PLAN.md` tasks 3 (setup detect → propose → approve → write flow) + 4 (SessionStart nudge).
> **Goal:** Open a repo the harness hasn't seen, run the detection flow, and persist an approved enablement config so every later phase resolves this repo's `{slug, type, enabled skills/hooks}`.
> **Prereqs:** agentm v4.8.0+ (ships V4 #32), `python3` on `PATH`, and a repo with a `.git` dir. A reachable vault (`MEMORY_VAULT_PATH`) to persist the config — without one the proposal still renders but the write is skipped.

When you open an unconfigured code project, the SessionStart hook emits a one-line nudge offering to configure it. Saying "configure this project" or running `/setup --detect` scans the repo, renders a default-all-enabled proposal with a per-skill/per-hook rationale, and writes the approved enablement block to `project.json` on approval.

## Steps

1. **Open the unconfigured repo.** The `harness-context-session-start` hook fires a single line each session until the repo is registered or `.agentm-no-register` is present:

   ```text
   [agentm] New project — I haven't configured this repo. Say 'configure this project' or run /setup --detect.
   ```

2. **Run the detection flow.** Say "configure this project" or run `/setup --detect`. `/setup` runs detection as its first step (§0), before the inventory + interview:

   ```bash
   python3 scripts/detect_project.py . --format json   # structured proposal (drives agent logic)
   python3 scripts/detect_project.py . --format text   # operator-facing approval block
   ```

3. **Read the proposed config block.** Every enableable skill and hook starts enabled (`✓`); each line carries a rationale — a default reason, or a detection reason when a rule matched this repo. See [Detection rules](Detection-Rules) for the 10 rules and what each attaches a rationale to.

4. **Choose a/b/c:**
   - **(a) Register with all-enabled** — `python3 scripts/project_config.py register .`
   - **(b) Register with custom selection** — answer per-skill/per-hook (Enter keeps the default of enabled), then pass each decline: `python3 scripts/project_config.py register . --disable <name> --disable <name>`. Each `--disable` records an `operator_overrides` entry.
   - **(c) Skip** — `touch .agentm-no-register`. One-time scratch session; the nudge stays silent until you remove the marker.

5. **On (a) or (b), finish registration.** `register` writes the enablement block to `project.json` (see [Project config](Project-Config)) and registers the repo in the vault `repo_registry`. Then:
   - Create the vault `_index.md` at `<vault>/projects/<slug>/_index.md` if absent — confirm the project dir with `python3 scripts/harness_memory.py vault-state-path PLAN.md`.
   - Accept or decline the offer to add a `vault_slug: <slug>` line to `AGENTS.md` (operator-confirmed; never silent).

6. **Re-open the repo.** `python3 scripts/project_config.py is-registered .` prints `registered` (exit 0) and the SessionStart nudge stays silent.

## Related

- [Detection rules reference](Detection-Rules) — the 10 built-in rules and what each detects.
- [Project config reference](Project-Config) — the `project.json` enablement-block schema this flow writes.
- [Auto-detect + auto-configure](../explanation/Auto-Detect-Configure.md) — why the flow proposes-then-approves and why config lives in `project.json`.
- [Install into project](Install-Into-Project) — the install step that precedes first-session configuration.
