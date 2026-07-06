"""test_ci_path_filter.py — docs/wiki-only pushes must skip the light push-only
job; a mixed code+docs diff must never sneak through on `pull_request` either
(PLAN-ci-walltime-diet task 4; amended 2026-07-06, worktree-native flow /
PLAN-worktree-native-flow).

**2026-07-06 contract change:** the full 3-OS matrix + `ci-all` moved from
`push` to `pull_request` (crickets-conventions.md's ci-battery amendment,
mirrored here). A trigger-level `paths-ignore` on a REQUIRED `pull_request`
check would mean a docs-only PR's check never reports, which blocks that PR's
merge forever — so the docs/wiki diet is deliberately NOT carried onto
`pull_request` this wave. It survives only on `push` for the 3 OS workflows
(which now run just the light `syntax`/no-op path on push) and disappears
entirely from `ci-all.yml`, which dropped its `push` trigger outright (the
aggregate only ever gated a PR merge, so it has no push-side job left to
skip). The diet returns as a job-level, diff-aware no-op inside `ci-all`
under the follow-on `PLAN-per-plan-ci` — at that point the `pull_request`-side
assertions below should flip back to "docs-only IS skippable."

Reads the real `paths-ignore` glob out of each test workflow's `on:` block and
replays it against two synthetic file lists: a docs-only diff and a mixed
diff, one code file among many doc files (must NEVER be classified skippable
anywhere — the guard is diff-based and conservative).

`matches_ignore_patterns` mirrors GitHub Actions' `paths-ignore` semantics for
the two pattern shapes this repo actually uses (`DIR/**` and `**/*.EXT`) — not
a general minimatch clone, which this codebase doesn't need.
"""
import fnmatch
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = ["tests-linux.yml", "tests-mac.yml", "tests-windows.yml", "ci-all.yml"]
# ci-all.yml dropped its push trigger entirely (2026-07-06) — it only ever
# gated a PR merge, so there's no push-side job left to assert a diet on.
PUSH_TRIGGERED_WORKFLOWS = ["tests-linux.yml", "tests-mac.yml", "tests-windows.yml"]

DOCS_ONLY_DIFF = ["wiki/foo.md", "README.md"]
MIXED_DIFF = ["wiki/foo.md", "scripts/bar.py"]


def matches_ignore_patterns(filepath, patterns):
    for pat in patterns:
        if pat.endswith("/**"):
            prefix = pat[: -len("/**")]
            if filepath == prefix or filepath.startswith(prefix + "/"):
                return True
        elif pat.startswith("**/"):
            if fnmatch.fnmatch(filepath.rsplit("/", 1)[-1], pat[len("**/") :]):
                return True
        elif fnmatch.fnmatch(filepath, pat):
            return True
    return False


def is_skippable(files, patterns):
    """GH Actions paths-ignore semantics: skip only if EVERY changed file matches."""
    return bool(patterns) and all(matches_ignore_patterns(f, patterns) for f in files)


def load_on_block(workflow_file):
    doc = yaml.safe_load((REPO / ".github" / "workflows" / workflow_file).read_text())
    return doc.get("on", doc.get(True, {}))  # PyYAML parses bare `on:` as bool True


def load_paths_ignore(workflow_file):
    on_block = load_on_block(workflow_file)
    push = on_block.get("push") or {}
    pr = on_block.get("pull_request") or {}
    return push.get("paths-ignore", []), pr.get("paths-ignore", [])


class TestDocsOnlyDiffSkipsMatrix(unittest.TestCase):
    def test_docs_only_diff_is_skippable_on_push(self):
        """Unchanged: the 3 OS workflows still diet on push (ci-all has no push left to test)."""
        for wf in PUSH_TRIGGERED_WORKFLOWS:
            push_patterns, _ = load_paths_ignore(wf)
            self.assertTrue(
                is_skippable(DOCS_ONLY_DIFF, push_patterns),
                f"{wf}: a wiki/**+*.md-only diff must skip the push trigger",
            )

    def test_ci_all_has_no_push_trigger(self):
        """ci-all.yml only ever gated a PR merge — it dropped push entirely (2026-07-06)."""
        on_block = load_on_block("ci-all.yml")
        self.assertNotIn(
            "push", on_block,
            "ci-all.yml should have no push trigger — it only gates PR merges now",
        )

    def test_docs_only_diff_is_not_yet_skippable_on_pull_request(self):
        """Deliberate interim state (rider 3, Fable judgment pass): a required
        pull_request check can't carry a trigger-level paths-ignore, or a
        docs-only PR's check never reports and the merge hangs forever. Flips
        back to assertTrue once PLAN-per-plan-ci lands the job-level diet."""
        for wf in WORKFLOWS:
            _, pr_patterns = load_paths_ignore(wf)
            self.assertFalse(
                is_skippable(DOCS_ONLY_DIFF, pr_patterns),
                f"{wf}: pull_request should carry no paths-ignore until "
                f"PLAN-per-plan-ci's job-level diet lands",
            )

    def test_mixed_code_and_docs_diff_never_skips_matrix(self):
        for wf in WORKFLOWS:
            push_patterns, pr_patterns = load_paths_ignore(wf)
            with self.subTest(workflow=wf, trigger="push"):
                self.assertFalse(
                    is_skippable(MIXED_DIFF, push_patterns),
                    f"{wf}: one code file among doc files must still run the matrix (push)",
                )
            with self.subTest(workflow=wf, trigger="pull_request"):
                self.assertFalse(
                    is_skippable(MIXED_DIFF, pr_patterns),
                    f"{wf}: one code file among doc files must still run the matrix (pull_request)",
                )


if __name__ == "__main__":
    unittest.main()
