#!/usr/bin/env python3
"""merge-settings-fragment.py — idempotently deep-merge a hook settings
fragment into a target's .claude/settings.json.

The toolkit installer shells out to this script when installing a `kind: hook`
customization. Each hook customization ships a `settings-fragment-{bash,pwsh}.json`
file containing a snippet like:

  {
    "hooks": {
      "PreToolUse": [
        { "matcher": ".*", "hooks": [ { "type": "command", "command": "..." } ] }
      ]
    }
  }

This script reads:
  - existing settings.json (or creates an empty {} if it doesn't exist)
  - fragment JSON file

And merges fragment.hooks.<event> entries into existing.hooks.<event>, skipping
any entry whose `command` is already present in the existing array (idempotent).

Other top-level keys in existing settings.json are preserved untouched.

Why python3 (not jq): python3 is already a hard prereq of the installer, which
cannot run without it at all. Adding jq would be a new dependency; reusing
python3 keeps the prereq surface flat.

Usage:
  python3 merge-settings-fragment.py <settings_json_path> <fragment_json_path>
                                      [--command <CMD>]

  --command <CMD>  Rewrite every inner hook's `command` to <CMD> before merging.
                   Used by the `--scope user` installer to absolutize a
                   fragment's project-relative `bash .claude/hooks/<name>.sh`
                   to the user-scope dir layout `bash ~/.claude/hooks/<name>/<name>.sh`.
                   Dedup uses the rewritten command, so re-running with the same
                   --command is idempotent. (One fragment ships one hook script,
                   so a single override applies to all its event entries.)

Exit:
  0  merged successfully (or no-op if fragment entries were already present)
  1  setup error (fragment file missing, JSON parse error, etc.)
  2  argument error
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def has_command_match(existing_entries: list, new_command: str) -> bool:
    """Walk an existing hooks.<event> array; return True iff any entry's
    inner hooks[*].command equals the new entry's command. Idempotence check."""
    for entry in existing_entries:
        if not isinstance(entry, dict):
            continue
        inner_hooks = entry.get("hooks", [])
        if not isinstance(inner_hooks, list):
            continue
        for inner in inner_hooks:
            if not isinstance(inner, dict):
                continue
            if inner.get("command") == new_command:
                return True
    return False


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="merge-settings-fragment.py",
        description="Idempotently deep-merge a hook settings fragment into settings.json.",
        add_help=True,
    )
    parser.add_argument("settings_json_path")
    parser.add_argument("fragment_json_path")
    parser.add_argument(
        "--command", default=None,
        help="rewrite every inner hook's `command` to this value before merge "
             "(used by --scope user to absolutize the hook script path)",
    )
    try:
        args = parser.parse_args(argv[1:])
    except SystemExit:
        # argparse exits 2 on bad args; preserve that contract.
        return 2

    settings_path = Path(args.settings_json_path)
    fragment_path = Path(args.fragment_json_path)
    command_override = args.command

    if not fragment_path.is_file():
        print(f"merge-settings-fragment: fragment not found: {fragment_path}", file=sys.stderr)
        return 1

    try:
        fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"merge-settings-fragment: invalid JSON in fragment: {e}", file=sys.stderr)
        return 1

    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as e:
            print(
                f"merge-settings-fragment: invalid JSON in existing settings: {e}",
                file=sys.stderr,
            )
            return 1
    else:
        existing = {}

    # Deep-merge fragment.hooks.<event> into existing.hooks.<event>, skipping
    # entries whose command is already present.
    fragment_hooks = fragment.get("hooks", {})
    if not isinstance(fragment_hooks, dict):
        print("merge-settings-fragment: fragment.hooks must be an object", file=sys.stderr)
        return 1

    added_count = 0
    for event_name, fragment_entries in fragment_hooks.items():
        if not isinstance(fragment_entries, list):
            print(
                f"merge-settings-fragment: fragment.hooks.{event_name} must be an array",
                file=sys.stderr,
            )
            return 1
        existing.setdefault("hooks", {}).setdefault(event_name, [])
        existing_entries = existing["hooks"][event_name]
        for entry in fragment_entries:
            if not isinstance(entry, dict):
                continue
            inner_hooks = entry.get("hooks", [])
            if not isinstance(inner_hooks, list) or not inner_hooks:
                continue
            # --command: rewrite every inner hook's command to the override (a
            # fragment ships one hook script, so all event entries point at it).
            # The rewrite happens before the dedup check so re-running with the
            # same --command is idempotent.
            if command_override is not None:
                for inner in inner_hooks:
                    if isinstance(inner, dict) and "command" in inner:
                        inner["command"] = command_override
            # Use the first inner hook's command as the dedup key.
            first_command = inner_hooks[0].get("command")
            if not isinstance(first_command, str):
                continue
            if has_command_match(existing_entries, first_command):
                continue
            existing_entries.append(entry)
            added_count += 1

    # Write back with stable 2-space indent + trailing newline.
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(existing, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    # Use POSIX-style forward slashes in the output message regardless of OS,
    # so the smoke test regex matches identically on Windows. Path.__str__()
    # uses native separators (backslash on Windows); as_posix() forces /.
    display_path = settings_path.as_posix()
    if added_count == 0:
        print(f"    kept    {display_path} (fragment entries already present)")
    else:
        print(f"    merged  {display_path} (+{added_count} hook entry)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
