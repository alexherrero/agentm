# How to read the weekly field brief

> [!NOTE]
> **Status: implemented** — shipped by `tasks/185-keep-up-with-the-field` (steps 3 and 4): the engine, `scripts/health/session_email.py`'s `send()`, and the weekly job template. The first brief was run by hand on Saturday 2026-10-03, and the weekly job runs Saturday evenings from 2026-10-10.
> **Goal:** Read the week's field brief, steer what it looks for, ask it a question of your own, and keep the items worth keeping.
> **Prereqs:** The agentm runner on the machine that holds your vault, with the weekly job registered (step 7). For the emailed copy, the mail path from [Enable the daily email](Enable-Email-Digest-Delivery).

Once a week, one note lands in your vault, and in your inbox if you set up a mail path. It holds at most ten links to what is new in agent harnesses, memory, automation and skills. Each item gives the link, two sentences on what it is, and one on why it matters to the work in flight. The brief ranks the items against the roadmap's *What remains* and the open designs. It replaces forward learning and its watchlist, which the same task retires.

## Steps

1. **Open the week's note.** It is `<vault>/resources/briefs/<YYYY-MM-DD>-field-brief.md`, with `kind: brief`. A short header names the week, the question asked, the sources consulted and the cost. Each item below it is a heading with the link, what it is, why it matters here, and a line naming the source that surfaced it. Every item ends with a `- [ ] keep` box.

2. **Or read it in your inbox.** The same text goes out through the daily email's mail path, under the subject `Field brief — week of <date>`. The email has no frontmatter and no keep boxes, since the boxes only work in the note, and ends with a line saying how to keep an item. With no mail path configured, the send is skipped and the skip is logged; the note is written either way. If a configured send fails, the note is kept, the run exits non-zero, and running the brief again sends it without another model run. [Enable the daily email](Enable-Email-Digest-Delivery) sets the path up.

3. **Tell it what you care about.** Edit `<vault>/projects/agentm/desk/field-brief.md`. The file lists topics, favoured sources and ignored sources in plain markdown, and every run reads it.

4. **Expect each link once.** The brief logs every URL it shows to a seen-list, `~/.local/state/agentm/field-brief/seen.jsonl`, in the engine state directory outside the vault. A URL on that list stays out of the brief for 90 days. A follow-up with new substance, such as a release or a later paper, has its own URL and passes.

5. **Ask a question between weeks.** The same engine answers any question you give it:

   ```text
   /memory field-brief --ask "what's new in agent memory this month?"
   ```

   It reads the seen-list, so it repeats nothing the weekly note already showed, then prints the brief and writes neither the note nor the seen-list, so nothing it shows counts as seen. Add `--deep` to route the run to the strong tier for an occasional monthly pass. The weekly run uses Sonnet.

6. **Keep what is worth keeping.** Items you do not keep expire with the note. To keep one, run:

   ```text
   /memory field-brief keep <note> <item> --why "<why it matters>"
   ```

   The item becomes a reference card through the capture door, with your reason attached, and its `- [ ] keep` box is ticked. Running `keep` on the same item again does not capture it a second time.

   `<note>` is the note's date (`2026-10-04`), its filename, or its full path. `<item>` is the number in the item's heading (`### 3. …` is item 3). `--why` is required: the card carries your reason, and a card the brief offered without one would be the watchlist again.

7. **Register the weekly job.** Copy the template into your local jobs directory once:

   ```bash
   cp templates/jobs/field-brief-weekly.yaml .harness/jobs/field-brief-weekly.yaml
   ```

   The job runs weekly in an `18:00-23:00` window, under a token budget, with a two-day lookback. The runner has no weekday key: the first run sets the day, so register the job on the day you want it to run, because one registered on a Tuesday afternoon runs that evening. If you have already run the brief by hand, record that run as the job's last run when you register it, or the runner writes a second brief that evening. This machine's first brief was run by hand on Saturday 2026-10-03, so its job runs on Saturday evenings. A week the machine sleeps through catches up within two days. The runner runs the scripts in the checkout it lives in, so that checkout must be at a commit that has `harness/skills/memory/scripts/field_brief.py`. `.harness/jobs/` is local to your machine. After this copy, edit the live file in place, because a second copy overwrites it.

## Verify

Check the setup:

- `python3 scripts/machinery_doctor.py` reports whether the weekly job is registered.
- After the first scheduled evening, the note exists under `<vault>/resources/briefs/`, its frontmatter carries `cost_usd`, and the email has arrived.
- `python3 harness/skills/memory/scripts/field_brief.py --no-mail` writes the week's note without the email, for a dry run.

## See also

- [Enable the daily email](Enable-Email-Digest-Delivery) — the mail path the emailed copy rides.
- [Read the morning note and the nightly scorecard](Read-The-Nightly-Scorecards) — the nightly report, read from the same vault.
- [Installer CLI](Installer-CLI) — the `--email-to`, `--email-smtp-url` and `--email-from` config keys.
- [Experience and dreaming](agentm-experience-and-dreaming) — the design that governs the brief (§ Forward experience).
