# Timeline: Hub, Spawner & Infrastructure Layer

> Dead Drop's orchestration tier — how teams get rooms, how rooms become containers,
> and how containers live, die, and get archived.

---

## 1. The Hub Server (`hub.py`)

**What it is:** The multi-team coordination layer. A single MCP server (FastMCP) that runs
on the Proxmox VM (kratos), manages the team registry, provisions collaboration rooms,
and orchestrates shared workspaces.

**Port:** `9500` (env `DD_HUB_PORT`)
**Transport:** Streamable HTTP at `/mcp`
**Database:** SQLite at `/var/lib/dead-drop/hub.db` (WAL mode, 5s busy timeout)

### Hub Database Schema

| Table | Primary Key | Purpose |
|---|---|---|
| `teams` | `name` | Team registry — leader, members (JSON array), created_at |
| `rooms` | `name` | Collaboration rooms — teams, port, container_id, status, token, pinned flag |
| `workspaces` | `name` | Dev environments — teams, port, container_id, password, handshake_id |

Room statuses: `starting` → `active` → `archived` | `destroyed`
Workspace statuses: `starting` → `active` → `destroyed`

### 14 MCP Tools

**Team Management (2)**

| Tool | Args | What it does |
|---|---|---|
| `register_team` | team_name, leader, members | Register or update a team. Leader auto-inserted into members list. |
| `list_teams` | — | List all teams with active room counts. |

**Room Lifecycle (8)**

| Tool | Args | What it does |
|---|---|---|
| `create_room` | creator, name, teams, project? | Validates teams exist → allocates port → spawns Docker container → returns URL + token. |
| `list_rooms` | status? | List rooms filtered by status. Includes container health for active rooms. Tokens hidden. |
| `join_room` | team_name, room_name | Add a team to an existing room. Returns connection details + token. |
| `leave_room` | team_name, room_name | Remove a team from a room. Warns if room becomes empty. |
| `archive_room` | room_name | Stop container → gzip DB → build index.json → clean up data dir. |
| `destroy_room` | room_name | Hard delete — stop container, delete DB and all data. No archive. For test rooms. |
| `room_status` | room_name | Detailed status: container health, port, teams, project. Token hidden. |
| `get_my_rooms` | team_name | List all active rooms a team belongs to. Designed for post-compaction recovery. |
| `pin_room` | room_name | Prevent TTL auto-deletion. Pinned rooms kept indefinitely. |

**Workspace Management (3)**

| Tool | Args | What it does |
|---|---|---|
| `create_workspace` | creator, name, teams, project?, handshake_id? | Spawns SSH-accessible dev container. Returns SSH command + password. |
| `list_workspaces` | status? | List workspaces with container health. Passwords hidden. |
| `destroy_workspace` | workspace_name | Stop container. Files preserved on host at `/var/lib/dead-drop/workspaces/{name}/`. |

### HTTP Endpoint

`GET /status` — JSON health dashboard: all teams, active rooms (with container health),
active workspaces, and resource utilization (ports used/available, container counts).

---

## 2. The Spawner (`spawner.py`)

**What it is:** The Docker container lifecycle manager. The Hub delegates all container
operations to the Spawner. It handles port allocation, container creation, health checks,
archive compression, idle reaping, and expired archive cleanup.

**Docker connection:** Uses `docker.from_env()` (Docker SDK). Connects on init, used
throughout the Spawner's lifetime.

### Port Allocation

Two separate port pools, both scanned sequentially for the first unused port:

| Pool | Range | Default | Env vars |
|---|---|---|---|
| **Room ports** | 9501–10500 | 1000 ports | `DD_PORT_START`, `DD_PORT_END` |
| **Workspace ports** | 10501–11500 | 1000 ports | `DD_WS_PORT_START`, `DD_WS_PORT_END` |

Allocation queries the hub DB for ports with status `active` or `starting`, then iterates
the range to find the first gap.

### Room Container Lifecycle

```
create_room (Hub)
    │
    ├─ allocate_port()          → first free port in 9501–10500
    ├─ INSERT rooms (starting)  → DB record with empty container_id
    │
    └─ spawn_room()             → Docker container:
         │
         │  Image:    dead-drop-server:latest  (env DD_IMAGE)
         │  Name:     dead-drop-room-{name}
         │  Port map: {host_port} → 9400/tcp
         │  Volume:   /var/lib/dead-drop/rooms/{name} → /data
         │  Limits:   128 MB RAM, 0.25 CPU
         │  Restart:  unless-stopped
         │  Env:      DEAD_DROP_DB_PATH=/data/messages.db
         │            DEAD_DROP_PORT=9400
         │            DD_ROOM_ID={name}
         │            DD_ROOM_TOKEN={uuid}
         │  Labels:   dead-drop.room={name}
         │            dead-drop.type=room-server
         │            dead-drop.teams={json}
         │  Health:   POST /mcp with MCP initialize (30s interval, 5s timeout, 3 retries, 10s start)
         │
         └─ UPDATE rooms (active) → store container_id

         On conflict (container name exists): stop + remove old, respawn.
```

**Stop flow:** `stop_room()` → `container.stop(timeout=10)` → `container.remove()`.
Falls back to lookup by container ID if name not found.

### Workspace Container Lifecycle

```
create_workspace (Hub)
    │
    ├─ allocate_workspace_port()  → first free port in 10501–11500
    ├─ generate password          → 12-char alphanumeric (secrets module)
    ├─ INSERT workspaces (starting)
    │
    └─ spawn_workspace()          → Docker container:
         │
         │  Image:    dead-drop-workspace:latest  (env DD_WORKSPACE_IMAGE)
         │  Name:     dead-drop-ws-{name}
         │  Port map: {host_port} → 22/tcp  (SSH)
         │  Volume:   /var/lib/dead-drop/workspaces/{name} → /workspace
         │  Limits:   512 MB RAM, 1.0 CPU
         │  Restart:  unless-stopped
         │  Env:      DD_WORKSPACE_PASSWORD={password}
         │            DD_WORKSPACE_NAME={name}
         │  Labels:   dead-drop.workspace={name}
         │            dead-drop.type=workspace
         │            dead-drop.teams={json}
         │  Health:   nc -w2 localhost 22 | grep SSH (30s interval, 5s timeout, 3 retries, 5s start)
         │
         └─ UPDATE workspaces (active)
```

**Destroy flow:** `stop_workspace()` removes container. Host volume at
`/var/lib/dead-drop/workspaces/{name}/` is preserved (not deleted).

### Archive System

When a room is archived (`archive_room`):

1. **Stop** the container (`stop_room`)
2. **Build index** from the room's SQLite DB:
   - Agents (name, role)
   - Message count
   - Task summary (id, title, status)
   - Date range (first/last message)
3. **Write** `index.json` to archive directory
4. **Gzip** `messages.db` → `messages.db.gz`
5. **Delete** the room data directory

Archive location: `/var/lib/dead-drop/archive/{room_name}_{YYYYMMDD-HHMMSS}/`

### Health Checks

`check_all_health()` — Iterates all active rooms in the DB, calls `get_room_health()` for
each. Returns Docker status, running state, health status, start time, and container ID.

`get_room_health()` / `get_workspace_health()` — Queries Docker container attrs for State
and Health objects. Returns one of:
- `{ status: "running", running: true, health: "healthy" }` — normal
- `{ status: "not_found", running: false }` — container missing
- `{ status: "docker_unavailable" }` — Docker daemon unreachable
- `{ status: "error", error: "..." }` — unexpected failure

### Auto-Reap & Cleanup

**Idle room reaping** (`reap_idle_rooms`):
- Scans all active rooms
- Opens each room's own `/data/messages.db`
- Checks `MAX(timestamp)` from messages table
- If idle > `IDLE_TIMEOUT` (default: 3600s / 1 hour) → archive the room
- Pinned rooms are not affected (handled at archive-TTL level, not reap level)

**Expired archive cleanup** (`cleanup_expired_archives`):
- Scans archived (non-pinned) rooms in hub DB
- If `archived_at` > `ARCHIVE_TTL_DAYS` (default: 90 days) → delete archive files + DB record
- Pinned rooms (`pinned = 1`) are exempt

**Dead container cleanup** (`cleanup_dead_containers`):
- Lists all containers with label `dead-drop.type=room-server` and status `exited`
- Force-removes each one

---

## 3. Docker Images

### `Dockerfile` — Room Server Image (`dead-drop-server:latest`)

```
Base:       python:3.12-slim
Install:    pip install . (from pyproject.toml)
Env:        DEAD_DROP_DB_PATH=/data/messages.db
            DEAD_DROP_PORT=9400
            DEAD_DROP_HOST=0.0.0.0
            DEAD_DROP_ROOM_TOKEN=""
            DEAD_DROP_TEAM=""
Volume:     /data (persistent SQLite DB)
Expose:     9400
Healthcheck: POST MCP initialize to localhost:9400/mcp (30s/5s/10s/3)
Entrypoint: python -m dead_drop.server --http --host 0.0.0.0 --port 9400
Extras:     Copies docs/PROTOCOL.md and docs/roles/ into /data for agent onboarding
```

Each room container is a full dead-drop MCP server instance. Agents connect via
`http://localhost:{host_port}/mcp` and authenticate with the room token.

### `Dockerfile.workspace` — Workspace Image (`dead-drop-workspace:latest`)

```
Base:       debian:12-slim
Packages:   openssh-server, git, curl, wget, python3, pip, venv, build-essential,
            jq, vim-tiny, less, procps, Node.js 20 (nodesource)
SSH:        PermitRootLogin yes, PasswordAuthentication yes
            ClientAliveInterval 60, ClientAliveCountMax 10
Volume:     /workspace (shared dev files)
Expose:     22
Healthcheck: nc -w2 localhost 22 | grep SSH (30s/5s/5s/3)
Entrypoint: Set root password from DD_WORKSPACE_PASSWORD env → exec sshd -D
```

Workspace containers are full dev environments. Teams connect via SSH, share a
`/workspace` volume that persists even after container destruction.

---

## 4. Package Metadata (`pyproject.toml`)

```
Name:           dead-drop-teams
Version:        1.0.0
Python:         >=3.11
Build:          hatchling
Dependencies:   mcp[cli]>=0.1.0, docker>=7.0.0
Entry points:   dead-drop-teams → dead_drop.server:main
                dead-drop-hub   → dead_drop.hub:main
Package:        src/dead_drop/
```

Two CLI entry points: `dead-drop-teams` runs the base server (agent messaging),
`dead-drop-hub` runs the hub (team/room/workspace orchestration).

---

## 5. Data Flow Timeline

```
Agent registers team         Hub registers workspace
        │                            │
        v                            v
  register_team              create_workspace
        │                            │
        v                            v
  teams table                 Spawner.spawn_workspace
        │                            │
        v                            v
  create_room                 SSH container on 10501+
        │
        v
  Spawner.allocate_port
        │
        v
  Spawner.spawn_room
        │
        v
  Docker container             Each room is isolated:
  dead-drop-room-{name}       - Own SQLite DB
  Port {9501+} → 9400         - Own MCP server
  128MB / 0.25 CPU             - Own agent registry
        │                      - Own task list
        │                      - Own message history
        v
  Agents connect via
  http://host:{port}/mcp
  with room token
        │
        │   ┌─────── idle > 1h ───────┐
        v   v                         v
  Active room ──── reap_idle ──→ archive_room
        │                         │
        │                         ├─ stop container
        │                         ├─ build index.json
        │                         ├─ gzip messages.db
        │                         └─ delete data dir
        │
        │   ┌─── archive > 90d ───┐
        v   v      (unpinned)     v
  Archived room ─ cleanup ──→ DELETE from DB + files
```

---

## 6. Resource Limits Summary

| Resource | Rooms | Workspaces |
|---|---|---|
| Port range | 9501–10500 (1000) | 10501–11500 (1000) |
| Memory | 128 MB | 512 MB |
| CPU | 0.25 cores | 1.0 core |
| Restart policy | unless-stopped | unless-stopped |
| Idle timeout | 1 hour → auto-archive | N/A |
| Archive TTL | 90 days (unpinned) | N/A (files preserved) |
| Health interval | 30s | 30s |
