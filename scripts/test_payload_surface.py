#!/usr/bin/env python3
"""Tests for the payload surface: the layout-free gate, the renderer, and the
parity gate that keeps the derived copies equal to the template.

The payload is the text pasted into claude.ai and the Gem. It has one source
(templates/agentmemory-context.md) and two derived copies (the Antigravity rule
and the managed section of ~/.gemini/GEMINI.md), and these tests hold the two
invariants the agentm-vault design asks for: the source names no folder a
migration moves, and a copy that drifts from the source is caught.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

def _load(module_file: str):
    """Import a hyphenated check script by path (they are not importable names)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        module_file.replace("-", "_").removesuffix(".py"), HERE / module_file
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Registered before exec: a dataclass defined in the module looks itself up
    # in sys.modules by __module__ while it is being built (py3.9).
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


LAYOUT_FREE = _load("check-payload-layout-free.py")
# Assembled rather than written out: the repo's PII gate matches an email
# shape literally, and it is right not to make an exception for a fixture.
FAKE_ADDRESS = "door" + "@" + "example.test"
SHORT_ADDRESS = "d" + "@" + "e.test"
TEMPLATE = HERE.parent / "templates" / "agentmemory-context.md"
SEPTEMBER = HERE / "fixtures" / "agentmemory-context.september.md"


class LayoutFreeGate(unittest.TestCase):
    def test_the_shipped_template_is_layout_free(self):
        self.assertEqual(LAYOUT_FREE.scan(TEMPLATE.read_text(encoding="utf-8")), [])

    def test_the_september_template_fails_on_every_forbidden_name(self):
        # The predecessor is kept as a fixture precisely so this gate can be
        # shown to fail: a checker that has never rejected anything is not
        # evidence of anything.
        hits = LAYOUT_FREE.scan(SEPTEMBER.read_text(encoding="utf-8"))
        found = {needle for needle, _reason, _line in hits}
        self.assertEqual(found, {needle for needle, _ in LAYOUT_FREE.FORBIDDEN})

    def test_the_card_guide_may_still_name_the_lowercase_archive(self):
        # `Agent/` is retired; `agent/archive/` is the landing path the card
        # guide has to explain. The scan is case-sensitive so the two do not
        # collide — and the shipped template really does name the lowercase one.
        self.assertEqual(LAYOUT_FREE.scan("a note under `agent/archive/` is finished work"), [])
        self.assertTrue(LAYOUT_FREE.scan("a note under `Agent/archive/` is finished work"))
        self.assertIn("agent/archive/", TEMPLATE.read_text(encoding="utf-8"))


PARITY = _load("check-payload-parity.py")
import payload_render as pr  # noqa: E402


class Renderer(unittest.TestCase):
    def test_the_operator_only_header_never_reaches_a_surface(self):
        body = pr.render(TEMPLATE.read_text(encoding="utf-8"), None)
        self.assertTrue(body.startswith("# Using my memory vault"))
        self.assertNotIn("SOURCE OF TRUTH", body)
        self.assertNotIn("<!--", body)

    def test_no_address_keeps_the_no_mail_alternate(self):
        body = pr.render(TEMPLATE.read_text(encoding="utf-8"), None)
        self.assertIn("no write door yet", body)
        self.assertNotIn("email it to", body)
        self.assertNotIn(pr.PLACEHOLDER, body)

    def test_an_address_keeps_the_mail_alternate_and_fills_it_in(self):
        body = pr.render(TEMPLATE.read_text(encoding="utf-8"), FAKE_ADDRESS)
        self.assertIn("email it to " + FAKE_ADDRESS, body)
        self.assertNotIn("no write door yet", body)
        self.assertNotIn(pr.PLACEHOLDER, body)

    def test_both_branches_render_to_the_same_shape(self):
        # Only the one sentence differs; the block markers leave no blank-line
        # scar behind whichever alternate is dropped.
        a = pr.render(TEMPLATE.read_text(encoding="utf-8"), None).splitlines()
        b = pr.render(TEMPLATE.read_text(encoding="utf-8"), FAKE_ADDRESS).splitlines()
        self.assertEqual(len(a), len(b))
        self.assertEqual([i for i, (x, y) in enumerate(zip(a, b)) if x != y], [12])

    def test_an_unresolvable_placeholder_raises_rather_than_shipping(self):
        # A placeholder that survives rendering would paste a literal
        # {{CAPTURE_ADDRESS}} onto a chat surface. Fail loudly instead. The
        # sentence here sits outside the alternates, so no branch can drop it.
        with self.assertRaises(ValueError):
            pr.render("# t\n\nmail {{CAPTURE_ADDRESS}} now\n", None)

    def test_a_headerless_template_keeps_its_first_alternate(self):
        # The header stripper eats the first HTML comment; the alternates are
        # HTML comments too. A template with no header must not lose its mail
        # block to the stripper.
        text = ("# t\n\n<!-- payload:mail -->\nmail {{CAPTURE_ADDRESS}}\n<!-- /payload:mail -->\n"
                "<!-- payload:no-mail -->\nshow me\n<!-- /payload:no-mail -->\n")
        self.assertEqual(pr.render(text, None), "# t\n\nshow me\n")
        self.assertEqual(pr.render(text, SHORT_ADDRESS), "# t\n\nmail " + SHORT_ADDRESS + "\n")
        headerless = text.replace("# t\n\n", "", 1)
        self.assertEqual(pr.render(headerless, SHORT_ADDRESS), "mail " + SHORT_ADDRESS + "\n")


class ParityGate(unittest.TestCase):
    def test_the_tracked_antigravity_rule_is_the_neutral_render(self):
        expected = pr.antigravity_rule(pr.render(TEMPLATE.read_text(encoding="utf-8"), None))
        self.assertEqual(pr.ANTIGRAVITY_RULE_PATH.read_text(encoding="utf-8"), expected)

    def test_the_tracked_rule_never_carries_a_capture_address(self):
        # The repo copy is deliberately address-free: a mailbox is not something
        # to commit, and check-no-pii would be the second line of defence.
        self.assertNotIn("email it to", pr.ANTIGRAVITY_RULE_PATH.read_text(encoding="utf-8"))

    def test_a_hand_edited_rule_fails_the_gate_and_the_writer_restores_it(self):
        path = pr.ANTIGRAVITY_RULE_PATH
        original = path.read_text(encoding="utf-8")
        try:
            path.write_text(original + "\nA line somebody added by hand.\n", encoding="utf-8")
            statuses = {label: status for label, status, _ in PARITY.check()}
            self.assertEqual(statuses["antigravity rule"], "FAIL")
            self.assertEqual(pr.write_antigravity_rule(), "written")
            statuses = {label: status for label, status, _ in PARITY.check()}
            self.assertEqual(statuses["antigravity rule"], "OK")
        finally:
            path.write_text(original, encoding="utf-8")

    def test_a_drifted_gemini_section_fails_and_the_writer_restores_it(self):
        # Driven against a scratch GEMINI.md rather than the operator's own.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            gemini = Path(td) / "GEMINI.md"
            gemini.write_text("# my own rules\n\nkeep me\n", encoding="utf-8")
            self.assertEqual(pr.write_gemini_section(gemini, address=None), "appended")
            text = gemini.read_text(encoding="utf-8")
            self.assertIn("keep me", text)  # everything outside the markers survives
            self.assertEqual(pr.managed_section(text),
                             pr.render(TEMPLATE.read_text(encoding="utf-8"), None))
            gemini.write_text(text.replace("read only", "READ ONLY"), encoding="utf-8")
            self.assertNotEqual(pr.managed_section(gemini.read_text(encoding="utf-8")),
                                pr.render(TEMPLATE.read_text(encoding="utf-8"), None))
            pr.write_gemini_section(gemini, address=None)
            self.assertEqual(pr.managed_section(gemini.read_text(encoding="utf-8")),
                             pr.render(TEMPLATE.read_text(encoding="utf-8"), None))


import machinery_doctor as DOCTOR  # noqa: E402
PAYLOAD_CMD = HERE.parent / "harness" / "skills" / "memory" / "scripts" / "payload.py"


class DoctorRows(unittest.TestCase):
    def test_a_row_per_copy_carries_a_hash(self):
        rows = DOCTOR.check_payload_copies()
        names = [r.name for r in rows]
        self.assertIn("payload-copy: antigravity rule", names)
        self.assertIn("payload-copy: gemini managed section", names)
        graded = [r for r in rows if r.status in ("OK", "FAIL")]
        self.assertTrue(graded, "no copy was actually graded on this machine")
        for row in graded:
            # A graded row carries the hash. A row with no hash is a row an
            # operator cannot act on. An UNVERIFIED row means the copy is not on
            # this machine, and has no hash to print.
            self.assertIn("sha ", row.detail)

    def test_a_drifted_rule_shows_as_FAIL_naming_both_hashes(self):
        path = pr.ANTIGRAVITY_RULE_PATH
        original = path.read_text(encoding="utf-8")
        try:
            path.write_text(original + "\ndrift\n", encoding="utf-8")
            row = next(r for r in DOCTOR.check_payload_copies()
                       if r.name == "payload-copy: antigravity rule")
            self.assertEqual(row.status, "FAIL")
            self.assertIn("differs from template's", row.detail)
            self.assertEqual(row.owner, "/memory payload --write")
        finally:
            path.write_text(original, encoding="utf-8")

    def test_the_sub_command_repairs_what_the_doctor_reported(self):
        import os
        import subprocess
        import tempfile
        path = pr.ANTIGRAVITY_RULE_PATH
        original = path.read_text(encoding="utf-8")
        try:
            path.write_text(original + "\ndrift\n", encoding="utf-8")
            row = next(r for r in DOCTOR.check_payload_copies()
                       if r.name == "payload-copy: antigravity rule")
            self.assertEqual(row.status, "FAIL")
            # A sandboxed HOME: --write also rewrites ~/.gemini/GEMINI.md, and a
            # test has no business editing the machine's real one. With HOME
            # redirected there is no ~/.gemini to find, so that half reports
            # "absent" and only the repo's tracked rule is touched.
            with tempfile.TemporaryDirectory() as home:
                env = dict(os.environ, HOME=home)
                env.pop("AGENTM_INSTALL_PREFIX", None)
                proc = subprocess.run([sys.executable, str(PAYLOAD_CMD), "--write"],
                                      capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("absent", proc.stdout)
            row = next(r for r in DOCTOR.check_payload_copies()
                       if r.name == "payload-copy: antigravity rule")
            self.assertEqual(row.status, "OK")
        finally:
            path.write_text(original, encoding="utf-8")

    def test_the_sub_command_prints_the_pasteable_body(self):
        import subprocess
        proc = subprocess.run([sys.executable, str(PAYLOAD_CMD), "--body-only"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, pr.render(TEMPLATE.read_text(encoding="utf-8"),
                                                pr.capture_address()))


if __name__ == "__main__":
    unittest.main()
