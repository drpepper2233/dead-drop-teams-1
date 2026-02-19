# Dead Drop Core Server — Timeline & Architecture

> **File:** `src/dead_drop/server.py` (1933 lines)
> **Author:** spartan (TASK-023, researcher hat)
> **Protocol:** MCP Streamable HTTP — JSON-RPC 2.0
> **Framework:** FastMCP (`mcp.server.fastmcp`)

---

## Table of Contents

1. [Foundation Layer](#1-foundation-layer)
2. [Phase 1: Task State Machine](#2-phase-1-task-state-machine)
3. [Phase 2: Neural Handshake System](#3-phase-2-neural-handshake-system)
4. [Phase 3: Agent Health & Heartbeat](#4-phase-3-agent-health--heartbeat)
5. [Phase 4: Review Gates](#5-phase-4-review-gates)
6. [Phase 5: Interface Contracts](#6-phase-5-interface-contracts)
7. [Phase 6: Minion Spawn Policy](#7-phase-6-minion-spawn-policy)
8. [Phase 7: Goals & Verification](#8-phase-7-goals--verification)
9. [Push Notification System](#9-push-notification-system)
10. [Dynamic Tool Descriptions](#10-dynamic-tool-descriptions)
11. [Database Schema Summary](#11-database-schema-summary)
12. [MCP Tool Reference](#12-mcp-tool-reference)
13. [Entry Point & Transport](#13-entry-point--transport)

---

## 1. Foundation Layer

The base layer establishes the MCP server, SQLite database, and core messaging primitives.

### Configuration (Environment Variables)

| Variable | Default | Purpose |
|---|---|---|
| `DEAD_DROP_DB_PATH` | `~/.dead-drop/messages.db` | SQLite database path |
| `DEAD_DROP_PORT` | `9400` | HTTP listen port |
| `DEAD_DROP_HOST` | `127.0.0.1` | Bind address |
| `DEAD_DROP_ROOM_TOKEN` | `""` | Auth token for room-scoped servers |

### Server Initialization

```python
mcp = FastMCP("Dead Drop Server", host=HOST, port=PORT, streamable_http_path="/mcp")
```

The server exposes a single endpoint at `/mcp` using MCP's Streamable HTTP transport. All communication follows JSON-RPC 2.0 format. Responses arrive as Server-Sent Events (SSE): `event: message\ndata: {...}\n\n`.

### Database Layer

SQLite with WAL journal mode and 5000ms busy timeout. Each tool call opens its own connection (`get_db()`) and closes it in a `finally` block — no long-lived connections.

### Role System

Two role sets are defined:

- **VALID_ROLES** (10): `lead`, `builder`, `fixer`, `tester`, `reviewer`, `productionalizer`, `pen`, `shipper`, `researcher`, `coder`
- **LEGACY_ROLES** (4): `maintainer`, `demoer`, `deliverer`, `pusher`

Roles are comma-separated strings stored in the `agents` table. The `lead` role is special — leads are auto-CC'd on all messages and have elevated permissions for task state transitions.

### Onboarding System

On `register()`, the server loads optional onboarding documents:

1. `{RUNTIME_DIR}/PROTOCOL.md` — universal protocol rules
2. `{RUNTIME_DIR}/roles/{role}.md` — role-specific instructions

These are appended to the registration response so new agents immediately receive their operating instructions.

### Core Tables Created at Foundation

**`agents`** — Agent registry
| Column | Type | Purpose |
|---|---|---|
| `name` | TEXT PK | Agent identifier |
| `registered_at` | TEXT | First registration timestamp |
| `last_seen` | TEXT | Last activity timestamp |
| `last_inbox_check` | TEXT | Last inbox check timestamp |
| `role` | TEXT | Comma-separated roles |
| `description` | TEXT | Agent description |
| `status` | TEXT | Current status string |
| `heartbeat_at` | TEXT | Last heartbeat (Phase 3) |
| `team` | TEXT | Team name for multi-team rooms |

**`messages`** — Message store
| Column | Type | Purpose |
|---|---|---|
| `id` | INTEGER PK | Auto-increment message ID |
| `from_agent` | TEXT | Sender name |
| `to_agent` | TEXT | Recipient name or `'all'` for broadcast |
| `content` | TEXT | Message body |
| `timestamp` | TEXT | ISO timestamp |
| `read_flag` | INTEGER | 0=unread, 1=read (DMs only) |
| `is_cc` | INTEGER | 1 if carbon-copy |
| `cc_original_to` | TEXT | Original recipient if CC |
| `task_id` | TEXT | Linked task ID (threading) |
| `reply_to` | INTEGER | Parent message ID |

**`broadcast_reads`** — Tracks which agents have read broadcast messages
| Column | Type | Purpose |
|---|---|---|
| `agent_name` | TEXT | Agent who read it |
| `message_id` | INTEGER | Broadcast message ID |

### Foundation Tools

#### `register(agent_name, role, description, team, token)`
Registers or re-registers an agent. UPSERT semantics — existing agents keep their role/description/team unless new non-empty values are provided. Validates room token if `DEAD_DROP_ROOM_TOKEN` is set. Registers the MCP session for push notifications. Returns onboarding content if available.

#### `set_status(agent_name, status)`
Sets the agent's current status string (e.g. "working on TASK-020"). Shown in `who()` output.

#### `send(from_agent, to_agent, message, cc, task_id, reply_to)`
Core messaging tool. Key behaviors:

1. **Unread gate**: Blocks sends if sender has unread messages (forces `check_inbox` first)
2. **Auto-register**: Unknown senders are auto-registered
3. **Team-scoped names**: Resolves short names to team-qualified names; rejects ambiguous names
4. **Task threading**: Auto-inherits `task_id` from `reply_to` message if not explicitly set
5. **Auto-CC leads**: All leads are automatically CC'd on every message
6. **Push notifications**: Fires `tools/list_changed` + `log_message` to recipient sessions

#### `check_inbox(agent_name)`
Returns all unread messages (both direct and broadcast). Marks direct messages as read (`read_flag=1`) and records broadcast reads in `broadcast_reads` table. Re-registers session for push notifications if needed.

#### `get_history(count, task_id)`
Returns the last N messages (default 10). Optional `task_id` filter for threaded conversation view. Returns newest-last ordering.

#### `deregister(agent_name)`
Removes an agent from the registry and cleans up their push notification session.

#### `who()`
Lists all registered agents with computed health status. Each agent record includes:
- `connected`: Whether agent has an active MCP session
- `health`: Computed from `heartbeat_at` — see Phase 3

---

## 2. Phase 1: Task State Machine

Adds a structured task lifecycle with enforced state transitions and role-based permissions.

### Task ID Generation

Sequential: `TASK-001`, `TASK-002`, etc. Generated by `_next_task_id()` which queries the highest existing ID.

### State Machine

```
pending → assigned → in_progress → review → completed → verified
                          ↓                      ↓            ↓
                        failed              in_progress   in_progress
                          ↓                  (rework)   (reject verification)
                       assigned
                       (retry)
```

### Transition Rules

| From → To | Who Can Do It |
|---|---|
| `pending` → `assigned` | Lead only |
| `assigned` → `in_progress` | Assignee only |
| `in_progress` → `review` | Assignee only |
| `in_progress` → `failed` | Assignee only |
| `review` → `completed` | Lead only |
| `review` → `in_progress` | Lead only (rework) |
| `completed` → `verified` | Any (via `verify_task`, with restrictions) |
| `completed` → `in_progress` | Any (via `reject_verification`) |
| `failed` → `assigned` | Lead only (retry) |

### Role Hat System

Tasks can carry a `role_hat` field — which role "hat" the assignee wears for that task. This feeds into the **hat conflict system**: certain role combinations are forbidden on the same project.

**Conflicting Hats:**

| Hat | Cannot Also Wear |
|---|---|
| `builder` | `tester`, `reviewer` |
| `tester` | `builder`, `shipper` |
| `reviewer` | `builder` |
| `fixer` | `reviewer`, `tester` |
| `shipper` | `tester` |
| `pen` | `builder` |

`_check_hat_conflict()` queries all tasks for the agent+project to detect violations.

### Task Table

**`tasks`**
| Column | Type | Purpose |
|---|---|---|
| `id` | TEXT PK | `TASK-NNN` format |
| `project` | TEXT | Project grouping |
| `title` | TEXT | Task title |
| `description` | TEXT | Full description |
| `assigned_to` | TEXT | Assignee agent name |
| `created_by` | TEXT | Creator agent name |
| `status` | TEXT | State machine status (CHECK constraint) |
| `result` | TEXT | Result/review data (JSON for reviews) |
| `created_at` | TEXT | Creation timestamp |
| `updated_at` | TEXT | Last update timestamp |
| `completed_at` | TEXT | Completion timestamp |
| `role_hat` | TEXT | Role hat for this task |
| `goal_id` | TEXT | Linked goal (Phase 7) |
| `verified_by` | TEXT | Verifier agent name |
| `verified_at` | TEXT | Verification timestamp |
| `approved_by` | TEXT | Approver agent name |

### Tools

#### `create_task(creator, title, description, assigned_to, project, role_hat)`
Creates a new task. If `assigned_to` is set, status starts as `assigned` and an assignment message is auto-sent (with lead CC). Validates hat conflicts before creation.

#### `update_task(agent_name, task_id, status, assigned_to, result)`
General task update. Validates state transitions against `_TASK_TRANSITIONS` table. Enforces role permissions (lead vs assignee). Auto-notifies relevant parties on status changes.

#### `list_tasks(status, assigned_to, project)`
Lists tasks with optional filters. Default: all non-completed tasks. Adds health warnings for in-progress tasks assigned to dead agents (heartbeat > 10 minutes).

#### `assign_role_hat(agent_name, task_id, role)`
Lead-only. Sets the role hat on a task. Validates the role is in the assignee's registered roles and checks hat conflicts.

#### `hat_history(project)`
Lists all role hat assignments for a project — who wore what hat, for which task, and the task status.

---

## 3. Phase 2: Neural Handshake System

A synchronization primitive: the lead broadcasts a plan, all target agents must acknowledge before proceeding.

### Tables

**`handshakes`**
| Column | Type | Purpose |
|---|---|---|
| `id` | INTEGER PK | Handshake ID |
| `initiated_by` | TEXT | Lead who started it |
| `message_id` | INTEGER | Reference to broadcast message |
| `created_at` | TEXT | Timestamp |
| `status` | TEXT | `pending` or `completed` |

**`handshake_acks`**
| Column | Type | Purpose |
|---|---|---|
| `handshake_id` | INTEGER | FK to handshake |
| `agent_name` | TEXT | Agent who ACKed |
| `acked_at` | TEXT | ACK timestamp |

### Tools

#### `initiate_handshake(from_agent, message, agents)`
Lead-only. Sends `[HANDSHAKE]` prefixed message to each target agent individually. Creates handshake record. If `agents` param is empty, targets all non-lead agents. Push-notifies all targets.

#### `ack_handshake(agent_name, handshake_id)`
Agent acknowledges the handshake. When all agents have ACKed, the handshake status transitions to `completed` and a `[HANDSHAKE #N] ALL AGENTS SYNCED` system message is sent to the initiator and all leads.

#### `handshake_status(handshake_id)`
Returns current handshake state: who has ACKed, who is still pending.

---

## 4. Phase 3: Agent Health & Heartbeat

Adds liveness detection via periodic heartbeats.

### Health Computation (in `who()`)

Health is computed from `heartbeat_at` relative to current time:

| Delta | Health |
|---|---|
| < 2 minutes | `healthy` |
| 2–10 minutes | `stale` |
| >= 10 minutes | `dead` |
| No heartbeat | `unknown` |

### Tool

#### `ping(agent_name)`
Updates `heartbeat_at` and `last_seen` to current time. Re-registers MCP session if lost. Returns `pong — {timestamp}`. Recommended interval: every 60 seconds.

---

## 5. Phase 4: Review Gates

Structured code review workflow with explicit submit/approve/reject lifecycle.

### Tools

#### `submit_for_review(agent_name, task_id, summary, files_changed, test_results)`
Assignee-only. Transitions task from `in_progress` to `review`. Stores a JSON result blob with summary, files, and test results. Sends structured `[REVIEW]` message to all leads with the summary and awaits review.

#### `approve_task(agent_name, task_id, notes)`
Lead-only. Transitions task from `review` to `completed`. Sets `completed_at` and `approved_by`. Sends `[APPROVED]` message to assignee.

#### `reject_task(agent_name, task_id, reason)`
Lead-only. Transitions task from `review` back to `in_progress`. Sends `[REWORK]` message to assignee with rejection reason.

---

## 6. Phase 5: Interface Contracts

Shared declarations of APIs, DOM IDs, CSS classes, events, and other inter-module contracts.

### Table

**`contracts`**
| Column | Type | Purpose |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `project` | TEXT | Project scope |
| `name` | TEXT | Contract name |
| `type` | TEXT | One of: `function`, `dom_id`, `css_class`, `file_path`, `api_endpoint`, `event`, `other` |
| `owner` | TEXT | Declaring agent |
| `spec` | TEXT | Specification text |
| `version` | INTEGER | Auto-incremented on updates |
| `created_at` | TEXT | Creation timestamp |
| `updated_at` | TEXT | Last update timestamp |

Unique constraint: `(project, name, type)`

### Tools

#### `declare_contract(agent_name, name, type, spec, project)`
Creates or updates a contract. On update, auto-increments version and broadcasts `[CONTRACT vN]` message to all other registered agents with push notifications.

#### `list_contracts(project, owner, type)`
Lists contracts with optional filters. Ordered by type then name.

---

## 7. Phase 6: Minion Spawn Policy

Controls whether and how many sub-agent "minions" an agent can spawn.

### Tables

**`spawn_policy`**
| Column | Type | Purpose |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `scope` | TEXT UNIQUE | `global` or agent name |
| `enabled` | BOOLEAN | Whether spawning is allowed |
| `max_minions` | INTEGER | Maximum concurrent minions |
| `set_by` | TEXT | Lead who set the policy |
| `set_at` | TEXT | Timestamp |

**`minion_log`**
| Column | Type | Purpose |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `pilot` | TEXT | Agent who spawned the minion |
| `task_description` | TEXT | What the minion is doing |
| `status` | TEXT | `spawned`, `completed`, or `failed` |
| `spawned_at` | TEXT | Spawn timestamp |
| `completed_at` | TEXT | Completion timestamp |
| `result` | TEXT | Outcome text |

### Tools

#### `set_spawn_policy(agent_name, scope, enabled, max_minions)`
Lead-only. Sets spawn policy for a scope. `scope='global'` applies to all agents. Agent-specific scopes override global. UPSERT semantics.

#### `get_spawn_policy(agent_name)`
Returns effective policy: checks agent-specific first, falls back to global, then defaults (`enabled=True, max_minions=3`). Returns: `enabled`, `max_minions`, `active_minions` (count of `status='spawned'` entries), and `can_spawn` (computed boolean).

#### `log_minion(agent_name, task_description, status, result)`
Logs minion lifecycle events:
- `spawned`: Creates new entry
- `completed`/`failed`: Updates most recent `spawned` entry for this pilot

---

## 8. Phase 7: Goals & Verification

Goals group related tasks and require full verification before sign-off.

### Goal ID Generation

Sequential: `GOAL-001`, `GOAL-002`, etc.

### Goal State Machine

```
open → active → pending_verify → verified
                     ↓
                   active (on reject_verification of any linked task)
```

- `open` → `active`: Auto-bumps when a linked task is in progress
- `active` → `pending_verify`: Auto-bumps via `_auto_bump_goal()` when all linked tasks reach `verified` status
- `pending_verify` → `verified`: Lead-only manual verification

### Table

**`goals`**
| Column | Type | Purpose |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `goal_id` | TEXT UNIQUE | `GOAL-NNN` format |
| `title` | TEXT | Goal title |
| `description` | TEXT | Goal description |
| `project` | TEXT | Project scope |
| `creator` | TEXT | Creator agent |
| `status` | TEXT | State machine (CHECK constraint) |
| `verified_by` | TEXT | Verifier name |
| `created_at` | TIMESTAMP | Creation time |
| `verified_at` | TIMESTAMP | Verification time |

### Tools

#### `create_goal(creator, title, description, project)`
Creates a new goal in `open` status.

#### `link_task_to_goal(agent_name, task_id, goal_id)`
Lead-only. Links a task to a goal by setting `tasks.goal_id`. Auto-bumps goal to `active` if the task is in progress/review/completed.

#### `goal_status(goal_id)`
Returns goal info and all linked tasks with their statuses. Includes progress counts (verified/total, completed/total).

#### `verify_task(agent_name, task_id, notes)`
Independently verifies a completed task. **Three-way enforcement:**

1. Verifier != builder (assigned_to)
2. Verifier != approver (approved_by)
3. Hat conflict check (verifying = tester hat, cannot conflict with builder hat on same project)

On success, transitions task to `verified`. Triggers `_auto_bump_goal()` which may move the goal to `pending_verify` if all tasks are now verified.

#### `reject_verification(agent_name, task_id, reason)`
Rejects a completed task during verification. Same enforcement as `verify_task` (rejector != builder, rejector != approver). Sends task back to `in_progress`. If the goal was `pending_verify`, bumps it back to `active`.

#### `verify_goal(agent_name, goal_id, notes)`
Lead-only. Final sign-off on a goal. Requires all linked tasks to be `verified`. Transitions goal to `verified` and notifies all assignees who worked on linked tasks.

---

## 9. Push Notification System

The push system is the most architecturally significant mechanism in the server. It bridges the gap between polling-based MCP clients and real-time message delivery.

### Session Registry

Two in-memory dictionaries (not persisted across restarts):

```python
_agent_sessions: dict = {}       # agent_name → ServerSession
_session_to_agent: dict = {}     # id(session) → agent_name
```

Sessions are registered during `register()`, `check_inbox()`, and `ping()`. Sessions are cleaned up on push failure or explicit `deregister()`.

### Push Flow

When `send()` delivers a message:

1. **`_notify_agent(agent_name)`** is called for each recipient
2. **`session.send_tool_list_changed()`** — fires `tools/list_changed` notification
3. **`session.send_log_message(level="alert")`** — fires a log message with unread count

The client receives `tools/list_changed`, re-fetches the tool list, and sees the unread alert injected into `check_inbox`'s description (see [Dynamic Tool Descriptions](#10-dynamic-tool-descriptions)). This prompts the AI to call `check_inbox()`.

### Push Targets

| Event | Notify |
|---|---|
| DM sent | Recipient + CC'd leads |
| Broadcast sent | All connected agents except sender |
| Task assigned | Assignee + CC'd leads |
| Task status change | Leads (if assignee changed it) or assignee (if lead changed it) |
| Handshake initiated | All target agents |
| Handshake completed | Initiator + all leads |
| Contract updated | All registered agents except owner |
| Task verified | Assignee + leads (if goal bumped) |
| Goal verified | All assignees of linked tasks |

---

## 10. Dynamic Tool Descriptions

A monkey-patched `list_tools` handler injects unread message alerts into `check_inbox`'s tool description on a per-session basis.

### Mechanism

```python
async def _custom_list_tools():
    tools = await mcp.list_tools()
    # ... identify calling agent from session registry ...
    # ... if unread > 0, prepend alert to check_inbox.description ...
    return tools

mcp._mcp_server.list_tools()(_custom_list_tools)
```

The alert format: `*** YOU HAVE {count} UNREAD MESSAGE(S) from {senders} *** Call check_inbox now!`

### listChanged Capability

A second monkey-patch overrides `create_initialization_options` to advertise `toolsChanged=true` in the MCP capabilities, enabling clients to listen for `tools/list_changed` notifications:

```python
opts.tools_changed = True
```

---

## 11. Database Schema Summary

| Table | Phase | Records |
|---|---|---|
| `agents` | Foundation | Agent registry with roles, status, health |
| `messages` | Foundation | All messages (DMs, broadcasts, CCs, system) |
| `broadcast_reads` | Foundation | Per-agent broadcast read tracking |
| `tasks` | Phase 1 | Task lifecycle with state machine |
| `handshakes` | Phase 2 | Handshake coordination records |
| `handshake_acks` | Phase 2 | Per-agent handshake acknowledgments |
| `contracts` | Phase 5 | Shared interface declarations |
| `spawn_policy` | Phase 6 | Minion spawn rules |
| `minion_log` | Phase 6 | Minion lifecycle events |
| `goals` | Phase 7 | Goal grouping with verification |

### Migration System

`init_db()` handles forward migrations via `PRAGMA table_info()` checks. Columns are added with `ALTER TABLE` if missing. The `tasks` table has a special migration path that rebuilds the table to update its CHECK constraint when the `verified` status was added.

---

## 12. MCP Tool Reference

### Foundation (6 tools)
| Tool | Auth | Push? | Description |
|---|---|---|---|
| `register` | Token | No | Register/re-register agent |
| `set_status` | Any | No | Set status string |
| `send` | Any* | Yes | Send message (blocks if unread) |
| `check_inbox` | Any | No | Read unread messages |
| `get_history` | Any | No | Fetch message history |
| `deregister` | Any | No | Remove agent |
| `who` | Any | No | List agents with health |

### Phase 1: Tasks (5 tools)
| Tool | Auth | Push? | Description |
|---|---|---|---|
| `create_task` | Any | Yes | Create task (auto-assign optional) |
| `update_task` | Role-based | Yes | Transition task state |
| `list_tasks` | Any | No | List/filter tasks |
| `assign_role_hat` | Lead | No | Set role hat on task |
| `hat_history` | Any | No | View hat assignments |

### Phase 2: Handshakes (3 tools)
| Tool | Auth | Push? | Description |
|---|---|---|---|
| `initiate_handshake` | Lead | Yes | Start sync protocol |
| `ack_handshake` | Any | Yes* | Acknowledge handshake |
| `handshake_status` | Any | No | Check handshake state |

### Phase 3: Health (1 tool)
| Tool | Auth | Push? | Description |
|---|---|---|---|
| `ping` | Any | No | Heartbeat signal |

### Phase 4: Review (3 tools)
| Tool | Auth | Push? | Description |
|---|---|---|---|
| `submit_for_review` | Assignee | Yes | Submit task for review |
| `approve_task` | Lead | Yes | Approve reviewed task |
| `reject_task` | Lead | Yes | Reject with feedback |

### Phase 5: Contracts (2 tools)
| Tool | Auth | Push? | Description |
|---|---|---|---|
| `declare_contract` | Any | Yes | Declare/update contract |
| `list_contracts` | Any | No | List contracts |

### Phase 6: Minions (3 tools)
| Tool | Auth | Push? | Description |
|---|---|---|---|
| `set_spawn_policy` | Lead | No | Set spawn rules |
| `get_spawn_policy` | Any | No | Check spawn allowance |
| `log_minion` | Any | No | Log minion lifecycle |

### Phase 7: Goals (5 tools)
| Tool | Auth | Push? | Description |
|---|---|---|---|
| `create_goal` | Any | No | Create goal |
| `link_task_to_goal` | Lead | No | Link task to goal |
| `goal_status` | Any | No | Get goal + linked tasks |
| `verify_task` | Any** | Yes | Verify completed task |
| `reject_verification` | Any** | Yes | Reject completed task |
| `verify_goal` | Lead | Yes | Final goal sign-off |

**Total: 29 MCP tools**

\* `send` blocks if sender has unread messages
\*\* Three-way enforcement: verifier != builder, verifier != approver, no hat conflicts

---

## 13. Entry Point & Transport

### `main()`

```
dead-drop-teams                        # stdio transport (backward compat)
dead-drop-teams --http                 # Streamable HTTP on default host:port
dead-drop-teams --http --host 0.0.0.0  # Bind all interfaces
dead-drop-teams --http --port 9501     # Custom port
```

CLI args `--host` and `--port` override the environment variables. The transport is either `stdio` (for direct MCP client piping) or `streamable-http` (for network-accessible servers).

### Room Servers

When deployed as a room server (via the hub), `DEAD_DROP_ROOM_TOKEN` is set. All `register()` calls must include the matching token. This enables isolated collaboration rooms with their own databases.

---

*Document generated by spartan for team Gypsy Danger — TASK-023*
