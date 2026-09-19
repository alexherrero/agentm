# How to reach your memory from Claude Desktop

> [!NOTE]
> **Status: implemented** — shipped by agentm-vault plan 12, task 8 (`tasks/160-finish-the-surfaces`).
> **Goal:** Give the Claude Desktop app the same two memory tools Claude Code already has — search and capture — against the same daemon and the same vault.
> **Prereqs:** The daemon installed and running (`agentmd status`). Claude Desktop 2.x. Nothing else: the bridge below ships with agentm and has no dependencies beyond the Python already on your machine.

Claude Code points straight at the daemon, because it can take a plain URL:

```bash
claude mcp add --transport http agentmemory http://127.0.0.1:7821/mcp
```

Claude Desktop cannot. Its custom-connector form requires an `https` URL — the app says so itself — and the daemon serves plain HTTP on loopback, where TLS would add a certificate to manage and protect nothing that is not already inside your own machine. Desktop's other door is its config file, and that one speaks stdio. So Desktop gets a small shim that carries messages between the two.

## Steps

### 1. Check the daemon is up

```bash
curl -fsS http://127.0.0.1:7821/health
```

If that fails, the rest of this will not work and Desktop will show you an error rather than a tool list. `agentmd status` says more.

### 2. Find the two absolute paths you need

Desktop launches the bridge itself, from its own working directory, so both paths in the entry have to be absolute.

```bash
which python3
```

```bash
python3 -c "import pathlib, subprocess; print(pathlib.Path(subprocess.run(['git','rev-parse','--show-toplevel'],capture_output=True,text=True,cwd='.').stdout.strip())/'harness/skills/memory/scripts/mcp_stdio_bridge.py')"
```

Run the second one from anywhere inside your agentm checkout.

### 3. Add the entry

Open `~/Library/Application Support/Claude/claude_desktop_config.json` — on Windows it is `%APPDATA%\Claude\claude_desktop_config.json`. If the file does not exist, create it with just the object below.

Add one entry under `mcpServers`, using the two paths from step 2:

```json
{
  "mcpServers": {
    "agentmemory": {
      "command": "/usr/bin/python3",
      "args": ["/ABSOLUTE/PATH/TO/harness/skills/memory/scripts/mcp_stdio_bridge.py"]
    }
  }
}
```

If the file already has other servers in it, add `agentmemory` alongside them and leave the rest alone.

The bridge takes an optional `--url` if your daemon is not on the default port:

```json
"args": ["/ABSOLUTE/PATH/.../mcp_stdio_bridge.py", "--url", "http://127.0.0.1:9999/mcp"]
```

That URL is pinned to `http` or `https` on this machine, and the bridge refuses to start on anything else. The pin is not about your typing: `urllib`'s default handlers would serve a `file:` URL, which would make the bridge read that file and write it into Desktop — and one of the files it could be pointed at holds the mail door's credential.

### 4. Restart Claude Desktop

Quit it fully — the app keeps running when the window closes, so use Quit rather than closing the window. MCP servers are started once at launch and the new entry is not read until then.

### 5. Check it took

In a new conversation, ask Desktop what tools it has. You should see two, `memory_search` and `memory_capture`, and no others. Two is the whole surface by design: the deep search is not on it and is not injected.

Or from a terminal, without involving the app:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 harness/skills/memory/scripts/mcp_stdio_bridge.py
```

That should print one line naming exactly those two tools.

The doctor has a row for this too, which says whether an agentm entry is wired in without printing your config:

```bash
python3 scripts/machinery_doctor.py
```

Look for `claude-desktop-entry`. `UNVERIFIED` means no entry yet, which is not a failure — it is the state of a machine where you have not done this.

## What Desktop can and cannot do with it

It can search your memory and capture to it. A capture from Desktop lands the same way any capture does, and a search returns the card's readable head — title, type, summary, importance, status, lifecycle, project — then the path and a snippet. It never returns the note's body, or `why`; a surface that wants those opens the file.

It cannot reach the deep search, which is not on the MCP surface at all, and it cannot write anywhere except through capture.

Recalls from Desktop are recorded under their own surface, so the per-surface counts in your morning note tell Desktop's reads apart from Claude Code's.

## If something is wrong

**Desktop shows no tools at all.** The entry did not parse or the app was not fully quit. A single trailing comma costs you every server in the file: Desktop starts with none of them rather than complaining about the one that is malformed. Check it parses:

```bash
python3 -m json.tool < ~/Library/Application\ Support/Claude/claude_desktop_config.json > /dev/null && echo ok
```

**Desktop shows an error mentioning the daemon.** That is the bridge telling you the truth: it could not reach `127.0.0.1:7821`. Start the daemon.

**The tools are there but every search comes back empty.** The daemon is answering for the vault its own config names, which may not be the one you expect. `agentmd status` prints which.

## Related

- [Open the mail door](Open-The-Mail-Door) — the other way a chat surface keeps something, for surfaces that cannot run a local process at all.
- [Memory daemon](Memory-Daemon) — what the daemon is, what it serves, and on which port.
- [AgentM Vault](agentm-vault) § Surfaces — why the surface is exactly two tools.
