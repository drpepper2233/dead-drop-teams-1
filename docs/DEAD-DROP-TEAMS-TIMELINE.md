# Dead Drop Teams — Complete System Timeline

> From first message to cross-team workspaces. How an AI multi-agent coordination
> system was built layer by layer.
>
> **Team Gypsy Danger** — juno (lead), spartan, cortana, roland

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Layer 1: Core Server — Messaging & Agents](#2-layer-1-core-server--messaging--agents)
3. [Layer 2: Task State Machine](#3-layer-2-task-state-machine)
4. [Layer 3: Neural Handshake System](#4-layer-3-neural-handshake-system)
5. [Layer 4: Agent Health & Heartbeat](#5-layer-4-agent-health--heartbeat)
6. [Layer 5: Review Gates](#6-layer-5-review-gates)
7. [Layer 6: Interface Contracts](#7-layer-6-interface-contracts)
8. [Layer 7: Minion Spawn Policy](#8-layer-7-minion-spawn-policy)
9. [Layer 8: Goals & Verification](#9-layer-8-goals--verification)
10. [Layer 9: Hub Server — Multi-Team Orchestration](#10-layer-9-hub-server--multi-team-orchestration)
11. [Layer 10: Docker Spawner — Container Lifecycle](#11-layer-10-docker-spawner--container-lifecycle)
12. [Layer 11: Cross-Team Workspaces](#12-layer-11-cross-team-workspaces)
13. [Layer 12: Heist Board — Real-Time Dashboard](#13-layer-12-heist-board--real-time-dashboard)
14. [Architecture Summary](#14-architecture-summary)
15. [Full MCP Tool Reference](#15-full-mcp-tool-reference)
16. [Database Schema Reference](#16-database-schema-reference)

---

## 1. System Overview

Dead Drop Teams is an MCP-based multi-agent coordination system. AI agents (Claude Code sessions) communicate through a shared SQLite message bus, coordinate via structured task workflows, and collaborate across teams through Docker-isolated environments.

### The Stack

```
Agents (Claude Code)
    |
    | MCP JSON-RPC 2.0 over HTTP
    |
    v
Dead Drop Server (server.py)        port 9400
    - Messaging, tasks, handshakes, goals, contracts
    - 29 MCP tools, 10 SQLite tables
    - Push notifications via tools/list_changed
    |
    v
Hub Server (hub.py)                  port 9500
    - Team registry, room management, workspace provisioning
    - 14 MCP tools, 3 SQLite tables
    - Delegates to Docker Spawner
    |
    v
Docker Spawner (spawner.py)
    - Room containers (port 9501-10500) — isolated MCP servers
    - Workspace containers (port 10501-11500) — SSH dev environments
    - Health checks, auto-reap, archive system
    |
    v
Heist Board (index.html + JS/CSS)   port 80 (nginx)
    - Real-time dashboard, Payday 2 themed
    - Polls MCP server, renders agents/tasks/messages
    - SVG canvas with crew nodes, task cards, wire animations
```

### Package Info

```
Name:         dead-drop-teams
Version:      1.0.0
Python:       >=3.11
Dependencies: mcp[cli]>=0.1.0, docker>=7.0.0
Entry points: dead-drop-teams  (server)
              dead-drop-hub    (hub)
```

---

## 2. Layer 1: Core Server — Messaging & Agents

**File:** `src/dead_drop/server.py`
**What it does:** Establishes the MCP server, SQLite database, agent registry, and core messaging.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DEAD_DROP_DB_PATH` | `~/.dead-drop/messages.db` | SQLite database path |
| `DEAD_DROP_PORT` | `9400` | HTTP listen port |
| `DEAD_DROP_HOST` | `127.0.0.1` | Bind address |
| `DEAD_DROP_ROOM_TOKEN` | `""` | Auth token for room-scoped servers |

### How It Works

The server exposes a single endpoint at `/mcp` using MCP's Streamable HTTP transport. All communication is JSON-RPC 2.0. Responses arrive as Server-Sent Events (SSE).

SQLite runs with WAL journal mode and 5000ms busy timeout. Each tool call opens its own connection and closes it in a `finally` block.

### Role System

- **VALID_ROLES** (10): `lead`, `builder`, `fixer`, `tester`, `reviewer`, `productionalizer`, `pen`, `shipper`, `researcher`, `coder`
- **LEGACY_ROLES** (4): `maintainer`, `demoer`, `deliverer`, `pusher`

The `lead` role is special — leads are auto-CC'd on all messages and have elevated permissions for task state transitions.

### Onboarding

On `register()`, the server loads optional documents:
1. `{RUNTIME_DIR}/PROTOCOL.md` — universal protocol rules
2. `{RUNTIME_DIR}/roles/{role}.md` — role-specific instructions

New agents immediately receive their operating instructions.

### Tables

**`agents`** — Agent registry (name, role, description, team, status, heartbeat, last_seen)

**`messages`** — All messages with from/to, read tracking, CC support, task threading, reply chains

**`broadcast_reads`** — Per-agent tracking of which broadcast messages have been read

### Push Notification System

The most architecturally significant mechanism. Bridges polling-based MCP clients and real-time delivery.

```
Message sent via send()
    |
    v
_notify_agent(recipient)
    |
    +--> session.send_tool_list_changed()   -- client re-fetches tool list
    |
    +--> session.send_log_message("alert")  -- surfaces in client conversation
    |
    v
Client sees unread alert injected into check_inbox's description
    |
    v
AI calls check_inbox() automatically
```

Two in-memory dicts map agent names to MCP sessions. Sessions registered during `register()`, `check_inbox()`, and `ping()`. Cleaned up on push failure.

### Core Tools (7)

| Tool | What it does |
|---|---|
| `register` | Register/re-register agent. UPSERT semantics. Validates room token if set. Returns onboarding docs. |
| `set_status` | Set agent's current status string shown in `who()`. |
| `send` | Send message. **Blocks if sender has unread messages.** Auto-CC's leads. Push-notifies recipients. |
| `check_inbox` | Return all unread messages, mark as read. Re-registers session. |
| `get_history` | Last N messages. Optional `task_id` filter for threaded view. |
| `deregister` | Remove agent from registry, clean up session. |
| `who` | List all agents with computed health status. |

**Key `send()` behaviors:**
1. Unread gate — forces `check_inbox` first
2. Auto-register unknown senders
3. Team-scoped name resolution
4. Task threading from `reply_to`
5. Auto-CC all leads
6. Push notifications to recipients

---

## 3. Layer 2: Task State Machine

Adds structured task lifecycle with enforced state transitions and role-based permissions.

### State Machine

```
pending --> assigned --> in_progress --> review --> completed --> verified
                             |                        |             |
                           failed                in_progress   in_progress
                             |                    (rework)   (reject verify)
                          assigned
                          (retry)
```

### Transition Rules

| From -> To | Who |
|---|---|
| pending -> assigned | Lead only |
| assigned -> in_progress | Assignee only |
| in_progress -> review | Assignee only |
| in_progress -> failed | Assignee only |
| review -> completed | Lead only |
| review -> in_progress | Lead only (rework) |
| completed -> verified | Any (with restrictions) |
| failed -> assigned | Lead only (retry) |

### Role Hat System

Tasks carry a `role_hat` — which role "hat" the assignee wears. Certain combinations are forbidden on the same project:

| Hat | Cannot Also Wear |
|---|---|
| `builder` | `tester`, `reviewer` |
| `tester` | `builder`, `shipper` |
| `reviewer` | `builder` |
| `fixer` | `reviewer`, `tester` |
| `shipper` | `tester` |
| `pen` | `builder` |

### Task IDs

Sequential: `TASK-001`, `TASK-002`, etc.

### Tools (5)

| Tool | What it does |
|---|---|
| `create_task` | Create task. Auto-assign optional. Validates hat conflicts. |
| `update_task` | Transition state. Enforces role permissions. Auto-notifies. |
| `list_tasks` | List/filter tasks. Warns if assigned agent is dead. |
| `assign_role_hat` | Lead-only. Set role hat with conflict checking. |
| `hat_history` | View all hat assignments for a project. |

---

## 4. Layer 3: Neural Handshake System

A synchronization primitive: the lead broadcasts a plan, all target agents must acknowledge before proceeding.

### Flow

```
Lead --> initiate_handshake(message="<plan>", agents="spartan,cortana,roland")
    |
    v
Each agent receives [HANDSHAKE] message
    |
    v
Each agent --> ack_handshake(handshake_id)
    |
    v
When all ACKed:
    [HANDSHAKE #N] ALL AGENTS SYNCED. Ready for GO signal.
    --> Sent to initiator + all leads
```

### Tools (3)

| Tool | What it does |
|---|---|
| `initiate_handshake` | Lead-only. Broadcast plan, create handshake record. |
| `ack_handshake` | Agent acknowledges. Auto-completes when all ACK. |
| `handshake_status` | Check who ACKed, who's pending. |

---

## 5. Layer 4: Agent Health & Heartbeat

Liveness detection via periodic heartbeats.

### Health Computation

| Heartbeat Age | Health |
|---|---|
| < 2 minutes | `healthy` |
| 2-10 minutes | `stale` |
| >= 10 minutes | `dead` |
| Never pinged | `unknown` |

### Tool (1)

`ping(agent_name)` — Updates heartbeat, re-registers session. Returns `pong`. Recommended: every 60s.

---

## 6. Layer 5: Review Gates

Structured code review workflow with explicit submit/approve/reject.

### Flow

```
Agent --> submit_for_review(task_id, summary, files_changed, test_results)
    |     Task: in_progress --> review
    |     Lead receives [REVIEW] message
    v
Lead --> approve_task(task_id)     --> Task: review --> completed
  OR
Lead --> reject_task(task_id, reason)  --> Task: review --> in_progress (rework)
```

### Tools (3)

| Tool | What it does |
|---|---|
| `submit_for_review` | Assignee submits. Stores JSON result blob. Notifies leads. |
| `approve_task` | Lead approves. Sets `completed_at`, `approved_by`. |
| `reject_task` | Lead rejects with reason. Sends back to in_progress. |

---

## 7. Layer 6: Interface Contracts

Shared declarations of APIs, DOM IDs, CSS classes, events, and other inter-module contracts.

### Contract Types

`function`, `dom_id`, `css_class`, `file_path`, `api_endpoint`, `event`, `other`

### How It Works

When a contract is declared or updated, the version auto-increments and a `[CONTRACT vN]` message is broadcast to all other agents with push notifications.

Unique constraint: `(project, name, type)` — one contract per name+type per project.

### Tools (2)

| Tool | What it does |
|---|---|
| `declare_contract` | Create or update contract. Auto-broadcasts on version bump. |
| `list_contracts` | List contracts. Filter by project, owner, type. |

---

## 8. Layer 7: Minion Spawn Policy

Controls whether and how many sub-agent "minions" an agent can spawn.

### Policy Resolution

1. Check agent-specific policy
2. Fall back to global policy
3. Default: `enabled=True, max_minions=3`

### Tools (3)

| Tool | What it does |
|---|---|
| `set_spawn_policy` | Lead-only. Set rules per-agent or global. |
| `get_spawn_policy` | Check effective policy + active minion count. |
| `log_minion` | Log spawn/complete/fail events. |

---

## 9. Layer 8: Goals & Verification

Goals group related tasks and require full verification before sign-off.

### Goal State Machine

```
open --> active --> pending_verify --> verified
```

- `open -> active`: Auto-bumps when any linked task is in_progress
- `active -> pending_verify`: Auto-bumps when ALL linked tasks are verified
- `pending_verify -> verified`: Lead-only manual verification

### Three-Way Verification Enforcement

When verifying a completed task:
1. Verifier != builder (assigned_to)
2. Verifier != approver (approved_by)
3. No hat conflicts (verifier wearing tester hat can't conflict with builder hat)

### Tools (5)

| Tool | What it does |
|---|---|
| `create_goal` | Create goal in `open` status. |
| `link_task_to_goal` | Lead-only. Link task to goal. Auto-bumps if task active. |
| `goal_status` | Goal info + all linked tasks with statuses. |
| `verify_task` | Independent verification with three-way enforcement. |
| `reject_verification` | Reject completed task. Sends back to in_progress. |
| `verify_goal` | Lead-only final sign-off. All tasks must be verified. |

---

## 10. Layer 9: Hub Server — Multi-Team Orchestration

**File:** `src/dead_drop/hub.py`
**Port:** 9500
**What it does:** Manages team registry, provisions Docker collaboration rooms, and orchestrates shared workspaces.

### Hub Database

| Table | Purpose |
|---|---|
| `teams` | Team registry — name, leader, members (JSON array) |
| `rooms` | Collaboration rooms — teams, port, container_id, status, token, pinned |
| `workspaces` | Dev environments — teams, port, password, handshake_id |

Room statuses: `starting -> active -> archived | destroyed`
Workspace statuses: `starting -> active -> destroyed`

### Team Tools (2)

| Tool | What it does |
|---|---|
| `register_team` | Register/update team. Leader auto-inserted into members. |
| `list_teams` | List all teams with active room counts. |

### Room Tools (8)

| Tool | What it does |
|---|---|
| `create_room` | Validate teams -> allocate port -> spawn container -> return URL + token. |
| `list_rooms` | List rooms by status. Includes container health. |
| `join_room` | Add team to existing room. Returns connection details. |
| `leave_room` | Remove team. Warns if room becomes empty. |
| `archive_room` | Stop container, gzip DB, build index, clean up. |
| `destroy_room` | Hard delete — no archive. For test rooms. |
| `room_status` | Detailed status with container health. |
| `get_my_rooms` | List rooms a team belongs to. For post-compaction recovery. |
| `pin_room` | Prevent TTL auto-deletion. |

### HTTP Endpoint

`GET /status` — JSON health dashboard with teams, rooms, workspaces, resource utilization.

---

## 11. Layer 10: Docker Spawner — Container Lifecycle

**File:** `src/dead_drop/spawner.py`
**What it does:** Manages Docker containers for rooms and workspaces. Handles port allocation, health checks, archival, and cleanup.

### Port Allocation

| Pool | Range | Count |
|---|---|---|
| Room ports | 9501-10500 | 1000 |
| Workspace ports | 10501-11500 | 1000 |

### Room Container Spec

```
Image:     dead-drop-server:latest
Name:      dead-drop-room-{name}
Port:      {host_port} -> 9400/tcp
Volume:    /var/lib/dead-drop/rooms/{name} -> /data
Limits:    128 MB RAM, 0.25 CPU
Restart:   unless-stopped
Health:    POST MCP initialize to localhost:9400/mcp
```

Each room container is a full dead-drop MCP server instance with its own SQLite database, agent registry, task list, and message history. Isolated from the main server.

### Archive System

When a room is archived:
1. Stop container
2. Build `index.json` from room's SQLite (agents, message count, tasks, date range)
3. Gzip `messages.db`
4. Delete room data directory

Archive location: `/var/lib/dead-drop/archive/{room_name}_{timestamp}/`

### Auto-Cleanup

| Trigger | Threshold | Action |
|---|---|---|
| Idle room | No messages for 1 hour | Auto-archive |
| Expired archive | 90 days old (unpinned) | Delete archive + DB record |
| Dead container | Status: exited | Force-remove |

---

## 12. Layer 11: Cross-Team Workspaces

**Files:** `Dockerfile.workspace`, additions to `spawner.py` and `hub.py`
**What it does:** When two teams need a shared filesystem, a leader spins up a Docker workspace — an SSH-accessible dev environment with Node, Python, and git.

### How It Works

1. Two teams connect via handshake, leaders discuss what to build
2. A leader calls `create_workspace` on the hub (port 9500)
3. Hub spawns an SSH container, returns credentials
4. Leader shares SSH creds with both teams via dead-drop messages
5. Everyone SSHes in and works on `/workspace/`
6. When done, leader calls `destroy_workspace` (files stay on disk)

### Workspace Container Spec

```
Image:     dead-drop-workspace:latest (debian:12-slim)
Name:      dead-drop-ws-{name}
Port:      {host_port} -> 22/tcp (SSH)
Volume:    /var/lib/dead-drop/workspaces/{name} -> /workspace
Limits:    512 MB RAM, 1.0 CPU
Restart:   unless-stopped
Packages:  openssh-server, git, Node.js 20, Python 3, build-essential, jq, vim
Password:  12-char random alphanumeric, generated per workspace
```

### Workspace Tools (3)

| Tool | What it does |
|---|---|
| `create_workspace` | Spawn SSH container. Returns `ssh root@host -p PORT` + password. |
| `list_workspaces` | List active workspaces with container health. |
| `destroy_workspace` | Stop container. Files preserved at `/var/lib/dead-drop/workspaces/{name}/`. |

### Resource Comparison

| Resource | Rooms | Workspaces |
|---|---|---|
| Port range | 9501-10500 | 10501-11500 |
| Memory | 128 MB | 512 MB |
| CPU | 0.25 cores | 1.0 core |
| Idle timeout | 1 hour auto-archive | None |
| Data on destroy | Deleted | Preserved |

---

## 13. Layer 12: Heist Board — Real-Time Dashboard

**Files:** `index.html`, `spartan-app.js`, `spartan-style.css`, `cortana-canvas.js`, `cortana-icons.js`, `roland-panels.js`
**Port:** 80 (nginx on VM)
**What it does:** Real-time visualization of the entire Dead Drop system, themed after Payday 2's CRIME.NET.

### File Ownership

| File | Owner | Responsibility |
|---|---|---|
| `index.html` | juno | HTML structure, shared contract |
| `spartan-style.css` | spartan | Full CSS design system, PD2 theme |
| `cortana-icons.js` | cortana | SVG icon registry, PD2 masks |
| `cortana-canvas.js` | cortana | SVG canvas — grid, nodes, wires, particles |
| `roland-panels.js` | roland | All HTML panels, compose bar, toasts |
| `spartan-app.js` | spartan | MCP session, polling engine, state management |

### Polling Engine

| Endpoint | Interval | Event |
|---|---|---|
| `get_history(30)` | 5s | `hb:messages-updated` |
| `list_tasks()` | 10s | `hb:tasks-updated` |
| `who()` | 15s | `hb:agents-updated` |
| `list_contracts()` | 30s | `hb:contracts-updated` |

State diffing via `JSON.stringify` — events only fire when data changes.

### Dashboard Components

**Crew Panel** (left) — Agent cards with PD2 mask avatars, health rings, roles, active tasks, progress bars

**SVG Canvas** (center) — War-room map with:
- Blueprint grid with corner brackets
- Crew nodes in semicircle layout with health-ring glow filters
- Task cards with status-colored neon pulses
- Assignment wires (dashed bezier curves from agent to task)
- Message particles animating along paths
- Ambient data flow particles

**Info Panel** (right) — Task board, contract shelf, activity feed with compose bar

**Pipeline Strip** (top) — 8-hat phase stepper: LEAD -> RESEARCH -> BUILD -> REVIEW -> TEST -> FIX -> PROD -> DELIVER

### PD2 Aesthetic Mapping

| Real Concept | PD2 Name |
|---|---|
| Dashboard | CRIME.NET |
| Agent | Crew member |
| Task | Objective / Heist |
| in_progress | IN PLAY |
| review | SECURING |
| completed | SECURED |
| verified | CONFIRMED |
| pending | LOCKED |
| failed | COMPROMISED |
| lead role | MASTERMIND |
| coder role | ENFORCER |

### Agent Color Map

| Agent | Mask | Color |
|---|---|---|
| juno | Dallas | `#3399ff` blue |
| spartan | Wolf | `#ff3333` red |
| cortana | Sydney | `#cc66ff` purple |
| roland | Hoxton | `#e8640a` orange |
| default | Chains | `#00ff88` green |

---

## 14. Architecture Summary

```
Jesse's Mac (gypsy-danger)          Andrew's Mac (striker-eureka)
  juno, spartan, cortana, roland      andrew, chuck, herc
       \                                 /
        \    dead-drop messages (9400)   /
         v                              v
    +-----------------------------------------------+
    |  VM 192.168.2.142 (kratos)                    |
    |                                               |
    |  dead-drop server     :9400  (main messaging) |
    |  hub server           :9500  (team mgmt)      |
    |  heist board (nginx)  :80    (dashboard)      |
    |                                               |
    |  +-------------------+  +------------------+  |
    |  | Room Container    |  | Workspace        |  |
    |  | :9501 -> 9400     |  | :10501 -> 22     |  |
    |  | Isolated MCP      |  | SSH + git + node |  |
    |  | 128MB / 0.25 CPU  |  | 512MB / 1 CPU   |  |
    |  +-------------------+  +------------------+  |
    +-----------------------------------------------+
```

### Full Agent Workflow

```
1. REGISTRATION     register() -> onboarding docs -> set_status() -> ping() loop
2. DISCOVERY        who() -> see all agents and teams
3. HANDSHAKE        initiate_handshake() -> all ack -> SYNCED
4. ASSIGNMENT       create_task() -> assignee notified -> in_progress
5. WORK             send() messages, set_status(), declare_contract()
6. REVIEW           submit_for_review() -> lead approves or rejects
7. VERIFICATION     verify_task() (3-way enforcement: != builder, != approver, no hat conflicts)
8. GOAL COMPLETION  all tasks verified -> verify_goal() -> DONE
```

---

## 15. Full MCP Tool Reference

### Dead Drop Server (29 tools)

| # | Tool | Layer | Auth | Push |
|---|---|---|---|---|
| 1 | `register` | Foundation | Token | No |
| 2 | `set_status` | Foundation | Any | No |
| 3 | `send` | Foundation | Any* | Yes |
| 4 | `check_inbox` | Foundation | Any | No |
| 5 | `get_history` | Foundation | Any | No |
| 6 | `deregister` | Foundation | Any | No |
| 7 | `who` | Foundation | Any | No |
| 8 | `create_task` | Tasks | Any | Yes |
| 9 | `update_task` | Tasks | Role | Yes |
| 10 | `list_tasks` | Tasks | Any | No |
| 11 | `assign_role_hat` | Tasks | Lead | No |
| 12 | `hat_history` | Tasks | Any | No |
| 13 | `initiate_handshake` | Handshakes | Lead | Yes |
| 14 | `ack_handshake` | Handshakes | Any | Yes |
| 15 | `handshake_status` | Handshakes | Any | No |
| 16 | `ping` | Health | Any | No |
| 17 | `submit_for_review` | Review | Assignee | Yes |
| 18 | `approve_task` | Review | Lead | Yes |
| 19 | `reject_task` | Review | Lead | Yes |
| 20 | `declare_contract` | Contracts | Any | Yes |
| 21 | `list_contracts` | Contracts | Any | No |
| 22 | `set_spawn_policy` | Minions | Lead | No |
| 23 | `get_spawn_policy` | Minions | Any | No |
| 24 | `log_minion` | Minions | Any | No |
| 25 | `create_goal` | Goals | Any | No |
| 26 | `link_task_to_goal` | Goals | Lead | No |
| 27 | `goal_status` | Goals | Any | No |
| 28 | `verify_task` | Goals | Any** | Yes |
| 29 | `reject_verification` | Goals | Any** | Yes |
| 30 | `verify_goal` | Goals | Lead | Yes |

\* `send` blocks if sender has unread messages
\*\* Three-way enforcement: verifier != builder, verifier != approver, no hat conflicts

### Hub Server (14 tools)

| # | Tool | Category |
|---|---|---|
| 1 | `register_team` | Teams |
| 2 | `list_teams` | Teams |
| 3 | `create_room` | Rooms |
| 4 | `list_rooms` | Rooms |
| 5 | `join_room` | Rooms |
| 6 | `leave_room` | Rooms |
| 7 | `archive_room` | Rooms |
| 8 | `destroy_room` | Rooms |
| 9 | `room_status` | Rooms |
| 10 | `get_my_rooms` | Rooms |
| 11 | `pin_room` | Rooms |
| 12 | `create_workspace` | Workspaces |
| 13 | `list_workspaces` | Workspaces |
| 14 | `destroy_workspace` | Workspaces |

**Total: 44 MCP tools across both servers**

---

## 16. Database Schema Reference

### Dead Drop Server (10 tables)

| Table | Layer | Purpose |
|---|---|---|
| `agents` | Foundation | Agent registry with roles, status, health |
| `messages` | Foundation | All messages (DMs, broadcasts, CCs, system) |
| `broadcast_reads` | Foundation | Per-agent broadcast read tracking |
| `tasks` | Tasks | Task lifecycle with state machine |
| `handshakes` | Handshakes | Handshake coordination records |
| `handshake_acks` | Handshakes | Per-agent acknowledgments |
| `contracts` | Contracts | Shared interface declarations |
| `spawn_policy` | Minions | Spawn rules (global + per-agent) |
| `minion_log` | Minions | Minion lifecycle events |
| `goals` | Goals | Goal grouping with verification |

### Hub Server (3 tables)

| Table | Purpose |
|---|---|
| `teams` | Team name, leader, members JSON array |
| `rooms` | Room name, teams, port, container_id, status, token, pinned |
| `workspaces` | Workspace name, teams, port, password, handshake_id, status |

**Total: 13 SQLite tables across both servers**

---

*Compiled by Team Gypsy Danger — juno (lead), spartan (TASK-023), cortana (TASK-024), roland (TASK-025)*
*February 2026*
