# Dead Drop Teams — Current State

> Updated: 2026-02-16

## What Is This

A shared MCP server for cross-agent communication. AI agents (Claude Code, Gemini CLI, Codex CLI) connect over HTTP, register themselves, and exchange messages through a SQLite database. When a message arrives, the server pushes a notification to the recipient so they check their inbox automatically.

## Architecture

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│  Claude  │     │  Gemini  │     │  Codex   │
│  Code    │     │   CLI    │     │   CLI    │
└────┬─────┘     └────┬─────┘     └────┬─────┘
     │                │                │
     │  Streamable HTTP (port 9400)    │
     ▼                ▼                ▼
┌─────────────────────────────────────────────┐
│        dead-drop-teams HTTP server          │
│     (FastMCP + uvicorn on 127.0.0.1:9400)   │
├─────────────────────────────────────────────┤
│  Connection Registry (agent → session)      │
│  Push: tools/list_changed per-session       │
│  Dynamic tool descriptions (unread alerts)  │
├─────────────────────────────────────────────┤
│            SQLite WAL database              │
│  ┌─────────┐ ┌──────────┐ ┌──────────────┐ │
│  │ agents  │ │ messages │ │broadcast_reads│ │
│  └─────────┘ └──────────┘ └──────────────┘ │
└─────────────────────────────────────────────┘
```

**Previous:** Each client spawned its own stdio MCP process. No push — agents had to manually poll `check_inbox`.

**Now:** Single HTTP server process. All clients connect to `http://localhost:9400/mcp`. Server pushes `notifications/tools/list_changed` when messages arrive. Client re-fetches tools, sees "YOU HAVE N UNREAD MESSAGES" in `check_inbox` description, AI calls it automatically.

## How Push Notifications Work

```
1. Agent A calls send(to_agent="agent-b", message="...")
2. Server stores message in SQLite
3. Server looks up Agent B's session in the connection registry
4. Server calls session_B.send_tool_list_changed()
5. Agent B's MCP client receives the notification, calls tools/list
6. Server returns check_inbox with modified description:
   "*** YOU HAVE 1 UNREAD MESSAGE(S) from agent-a *** Call check_inbox now!"
7. Agent B's AI reads the description and calls check_inbox
```

This works because `notifications/tools/list_changed` is the ONE notification that Claude Code, Gemini CLI, and Codex CLI all respond to.

## Running Services

### Dead Drop HTTP Server

- **Process:** launchd daemon (`com.dead-drop.server`)
- **Plist:** `~/Library/LaunchAgents/com.dead-drop.server.plist`
- **Endpoint:** `http://127.0.0.1:9400/mcp`
- **Logs:** `~/.dead-drop/server.log`
- **Auto-start:** Yes (RunAtLoad + KeepAlive)
- **Transport:** Streamable HTTP (MCP 2025-03-26)

```bash
# Start/restart
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.dead-drop.server.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.dead-drop.server.plist

# Check status
curl -s http://127.0.0.1:9400/mcp -X POST \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"check","version":"1.0"}}}'

# View logs
tail -f ~/.dead-drop/server.log
```

## Client Configurations

### Claude Code (DONE)

File: `~/.claude/settings.json`

```json
{
  "mcpServers": {
    "dead-drop": {
      "type": "http",
      "url": "http://localhost:9400/mcp"
    }
  }
}
```

### Gemini CLI (TODO)

Needs `httpUrl` field in Gemini's MCP config (exact location TBD — check Gemini CLI docs).

### Codex CLI (TODO)

Needs `url` field in Codex's MCP config (exact location TBD — check Codex CLI docs).

## Server Capabilities

| Capability | Value |
|------------|-------|
| Protocol | MCP 2025-03-26 |
| Transport | Streamable HTTP |
| tools.listChanged | **true** |
| Port | 9400 (configurable via `DEAD_DROP_PORT`) |
| SQLite mode | WAL (concurrent reads) |
| Backward compat | `dead-drop-teams` (no args) still runs stdio |

## Tools

| Tool | Params | Push? | Description |
|------|--------|-------|-------------|
| `register` | `agent_name`, `role`, `description` | — | Register + capture session for push |
| `send` | `from_agent`, `to_agent`, `message`, `cc` | YES | Send message, notify recipients |
| `check_inbox` | `agent_name` | — | Get unread messages, mark read |
| `set_status` | `agent_name`, `status` | — | Update agent status |
| `get_history` | `count` | — | Last N messages |
| `deregister` | `agent_name` | — | Remove agent + cleanup session |
| `who` | — | — | List agents (includes `connected` field) |

## Key Files

| File | Purpose |
|------|---------|
| `src/dead_drop/server.py` | Main server — tools, push, HTTP transport |
| `pyproject.toml` | Package config, entry point |
| `~/.dead-drop/messages.db` | SQLite database |
| `~/.dead-drop/server.log` | Server logs |
| `~/Library/LaunchAgents/com.dead-drop.server.plist` | launchd daemon config |
| `~/.claude/settings.json` | Claude Code MCP config |
| `docs/PROTOCOL.md` | Agent-facing protocol rules |
| `docs/ARCHITECTURE.md` | Original architecture doc (pre-HTTP) |
| `scripts/poll_inbox.sh` | Legacy polling script (superseded by push) |
| `scripts/check-inbox.sh` | Legacy Claude Code hook (superseded by push) |

## Database

- **Path:** `~/.dead-drop/messages.db`
- **Mode:** WAL (set on every connection)
- **Tables:** `agents`, `messages`, `broadcast_reads`
- **Migrations:** Run automatically on server start

## What's Left

- [ ] Configure Gemini CLI to connect via HTTP
- [ ] Configure Codex CLI to connect via HTTP
- [ ] Test push notifications end-to-end with real Claude Code session (restart required)
- [ ] Update `docs/ARCHITECTURE.md` to reflect HTTP architecture
- [ ] Consider removing legacy polling scripts once push is verified
