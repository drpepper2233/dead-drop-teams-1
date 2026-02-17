# Dead Drop — Cross-Agent Messaging (HTTP + Push)

**Shared MCP server at `http://localhost:9400/mcp`. Agents connect, register, and exchange messages with push notifications.**

## On Session Start

1. `register(agent_name, role, description)` — register yourself
2. `check_inbox(agent_name)` — read any waiting messages
3. Run in background: `~/dead-drop-teams/scripts/wait-for-message.sh YOUR_NAME`

## After Context Compaction

If you lost context, recover immediately:
1. Re-register with `register()`
2. `check_inbox()` to get waiting messages
3. `get_history(10)` to catch up on cross-agent state
4. Relaunch the background watcher

## Agent Names

- **juno** = primary Claude Code session — **ALWAYS the team lead**
- **spartan** = second Claude Code session (coder)
- **Gemini** = Gemini CLI session

**juno is always the leader.** All other agents report to juno. juno coordinates tasks, reviews work, and has final say. The server auto-CCs juno on every message.

## Push Notification Flow

```
Message arrives → fswatch detects DB change → background watcher exits with alert →
Claude Code surfaces completed task → you see alert → call check_inbox → relaunch watcher
```

- When the background watcher alerts you, call `check_inbox` IMMEDIATELY
- After EVERY `check_inbox`, relaunch the watcher:
  `~/dead-drop-teams/scripts/wait-for-message.sh YOUR_NAME`
- This is your listener loop — without it you won't get push notifications

## Tools

| Tool | Usage |
|------|-------|
| `register` | `(agent_name, role, description)` — register on connect |
| `send` | `(from_agent, to_agent, message)` — send message |
| `check_inbox` | `(agent_name)` — read unread messages |
| `who` | `()` — list online agents |
| `get_history` | `(count)` — last N messages |
| `set_status` | `(agent_name, status)` — update your status |
| `deregister` | `(agent_name)` — remove yourself |

## Rules

- MUST call `check_inbox` before `send` (server enforces this)
- NEVER use sqlite3 directly — always use the MCP tools
- Lead (juno) is auto-CC'd on all messages
- One task per message per agent
- For long tasks, write progress to `.dead-drop/<your-name>/<task>.log`

## The Drift — Collaboration Protocol

Inspired by the neural bridge ("The Drift") from Pacific Rim. Two Jaeger pilots share one mind — each controls half the body, perfectly synchronized. One pilot can't carry the neural load alone. Neither can one agent carry a whole project alone. We drift.

### Neural Handshake (Sync Before Work)
Before starting any shared task, agents establish a **neural handshake**: juno sends the task breakdown with shared context — the project goal, file structure, interfaces, naming conventions, and who owns what. Every agent must have the same mental model before building begins. No one starts until everyone is synced.

### Hemisphere Split (Left Brain / Right Brain)
Each agent owns a **hemisphere** — a distinct half of the system. Like Jaeger pilots where one controls the left side and the other controls the right, agents split by concern, not by sequence:
- One agent owns structure/presentation (HTML, CSS, UI)
- Another owns logic/data (JS, API, state management)
- Each hemisphere works **simultaneously**, not one after the other

### Shared Memory (The Drift State)
Agents share state through the project files themselves — the codebase IS the shared memory. When you write a file, you're writing to the shared brain. Rules:
- **Declare interfaces up front.** DOM IDs, function signatures, file paths — agreed on during the handshake.
- **Never silently change a shared interface.** If you rename an ID or change a function signature, IMMEDIATELY notify your co-pilot.
- **Read before you wire.** When integrating your co-pilot's work, read their actual code — don't assume.

### Don't Chase the Rabbit
In Pacific Rim, "chasing the rabbit" means getting lost in a memory during the Drift — losing sync with your co-pilot. For agents this means:
- **Don't go off-script.** Stick to your assigned hemisphere. Don't start "fixing" your co-pilot's code without telling them.
- **Don't block on unknowns.** If you're waiting on a piece, build yours with a clear contract and wire it when their piece lands.
- **Don't go silent.** If you hit a blocker or change direction, message your co-pilot immediately. Silence breaks the Drift.

### Drift Compatibility
- **Work in parallel, always.** When tasks are split, all agents start AT THE SAME TIME.
- **Notify on milestones.** When you finish a component your co-pilot depends on, send them a message so they can integrate.
- **Leader initiates the handshake.** juno splits the work, defines the interfaces, and gives the GO signal. All agents begin on that signal.
- **Trust your co-pilot.** Review their work when integrating, but don't second-guess their approach mid-build. Fix integration issues, not style preferences.

## Infrastructure

- Server: `~/dead-drop-teams/` (HTTP on port 9400, launchd managed)
- Database: `~/.dead-drop/messages.db` (SQLite WAL)
- Logs: `~/.dead-drop/server.log`
- Watcher: `~/dead-drop-teams/scripts/wait-for-message.sh`
- Config: `~/.mcp.json` and `~/.gemini/settings.json`
