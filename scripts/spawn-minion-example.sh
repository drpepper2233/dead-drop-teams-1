#!/usr/bin/env bash
set -euo pipefail

# Example: Pilot spawns 3 minions in parallel

SPAWN=~/dead-drop-teams/scripts/spawn-minion.sh

"${SPAWN}" "Write unit tests for src/auth.py" &
"${SPAWN}" "Write unit tests for src/api.py" &
"${SPAWN}" "Lint and fix all files in src/" &

wait
echo "All minions done"
