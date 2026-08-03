# How to re-detect a configured project

> [!NOTE]
> **Goal:** Re-scan a repo you already configured, see what detection would say differently now, and decide what to keep.
> **Prereqs:** A repo with an enablement block in `project.json` (run [Configure a new project](Configure-A-New-Project) first), and `python3` on `PATH`.

Repos change after you configure them. You add a `wiki/` dir, a CHANGELOG, a Dependabot config — and the rationale stored in `project.json` slowly stops describing the repo you actually have. Re-detection re-runs the same rules and shows you the gap. It surfaces the diff; it does not act on it.

## Steps

1. **Run the diff.** Say "re-detect this project" or run `/setup --redetect`. Either way the underlying call is:

   ```bash
   python3 scripts/project_config.py redetect .
   ```

   You get one block per target that moved, each naming the rule behind it:

   ```text
   Re-detected my-app.
   Rules matching now: R-wiki, R-pii

   Proposed changes:
     [newly-detected] skill diataxis-author
         a rule now justifies this — refresh its rationale
         now: R-wiki — Found wiki/ dir -> diataxis-author manages Diataxis-shaped documentation.

   Suppressed — you already decided these, so re-detect will not re-suggest them:
     [newly-detected] skill pii-scrubber — declined on 2026-06-01T00:00:00Z (.envrc is direnv, not a secret)

   Nothing was changed. Re-run with --apply to refresh the detection rationale.
   ```

2. **Read the suppressed list.** Anything you declined at registration lands there instead of in the proposal, with your original reason attached. That list is how you check the harness is still honoring a past decision — if a skill you turned off shows up under *Proposed changes* rather than *Suppressed*, the override didn't record, and you should re-run the decline.

3. **Decide.** The run above changed nothing except the `last_redetect_at` stamp. Your options:

   - **Keep the stored rationale** — do nothing. The diff is informational.
   - **Refresh the rationale** — `python3 scripts/project_config.py redetect . --apply`. This writes `auto_detected`, `rule_id`, and `rationale`. It never touches `enabled`, so applying cannot turn a skill off.
   - **Turn something off** — that's an override, not a detection result. Record it through the register path: `python3 scripts/project_config.py register . --disable <name>`. From then on, re-detect suppresses suggestions about that target.

4. **Confirm it converged.** Re-run the plain command. A clean repo prints `No changes — the stored config still matches what this repo looks like` and exits `0`.

## Checking without writing anything

The default run stamps `last_redetect_at`, because that field records when the scan last ran. For a look that touches the file not at all:

```bash
python3 scripts/project_config.py redetect . --dry-run
```

## Scripting it

Use `--format json` for the machine-readable diff and read the exit code: `0` means the config matches the repo, `1` means changes were surfaced, `2` means re-detect couldn't run — the repo has no enablement block yet, or it's the harness source repo, which detection bypasses.

```bash
python3 scripts/project_config.py redetect . --format json
```

## Related

- [Configure a new project](Configure-A-New-Project) — the first-session flow that writes the block this compares against.
- [Project config](Project-Config) — the change categories, the exit codes, and the enablement-block schema.
- [Detection rules](Detection-Rules) — the rules being re-run and what each attaches a rationale to.
- [Auto-detect + auto-configure](Auto-Detect-Configure) — why re-detection surfaces a diff instead of applying one.
