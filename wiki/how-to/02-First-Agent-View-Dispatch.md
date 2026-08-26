# Tutorial 2 — Your first Agent View dispatch

> [!NOTE]
> **Goal:** Run a background Agent View session and verify your machine's auth setup.
> **Time:** ~5 minutes.
> **Prereqs:** Claude Code installed and harness installed into a project ([Your first install](01-First-Install)). No vault required.

By the end of this tutorial you'll have run one real background session through Agent View, and you'll know the one setup step every new machine needs before dispatch works at all.

## What Agent View is

Agent View provides background sessions for unattended task execution. It uses `claude --bg` and `claude agents` to run processes independently. The session survives machine sleep and restart. As of Claude Code 2.1.198, it auto-commits, pushes, and opens a draft pull request when it finishes. Read [the autonomy design](agentm-autonomy) for design details.

Agent View is a Claude Code feature — see [Compatibility](Compatibility) for which hosts the harness supports.

## Step 1 — Check whether you're already set up

Run the status command:

```bash
claude auth status
```

Check the command output:

- `loggedIn: true`: Skip to Step 3.
- `loggedIn: false`: Proceed to Step 2.

Fresh machines print `loggedIn: false`. Interactive hosts like IDE extensions and desktop apps inject temporary auth per session.

## Step 2 — Log in once for the base CLI

Run the login command:

```bash
claude auth login
```

Follow the prompts to authenticate the base CLI. A background session spawned with `claude --bg` runs as an isolated process. It does not inherit auth from an interactive host. Persistent login gives the background process its own authentication.

Verify the authentication status:

```bash
claude auth status
```

Confirm the command output shows `loggedIn: true`.

## Step 3 — Dispatch a real background session

Start a background session:

```bash
claude --bg "echo hello from agent view"
```

Check the active sessions:

```bash
claude agents
```

The output lists active background sessions with these fields:

- `sessionId`: The unique session identifier.
- `pid`: The background process ID.
- `startedAt`: The start timestamp.

Run `claude agents --json --all` to view the final status after completion — `--all` includes finished sessions; without it, `--json` only lists active ones.

## What you learned

- **A background session doesn't inherit your interactive host's auth.** `claude --bg` spawns its own process, so it needs `claude auth login` run once on that machine.
- **`claude auth status` tells you where you stand.** Run it before assuming a dispatch will work.
- **This is a one-time step per machine.** Once the login succeeds, every later `claude --bg` dispatch works without repeating it.

## Next

- **Understand the design behind Agent View:** [Autonomy](agentm-autonomy).
- **Install AgentM on your machine:** [Install AgentM machine-wide](Install-Machine-Wide).
