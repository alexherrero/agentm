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

The fake HOME is for install *paths*. Caches that also hang off $HOME are
deliberately left pointing at the machine's own copies — see install_env(),
which holds the reasoning and is the thing to read before re-isolating one.

Every test here shells out to install.sh, so the whole file skips on Windows —
`bash` there resolves to WSL, which on a bare runner has no distribution
installed. install.ps1 is the Windows path and covers none of this.

Run: python3 scripts/test_install_daemon_refresh.py
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_INSTALL = _REPO / "install.sh"

PLIST_REL = "Library/LaunchAgents/com.agentm.daemon.plist"

# The real HOME, captured at import before any test can put a fake one in its
# place. The machine's caches hang off it and are reused on purpose.
_REAL_HOME = Path(os.path.expanduser("~"))

# install.sh's MODEL_DIR + MODEL_FILE, spelled out. Kept honest by
# test_an_existing_model_is_reused_not_refetched, which fails if install.sh
# moves the path out from under this literal.
MODEL_REL = ".local/share/agentm/models/embeddinggemma-300M-Q8_0.gguf"

_GO_ENV = None


def real_go_env() -> dict:
    """GOCACHE/GOMODCACHE/GOPATH as this machine resolves them under the real HOME.

    Asked of Go rather than assumed, because a machine that sets them itself —
    a shared cache, a non-default GOPATH — has to get its own answer and not
    $HOME/go. Empty when there is no Go to ask; the tests that need one skip.
    """
    global _GO_ENV
    if _GO_ENV is None:
        keys = ("GOCACHE", "GOMODCACHE", "GOPATH")
        _GO_ENV = {}
        if shutil.which("go"):
            try:
                out = subprocess.run(["go", "env", *keys], capture_output=True,
                                     text=True, timeout=60, check=True).stdout
                values = out.splitlines()
                if len(values) == len(keys):
                    _GO_ENV = {k: v for k, v in zip(keys, values) if v}
            except (subprocess.SubprocessError, OSError):
                _GO_ENV = {}
    return _GO_ENV


@unittest.skipIf(os.name == "nt", "drives install.sh — POSIX only (install.ps1 is the Windows path)")
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

        # Point the fake HOME's model cache at the machine's own copy, so
        # install.sh checksums a model that is already there and skips the
        # ~330MB fetch. See install_env() for why this is not re-isolated.
        #
        # Safe against a stale or mismatched copy on this machine: install.sh
        # writes its download to a sibling `.part` and `mv`s that over the
        # destination, and `mv` replaces a symlink rather than writing through
        # it. The worst case is a run re-downloading into its own fake HOME —
        # never the machine's file being written.
        model = _REAL_HOME / MODEL_REL
        if model.is_file():
            link = self.home / MODEL_REL
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(model)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def install_env(self, **overrides: str) -> dict:
        """The environment install.sh runs under: fake install paths, real caches.

        HOME is faked because every path install.sh writes hangs off it — the
        plist, the daemon binary, the log dir — so a fake HOME fully contains
        the run. That is the isolation these tests asked for.

        The isolation they never asked for is the caches that also key off HOME
        and follow it silently, leaving every run to re-fetch what the machine
        already has on disk:

          GOCACHE / GOMODCACHE / GOPATH default under $HOME, so building
          daemon/ inside a fake HOME re-downloads the module graph and
          recompiles the world. Pinned to the machine's own, here.

          install.sh's embedding model lives at
          $HOME/.local/share/agentm/models, so a fake HOME re-downloads ~330MB
          of GGUF weights over the network. Seeded as a symlink in setUp.

        Both are content-addressed caches, not install paths. Nothing this
        module asserts can tell the difference, and re-isolating either costs
        most of the module's wall clock plus a network dependency for tests
        that check neither Go's downloader nor curl. If you are here because a
        run wrote somewhere unexpected, the thing to move is an install path.
        """
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["AGENTM_LAUNCHCTL"] = str(self.stub)
        env.update(real_go_env())
        env.update(overrides)
        return env

    def run_install(self, *flags: str, env: dict | None = None):
        return subprocess.run(
            ["bash", str(_INSTALL), *flags, str(self.target)],
            capture_output=True, text=True,
            env=self.install_env() if env is None else env, timeout=900,
        )

    def preinstall_plist(self) -> None:
        """Stand in for an earlier `--daemon` run, so this one is a refresh."""
        p = self.plist()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("<plist>pretend this was installed earlier</plist>\n")

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

    @unittest.skipUnless(shutil.which("go"), "needs Go to build the daemon")
    @unittest.skipUnless(platform.system() == "Darwin", "launchd is macOS-only")
    def test_existing_agent_is_refreshed_without_the_flag(self) -> None:
        self.preinstall_plist()
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
        self.preinstall_plist()
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
        self.preinstall_plist()

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

        r = self.run_install(env=self.install_env(PATH=str(bin_dir)))

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


class TestTheMachinesCachesAreReused(DaemonRefreshBase):
    """The fake HOME is for install paths; caches keyed off HOME must not follow.

    These exist because re-isolating a cache is a one-line change that looks
    like tightening the sandbox and costs most of the module's wall clock. The
    symptom — "the install tests got slow again", or a run that fails on a
    flaky network — points nowhere near the cause, so the invariant is asserted
    directly rather than left to whoever notices the clock.
    """

    @unittest.skipUnless(shutil.which("go"), "needs Go for there to be a Go cache")
    def test_go_caches_are_not_relocated_into_the_fake_home(self) -> None:
        env = self.install_env()
        for key in ("GOCACHE", "GOMODCACHE"):
            self.assertIn(
                key, env,
                f"{key} is unset, so Go derives it from the fake HOME and the "
                "daemon build re-downloads the module graph and recompiles the "
                "world on every run — see install_env()",
            )
            self.assertFalse(
                Path(env[key]).is_relative_to(self.home),
                f"{key} points inside the fake HOME ({env[key]}) — the build "
                "cache was re-isolated; see install_env()",
            )

    def test_model_rel_matches_the_path_install_sh_reads(self) -> None:
        """MODEL_REL duplicates install.sh's path; this is what keeps it honest.

        The seed only helps if it lands where install.sh looks, and drift is
        silent — a run that finds nothing just downloads again and still
        passes. Compared directly rather than left to the end-to-end test
        below, which skips on a machine that has no model to reuse and so
        cannot be the thing that catches this.
        """
        text = _INSTALL.read_text()
        d = re.search(r'^MODEL_DIR="\$HOME/(.+)"$', text, re.M)
        f = re.search(r'^MODEL_FILE="(.+)"$', text, re.M)
        self.assertTrue(
            d and f,
            "install.sh no longer spells MODEL_DIR/MODEL_FILE as plain $HOME "
            "literals, so the seed in setUp cannot be checked against them — "
            "re-derive MODEL_REL from whatever replaced them",
        )
        self.assertEqual(
            f"{d.group(1)}/{f.group(1)}", MODEL_REL,
            "install.sh moved its model path and MODEL_REL did not follow, so "
            "the seed lands where nothing reads it and every run re-downloads "
            "~330MB",
        )

    def test_the_fake_home_is_seeded_with_the_machines_model(self) -> None:
        real = _REAL_HOME / MODEL_REL
        if not real.is_file():
            self.skipTest(f"no embedding model at {real} to reuse")
        link = self.home / MODEL_REL
        self.assertTrue(
            link.is_symlink(),
            f"the fake HOME has no model at {MODEL_REL}, so install.sh fetches "
            "~330MB over the network on every run — see install_env()",
        )
        self.assertEqual(link.resolve(), real.resolve())

    @unittest.skipUnless(shutil.which("go"), "needs Go to build the daemon")
    @unittest.skipUnless(platform.system() == "Darwin", "launchd is macOS-only")
    def test_an_existing_model_is_reused_not_refetched(self) -> None:
        """End-to-end: the seeded path is the one install.sh actually reads.

        The seed spells out install.sh's MODEL_DIR. If that moves, the seed
        lands where nothing reads it and the download comes back — silently,
        because fetching still succeeds and the install still passes. The
        assertion is on install.sh saying it skipped, which is the only
        observable difference between the two.
        """
        if not (_REAL_HOME / MODEL_REL).is_file():
            self.skipTest("no embedding model on this machine to reuse")
        self.preinstall_plist()
        r = self.run_install()
        self.assertEqual(r.returncode, 0, f"install failed:\n{r.stdout}\n{r.stderr}")
        self.assertIn(
            "embedding model already present and verified", r.stdout,
            "install.sh did not find the seeded model and fetched instead — "
            f"MODEL_REL is stale against install.sh's MODEL_DIR.\n{r.stdout}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
