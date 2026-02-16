# dead-drop-teams

A message-passing server that lets multiple AI agents talk to each other and coordinate work — like a shared inbox for your AI team.

## What It Does

You give tasks to a team of AI agents. They need to communicate: the researcher finds a bug, tells the coder to fix it, the builder runs the tests. **dead-drop-teams** is the mailbox system that makes this work.

- Agents register with a name and role (lead, researcher, coder, builder)
- They send messages to each other through the server
- The team lead automatically gets copied on everything (so nothing happens without visibility)
- Messages are stored in a local SQLite database — no cloud, no external services

## Quick Start

Tell your AI agent:

> "Set up dead-drop-teams from ~/projects/dead-drop-teams. Install it, configure the MCP server in Claude Code settings, and restart."

Your agent will handle the rest. If it needs specifics, here they are:

### Install

```bash
cd ~/projects/dead-drop-teams
uv venv && source .venv/bin/activate && uv pip install -e .
```

Or with pip:

```bash
cd ~/projects/dead-drop-teams
python -m venv .venv && source .venv/bin/activate && pip install -e .
```

Requires Python 3.12+.

### Configure

Add to your MCP config (Claude Code uses `~/.claude/settings.json` under `mcpServers`):

```json
{
  "dead-drop": {
    "command": "/path/to/dead-drop-teams/.venv/bin/dead-drop-teams",
    "args": []
  }
}
```

The database is created automatically at `~/.dead-drop/messages.db`. To change the location, set the `DEAD_DROP_DB_PATH` environment variable.

Restart your AI tool after adding the config.

## How It Works

### Agents and Roles

Each AI session registers as an agent with a role:

| Role | What They Do | Example |
|------|-------------|---------|
| **lead** | Coordinates the team, reviews work, makes decisions | Claude Opus |
| **researcher** | Reads code, finds bugs, writes analysis | Gemini (large context) |
| **coder** | Writes code from specific instructions | Claude Sonnet |
| **builder** | Builds, runs tests, executes commands | Claude Haiku |

### The CC Rule

The lead sees everything. When any agent sends a message to another agent, the lead automatically gets a copy tagged `[CC]`. This means:

- The lead always knows what's happening
- No side conversations the lead can't see
- The lead can spawn ephemeral agents (coder, builder) when a CC tells them work is needed

### Message Flow

```
Researcher finds bug → sends to Lead
Lead sends fix instructions → to Coder
Coder finishes → sends "ready to test" → to Lead (or to Builder, Lead gets CC)
Lead tells Builder → "build and test"
Builder reports → pass/fail → to Lead
```

### Inbox Discipline

- Agents must read their inbox before they can send (enforced by the server)
- This prevents message pileup and ensures agents process incoming work before creating more

## Tools

The server exposes 5 tools via MCP:

| Tool | What It Does |
|------|-------------|
| `register` | Sign in with your name, role, and description |
| `send` | Send a message to another agent (or "all" for broadcast) |
| `check_inbox` | Read your unread messages (marks them as read) |
| `who` | See all registered agents and when they last checked in |
| `get_history` | Get the last N messages (useful after context resets) |

## For Long-Running Tasks

Agents working on builds or tests should write progress to shared log files:

```
.dead-drop/<agent-name>/<task>.log
```

When done, send one summary message. Don't spam the inbox with progress updates.

## Protocol Details

See [docs/PROTOCOL.md](docs/PROTOCOL.md) for the full specification.

## License

MIT — see [LICENSE](LICENSE).
