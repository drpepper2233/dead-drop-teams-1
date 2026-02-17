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

mcp = FastMCP(
    "Dead Drop Server",
    host="127.0.0.1",
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


def _get_lead(cursor):
    """Find the agent with role 'lead'. Returns name or None."""
    cursor.execute("SELECT name FROM agents WHERE role = 'lead' LIMIT 1")
    row = cursor.fetchone()
    return row[0] if row else None


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
    # Migrations
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

    cursor.execute("PRAGMA table_info(messages)")
    mcols = [c[1] for c in cursor.fetchall()]
    if 'is_cc' not in mcols:
        cursor.execute("ALTER TABLE messages ADD COLUMN is_cc INTEGER DEFAULT 0")
    if 'cc_original_to' not in mcols:
        cursor.execute("ALTER TABLE messages ADD COLUMN cc_original_to TEXT DEFAULT NULL")

    conn.commit()
    conn.close()


init_db()


# ── Tools ────────────────────────────────────────────────────────────

@mcp.tool()
async def register(agent_name: str, ctx: Context, role: str = "", description: str = "") -> str:
    """Registers the caller into the system. Role: 'lead', 'researcher', 'coder', 'builder'. Description: what this agent does."""
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    try:
        cursor.execute("""
            INSERT INTO agents (name, registered_at, last_seen, role, description, status)
            VALUES (?, ?, ?, ?, ?, 'waiting for work')
            ON CONFLICT(name) DO UPDATE SET
                last_seen = ?,
                role = COALESCE(NULLIF(?, ''), agents.role),
                description = COALESCE(NULLIF(?, ''), agents.description),
                status = 'waiting for work'
        """, (agent_name, now, now, role or None, description or None, now, role, description))
        conn.commit()

        # Register session for push notifications
        await _register_session(agent_name, ctx.session)

        role_note = f" role={role}" if role else ""
        result = f"Agent '{agent_name}' registered successfully.{role_note}"
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
async def send(from_agent: str, to_agent: str, message: str, ctx: Context, cc: str = "") -> str:
    """Sends a message to a specific agent name, or 'all' for broadcast. Optional cc param for carbon-copying another agent."""
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    try:
        # Check for unread messages before allowing send
        cursor.execute("SELECT COUNT(*) FROM messages WHERE to_agent = ? AND read_flag = 0", (from_agent,))
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

        # Insert primary message
        cursor.execute("INSERT INTO messages (from_agent, to_agent, content, timestamp, read_flag, is_cc) VALUES (?, ?, ?, ?, 0, 0)", (from_agent, to_agent, message, now))

        # Build CC list: explicit + auto-CC lead
        cc_agents = [a.strip() for a in cc.split(",") if a.strip()] if cc else []
        lead_name = _get_lead(cursor)
        if lead_name and from_agent != lead_name and to_agent != lead_name and lead_name not in cc_agents:
            cc_agents.append(lead_name)

        for cc_agent in cc_agents:
            if cc_agent != to_agent:
                cursor.execute("INSERT INTO messages (from_agent, to_agent, content, timestamp, read_flag, is_cc, cc_original_to) VALUES (?, ?, ?, ?, 0, 1, ?)", (from_agent, cc_agent, message, now, to_agent))

        conn.commit()

        # ── Push notifications to recipients ──
        notify_targets = []
        if to_agent == 'all':
            notify_targets = [a for a in _agent_sessions if a != from_agent]
        else:
            notify_targets.append(to_agent)
        for cc_agent in cc_agents:
            if cc_agent not in notify_targets and cc_agent != from_agent:
                notify_targets.append(cc_agent)

        await _notify_agents(notify_targets)

        cc_note = f" (cc: {cc})" if cc else ""
        return f"Message sent from '{from_agent}' to '{to_agent}'{cc_note}."
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

        cursor.execute("SELECT * FROM messages WHERE to_agent = ? AND read_flag = 0", (agent_name,))
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
async def get_history(count: int = 10) -> str:
    """Returns the last N messages across all agents (for catch-up)."""
    conn = get_db()
    cursor = conn.cursor()
    try:
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
    """Lists all registered agents and when they last checked in."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM agents ORDER BY last_seen DESC")
        agents = [dict(row) for row in cursor.fetchall()]
        for agent in agents:
            agent['connected'] = agent['name'] in _agent_sessions
        return json.dumps(agents, indent=2)
    except Exception as e:
        return f"Error listing agents: {e}"
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
        dead-drop-teams          # stdio transport (backward compatible)
        dead-drop-teams --http   # Streamable HTTP on port 9400 (push notifications)
    """
    transport = "stdio"
    if "--http" in sys.argv:
        transport = "streamable-http"
        logger.info(f"Dead Drop server starting on http://127.0.0.1:{PORT}/mcp")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
