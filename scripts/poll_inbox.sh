#!/bin/bash
# Poll dead drop inbox for unread messages
# Usage: ./poll_inbox.sh <agent_name> [interval_seconds]
# Shared script — do not hardcode agent names

AGENT_NAME="${1:-claude-lead}"
INTERVAL="${2:-120}"
DB_PATH="$HOME/.dead-drop/messages.db"

echo "Polling inbox for $AGENT_NAME every ${INTERVAL}s..."

while true; do
    DIRECT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM messages WHERE to_agent = '$AGENT_NAME' AND read_flag = 0;")
    BROADCAST=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM messages WHERE to_agent = 'all' AND from_agent != '$AGENT_NAME' AND id NOT IN (SELECT message_id FROM broadcast_reads WHERE agent_name = '$AGENT_NAME');")
    TOTAL=$((DIRECT + BROADCAST))

    if [ "$TOTAL" -gt 0 ]; then
        echo "[$(date '+%H:%M:%S')] $TOTAL unread message(s) for $AGENT_NAME"
        sqlite3 "$DB_PATH" "SELECT from_agent || ': ' || content FROM messages WHERE to_agent = '$AGENT_NAME' AND read_flag = 0;"
    fi

    sleep "$INTERVAL"
done
