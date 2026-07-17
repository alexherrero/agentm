# How to enable the daily digest email

> [!NOTE]
> **Status: implemented** — shipped by `PLAN-proactive-delivery.md#task-4` (FRIDAY ladder feature 1, task 4 of 5).
> **Goal:** Opt in to a once-daily email carrying the same digest the SessionStart brief line shows, for a read away from the machine.
> **Prereqs:** A first-party SMTP relay or on-device mail agent you control — this channel never talks to a third-party push service. A resolvable vault with at least one digest-ladder cycle already run (see [Persist a morning report](Persist-A-Morning-Report) for the sibling `scripts/health/` family). **Not yet wired to the runner** — task 5 of the same plan schedules this automatically; until it ships, you invoke it yourself.

`scripts/health/session_email.py` is the opt-in, once-daily email channel from `wiki/designs/agentm-autonomy.md`'s [Delivery subsection](agentm-autonomy#delivery--getting-it-in-front-of-you). It reads the same digest ladder the SessionStart line and the on-device notification already read, so the email matches what you'd see elsewhere.

## Steps

1. **Set both config keys.** The channel requires **both** — either alone reads as unconfigured and the channel silently no-ops:

   ```bash
   python3 scripts/agentm_config.py --email-to you@example.com
   python3 scripts/agentm_config.py --email-smtp-url smtp://relay@localhost:587
   ```

   These write `plugins.autonomy.email_to` and `plugins.autonomy.email_smtp_url` (`cmd_set_email_to`, `scripts/agentm_config.py:238`; `cmd_set_email_smtp_url`, `scripts/agentm_config.py:262`; keys declared at `scripts/agentm_config.py:78-79`). Confirm with `--get plugins.autonomy.email_to` / `--get plugins.autonomy.email_smtp_url`. The `smtp://` URL takes the form `smtp://[user@]host[:port]` — the optional `user@` becomes the `From` address; port defaults to `25` when omitted.

2. **Run the emailer.** Nothing schedules this yet (see the Prereqs note above), so invoke it directly:

   ```bash
   python3 scripts/health/session_email.py
   ```

   `main()` (`session_email.py:181`) calls `run()` (`session_email.py:148`), which checks both keys are set first (`email_config()`, `session_email.py:53-74`), resolves the vault via `session_brief.resolve_vault()` (`session_email.py:160`), and no-ops silently on any missing piece — unconfigured, no vault, or no digest ever run.

3. **What it sends.** `email_body()` (`session_email.py:106-121`) calls `session_brief.latest_digest()` — the newest delivered digest note, whichever cadence landed most recently. This is a deliberate difference from `session_notify.py`, which calls `build_brief()`: an email has no session-boot moment to gate staleness against, so it just wants the newest digest, stale or not — the SessionStart line's own deadman logic already covers "the ladder went quiet." `_send_smtp()` (`session_email.py:124-145`) builds the message with stdlib's `email.message.EmailMessage` and sends it via `smtplib.SMTP` — the operator's own configured host is the only destination this ever talks to, never a third-party relay.

4. **Once-a-day, not once-per-hours.** A calendar-day state file at `~/.cache/agentm/telemetry/email-state.json` (`default_state_path()`, `session_email.py:77-78`) records `last_sent_date` (`_record_sent()`, `session_email.py:95-103`), checked by `_already_sent_today()` (`session_email.py:87-92`). A second call the same day is a no-op regardless of how many times you (or a scheduler, once task 5 lands) invoke the script. An SMTP failure never records the day as sent, so the next invocation retries rather than silently skipping a day.

5. **Turn it back off.** Unset either key and the channel graceful-skips:

   ```bash
   python3 scripts/agentm_config.py --unset plugins.autonomy.email_to
   ```

## Verify

- `test_configured_sends` and `test_unconfigured_never_sends` (`RunEndToEndTests`, `scripts/health/test_session_email.py:156-170`) prove the both-keys-required gate end to end.
- `test_same_day_rerun_does_not_resend` and `test_new_day_resends` (`scripts/health/test_session_email.py:172-193`) prove the calendar-day anti-fatigue behavior.
- `test_smtp_failure_does_not_record_sent` (`scripts/health/test_session_email.py:195-204`) proves a failed send doesn't consume the day's attempt.
- `test_subject_and_body_from_latest_digest` (`scripts/health/test_session_email.py:118-124`) proves the email body matches the latest delivered digest.

## Troubleshooting

- **Nothing sends and no error appears.** By design — `run()` swallows every exception and every graceful-skip case returns `False` silently (`session_email.py:154-178`), so a missing key, a missing vault, or an SMTP failure all look identical: nothing happens. Check both `python3 scripts/agentm_config.py --get plugins.autonomy.email_to` and `--get plugins.autonomy.email_smtp_url` first.
- **Only one of the two keys is set.** `email_config()` returns `None` unless both are present and non-empty (`session_email.py:70-74`) — a recipient with no relay, or a relay with no recipient, is treated as fully unconfigured, not a partial state worth sending from.
- **SMTP connection errors.** `_send_smtp()` catches `smtplib.SMTPException`, `OSError`, and `ValueError` (an unparseable URL) and returns `False` rather than raising (`session_email.py:144-145`) — the runner cycle this rides is never blocked by a mail-relay outage.

## See also

- [Autonomy — Delivery](agentm-autonomy#delivery--getting-it-in-front-of-you) — the design this channel implements, and the amendment log entry with the full build detail.
- [Installer CLI](Installer-CLI) — the `--notify-enabled` / `--email-to` / `--email-smtp-url` config-key reference row.
- [Enable on-device notifications](Enable-On-Device-Notifications) — the sibling opt-in delivery channel, same plan.
- [Persist a morning report](Persist-A-Morning-Report) — the sibling `scripts/health/` recipe for the overnight-run report this same digest ladder feeds.
