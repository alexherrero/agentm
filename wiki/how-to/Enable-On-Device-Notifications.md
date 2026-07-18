# How to enable the on-device notification

> [!NOTE]
> **Status: implemented** — shipped by `PLAN-proactive-delivery.md#task-3` (FRIDAY ladder feature 1, task 3 of 5); the runner-job manifest referenced in step 6 shipped in task 5 of the same plan.
> **Goal:** Opt in to a once-daily native notification carrying the same headline the SessionStart brief line shows, so a stalled or notable digest reaches you even on a day you never open a session.
> **Prereqs:** macOS — the channel fires via `osascript`; a non-macOS host silently no-ops. A resolvable vault with at least one digest-ladder cycle already run (see [Persist a morning report](Persist-A-Morning-Report) for the sibling `scripts/health/` family). To have the runner fire this daily, copy `templates/jobs/observability-notify-daily.yaml` into `.harness/jobs/` (step 6 below) — until then, invoke it yourself.

`scripts/health/session_notify.py` is the opt-in, once-daily on-device notification channel from `wiki/designs/agentm-autonomy.md`'s [Delivery subsection](agentm-autonomy#delivery--getting-it-in-front-of-you). It reads the same digest ladder the SessionStart line already reads, so what you see in the notification matches what you'd see at the top of your next session.

## Steps

1. **Opt in.** Set the config key that gates the whole channel:

   ```bash
   python3 scripts/agentm_config.py --notify-enabled true
   ```

   This writes `plugins.autonomy.notify_enabled` (`cmd_set_notify_enabled`, `scripts/agentm_config.py:213`; the key itself declared at `scripts/agentm_config.py:77`). Confirm it took with `--get plugins.autonomy.notify_enabled`.

2. **Run the notifier.** Until you register the runner job (step 6), invoke it directly:

   ```bash
   python3 scripts/health/session_notify.py
   ```

   `main()` (`session_notify.py:169`) calls `run()` (`session_notify.py:138`), which checks the opt-in first (`notify_enabled()`, `session_notify.py:146`), resolves the vault via `session_brief.resolve_vault()` (`session_notify.py:149`), and no-ops silently on any missing piece — disabled, no vault, or no digest ever run.

3. **What fires.** `notify_body()` (`session_notify.py:120-135`) calls `session_brief.build_brief()` — the exact digest reader the existing SessionStart line uses, not a second parser — and strips the leading `"[agentm] "` the SessionStart line carries, since a native notification banner already has its own title. `_fire_osascript()` (`session_notify.py:99-110`) shells to `osascript -e 'display notification "<body>" with title "AgentM"'`.

4. **Once-a-day, not once-per-hours.** A calendar-day state file at `~/.cache/agentm/telemetry/notify-state.json` (`default_state_path()`, `session_notify.py:70-71`) records `last_fired_date`. A second call the same day is a no-op (`_already_fired_today()`, `session_notify.py:80-85`) no matter how many times you (or the runner job below) invoke the script — this is deliberately calendar-day anti-fatigue rather than `session_brief.py`'s hours-based cooldown, because a runner-scheduled job doesn't have "once per session boot" semantics.

5. **Turn it back off.**

   ```bash
   python3 scripts/agentm_config.py --notify-enabled false
   ```

   The channel graceful-skips the moment the key isn't `true` (`notify_enabled()`, `session_notify.py:53-67`) — no notification fires, no state file is touched.

6. **Optional: let the runner fire it for you.** `templates/jobs/observability-notify-daily.yaml` is the job manifest for this channel — `schedule: daily`, `command: python3 -m health.session_notify`, `dry_run: false` (the opt-in config gate above is already the safety gate, so a second dry-run layer would be redundant). `.harness/jobs/` is gitignored, so registering it is a one-time local copy:

   ```bash
   cp templates/jobs/observability-notify-daily.yaml .harness/jobs/observability-notify-daily.yaml
   ```

   The local runner (`scripts/agentm-runner.sh`) then picks it up on its next daily tick. A fresh copy still fires nothing until `--notify-enabled true` is set — the manifest schedules the check, not the fire.

## Verify

- `test_disabled_by_default_never_fires` and `test_enabled_with_osascript_fires` (`RunEndToEndTests`, `scripts/health/test_session_notify.py:157-175`) prove the opt-in gate end to end.
- `test_same_day_rerun_does_not_refire` and `test_new_day_with_new_digest_refires` (`scripts/health/test_session_notify.py:192-217`) prove the calendar-day anti-fatigue behavior.
- `test_strips_agentm_prefix` (`scripts/health/test_session_notify.py:123-129`) proves the notification body matches the SessionStart line minus its prefix.

## Troubleshooting

- **Nothing fires and no error appears.** By design — `run()` swallows every exception and every graceful-skip case returns `False` silently (`session_notify.py:145-166`), so a missing vault, an unset opt-in, or a non-macOS host all look identical: nothing happens. Check `python3 scripts/agentm_config.py --get plugins.autonomy.notify_enabled` first.
- **Non-macOS hosts.** `_fire_osascript()` (`session_notify.py:99-110`) resolves the `osascript` binary with `shutil.which`; its absence is a silent no-op, not an error.

## See also

- [Autonomy — Delivery](agentm-autonomy#delivery--getting-it-in-front-of-you) — the design this channel implements, and the amendment log entry with the full build detail.
- [Installer CLI](Installer-CLI) — the `--notify-enabled` / `--email-to` / `--email-smtp-url` config-key reference row.
- [Enable email digest delivery](Enable-Email-Digest-Delivery) — the sibling opt-in delivery channel, same plan.
- [Persist a morning report](Persist-A-Morning-Report) — the sibling `scripts/health/` recipe for the overnight-run report this same digest ladder feeds.
