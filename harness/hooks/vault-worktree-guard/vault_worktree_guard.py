#!/usr/bin/env python3
"""vault-worktree-guard — refuse a git worktree in the vault.

A PreToolUse hook, registered once for the machine. Claude Code hands it the
tool call it is about to make. The hook refuses the call, with exit 2 and the
reason on stderr, when it would create a git worktree in the vault: a worktree
of the vault's own repository, or a worktree of any repository whose working
tree would sit under the vault root. Every other call passes untouched, and so
does every worktree anywhere else, agentm's and crickets' included.

WHY THE VAULT. Google Drive mirrors the vault folder in place, so it uploads a
worktree there file by file: 6,276 upload events on 2026-10-02. The desktop app
also sets `extensions.worktreeConfig` in the repository's config when it makes
a worktree. The daemon's git library refuses that extension, so the daemon
stops committing on its next restart (agentm issue #859). And a worktree in the
vault has no authority under the operator's doctrine: the vault carries no
worktree-per-plan opt-in.

THE FOUR CALLS IT JUDGES. Task 189's `routes.md` has the evidence for each.

- `mcp__ccd_session__spawn_task`, a background-task chip. Clicking a chip
  starts a session in a fresh worktree under the chip's cwd, and the app makes
  that worktree itself, where no hook sees it. The spawn is the last point a
  hook reaches. This was the 2026-10-02 route.
- `EnterWorktree`, which makes the worktree under the current cwd's repository.
  This was the 2026-08-16 route.
- `Agent` (or its older name `Task`) with `isolation: "worktree"`.
- `Bash`, when the command line adds a git worktree. The hook reads the command
  the way bash would, through `cd` and `pushd` (undone at the end of a subshell
  or `$(…)`), `-C`, `--git-dir`, `GIT_DIR=` and `export`, line continuations,
  `bash -c` and `-lc`, `eval`, and a here-document fed to a shell. A path built
  from a variable the hook can't expand, quoting the tokenizer can't follow, or
  a script file that runs git itself gets past it. The doctor's
  `vault-worktrees` row catches those the same day.

WHAT "THE VAULT" IS. `harness_memory.vault_path()` names the root, so the hook
holds no path of its own. The repository is identified by its git common dir,
which is the same for the vault and for every worktree of it, wherever that
worktree sits. Both sides are compared after resolving symlinks, and as paths,
never as strings, so a directory that only looks like the vault is not the
vault.

IT FAILS OPEN. A payload it can't read, a vault that doesn't resolve, or git
missing from PATH all mean "allow". A guard that blocked every Bash call on its
own error would cost more than the worktree it exists to stop.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, Optional

sys.dont_write_bytecode = True

SPAWN_TASK = "mcp__ccd_session__spawn_task"
ENTER_WORKTREE = "EnterWorktree"
AGENT_TOOLS = ("Agent", "Task")  # root-casing: Claude Code's subagent tool names, not a vault path
BASH = "Bash"

GIT_TIMEOUT = 5  # seconds, per git call

# git's global options that take their value as the next word.
_GIT_OPTS_WITH_VALUE = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix",
    "--config-env", "--attr-source",
})
# The add verb's options that take their value as the next word.
_ADD_OPTS_WITH_VALUE = frozenset({"-b", "-B", "--reason"})
# Words that run the command after them: `env GIT_DIR=x git …`, `command git …`.
_PREFIX_COMMANDS = frozenset({"env", "command", "exec", "nohup", "builtin", "sudo", "nice",
                              "xargs", "timeout"})
_PREFIX_OPTS_WITH_VALUE = {
    "env": frozenset({"-u", "-C", "-S"}),
    "sudo": frozenset({"-u", "-g", "-C", "-D", "-h", "-p", "-U"}),
    "nice": frozenset({"-n"}),
    "xargs": frozenset({"-n", "-I", "-L", "-P", "-d", "-s", "-E", "-a"}),
    "timeout": frozenset({"-s", "-k"}),
}
# Shell words that come before a command without being one.
_KEYWORDS = frozenset({"{", "}", "!", "if", "then", "elif", "else", "while", "until", "do", "time"})
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_HEREDOC = re.compile(r"<<(?P<dash>-)?[ \t]*(?P<q>['\"]?)(?P<word>[A-Za-z0-9_.-]+)(?P=q)")
_HEREDOC_MARK = "__vault_worktree_guard_heredoc_{}__"
_HEREDOC_MARK_RE = re.compile(r"^__vault_worktree_guard_heredoc_(\d+)__$")
_PUNCTUATION = "();<>|&\n"
_QUOTED_MARK = "\x01"
# Past this many characters the command isn't read, and the call is allowed.
# The tokenizer is slow on one enormous word, and a hook that ran past Claude
# Code's timeout would be killed anyway; this makes the outcome deliberate.
MAX_COMMAND = 100_000


class Vault(NamedTuple):
    root: Path                  # resolved
    common_dir: Optional[Path]  # resolved git common dir; None when the vault is not a repository


class Spawn(NamedTuple):
    """One `worktree add` found in a command line."""
    cwd: Optional[Path]      # where git runs, after `cd` and `-C`; None when unknown
    git_dir: Optional[Path]  # from `--git-dir` or `GIT_DIR=`
    dest: Optional[Path]     # the new worktree's path; None when unknown


# ── the vault ────────────────────────────────────────────────────────────────

def _agentm_scripts_candidates() -> list:
    here = Path(__file__).resolve()
    out = []
    if len(here.parents) > 3:
        out.append(here.parents[3] / "scripts")  # <clone>/harness/hooks/<name>/<file>
    prefix = Path(os.environ.get("AGENTM_INSTALL_PREFIX") or Path.home() / ".claude")
    try:
        cfg = json.loads((prefix / ".agentm-config.json").read_text(encoding="utf-8"))
        clone = (cfg.get("source_clones") or {}).get("agentm")
        if clone:
            out.append(Path(clone) / "scripts")
    except (OSError, ValueError, AttributeError):
        pass
    out.append(Path.home() / "Antigravity" / "agentm" / "scripts")
    return out


def _harness_memory():
    for scripts in _agentm_scripts_candidates():
        if not (scripts / "harness_memory.py").is_file():
            continue
        sys.path.insert(0, str(scripts))
        try:
            import harness_memory  # noqa: PLC0415
            return harness_memory
        except Exception:  # noqa: BLE001 — a broken clone is a reason to try the next
            sys.path.pop(0)
            sys.modules.pop("harness_memory", None)
    return None


def resolve_vault() -> Optional[Vault]:
    hm = _harness_memory()
    if hm is None:
        return None
    try:
        v = hm.vault_path()
    except Exception:  # noqa: BLE001 — StorageBackendNotInstalledError and the like
        return None
    if v is None:
        return None
    root = Path(os.path.realpath(v))
    return Vault(root, common_dir(root, None))


def _git_env() -> dict:
    # The hook inherits Claude Code's environment. A GIT_DIR or GIT_WORK_TREE
    # left in it would answer for that repository, not the one asked about.
    return {k: v for k, v in os.environ.items()
            if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE")}


def _existing(p: Path) -> Optional[Path]:
    """`p`, or its nearest ancestor that exists."""
    for candidate in (p, *p.parents):
        if candidate.is_dir():
            return candidate
    return None


def common_dir(where: Optional[Path], git_dir: Optional[Path]) -> Optional[Path]:
    """The resolved git common dir of the repository at `where` (or of
    `git_dir`), or None when there is none or git can't say."""
    if git_dir is not None:
        args = ["git", f"--git-dir={git_dir}"]
        run_in = _existing(git_dir)
    else:
        run_in = _existing(where) if where is not None else None
        args = ["git"]
    if run_in is None:
        return None
    try:
        proc = subprocess.run(
            args + ["rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(run_in), env=_git_env(), capture_output=True, text=True, timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = proc.stdout.strip()
    if proc.returncode != 0 or not out:
        return None
    p = Path(out)
    if not p.is_absolute():
        p = run_in / p
    return Path(os.path.realpath(p))


def under(path: Path, root: Path) -> bool:
    """True when `path` is `root` or inside it. Both are resolved already.

    The path test is the fast answer. The fallback asks the filesystem whether
    any existing ancestor of `path` is the same directory as `root`, because
    macOS's disk ignores case: `~/vault/x` is inside `~/Vault`, and no
    comparison of the two strings says so."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        pass
    try:
        root_stat = os.stat(root)
    except OSError:
        return False
    for candidate in (path, *path.parents):
        try:
            if os.path.samestat(os.stat(candidate), root_stat):
                return True
        except OSError:
            continue
    return False


def _same_dir(a: Path, b: Path) -> bool:
    if a == b:
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _is_vault_repository(vault: Vault, where: Optional[Path], git_dir: Optional[Path] = None) -> bool:
    if vault.common_dir is None:
        return False
    found = common_dir(where, git_dir)
    return found is not None and _same_dir(found, vault.common_dir)


# ── reading a command line ──────────────────────────────────────────────────

def strip_comments_and_heredocs(command: str) -> tuple:
    """(the command as the shell's parser sees it, its here-document bodies).

    Four things are done to the text so the tokenizer reads it the way bash
    does:

    - A backslash before a newline, outside single quotes, is a line
      continuation and is removed with the newline.
    - `$'…'` (ANSI-C quoting) becomes an ordinary double-quoted word.
    - A `#` starts a comment only at the start of a word and outside quotes, so
      `foo#bar` keeps its `#`.
    - A quoted word made only of shell punctuation, such as `'&&'`, gets a
      marker inside its quotes. The tokenizer hands back a quoted `&&` and the
      operator `&&` as the same string, and the marker keeps the word a word.
    - A here-document's body leaves the text. The operator stays, with a marker
      in place of its delimiter, and the body is returned in the list at the
      marker's index. A body is text, not commands, unless it is fed to a
      shell, and `bash_spawns` decides which.
    """
    out, bodies, pending = [], [], []
    i, n = 0, len(command)
    quote, opened = None, 0
    prev = "\n"
    while i < n:
        ch = command[i]
        if quote == "'":
            out.append(ch)
            if ch == "'":
                quote = None
                _mark_quoted_punctuation(out, opened)
            prev, i = ch, i + 1
            continue
        if ch == "\\" and i + 1 < n:
            if command[i + 1] == "\n":
                i += 2  # a line continuation
                continue
            out.append(command[i:i + 2])
            prev, i = command[i + 1], i + 2
            continue
        if quote == '"':
            out.append(ch)
            if ch == '"':
                quote = None
                _mark_quoted_punctuation(out, opened)
            prev, i = ch, i + 1
            continue
        if ch == "$" and command.startswith("$'", i):
            j, buf = i + 2, []
            while j < n and command[j] != "'":
                if command[j] == "\\" and j + 1 < n:
                    buf.append(command[j + 1])
                    j += 2
                    continue
                buf.append(command[j])
                j += 1
            out.append('"' + re.sub(r'([\\"$`])', r"\\\1", "".join(buf)) + '"')
            prev, i = '"', j + 1
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            opened = len(out)
            prev, i = ch, i + 1
            continue
        if ch == "#" and prev in " \t\n;&|()":
            j = command.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch == "<" and command.startswith("<<", i) and not command.startswith("<<<", i):
            m = _HEREDOC.match(command, i)
            if m:
                pending.append((m.group("word"), bool(m.group("dash")), len(bodies)))
                out.append(f"<< {_HEREDOC_MARK.format(len(bodies))}")
                bodies.append("")
                prev, i = command[m.end() - 1], m.end()
                continue
        if ch == "\n" and pending:
            out.append("\n")
            i += 1
            for word, dash, index in pending:
                lines = []
                while i < n:
                    j = command.find("\n", i)
                    line = command[i:n if j < 0 else j]
                    i = n if j < 0 else j + 1
                    if (line.lstrip("\t") if dash else line) == word:
                        break
                    lines.append(line)
                bodies[index] = "\n".join(lines)
            pending = []
            prev = "\n"
            continue
        out.append(ch)
        prev, i = ch, i + 1
    return "".join(out), bodies


def _mark_quoted_punctuation(out: list, opened: int) -> None:
    """Mark the quoted word that just closed if it is all punctuation. `opened`
    is the index in `out` just after its opening quote."""
    inner = "".join(out[opened:-1])
    if inner and all(c in _PUNCTUATION for c in inner):
        out.insert(opened, _QUOTED_MARK)


def _tokens(text: str) -> Optional[list]:
    lex = shlex.shlex(text, posix=True, punctuation_chars=_PUNCTUATION)
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError:
        return None


def _is_punct(tok: str) -> bool:
    return bool(tok) and all(c in _PUNCTUATION for c in tok)


def _events(tokens: list) -> list:
    """The tokens as a sequence of events: ("cmd", words, here-doc indexes) for
    each simple command, and ("push",) / ("pop",) where a subshell, command
    substitution or process substitution opens and closes. A `cd` inside one
    of those doesn't move the commands after it."""
    events, words, docs = [], [], []

    def flush():
        nonlocal words, docs
        if words:
            events.append(("cmd", words, docs))
        words, docs = [], []

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if not _is_punct(tok):
            words.append(tok)
            i += 1
            continue
        if "(" in tok or ")" in tok:
            flush()
            for c in tok:
                if c == "(":
                    events.append(("push",))
                elif c == ")":
                    events.append(("pop",))
            i += 1
            continue
        if "<" in tok or ">" in tok:
            # A redirection: drop its target, and a file descriptor number
            # written just before it (`2>&1`). A here-document's marker is kept
            # aside for the command it belongs to.
            if words and words[-1].isdigit():
                words.pop()
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            m = _HEREDOC_MARK_RE.match(target)
            if m and tok.startswith("<<"):
                docs.append(int(m.group(1)))
            i += 2
            continue
        flush()
        i += 1
    flush()
    return events


def _resolve(base: Optional[Path], raw: Optional[str]) -> Optional[Path]:
    if not raw:
        return None
    s = os.path.expandvars(os.path.expanduser(raw))
    if "$" in s or "`" in s:
        return None  # a variable the hook can't see
    p = Path(s)
    if not p.is_absolute():
        if base is None:
            return None
        p = base / p
    return Path(os.path.realpath(p))


def _strip_prefix(words: list) -> tuple:
    """(environment assignments, the command words after any prefix)."""
    env = {}
    i = 0
    while i < len(words):
        w = words[i]
        if w in _KEYWORDS:
            i += 1
            continue
        if _ASSIGNMENT.match(w):
            name, _, value = w.partition("=")
            env[name] = value
            i += 1
            continue
        base = os.path.basename(w)
        if base in _PREFIX_COMMANDS:
            takes_value = _PREFIX_OPTS_WITH_VALUE.get(base, frozenset())
            i += 1
            while i < len(words) and words[i].startswith("-") and words[i] != "-":
                i += 2 if words[i] in takes_value else 1
            if base == "timeout" and i < len(words):
                i += 1  # the duration
            continue
        break
    return env, words[i:]


def _cd(cwd: Optional[Path], args: list) -> Optional[Path]:
    args = [a for a in args if a not in ("-L", "-P", "-e", "-@", "--")]
    if not args:
        return Path(os.path.realpath(Path.home()))
    if args[0] == "-":
        return None
    return _resolve(cwd, args[0])


def _dash_c_script(words: list) -> Optional[str]:
    """The script a shell is told to run with `-c` (or `-lc`, `-ec` …), or None."""
    i = 1
    while i < len(words):
        w = words[i]
        if w == "--" or not w.startswith(("-", "+")):
            return None  # a script file, which the hook doesn't read
        if w in ("-o", "+o", "-O", "+O"):
            i += 2
            continue
        if not w.startswith("--") and "c" in w[1:]:
            return words[i + 1] if i + 1 < len(words) else None
        i += 1
    return None


def _git_spawn(words: list, cwd: Optional[Path], env: dict) -> Optional[Spawn]:
    raw_git_dir = env.get("GIT_DIR")
    i = 1
    while i < len(words):
        w = words[i]
        if w == "-C" and i + 1 < len(words):
            cwd = _resolve(cwd, words[i + 1])
            i += 2
            continue
        if w == "--git-dir" and i + 1 < len(words):
            raw_git_dir = words[i + 1]
            i += 2
            continue
        if w.startswith("--git-dir="):
            raw_git_dir = w.split("=", 1)[1]
            i += 1
            continue
        if w in _GIT_OPTS_WITH_VALUE:
            i += 2
            continue
        if w.startswith("-"):
            i += 1
            continue
        break
    if i >= len(words) or words[i] != "worktree":
        return None
    rest = words[i + 1:]
    j = 0
    while j < len(rest) and rest[j].startswith("-"):
        j += 1
    if j >= len(rest) or rest[j] != "add":
        return None
    args = rest[j + 1:]
    dest = None
    k = 0
    while k < len(args):
        a = args[k]
        if a == "--":
            dest = args[k + 1] if k + 1 < len(args) else None
            break
        if a in _ADD_OPTS_WITH_VALUE:
            k += 2
            continue
        if a.startswith("-"):
            k += 1
            continue
        dest = a
        break
    return Spawn(cwd, _resolve(cwd, raw_git_dir), _resolve(cwd, dest))


def bash_spawns(command: str, cwd: Optional[Path], *, depth: int = 0, env: Optional[dict] = None) -> list:
    """Every `worktree add` the command line would run, with where it runs."""
    if len(command) > MAX_COMMAND:
        return []  # fail open: see MAX_COMMAND
    text, bodies = strip_comments_and_heredocs(command)
    tokens = _tokens(text)
    if tokens is None:
        # Quoting the tokenizer can't follow. bash may reject the line too, and
        # a refusal on a guess would block commands that never touch a
        # worktree, so the hook fails open.
        return []
    exported = dict(env or {})
    saved, dirs, spawns = [], [], []
    for event in _events(tokens):
        if event[0] == "push":
            saved.append(cwd)
            continue
        if event[0] == "pop":
            if saved:
                cwd = saved.pop()
            continue
        _, words, docs = event
        assigns, words = _strip_prefix(words)
        if not words:
            continue
        head = os.path.basename(words[0])
        here_env = {**exported, **assigns}
        if head == "export":
            for w in words[1:]:
                if _ASSIGNMENT.match(w):
                    name, _, value = w.partition("=")
                    exported[name] = value
        elif head == "cd":
            cwd = _cd(cwd, words[1:])
        elif head == "pushd":
            dirs.append(cwd)
            cwd = _cd(cwd, words[1:])
        elif head == "popd":
            if dirs:
                cwd = dirs.pop()
        elif head == "git":
            found = _git_spawn(words, cwd, here_env)
            if found is not None:
                spawns.append(found)
        elif depth < 3 and head == "eval":
            script = " ".join(w.replace(_QUOTED_MARK, "") for w in words[1:])
            spawns.extend(bash_spawns(script, cwd, depth=depth + 1, env=here_env))
        elif depth < 3 and head in _SHELLS:
            script = _dash_c_script(words)
            if script is not None:
                spawns.extend(bash_spawns(script, cwd, depth=depth + 1, env=here_env))
            else:
                # `bash <<EOF`: the here-document is the script.
                for index in docs:
                    spawns.extend(bash_spawns(bodies[index], cwd, depth=depth + 1, env=here_env))
    return spawns


# ── the decision ─────────────────────────────────────────────────────────────

_WHY = (
    "Google Drive mirrors the vault, so it would upload the worktree file by file. "
    "Making one also sets `extensions.worktreeConfig` in the vault repository's config, "
    "and the memory daemon stops committing on its next restart when that key is there "
    "(agentm issue #859). The vault has no worktree opt-in, so a worktree there has no authority."
)


def _refusal(what: str, where: Path, vault: Vault, instead: str) -> str:
    return f"Refused by vault-worktree-guard: {what} ({where}), and that is in the vault ({vault.root}). {_WHY} {instead}"


def judge(payload: dict, *, vault_fn=resolve_vault) -> Optional[str]:
    """The refusal for this tool call, or None to let it run."""
    tool = payload.get("tool_name")
    ti = payload.get("tool_input")
    if not isinstance(ti, dict):
        ti = {}
    raw_cwd = payload.get("cwd")
    cwd = Path(os.path.realpath(raw_cwd)) if isinstance(raw_cwd, str) and raw_cwd else Path(os.path.realpath(os.getcwd()))

    if tool == BASH:
        command = ti.get("command")
        if not isinstance(command, str) or "worktree" not in command:
            return None
        spawns = bash_spawns(command, cwd)
        if not spawns:
            return None
        vault = vault_fn()
        if vault is None:
            return None
        for s in spawns:
            if s.dest is not None and under(s.dest, vault.root):
                return _refusal("this command adds a git worktree at a path", s.dest, vault,
                                "Add it outside the vault, from the code repository it belongs to.")
            if (s.cwd is not None or s.git_dir is not None) and _is_vault_repository(vault, s.cwd, s.git_dir):
                return _refusal("this command adds a git worktree to the repository at", s.git_dir or s.cwd, vault,
                                "Edit the vault in place: the daemon commits every change, and undo works from its history.")
        return None

    if tool == SPAWN_TASK:
        target = _resolve(cwd, ti.get("cwd")) if isinstance(ti.get("cwd"), str) and ti.get("cwd") else cwd
        what = "this chip would start its session in a new git worktree under"
        instead = ("Pass `cwd` with the code repository the work belongs to, outside the vault, "
                   "or do the work in this session.")
    elif tool == ENTER_WORKTREE:
        target = _resolve(cwd, ti.get("path")) if isinstance(ti.get("path"), str) and ti.get("path") else cwd
        what = "EnterWorktree would put this session in a git worktree of the repository at"
        instead = ("Edit the vault in place: the daemon commits every change, and undo works from its history. "
                   "If you meant a code repository, change to it first.")
    elif tool in AGENT_TOOLS:
        if ti.get("isolation") != "worktree":
            return None
        target = cwd
        what = 'a subagent with isolation "worktree" would get a git worktree of the repository at'
        instead = "Dispatch it without isolation, or from the code repository it should work in."
    else:
        return None

    if target is None:
        return None
    vault = vault_fn()
    if vault is None:
        return None
    if under(target, vault.root) or _is_vault_repository(vault, target):
        return _refusal(what, target, vault, instead)
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError, RecursionError):
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        reason = judge(payload)
    except Exception:  # noqa: BLE001 — fail open; see the module docstring
        return 0
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
