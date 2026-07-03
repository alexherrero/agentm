<!-- mode: how-to -->
# Stand up the memory MCP server

> [!NOTE]
> **Goal:** Connect desktop MCP hosts (Claude Code, Cursor, Goose, Claude Desktop) to your MemoryVault through a single local HTTP daemon.
> **Prereqs:** agentm installed (`install.sh` complete), vault configured, Python 3.11+, `uv` available.

The daemon exposes three tools — `memory_search`, `memory_append`, `memory_forget` — to every host that speaks MCP while routing all writes through one process. See [Memory MCP tools](Memory-MCP-Tools) for full signatures and the soft-delete contract.

## Steps

### 1. Install server dependencies

```bash
uv tool install "agentm[mcp]"   # or: pip install "fastmcp>=3,<4"
```

### 2. Set a bearer token

Pick a random secret and add it to your shell profile so it survives reboots:

```bash
# generate once and paste the output into your ~/.zshrc / ~/.bashrc
openssl rand -hex 32
export AGENTM_MCP_TOKEN="<paste-here>"
```

### 3. Launch the daemon (macOS — launchd)

```bash
bash install.sh --mcp-server agentm
launchctl setenv AGENTM_MCP_TOKEN "$AGENTM_MCP_TOKEN"
launchctl bootstrap gui/$(id -u) ~/.config/agentm/com.agentm.memory-mcp-server.plist
launchctl list | grep agentm   # → a PID, not "-"
```

The daemon binds `127.0.0.1:7821` and restarts automatically at login.

> **Linux / Windows:** run `memory_mcp_server.py` directly and supervise with systemd or NSSM. The stdio shim works cross-platform for hosts that require stdio transport.

### 4. Configure each host

**Claude Code** — add to `.claude/mcp.json` (project) or `~/.claude/mcp.json` (global):

```json
{
  "agentmemory": {
    "url": "http://127.0.0.1:7821/mcp",
    "headers": { "Authorization": "Bearer ${AGENTM_MCP_TOKEN}" }
  }
}
```

**Cursor** — Settings → MCP → Add server → HTTP; URL `http://127.0.0.1:7821/mcp`; Authorization `Bearer ${AGENTM_MCP_TOKEN}`.

**Goose** — `~/.config/goose/config.yaml`:

```yaml
mcp_servers:
  agentmemory:
    transport: http
    url: http://127.0.0.1:7821/mcp
    bearer_token: "${AGENTM_MCP_TOKEN}"
```

**Claude Desktop** — requires stdio transport; use the bundled shim which proxies stdio ↔ the HTTP daemon so the daemon stays the sole writer:

```json
{
  "mcpServers": {
    "agentmemory": {
      "command": "python3",
      "args": ["/path/to/memory_mcp_stdio_shim.py"],
      "env": { "AGENTM_MCP_TOKEN": "<your-token>" }
    }
  }
}
```

Replace `/path/to/` with the absolute path where agentm is installed (e.g. `~/Antigravity/agentm/scripts/`).

### 5. Verify

```bash
python3 scripts/memory_mcp_doctor.py --live
```

All four checks should show `[OK]`. Any `[FAIL]` line includes a remedy command.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `[FAIL] liveness` — connection refused | Run the `launchctl bootstrap` command in step 3; confirm the plist path is correct |
| Host times out on first tool call | Cold-start before launchd has run — confirm `RunAtLoad` + `KeepAlive` in the plist (set by default) |
| 401 Unauthorized | Token mismatch — confirm `AGENTM_MCP_TOKEN` matches what was passed to `launchctl setenv` |
| `[FAIL] origin_guard` — got 200, expected 403 | Origin-validation middleware not loaded; check the server startup log at `/tmp/agentm-memory-mcp.log` |
