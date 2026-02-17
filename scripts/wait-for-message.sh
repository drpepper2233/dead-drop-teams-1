#!/bin/bash
# Dead Drop Message Watcher
# Run this as a background task in Claude Code.
# It blocks until a new unread message arrives for the given agent,
# then prints the alert and exits — waking up the model.
#
# Usage: wait-for-message.sh <agent_name>

AGENT_NAME="${1:?Usage: wait-for-message.sh <agent_name>}"
DB_PATH="$HOME/.dead-drop/messages.db"

get_unread() {
    sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM messages WHERE to_agent = '$AGENT_NAME' AND read_flag = 0;" 2>/dev/null || echo "0"
}

# Check if already have unread
UNREAD=$(get_unread)
if [ "$UNREAD" -gt 0 ] 2>/dev/null; then
    echo "*** YOU HAVE $UNREAD UNREAD MESSAGE(S) — call check_inbox(agent_name=\"$AGENT_NAME\") NOW ***"
    exit 0
fi

# Watch the database file for changes using fswatch (event-driven, no polling)
while true; do
    fswatch -1 --event Updated "$DB_PATH" > /dev/null 2>&1
    UNREAD=$(get_unread)
    if [ "$UNREAD" -gt 0 ] 2>/dev/null; then
        SENDERS=$(sqlite3 "$DB_PATH" "SELECT DISTINCT from_agent FROM messages WHERE to_agent = '$AGENT_NAME' AND read_flag = 0;" 2>/dev/null | tr '\n' ', ' | sed 's/,$//')
        echo "*** YOU HAVE $UNREAD UNREAD MESSAGE(S) from $SENDERS — call check_inbox(agent_name=\"$AGENT_NAME\") NOW ***"
        exit 0
    fi
done
