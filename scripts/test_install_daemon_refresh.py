#!/usr/bin/env python3
"""Tests for install.sh's memory-daemon install/refresh decision.

The daemon is compiled from `daemon/`, so a harness refresh that does not rebuild
it leaves stale code resident with nothing saying so. `--daemon` installs the
launchd agent once; after that every install run refreshes it. This file pins
which of those two things happens, and — the part that matters — that a refresh
which cannot run says so instead of failing the project install or silently
skipping.

Driven as a subprocess against the real install.sh, with two seams so it never
touches the machine it runs on:

  HOME=<tmp>          the plist path, the binary path and the log dir all hang
                      off $HOME, so a fake HOME fully contains the effects.
  AGENTM_LAUNCHCTL    a stub script that records its arguments, standing in for
                      the real launchd. The reload sequence it drives broke in
                      production once, which is the argument for being able to
                      test it at all.

Run: python3 scripts/test_install_daemon_refresh.py
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_INSTALL = _REPO / "install.sh"

PLIST_REL = "Library/LaunchAgents/com.agentm.daemon.plist"


class DaemonRefreshBase(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.target = self.root / "project"
        for d in (self.home, self.target):
            d.mkdir(parents=True)

        # A launchctl that always succeeds and records what it was asked to do.
        self.calls = self.root / "launchctl-calls.txt"
        self.stub = self.root / "launchctl-stub"
        self.stub.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "$@" >> {self.calls}\n'
            # `print` must fail until a bootstrap has happened, or install.sh
            # would think a job already exists in a fresh HOME.
            'if [[ "$1" == "print" ]]; then\n'
            f'  grep -q "^bootstrap" {self.calls} 2>/dev/null && exit 0\n'
            "  exit 1\n"
            "fi\n"
            "exit 0\n"
        )
        self.stub.chmod(0o755)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_install(self, *flags: str):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["AGENTM_LAUNCHCTL"] = str(self.stub)
        return subprocess.run(
            ["bash", str(_INSTALL), *flags, str(self.target)],
            capture_output=True, text=True, env=env, timeout=900,
        )

    def launchctl_calls(self) -> str:
        return self.calls.read_text() if self.calls.exists() else ""

    def plist(self) -> Path:
        return self.home / PLIST_REL


class TestDaemonNotTouchedWithoutOptIn(DaemonRefreshBase):
    """A plain install must not install standing automation nobody asked for."""

    def test_plain_install_does_not_install_the_daemon(self) -> None:
        r = self.run_install()
        self.assertEqual(r.returncode, 0, f"install failed:\n{r.stdout}\n{r.stderr}")
        self.assertFalse(
            self.plist().exists(),
            "a plain install created a launchd agent — installing a resident "
            "service is an operator decision, not a side effect of installing "
            "the harness into some project",
        )
        self.assertEqual(
            self.launchctl_calls(), "",
            f"a plain install talked to launchd: {self.launchctl_calls()!r}",
        )
        # And it says nothing about the daemon, because there is nothing to say.
        self.assertNotIn("Refreshing the memory daemon", r.stdout)


class TestRefreshFiresOnlyWhenAlreadyInstalled(DaemonRefreshBase):
    """The whole point: an existing agent gets rebuilt on an ordinary run."""

    def _preinstall_plist(self) -> None:
        p = self.plist()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("<plist>pretend this was installed earlier</plist>\n")

    @unittest.skipUnless(shutil.which("go"), "needs Go to build the daemon")
    @unittest.skipUnless(platform.system() == "Darwin", "launchd is macOS-only")
    def test_existing_agent_is_refreshed_without_the_flag(self) -> None:
        self._preinstall_plist()
        r = self.run_install()
        self.assertEqual(r.returncode, 0, f"install failed:\n{r.stdout}\n{r.stderr}")
        self.assertIn(
            "Refreshing the memory daemon", r.stdout,
            "an installed daemon was not refreshed by an ordinary install run — "
            "the binary is built from daemon/, so this is how stale code stays "
            f"resident.\n{r.stdout}",
        )
        self.assertTrue((self.home / ".local/bin/agentmd").exists(),
                        "the refresh did not produce a binary")
        calls = self.launchctl_calls()
        self.assertIn("bootstrap", calls, f"the agent was never reloaded: {calls!r}")

    @unittest.skipUnless(platform.system() == "Darwin", "launchd is macOS-only")
    def test_no_daemon_opts_out_of_the_refresh(self) -> None:
        self._preinstall_plist()
        r = self.run_install("--no-daemon")
        self.assertEqual(r.returncode, 0, f"install failed:\n{r.stdout}\n{r.stderr}")
        self.assertNotIn("Refreshing the memory daemon", r.stdout)
        self.assertEqual(
            self.launchctl_calls(), "",
            "--no-daemon still reloaded the agent",
        )


class TestRefreshFailsLoudlyNotFatally(DaemonRefreshBase):
    """A refresh that cannot run must warn and let the install finish.

    Both halves are asserted, because either one alone is a bug: a refresh that
    aborts the run breaks an unrelated project install over a missing toolchain,
    and a refresh that skips quietly is how a daemon ends up running code nobody
    can account for.
    """

    @unittest.skipUnless(platform.system() == "Darwin", "launchd is macOS-only")
    def test_missing_go_warns_but_the_install_succeeds(self) -> None:
        p = self.plist()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("<plist>pretend this was installed earlier</plist>\n")

        # A PATH with no `go` on it, keeping the tools install.sh itself needs.
        bin_dir = self.root / "nogo-bin"
        bin_dir.mkdir()
        for tool in ("bash", "sh", "env", "mkdir", "cp", "rm", "mv", "ln", "sed",
                     "grep", "awk", "cat", "chmod", "find", "date", "uname",
                     "dirname", "basename", "id", "seq", "curl", "python3",
                     "sort", "head", "tail", "tr", "wc", "diff", "touch",
                     "printf", "test", "jq", "git", "xargs", "comm", "cut", "stat"):
            src = shutil.which(tool)
            if src:
                (bin_dir / tool).symlink_to(src)

        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["AGENTM_LAUNCHCTL"] = str(self.stub)
        env["PATH"] = str(bin_dir)
        r = subprocess.run(
            ["bash", str(_INSTALL), str(self.target)],
            capture_output=True, text=True, env=env, timeout=900,
        )

        self.assertEqual(
            r.returncode, 0,
            "a missing Go toolchain aborted the whole install; refreshing the "
            f"daemon is not what the run was for.\n{r.stdout}\n{r.stderr}",
        )
        combined = r.stdout + r.stderr
        self.assertIn(
            "NOT refreshed", combined,
            f"the skipped refresh was silent, which is the failure mode this "
            f"whole project exists to end.\n{combined}",
        )
        self.assertIn("brew install go", combined,
                      "the warning does not name the fix")


if __name__ == "__main__":
    unittest.main(verbosity=2)
