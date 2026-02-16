from mcp.server.fastmcp import FastMCP
import sqlite3
import datetime
import os
import json

# Setup
DB_PATH = os.getenv("DEAD_DROP_DB_PATH", os.path.expanduser("~/.dead-drop/messages.db"))
RUNTIME_DIR = os.path.dirname(DB_PATH)
mcp = FastMCP("Dead Drop Server")


def _load_onboarding(role: str) -> str:
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

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _get_lead(cursor):
    """Find the agent with role 'lead'. Returns name or None."""
    cursor.execute("SELECT name FROM agents WHERE role = 'lead' LIMIT 1")
    row = cursor.fetchone()
    return row[0] if row else None

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
    # New table for tracking broadcast reads
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS broadcast_reads (
            agent_name TEXT,
            message_id INTEGER,
            PRIMARY KEY (agent_name, message_id)
        )
    ''')
    # Migration: add last_inbox_check if missing
    cursor.execute("PRAGMA table_info(agents)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'last_inbox_check' not in columns:
        cursor.execute("ALTER TABLE agents ADD COLUMN last_inbox_check TEXT")

    # Migration: add CC columns if missing
    cursor.execute("PRAGMA table_info(messages)")
    msg_columns = [col[1] for col in cursor.fetchall()]
    if 'is_cc' not in msg_columns:
        cursor.execute("ALTER TABLE messages ADD COLUMN is_cc INTEGER DEFAULT 0")
    if 'cc_original_to' not in msg_columns:
        cursor.execute("ALTER TABLE messages ADD COLUMN cc_original_to TEXT DEFAULT NULL")

    # Migration: add role/description to agents if missing
    cursor.execute("PRAGMA table_info(agents)")
    agent_columns = [col[1] for col in cursor.fetchall()]
    if 'role' not in agent_columns:
        cursor.execute("ALTER TABLE agents ADD COLUMN role TEXT DEFAULT NULL")
    if 'description' not in agent_columns:
        cursor.execute("ALTER TABLE agents ADD COLUMN description TEXT DEFAULT NULL")
    if 'status' not in agent_columns:
        cursor.execute("ALTER TABLE agents ADD COLUMN status TEXT DEFAULT 'offline'")

    conn.commit()
    conn.close()

# Initialize DB on start
init_db()

@mcp.tool()
def register(agent_name: str, role: str = "", description: str = "") -> str:
    """Registers the caller (e.g. gemini-benchmarker) into the system. Role: 'lead', 'researcher', 'coder', 'builder'. Description: what this agent does."""
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
def set_status(agent_name: str, status: str) -> str:
    """Set your current status (e.g. 'working on BUG-014', 'waiting for work', 'reviewing softmax changes'). Shows up in who() output."""
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
def send(from_agent: str, to_agent: str, message: str, cc: str = "") -> str:
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

        # Insert primary message
        cursor.execute("INSERT INTO messages (from_agent, to_agent, content, timestamp, read_flag, is_cc) VALUES (?, ?, ?, ?, 0, 0)", (from_agent, to_agent, message, now))

        # Build CC list: explicit + auto-CC lead on all non-lead messages
        cc_agents = [a.strip() for a in cc.split(",") if a.strip()] if cc else []

        # Auto-CC the lead on every message not from/to the lead
        lead_name = _get_lead(cursor)
        if lead_name and from_agent != lead_name and to_agent != lead_name and lead_name not in cc_agents:
            cc_agents.append(lead_name)

        # Insert CC copies
        for cc_agent in cc_agents:
            if cc_agent != to_agent:  # don't double-deliver
                cursor.execute("INSERT INTO messages (from_agent, to_agent, content, timestamp, read_flag, is_cc, cc_original_to) VALUES (?, ?, ?, ?, 0, 1, ?)", (from_agent, cc_agent, message, now, to_agent))

        conn.commit()
        cc_note = f" (cc: {cc})" if cc else ""
        return f"Message sent from '{from_agent}' to '{to_agent}'{cc_note}."
    except Exception as e:
        return f"Error sending message: {e}"
    finally:
        conn.close()

@mcp.tool()
def check_inbox(agent_name: str) -> str:
    """Returns unread messages for the agent, marks specific ones as read."""
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    try:
        # Update last_seen and last_inbox_check
        cursor.execute("UPDATE agents SET last_seen = ?, last_inbox_check = ? WHERE name = ?", (now, now, agent_name))

        # Get unread specific messages
        cursor.execute("SELECT * FROM messages WHERE to_agent = ? AND read_flag = 0", (agent_name,))
        specific_msgs = [dict(row) for row in cursor.fetchall()]

        # Mark specific messages as read
        if specific_msgs:
            ids = [m['id'] for m in specific_msgs]
            cursor.execute(f"UPDATE messages SET read_flag = 1 WHERE id IN ({','.join(['?']*len(ids))})", ids)

        # Get broadcast messages NOT already read by this agent
        cursor.execute("""
            SELECT * FROM messages
            WHERE to_agent = 'all'
            AND id NOT IN (
                SELECT message_id FROM broadcast_reads WHERE agent_name = ?
            )
        """, (agent_name,))
        broadcast_msgs = [dict(row) for row in cursor.fetchall()]

        # Mark these broadcasts as read for this agent
        if broadcast_msgs:
            for msg in broadcast_msgs:
                cursor.execute("INSERT INTO broadcast_reads (agent_name, message_id) VALUES (?, ?)", (agent_name, msg['id']))

        conn.commit()

        all_messages = specific_msgs + broadcast_msgs
        # Sort by timestamp
        all_messages.sort(key=lambda x: x['timestamp'])

        # Tag CC messages for display
        for msg in all_messages:
            if msg.get('is_cc'):
                msg['cc_note'] = f"[CC] originally to: {msg.get('cc_original_to', 'unknown')}"

        return json.dumps(all_messages, indent=2)
    except Exception as e:
        return f"Error checking inbox: {e}"
    finally:
        conn.close()

@mcp.tool()
def get_history(count: int = 10) -> str:
    """Returns the last N messages across all agents (for catch-up)."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM messages ORDER BY timestamp DESC LIMIT ?", (count,))
        msgs = [dict(row) for row in cursor.fetchall()]
        # Return oldest to newest for readability
        return json.dumps(msgs[::-1], indent=2)
    except Exception as e:
        return f"Error fetching history: {e}"
    finally:
        conn.close()

@mcp.tool()
def deregister(agent_name: str) -> str:
    """Removes an agent from the registry. Use to clean up stale/ghost entries from previous sessions."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT name FROM agents WHERE name = ?", (agent_name,))
        if not cursor.fetchone():
            return f"Agent '{agent_name}' not found."
        cursor.execute("DELETE FROM agents WHERE name = ?", (agent_name,))
        conn.commit()
        return f"Agent '{agent_name}' deregistered."
    except Exception as e:
        return f"Error deregistering agent: {e}"
    finally:
        conn.close()

@mcp.tool()
def who() -> str:
    """Lists all registered agents and when they last checked in."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM agents ORDER BY last_seen DESC")
        agents = [dict(row) for row in cursor.fetchall()]
        return json.dumps(agents, indent=2)
    except Exception as e:
        return f"Error listing agents: {e}"
    finally:
        conn.close()

def main():
    """Entry point for the MCP server."""
    mcp.run()

if __name__ == "__main__":
    main()
