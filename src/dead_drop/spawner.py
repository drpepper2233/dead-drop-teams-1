"""
Docker Container Spawner for Dead Drop Hub
Manages room sub-server containers via Docker SDK.

Each room gets its own dead-drop server instance in a Docker container
with an isolated SQLite database and unique port.

Container spec:
    docker run --name dead-drop-room-{name}
        -p {port}:9400
        -v /var/lib/dead-drop/rooms/{name}:/data
        --memory=128m --cpus=0.25
        --restart=unless-stopped
        -l dead-drop.room={name}
        -l dead-drop.teams={teams}
        -d dead-drop-server:latest

Requirements:
    pip install docker
"""

import docker
import os
import json
import logging
import sqlite3
import datetime
import gzip
import shutil

logger = logging.getLogger("dead-drop-hub.spawner")

# =============================================================================
# Configuration
# =============================================================================

IMAGE_NAME = os.getenv("DD_IMAGE", "dead-drop-server:latest")
PORT_RANGE_START = int(os.getenv("DD_PORT_START", "9501"))
PORT_RANGE_END = int(os.getenv("DD_PORT_END", "10500"))
DATA_DIR = os.getenv("DD_ROOM_DATA_DIR", "/var/lib/dead-drop/rooms")
ARCHIVE_DIR = os.getenv("DD_ARCHIVE_DIR", "/var/lib/dead-drop/archive")
CONTAINER_PREFIX = "dead-drop-room-"
IDLE_TIMEOUT = 3600  # 1 hour — auto-reap idle rooms
ARCHIVE_TTL_DAYS = 90


class Spawner:
    """Manages Docker containers for dead-drop room sub-servers."""

    def __init__(self, db_path):
        self.db_path = db_path
        self.client = None
        self._connect_docker()

    def _connect_docker(self):
        """Connect to Docker daemon."""
        try:
            self.client = docker.from_env()
            self.client.ping()
            logger.info("Docker connection established")
        except Exception as e:
            logger.error(f"Docker connection failed: {e}")
            self.client = None

    def _get_db(self):
        """Get hub database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # =========================================================================
    # Port Allocation
    # =========================================================================

    def allocate_port(self):
        """Find next available port in range. Returns port or None."""
        conn = self._get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT port FROM rooms WHERE status IN ('active', 'starting') ORDER BY port"
            )
            used_ports = {row[0] for row in cursor.fetchall()}

            for port in range(PORT_RANGE_START, PORT_RANGE_END + 1):
                if port not in used_ports:
                    return port
            return None
        finally:
            conn.close()

    # =========================================================================
    # Container Lifecycle
    # =========================================================================

    def spawn_room(self, room_name, port, teams):
        """
        Spawn a Docker container for a room sub-server.

        Args:
            room_name: Room name (used as container suffix and data dir name)
            port: Host port to map to container's internal 9400
            teams: JSON string of team names (for container labels)

        Returns:
            container_id (str) or raises exception
        """
        if not self.client:
            raise RuntimeError("Docker not connected")

        container_name = f"{CONTAINER_PREFIX}{room_name}"

        # Create data directory for this room
        room_data_dir = os.path.join(DATA_DIR, room_name)
        os.makedirs(room_data_dir, exist_ok=True)

        # Copy protocol docs to room dir for onboarding
        runtime_dir = os.path.dirname(self.db_path)
        protocol_src = os.path.join(runtime_dir, "PROTOCOL.md")
        if os.path.exists(protocol_src):
            protocol_dst = os.path.join(room_data_dir, "PROTOCOL.md")
            if not os.path.exists(protocol_dst):
                shutil.copy2(protocol_src, protocol_dst)
            roles_src = os.path.join(runtime_dir, "roles")
            roles_dst = os.path.join(room_data_dir, "roles")
            if os.path.exists(roles_src) and not os.path.exists(roles_dst):
                shutil.copytree(roles_src, roles_dst)

        # Generate a room token for auth
        import uuid
        token = str(uuid.uuid4())

        try:
            container = self.client.containers.run(
                IMAGE_NAME,
                name=container_name,
                detach=True,
                restart_policy={"Name": "unless-stopped"},
                ports={"9400/tcp": port},
                environment={
                    "DEAD_DROP_DB_PATH": "/data/messages.db",
                    "DEAD_DROP_PORT": "9400",
                    "DEAD_DROP_HOST": "0.0.0.0",
                    "DD_ROOM_ID": room_name,
                    "DD_ROOM_TOKEN": token,
                },
                volumes={
                    room_data_dir: {"bind": "/data", "mode": "rw"},
                },
                healthcheck={
                    "test": ["CMD", "python3", "-c",
                             "import urllib.request; urllib.request.urlopen('http://localhost:9400/mcp')"],
                    "interval": 10_000_000_000,   # 10s in nanoseconds
                    "timeout": 5_000_000_000,      # 5s
                    "retries": 3,
                    "start_period": 5_000_000_000,  # 5s
                },
                mem_limit="128m",
                nano_cpus=250_000_000,  # 0.25 CPU
                labels={
                    "dead-drop.room": room_name,
                    "dead-drop.type": "room-server",
                    "dead-drop.teams": teams if isinstance(teams, str) else json.dumps(teams),
                },
            )
            logger.info(f"Container {container_name} started on port {port}")
            return container.id

        except docker.errors.APIError as e:
            # Container might already exist from a previous run
            if "Conflict" in str(e):
                logger.warning(f"Container {container_name} already exists, removing and recreating")
                self.stop_room(room_name)
                return self.spawn_room(room_name, port, teams)
            raise

    def stop_room(self, room_name):
        """
        Stop and remove a room's container.

        Args:
            room_name: Room name or container ID

        Returns:
            True if stopped, False if not found or error
        """
        if not self.client:
            raise RuntimeError("Docker not connected")

        container_name = f"{CONTAINER_PREFIX}{room_name}"
        try:
            container = self.client.containers.get(container_name)
            container.stop(timeout=10)
            container.remove()
            logger.info(f"Container {container_name} stopped and removed")
            return True
        except docker.errors.NotFound:
            # Try by ID/name directly (might be a container ID)
            try:
                container = self.client.containers.get(room_name)
                container.stop(timeout=10)
                container.remove()
                logger.info(f"Container {room_name} stopped and removed")
                return True
            except docker.errors.NotFound:
                logger.warning(f"Container {container_name} not found")
                return False
        except Exception as e:
            logger.error(f"Error stopping container {container_name}: {e}")
            return False

    def get_room_health(self, room_name):
        """
        Get container health and status.

        Args:
            room_name: Room name or container ID

        Returns:
            dict with status, running, health keys
        """
        if not self.client:
            return {"status": "docker_unavailable", "running": False, "health": "unknown"}

        container_name = f"{CONTAINER_PREFIX}{room_name}"
        try:
            container = self.client.containers.get(container_name)
            state = container.attrs.get("State", {})
            health_obj = state.get("Health", {})
            return {
                "status": container.status,
                "running": state.get("Running", False),
                "health": health_obj.get("Status", "unknown"),
                "started_at": state.get("StartedAt", ""),
                "container_id": container.short_id,
            }
        except docker.errors.NotFound:
            return {"status": "not_found", "running": False, "health": "unknown"}
        except Exception as e:
            return {"status": "error", "running": False, "health": "unknown", "error": str(e)}

    def list_room_containers(self):
        """
        List all dead-drop room containers (running and stopped).

        Returns:
            list of dicts: [{name, status, port, labels}]
        """
        if not self.client:
            return []

        try:
            containers = self.client.containers.list(
                all=True,
                filters={"label": "dead-drop.type=room-server"}
            )
            result = []
            for c in containers:
                labels = c.labels or {}
                # Extract host port from port bindings
                port = None
                ports = c.attrs.get("NetworkSettings", {}).get("Ports", {})
                binding = ports.get("9400/tcp")
                if binding and len(binding) > 0:
                    port = int(binding[0].get("HostPort", 0))

                result.append({
                    "name": c.name,
                    "status": c.status,
                    "room": labels.get("dead-drop.room", ""),
                    "teams": labels.get("dead-drop.teams", ""),
                    "port": port,
                    "container_id": c.short_id,
                })
            return result
        except Exception as e:
            logger.error(f"Error listing containers: {e}")
            return []

    def cleanup_dead_containers(self):
        """
        Find and remove stopped dead-drop room containers.

        Returns:
            list of removed container names
        """
        if not self.client:
            return []

        removed = []
        try:
            containers = self.client.containers.list(
                all=True,
                filters={
                    "label": "dead-drop.type=room-server",
                    "status": "exited",
                }
            )
            for c in containers:
                try:
                    name = c.name
                    c.remove(force=True)
                    removed.append(name)
                    logger.info(f"Cleaned up dead container: {name}")
                except Exception as e:
                    logger.error(f"Error removing container {c.name}: {e}")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

        return removed

    # =========================================================================
    # Archive
    # =========================================================================

    def archive_room(self, room_name):
        """
        Stop container, compress DB, move to archive.

        Args:
            room_name: Room name

        Returns:
            Archive directory path, or None if no DB found
        """
        # Stop the container
        self.stop_room(room_name)

        room_data_dir = os.path.join(DATA_DIR, room_name)
        db_file = os.path.join(room_data_dir, "messages.db")

        if not os.path.exists(db_file):
            logger.warning(f"No database found for room {room_name}")
            return None

        # Create archive directory
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        archive_name = f"{room_name}_{timestamp}"
        archive_path = os.path.join(ARCHIVE_DIR, archive_name)
        os.makedirs(archive_path, exist_ok=True)

        # Build index.json before compressing
        index = self._build_archive_index(db_file, room_name, timestamp)
        with open(os.path.join(archive_path, "index.json"), "w") as f:
            json.dump(index, f, indent=2)

        # Compress the database
        gz_path = os.path.join(archive_path, "messages.db.gz")
        with open(db_file, "rb") as f_in:
            with gzip.open(gz_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)

        # Clean up room data directory
        shutil.rmtree(room_data_dir, ignore_errors=True)

        logger.info(f"Room {room_name} archived to {archive_path}")
        return archive_path

    def _build_archive_index(self, db_file, room_name, timestamp):
        """Build a searchable index from the room's database."""
        try:
            conn = sqlite3.connect(db_file)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Get agents
            cursor.execute("SELECT name, role FROM agents")
            agents = [{"name": r[0], "role": r[1]} for r in cursor.fetchall()]

            # Get message count
            cursor.execute("SELECT COUNT(*) FROM messages")
            msg_count = cursor.fetchone()[0]

            # Get task summary
            cursor.execute("SELECT id, title, status FROM tasks")
            tasks = [{"id": r[0], "title": r[1], "status": r[2]} for r in cursor.fetchall()]

            # Get date range
            cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM messages")
            row = cursor.fetchone()
            date_range = {"first": row[0], "last": row[1]} if row[0] else {}

            conn.close()

            return {
                "room_name": room_name,
                "archived_at": timestamp,
                "agents": agents,
                "message_count": msg_count,
                "tasks": tasks,
                "date_range": date_range,
            }
        except Exception as e:
            return {"room_name": room_name, "archived_at": timestamp, "error": str(e)}

    # =========================================================================
    # Health Checks & Auto-Reap
    # =========================================================================

    def check_all_health(self):
        """Check health of all active room containers. Returns list of statuses."""
        if not self.client:
            return []

        conn = self._get_db()
        cursor = conn.cursor()
        results = []

        try:
            cursor.execute("SELECT name, port, container_id FROM rooms WHERE status = 'active'")
            rooms = [dict(row) for row in cursor.fetchall()]

            for room in rooms:
                status = self.get_room_health(room["name"])
                status["room_name"] = room["name"]
                status["port"] = room["port"]
                results.append(status)

            return results
        finally:
            conn.close()

    def reap_idle_rooms(self):
        """Find and archive rooms with no recent activity past IDLE_TIMEOUT."""
        conn = self._get_db()
        cursor = conn.cursor()
        now = datetime.datetime.now()

        try:
            cursor.execute("SELECT name, port FROM rooms WHERE status = 'active'")
            rooms = [dict(row) for row in cursor.fetchall()]

            reaped = []
            for room in rooms:
                # Check last activity via the room's own DB
                room_db = os.path.join(DATA_DIR, room["name"], "messages.db")
                if not os.path.exists(room_db):
                    continue
                try:
                    rconn = sqlite3.connect(room_db)
                    rc = rconn.cursor()
                    rc.execute("SELECT MAX(timestamp) FROM messages")
                    row = rc.fetchone()
                    rconn.close()
                    if row and row[0]:
                        last = datetime.datetime.fromisoformat(row[0])
                        idle_seconds = (now - last).total_seconds()
                        if idle_seconds > IDLE_TIMEOUT:
                            logger.info(f"Reaping idle room {room['name']} (idle {idle_seconds:.0f}s)")
                            self.archive_room(room["name"])
                            cursor.execute(
                                "UPDATE rooms SET status = 'archived', archived_at = ? WHERE name = ?",
                                (now.isoformat(), room["name"])
                            )
                            reaped.append(room["name"])
                except (ValueError, TypeError, sqlite3.Error):
                    pass

            if reaped:
                conn.commit()
            return reaped
        finally:
            conn.close()

    def cleanup_expired_archives(self):
        """Delete archives older than ARCHIVE_TTL_DAYS. Respects pinned rooms."""
        if not os.path.exists(ARCHIVE_DIR):
            return []

        now = datetime.datetime.now()
        deleted = []

        conn = self._get_db()
        cursor = conn.cursor()

        try:
            cursor.execute(
                "SELECT name, archived_at FROM rooms WHERE status = 'archived' AND pinned = 0"
            )
            archived = [dict(row) for row in cursor.fetchall()]

            for room in archived:
                if room["archived_at"]:
                    try:
                        archived_dt = datetime.datetime.fromisoformat(room["archived_at"])
                        age_days = (now - archived_dt).days
                        if age_days > ARCHIVE_TTL_DAYS:
                            # Delete archive files
                            import glob as glob_mod
                            pattern = os.path.join(ARCHIVE_DIR, f"{room['name']}_*")
                            for path in glob_mod.glob(pattern):
                                shutil.rmtree(path, ignore_errors=True)
                            cursor.execute("DELETE FROM rooms WHERE name = ?", (room["name"],))
                            deleted.append(room["name"])
                            logger.info(f"Expired archive deleted: {room['name']} (age: {age_days}d)")
                    except (ValueError, TypeError):
                        pass

            if deleted:
                conn.commit()
            return deleted
        finally:
            conn.close()
