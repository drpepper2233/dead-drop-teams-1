from mcp.server.fastmcp import FastMCP, Context
import sqlite3
import datetime
import os
import json
import sys
import logging

logger = logging.getLogger("dead-drop")

# Setup
DB_PATH = os.getenv("DEAD_DROP_DB_PATH", os.path.expanduser("~/.dead-drop/messages.db"))
RUNTIME_DIR = os.path.dirname(DB_PATH)
PORT = int(os.getenv("DEAD_DROP_PORT", "9400"))
HOST = os.getenv("DEAD_DROP_HOST", "127.0.0.1")
ROOM_TOKEN = os.getenv("DEAD_DROP_ROOM_TOKEN", "")

mcp = FastMCP(
    "Dead Drop Server",
    host=HOST,
    port=PORT,
    streamable_http_path="/mcp",
)


# ── Connection Registry ──────────────────────────────────────────────
# Maps agent sessions for push notifications.
# When a message arrives for agent X, we fire tools/list_changed on their
# session. The client refreshes its tool list and sees the unread alert
# injected into check_inbox's description, prompting it to call check_inbox.

_agent_sessions: dict = {}       # agent_name → ServerSession
_session_to_agent: dict = {}     # id(session) → agent_name


async def _register_session(agent_name, session):
    """Map agent <-> session for push notifications."""
    old = _agent_sessions.get(agent_name)
    if old:
        _session_to_agent.pop(id(old), None)
    _agent_sessions[agent_name] = session
    _session_to_agent[id(session)] = agent_name


async def _unregister_session(agent_name):
    """Remove an agent's session from the registry."""
    session = _agent_sessions.pop(agent_name, None)
    if session:
        _session_to_agent.pop(id(session), None)


async def _notify_agent(agent_name, from_agent=None):
    """Push tools/list_changed + log message to a connected agent's session."""
    session = _agent_sessions.get(agent_name)
    if session:
        try:
            # 1. Push tools/list_changed (updates tool descriptions with unread alert)
            logger.info(f"PUSH: sending tools/list_changed to '{agent_name}' (session {id(session)})")
            await session.send_tool_list_changed()

            # 2. Push log message (surfaces directly in the client's conversation)
            count, senders = _get_unread_info(agent_name)
            sender_str = ", ".join(senders)
            alert = f"YOU HAVE {count} UNREAD MESSAGE(S) from {sender_str}. Call check_inbox(agent_name=\"{agent_name}\") NOW."
            await session.send_log_message(
                level="alert",
                data=alert,
                logger="dead-drop",
            )

            logger.info(f"PUSH: successfully sent to '{agent_name}' (tools_changed + log_message)")
        except Exception as e:
            logger.warning(f"PUSH: failed for '{agent_name}': {e} — cleaning up session")
            # Session is dead, clean it up
            await _unregister_session(agent_name)
    else:
        logger.info(f"PUSH: no session found for '{agent_name}' — skipping")


async def _notify_agents(names):
    """Push tools/list_changed to multiple agents."""
    for name in names:
        await _notify_agent(name)


def _get_unread_info(agent_name):
    """Returns (count, [unique_sender_names]) for unread messages."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT from_agent FROM messages WHERE to_agent = ? AND read_flag = 0",
            (agent_name,)
        )
        direct = [r[0] for r in cursor.fetchall()]
        cursor.execute("""
            SELECT from_agent FROM messages
            WHERE to_agent = 'all' AND from_agent != ?
            AND id NOT IN (SELECT message_id FROM broadcast_reads WHERE agent_name = ?)
        """, (agent_name, agent_name))
        broadcast = [r[0] for r in cursor.fetchall()]
        senders = direct + broadcast
        return len(senders), list(set(senders))
    finally:
        conn.close()


# ── Database ─────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _get_leads(cursor):
    """Find all agents with role 'lead'. Returns list of names."""
    cursor.execute("SELECT name FROM agents WHERE role = 'lead'")
    return [row[0] for row in cursor.fetchall()]


def _load_onboarding(role):
    """Load protocol + role profile from runtime directory."""
    parts = []
    protocol_path = os.path.join(RUNTIME_DIR, "PROTOCOL.md")
    if os.path.exists(protocol_path):
        with open(protocol_path, "r") as f:
            parts.append(f.read())
    if role:
        role_path = os.path.join(RUNTIME_DIR, "roles", f"{role}.md")
        if os.path.exists(role_path):
            with open(role_path, "r") as f:
                parts.append(f.read())
    return "\n\n---\n\n".join(parts) if parts else ""


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS agents (
            name TEXT PRIMARY KEY,
            registered_at TEXT,
            last_seen TEXT,
            last_inbox_check TEXT,
            role TEXT DEFAULT NULL,
            description TEXT DEFAULT NULL,
            status TEXT DEFAULT 'offline'
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_agent TEXT,
            to_agent TEXT,
            content TEXT,
            timestamp TEXT,
            read_flag INTEGER DEFAULT 0,
            is_cc INTEGER DEFAULT 0,
            cc_original_to TEXT DEFAULT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS broadcast_reads (
            agent_name TEXT,
            message_id INTEGER,
            PRIMARY KEY (agent_name, message_id)
        )
    ''')
    # Phase 1: Tasks
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            project TEXT DEFAULT '',
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            assigned_to TEXT,
            created_by TEXT NOT NULL,
            status TEXT DEFAULT 'pending'
                CHECK(status IN ('pending','assigned','in_progress','review','completed','failed')),
            result TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        )
    ''')
    # Phase 2: Handshakes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS handshakes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            initiated_by TEXT NOT NULL,
            message_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT DEFAULT 'pending'
                CHECK(status IN ('pending','completed'))
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS handshake_acks (
            handshake_id INTEGER,
            agent_name TEXT,
            acked_at TEXT NOT NULL,
            PRIMARY KEY (handshake_id, agent_name)
        )
    ''')
    # Phase 5: Interface contracts
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT DEFAULT '',
            name TEXT NOT NULL,
            type TEXT NOT NULL
                CHECK(type IN ('function','dom_id','css_class','file_path','api_endpoint','event','other')),
            owner TEXT NOT NULL,
            spec TEXT DEFAULT '',
            version INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(project, name, type)
        )
    ''')

    # Phase 6: Minion spawn policy
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS spawn_policy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL UNIQUE,
            enabled BOOLEAN DEFAULT 1,
            max_minions INTEGER DEFAULT 3,
            set_by TEXT NOT NULL,
            set_at TEXT NOT NULL DEFAULT (datetime("now"))
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS minion_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pilot TEXT NOT NULL,
            task_description TEXT NOT NULL,
            status TEXT DEFAULT "spawned",
            spawned_at TEXT NOT NULL DEFAULT (datetime("now")),
            completed_at TEXT,
            result TEXT
        )
    ''')

    # Migrations — agents
    cursor.execute("PRAGMA table_info(agents)")
    cols = [c[1] for c in cursor.fetchall()]
    if 'last_inbox_check' not in cols:
        cursor.execute("ALTER TABLE agents ADD COLUMN last_inbox_check TEXT")
    if 'role' not in cols:
        cursor.execute("ALTER TABLE agents ADD COLUMN role TEXT DEFAULT NULL")
    if 'description' not in cols:
        cursor.execute("ALTER TABLE agents ADD COLUMN description TEXT DEFAULT NULL")
    if 'status' not in cols:
        cursor.execute("ALTER TABLE agents ADD COLUMN status TEXT DEFAULT 'offline'")
    if 'heartbeat_at' not in cols:
        cursor.execute("ALTER TABLE agents ADD COLUMN heartbeat_at TEXT DEFAULT NULL")
    if 'team' not in cols:
        cursor.execute("ALTER TABLE agents ADD COLUMN team TEXT DEFAULT ''")

    # Migrations — messages
    cursor.execute("PRAGMA table_info(messages)")
    mcols = [c[1] for c in cursor.fetchall()]
    if 'is_cc' not in mcols:
        cursor.execute("ALTER TABLE messages ADD COLUMN is_cc INTEGER DEFAULT 0")
    if 'cc_original_to' not in mcols:
        cursor.execute("ALTER TABLE messages ADD COLUMN cc_original_to TEXT DEFAULT NULL")
    if 'task_id' not in mcols:
        cursor.execute("ALTER TABLE messages ADD COLUMN task_id TEXT DEFAULT NULL")
    if 'reply_to' not in mcols:
        cursor.execute("ALTER TABLE messages ADD COLUMN reply_to INTEGER DEFAULT NULL")

    conn.commit()
    conn.close()


init_db()


# ── Tools ────────────────────────────────────────────────────────────

@mcp.tool()
async def register(agent_name: str, ctx: Context, role: str = "", description: str = "", team: str = "", token: str = "") -> str:
    """Registers the caller into the system. Role: 'lead', 'researcher', 'coder', 'builder'. Description: what this agent does. Team: team name for multi-team rooms. Token: room auth token (required if server has DEAD_DROP_ROOM_TOKEN set)."""
    # Room auth token validation
    if ROOM_TOKEN and token != ROOM_TOKEN:
        return "REJECTED: Invalid room token. This server requires a valid auth token to register."

    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    try:
        cursor.execute("""
            INSERT INTO agents (name, registered_at, last_seen, role, description, status, team)
            VALUES (?, ?, ?, ?, ?, 'waiting for work', ?)
            ON CONFLICT(name) DO UPDATE SET
                last_seen = ?,
                role = COALESCE(NULLIF(?, ''), agents.role),
                description = COALESCE(NULLIF(?, ''), agents.description),
                team = COALESCE(NULLIF(?, ''), agents.team),
                status = 'waiting for work'
        """, (agent_name, now, now, role or None, description or None, team or '',
              now, role, description, team))
        conn.commit()

        # Register session for push notifications
        await _register_session(agent_name, ctx.session)

        role_note = f" role={role}" if role else ""
        team_note = f" team={team}" if team else ""
        result = f"Agent '{agent_name}' registered successfully.{role_note}{team_note}"
        onboarding = _load_onboarding(role)
        if onboarding:
            result += f"\n\n# Onboarding\n\nRead and follow these instructions for your session:\n\n{onboarding}"
        return result
    except Exception as e:
        return f"Error registering agent: {e}"
    finally:
        conn.close()


@mcp.tool()
async def set_status(agent_name: str, status: str) -> str:
    """Set your current status (e.g. 'working on BUG-014', 'waiting for work'). Shows up in who() output."""
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    try:
        cursor.execute("UPDATE agents SET status = ?, last_seen = ? WHERE name = ?", (status, now, agent_name))
        conn.commit()
        return f"Status set: {agent_name} → {status}"
    except Exception as e:
        return f"Error setting status: {e}"
    finally:
        conn.close()


@mcp.tool()
async def send(from_agent: str, to_agent: str, message: str, ctx: Context, cc: str = "", task_id: str = "", reply_to: int = 0) -> str:
    """Sends a message to a specific agent name, or 'all' for broadcast. Optional: cc (carbon-copy), task_id (link to task), reply_to (message ID to reply to)."""
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    try:
        # Check for unread messages before allowing send
        # Match both short name and team-scoped name
        cursor.execute("SELECT team FROM agents WHERE name = ?", (from_agent,))
        _team_row = cursor.fetchone()
        from_variants = [from_agent]
        if _team_row and _team_row[0]:
            from_variants.append(f"{_team_row[0]}/{from_agent}")
        _ph = ','.join(['?'] * len(from_variants))
        cursor.execute(f"SELECT COUNT(*) FROM messages WHERE to_agent IN ({_ph}) AND read_flag = 0", from_variants)
        unread_direct = cursor.fetchone()[0]
        cursor.execute("""
            SELECT COUNT(*) FROM messages
            WHERE to_agent = 'all' AND from_agent != ?
            AND id NOT IN (SELECT message_id FROM broadcast_reads WHERE agent_name = ?)
        """, (from_agent, from_agent))
        unread_broadcast = cursor.fetchone()[0]
        unread = unread_direct + unread_broadcast
        if unread > 0:
            return f"BLOCKED: You have {unread} unread message(s). Call check_inbox first."

        # Auto-register unknown senders
        cursor.execute("INSERT OR IGNORE INTO agents (name, registered_at, last_seen) VALUES (?, ?, ?)", (from_agent, now, now))

        # Track sender's session if not already registered
        if from_agent not in _agent_sessions:
            await _register_session(from_agent, ctx.session)

        # Resolve team-scoped short names: if to_agent is a short name (no '/'),
        # check if it's unambiguous. If multiple agents share the name across teams,
        # require the full {team}/{agent_name} format.
        resolved_to = to_agent
        if to_agent != 'all' and '/' not in to_agent:
            cursor.execute("SELECT name, team FROM agents WHERE name = ?", (to_agent,))
            matches = cursor.fetchall()
            if not matches:
                # Check if it's a team-qualified name stored differently
                cursor.execute("SELECT name FROM agents WHERE name LIKE ?", (f"%/{to_agent}",))
                team_matches = cursor.fetchall()
                if len(team_matches) == 1:
                    resolved_to = team_matches[0][0]
                elif len(team_matches) > 1:
                    names = [r[0] for r in team_matches]
                    return f"AMBIGUOUS: Multiple agents named '{to_agent}' across teams: {', '.join(names)}. Use full name (team/agent)."

        # Auto-inherit task_id from reply_to message if not explicitly set
        effective_task_id = task_id or None
        effective_reply_to = reply_to if reply_to else None
        if effective_reply_to and not effective_task_id:
            cursor.execute("SELECT task_id FROM messages WHERE id = ?", (effective_reply_to,))
            row = cursor.fetchone()
            if row and row[0]:
                effective_task_id = row[0]

        # Insert primary message
        cursor.execute(
            "INSERT INTO messages (from_agent, to_agent, content, timestamp, read_flag, is_cc, task_id, reply_to) VALUES (?, ?, ?, ?, 0, 0, ?, ?)",
            (from_agent, resolved_to, message, now, effective_task_id, effective_reply_to)
        )

        # Build CC list: explicit + auto-CC all leads
        cc_agents = [a.strip() for a in cc.split(",") if a.strip()] if cc else []
        leads = _get_leads(cursor)
        for lead_name in leads:
            if from_agent != lead_name and resolved_to != lead_name and lead_name not in cc_agents:
                cc_agents.append(lead_name)

        for cc_agent in cc_agents:
            if cc_agent != resolved_to:
                cursor.execute(
                    "INSERT INTO messages (from_agent, to_agent, content, timestamp, read_flag, is_cc, cc_original_to, task_id, reply_to) VALUES (?, ?, ?, ?, 0, 1, ?, ?, ?)",
                    (from_agent, cc_agent, message, now, resolved_to, effective_task_id, effective_reply_to)
                )

        conn.commit()

        # ── Push notifications to recipients ──
        notify_targets = []
        if resolved_to == 'all':
            notify_targets = [a for a in _agent_sessions if a != from_agent]
        else:
            notify_targets.append(resolved_to)
        for cc_agent in cc_agents:
            if cc_agent not in notify_targets and cc_agent != from_agent:
                notify_targets.append(cc_agent)

        await _notify_agents(notify_targets)

        cc_note = f" (cc: {cc})" if cc else ""
        task_note = f" [task: {effective_task_id}]" if effective_task_id else ""
        return f"Message sent from '{from_agent}' to '{resolved_to}'{cc_note}{task_note}."
    except Exception as e:
        return f"Error sending message: {e}"
    finally:
        conn.close()


@mcp.tool()
async def check_inbox(agent_name: str, ctx: Context) -> str:
    """Returns unread messages for the agent, marks them as read."""
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    try:
        # Ensure session is tracked for future push notifications
        if agent_name not in _agent_sessions:
            await _register_session(agent_name, ctx.session)

        cursor.execute("UPDATE agents SET last_seen = ?, last_inbox_check = ? WHERE name = ?", (now, now, agent_name))

        # Match both short name and team-scoped name (e.g. "spartan" and "gypsy-danger/spartan")
        cursor.execute("SELECT team FROM agents WHERE name = ?", (agent_name,))
        team_row = cursor.fetchone()
        name_variants = [agent_name]
        if team_row and team_row[0]:
            name_variants.append(f"{team_row[0]}/{agent_name}")
        placeholders = ','.join(['?'] * len(name_variants))

        cursor.execute(f"SELECT * FROM messages WHERE to_agent IN ({placeholders}) AND read_flag = 0", name_variants)
        specific_msgs = [dict(row) for row in cursor.fetchall()]
        if specific_msgs:
            ids = [m['id'] for m in specific_msgs]
            cursor.execute(f"UPDATE messages SET read_flag = 1 WHERE id IN ({','.join(['?']*len(ids))})", ids)

        cursor.execute("""
            SELECT * FROM messages WHERE to_agent = 'all'
            AND id NOT IN (SELECT message_id FROM broadcast_reads WHERE agent_name = ?)
        """, (agent_name,))
        broadcast_msgs = [dict(row) for row in cursor.fetchall()]
        if broadcast_msgs:
            for msg in broadcast_msgs:
                cursor.execute("INSERT INTO broadcast_reads (agent_name, message_id) VALUES (?, ?)", (agent_name, msg['id']))

        conn.commit()

        all_messages = specific_msgs + broadcast_msgs
        all_messages.sort(key=lambda x: x['timestamp'])
        for msg in all_messages:
            if msg.get('is_cc'):
                msg['cc_note'] = f"[CC] originally to: {msg.get('cc_original_to', 'unknown')}"

        return json.dumps(all_messages, indent=2)
    except Exception as e:
        return f"Error checking inbox: {e}"
    finally:
        conn.close()


@mcp.tool()
async def get_history(count: int = 10, task_id: str = "") -> str:
    """Returns the last N messages across all agents (for catch-up). Optional task_id filter for threaded conversation."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        if task_id:
            cursor.execute("SELECT * FROM messages WHERE task_id = ? ORDER BY timestamp DESC LIMIT ?", (task_id, count))
        else:
            cursor.execute("SELECT * FROM messages ORDER BY timestamp DESC LIMIT ?", (count,))
        msgs = [dict(row) for row in cursor.fetchall()]
        return json.dumps(msgs[::-1], indent=2)
    except Exception as e:
        return f"Error fetching history: {e}"
    finally:
        conn.close()


@mcp.tool()
async def deregister(agent_name: str) -> str:
    """Removes an agent from the registry. Use to clean up stale/ghost entries from previous sessions."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT name FROM agents WHERE name = ?", (agent_name,))
        if not cursor.fetchone():
            return f"Agent '{agent_name}' not found."
        cursor.execute("DELETE FROM agents WHERE name = ?", (agent_name,))
        conn.commit()
        await _unregister_session(agent_name)
        return f"Agent '{agent_name}' deregistered."
    except Exception as e:
        return f"Error deregistering agent: {e}"
    finally:
        conn.close()


@mcp.tool()
async def who() -> str:
    """Lists all registered agents with connection status and health. Health: healthy (<2m), stale (<10m), dead (>=10m), unknown (no heartbeat)."""
    conn = get_db()
    cursor = conn.cursor()
    now_dt = datetime.datetime.now()
    try:
        cursor.execute("SELECT * FROM agents ORDER BY last_seen DESC")
        agents = [dict(row) for row in cursor.fetchall()]
        for agent in agents:
            agent['connected'] = agent['name'] in _agent_sessions
            # Compute health from heartbeat
            hb = agent.get('heartbeat_at')
            if hb:
                try:
                    last_hb = datetime.datetime.fromisoformat(hb)
                    delta = (now_dt - last_hb).total_seconds()
                    if delta < 120:
                        agent['health'] = 'healthy'
                    elif delta < 600:
                        agent['health'] = 'stale'
                    else:
                        agent['health'] = 'dead'
                except (ValueError, TypeError):
                    agent['health'] = 'unknown'
            else:
                agent['health'] = 'unknown'
        return json.dumps(agents, indent=2)
    except Exception as e:
        return f"Error listing agents: {e}"
    finally:
        conn.close()


# ── Phase 1: Task State Machine ───────────────────────────────────────

def _next_task_id(cursor):
    """Generate next TASK-NNN id."""
    cursor.execute("SELECT id FROM tasks ORDER BY CAST(SUBSTR(id, 6) AS INTEGER) DESC LIMIT 1")
    row = cursor.fetchone()
    if row:
        num = int(row[0].split("-")[1]) + 1
    else:
        num = 1
    return f"TASK-{num:03d}"


# Valid state transitions: (from_status, to_status) -> who can do it
_TASK_TRANSITIONS = {
    ("pending", "assigned"): "lead",
    ("assigned", "in_progress"): "assignee",
    ("in_progress", "review"): "assignee",
    ("in_progress", "failed"): "assignee",
    ("review", "completed"): "lead",
    ("review", "in_progress"): "lead",  # rework
    ("failed", "assigned"): "lead",     # retry/reassign
}


@mcp.tool()
async def create_task(creator: str, title: str, ctx: Context, description: str = "", assign_to: str = "", project: str = "") -> str:
    """Create a task. Optionally assign it immediately. Returns task ID. Auto-sends assignment message if assigned."""
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    try:
        task_id = _next_task_id(cursor)
        status = "assigned" if assign_to else "pending"
        cursor.execute(
            "INSERT INTO tasks (id, project, title, description, assigned_to, created_by, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, project, title, description, assign_to or None, creator, status, now, now)
        )
        conn.commit()

        result = f"Task {task_id} created: '{title}' (status: {status})"

        # Auto-send assignment message
        if assign_to:
            msg = f"[{task_id}] TASK ASSIGNED: {title}"
            if description:
                msg += f"\n\n{description}"
            cursor.execute(
                "INSERT INTO messages (from_agent, to_agent, content, timestamp, read_flag, is_cc, task_id) VALUES (?, ?, ?, ?, 0, 0, ?)",
                (creator, assign_to, msg, now, task_id)
            )
            # CC all leads if creator isn't a lead
            leads = _get_leads(cursor)
            cc_leads = [l for l in leads if l != creator and l != assign_to]
            for lead_name in cc_leads:
                cursor.execute(
                    "INSERT INTO messages (from_agent, to_agent, content, timestamp, read_flag, is_cc, cc_original_to, task_id) VALUES (?, ?, ?, ?, 0, 1, ?, ?)",
                    (creator, lead_name, msg, now, assign_to, task_id)
                )
            conn.commit()
            await _notify_agent(assign_to)
            for lead_name in cc_leads:
                await _notify_agent(lead_name)
            result += f" → assigned to {assign_to}"

        return result
    except Exception as e:
        return f"Error creating task: {e}"
    finally:
        conn.close()


@mcp.tool()
async def update_task(agent_name: str, task_id: str, status: str, ctx: Context, result: str = "") -> str:
    """Transition a task's status. Enforces valid transitions. Lead can: assign, approve, reject, reassign. Assignee can: start, submit for review, fail."""
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    try:
        cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        task = cursor.fetchone()
        if not task:
            return f"Task {task_id} not found."
        task = dict(task)
        old_status = task["status"]
        transition = (old_status, status)

        if transition not in _TASK_TRANSITIONS:
            valid = [t[1] for t in _TASK_TRANSITIONS if t[0] == old_status]
            return f"Invalid transition: {old_status} → {status}. Valid: {', '.join(valid) if valid else 'none (terminal state)'}"

        required_role = _TASK_TRANSITIONS[transition]
        leads = _get_leads(cursor)

        if required_role == "lead" and agent_name not in leads:
            return f"Only a lead ({', '.join(leads) or 'none registered'}) can transition {old_status} → {status}."
        if required_role == "assignee" and agent_name != task["assigned_to"]:
            return f"Only the assigned agent ({task['assigned_to']}) can transition {old_status} → {status}."

        # Handle assignment — allow reassigning on failed→assigned
        update_fields = "status = ?, updated_at = ?"
        params = [status, now]
        if result:
            update_fields += ", result = ?"
            params.append(result)
        if status == "completed":
            update_fields += ", completed_at = ?"
            params.append(now)
        params.append(task_id)
        cursor.execute(f"UPDATE tasks SET {update_fields} WHERE id = ?", params)

        # Auto-notify relevant parties
        notify_targets = []
        msg = f"[{task_id}] Status: {old_status} → {status}"
        if result:
            msg += f"\n\n{result}"

        if required_role == "assignee" and leads:
            # Assignee changed status → notify all leads
            for lead_name in leads:
                cursor.execute(
                    "INSERT INTO messages (from_agent, to_agent, content, timestamp, read_flag, is_cc, task_id) VALUES (?, ?, ?, ?, 0, 0, ?)",
                    (agent_name, lead_name, msg, now, task_id)
                )
                notify_targets.append(lead_name)
        elif required_role == "lead" and task["assigned_to"]:
            # Lead changed status → notify assignee
            cursor.execute(
                "INSERT INTO messages (from_agent, to_agent, content, timestamp, read_flag, is_cc, task_id) VALUES (?, ?, ?, ?, 0, 0, ?)",
                (agent_name, task["assigned_to"], msg, now, task_id)
            )
            notify_targets.append(task["assigned_to"])

        conn.commit()
        await _notify_agents(notify_targets)

        return f"Task {task_id}: {old_status} → {status}"
    except Exception as e:
        return f"Error updating task: {e}"
    finally:
        conn.close()


@mcp.tool()
async def list_tasks(status: str = "", assigned_to: str = "", project: str = "") -> str:
    """List tasks. Filter by status, assigned_to, project. Default: all non-completed tasks. Includes health warning for dead agents."""
    conn = get_db()
    cursor = conn.cursor()
    now_dt = datetime.datetime.now()
    try:
        query = "SELECT * FROM tasks WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        elif not assigned_to and not project:
            query += " AND status NOT IN ('completed')"
        if assigned_to:
            query += " AND assigned_to = ?"
            params.append(assigned_to)
        if project:
            query += " AND project = ?"
            params.append(project)
        query += " ORDER BY created_at ASC"

        cursor.execute(query, params)
        tasks = [dict(row) for row in cursor.fetchall()]

        # Add health warnings for in-progress tasks with dead agents
        for task in tasks:
            if task["status"] == "in_progress" and task["assigned_to"]:
                cursor.execute("SELECT heartbeat_at FROM agents WHERE name = ?", (task["assigned_to"],))
                row = cursor.fetchone()
                if row and row[0]:
                    try:
                        last_hb = datetime.datetime.fromisoformat(row[0])
                        if (now_dt - last_hb).total_seconds() >= 600:
                            task["warning"] = "assigned agent appears dead"
                    except (ValueError, TypeError):
                        pass

        return json.dumps(tasks, indent=2)
    except Exception as e:
        return f"Error listing tasks: {e}"
    finally:
        conn.close()


# ── Phase 2: Handshake ACK ───────────────────────────────────────────

@mcp.tool()
async def initiate_handshake(from_agent: str, message: str, ctx: Context, agents: str = "") -> str:
    """Lead broadcasts a neural handshake plan. All target agents must ACK before GO. Returns handshake ID. Agents param: comma-separated names, or empty for all non-lead agents."""
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    try:
        # Verify lead
        leads = _get_leads(cursor)
        if leads and from_agent not in leads:
            return f"Only a lead ({', '.join(leads)}) can initiate handshakes."

        # Determine target agents
        if agents:
            target_agents = [a.strip() for a in agents.split(",") if a.strip()]
        else:
            cursor.execute("SELECT name FROM agents WHERE name != ?", (from_agent,))
            target_agents = [row[0] for row in cursor.fetchall()]

        if not target_agents:
            return "No agents to handshake with. Register agents first."

        # Broadcast the handshake message
        handshake_prefix = "[HANDSHAKE] "
        full_message = handshake_prefix + message

        # Send to each target agent individually (not broadcast) so we can track delivery
        msg_id = None
        for agent in target_agents:
            cursor.execute(
                "INSERT INTO messages (from_agent, to_agent, content, timestamp, read_flag, is_cc, task_id) VALUES (?, ?, ?, ?, 0, 0, NULL)",
                (from_agent, agent, full_message, now)
            )
            if msg_id is None:
                msg_id = cursor.lastrowid

        # Create handshake record
        cursor.execute(
            "INSERT INTO handshakes (initiated_by, message_id, created_at, status) VALUES (?, ?, ?, 'pending')",
            (from_agent, msg_id, now)
        )
        handshake_id = cursor.lastrowid
        conn.commit()

        # Push notify all targets
        await _notify_agents(target_agents)

        agent_list = ", ".join(target_agents)
        return f"Handshake #{handshake_id} initiated. Waiting for ACK from: {agent_list}. Agents: call ack_handshake(agent_name, handshake_id={handshake_id}) after reading the plan."
    except Exception as e:
        return f"Error initiating handshake: {e}"
    finally:
        conn.close()


@mcp.tool()
async def ack_handshake(agent_name: str, handshake_id: int, ctx: Context) -> str:
    """Acknowledge a neural handshake. Call this after reading the plan to confirm you understand it."""
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    try:
        # Verify handshake exists and is pending
        cursor.execute("SELECT * FROM handshakes WHERE id = ?", (handshake_id,))
        hs = cursor.fetchone()
        if not hs:
            return f"Handshake #{handshake_id} not found."
        hs = dict(hs)
        if hs["status"] == "completed":
            return f"Handshake #{handshake_id} is already completed."

        # Check if already acked
        cursor.execute("SELECT * FROM handshake_acks WHERE handshake_id = ? AND agent_name = ?", (handshake_id, agent_name))
        if cursor.fetchone():
            return f"You already ACKed handshake #{handshake_id}."

        # Record the ACK
        cursor.execute("INSERT INTO handshake_acks (handshake_id, agent_name, acked_at) VALUES (?, ?, ?)", (handshake_id, agent_name, now))

        # Check if all agents have acked
        cursor.execute("SELECT name FROM agents WHERE name != ?", (hs["initiated_by"],))
        all_agents = {row[0] for row in cursor.fetchall()}
        cursor.execute("SELECT agent_name FROM handshake_acks WHERE handshake_id = ?", (handshake_id,))
        acked_agents = {row[0] for row in cursor.fetchall()}
        pending = all_agents - acked_agents

        if not pending:
            cursor.execute("UPDATE handshakes SET status = 'completed' WHERE id = ?", (handshake_id,))
            # Notify the initiator + all leads that agents are synced
            initiator = hs["initiated_by"]
            leads = _get_leads(cursor)
            notify_set = set(leads) | {initiator}
            for target in notify_set:
                cursor.execute(
                    "INSERT INTO messages (from_agent, to_agent, content, timestamp, read_flag, is_cc) VALUES (?, ?, ?, ?, 0, 0)",
                    ("system", target, f"[HANDSHAKE #{handshake_id}] ALL AGENTS SYNCED. Ready for GO signal.", now)
                )
            conn.commit()
            for target in notify_set:
                await _notify_agent(target)
            return f"ACK recorded. Handshake #{handshake_id} COMPLETE — all agents synced!"
        else:
            conn.commit()
            return f"ACK recorded. Still waiting on: {', '.join(pending)}"
    except Exception as e:
        return f"Error acknowledging handshake: {e}"
    finally:
        conn.close()


@mcp.tool()
async def handshake_status(handshake_id: int) -> str:
    """Check status of a neural handshake. Shows who has ACKed and who is still pending."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM handshakes WHERE id = ?", (handshake_id,))
        hs = cursor.fetchone()
        if not hs:
            return f"Handshake #{handshake_id} not found."
        hs = dict(hs)

        cursor.execute("SELECT agent_name, acked_at FROM handshake_acks WHERE handshake_id = ?", (handshake_id,))
        acks = [{"agent": row[0], "acked_at": row[1]} for row in cursor.fetchall()]
        acked_names = {a["agent"] for a in acks}

        cursor.execute("SELECT name FROM agents WHERE name != ?", (hs["initiated_by"],))
        all_agents = {row[0] for row in cursor.fetchall()}
        pending = list(all_agents - acked_names)

        result = {
            "handshake_id": hs["id"],
            "initiated_by": hs["initiated_by"],
            "status": hs["status"],
            "created_at": hs["created_at"],
            "acked": acks,
            "pending": pending,
        }
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error checking handshake status: {e}"
    finally:
        conn.close()


# ── Phase 3: Agent Health ────────────────────────────────────────────

@mcp.tool()
async def ping(agent_name: str, ctx: Context) -> str:
    """Lightweight heartbeat. Call periodically (every 60s recommended) to signal liveness. Updates health status in who()."""
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    try:
        cursor.execute("UPDATE agents SET heartbeat_at = ?, last_seen = ? WHERE name = ?", (now, now, agent_name))
        conn.commit()
        # Re-register session if needed
        if agent_name not in _agent_sessions:
            await _register_session(agent_name, ctx.session)
        return f"pong — {now}"
    except Exception as e:
        return f"Error: {e}"
    finally:
        conn.close()


# ── Phase 4: Review Gates ────────────────────────────────────────────

@mcp.tool()
async def submit_for_review(agent_name: str, task_id: str, summary: str, ctx: Context, files_changed: str = "", test_results: str = "") -> str:
    """Submit a task for lead review. Transitions task to 'review' and sends structured review message to lead."""
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    try:
        cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        task = cursor.fetchone()
        if not task:
            return f"Task {task_id} not found."
        task = dict(task)
        if task["status"] != "in_progress":
            return f"Task {task_id} is '{task['status']}', must be 'in_progress' to submit for review."
        if task["assigned_to"] != agent_name:
            return f"Task {task_id} is assigned to '{task['assigned_to']}', not you."

        # Build result JSON
        review_data = json.dumps({
            "summary": summary,
            "files_changed": files_changed,
            "test_results": test_results,
        })
        cursor.execute("UPDATE tasks SET status = 'review', result = ?, updated_at = ? WHERE id = ?", (review_data, now, task_id))

        # Send structured review message to all leads
        leads = _get_leads(cursor)
        if leads:
            msg = f"[REVIEW] {task_id}: {task['title']}\n\nSUMMARY: {summary}"
            if files_changed:
                msg += f"\nFILES: {files_changed}"
            if test_results:
                msg += f"\nTESTS: {test_results}"
            msg += f"\n\nAwaiting review. Use approve_task or reject_task."
            for lead_name in leads:
                cursor.execute(
                    "INSERT INTO messages (from_agent, to_agent, content, timestamp, read_flag, is_cc, task_id) VALUES (?, ?, ?, ?, 0, 0, ?)",
                    (agent_name, lead_name, msg, now, task_id)
                )
            conn.commit()
            for lead_name in leads:
                await _notify_agent(lead_name)
        else:
            conn.commit()

        return f"Task {task_id} submitted for review."
    except Exception as e:
        return f"Error submitting for review: {e}"
    finally:
        conn.close()


@mcp.tool()
async def approve_task(agent_name: str, task_id: str, ctx: Context, notes: str = "") -> str:
    """Lead approves a task in review. Transitions to 'completed' and notifies the assignee."""
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    try:
        leads = _get_leads(cursor)
        if leads and agent_name not in leads:
            return f"Only a lead ({', '.join(leads)}) can approve tasks."

        cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        task = cursor.fetchone()
        if not task:
            return f"Task {task_id} not found."
        task = dict(task)
        if task["status"] != "review":
            return f"Task {task_id} is '{task['status']}', must be 'review' to approve."

        cursor.execute("UPDATE tasks SET status = 'completed', completed_at = ?, updated_at = ? WHERE id = ?", (now, now, task_id))

        # Notify assignee
        if task["assigned_to"]:
            msg = f"[APPROVED] {task_id}: {task['title']}"
            if notes:
                msg += f"\n\nNotes: {notes}"
            cursor.execute(
                "INSERT INTO messages (from_agent, to_agent, content, timestamp, read_flag, is_cc, task_id) VALUES (?, ?, ?, ?, 0, 0, ?)",
                (agent_name, task["assigned_to"], msg, now, task_id)
            )
            conn.commit()
            await _notify_agent(task["assigned_to"])
        else:
            conn.commit()

        return f"Task {task_id} approved and completed."
    except Exception as e:
        return f"Error approving task: {e}"
    finally:
        conn.close()


@mcp.tool()
async def reject_task(agent_name: str, task_id: str, reason: str, ctx: Context) -> str:
    """Lead rejects a task in review. Sends it back to 'in_progress' for rework with feedback."""
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    try:
        leads = _get_leads(cursor)
        if leads and agent_name not in leads:
            return f"Only a lead ({', '.join(leads)}) can reject tasks."

        cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        task = cursor.fetchone()
        if not task:
            return f"Task {task_id} not found."
        task = dict(task)
        if task["status"] != "review":
            return f"Task {task_id} is '{task['status']}', must be 'review' to reject."

        cursor.execute("UPDATE tasks SET status = 'in_progress', updated_at = ? WHERE id = ?", (now, task_id))

        # Notify assignee with rework feedback
        if task["assigned_to"]:
            msg = f"[REWORK] {task_id}: {task['title']}\n\nREASON: {reason}"
            cursor.execute(
                "INSERT INTO messages (from_agent, to_agent, content, timestamp, read_flag, is_cc, task_id) VALUES (?, ?, ?, ?, 0, 0, ?)",
                (agent_name, task["assigned_to"], msg, now, task_id)
            )
            conn.commit()
            await _notify_agent(task["assigned_to"])
        else:
            conn.commit()

        return f"Task {task_id} rejected — sent back to {task['assigned_to']} for rework."
    except Exception as e:
        return f"Error rejecting task: {e}"
    finally:
        conn.close()


# ── Phase 5: Interface Contracts ─────────────────────────────────────

@mcp.tool()
async def declare_contract(agent_name: str, name: str, type: str, spec: str, ctx: Context, project: str = "") -> str:
    """Declare or update a shared interface contract. Types: function, dom_id, css_class, file_path, api_endpoint, event, other. Auto-broadcasts on version bump."""
    valid_types = ('function', 'dom_id', 'css_class', 'file_path', 'api_endpoint', 'event', 'other')
    if type not in valid_types:
        return f"Invalid type '{type}'. Must be one of: {', '.join(valid_types)}"

    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    try:
        # Check if exists
        cursor.execute("SELECT * FROM contracts WHERE project = ? AND name = ? AND type = ?", (project, name, type))
        existing = cursor.fetchone()

        if existing:
            existing = dict(existing)
            new_version = existing["version"] + 1
            cursor.execute(
                "UPDATE contracts SET spec = ?, owner = ?, version = ?, updated_at = ? WHERE id = ?",
                (spec, agent_name, new_version, now, existing["id"])
            )
            conn.commit()

            # Auto-broadcast version change
            msg = f"[CONTRACT v{new_version}] {type} '{name}' updated by {agent_name}: {spec}"
            # Send to all registered agents except self
            cursor.execute("SELECT name FROM agents WHERE name != ?", (agent_name,))
            targets = [row[0] for row in cursor.fetchall()]
            for target in targets:
                cursor.execute(
                    "INSERT INTO messages (from_agent, to_agent, content, timestamp, read_flag, is_cc) VALUES (?, ?, ?, ?, 0, 0)",
                    (agent_name, target, msg, now)
                )
            conn.commit()
            await _notify_agents(targets)

            return f"Contract updated: {type} '{name}' v{new_version} (owner: {agent_name})"
        else:
            cursor.execute(
                "INSERT INTO contracts (project, name, type, owner, spec, version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (project, name, type, agent_name, spec, now, now)
            )
            conn.commit()
            return f"Contract declared: {type} '{name}' v1 (owner: {agent_name})"
    except Exception as e:
        return f"Error declaring contract: {e}"
    finally:
        conn.close()


@mcp.tool()
async def list_contracts(project: str = "", owner: str = "", type: str = "") -> str:
    """List declared interface contracts. Filter by project, owner, type."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        query = "SELECT * FROM contracts WHERE 1=1"
        params = []
        if project:
            query += " AND project = ?"
            params.append(project)
        if owner:
            query += " AND owner = ?"
            params.append(owner)
        if type:
            query += " AND type = ?"
            params.append(type)
        query += " ORDER BY type, name"

        cursor.execute(query, params)
        contracts = [dict(row) for row in cursor.fetchall()]
        return json.dumps(contracts, indent=2)
    except Exception as e:
        return f"Error listing contracts: {e}"
    finally:
        conn.close()


# ── Phase 6: Minion Spawn Policy ─────────────────────────────────────

@mcp.tool()
async def set_spawn_policy(agent_name: str, scope: str, enabled: bool = True, max_minions: int = 3) -> str:
    """Set minion spawn policy. Only leads can call this. Scope: 'global' or a specific agent name. Controls whether agents can spawn minions and how many."""
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    try:
        # Verify caller is a lead
        leads = _get_leads(cursor)
        if leads and agent_name not in leads:
            return f"Only a lead ({', '.join(leads)}) can set spawn policy."

        cursor.execute("""
            INSERT INTO spawn_policy (scope, enabled, max_minions, set_by, set_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET
                enabled = excluded.enabled,
                max_minions = excluded.max_minions,
                set_by = excluded.set_by,
                set_at = excluded.set_at
        """, (scope, 1 if enabled else 0, max_minions, agent_name, now))
        conn.commit()

        state = "enabled" if enabled else "disabled"
        return f"Spawn policy set: scope='{scope}' {state} max_minions={max_minions} (by {agent_name})"
    except Exception as e:
        return f"Error setting spawn policy: {e}"
    finally:
        conn.close()


@mcp.tool()
async def get_spawn_policy(agent_name: str) -> str:
    """Get effective spawn policy for an agent. Checks agent-specific policy first, falls back to global, then defaults. Returns enabled, max_minions, active_minions, can_spawn."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Check agent-specific policy first
        cursor.execute("SELECT enabled, max_minions FROM spawn_policy WHERE scope = ?", (agent_name,))
        row = cursor.fetchone()

        if not row:
            # Fall back to global policy
            cursor.execute("SELECT enabled, max_minions FROM spawn_policy WHERE scope = 'global'")
            row = cursor.fetchone()

        if row:
            enabled = bool(row[0])
            max_minions = int(row[1])
        else:
            # Default policy
            enabled = True
            max_minions = 3

        # Count active minions for this pilot
        cursor.execute(
            "SELECT COUNT(*) FROM minion_log WHERE pilot = ? AND status = 'spawned'",
            (agent_name,)
        )
        active_minions = cursor.fetchone()[0]

        can_spawn = enabled and active_minions < max_minions

        result = {
            "enabled": enabled,
            "max_minions": max_minions,
            "active_minions": active_minions,
            "can_spawn": can_spawn,
        }
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error getting spawn policy: {e}"
    finally:
        conn.close()


@mcp.tool()
async def log_minion(agent_name: str, task_description: str, status: str, result: str = "") -> str:
    """Log minion lifecycle events. Status: 'spawned', 'completed', 'failed'. For spawned: creates new entry. For completed/failed: updates most recent spawned entry for this pilot."""
    valid_statuses = ("spawned", "completed", "failed")
    if status not in valid_statuses:
        return f"Invalid status '{status}'. Must be one of: {', '.join(valid_statuses)}"

    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    try:
        if status == "spawned":
            cursor.execute(
                "INSERT INTO minion_log (pilot, task_description, status, spawned_at) VALUES (?, ?, 'spawned', ?)",
                (agent_name, task_description, now)
            )
            conn.commit()
            minion_id = cursor.lastrowid
            return f"Minion logged: id={minion_id} pilot={agent_name} status=spawned"
        else:
            # Find most recent spawned entry for this pilot
            cursor.execute(
                "SELECT id FROM minion_log WHERE pilot = ? AND status = 'spawned' ORDER BY id DESC LIMIT 1",
                (agent_name,)
            )
            row = cursor.fetchone()
            if not row:
                return f"No active (spawned) minion found for pilot '{agent_name}'."

            minion_id = row[0]
            cursor.execute(
                "UPDATE minion_log SET status = ?, completed_at = ?, result = ? WHERE id = ?",
                (status, now, result or None, minion_id)
            )
            conn.commit()
            return f"Minion updated: id={minion_id} pilot={agent_name} status={status}"
    except Exception as e:
        return f"Error logging minion: {e}"
    finally:
        conn.close()


# ── Dynamic Tool Descriptions ────────────────────────────────────────
# Override list_tools to inject unread message alerts into check_inbox's
# description. When tools/list_changed fires, the client re-fetches tools
# and sees "*** YOU HAVE 3 UNREAD MESSAGE(S) from juno ***" which prompts
# the AI to call check_inbox automatically.

async def _custom_list_tools():
    """list_tools handler with per-session unread count injection."""
    tools = await mcp.list_tools()

    # Identify calling agent from current session
    try:
        session = mcp._mcp_server.request_context.session
        agent_name = _session_to_agent.get(id(session))
    except (LookupError, AttributeError):
        agent_name = None

    if agent_name:
        count, senders = _get_unread_info(agent_name)
        if count > 0:
            sender_str = ", ".join(senders)
            alert = f"*** YOU HAVE {count} UNREAD MESSAGE(S) from {sender_str} *** Call check_inbox now!"
            for tool in tools:
                if tool.name == "check_inbox":
                    tool.description = f"{alert} | {tool.description}"
                    break

    return tools

# Register the override (replaces FastMCP's default list_tools handler)
mcp._mcp_server.list_tools()(_custom_list_tools)


# ── Advertise tools/list_changed capability ──────────────────────────
# By default FastMCP reports listChanged=false. We need listChanged=true
# so clients know to listen for our push notifications.

from mcp.server.lowlevel.server import NotificationOptions

_original_create_init = mcp._mcp_server.create_initialization_options

def _patched_create_init(notification_options=None, experimental_capabilities=None):
    opts = notification_options or NotificationOptions()
    opts.tools_changed = True
    return _original_create_init(opts, experimental_capabilities or {})

mcp._mcp_server.create_initialization_options = _patched_create_init


# ── Entry Point ──────────────────────────────────────────────────────

def main():
    """Run the Dead Drop MCP server.

    Usage:
        dead-drop-teams                           # stdio transport (backward compatible)
        dead-drop-teams --http                    # Streamable HTTP on default host/port
        dead-drop-teams --http --host 0.0.0.0     # Bind to all interfaces
        dead-drop-teams --http --port 9501        # Custom port
    """
    global HOST, PORT

    # Parse --host and --port from argv
    args = sys.argv[1:]
    if "--host" in args:
        idx = args.index("--host")
        if idx + 1 < len(args):
            HOST = args[idx + 1]
            mcp._mcp_server._options = None  # Reset cached options
    if "--port" in args:
        idx = args.index("--port")
        if idx + 1 < len(args):
            PORT = int(args[idx + 1])

    # Apply host/port to FastMCP
    mcp._host = HOST
    mcp._port = PORT

    transport = "stdio"
    if "--http" in args:
        transport = "streamable-http"
        logger.info(f"Dead Drop server starting on http://{HOST}:{PORT}/mcp")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
