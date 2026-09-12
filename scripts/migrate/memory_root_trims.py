#!/usr/bin/env python3
"""memory_root_trims.py — the vault-side moves of agentm-vault plan 05, in
one scripted, resumable pass.

The code that ships with this script already reads and writes the new
locations (through `vault_layout.py`, newest home first, the retired one as
the fallback); this script moves the existing content to meet it. Run order
on the live machine: merge the PR, quiesce the runner and the daemon
(`launchctl bootout`), let the vault be committed clean, run with `--apply`,
fast-forward the clone, rebuild and reinstall the binaries, bootstrap and
kickstart, `agentmd embed`, verify. Until `--apply` is passed it only prints
what it would do — the dry run IS the default (the migration invariant:
dry-run reviewed before apply; link check green on both sides; `agentmd
embed` closes the move).

What it does, in order — each step checks its own preconditions, so a
re-run after a partial apply completes the remainder and touches nothing
already done:

  1. the standards set     `standards/user-preferences.md` from the pen's
                            voice kernel (or the packaged template when the
                            pen is empty); `security-and-secret-governance.md`
                            from the packaged template; the voice rules from
                            `Projects/_global/wiki-style/` to
                            `standards/voice/`; `moc-standards.md` generated;
                            the pen removed once it is empty.
  2. feature state         the two watchlists, the three settings files and
                            `forward-learning-sources.json` to
                            `Projects/agentm/`.
  3. engine files out      `.heat.json`, `.lifecycle.json`, `_meta/repos.json`
                            and `_dream/insights/*` to the engine state
                            directory; `_dream/` removed.
  4. the twin and the      the reading order folded into `index.md` under
     empties                "How to read this vault"; `_meta/`, `desk/` and
                            `memory/memory/` removed when nothing but
                            `.DS_Store` / `Icon` files is left in them.

`standards/` is the operator's: this script writes there under the plan's
authority and nothing else does — the two drafted files, the voice library
and the generated map, and only when absent.

Usage:
  python3 scripts/migrate/memory_root_trims.py                # dry run
  python3 scripts/migrate/memory_root_trims.py --apply
  python3 scripts/migrate/memory_root_trims.py --memory-root DIR --engine-dir DIR [--apply]
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_SCRIPTS = _REPO / "scripts"
_TOOLKIT = _REPO / "harness" / "skills" / "memory" / "scripts"
for _p in (str(_SCRIPTS), str(_TOOLKIT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import vault_layout  # noqa: E402

TEMPLATES = _REPO / "templates" / "standards"
IGNORABLE = frozenset({".DS_Store", "Icon\r", "Icon"})
FEATURE_ITEMS = (
    "_watchlist", "_skill-watchlist",
    "auto-orchestration-config.md", "skill-discovery-sources.md", "trusted-sources.md",
)
READ_SECTION_MARKER = "## How to read this vault"
READ_SECTION = """
## How to read this vault

Read in this order. First this file: it is the map. Then everything in
`standards/`: the filing contract (`storage-rules.md`), my preferences and
the standing security rules; they apply to every answer. For a project
question, open `Projects/<slug>/` next. Then search for the subject before
falling back to what you already know. If the vault says something, it wins.

`standards/storage-rules.md` decides where a capture goes. Entries are
markdown with YAML frontmatter; every one carries `status` and `created`
and exactly one of `type` (a memory) or `kind` (a record). `status: active`
is current, `superseded` is history, `unfiled` is captured and awaiting
filing — real content ranked lower, not content to skip. Follow
`[[wikilinks]]` when they are relevant.
"""


class Trims:
    def __init__(self, memory_root: Path, engine_dir: Path, *, apply: bool, out=sys.stdout):
        self.root = Path(memory_root)
        self.engine = Path(engine_dir)
        self.apply = apply
        self.out = out
        self.done: list[str] = []
        self.pending: list[str] = []
        self.left: list[str] = []
        # What a dry run would have moved or removed, so the emptiness checks
        # that follow read the way the apply will run.
        self._gone: set[Path] = set()
        vault_cands = vault_layout.vault_root_candidates(self.root)
        self.vault = vault_cands[0]
        self.standards = self.vault / vault_layout.STANDARDS_DIRNAME
        self.projects = self.vault / vault_layout.PROJECTS_DIRNAME
        self.feature = self.projects / vault_layout.FEATURE_PROJECT

    # ── helpers ────────────────────────────────────────────────────────
    def _say(self, what: str) -> None:
        verb = "did" if self.apply else "would"
        print(f"  {verb}: {what}", file=self.out)
        (self.done if self.apply else self.pending).append(what)

    def _leave(self, what: str) -> None:
        print(f"  left: {what}", file=self.out)
        self.left.append(what)

    def _move(self, src: Path, dest: Path) -> None:
        if dest.exists():
            self._leave(f"{dest} exists; {src} not moved (vault wins on collision)")
            return
        self._say(f"move {src} -> {dest}")
        self._gone.add(src)
        if self.apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))

    def _write(self, dest: Path, text: str, what: str) -> None:
        self._say(what)
        if self.apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")

    def _only_ignorable(self, d: Path) -> bool:
        for p in d.rglob("*"):
            if not p.is_file() or p.name in IGNORABLE:
                continue
            if p in self._gone or any(g in p.parents for g in self._gone):
                continue
            return False
        return True

    def _remove_empty_tree(self, d: Path, what: str) -> None:
        if not d.exists():
            return
        if not self._only_ignorable(d):
            self._leave(f"{d} still holds files; not removed")
            return
        self._say(f"remove {what} {d}")
        if self.apply:
            shutil.rmtree(d)

    # ── 1. the standards set ───────────────────────────────────────────
    def standards_set(self) -> None:
        pen = vault_layout.legacy_pen_dir(self.root)
        kernel = pen / "voice-kernel.md"
        prefs = self.standards / "user-preferences.md"
        if not prefs.exists():
            template = (TEMPLATES / "user-preferences.md").read_text(encoding="utf-8")
            if kernel.is_file():
                body = _body_of(kernel.read_text(encoding="utf-8"))
                head, _sep, _tail = template.partition("## Voice\n")
                tail_start = template.index("## How I want things done")
                text = head + "## Voice\n\n" + body.strip() + "\n\n" + template[tail_start:]
                self._write(prefs, text, f"write {prefs} from the pen's voice kernel")
            else:
                self._write(prefs, template, f"write {prefs} from the packaged template")
        if kernel.is_file():
            self._say(f"remove the folded pen file {kernel}")
            self._gone.add(kernel)
            if self.apply:
                kernel.unlink()
        sec = self.standards / "security-and-secret-governance.md"
        if not sec.exists():
            self._write(sec, (TEMPLATES / "security-and-secret-governance.md").read_text(encoding="utf-8"),
                        f"write {sec} from the packaged template")
        # The voice library.
        voice = self.standards / vault_layout.VOICE_DIRNAME
        for cand in vault_layout.voice_dir_candidates(self.root):
            if cand == voice or not cand.is_dir():
                continue
            for p in sorted(cand.iterdir()):
                if p.name in IGNORABLE:
                    continue
                self._move(p, voice / p.name)
            self._remove_empty_tree(cand, "the emptied voice store")
            parent = cand.parent
            if parent.name == "_global" and parent.exists() and self._only_ignorable(parent):
                self._remove_empty_tree(parent, "the emptied _global project")
        # The pen goes once it is empty. In a dry run the kernel counts as
        # already folded, so the report reads the way the apply will run.
        if pen.is_dir():
            remaining = [p for p in pen.rglob("*")
                         if p.is_file() and p.name not in IGNORABLE and p not in self._gone]
            if remaining:
                self._leave(f"the pen {pen} still holds entries you did not fold; not removed")
            else:
                self._say(f"remove the pen {pen}")
                if self.apply:
                    shutil.rmtree(pen)
        # The generated map.
        import moc_generator  # noqa: E402 — same toolkit dir
        moc = self.standards / "moc-standards.md"
        self._say(f"generate {moc}")
        if self.apply:
            moc_generator.generate_standards_moc(self.root)

    # ── 2. feature state ───────────────────────────────────────────────
    def feature_state(self) -> None:
        for name in FEATURE_ITEMS:
            src = self.root / "memory" / name
            if src.exists():
                self._move(src, self.feature / name)
        src = self.standards / "forward-learning-sources.json"
        if src.is_file():
            self._move(src, self.feature / "forward-learning-sources.json")

    # ── 3. engine files out ────────────────────────────────────────────
    def engine_files(self) -> None:
        for name in (".heat.json", ".lifecycle.json"):
            src = self.root / name
            if src.is_file():
                self._move(src, self.engine / name)
        reg = self.root / "_meta" / "repos.json"
        if reg.is_file():
            self._move(reg, self.engine / "repos.json")
        dream = self.root / "_dream"
        if dream.is_dir():
            insights = vault_layout.dream_insights_dir()
            for p in sorted(dream.rglob("*")):
                if p.is_file() and p.name not in IGNORABLE:
                    self._move(p, insights / p.name)
            self._remove_empty_tree(dream, "the dream directory")

    # ── 4. the twin and the empties ────────────────────────────────────
    def twin_and_empties(self) -> None:
        index = self.vault / "index.md"
        twin = self.root / "_meta" / "how-to-use-agentmemory.md"
        if index.is_file():
            text = index.read_text(encoding="utf-8")
            if READ_SECTION_MARKER not in text:
                self._write(index, text.rstrip("\n") + "\n" + READ_SECTION,
                            f"fold the reading order into {index}")
        else:
            self._leave(f"no {index}; the reading order was not folded")
        if twin.is_file():
            self._say(f"remove the twin {twin}")
            self._gone.add(twin)
            if self.apply:
                twin.unlink()
        self._remove_empty_tree(self.root / "_meta", "the machine directory")
        self._remove_empty_tree(self.root / "desk", "the empty desk")
        self._remove_empty_tree(self.root / "memory" / "memory", "the nested memory directory")

    def run(self) -> None:
        for label, step in (("the standards set", self.standards_set),
                            ("feature state to Projects/agentm/", self.feature_state),
                            ("engine files out", self.engine_files),
                            ("the twin and the empties", self.twin_and_empties)):
            print(f"{label}:", file=self.out)
            step()
        mode = "applied" if self.apply else "dry run"
        n = len(self.done if self.apply else self.pending)
        print(f"memory-root-trims: {mode} — {n} action(s), {len(self.left)} left for you",
              file=self.out)


def _body_of(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--memory-root", default=None,
                    help="the memory root (default: $MEMORY_ROOT, else the configured one)")
    ap.add_argument("--engine-dir", default=None,
                    help="the engine state directory (default: $AGENTM_STATE_DIR, else ~/.local/state/agentm)")
    ap.add_argument("--apply", action="store_true", help="perform the moves (default: dry run)")
    args = ap.parse_args(argv)
    root = Path(args.memory_root) if args.memory_root else vault_layout.env_memory_root()
    if root is None:
        try:
            import harness_memory as hm  # noqa: E402
            root = hm.memory_root()
        except ImportError:
            root = None
    if root is None or not Path(root).is_dir():
        print("memory-root-trims: no memory root resolves (pass --memory-root or set MEMORY_ROOT)",
              file=sys.stderr)
        return 2
    if args.engine_dir:
        os.environ["AGENTM_STATE_DIR"] = str(Path(args.engine_dir))
    engine = vault_layout.engine_state.engine_state_dir()
    Trims(Path(root), engine, apply=args.apply).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
