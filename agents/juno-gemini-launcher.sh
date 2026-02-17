#!/usr/bin/env bash
# juno-gemini-launcher.sh — Launch and manage the Gemini dead-drop agent
# Author: juno (dead-drop team lead)
#
# Usage:
#   ./juno-gemini-launcher.sh start    # Start Gemini agent in background
#   ./juno-gemini-launcher.sh stop     # Stop Gemini agent
#   ./juno-gemini-launcher.sh status   # Check if running
#   ./juno-gemini-launcher.sh restart  # Restart
#   ./juno-gemini-launcher.sh logs     # Tail the log file
#   ./juno-gemini-launcher.sh run      # Run in foreground (for debugging)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$SCRIPT_DIR"
DEAD_DROP_DIR="$(dirname "$AGENT_DIR")"
VENV="$DEAD_DROP_DIR/.venv"
PYTHON="$VENV/bin/python3"
AGENT_SCRIPT="$AGENT_DIR/spartan-gemini-agent.py"
PID_FILE="$AGENT_DIR/.gemini-agent.pid"
LOG_FILE="$AGENT_DIR/gemini-agent.log"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

check_prereqs() {
    # Check venv
    if [ ! -f "$PYTHON" ]; then
        echo -e "${RED}ERROR: Python venv not found at $VENV${NC}"
        echo "Run: python3 -m venv $VENV && $VENV/bin/pip install google-genai mcp"
        exit 1
    fi

    # Check agent script
    if [ ! -f "$AGENT_SCRIPT" ]; then
        echo -e "${RED}ERROR: Agent script not found at $AGENT_SCRIPT${NC}"
        echo "Spartan hasn't delivered yet. Wait for dead-drop delivery."
        exit 1
    fi

    # Check API key
    if [ -z "${GOOGLE_API_KEY:-}" ]; then
        # Try loading from .env
        if [ -f "$AGENT_DIR/.env" ]; then
            source "$AGENT_DIR/.env"
        fi
        if [ -z "${GOOGLE_API_KEY:-}" ]; then
            echo -e "${RED}ERROR: GOOGLE_API_KEY not set${NC}"
            echo "Set it: export GOOGLE_API_KEY='your-key-here'"
            echo "Or create $AGENT_DIR/.env with: GOOGLE_API_KEY=your-key-here"
            exit 1
        fi
    fi

    # Check dead-drop server
    if ! curl -s --connect-timeout 2 http://localhost:9400/mcp > /dev/null 2>&1; then
        echo -e "${YELLOW}WARNING: Dead-drop server not responding on port 9400${NC}"
        echo "Start it: cd $DEAD_DROP_DIR && python3 -m dead_drop_server"
    fi
}

get_pid() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return 0
        else
            rm -f "$PID_FILE"
        fi
    fi
    return 1
}

cmd_start() {
    check_prereqs

    if pid=$(get_pid); then
        echo -e "${YELLOW}Gemini agent already running (PID $pid)${NC}"
        return 0
    fi

    echo -e "${CYAN}Starting Gemini dead-drop agent...${NC}"

    # Load .env if exists
    if [ -f "$AGENT_DIR/.env" ]; then
        set -a
        source "$AGENT_DIR/.env"
        set +a
    fi

    nohup "$PYTHON" "$AGENT_SCRIPT" >> "$LOG_FILE" 2>&1 < /dev/null &
    local pid=$!
    echo "$pid" > "$PID_FILE"

    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        echo -e "${GREEN}Gemini agent started (PID $pid)${NC}"
        echo -e "Log: $LOG_FILE"
    else
        echo -e "${RED}Gemini agent failed to start. Check log:${NC}"
        tail -20 "$LOG_FILE"
        rm -f "$PID_FILE"
        exit 1
    fi
}

cmd_stop() {
    if pid=$(get_pid); then
        echo -e "${CYAN}Stopping Gemini agent (PID $pid)...${NC}"
        kill "$pid" 2>/dev/null || true
        sleep 1
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$PID_FILE"
        echo -e "${GREEN}Stopped.${NC}"
    else
        echo -e "${YELLOW}Gemini agent is not running.${NC}"
    fi
}

cmd_status() {
    if pid=$(get_pid); then
        echo -e "${GREEN}Gemini agent is RUNNING (PID $pid)${NC}"
        echo -e "Log: $LOG_FILE"
        echo ""
        echo "Last 5 log lines:"
        tail -5 "$LOG_FILE" 2>/dev/null || echo "(no log yet)"
    else
        echo -e "${RED}Gemini agent is NOT RUNNING${NC}"
    fi
}

cmd_restart() {
    cmd_stop
    sleep 1
    cmd_start
}

cmd_logs() {
    if [ -f "$LOG_FILE" ]; then
        tail -f "$LOG_FILE"
    else
        echo "No log file yet. Start the agent first."
    fi
}

cmd_run() {
    check_prereqs
    echo -e "${CYAN}Running Gemini agent in foreground (Ctrl+C to stop)...${NC}"

    # Load .env if exists
    if [ -f "$AGENT_DIR/.env" ]; then
        set -a
        source "$AGENT_DIR/.env"
        set +a
    fi

    "$PYTHON" "$AGENT_SCRIPT"
}

# Main
case "${1:-help}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    restart) cmd_restart ;;
    logs)    cmd_logs ;;
    run)     cmd_run ;;
    *)
        echo "Gemini Dead-Drop Agent Launcher"
        echo ""
        echo "Usage: $0 {start|stop|status|restart|logs|run}"
        echo ""
        echo "  start   - Start agent in background"
        echo "  stop    - Stop agent"
        echo "  status  - Check if running"
        echo "  restart - Restart agent"
        echo "  logs    - Tail log file"
        echo "  run     - Run in foreground (debug)"
        ;;
esac
