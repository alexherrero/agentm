<!-- mode: decision -->
# ADR 0017 — MCP server design: singleton-HTTP broker, four tools, loopback-first

> [!NOTE]
> **Status:** accepted
> **Date:** 2026-06-17

## Context

V5-9 wraps the memory engine as an MCP server so any MCP host — Cursor, VS Code,
Goose, Claude Desktop — can reach the vault through the one protocol they all speak.
Five design questions needed explicit resolution before the build started:

1. **Which transport, and why does it matter?** The transport choice determines
   whether there is a single write-broker or N concurrent writers.
2. **How many tools, and what are they named?** The incumbent ships five; the
   cautionary cousin ships twenty-plus. A name collision on a major host would
   silently block adoption.
3. **Is forgetting a first-class operation or an edge case?** The vault is synced
   via Google Drive; a hard-delete races the sync client.
4. **Should v1 reach the web surfaces?** Adding a remote tier pulls in OAuth 2.1
   and outbound tunnel infrastructure — is that the right scope?
5. **Which MCP framework, and what is the escape hatch?** The official `mcp` SDK
   is the spec-canonical choice; FastMCP offers bearer-auth OOtB and an in-memory
   test client.

## Decision

### [DC-1] Singleton streamable-HTTP is the broker

One daemon, many sessions (N `Mcp-Session-Id` headers). Every write from every
MCP host funnels through one process — the Phase-1 concurrent-write broker the
V5-0 write protocol was staged to receive.

**Why not stdio transport:** stdio spawns one server process per client. Five hosts
editing memory are five OS processes contending on `vault_mutex`. The lock makes
that *safe*, but it does not make it *single-writer*. One HTTP daemon collapses the
MCP-host fan-out from N writers to one (writer #2 alongside the CLI as writer #1).
The broker property is the reason for the choice; streamable-HTTP happens to also
be the live, spec-supported transport while SSE is deprecated.

**Why not the singleton being a stdio server with an internal multiplexer:** the
stdio shim for Claude Desktop already does this — it proxies stdio to the HTTP
daemon rather than spawning a peer. The shim is a thin compatibility adaptor;
the daemon is the real server.

### [DC-2] Four tools, snake_case — `memory_search`, `memory_recall`, `memory_append`, `memory_forget`

Snake_case names; never dots. Four tools; not fewer, not more.

**Why not dots (`memory.search`):** MCP spec permits dots since late 2025, but
OpenAI-family hosts reject them. A name that breaks on a major host is not a name.
The snake_case rename is a compatibility decision, not a style preference.

**Why not fewer tools:** three tools (`search` / `append` / `recall`) would drop
soft-delete. Forgetting is a hard acceptance criterion (DC-3 below) — it cannot
be a parameter on `append`, because the operation is a status flip on an existing
entry, not a write.

**Why not more tools:** twenty-plus is the cautionary direction — the competitor
that ships that many is the documented bloat target. Four tools cover the complete
surface (search / bundle / append / forget); everything else is a parameter or a
reserved v1.1 verb. `memory_get` (a remote-fetch verb) is the named v1.1 addition,
held until the remote tier needs it.

### [DC-3] Soft-delete is a hard acceptance criterion

`memory_forget` flips `status → deleted` and stamps `deleted_at`. The backing file
is **never unlinked**.

**Why not hard-delete:** three compounding reasons.

1. **Synced-vault resurrection race.** The vault lives on Google Drive. An `unlink`
   on device A propagates a delete to the sync client; if device B has a cached
   local copy, the sync client may resurrect the file from that copy during the
   propagation window. A status flip is a write, not a delete — it propagates as
   a content update, which the sync client handles correctly.
2. **Audit and undo.** A deleted entry whose file is gone cannot be found by
   `include_deleted=True`. Soft-delete preserves the full audit trail; the
   operator can un-delete by flipping `status` back in the vault.
3. **Matched state machine.** The primary research reference ships exactly this
   state machine; deviating would require a strong reason that does not exist.

A version of this server that hard-deletes is not a smaller version of the right
design; it is the wrong design.

### [DC-4] Loopback-first; remote tier deferred

v1 binds `127.0.0.1` only (Unix socket where the host supports it). Remote access
— reaching the daemon from claude.ai, ChatGPT, or another device — is an explicit
`v1.1` scope via an outbound-only tunnel.

**Why not build the remote tier now:** three reasons.

1. **Homelab posture:** the operator's network forbids router port-forwards, public
   DNS entries, and self-hosted VPS. The only compliant remote-access shape is an
   outbound-only tunnel (Tailscale first, NetBird second, Cloudflare a distant
   third given its edge-relay metadata tax). That tunnel pulls in OAuth 2.1 —
   a static bearer token is right on loopback but not across an authenticated
   tunnel endpoint.
2. **Scope:** the loopback daemon already delivers the headline value — vault
   reachable from every desktop MCP host — without the tunnel. Deferring the
   tunnel is not a gap; the web surfaces are honestly unreachable by *any*
   localhost server regardless of transport.
3. **Shape is bounded:** the remote tier's architecture is decided now (loopback
   daemon + outbound tunnel + OAuth 2.1 + optional SOPS/age encryption at rest)
   so adding it later is additive, not a redesign. v1 builds the local half
   knowing the remote half will slot on.

### [DC-5] FastMCP `>=3,<4` with the official `mcp` SDK `<2` as the named fallback

FastMCP (PrefectHQ) is the primary framework; the official `mcp` SDK `<2` is the
explicit fallback, named in comments and pinned.

**Why not the official SDK as primary:** at design time the SDK lacked bearer-auth
OOtB and an in-memory test client that fits `check-all.sh`'s offline-first
discipline. FastMCP provides both, and with a four-tool surface the framework
is cheap to swap — the transport decision (DC-1) is the load-bearing one, not
the framework.

**Why not `fastmcp>=3` unpinned:** FastMCP 3.0 broke unpinned transitive
dependencies. `>=3,<4` pins to the stable minor series while still picking up
patch releases.

**Why the SDK fallback at all:** four tools is a minimal surface. If FastMCP
diverges from the spec or the maintenance trajectory changes, swapping the
framework for the official SDK requires touching only the server skeleton, not
the tool implementations. Naming the fallback in the design is the commitment
to keep that swap cheap.

## Consequences

**Positive:**

- Every desktop MCP host (Claude Code, Cursor, VS Code, Goose, Claude Desktop
  via shim) can reach the vault through a single protocol surface.
- The singleton HTTP daemon is the Phase-1 concurrent-write broker: MCP-host
  writes no longer fan out with client count.
- `memory_recall`'s budgeted, phase-aware bundle is the differentiator over
  generic filesystem MCP servers — it is not reachable via `read_file`.
- Soft-delete leaves the vault safe under synced-client propagation; no
  resurrection race.
- The remote tier's shape is bounded (loopback + outbound tunnel + OAuth) so
  it slots on additively when needed.

**Negative:**

- A long-running daemon is genuinely new operational surface (the first in V5).
  Requires launchd supervision, a liveness probe, and a `doctor` check.
- A cold-start daemon (spawned on demand by `uvx`) gets dropped by host
  timeouts (~10 s). Mitigation: `uv tool install` + launchd `RunAtLoad`+`KeepAlive`.
- A static bearer token is weaker than OAuth for loopback. Acceptable on loopback
  with mandatory Origin-validation; re-examine at the remote-tier boundary.
- Windows daemon parity is unbuilt — launchd is macOS-only. The stdio shim
  works cross-platform meanwhile, so Windows hosts aren't blocked, only
  un-broker'd.

**Load-bearing assumptions with re-audit triggers:**

1. **Streamable-HTTP is the live, supported MCP transport.** Re-audit if the MCP
   spec deprecates streamable-HTTP in favour of a newer transport.
2. **OpenAI-family hosts reject dot-names.** Re-audit if a future spec revision
   mandates dot-names or the major host changes its name-parsing.
3. **FastMCP `>=3,<4` is API-stable.** Re-audit at the next FastMCP major version;
   check whether the official SDK has closed the bearer-auth and in-memory-client
   gaps before upgrading.
4. **The vault is the sole sink for soft-deleted entries** (no external backup
   service relies on hard-deletes for cleanup). Re-audit if a storage backend
   or compliance requirement introduces a hard-delete obligation.
5. **The operator's homelab posture (no port-forwards / public DNS / VPS) is
   stable.** Re-audit when planning the remote tier — a changed posture may
   change the tunnel ranking (Tailscale → NetBird → Cloudflare).

## Related

- [ADR 0012 — Vault-write protocol](0012-vault-write-protocol) — V5-0; the
  write protocol (`vault_lock.py`) this daemon composes as writer #2.
- [ADR 0013 — Storage-seam fail-loud selection](0013-storage-seam-fail-loud-selection) — V5-1; the seam the daemon reads/writes through.
- [Memory MCP tools reference](Memory-MCP-Tools) — signatures, behavioral guarantees, soft-delete contract.
- [Stand up memory MCP server](Stand-Up-Memory-MCP-Server) — how-to for connecting each host.
