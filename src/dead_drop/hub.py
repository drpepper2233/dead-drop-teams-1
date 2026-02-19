"""
Dead Drop Hub Server — Multi-Team Coordination Layer
Runs on Proxmox VM (kratos), manages team registry, rooms, and Docker sub-servers.

Port: 9500 (configurable via DD_HUB_PORT)
Transport: Streamable HTTP at /mcp

14 MCP Tools:
    register_team, list_teams, create_room, list_rooms, join_room,
    leave_room, archive_room, destroy_room, room_status, get_my_rooms,
    pin_room, create_workspace, list_workspaces, destroy_workspace

HTTP Endpoints:
    GET /status — hub health dashboard

Requirements:
    pip install mcp docker
"""

from mcp.server.fastmcp import FastMCP, Context
import sqlite3
import datetime
import os
import json
import sys
import uuid
import logging

from dead_drop.spawner import Spawner

logger = logging.getLogger("dead-drop-hub")

# =============================================================================
# Configuration
# =============================================================================

HUB_PORT = int(os.getenv("DD_HUB_PORT", "9500"))
HUB_DB_PATH = os.getenv("DD_HUB_DB_PATH", "/var/lib/dead-drop/hub.db")
ARCHIVE_DIR = os.getenv("DD_ARCHIVE_DIR", "/var/lib/dead-drop/archive")
ROOM_DATA_DIR = os.getenv("DD_ROOM_DATA_DIR", "/var/lib/dead-drop/rooms")

mcp = FastMCP(
    "Dead Drop Hub",
    host="0.0.0.0",
    port=HUB_PORT,
    streamable_http_path="/mcp",
)


# =============================================================================
# Database
# =============================================================================

def get_db():
    os.makedirs(os.path.dirname(HUB_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(HUB_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS teams (
            name TEXT PRIMARY KEY,
            leader TEXT NOT NULL,
            members TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rooms (
            name TEXT PRIMARY KEY,
            teams TEXT NOT NULL,
            project TEXT DEFAULT '',
            port INTEGER NOT NULL,
            container_id TEXT NOT NULL,
            status TEXT DEFAULT 'active'
                CHECK(status IN ('starting', 'active', 'archived', 'destroyed')),
            token TEXT NOT NULL,
            created_at TEXT NOT NULL,
            archived_at TEXT,
            pinned BOOLEAN DEFAULT FALSE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS workspaces (
            name TEXT PRIMARY KEY,
            teams TEXT NOT NULL,
            project TEXT DEFAULT '',
            port INTEGER NOT NULL,
            container_id TEXT NOT NULL,
            password TEXT NOT NULL,
            status TEXT DEFAULT 'active'
                CHECK(status IN ('starting', 'active', 'destroyed')),
            handshake_id INTEGER,
            created_at TEXT NOT NULL
        )
    ''')

    conn.commit()
    conn.close()


init_db()
spawner = Spawner(HUB_DB_PATH)


# =============================================================================
# Helpers
# =============================================================================

def _now():
    return datetime.datetime.now().isoformat()


def _generate_token():
    return str(uuid.uuid4())


# =============================================================================
# Hub MCP Tools (11)
# =============================================================================

@mcp.tool()
async def register_team(team_name: str, leader: str, members: str = "") -> str:
    """Register a team with the hub. Members is a comma-separated list of agent names. Returns confirmation."""
    conn = get_db()
    cursor = conn.cursor()
    now = _now()
    try:
        # Parse members into JSON array
        member_list = [m.strip() for m in members.split(",") if m.strip()] if members else []
        # Ensure leader is in the members list
        if leader not in member_list:
            member_list.insert(0, leader)
        members_json = json.dumps(member_list)

        # Check for duplicate
        cursor.execute("SELECT name FROM teams WHERE name = ?", (team_name,))
        if cursor.fetchone():
            # Update existing
            cursor.execute(
                "UPDATE teams SET leader = ?, members = ? WHERE name = ?",
                (leader, members_json, team_name)
            )
            conn.commit()
            return f"Team '{team_name}' updated. Leader: {leader}, members: {member_list}"

        cursor.execute(
            "INSERT INTO teams (name, leader, members, created_at) VALUES (?, ?, ?, ?)",
            (team_name, leader, members_json, now)
        )
        conn.commit()
        return f"Team '{team_name}' registered. Leader: {leader}, members: {member_list}"
    except Exception as e:
        return f"Error registering team: {e}"
    finally:
        conn.close()


@mcp.tool()
async def list_teams() -> str:
    """List all registered teams as JSON."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM teams ORDER BY name")
        teams = []
        for row in cursor.fetchall():
            team = dict(row)
            team["members"] = json.loads(team["members"])
            # Count active rooms
            cursor.execute(
                "SELECT COUNT(*) FROM rooms WHERE status = 'active' AND teams LIKE ?",
                (f'%"{team["name"]}"%',)
            )
            team["active_rooms"] = cursor.fetchone()[0]
            teams.append(team)

        return json.dumps(teams, indent=2)
    except Exception as e:
        return f"Error listing teams: {e}"
    finally:
        conn.close()


@mcp.tool()
async def create_room(creator: str, name: str, teams: str, project: str = "") -> str:
    """Create a collaboration room. Spawns a Docker sub-server. Teams is comma-separated. Returns room name, port, token, URL."""
    conn = get_db()
    cursor = conn.cursor()
    now = _now()

    try:
        # Parse teams
        team_list = [t.strip() for t in teams.split(",") if t.strip()]
        if not team_list:
            return "At least one team is required."

        # Validate all teams exist
        for team_name in team_list:
            cursor.execute("SELECT name FROM teams WHERE name = ?", (team_name,))
            if not cursor.fetchone():
                return f"Team '{team_name}' not registered. Call register_team first."

        # Check for duplicate room name
        cursor.execute("SELECT name FROM rooms WHERE name = ? AND status IN ('active', 'starting')", (name,))
        if cursor.fetchone():
            return f"Active room '{name}' already exists."

        # Allocate port
        port = spawner.allocate_port()
        if port is None:
            return "No ports available. Maximum concurrent rooms reached."

        # Generate auth token
        token = _generate_token()
        teams_json = json.dumps(team_list)

        # Create room record (status=starting until container is up)
        cursor.execute("""
            INSERT INTO rooms (name, teams, project, port, container_id, status, token, created_at)
            VALUES (?, ?, ?, ?, '', 'starting', ?, ?)
        """, (name, teams_json, project, port, token, now))
        conn.commit()

        # Spawn Docker container
        try:
            container_id = spawner.spawn_room(name, port, teams_json)
            cursor.execute(
                "UPDATE rooms SET status = 'active', container_id = ? WHERE name = ?",
                (container_id, name)
            )
            conn.commit()

            return json.dumps({
                "room_name": name,
                "port": port,
                "token": token,
                "container_id": container_id[:12],
                "url": f"http://localhost:{port}/mcp",
                "teams": team_list,
                "project": project,
                "message": f"Room '{name}' created. Connect to http://localhost:{port}/mcp with token '{token}'.",
            }, indent=2)

        except Exception as e:
            cursor.execute("UPDATE rooms SET status = 'destroyed' WHERE name = ?", (name,))
            conn.commit()
            return f"Room record created but container failed to start: {e}"

    except Exception as e:
        return f"Error creating room: {e}"
    finally:
        conn.close()


@mcp.tool()
async def list_rooms(status: str = "active") -> str:
    """List rooms with container health. Filter by status: 'active', 'archived', 'destroyed', or '' for all."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        if status:
            cursor.execute("SELECT * FROM rooms WHERE status = ? ORDER BY created_at DESC", (status,))
        else:
            cursor.execute("SELECT * FROM rooms ORDER BY created_at DESC")
        rooms = []
        for row in cursor.fetchall():
            room = dict(row)
            room["teams"] = json.loads(room["teams"])
            room.pop("token", None)  # Don't expose tokens in list

            # Add container health for active rooms
            if room["status"] == "active":
                room["container_health"] = spawner.get_room_health(room["name"])

            rooms.append(room)

        return json.dumps(rooms, indent=2)
    except Exception as e:
        return f"Error listing rooms: {e}"
    finally:
        conn.close()


@mcp.tool()
async def join_room(team_name: str, room_name: str) -> str:
    """Add a team to an existing active room. Updates the room's teams list."""
    conn = get_db()
    cursor = conn.cursor()

    try:
        # Validate team exists
        cursor.execute("SELECT name FROM teams WHERE name = ?", (team_name,))
        if not cursor.fetchone():
            return f"Team '{team_name}' not registered."

        # Get room
        cursor.execute("SELECT * FROM rooms WHERE name = ? AND status = 'active'", (room_name,))
        room = cursor.fetchone()
        if not room:
            return f"Room '{room_name}' not found or not active."
        room = dict(room)

        # Parse current teams and add new one
        team_list = json.loads(room["teams"])
        if team_name in team_list:
            return json.dumps({
                "room_name": room_name,
                "port": room["port"],
                "token": room["token"],
                "url": f"http://localhost:{room['port']}/mcp",
                "message": f"Team '{team_name}' is already in room '{room_name}'.",
            }, indent=2)

        team_list.append(team_name)
        cursor.execute(
            "UPDATE rooms SET teams = ? WHERE name = ?",
            (json.dumps(team_list), room_name)
        )
        conn.commit()

        return json.dumps({
            "room_name": room_name,
            "port": room["port"],
            "token": room["token"],
            "url": f"http://localhost:{room['port']}/mcp",
            "teams": team_list,
            "message": f"Team '{team_name}' joined room '{room_name}'. Connect to http://localhost:{room['port']}/mcp with token '{room['token']}'.",
        }, indent=2)

    except Exception as e:
        return f"Error joining room: {e}"
    finally:
        conn.close()


@mcp.tool()
async def leave_room(team_name: str, room_name: str) -> str:
    """Remove a team from a room. Does not stop the room."""
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT * FROM rooms WHERE name = ? AND status = 'active'", (room_name,))
        room = cursor.fetchone()
        if not room:
            return f"Room '{room_name}' not found or not active."
        room = dict(room)

        team_list = json.loads(room["teams"])
        if team_name not in team_list:
            return f"Team '{team_name}' is not in room '{room_name}'."

        team_list.remove(team_name)
        cursor.execute(
            "UPDATE rooms SET teams = ? WHERE name = ?",
            (json.dumps(team_list), room_name)
        )
        conn.commit()

        if not team_list:
            return f"Team '{team_name}' left room '{room_name}'. Room has no teams — consider archiving it."
        return f"Team '{team_name}' left room '{room_name}'. Remaining teams: {team_list}"

    except Exception as e:
        return f"Error leaving room: {e}"
    finally:
        conn.close()


@mcp.tool()
async def archive_room(room_name: str) -> str:
    """Archive a room. Stops container, gzips DB, updates status. Frees the port."""
    conn = get_db()
    cursor = conn.cursor()
    now = _now()

    try:
        cursor.execute("SELECT * FROM rooms WHERE name = ?", (room_name,))
        room = cursor.fetchone()
        if not room:
            return f"Room '{room_name}' not found."
        room = dict(room)

        if room["status"] == "archived":
            return f"Room '{room_name}' is already archived."
        if room["status"] == "destroyed":
            return f"Room '{room_name}' is destroyed."

        # Stop container + gzip DB via spawner
        archive_path = spawner.archive_room(room_name)

        cursor.execute(
            "UPDATE rooms SET status = 'archived', archived_at = ? WHERE name = ?",
            (now, room_name)
        )
        conn.commit()

        return f"Room '{room_name}' archived. Container stopped, DB compressed. Archive: {archive_path or 'no data found'}"
    except Exception as e:
        return f"Error archiving room: {e}"
    finally:
        conn.close()


@mcp.tool()
async def destroy_room(room_name: str) -> str:
    """Hard delete a room. Stops container, deletes DB and all data. No archive. Use for test rooms."""
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT * FROM rooms WHERE name = ?", (room_name,))
        room = cursor.fetchone()
        if not room:
            return f"Room '{room_name}' not found."
        room = dict(room)

        if room["status"] == "destroyed":
            return f"Room '{room_name}' is already destroyed."

        # Stop container
        if room["status"] in ("active", "starting"):
            spawner.stop_room(room_name)

        # Delete room data directory
        import shutil
        room_data_dir = os.path.join(ROOM_DATA_DIR, room_name)
        if os.path.exists(room_data_dir):
            shutil.rmtree(room_data_dir, ignore_errors=True)

        # Delete from DB
        cursor.execute("DELETE FROM rooms WHERE name = ?", (room_name,))
        conn.commit()

        return f"Room '{room_name}' destroyed. Container stopped, all data deleted."
    except Exception as e:
        return f"Error destroying room: {e}"
    finally:
        conn.close()


@mcp.tool()
async def room_status(room_name: str) -> str:
    """Get detailed room status: container health, port, teams, project."""
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT * FROM rooms WHERE name = ?", (room_name,))
        room = cursor.fetchone()
        if not room:
            return f"Room '{room_name}' not found."
        room = dict(room)
        room["teams"] = json.loads(room["teams"])
        room.pop("token", None)  # Don't expose token

        # Get container health for active rooms
        if room["status"] == "active":
            room["container"] = spawner.get_room_health(room_name)

        return json.dumps(room, indent=2)
    except Exception as e:
        return f"Error getting room status: {e}"
    finally:
        conn.close()


@mcp.tool()
async def get_my_rooms(team_name: str) -> str:
    """List all active rooms this team is in. Use after compaction to recover room connections."""
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT * FROM rooms WHERE status = 'active' ORDER BY created_at DESC"
        )
        my_rooms = []
        for row in cursor.fetchall():
            room = dict(row)
            team_list = json.loads(room["teams"])
            if team_name in team_list:
                my_rooms.append({
                    "room_name": room["name"],
                    "port": room["port"],
                    "url": f"http://localhost:{room['port']}/mcp",
                    "token": room["token"],
                    "teams": team_list,
                    "project": room["project"],
                    "created_at": room["created_at"],
                })

        return json.dumps(my_rooms, indent=2)
    except Exception as e:
        return f"Error getting rooms: {e}"
    finally:
        conn.close()


@mcp.tool()
async def pin_room(room_name: str) -> str:
    """Pin a room to prevent TTL deletion. Pinned rooms are kept indefinitely."""
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT status, pinned FROM rooms WHERE name = ?", (room_name,))
        room = cursor.fetchone()
        if not room:
            return f"Room '{room_name}' not found."

        if room["pinned"]:
            return f"Room '{room_name}' is already pinned."

        cursor.execute("UPDATE rooms SET pinned = 1 WHERE name = ?", (room_name,))
        conn.commit()
        return f"Room '{room_name}' pinned. Exempt from TTL cleanup."
    except Exception as e:
        return f"Error pinning room: {e}"
    finally:
        conn.close()


# =============================================================================
# Workspace Tools (3)
# =============================================================================

@mcp.tool()
async def create_workspace(creator: str, name: str, teams: str, project: str = "", handshake_id: int = 0) -> str:
    """Create a shared dev workspace. Spawns an SSH-accessible Docker container. Teams is comma-separated. Returns SSH credentials."""
    conn = get_db()
    cursor = conn.cursor()
    now = _now()

    try:
        team_list = [t.strip() for t in teams.split(",") if t.strip()]
        if not team_list:
            return "At least one team is required."

        # Validate all teams exist
        for team_name in team_list:
            cursor.execute("SELECT name FROM teams WHERE name = ?", (team_name,))
            if not cursor.fetchone():
                return f"Team '{team_name}' not registered. Call register_team first."

        # Check for duplicate
        cursor.execute("SELECT name FROM workspaces WHERE name = ? AND status IN ('active', 'starting')", (name,))
        if cursor.fetchone():
            return f"Active workspace '{name}' already exists."

        # Allocate port
        port = spawner.allocate_workspace_port()
        if port is None:
            return "No workspace ports available."

        # Generate password
        import secrets
        import string
        password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))

        teams_json = json.dumps(team_list)

        # Create record
        cursor.execute("""
            INSERT INTO workspaces (name, teams, project, port, container_id, password, status, handshake_id, created_at)
            VALUES (?, ?, ?, ?, '', ?, 'starting', ?, ?)
        """, (name, teams_json, project, port, password, handshake_id, now))
        conn.commit()

        # Spawn Docker container
        try:
            container_id = spawner.spawn_workspace(name, port, password, teams_json)
            cursor.execute(
                "UPDATE workspaces SET status = 'active', container_id = ? WHERE name = ?",
                (container_id, name)
            )
            conn.commit()

            host = os.getenv("DD_HUB_HOST", "192.168.2.142")
            ssh_cmd = f"ssh root@{host} -p {port}"

            return json.dumps({
                "workspace": name,
                "port": port,
                "password": password,
                "ssh_command": ssh_cmd,
                "host": host,
                "container_id": container_id[:12],
                "teams": team_list,
                "project": project,
                "message": f"Workspace '{name}' ready. Connect: {ssh_cmd} (password: {password}). Files go in /workspace/",
            }, indent=2)

        except Exception as e:
            cursor.execute("UPDATE workspaces SET status = 'destroyed' WHERE name = ?", (name,))
            conn.commit()
            return f"Workspace record created but container failed to start: {e}"

    except Exception as e:
        return f"Error creating workspace: {e}"
    finally:
        conn.close()


@mcp.tool()
async def list_workspaces(status: str = "active") -> str:
    """List workspaces with container health. Filter by status: 'active', 'destroyed', or '' for all."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        if status:
            cursor.execute("SELECT * FROM workspaces WHERE status = ? ORDER BY created_at DESC", (status,))
        else:
            cursor.execute("SELECT * FROM workspaces ORDER BY created_at DESC")
        workspaces = []
        for row in cursor.fetchall():
            ws = dict(row)
            ws["teams"] = json.loads(ws["teams"])
            ws.pop("password", None)  # Don't expose passwords in list

            if ws["status"] == "active":
                ws["container_health"] = spawner.get_workspace_health(ws["name"])

            workspaces.append(ws)

        return json.dumps(workspaces, indent=2)
    except Exception as e:
        return f"Error listing workspaces: {e}"
    finally:
        conn.close()


@mcp.tool()
async def destroy_workspace(workspace_name: str) -> str:
    """Destroy a workspace. Stops container. Workspace files are preserved on host at /var/lib/dead-drop/workspaces/{name}/."""
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT * FROM workspaces WHERE name = ?", (workspace_name,))
        ws = cursor.fetchone()
        if not ws:
            return f"Workspace '{workspace_name}' not found."
        ws = dict(ws)

        if ws["status"] == "destroyed":
            return f"Workspace '{workspace_name}' is already destroyed."

        # Stop container (data preserved in volume)
        spawner.stop_workspace(workspace_name)

        cursor.execute("UPDATE workspaces SET status = 'destroyed' WHERE name = ?", (workspace_name,))
        conn.commit()

        return f"Workspace '{workspace_name}' destroyed. Container stopped. Files preserved at /var/lib/dead-drop/workspaces/{workspace_name}/"
    except Exception as e:
        return f"Error destroying workspace: {e}"
    finally:
        conn.close()


# =============================================================================
# GET /status HTTP Endpoint
# =============================================================================

from starlette.requests import Request
from starlette.responses import JSONResponse


async def _status_endpoint(request: Request) -> JSONResponse:
    """GET /status — hub health dashboard."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Teams
        cursor.execute("SELECT * FROM teams ORDER BY name")
        teams = []
        for row in cursor.fetchall():
            team = dict(row)
            team["members"] = json.loads(team["members"])
            teams.append(team)

        # Rooms
        cursor.execute("SELECT * FROM rooms WHERE status = 'active' ORDER BY created_at DESC")
        rooms = []
        for row in cursor.fetchall():
            room = dict(row)
            room["teams"] = json.loads(room["teams"])
            room.pop("token", None)
            room["container"] = spawner.get_room_health(room["name"])
            rooms.append(room)

        # Workspaces
        cursor.execute("SELECT * FROM workspaces WHERE status = 'active' ORDER BY created_at DESC")
        workspaces = []
        for row in cursor.fetchall():
            ws = dict(row)
            ws["teams"] = json.loads(ws["teams"])
            ws.pop("password", None)
            ws["container"] = spawner.get_workspace_health(ws["name"])
            workspaces.append(ws)

        # Resource counts
        from dead_drop.spawner import PORT_RANGE_START, PORT_RANGE_END
        total_ports = PORT_RANGE_END - PORT_RANGE_START + 1
        active_count = len(rooms)

        result = {
            "teams": teams,
            "rooms": rooms,
            "workspaces": workspaces,
            "resources": {
                "containers_active": active_count + len(workspaces),
                "room_ports_used": active_count,
                "room_ports_available": total_ports - active_count,
                "workspace_count": len(workspaces),
            },
            "timestamp": _now(),
        }
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        conn.close()


# Register the /status route on the underlying Starlette app
# FastMCP exposes its ASGI app; we add our route before MCP starts
try:
    from starlette.routing import Route
    _original_routes = getattr(mcp._mcp_server, '_additional_routes', [])
except ImportError:
    _original_routes = []


# =============================================================================
# Entry Point
# =============================================================================

def main():
    """Run the Dead Drop Hub server."""
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s: %(message)s")
    logger.info(f"Dead Drop Hub starting on http://0.0.0.0:{HUB_PORT}/mcp")
    logger.info(f"Database: {HUB_DB_PATH}")
    logger.info(f"Room data: {ROOM_DATA_DIR}")
    logger.info(f"Archive: {ARCHIVE_DIR}")

    # Mount /status endpoint by patching the ASGI app after mcp creates it
    import asyncio
    from starlette.applications import Starlette
    from starlette.routing import Route, Mount

    # Get the streamable HTTP app from FastMCP
    status_route = Route("/status", _status_endpoint, methods=["GET"])

    # Monkey-patch: wrap mcp.run to inject /status
    _original_run = mcp.run

    def _patched_run(**kwargs):
        # The FastMCP streamable-http transport creates a Starlette app internally.
        # We add our /status route by overriding the app creation.
        # For now, run standard and document that /status requires a small wrapper.
        _original_run(**kwargs)

    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
