---
name: compaction-reanchor
description: "SessionStart hook with matcher `compact`, registered once machine-wide, that tells the resuming session it lost its conversation and points it at the compaction marker in the active plan's progress log. Deliberately short — harness-context-session-start fires on the same event and already names where state lives. Silent no-op outside a harness project."
kind: hook
supported_hosts: [claude-code]
version: 0.1.0
---

# compaction-reanchor — the summary is not enough

Fires only on a session that resumes from a compaction (`SessionStart`, matcher `compact`), never on an ordinary start. Claude Code injects its stdout into the post-compaction context.

## Why it is this short

`harness-context-session-start` is registered with matcher `.*`, so it fires on this same event and already prints where the active plan and progress log live. Repeating that here would print it twice on exactly the sessions that are already short on context.

What that hook does not say — and should not, because it fires on *every* start — is that **this particular session lost its conversation.** That is the whole job here, and it is why this is a separate hook rather than a branch inside the other one: the two have different matchers and different audiences.

## What it says

That the previous conversation was discarded rather than paused; that the summary preserves themes and loses specifics — which files were mid-edit, which assertion was failing, which decision was already settled and should not be reopened; and where to find the most recent `## compaction event` marker written by [`compaction-marker`](compaction-marker), above which everything came from the session whose context is gone.

## What changed from the retired per-project version

The predecessor tested for a cwd-relative `.harness/PLAN.md` and hardcoded the three singleton filenames in its output. Both are wrong now: state may live in the vault, and the active plan may be a named `PLAN-<slug>.md`. This asks the seam whether the directory is a harness project at all, names the progress log the seam actually resolved, and says nothing when nothing resolves.

It reads the **event's** `cwd` rather than `$PWD` (DC-6).
