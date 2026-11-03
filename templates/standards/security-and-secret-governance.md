---
kind: reference
status: active
created: '2026-09-11'
updated: '2026-11-02'
slug: security-and-secret-governance
tags: [security, secrets, always-load]
priority: high
---

# Security and secret governance

The standing rules every session works under. They were scattered across a
brief, the follow-ups file and the repo's CLAUDE.md; this is where the vault
keeps them now. The repos' own AGENTS.md and CLAUDE.md still carry the
commit-time rules, and say the same thing. Drafted by the memory-root trims (agentm-vault plan 05) and
read by you; edit it as the rules change.

## Where secrets live, and the one rule about them

This document names where a secret is kept. It never holds one, and neither
does anything else in this vault.

- `~/.claude/.agentm-config.json` holds the SMTP credential every outgoing
  mail uses — the daily email, the weekly field brief, the scorecard and the
  red-status alert — under `plugins.autonomy.email_smtp_url`. Never print, quote, echo or
  reproduce that value — not in a note, a commit, a log line, a chat reply
  or a test fixture. Name the key when you need to point at it. The mail
  door's own four keys are gone with its transport (agentm-vault plan 16);
  the Drive drop folder needs no credential on any device.
- The `claude` command's login is a session the CLI keeps for itself. The
  nightly enrichment shells out to it; when the session lapses, every call
  fails the same way and the fix is yours, in a terminal (`claude`, then
  `/login`). No agent re-authenticates on your behalf.
- `gh` keeps the GitHub token in the keychain. Agents use `gh`; they never
  read or copy the token.
- The vault is a git repository whose remote is local. Nothing pushes it to
  a hosted remote. Google Drive mirrors the vault folder in place; that is
  file sync, not a git remote, and the no-cloud rule is about the remote.

## What an agent may change in the vault, and where

- `standards/` and the root notes are yours. Under plan authority the
  packaged contract's lines are mirrored into the live `storage-rules.md`;
  nothing else is written here without you saying so in the session.
- `personal/` and `projects/` hold your documents. In a session an agent
  edits them as the task in hand asks, within the grants `index.md` sets.
  Unattended, the night does three things there and nothing else:
  enrichment writes `summary`, `tags` and `importance_proposed` into a
  `personal/` note's frontmatter and never its body; a finished task's
  folder moves whole from `tasks/` to `completed/tasks/` once its tracker
  has read `done` or `dropped` for `task_completed_after_days`, with every
  link into it rewritten; and each project's generated map is rebuilt.
- A pass deletes only what `storage-rules.md` lists under "What the night
  may delete", and writes a manifest before each deletion. Everywhere else,
  purge and deletion are yours alone, with a manifest first.
- A chat surface may write in exactly one place: `agent/inbox/`, the drop
  folder it reaches over the Drive mirror (agentm-vault plan 16). Everywhere
  else it is read-only, whatever tools its environment exposes. A card in the
  drop folder is not filed: it sits `unfiled` and `untrusted` until you read
  it at `/memory inbox` and say where it goes. Nothing files an inbox card
  except you, at the pass — and that holds however the card arrived, so a
  card you paste in by hand from a surface that cannot reach Drive is
  governed by exactly this rule and no other.
- `scripts/health/results/**` and a finished task's records (a `tasks/<name>/`
  whose tracker reads `done`) are frozen evidence.
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
