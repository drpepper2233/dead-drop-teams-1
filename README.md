# dead-drop-teams

A shared MCP server that lets multiple AI agents (Claude Code, Gemini CLI, Codex CLI) talk to each other with **push notifications** — no polling required.

## What It Does

You give tasks to a team of AI agents. They need to communicate: the researcher finds a bug, tells the coder to fix it, the builder runs the tests. **dead-drop-teams** is the message bus that makes this work.

- Single HTTP server, multiple clients connect simultaneously
- Push notifications via MCP `tools/list_changed` + `send_log_message`
- Background file watcher (`fswatch`) wakes up idle agents automatically
- Messages stored in local SQLite (WAL mode) — no cloud, no external services
- Team lead auto-CC'd on all messages for full visibility

## Architecture

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│  Claude  │     │  Claude  │     │  Gemini  │
│  (juno)  │     │ (spartan)│     │   CLI    │
└────┬─────┘     └────┬─────┘     └────┬─────┘
     │                │                │
     │  Streamable HTTP (port 9400)    │
     ▼                ▼                ▼
┌─────────────────────────────────────────────┐
│        dead-drop-teams HTTP server          │
│     (FastMCP + uvicorn on 127.0.0.1:9400)   │
├─────────────────────────────────────────────┤
│  Connection Registry (agent → session)      │
│  Push: tools/list_changed + log_message     │
│  Dynamic tool descriptions (unread alerts)  │
├─────────────────────────────────────────────┤
│            SQLite WAL database              │
│  ┌─────────┐ ┌──────────┐ ┌──────────────┐ │
│  │ agents  │ │ messages │ │broadcast_reads│ │
│  └─────────┘ └──────────┘ └──────────────┘ │
└─────────────────────────────────────────────┘
```

## How Push Notifications Work

```
1. Agent A calls send(to_agent="spartan", message="...")
2. Server stores message in SQLite
3. Server pushes tools/list_changed + log_message to spartan's session
4. Spartan's background watcher (fswatch) detects DB change
5. Watcher exits with alert: "YOU HAVE 1 UNREAD MESSAGE(S)"
6. Claude Code surfaces the completed background task
7. Agent sees the alert and calls check_inbox automatically
```

No polling. Event-driven at every layer — same pattern as an Android app receiving an HTTP POST.

## Quick Start

### Install

```bash
git clone git@gitlab.com:Jessehampton05/dead-drop-teams.git ~/dead-drop-teams
cd ~/dead-drop-teams
uv venv && source .venv/bin/activate && uv pip install -e .
```

### Start the HTTP Server

```bash
# One-time: install the launchd daemon
cp ~/dead-drop-teams/com.dead-drop.server.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.dead-drop.server.plist
```

Or run manually:

```bash
dead-drop-teams --http
```

Server runs on `http://127.0.0.1:9400/mcp`.

### Configure Clients

**Claude Code** (`~/.claude/settings.json` or `~/.mcp.json`):

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

**Gemini CLI** (`~/.gemini/settings.json`):

```json
{
  "mcpServers": {
    "dead-drop": {
      "httpUrl": "http://localhost:9400/mcp",
      "trust": true
    }
  }
}
```

Restart your AI tool after adding the config.

### Agent Prompt

Give your agent this on startup:

```
You have MCP tools from a server called "dead-drop".

1. register(agent_name="YOUR_NAME", role="coder", description="what you do")
2. check_inbox(agent_name="YOUR_NAME")
3. Run in background: ~/dead-drop-teams/scripts/wait-for-message.sh YOUR_NAME

Rules:
- Always use YOUR_NAME as agent_name
- You must check_inbox before you can send
- When the background watcher alerts you, call check_inbox immediately
- After each check_inbox, relaunch the background watcher
```

## Tools

| Tool | Params | Description |
|------|--------|-------------|
| `register` | `agent_name`, `role`, `description` | Register + capture session for push |
| `send` | `from_agent`, `to_agent`, `message`, `cc` | Send message, notify recipients |
| `check_inbox` | `agent_name` | Get unread messages, mark read |
| `set_status` | `agent_name`, `status` | Update agent status |
| `get_history` | `count` | Last N messages |
| `deregister` | `agent_name` | Remove agent + cleanup session |
| `who` | — | List agents (includes `connected` field) |

## Roles

| Role | Lifecycle | Function |
|------|-----------|----------|
| `lead` | Persistent | Coordinates, reviews, routes tasks |
| `researcher` | Persistent | Reads source, finds bugs, writes analysis |
| `coder` | Ephemeral | Writes code from specific instructions |
| `builder` | Ephemeral | Builds, runs tests, executes commands |

## The CC Rule

The lead sees everything. When any agent sends a message, the lead automatically gets a CC copy. No side conversations without visibility.

## Key Files

| File | Purpose |
|------|---------|
| `src/dead_drop/server.py` | Main server — tools, push, HTTP transport |
| `scripts/wait-for-message.sh` | Background watcher for idle agents |
| `tests/test_push_e2e.py` | End-to-end push notification test |
| `docs/PROTOCOL.md` | Agent-facing protocol rules |
| `~/.dead-drop/messages.db` | SQLite database |
| `~/.dead-drop/server.log` | Server logs |

## Requirements

- Python 3.12+
- macOS (for `fswatch` and `launchd`)
- `fswatch` (`brew install fswatch`)

## License

MIT
