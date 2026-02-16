#!/bin/bash
# Install or refresh dead-drop-teams MCP server
# Run from anywhere: ~/projects/dead-drop-teams/scripts/install.sh

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_DIR="$HOME/.dead-drop"

echo "=== dead-drop-teams install ==="
echo "Project: $PROJECT_DIR"
echo "Runtime: $RUNTIME_DIR"

# 1. Ensure runtime directory exists
mkdir -p "$RUNTIME_DIR"

# 2. Create venv if missing
if [ ! -d "$PROJECT_DIR/.venv" ]; then
    echo "Creating venv..."
    cd "$PROJECT_DIR"
    if command -v uv &>/dev/null; then
        uv venv
    else
        python3 -m venv .venv
    fi
fi

# 3. Install/reinstall package
echo "Installing package..."
cd "$PROJECT_DIR"
source .venv/bin/activate
if command -v uv &>/dev/null; then
    uv pip install -e .
else
    pip install -e .
fi

# 4. Kill old server processes
OLD_PIDS=$(pgrep -f "dead_drop.server\|dead-drop/server/main.py" 2>/dev/null || true)
if [ -n "$OLD_PIDS" ]; then
    echo "Stopping old server processes: $OLD_PIDS"
    echo "$OLD_PIDS" | xargs kill 2>/dev/null || true
    sleep 1
fi

# 5. Deploy server to runtime
mkdir -p "$RUNTIME_DIR/server"
cp "$PROJECT_DIR/src/dead_drop/server.py" "$RUNTIME_DIR/server/main.py"
echo "Deployed: server.py → $RUNTIME_DIR/server/main.py"

# 6. Ensure runtime venv has dependencies
if [ ! -d "$RUNTIME_DIR/server/.venv" ]; then
    echo "Creating runtime venv..."
    cd "$RUNTIME_DIR/server"
    if command -v uv &>/dev/null; then
        uv venv
        uv pip install mcp
    else
        python3 -m venv .venv
        .venv/bin/pip install mcp
    fi
fi

# 7. Copy scripts to runtime
cp "$PROJECT_DIR/scripts/poll_inbox.sh" "$RUNTIME_DIR/poll_inbox.sh"
chmod +x "$RUNTIME_DIR/poll_inbox.sh"

mkdir -p "$RUNTIME_DIR/hooks"
cp "$PROJECT_DIR/scripts/check-inbox.sh" "$RUNTIME_DIR/hooks/check-inbox.sh"
chmod +x "$RUNTIME_DIR/hooks/check-inbox.sh"

# 8. Copy docs to runtime (protocol + role profiles)
cp "$PROJECT_DIR/docs/PROTOCOL.md" "$RUNTIME_DIR/PROTOCOL.md"
echo "Deployed: PROTOCOL.md → $RUNTIME_DIR/PROTOCOL.md"

mkdir -p "$RUNTIME_DIR/roles"
cp "$PROJECT_DIR/docs/roles/"*.md "$RUNTIME_DIR/roles/"
echo "Deployed: docs/roles/ → $RUNTIME_DIR/roles/"

# 9. Verify
echo ""
echo "=== Verify ==="
echo "Binary: $(which dead-drop-teams 2>/dev/null || echo 'not in PATH — use .venv/bin/dead-drop-teams')"
echo "Database: ${DEAD_DROP_DB_PATH:-$RUNTIME_DIR/messages.db}"
echo "Poll script: $RUNTIME_DIR/poll_inbox.sh"
echo "Hook: $RUNTIME_DIR/hooks/check-inbox.sh"
echo ""
echo "Done. Restart your AI tool to pick up the new MCP server."
