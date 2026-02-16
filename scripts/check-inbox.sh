#!/bin/bash
# Dead drop inbox check hook for Claude Code
# Fires on UserPromptSubmit — injects unread count into Claude's context
# Debounced: nags at most once per 30 seconds

AGENT_NAME="claude-lead"
DB_PATH="$HOME/.dead-drop/messages.db"
NAG_FILE="/tmp/dead-drop-last-nag"
DEBOUNCE_SEC=30

# Bail if DB doesn't exist
[ -f "$DB_PATH" ] || exit 0

UNREAD=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM messages WHERE to_agent = '$AGENT_NAME' AND read_flag = 0;")

if [ "$UNREAD" -gt 0 ]; then
    NOW=$(date +%s)
    LAST_NAG=0
    [ -f "$NAG_FILE" ] && LAST_NAG=$(cat "$NAG_FILE")
    ELAPSED=$((NOW - LAST_NAG))

    if [ "$ELAPSED" -ge "$DEBOUNCE_SEC" ]; then
        echo "Dead drop: $UNREAD unread message(s) for $AGENT_NAME. Call check_inbox to read them."
        echo "$NOW" > "$NAG_FILE"
    fi
fi
