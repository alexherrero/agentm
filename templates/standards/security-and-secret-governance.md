---
kind: reference
status: active
created: '2026-09-11'
updated: '2026-09-11'
slug: security-and-secret-governance
tags: [security, secrets, always-load]
priority: high
---

# Security and secret governance

The standing rules every session works under. They were scattered across a
brief, the follow-ups file and the repo's CLAUDE.md; this is the one place
they live now. Drafted by the memory-root trims (agentm-vault plan 05) and
read by you; edit it as the rules change.

## Where secrets live, and the one rule about them

This document names where a secret is kept. It never holds one, and neither
does anything else in this vault.

- `~/.claude/.agentm-config.json` holds the mail door's SMTP credential
  under `plugins.autonomy.email_smtp_url`. Never print, quote, echo or
  reproduce that value — not in a note, a commit, a log line, a chat reply
  or a test fixture. Name the key when you need to point at it.
- The `claude` command's login is a session the CLI keeps for itself. The
  nightly enrichment shells out to it; when the session lapses, every call
  fails the same way and the fix is yours, in a terminal (`claude`, then
  `/login`). No agent re-authenticates on your behalf.
- `gh` keeps the GitHub token in the keychain. Agents use `gh`; they never
  read or copy the token.
- The vault is a git repository whose remote is local. Nothing pushes it to
  a hosted remote. Google Drive mirrors the vault folder in place; that is
  file sync, not a git remote, and the no-cloud rule is about the remote.

## What an agent may not do to the vault

- `standards/` and the root notes are yours. Under plan authority the
  packaged contract's lines are mirrored into the live `storage-rules.md`;
  nothing else is written here without you saying so in the session.
- `Personal/` and `Projects/` content is yours. Designs shape policy for
  those spaces; an agent does not move, rewrite or delete their files.
- Purge and deletion are yours alone, with a manifest first. Deletion is
  never a policy outcome — a lifecycle pass demotes and archives, it does
  not delete.
- Chat surfaces (Claude.ai, Claude Desktop, the Gemini Gem) are read-only
  on the vault, whatever tools their environment exposes. They suggest an
  entry; you file it.
- `scripts/health/results/**` and `_harness/archive/**` are frozen evidence.
- The daemon's MCP surface stays exactly two tools: search and capture.

## What guards a commit

- The PII pre-push hook is mandatory, and the `pii-scrubber` skill runs
  before any push. `scripts/check-no-pii.sh` is the same detector as a CI
  gate, and `gitleaks` runs on every push in CI.
- Vault paths are resolved at runtime (`harness_memory.vault_path()` for
  the vault, `memory_root()` for the agent's tree — two different
  directories). A cached absolute path is a defect;
  `check-no-hardcoded-vault-path` fails the build on one.
- No attribution trailers on commits. A failing test is fixed in the code,
  never edited to pass.

## Machine state stays out of the vault

Engine files — the recall sidecars, the repo registry, dream exhaust,
journals, cursors, caches — live in the engine state directory
(`~/.local/state/agentm`, or `$AGENTM_STATE_DIR`), never under the memory
root. The vault is the knowledge surface both you and the agents read;
opaque state serves neither of you in Obsidian and costs Drive sync.
