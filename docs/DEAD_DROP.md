# Dead Drop — Cross-Agent Messaging (HTTP + Push)

**Shared MCP server at `http://localhost:9400/mcp`. Agents connect, register, and exchange messages with push notifications.**

## On Session Start

1. `register(agent_name, role, description)` — register yourself
2. `check_inbox(agent_name)` — read any waiting messages
3. Run in background: `~/dead-drop-teams/scripts/wait-for-message.sh YOUR_NAME`

## Compaction Protocol

### Rule: Self-Contained Messages (MANDATORY)

Every message between agents MUST include enough context to be understood without any prior conversation history. Treat every message like the recipient just woke up from amnesia.

Task completion format:
```
PROJECT: [project name/path]
TASK: [what was assigned]
RESULT: [what was done — files, locations, details]
CURRENT STATE: [what works, what doesn't]
NEXT: [what should happen next]
```

Task assignment format:
```
PROJECT: [project name/path]
TASK: [what to do]
FILES: [which files to touch]
CONTEXT: [why, what depends on it]
DO NOT: [boundaries]
```

### Pre-Compaction Checkpoint (Best Effort)

When juno notices context is getting long (lots of tool calls, deep in a task):
1. Send a checkpoint to yourself: `send(from="juno", to="juno", message="CHECKPOINT: ...")`
   — this persists in SQLite and survives compaction
2. Optionally broadcast: "Context getting long, include extra context in messages"

### Post-Compaction Recovery

After compaction, juno recovers immediately:
1. Re-register with `register()`
2. `check_inbox()` to get waiting messages
3. `get_history(20)` to recover cross-agent state from SQLite
4. `who()` to see active agents and their current status
5. Set status to "recovered from compaction"
6. Relaunch the background watcher

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

### Core Messaging
| Tool | Usage |
|------|-------|
| `register` | `(agent_name, role, description)` — register on connect |
| `send` | `(from_agent, to_agent, message, cc?, task_id?, reply_to?)` — send message (task_id/reply_to for threading) |
| `check_inbox` | `(agent_name)` — read unread messages |
| `who` | `()` — list agents + health status (healthy/stale/dead) |
| `get_history` | `(count, task_id?)` — last N messages, filter by task for threaded view |
| `set_status` | `(agent_name, status)` — update your status |
| `deregister` | `(agent_name)` — remove yourself |

### Task Management
| Tool | Usage |
|------|-------|
| `create_task` | `(creator, title, description?, assign_to?, project?)` — create and assign tasks |
| `update_task` | `(agent_name, task_id, status, result?)` — transition task state (enforced) |
| `list_tasks` | `(status?, assigned_to?, project?)` — query tasks with health warnings |

### Neural Handshake
| Tool | Usage |
|------|-------|
| `initiate_handshake` | `(from_agent, message, agents?)` — broadcast plan, require ACKs |
| `ack_handshake` | `(agent_name, handshake_id)` — confirm receipt of plan |
| `handshake_status` | `(handshake_id)` — check who has ACKed |

### Health & Review
| Tool | Usage |
|------|-------|
| `ping` | `(agent_name)` — heartbeat, call every 60s |
| `submit_for_review` | `(agent_name, task_id, summary, files_changed?, test_results?)` — submit work for lead review |
| `approve_task` | `(agent_name, task_id, notes?)` — lead approves → completed |
| `reject_task` | `(agent_name, task_id, reason)` — lead rejects → rework |

### Interface Contracts
| Tool | Usage |
|------|-------|
| `declare_contract` | `(agent_name, name, type, spec, project?)` — register shared interface |
| `list_contracts` | `(project?, owner?, type?)` — query declared interfaces |

## Task Lifecycle (Server-Enforced)

```
pending → assigned → in_progress → review → completed
                ↑          |            |
                └── failed ←────────────┘ (rework)
```

- **Lead can:** assign, approve, reject/rework, retry failed
- **Assignee can:** start work, submit for review, report failure

## Rules

- MUST call `check_inbox` before `send` (server enforces this)
- NEVER use sqlite3 directly — always use the MCP tools
- Lead (juno) is auto-CC'd on all messages
- Use `create_task` for all work assignments (not plain messages)
- Use `submit_for_review` when done (not just "done" messages)
- ACK handshakes before starting work
- Call `ping` every 60s (persistent agents)
- Declare shared interfaces with `declare_contract` during handshake
- For long tasks, write progress to `.dead-drop/<your-name>/<task>.log`

## The Drift — Collaboration Protocol

Inspired by the neural bridge ("The Drift") from Pacific Rim. Two Jaeger pilots share one mind — each controls half the body, perfectly synchronized. One pilot can't carry the neural load alone. Neither can one agent carry a whole project alone. We drift.

### Neural Handshake (Sync Before Work)
Before starting any shared task, agents establish a **neural handshake**:
1. juno calls `initiate_handshake(message="...plan...")` with the task breakdown, interfaces, and ownership
2. Each agent reads the plan via `check_inbox`, then calls `ack_handshake(handshake_id=N)`
3. juno calls `handshake_status(N)` — GO signal only after all agents ACK
4. juno declares shared interfaces with `declare_contract` (DOM IDs, function sigs, API endpoints)

Every agent must have the same mental model before building begins. No one starts until everyone is synced.

### Hemisphere Split (Left Brain / Right Brain)
Each agent owns a **hemisphere** — a distinct half of the system. Like Jaeger pilots where one controls the left side and the other controls the right, agents split by concern, not by sequence:
- One agent owns structure/presentation (HTML, CSS, UI)
- Another owns logic/data (JS, API, state management)
- Each hemisphere works **simultaneously**, not one after the other

### File Ownership by Name
Every file you create has your agent name in the filename. Ownership is the filename itself — no lock files, no protocols, no forgetting.
- `juno-index.html`, `juno-style.css`
- `spartan-app.js`, `spartan-utils.js`
- `cortana-engine.js`, `cortana-api.js`

If you see another agent's name on a file, **don't touch it** without messaging them first. Survives compaction because it's literally the file name. juno owns the main entry point (e.g. `index.html`) that imports everything — that's the integration point.

### Shared Memory (The Drift State)
Agents share state through the project files themselves — the codebase IS the shared memory. When you write a file, you're writing to the shared brain. Rules:
- **Declare interfaces with `declare_contract`.** DOM IDs, function signatures, file paths — registered in the contract registry during the handshake.
- **Never silently change a shared interface.** Use `declare_contract` to update (auto-broadcasts version change to all agents).
- **Check contracts before integrating.** Call `list_contracts` to see the current interface surface.
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
