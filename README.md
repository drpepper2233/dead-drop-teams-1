# dead-drop-teams

A distributed MCP server system for multi-agent and multi-team AI coordination. Agents communicate via SQLite message passing with push notifications — no polling required.

Supports Claude Code, Gemini CLI, Codex CLI, and any MCP-compatible client.

## Three-Tier Architecture

```
TIER 1 — LOCAL SERVER (laptop, solo work)
┌──────────┐  ┌──────────┐  ┌──────────┐
│  juno    │  │ spartan  │  │ cortana  │
│  (lead)  │  │ (coder)  │  │ (coder)  │
└────┬─────┘  └────┬─────┘  └────┬─────┘
     │             │              │
     │  Streamable HTTP (:9400)   │
     ▼             ▼              ▼
┌─────────────────────────────────────────┐
│     Local Dead Drop Server              │
│     FastMCP + uvicorn on :9400          │
│     SQLite WAL · 19 Drift v2 tools     │
└─────────────────────────────────────────┘

TIER 2 — HUB SERVER (VM, multi-team coordination)
┌─────────────────────────────────────────┐
│     Dead Drop Hub (:9500)               │
│     Team registry · Room management     │
│     Docker container spawner            │
│     11 hub tools                        │
└──────────────┬──────────────────────────┘
               │ spawns
               ▼
TIER 3 — ROOM SUB-SERVERS (Docker containers, per-collaboration)
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ Room: project-x  │  │ Room: project-y  │  │ Room: project-z  │
│ :9501 · 128MB    │  │ :9502 · 128MB    │  │ :9503 · 128MB    │
│ Isolated SQLite  │  │ Isolated SQLite  │  │ Isolated SQLite  │
│ 19 Drift v2 tools│  │ 19 Drift v2 tools│  │ 19 Drift v2 tools│
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

**Tier 1** runs on your laptop for solo work. Nothing changes from the original setup.

**Tier 2** runs on a VM (or any server) and coordinates multiple teams. It manages a registry of teams and spawns Docker containers on demand.

**Tier 3** containers are isolated sub-servers — one per active collaboration. Each gets its own SQLite database, port, and the full Drift v2 protocol. When a collab ends, the container is stopped and the DB is archived.

## Drift Protocol v2 — 19 MCP Tools

The protocol is inspired by the neural bridge ("The Drift") from Pacific Rim. Two Jaeger pilots share one mind — each controls half the body, perfectly synchronized. Agents drift the same way.

### Core Messaging (7 tools)
| Tool | Purpose |
|------|---------|
| `register` | Register agent with name, role, description, team |
| `send` | Send message (blocked if you have unread messages) |
| `check_inbox` | Read unread messages, mark as read |
| `who` | List agents with health status and team grouping |
| `get_history` | Last N messages (for post-compaction recovery) |
| `set_status` | Update your status text |
| `deregister` | Remove yourself |

### Task Management (3 tools)
| Tool | Purpose |
|------|---------|
| `create_task` | Create and assign tasks with enforced state machine |
| `update_task` | Transition task state (server enforces valid transitions) |
| `list_tasks` | Query tasks with health warnings for stale agents |

### Neural Handshake (3 tools)
| Tool | Purpose |
|------|---------|
| `initiate_handshake` | Broadcast plan, require ACKs from all agents |
| `ack_handshake` | Confirm receipt and agreement with plan |
| `handshake_status` | Check who has ACKed — GO signal when all are synced |

### Health & Review (4 tools)
| Tool | Purpose |
|------|---------|
| `ping` | Heartbeat (call every 60s for persistent agents) |
| `submit_for_review` | Submit completed work for lead review |
| `approve_task` | Lead approves — task moves to completed |
| `reject_task` | Lead rejects — task goes back for rework |

### Interface Contracts (2 tools)
| Tool | Purpose |
|------|---------|
| `declare_contract` | Register shared interface (DOM IDs, function sigs, API endpoints) |
| `list_contracts` | Query declared interfaces |

### Task Lifecycle (Server-Enforced)

```
pending → assigned → in_progress → review → completed
               ↑          |            |
               └── failed ←────────────┘ (rework)
```

## Hub Server — 11 Tools

The hub manages teams and collaboration rooms. It runs on a dedicated server and spawns Docker containers for each active collaboration.

| Tool | Purpose |
|------|---------|
| `register_team` | Register a team (name, leader, members) |
| `list_teams` | Show all registered teams |
| `create_room` | Spawn a Docker container for a collaboration |
| `list_rooms` | Active rooms with container health |
| `join_room` | Add a team to an existing room |
| `leave_room` | Remove a team from a room |
| `archive_room` | Stop container, gzip DB, free port |
| `destroy_room` | Hard delete — no archive |
| `room_status` | Container health, port, teams, project |
| `get_my_rooms` | List rooms a team belongs to |
| `pin_room` | Exempt from TTL — keep archive permanently |

### Docker Sub-Servers

Each room gets its own container:

```
docker run --name dead-drop-room-{name}
    -p {port}:9400
    -v /var/lib/dead-drop/rooms/{name}:/data
    --memory=128m --nano-cpus=250000000
    --restart=unless-stopped
    dead-drop-server:latest
```

- **Port range:** 9501–10500 (1,000 slots)
- **Image:** `dead-drop-server:latest` (Python 3.12 slim, ~150MB)
- **Health check:** POST a valid MCP initialize request every 30s
- **Auto-reap:** Idle rooms archived after 1 hour
- **Archive TTL:** 90 days unless pinned

## Team-Scoped Agent Names

In shared rooms, agent names are namespaced by team:

```
gypsy-danger/juno        (Jesse's lead)
gypsy-danger/spartan     (Jesse's coder)
gypsy-danger/cortana     (Jesse's coder)
striker-eureka/chief     (Andrew's lead)
striker-eureka/spartan   (no collision!)
```

Short names (`spartan`) resolve when unambiguous. The server returns `AMBIGUOUS` if multiple teams have an agent with the same name — use the full `team/agent` format.

## Cross-Team Communication

- **Open messaging:** Any agent can send to any other agent in the room
- **Lead auto-CC:** All team leads are automatically CC'd on every message
- **Dual-sign tasks:** Cross-team task assignments require both leads to agree
- **Each lead reviews their own agents:** gypsy-danger/juno reviews gypsy-danger/spartan's work

## Quick Start

### Local Server (Tier 1)

```bash
# Install
git clone git@gitlab.com:Jessehampton05/dead-drop-teams.git ~/dead-drop-teams
cd ~/dead-drop-teams
pip install -e .

# Run
dead-drop-teams --http
# Server: http://127.0.0.1:9400/mcp
```

### Hub Server (Tier 2)

```bash
# On your VM/server (Debian 12):
sudo bash scripts/install-hub.sh

# Or manually:
pip install -e .
docker build -t dead-drop-server:latest .
dead-drop-hub
# Hub: http://0.0.0.0:9500/mcp
```

### MCP Client Config

Add to `~/.mcp.json`:

```json
{
  "mcpServers": {
    "dead-drop": {
      "type": "http",
      "url": "http://localhost:9400/mcp"
    },
    "dead-drop-hub": {
      "type": "http",
      "url": "http://<HUB-IP>:9500/mcp"
    },
    "dead-drop-room": {
      "type": "http",
      "url": "http://<HUB-IP>:9501/mcp"
    }
  }
}
```

### Agent Startup Prompt

```
You are <NAME>, a <ROLE> on team <TEAM>. Your lead is <LEAD>.

## Startup (do this NOW)
1. register(agent_name="<NAME>", role="<ROLE>", description="<DESC>", team="<TEAM>")
2. check_inbox(agent_name="<NAME>")
3. MANDATORY — launch background watcher:
   Run in background: ~/dead-drop-teams/scripts/wait-for-message.sh <NAME>
   Without the watcher you are DEAF. After every check_inbox, relaunch it.

## Rules
- MUST check_inbox before sending (server BLOCKS you)
- Lead is auto-CC'd on all messages
- Tasks: pending → in_progress → review → approved/rejected
- Neural Handshake: read the plan, ACK, then wait for GO signal
- Do NOT start work until handshake is complete
```

## Push Notification Flow

```
1. Agent A calls send(to_agent="spartan", message="...")
2. Server stores message in SQLite
3. Server pushes tools/list_changed to spartan's MCP session
4. Spartan's background watcher (fswatch) detects DB change
5. Watcher exits with alert: "YOU HAVE 1 UNREAD MESSAGE(S)"
6. Claude Code surfaces the completed background task
7. Agent sees alert → calls check_inbox → relaunches watcher
```

No polling. Event-driven at every layer.

## The Drift — Collaboration Protocol

### Neural Handshake (Sync Before Work)
Before starting any shared task, the lead initiates a handshake with the full plan. Every agent must ACK before anyone starts building. No one works until everyone is synced.

### Hemisphere Split
Each agent owns a distinct part of the system. Like Jaeger pilots where one controls the left side and the other controls the right, agents split by concern and work simultaneously.

### File Ownership by Name
Files carry the agent's name: `juno-index.html`, `spartan-app.js`, `cortana-engine.js`. If you see another agent's name on a file, don't touch it without messaging them first.

### Don't Chase the Rabbit
Stay on script. Don't go silent. If you hit a blocker, message immediately. Silence breaks the Drift.

## Key Files

| File | Purpose |
|------|---------|
| `src/dead_drop/server.py` | Local server — 19 Drift v2 tools, push notifications |
| `src/dead_drop/hub.py` | Hub server — team registry, room management, 11 tools |
| `src/dead_drop/spawner.py` | Docker container lifecycle manager |
| `src/dead_drop/archive.py` | Room archival — gzip, index, TTL cleanup |
| `Dockerfile` | Container image for room sub-servers |
| `scripts/install-hub.sh` | One-command hub deployment on Debian 12 |
| `scripts/wait-for-message.sh` | Background watcher (fswatch) for push alerts |
| `docs/PROTOCOL.md` | Agent-facing protocol rules |
| `docs/roles/` | Role profiles (lead, coder, researcher, builder) |

## Example Flow

1. Jesse's team (Gypsy Danger) works solo on local dead-drop (:9400)
2. Jesse says "collab with Andrew on Project X"
3. Juno calls `create_room("project-x", teams="gypsy-danger,striker-eureka")` on hub
4. Hub spawns Docker container on :9501, returns room token
5. Both teams' agents connect to :9501, register with team-scoped names
6. Leads discuss scope, jointly assign tasks (dual-sign)
7. Neural Handshake — all agents ACK
8. Agents work in parallel, open messaging, lead reviews own agents
9. Done — `archive_room("project-x")` stops container, gzips DB

## Requirements

- Python 3.11+
- Docker (for hub/room containers)
- macOS or Linux
- `fswatch` (macOS: `brew install fswatch`, Linux: `apt install fswatch`)

## License

MIT
