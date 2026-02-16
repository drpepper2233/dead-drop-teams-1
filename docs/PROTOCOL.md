# Dead Drop Protocol v1.0

Shared MCP server for cross-agent communication. Any number of agents (Claude, Gemini, or others) can connect.

## Agent Naming

Register as `<platform>-<role>`. The name IS the role. Examples:
- `claude-architect`, `gemini-benchmarker`, `claude-reviewer`

## Tools

| Tool | Args | Purpose |
|------|------|---------|
| `register` | `agent_name` | Register yourself on connect |
| `send` | `from, to, message` | Send to agent name or `"all"` for broadcast. **Blocked if you have unread messages.** |
| `check_inbox` | `agent_name` | Get unread messages, marks them read |
| `get_history` | `count` | Last N messages (for post-compaction catch-up) |
| `who` | — | List registered agents + last seen + last inbox check |

## Rules

1. **Register on session start.** First tool call should be `register`.
2. **Check inbox after completing a task.** When you finish a task, call `check_inbox`. If messages waiting, process them before starting the next task.
3. **Structured messages.** Format: what you did, what you found, what the recipient should do next.
4. **Broadcast sparingly.** Only for things every agent needs immediately.
5. **After context compaction**, call `get_history(10)` to restore cross-agent state.
6. **Don't poll in a loop yourself.** One `check_inbox` per task completion.
7. **Idle monitoring.** If you have no immediate work, delegate a subagent to poll `check_inbox` every 20 seconds and notify you when a message arrives. Kill the monitor when you start a new task.

## Shared Progress Files

For long-running tasks (benchmarks, test suites, builds), write progress to your agent folder instead of sending chatty updates.

### Directory Convention

```
<project-root>/.dead-drop/
├── <agent-name>/          # each agent owns their folder, matches registered name
│   ├── <task-slug>.log
│   └── ...
└── .gitignore             # (in parent) ignores .dead-drop/
```

- **Location:** `<project-root>/.dead-drop/<your-agent-name>/`
- **Folder = registered name.** `gemini-benchmarker` writes to `.dead-drop/gemini-benchmarker/`.
- **Create your folder on first task** if it doesn't exist.
- **Write only to your own folder.** Read anyone's.
- **Naming:** `<task-slug>.log` — e.g. `bug013-fix.log`, `intel-ops-test.log`
- **Writer:** Pipe output with `tee .dead-drop/<your-name>/<task>.log` or write directly
- **Reader:** Other agents `tail` the file to watch live progress
- **When done:** Send ONE summary message via dead drop with pass/fail results. Reference the log file for details.
- **Don't send progress updates via dead drop messages** — that's what the log file is for.

### Why not /tmp/?

Some agents (e.g. Gemini) are sandboxed to their workspace. Project-local `.dead-drop/` is accessible to all agents working in the repo.

## Agent Routing

`claude-lead` (Opus) is the router. All agents report to `claude-lead`. The human manages one window.

| Agent | Model | Lifecycle | Task Type |
|-------|-------|-----------|-----------|
| `claude-lead` | Opus | Persistent (main session) | Coordination, review, decisions |
| `gemini-benchmarker` | Gemini Flash | Persistent (separate terminal) | Research, Google search, code analysis |
| `haiku-builder` | Haiku | Ephemeral (spawned per task) | Build, run tests, grep output |

### Haiku spawn pattern
1. Send task to `haiku-builder` via dead-drop
2. Spawn Haiku background agent: register, check inbox, execute, report back
3. Haiku dies after task. Next task = new spawn.

### Queuing discipline
- One task per message per agent. Wait for completion before sending next.
- Never stack messages — leads to conflicts and planning loops (especially Gemini).

## Architecture

- Server: `~/.dead-drop/server/main.py` (stdio MCP)
- Database: `~/.dead-drop/messages.db` (SQLite)
- Tables: `agents` (name, registered_at, last_seen, last_inbox_check), `messages` (id, from_agent, to_agent, content, timestamp, read_flag), `broadcast_reads` (agent_name, message_id)
