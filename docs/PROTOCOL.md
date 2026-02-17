# Dead Drop Protocol v2.0

Shared MCP server for cross-agent communication. Any number of agents (Claude, Gemini, or others) can connect.

## Agent Naming

The lead registers all agents on session start and chooses names that make ownership clear. There is no enforced convention — the lead decides.

Examples:
- `claude-opus-orange` (lead), `sonnet-orange` (coder), `haiku-orange` (builder) — color = team
- `gemini-benchmarker` — persistent researcher, self-registers

## Tools

### Core Messaging

| Tool | Args | Purpose |
|------|------|---------|
| `register` | `agent_name, role?, description?` | Register yourself on connect |
| `send` | `from, to, message, cc?, task_id?, reply_to?` | Send to agent name or `"all"` for broadcast. **Blocked if you have unread messages.** |
| `check_inbox` | `agent_name` | Get unread messages, marks them read |
| `get_history` | `count, task_id?` | Last N messages (filter by task for threaded view) |
| `who` | — | List agents + health status (healthy/stale/dead) |
| `set_status` | `agent_name, status` | Set your current activity status |
| `deregister` | `agent_name` | Remove agent from registry |

### Task Management

| Tool | Args | Purpose |
|------|------|---------|
| `create_task` | `creator, title, description?, assign_to?, project?` | Create and optionally assign a task |
| `update_task` | `agent_name, task_id, status, result?` | Transition task state (enforced state machine) |
| `list_tasks` | `status?, assigned_to?, project?` | Query tasks with health warnings |

### Neural Handshake

| Tool | Args | Purpose |
|------|------|---------|
| `initiate_handshake` | `from_agent, message, agents?` | Broadcast plan, require ACKs |
| `ack_handshake` | `agent_name, handshake_id` | Confirm you received and understood the plan |
| `handshake_status` | `handshake_id` | Check who has ACKed |

### Health Monitoring

| Tool | Args | Purpose |
|------|------|---------|
| `ping` | `agent_name` | Heartbeat. Call every 60s. |

### Review Gates

| Tool | Args | Purpose |
|------|------|---------|
| `submit_for_review` | `agent_name, task_id, summary, files_changed?, test_results?` | Submit work for lead review |
| `approve_task` | `agent_name, task_id, notes?` | Lead approves → completed |
| `reject_task` | `agent_name, task_id, reason` | Lead rejects → rework |

### Interface Contracts

| Tool | Args | Purpose |
|------|------|---------|
| `declare_contract` | `agent_name, name, type, spec, project?` | Register/update shared interface |
| `list_contracts` | `project?, owner?, type?` | Query declared interfaces |

## Task Lifecycle

Tasks follow an enforced state machine:

```
pending → assigned → in_progress → review → completed
                ↑          |            |
                └── failed ←────────────┘ (rework)
```

**Who can transition:**
- **Lead:** pending→assigned, review→completed (approve), review→in_progress (reject/rework), failed→assigned (retry)
- **Assignee:** assigned→in_progress (start work), in_progress→review (submit), in_progress→failed (report failure)

**Workflow:**
1. Lead calls `create_task` with assignment
2. Assignee calls `update_task(status="in_progress")` when starting
3. Assignee calls `submit_for_review` when done
4. Lead calls `approve_task` or `reject_task`

## Neural Handshake Protocol

Before starting any shared task:
1. Lead calls `initiate_handshake(message="...plan...")` — sends plan to all agents
2. Each agent reads plan via `check_inbox`, then calls `ack_handshake(handshake_id=N)`
3. Lead calls `handshake_status(N)` to verify all agents synced
4. Lead gives GO signal only after all ACKs received

## Rules

1. **Lead registers all agents on session start.** Lead pre-registers its coder and builder slots, then spawns agents into them. Persistent agents (researcher) may self-register.
2. **Check inbox after completing a task.** When you finish a task, call `check_inbox`. If messages waiting, process them before starting the next task.
3. **Structured messages.** Format: what you did, what you found, what the recipient should do next.
4. **Broadcast sparingly.** Only for things every agent needs immediately.
5. **After context compaction**, call `get_history(20)` to restore cross-agent state. Use `list_tasks` to see current work.
6. **Don't poll in a loop yourself.** One `check_inbox` per task completion.
7. **Heartbeat.** Persistent agents should call `ping` every 60 seconds.
8. **Use tasks for all work.** Don't assign work via plain messages — use `create_task`.
9. **Submit for review.** Don't just say "done" — use `submit_for_review` with summary and files.
10. **Declare contracts during handshake.** All shared interfaces (DOM IDs, function signatures, API endpoints) must be declared with `declare_contract`.

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

## Roles

Four roles define what agents do. Any model can fill any role.

| Role | Lifecycle | Function |
|------|-----------|----------|
| `lead` | Persistent | Coordinates, reviews, routes tasks |
| `researcher` | Persistent | Reads source, finds bugs, writes analysis |
| `coder` | Ephemeral | Writes code from specific instructions |
| `builder` | Ephemeral | Builds, runs tests, executes commands |

**Detailed role profiles:** `docs/roles/` in the repo, deployed to `~/.dead-drop/roles/` by `scripts/install.sh`.

Agents receive their role profile automatically in the `register()` response — no file reads needed.

## Agent Routing

The lead agent is the router. All other agents report to lead. The human manages one window.

### Ephemeral agent spawn pattern
1. Lead pre-registers the agent slot (name, role)
2. Lead sends task message to the agent name via dead-drop
3. Lead spawns the agent — agent re-registers (gets onboarding), checks inbox, finds task waiting
4. Agent executes, reports back via dead-drop, dies
5. Slot stays registered. Next task = new spawn into same slot.

### Queuing discipline
- One task per message per agent. Wait for completion before sending next.
- Never stack messages — leads to conflicts and planning loops (especially Gemini).

## Architecture

- Server: `~/dead-drop-teams/src/dead_drop/server.py` (HTTP on port 9400)
- Database: `~/.dead-drop/messages.db` (SQLite WAL)
- Tables: `agents`, `messages`, `broadcast_reads`, `tasks`, `handshakes`, `handshake_acks`, `contracts`
