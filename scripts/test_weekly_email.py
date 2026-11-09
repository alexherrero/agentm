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
