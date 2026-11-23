"""Tests for the weekly summary email (agentm task 200).

Everything runs against a scratch vault and a scratch engine dir. The daemon,
`gh`, the homelab fetch and the relay are stubs: nothing here sends mail or
touches the network.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent / "harness" / "skills" / "memory" / "scripts"
for p in (_SKILL, _HERE / "health", _HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import runner_pulse  # noqa: E402
import session_email  # noqa: E402
import weekly_email as w  # noqa: E402
import weekly_render as r  # noqa: E402

# Sunday 8 November 2026, noon UTC: a Sunday in every time zone a runner uses.
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc).timestamp()
BASE = "https://git.example/vault/src/branch/main/"

NAS_REPORT = """# NAS weekly report · week ending Sun 8 Nov 2026

**Needs a look.** Backup finished overnight.

| Status | Area | Box | Reading |
|---|---|---|---|
| LOOK | [Cooling](#cooling) | Main NAS | CPU at 84°C |
| OK | [Disks](#disks) | Main NAS | all OK |
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _tracker(root: Path, project: str, task: str, *, status: str, updated: str = "2026-11-04",
             closed: str = "", title: str = "", nxt: str = "") -> Path:
    return _write(root / "projects" / project / "tasks" / task / "tracker.md",
                  f"---\nkind: tracker\ntitle: {title or task}\nproject: {project}\nstatus: {status}\n"
                  f"updated: {updated}\nclosed: {closed}\n---\n\n## State\n\nx\n\n## Next\n\n{nxt}\n\n## Outcome\n")


class WeeklyFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="weekly-email-"))
        self.root = self.tmp / "vault"
        self.mem = self.root / "agent"
        self.engine = self.tmp / "engine"
        self.runner = self.tmp / "runner"
        for d in (self.mem, self.engine, self.runner):
            d.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # Two scorecards: last week's recall 42, this week's 35; 41 writes on a budget of 30.
        _write(self.mem / "diagnostics/health/2026-11-01-health-scorecard.md",
               "| gold-set R@5 | 0.7 | 42 of 60 scored |\n")
        _write(self.mem / "diagnostics/health/2026-11-08-health-scorecard.md",
               "| gold-set R@5 | 0.58 | 35 of 60 scored |\n"
               "| writes, the last 24 hours | 41 | 41 new card(s) · budget 30 · vault +2 |\n")
        _write(self.mem / "diagnostics/migrations/purge/retention-first-deletion.md",
               "---\ntitle: Retention\napproved: false\n---\n")
        _tracker(self.root, "pixelton", "014-art", status="active", title="Build the early build",
                 nxt="Two build questions wait on the operator.")
        _tracker(self.root, "doom-3d", "005-stage-4", status="done", closed="2026-11-05", title="Stage four")
        _tracker(self.root, "doom-3d", "006-voxel", status="active", title="Voxel actors", nxt="Blocked on the rig.")
        _tracker(self.root, "home", "001-move", status="active", title="Trade up to Springfield",
                 nxt="The estimate came in at $1,250,000 for 12 Elm Street.")
        _write(self.root / "resources/briefs/2026-11-07-field-brief.md",
               "### 1. [Thinking harder beat the harnesses](https://example.com/a)\n\nx\n\n"
               "### 2. [An always-on agent](https://example.com/b)\n")
        _write(self.root / "personal/ideas/self-host.md",
               "---\ntitle: Self-host the agents\ncreated: 2026-11-03\nimportance: 8\n---\n")
        # Enrichment ran six nights of seven; Wednesday's run failed on the login.
        runs = []
        for day in (2, 3, 5, 6, 7, 8):
            runs.append({"at": f"2026-11-0{day}T10:00:00Z", "total_cost_usd": 2.5, "errors": []})
        runs.append({"at": "2026-11-04T10:00:00Z", "total_cost_usd": 0,
                     "errors": ["OAuth session expired and could not be refreshed"]})
        _write(self.engine / "enrich-runs.jsonl", "\n".join(json.dumps(x) for x in runs) + "\n")
        w.log_urgent(self.engine, ["backup-stale"], source="daemon",
                     at=datetime(2026, 11, 5, 15, 12, tzinfo=timezone.utc).timestamp())
        w.log_urgent(self.engine, ["backup-stale"], source="daemon",           # last week: not shown
                     at=datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc).timestamp())
        self.config = {
            "link_base": BASE, "github_owner": "someone",
            "project_cards": [{"project": "home", "title": "House move", "icon": "🏠", "private": True}],
            "homelab": {"title": "Homelab", "name": "NAS", "report_url": "https://nas.example/{year}/{date}.md",
                        "link": "https://nas.example/view/{date}"},
        }
        self.gh_calls = []
        patches = [
            mock.patch.object(w.engine_state, "engine_state_dir", return_value=self.engine),
            mock.patch.object(w.runner_pulse, "grade", return_value=runner_pulse.Pulse("OK", "on time")),
            mock.patch.object(w.morning_note, "vault_offenders", return_value=[]),
            mock.patch.object(w.needs_review, "collect", return_value=[1, 2, 3]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def gh(self, args):
        self.gh_calls.append(args)
        if args[:2] == ["repo", "list"]:
            return [{"name": "alpha", "isArchived": False, "pushedAt": "2026-11-07T00:00:00Z",
                     "defaultBranchRef": {"name": "main"}},
                    {"name": "old", "isArchived": False, "pushedAt": "2026-02-11T00:00:00Z",
                     "defaultBranchRef": {"name": "master"}}]
        if args[:2] == ["run", "list"]:
            if "someone/alpha" in args:
                return [{"workflowName": "CI", "conclusion": "failure", "createdAt": "2026-11-07T00:00:00Z"},
                        {"workflowName": "CI", "conclusion": "success", "createdAt": "2026-11-06T00:00:00Z"}]
            return []
        if args[0] == "api" and "dependabot" in args[1]:
            if "alpha" in args[1]:
                return []
            raise RuntimeError("Dependabot alerts are disabled for this repository.")
        if args[0] == "api":
            return []
        if args[:2] == ["pr", "list"]:
            return [{"number": 4, "createdAt": "2026-07-21T00:00:00Z"}] if "someone/old" in args else []
        if args[:2] == ["search", "prs"]:
            return [{"repository": {"name": "doom-3d"}}] * 3
        raise AssertionError(args)

    def sources(self, config=None, ask=None) -> w.Sources:
        cfg = self.config if config is None else config
        return w.Sources(vault=self.mem, vault_root=self.root, engine_dir=self.engine, runner_dir=self.runner,
                         link=w.Links(cfg.get("link_base")), config=cfg,
                         ask=ask or (lambda a: {"health": {"level": "ok", "alerts": []}}), gh=self.gh)

    def week(self, **kw) -> w.Week:
        return w.collect(self.sources(**kw), now=NOW, fetch=lambda url, tok: NAS_REPORT)

    def card(self, week: w.Week, slug: str) -> w.Card:
        return next(c for c in week.cards if c.slug == slug)


class TheWeek(WeeklyFixture):
    def test_the_week_is_monday_to_sunday(self):
        start, end = w.week_of(datetime(2026, 11, 8, 12, tzinfo=timezone.utc))
        self.assertEqual((start.strftime("%a %d"), end.strftime("%a %d")), ("Mon 02", "Sun 08"))
        start, end = w.week_of(datetime(2026, 11, 10, 12, tzinfo=timezone.utc))   # a Tuesday catch-up
        self.assertEqual(end.strftime("%a %d"), "Sun 08")

    def test_needs_you_lists_the_decisions_only_the_operator_can_make(self):
        card = self.card(self.week(), "needs-you")
        texts = [i.text for i in card.items]
        self.assertIn("Approve retention purge", texts)
        self.assertTrue(any(t.startswith("pixelton") for t in texts), texts)
        self.assertEqual(card.footer[0], "3 notes to review")
        self.assertFalse(card.hidden)

    def test_agent_health_reads_the_scorecards_from_their_history(self):
        # Task 201: the scorecard keeps one page. The two weeks the card
        # compares are rows in its history, and both links go to a page.
        health = self.mem / "diagnostics/health"
        for note in health.glob("*-health-scorecard.md"):
            w.corpus_scorecard.report_history.record(
                health, w.corpus_scorecard.HISTORY_REPORT, note.name[:10],
                {"body": note.read_text(encoding="utf-8")})
            note.unlink()
        _write(health / "latest_health_scorecard.md", "the page\n")
        _write(self.mem / "diagnostics/morning/latest_morning_note.md", "the page\n")
        card = self.card(self.week(), "agent-health")
        self.assertEqual([m.label for m in card.meters], ["Recall 35/60", "Writes 41 vs budget 30"])
        self.assertEqual(card.meters[0].mark, 42)
        self.assertEqual(card.meters[0].status, w.LOOK)
        self.assertTrue(card.meters[0].link.endswith("agent/diagnostics/health/latest_health_scorecard.md"),
                        card.meters[0].link)
        self.assertTrue(card.meters[1].link.endswith("agent/diagnostics/morning/latest_morning_note.md"),
                        card.meters[1].link)

    def test_agent_health_reads_the_meters_the_strip_and_the_urgent_log(self):
        card = self.card(self.week(), "agent-health")
        self.assertEqual([m.label for m in card.meters], ["Recall 35/60", "Writes 41 vs budget 30"])
        self.assertEqual(card.meters[0].mark, 42)
        self.assertEqual(card.meters[0].status, w.LOOK)
        # Mon–Sun: Wednesday's run failed on the login, so it did not run.
        self.assertEqual(card.strip, ["ok", "ok", "act", "ok", "ok", "ok", "ok"])
        backup = next(t for t in card.tiles if t.name == "Backup")
        self.assertTrue(backup.emailed and backup.emailed.startswith("Emailed you Thu 5 Nov"), backup.emailed)
        self.assertEqual(card.status, w.LOOK)
        self.assertEqual(card.footer[0], "$15 this week")

    def test_a_lapsed_login_is_red_and_tops_needs_you(self):
        runs = self.engine / "enrich-runs.jsonl"
        runs.write_text(runs.read_text() + json.dumps(
            {"at": "2026-11-08T10:30:00Z", "errors": ["Not logged in · Please run /login"]}) + "\n")
        week = self.week()
        health = self.card(week, "agent-health")
        self.assertEqual(next(t for t in health.tiles if t.name == "Login").status, w.ACT)
        self.assertEqual(self.card(week, "needs-you").items[0].text, "Sign in: run claude, then /login")
        self.assertEqual(week.status, w.ACT)

    def test_a_daemon_that_does_not_answer_is_red(self):
        def down(_args):
            raise RuntimeError("connection refused")
        card = self.card(self.week(ask=down), "agent-health")
        self.assertEqual(next(t for t in card.tiles if t.name == "Daemon").status, w.ACT)

    def test_github_counts_red_main_alerts_off_aging_prs_and_stale_repos(self):
        card = self.card(self.week(), "github")
        got = {n.label: n.value for n in card.numbers}
        self.assertEqual(got, {"red on main": "1", "alerts on": "1 of 2", "PRs aging": "1", "repos stale": "1"})
        self.assertEqual(card.status, w.LOOK)

    def test_a_high_security_alert_is_red(self):
        gh = self.gh

        def high(args):
            if args[0] == "api" and "dependabot" in args[1] and "alpha" in args[1]:
                return [{"security_advisory": {"severity": "high"}}]
            return gh(args)
        self.gh = high
        card = self.card(self.week(), "github")
        self.assertEqual(card.status, w.ACT)
        self.assertEqual(card.numbers[0].label, "high alert")

    def test_a_private_project_card_carries_no_words_from_its_notes(self):
        week = self.week()
        card = self.card(week, "house-move")
        blob = r.html(week) + r.text(week) + r.markdown(week)
        for leaked in ("Springfield", "1,250,000", "Elm"):
            self.assertNotIn(leaked, blob)
        self.assertEqual([i.text for i in card.items], ["Moved Wed 4 Nov"])

    def test_projects_in_motion_names_shipped_next_and_blocked(self):
        card = self.card(self.week(), "projects-in-motion")
        texts = [i.text for i in card.items]
        self.assertTrue(texts[0].startswith("doom-3d ■"), texts)       # blocked rows first
        self.assertFalse(any(t.startswith("home") for t in texts))     # it has its own card

    def test_research_lists_the_briefs_stories(self):
        card = self.card(self.week(), "agent-and-memory-research")
        self.assertEqual(card.chip, "2 stories")
        self.assertEqual(card.status, w.OK)

    def test_the_homelab_line_quotes_the_nas_verdict(self):
        card = self.card(self.week(), "homelab")
        self.assertTrue(card.line)
        self.assertEqual(card.items[0].text, "NAS · Needs a look · cooling")
        self.assertEqual(card.items[0].link, "https://nas.example/view/2026-11-08")

    def test_ideas_show_new_ideas_and_one_worth_a_look(self):
        card = self.card(self.week(), "ideas")
        self.assertFalse(card.hidden)
        self.assertEqual(card.chip, "1 new")

    def test_a_card_whose_source_breaks_says_so_and_the_rest_still_build(self):
        with mock.patch.object(w, "research", side_effect=ValueError("bad")):
            week = self.week()
        card = next(c for c in week.cards if c.title == "Agent and memory research")
        self.assertEqual(card.status, w.LOOK)
        self.assertIn("could not read", card.items[0].text)
        self.assertGreater(len(week.cards), 5)

    def test_without_config_there_are_no_links_and_no_github(self):
        week = self.week(config={})
        self.assertFalse(any(c.slug == "github" for c in week.cards))
        self.assertIsNone(week.report_link)
        self.assertNotIn("href=\"http", r.html(week).replace('href="#', ""))


class TheEmail(WeeklyFixture):
    def test_green_cards_are_chips_and_the_rest_are_full_cards(self):
        week = self.week()
        page = r.html(week)
        self.assertIn("<!-- card:agent-health -->", page)
        self.assertNotIn("<!-- card:agent-and-memory-research -->", page)
        self.assertIn(f"{BASE}agent/diagnostics/weekly/2026-11-08.md#agent-and-memory-research", page)
        for banned in ("<script", "<img", "url(", "@import", "obsidian://"):
            self.assertNotIn(banned, page)
        self.assertLess(len(page.encode()), 60_000)

    def test_a_meter_over_its_budget_marks_the_budget(self):
        page = r.html(self.week())
        self.assertIn('width="73%" style="background:#10B981;border-right:2px solid #111827;', page)   # 30 of 41

    def test_an_all_green_week_hides_needs_you_and_says_so(self):
        week = self.week()
        for c in week.cards:
            if c.slug == "needs-you":
                c.hidden = True
            else:
                c.status = w.OK
        week.headline = w.headline(week)
        self.assertEqual(week.status, w.OK)
        self.assertEqual(w.subject(week), "AgentM weekly · All good")
        self.assertEqual(week.headline, "Everything checked out.")
        self.assertNotIn("<!-- card:needs-you -->", r.html(week))

    def test_subject_names_the_cards_that_need_a_look(self):
        subject = w.subject(self.week())
        self.assertTrue(subject.startswith("AgentM weekly · Needs a look · Needs you, Agent health, GitHub"), subject)

    def test_the_report_has_an_anchor_for_every_chip(self):
        week = self.week()
        md = r.markdown(week)
        for c in week.cards:
            if not c.hidden:
                self.assertIn(f'<a id="{c.slug}"></a>', md)
        self.assertTrue(md.startswith("---\ntitle: AgentM weekly — 2026-11-08\nkind: report\n"))

    def test_the_text_copy_carries_every_link(self):
        txt = r.text(self.week())
        self.assertIn("[LOOK] Agent health", txt)
        self.assertIn("ALL GOOD:", txt)
        self.assertIn("Full report: " + BASE, txt)


class TheUrgentLane(WeeklyFixture):
    def test_a_lapse_emails_once_and_is_logged(self):
        runs = self.engine / "enrich-runs.jsonl"
        runs.write_text(json.dumps({"at": "2026-11-08T10:30:00Z", "errors": ["OAuth session expired"]}) + "\n")
        sent = []
        send = lambda s, b: sent.append((s, b)) or {"sent": True}  # noqa: E731
        ok = lambda a: {}  # noqa: E731
        w.run_urgent(self.mem, now=NOW, ask=ok, send=send)
        w.run_urgent(self.mem, now=NOW + 3600, ask=ok, send=send)
        self.assertEqual(len(sent), 1)
        self.assertIn("claude-login", sent[0][0])
        log = w.read_jsonl(self.engine / w.URGENT_LOG)
        self.assertEqual(log[-1]["codes"], ["claude-login"])

    def test_a_cleared_condition_can_page_again(self):
        runs = self.engine / "enrich-runs.jsonl"
        sent = []
        send = lambda s, b: sent.append(s) or {"sent": True}  # noqa: E731
        ok = lambda a: {}  # noqa: E731
        runs.write_text(json.dumps({"at": "2026-11-06T10:30:00Z", "errors": ["OAuth session expired"]}) + "\n")
        w.run_urgent(self.mem, now=NOW, ask=ok, send=send)
        runs.write_text(json.dumps({"at": "2026-11-07T10:30:00Z", "errors": []}) + "\n")
        w.run_urgent(self.mem, now=NOW, ask=ok, send=send)
        runs.write_text(json.dumps({"at": "2026-11-08T10:30:00Z", "errors": ["Not logged in"]}) + "\n")
        w.run_urgent(self.mem, now=NOW, ask=ok, send=send)
        self.assertEqual(len(sent), 2)

    def test_a_failed_send_is_tried_again_next_time(self):
        runs = self.engine / "enrich-runs.jsonl"
        runs.write_text(json.dumps({"at": "2026-11-08T10:30:00Z", "errors": ["OAuth session expired"]}) + "\n")
        outcomes = iter([{"sent": False}, {"sent": True}])
        sent = []
        send = lambda s, b: sent.append(s) or next(outcomes)  # noqa: E731
        w.run_urgent(self.mem, now=NOW, ask=lambda a: {}, send=send)
        w.run_urgent(self.mem, now=NOW, ask=lambda a: {}, send=send)
        self.assertEqual(len(sent), 2)
        self.assertEqual(len(w.read_jsonl(self.engine / w.URGENT_LOG)), 3)   # two from setUp, one now

    def test_the_alert_carries_its_evidence(self):
        runs = self.engine / "enrich-runs.jsonl"
        runs.write_text(json.dumps({"at": "2026-11-08T10:30:00Z", "model_calls": 7,
                                    "errors": ["x.md: enrich: claude exited exit status 1: Failed to authenticate: "
                                               "OAuth session expired and could not be refreshed"] * 7}) + "\n")
        sent = []
        w.run_urgent(self.mem, now=NOW, ask=lambda a: {}, send=lambda s, b: sent.append(b) or {"sent": True})
        self.assertIn("had 7 of its 7 call(s) refused", sent[0])


# The rejection the enrichment logged on 2026-11-22: the model answered, and the
# grounding gate turned its proposal down. "coauthor" holds the letters "oauth".
COAUTHOR = ("agent/memory/semantic/scope-agent-trailer-matching.md: enrich: post-gate grounding: enrich: note is "
            "not eligible: the proposal asserts what the card and its neighbours do not: `Records the fix for "
            "coauthor-guard.sh's AGENT_RE (the source does not say a fix was applied)`")


class TheLoginCheck(WeeklyFixture):
    """Task 198's check named the login lapsed on 2026-11-16 and 11-22, both
    nights every call reached the model."""

    def test_only_the_clis_own_words_are_a_lapse(self):
        lapse = w.claude_login.is_lapse
        for real in ("x.md: enrich: claude exited exit status 1: Failed to authenticate: OAuth session expired "
                     "and could not be refreshed", "Not logged in · Please run /login", "Invalid API key",
                     "OAuth session expired"):
            self.assertTrue(lapse(real), real)
        for false in (COAUTHOR,
                      "agent/memory/procedural/google-oauth-sign-in-for-the-blog.md: enrich: post-gate grounding: x",
                      "notes/login-flow.md: enrich: the proposal is empty",
                      "x.md: enrich: post-gate grounding: the card says `Failed to authenticate: OAuth session "
                      "expired` and the source does not"):
            self.assertFalse(lapse(false), false)

    def test_a_night_whose_note_mentions_oauth_stays_green(self):
        runs = self.engine / "enrich-runs.jsonl"
        runs.write_text(runs.read_text() + json.dumps(
            {"at": "2026-11-08T10:30:00Z", "model_calls": 90, "errors": [COAUTHOR]}) + "\n")
        week = self.week()
        health = self.card(week, "agent-health")
        self.assertEqual(next(t for t in health.tiles if t.name == "Login").status, w.OK)
        self.assertEqual(health.strip[-1], "ok")
        self.assertFalse(any("login" in i.text for i in self.card(week, "needs-you").items))
        sent = []
        w.run_urgent(self.mem, now=NOW, ask=lambda a: {}, send=lambda s, b: sent.append(s) or {"sent": True})
        self.assertEqual(sent, [])

    def test_the_login_items_open_the_morning_note_not_its_folder(self):
        runs = self.engine / "enrich-runs.jsonl"
        runs.write_text(runs.read_text() + json.dumps(
            {"at": "2026-11-08T10:30:00Z", "errors": ["Not logged in · Please run /login"] * 2}) + "\n")
        week = self.week()
        sign_in = self.card(week, "needs-you").items[0]
        lapsed = next(i for i in self.card(week, "agent-health").items if i.text.startswith("Claude login lapsed"))
        for it in (sign_in, lapsed):
            self.assertEqual(it.link, BASE + "agent/diagnostics/morning/latest_morning_note.md")
        self.assertIn("2 calls refused", lapsed.text)


class TheTruth(WeeklyFixture):
    def test_one_failing_note_says_fails_and_opens_that_note(self):
        rel = "projects/doom-3d/tasks/008-the-sound/tracker.md"
        with mock.patch.object(w.morning_note, "vault_offenders", return_value=[{"rel": rel, "finding": "x"}]):
            card = self.card(self.week(), "agent-health")
        it = next(i for i in card.items if "vault checks" in i.text)
        self.assertEqual((it.text, it.link), ("1 note fails the vault checks", BASE + rel))

    def test_one_red_card_needs_action(self):
        runs = self.engine / "enrich-runs.jsonl"
        runs.write_text(runs.read_text() + json.dumps(
            {"at": "2026-11-08T10:30:00Z", "errors": ["Not logged in"]}) + "\n")
        self.assertEqual(self.week().headline, "Agent health needs action.")

    def test_spend_counts_what_the_lesson_compile_spent(self):
        _write(self.engine / w.morning_note.lesson_compile.STATE_SUBDIR / "runs.jsonl",
               json.dumps({"at": "2026-11-07T10:00:00Z", "spent": 2.0, "projects": []}) + "\n")
        self.assertEqual(self.card(self.week(), "agent-health").footer[0], "$17 this week")

    def test_the_writes_meter_says_it_is_one_night(self):
        meter = self.card(self.week(), "agent-health").meters[1]
        self.assertEqual(meter.mark_label, "night of Sun 8 Nov")

    def test_red_on_main_opens_the_failing_run(self):
        gh = self.gh
        url = "https://github.com/someone/alpha/actions/runs/7"

        def with_urls(args):
            out = gh(args)
            if args[:2] == ["run", "list"]:
                out = [dict(x, url=url) for x in out]
            return out
        self.gh = with_urls
        number = next(n for n in self.card(self.week(), "github").numbers if n.label == "red on main")
        self.assertEqual(number.link, url)

    def test_the_homelab_line_names_the_areas_that_need_action_first(self):
        report = NAS_REPORT.replace("**Needs a look.**", "**Needs action.**").replace(
            "| OK | [Disks](#disks)", "| ACT | [Last backup](#last-backup)").replace(
            "| LOOK | [Cooling](#cooling) | Main NAS | CPU at 84°C |\n",
            "| LOOK | [Disks](#disks) | Main NAS | 50°C |\n| LOOK | [Cooling](#cooling) | Main NAS | CPU at 84°C |\n")
        week = w.collect(self.sources(), now=NOW, fetch=lambda url, tok: report)
        self.assertEqual(self.card(week, "homelab").items[0].text, "NAS · Needs action · last backup, disks")


class ThePhoneLayout(WeeklyFixture):
    """At 320–375px, with the `<style>` block stripped as some Gmail apps do."""

    def page(self) -> str:
        return r.html(self.week())

    def test_no_row_of_tiles_or_numbers_is_more_than_three_wide(self):
        import re
        rows = re.findall(r"<tr>((?:(?!<tr>|</tr>).)*)</tr>", self.page(), re.DOTALL)
        widest = max(row.count('class="surface-tile"') for row in rows)
        self.assertLessEqual(widest, 3)

    def test_nothing_that_wraps_is_shaped_as_a_pill(self):
        page = self.page()
        self.assertNotIn('class="surface-capsule" style="background:#FFFFFF;border:1px solid #DCE0D9;border-radius:999px',
                         page)
        self.assertNotIn("border-radius:999px;padding:1px 6px", page)      # the "Emailed you" badge

    def test_there_are_no_in_message_anchor_links(self):
        self.assertNotIn('href="#', self.page())

    def test_the_strip_names_each_night(self):
        page = self.page()
        self.assertEqual(page.count(">S</td>"), 2)
        self.assertEqual(page.count(">T</td>"), 2)

    def test_long_tokens_wrap(self):
        week = self.week()
        self.card(week, "needs-you").items[0].text = "x" * 200
        self.assertIn("overflow-wrap:anywhere;", r.html(week))


def _sunday_local(hour: int, minute: int = 0, day: int = 8) -> float:
    """A local wall-clock time on the fixture's week (Sunday 8 Nov 2026)."""
    return datetime(2026, 11, day, hour, minute).astimezone().timestamp()


class TheHomelabWait(WeeklyFixture):
    """The NAS sends its report at 07:00, or up to noon when its backup box is
    late; on 2026-11-22 it landed at 12:00 and the 07:07 email said "not in
    yet". The email now waits for it until `homelab.wait_until`."""

    def at(self, now: float, report=None, config=None) -> bool:
        src = self.sources(config=config)
        week = w.collect(src, now=now, fetch=lambda url, tok: report)
        return w.homelab_pending(src, week, now)

    def test_a_missing_report_holds_the_email_on_sunday_morning(self):
        self.assertTrue(self.at(_sunday_local(7, 7)))
        self.assertTrue(self.at(_sunday_local(12, 29)))

    def test_it_sends_without_the_report_once_the_wait_runs_out(self):
        self.assertFalse(self.at(_sunday_local(12, 30)))

    def test_a_catch_up_run_on_a_later_day_does_not_wait(self):
        self.assertFalse(self.at(_sunday_local(8, 0, day=9)))

    def test_a_report_that_is_in_does_not_wait(self):
        self.assertFalse(self.at(_sunday_local(7, 7), report=NAS_REPORT))

    def test_the_wait_is_configurable_and_off_without_a_homelab(self):
        cfg = dict(self.config, homelab=dict(self.config["homelab"], wait_until="09:00"))
        self.assertFalse(self.at(_sunday_local(9, 0), config=cfg))
        self.assertTrue(self.at(_sunday_local(8, 59), config=cfg))
        self.assertFalse(self.at(_sunday_local(7, 7), config={k: v for k, v in self.config.items() if k != "homelab"}))

    def test_a_held_run_writes_nothing_and_sends_nothing(self):
        real = w.collect
        with mock.patch.object(w, "load_config", return_value=self.config), \
                mock.patch.object(w, "build_sources", return_value=self.sources()), \
                mock.patch.object(w, "collect", lambda src, now: real(src, now=now, fetch=lambda u, t: None)), \
                mock.patch.object(w, "_session_email", side_effect=AssertionError("sent")), \
                mock.patch.object(w, "push_vault", side_effect=AssertionError("pushed")):
            code = w.run_weekly(self.mem, now=_sunday_local(7, 7))
        self.assertEqual(code, w.NOT_READY)
        self.assertFalse((self.mem / w.WEEKLY_DIR).exists())


class ThePush(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="weekly-push-"))
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        self.git = lambda *a: __import__("subprocess").run(
            ["git", "-C", str(self.repo), "-c", "user.email=t@example.com", "-c", "user.name=t", *a],
            check=True, capture_output=True, env={**__import__("os").environ, **env})
        self.git("init", "-q")
        _write(self.repo / "a.md", "x\n")
        self.git("add", "a.md")
        self.git("commit", "-qm", "a")

    def test_a_committed_report_is_ready_at_once(self):
        self.assertTrue(w.wait_for_commit(self.repo, "a.md", timeout=0, sleep=lambda s: None))

    def test_an_uncommitted_report_waits_then_gives_up(self):
        _write(self.repo / "r.md", "report\n")
        ticks = iter(range(0, 1000, 10))
        slept = []
        ok = w.wait_for_commit(self.repo, "r.md", timeout=30, clock=lambda: next(ticks), sleep=slept.append)
        self.assertFalse(ok)
        self.assertEqual(len(slept), 2)   # checks at 10 and 20 sleep; the one at 30 gives up

    def test_a_report_committed_while_waiting_is_seen(self):
        _write(self.repo / "r.md", "report\n")

        def commit(_s):
            self.git("add", "r.md")
            self.git("commit", "-qm", "r")
        self.assertTrue(w.wait_for_commit(self.repo, "r.md", timeout=60, sleep=commit))


class TheMailPath(unittest.TestCase):
    def test_html_rides_as_the_alternative_to_the_text(self):
        with mock.patch("smtplib.SMTP") as smtp_cls:
            server = smtp_cls.return_value.__enter__.return_value
            ok, _ = session_email._deliver("smtp://relay.example:587", "me@example.com", "Subj", "plain body",
                                           html="<p>rich</p>", sender_name="AgentM weekly")
        self.assertTrue(ok)
        msg = server.send_message.call_args[0][0]
        self.assertEqual(msg.get_content_type(), "multipart/alternative")
        self.assertEqual([p.get_content_type() for p in msg.iter_parts()], ["text/plain", "text/html"])
        self.assertEqual(msg["From"], "AgentM weekly <me@example.com>")

    def test_without_html_the_message_stays_plain(self):
        with mock.patch("smtplib.SMTP") as smtp_cls:
            server = smtp_cls.return_value.__enter__.return_value
            session_email._deliver("smtp://relay.example:587", "me@example.com", "Subj", "plain body")
        msg = server.send_message.call_args[0][0]
        self.assertEqual(msg.get_content_type(), "text/plain")
        self.assertEqual(msg["From"], "me@example.com")


if __name__ == "__main__":
    unittest.main()
