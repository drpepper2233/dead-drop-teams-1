#!/usr/bin/env bash
set -euo pipefail

MODEL="sonnet"
MAX_TOKENS="16000"
PROJECT_DIR="$(pwd)"

usage() {
  cat <<'USAGE'
Usage: spawn-minion.sh <task-description> [options]

Spawn a headless Claude minion to execute a single task.

Arguments:
  task-description    Required. The task for the minion to complete.

Options:
  --model MODEL       Claude model to use (default: sonnet).
  --max-tokens N      Max output tokens (default: 16000).
  --project-dir PATH  Working directory for the minion (default: current dir).
  --help              Show this help text.
USAGE
}

if [[ $# -eq 0 ]]; then
  usage
  exit 2
fi

TASK=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)
      MODEL="$2"
      shift 2
      ;;
    --max-tokens)
      MAX_TOKENS="$2"
      shift 2
      ;;
    --project-dir)
      PROJECT_DIR="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
    *)
      if [[ -z "${TASK}" ]]; then
        TASK="$1"
      else
        echo "Error: Multiple task descriptions provided. Wrap in quotes." >&2
        exit 2
      fi
      shift
      ;;
  esac
done

if [[ -z "${TASK}" ]]; then
  echo "Error: task description is required." >&2
  usage
  exit 2
fi

PROJECT_DIR="$(cd "${PROJECT_DIR}" && pwd)"

SYSTEM_PROMPT="You are a minion. You are a headless worker spawned by a pilot.
Your ONLY job: complete the task below and output the result.
Do NOT use dead-drop tools. Do NOT send messages. Do NOT spawn other minions.
Do NOT ask questions. If unclear, make your best judgment and note assumptions.
Work in the project directory: ${PROJECT_DIR}
Output a clear summary of what you did when finished."

claude -p \
  --model "${MODEL}" \
  --output-format text \
  --max-turns 20 \
  --allowedTools "Edit,Write,Read,Glob,Grep,Bash" \
  --system-prompt "${SYSTEM_PROMPT}" \
  <<< "${TASK}"
