# How to enable the weekly email

> [!NOTE]
> **Status: implemented** — agentm task 200.
> **Goal:** Get one summary email every Sunday morning in place of the daily email, the field brief's email and the non-urgent alert emails.
> **Prereqs:** The mail path from [Enable the daily email](Enable-Email-Digest-Delivery) (steps 1 and 2: `email_to` and `email_smtp_url`), the runner, and `gh` signed in if you want the GitHub card.

`harness/skills/memory/scripts/weekly_email.py` builds the email from the week's records: the morning notes and scorecards, the run logs, project trackers, the latest field brief, GitHub, and any project's own feed. Each card carries a green, yellow or red pill. A green card shrinks to a chip; only cards that need a look are drawn in full. The full report is written to `diagnostics/weekly/<date>.md` under the memory root.

## Steps

1. **Add the weekly settings.** They live under one key, `plugins.autonomy.weekly`, in `~/.claude/.agentm-config.json`. Every field is optional:

   ```json
   "plugins.autonomy.weekly": {
     "link_base": "https://git.example/you/vault/src/branch/main/",
     "push_command": ["/path/to/your-vault-push"],
     "github_owner": "your-github-name",
     "project_cards": [
       {"project": "blog", "title": "Blog and readership", "icon": "✍️"},
       {"project": "home", "title": "House move", "icon": "🏠", "private": true}
     ],
     "exclude_projects": ["sandbox"],
     "homelab": {"title": "Homelab", "name": "NAS", "report_url": "https://…/{year}/{date}.md",
                 "link": "https://…/{date}", "token_command": ["security", "find-generic-password", "-s", "your-token", "-w"]}
   }
   ```

   - `link_base` is a web view of your vault's repository. Mail clients strip `obsidian://` links, so the email links there; without it the email has no links.
   - `push_command` runs before the send, so the report's page exists when you tap it.
   - A `private` project card shows only steps and dates, never the words in its notes.
   - `token_command` prints a token to stdout. The token goes straight into the request and is never stored.

2. **Register the two jobs.** Copy `templates/jobs/weekly-email.yaml` and `templates/jobs/urgent-lane.yaml` into `.harness/jobs/`. The weekly job runs on Sunday between 07:00 and 09:00; the urgent lane checks daily between 06:00 and 09:00.

3. **Turn the old emails off.** Set `enabled: false` in `.harness/jobs/observability-email-daily.yaml`, and add `--no-mail` to the command in `.harness/jobs/field-brief-weekly.yaml`. The morning note and the field brief keep being written; the weekly email reads them.

## Verify

```bash
python3 harness/skills/memory/scripts/weekly_email.py --no-mail --no-push
```

It prints where it wrote the report and the week's verdict. Open the report to see every card. Run without `--no-mail` to send one.

## A project's own card

A project's tooling can write `projects/<project>/weekly-feed.json` in the vault, and its card shows it alongside the project's trackers:

```json
{"generated": "2026-11-08T07:00:00Z", "status": "look", "chip": "212 added",
 "numbers": [{"value": "212", "label": "episodes added", "rel": "projects/media/progress.md"}],
 "items": [{"text": "2 rips to triage", "mark": "look", "rel": "projects/media/rips.md"}]}
```

A feed older than the week is ignored.

## The urgent lane

The daemon emails straight away only for a stopped runner, a backup not landing, a stale index, a failed self-probe or a stalled committer. The daily check emails for a lapsed `claude` login or a daemon that does not answer, once until it clears. Every urgent send is logged to `urgent-log.jsonl` in the engine state dir, and the weekly email shows "Emailed you …" on the matching tile.

## See also

- [Autonomy — Delivery](agentm-autonomy#delivery--getting-it-in-front-of-you) — the design, and its amendment log.
- [Enable the daily email](Enable-Email-Digest-Delivery) — the mail path the weekly email rides.
- [Memory daemon reference](Memory-Daemon) — the daemon's status and alert codes the agent-health card reads.
