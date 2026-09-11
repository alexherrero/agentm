# How to enable the daily email

> [!NOTE]
> **Status: implemented** — shipped by `PLAN-proactive-delivery.md#task-4` (FRIDAY ladder feature 1, task 4 of 5); the runner-job manifest referenced in step 6 shipped in task 5 of the same plan.
> **Goal:** Opt in to a daily email that carries the morning note, so you can read what the night did away from your machine.
> **Prereqs:** You need a first-party SMTP relay or on-device mail agent you control. This channel never communicates with third-party push services. You also need a resolvable vault with the `morning-note` job registered (see [Read the morning note and the nightly scorecard](Read-The-Nightly-Scorecards)); without a recent note, the email sends the newest digest instead.

`scripts/health/session_email.py` is the daily email delivery channel. It implements the design described in [Delivery](agentm-autonomy#delivery--getting-it-in-front-of-you). The email body is the morning note. This is the same page whose first section the session-start line shows.

## Steps

Configure and run the daily email by following these steps:

1. **Set both config keys.** The channel requires both keys to function. If you configure only one key, the channel silently disables itself:

   ```bash
   python3 scripts/agentm_config.py --email-to you@example.com
   python3 scripts/agentm_config.py --email-smtp-url smtp://relay@localhost:587
   ```

   These write `plugins.autonomy.email_to` and `plugins.autonomy.email_smtp_url` in your configuration. Confirm the settings by running `--get plugins.autonomy.email_to` or `--get plugins.autonomy.email_smtp_url`. The `smtp://` URL uses the form `smtp://[user[:password]@]host[:port]`, and the port defaults to `25`.

   A password authenticates against the relay and turns on TLS automatically: port `465` uses implicit TLS (`SMTP_SSL`), any other port attempts opportunistic `STARTTLS` (silently skipped if the relay doesn't offer it). This is what lets you point the channel at an authenticated transactional-email relay — Resend and similar services — instead of only a bare local relay. With no password, the send is unauthenticated and unencrypted, matching a local/on-device relay that needs neither.

   If your relay requires a domain-verified sending address distinct from the SMTP auth username, set it separately:

   ```bash
   python3 scripts/agentm_config.py --email-from digest@example.com
   ```

   This is optional — omit it and the `From` header falls back to `--email-to` (mail-to-self), which is fine for a relay with no verified-sender requirement.

2. **Run the emailer.** Until you register the runner job, invoke the script directly:

   ```bash
   python3 scripts/health/session_email.py
   ```

   The script verifies both config keys, resolves your vault, and exits silently if any requirement is missing.

3. **Understand the message content.** When this morning's or yesterday's morning note is on disk, the email is that note with its frontmatter removed, under the subject `AgentM morning — <headline>`. A note older than yesterday is never sent. Without a recent note, the email carries the newest delivered digest, stale or not; staleness warnings stay with the session-start line. The script builds the message using the Python standard library and sends it directly to your configured SMTP host.

4. **Observe the daily limit.** The script tracks delivery in `~/.cache/agentm/telemetry/email-state.json`. It will only send one email per calendar day. Subsequent invocations on the same day do not trigger another email. If the SMTP send fails, the script does not record the attempt, and the next run will retry.

5. **Disable the channel.** Unset either configuration key to stop email delivery:

   ```bash
   python3 scripts/agentm_config.py --unset plugins.autonomy.email_to
   ```

6. **Automate delivery with the runner.** Copy both job templates to your local harness directory:

   ```bash
   cp templates/jobs/morning-note.yaml .harness/jobs/morning-note.yaml
   cp templates/jobs/observability-email-daily.yaml .harness/jobs/observability-email-daily.yaml
   ```

   Both run inside the night's `02:00-06:00` window: the note at order 5, the email at order 6, so the email carries the note the night just wrote. The runner will not send any emails until you set both configuration keys.

## Verify

Run these tests in `scripts/health/test_session_email.py` to confirm the behavior of the email channel:

- `test_unconfigured_never_sends` and `test_configured_sends` verify that the channel requires both configuration keys.
- `test_same_day_rerun_does_not_resend` and `test_new_day_resends` verify the calendar-day delivery limit.
- `test_smtp_failure_does_not_record_sent` verifies that a failed send does not consume the attempt.
- `test_the_morning_note_is_the_body` verifies that the email sends the morning note, frontmatter removed, under the `AgentM morning — <headline>` subject.
- `test_a_stale_morning_note_is_not_resent_as_news` verifies that a note older than yesterday gives way to the newest digest.
- `test_subject_and_body_from_latest_digest` verifies the digest fallback: with no morning note, the email body matches the latest digest.
- `test_login_called_when_password_present`, `test_starttls_attempted_on_non_465_port`, `test_ssl_used_on_port_465_no_starttls`, and `test_false_on_auth_failure` verify the authenticated-relay path: login is attempted when the URL carries a password, TLS is negotiated per port, and a rejected login returns `False` rather than raising.
- `test_from_addr_used_as_sender_when_given` and `test_from_addr_falls_back_to_to_addr_when_absent` verify the `--email-from` precedence.

## Troubleshooting

Refer to these solutions when troubleshooting email delivery:

- **Nothing sends and no error appears.** By design, the script swallows exceptions and exits silently when keys are missing, the vault is unresolved, or SMTP fails. Confirm both config keys are set using the `agentm_config.py --get` commands.
- **Only one of the two keys is set.** The script requires both recipient and SMTP relay configurations. It treats a single configured key as unconfigured and does not send.
- **SMTP connection errors.** The script catches SMTP connection exceptions and returns `False` instead of raising an error. This ensures a mail-relay outage does not block your runner execution.
- **Authenticated relay rejects the login.** A bad username or password in the `smtp://` URL surfaces the same way as any other SMTP failure — the script returns `False` and does not record a sent attempt, so the next run retries. Re-check the credentials with `--get plugins.autonomy.email_smtp_url`.

## See also

Refer to these related topics for more details:

- [Autonomy — Delivery](agentm-autonomy#delivery--getting-it-in-front-of-you) — the design this channel implements, and the amendment log entry with the full build detail.
- [Read the morning note and the nightly scorecard](Read-The-Nightly-Scorecards) — what the email carries, section by section.
- [Memory daemon reference § the morning note](Memory-Daemon#the-morning-note) — the note's sections, its schedule, and every surface that reads it.
- [Installer CLI](Installer-CLI) — the `--notify-enabled` / `--email-to` / `--email-smtp-url` / `--email-from` config-key reference row.
- [Enable on-device notifications](Enable-On-Device-Notifications) — the sibling opt-in delivery channel, same plan.
- [Persist a morning report](Persist-A-Morning-Report) — the sibling `scripts/health/` recipe for the overnight-run report the observability rollup feeds, a different page from the morning note.
