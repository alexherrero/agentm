"""test_ci_path_filter.py — docs/wiki-only pushes must skip the 3-OS matrix,
a mixed code+docs diff must never sneak through (PLAN-ci-walltime-diet task 4).

Reads the real `paths-ignore` glob out of each test/aggregate workflow's `on:`
block and replays it against two synthetic file lists: a docs-only diff (should
be classified skippable) and a mixed diff, one code file among many doc files
(must NEVER be classified skippable — the guard is diff-based and conservative,
per the crickets-conventions.md ci-battery amendment this task implements).

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


def load_paths_ignore(workflow_file):
    doc = yaml.safe_load((REPO / ".github" / "workflows" / workflow_file).read_text())
    on_block = doc.get("on", doc.get(True, {}))  # PyYAML parses bare `on:` as bool True
    push = on_block.get("push") or {}
    pr = on_block.get("pull_request") or {}
    return push.get("paths-ignore", []), pr.get("paths-ignore", [])


class TestDocsOnlyDiffSkipsMatrix(unittest.TestCase):
    def test_docs_only_diff_is_skippable_on_every_workflow(self):
        for wf in WORKFLOWS:
            push_patterns, pr_patterns = load_paths_ignore(wf)
            with self.subTest(workflow=wf, trigger="push"):
                self.assertTrue(
                    is_skippable(DOCS_ONLY_DIFF, push_patterns),
                    f"{wf}: a wiki/**+*.md-only diff must skip the push trigger",
                )
            with self.subTest(workflow=wf, trigger="pull_request"):
                self.assertTrue(
                    is_skippable(DOCS_ONLY_DIFF, pr_patterns),
                    f"{wf}: a wiki/**+*.md-only diff must skip the pull_request trigger",
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
