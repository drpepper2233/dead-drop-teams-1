# Architecture

## Overview

dead-drop-teams is a FastMCP stdio server backed by SQLite. AI agents connect via MCP, register themselves, and exchange messages through a shared database. No cloud, no network — just a local DB file.

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│  Lead    │     │Researcher│     │  Coder   │
│ (Opus)   │     │ (Gemini) │     │ (Sonnet) │
└────┬─────┘     └────┬─────┘     └────┬─────┘
     │                │                │
     │    MCP stdio   │   MCP stdio    │
     ▼                ▼                ▼
┌─────────────────────────────────────────────┐
│           dead-drop-teams server            │
│              (FastMCP stdio)                │
├─────────────────────────────────────────────┤
│              SQLite database                │
│  ┌─────────┐ ┌──────────┐ ┌──────────────┐ │
│  │ agents  │ │ messages │ │broadcast_reads│ │
│  └─────────┘ └──────────┘ └──────────────┘ │
└─────────────────────────────────────────────┘
```

Each Claude Code session (or Gemini session, etc.) gets its own MCP server process. All processes share the same SQLite database file — SQLite's file-level locking handles concurrency.

## Database Schema

### agents

| Column | Type | Purpose |
|--------|------|---------|
| `name` | TEXT PK | Agent identifier (e.g. `claude-lead`) |
| `registered_at` | TEXT | First registration timestamp |
| `last_seen` | TEXT | Last activity timestamp |
| `last_inbox_check` | TEXT | Last `check_inbox` call |
| `role` | TEXT | `lead`, `researcher`, `coder`, `builder` |
| `description` | TEXT | What this agent does |

### messages

| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER PK | Auto-incrementing message ID |
| `from_agent` | TEXT | Sender name |
| `to_agent` | TEXT | Recipient name (or `all` for broadcast) |
| `content` | TEXT | Message body |
| `timestamp` | TEXT | ISO timestamp |
| `read_flag` | INTEGER | 0 = unread, 1 = read |
| `is_cc` | INTEGER | 0 = direct, 1 = carbon copy |
| `cc_original_to` | TEXT | Original recipient (for CC messages) |

### broadcast_reads

| Column | Type | Purpose |
|--------|------|---------|
| `agent_name` | TEXT | Which agent read it |
| `message_id` | INTEGER | Which broadcast message |

Composite PK on (agent_name, message_id). Allows per-agent tracking of broadcast read status without a global flag.

## Auto-CC Protocol

The lead agent (registered with `role='lead'`) gets automatic carbon copies of all inter-agent messages.

```
gemini-researcher → sonnet-coder: "Apply fix to line 42"
                  → claude-lead:  [CC] "Apply fix to line 42" (originally to: sonnet-coder)
```

Logic in `send()`:
1. Look up agent with `role='lead'` in the agents table
2. If sender is not the lead AND recipient is not the lead → auto-CC
3. CC message is a separate row with `is_cc=1` and `cc_original_to` set
4. Explicit `cc` parameter can add additional CC recipients

The lead never needs to poll or ask "what happened?" — everything flows through their inbox automatically.

## Inbox Discipline (Send Blocking)

Before allowing a `send()`, the server checks if the sender has unread messages:

```python
if unread_direct + unread_broadcast > 0:
    return "BLOCKED: You have N unread message(s). Call check_inbox first."
```

This enforces a read-before-write discipline:
- Agents can't fire-and-forget messages while ignoring incoming work
- Prevents message storms where agents talk past each other
- Forces sequential processing: read → process → respond

## Agent Lifecycle

### Persistent agents
- **Lead** — runs in the main Claude Code session, lives for the whole work session
- **Researcher** — runs in a separate terminal (e.g. Gemini with large context), lives for the session

### Ephemeral agents
- **Coder** — spawned by lead for a specific code change, dies after reporting back
- **Builder** — spawned by lead for a build/test task, dies after reporting back

Ephemeral agents follow the pattern:
1. Lead sends task message to dead-drop
2. Lead spawns agent (e.g. via Claude Code Task tool)
3. Agent registers, checks inbox, gets the task
4. Agent executes, reports result via `send()`
5. Agent process ends
6. Next task = new spawn (fresh context, no stale state)

## Shared Progress Files

For tasks that take time (builds, benchmarks), agents write to project-local log files instead of sending chatty message updates:

```
<project-root>/.dead-drop/<agent-name>/<task-slug>.log
```

- Each agent owns their directory (matches registered name)
- Other agents can `tail` the log for live progress
- When done, send ONE summary message with pass/fail
- Keeps the message inbox clean for decisions, not noise

## Files

| Path | Purpose |
|------|---------|
| `src/dead_drop/server.py` | MCP server — all 5 tools, DB init, migrations |
| `scripts/poll_inbox.sh` | Shell script that polls inbox every N seconds (runs in terminal) |
| `scripts/check-inbox.sh` | Claude Code hook — fires on UserPromptSubmit, debounced nag |
| `docs/PROTOCOL.md` | Agent-facing protocol rules (register, inbox discipline, message format) |
| `docs/ARCHITECTURE.md` | This file |

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DEAD_DROP_DB_PATH` | `~/.dead-drop/messages.db` | SQLite database location |

## Migrations

The server runs migrations on startup (`init_db()`). Existing databases are upgraded automatically:
- Adds `last_inbox_check` column to agents (if missing)
- Adds `is_cc`, `cc_original_to` columns to messages (if missing)
- Adds `role`, `description` columns to agents (if missing)

New databases get the full schema from the CREATE TABLE statements.
