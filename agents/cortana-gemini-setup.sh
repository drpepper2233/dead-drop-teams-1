#!/bin/bash
# cortana-gemini-setup.sh — Setup Gemini agent for dead-drop collaboration
# Installs dependencies, verifies API key, tests connectivity

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$PROJECT_DIR/.venv"
DEAD_DROP_PORT=9400

echo "============================================"
echo "  Gemini Dead Drop Agent — Setup"
echo "============================================"
echo ""

# --- Step 1: Check Python ---
echo "[1/6] Checking Python..."
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install Python 3.12+."
    exit 1
fi
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "  Found Python $PYVER"

# --- Step 2: Setup venv ---
echo "[2/6] Setting up virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "  Created venv at $VENV_DIR"
else
    echo "  Venv already exists at $VENV_DIR"
fi
source "$VENV_DIR/bin/activate"

# --- Step 3: Install dependencies ---
echo "[3/6] Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet google-genai mcp
echo "  Installed: google-genai, mcp"

# --- Step 4: Check API key ---
echo "[4/6] Checking Google API key..."
if [ -z "$GOOGLE_API_KEY" ]; then
    echo ""
    echo "  GOOGLE_API_KEY is not set."
    echo "  Get one at: https://aistudio.google.com → Get API key"
    echo ""
    read -rp "  Enter your Google API key: " api_key
    if [ -z "$api_key" ]; then
        echo "  ERROR: No API key provided."
        exit 1
    fi
    export GOOGLE_API_KEY="$api_key"
    echo ""
    echo "  To make this permanent, add to your shell profile:"
    echo "    export GOOGLE_API_KEY='$api_key'"
    echo ""
else
    echo "  GOOGLE_API_KEY is set (${GOOGLE_API_KEY:0:8}...)"
fi

# --- Step 5: Verify dead-drop server ---
echo "[5/6] Checking dead-drop server on port $DEAD_DROP_PORT..."
if curl -s -o /dev/null -w "%{http_code}" "http://localhost:$DEAD_DROP_PORT" 2>/dev/null | grep -q "200\|404\|405"; then
    echo "  Dead-drop server is responding on port $DEAD_DROP_PORT"
elif lsof -i ":$DEAD_DROP_PORT" &>/dev/null; then
    echo "  Port $DEAD_DROP_PORT is in use (server likely running via stdio)"
else
    echo "  WARNING: Dead-drop server not detected on port $DEAD_DROP_PORT"
    echo "  The dead-drop MCP server runs via stdio (not HTTP)."
    echo "  Make sure it's configured in your Gemini CLI settings."
fi

# --- Step 6: Test Gemini API ---
echo "[6/6] Testing Gemini API connection..."
TEST_RESULT=$(python3 -c "
from google import genai
client = genai.Client()
response = client.models.generate_content(
    model='gemini-2.5-flash',
    contents='Reply with exactly: DRIFT_ONLINE'
)
print(response.text.strip())
" 2>&1) || true

if echo "$TEST_RESULT" | grep -q "DRIFT_ONLINE"; then
    echo "  Gemini API is working!"
else
    echo "  WARNING: Gemini API test returned unexpected result:"
    echo "  $TEST_RESULT"
    echo ""
    echo "  Common issues:"
    echo "  - Invalid API key → regenerate at aistudio.google.com"
    echo "  - Rate limited → wait 60 seconds and retry"
    echo "  - Network error → check internet connection"
fi

# --- Done ---
echo ""
echo "============================================"
echo "  Setup Complete!"
echo "============================================"
echo ""
echo "To run the Gemini agent:"
echo "  1. Install Gemini CLI:  npm install -g @google/gemini-cli"
echo "     Or: brew install gemini-cli"
echo ""
echo "  2. Configure dead-drop MCP in ~/.gemini/settings.json:"
echo '     {'
echo '       "mcpServers": {'
echo '         "dead-drop": {'
echo '           "command": "'$VENV_DIR'/bin/python",'
echo '           "args": ["-m", "dead_drop.server"],'
echo '           "cwd": "'$PROJECT_DIR'/src"'
echo '         }'
echo '       }'
echo '     }'
echo ""
echo "  3. Create ~/.gemini/GEMINI.md with agent instructions"
echo ""
echo "  4. Start Gemini CLI: gemini"
echo "     Then register: register(agent_name='gemini', role='researcher')"
echo ""
echo "  See cortana-gemini-config.md for full documentation."
echo ""
