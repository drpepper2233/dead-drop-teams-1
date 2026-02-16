# dead-drop-teams — Agent Instructions

Multi-agent coordination MCP server. SQLite message passing with role-based agents and auto-CC to lead.

Any AI coding tool that supports MCP can participate: Claude Code, Codex CLI, OpenCode, Gemini, or anything that speaks the protocol.

## Quick Reference

- **Server:** `src/dead_drop/server.py`
- **Architecture:** `docs/ARCHITECTURE.md` — schema, CC protocol, agent lifecycle, inbox discipline
- **Protocol:** `docs/PROTOCOL.md` — agent-facing rules (register, message format, queuing)
- **Database:** `~/.dead-drop/messages.db` (override with `DEAD_DROP_DB_PATH`)

## Tools

| Tool | Purpose |
|------|---------|
| `register` | Sign in with name, role, description |
| `send` | Message an agent (auto-CCs lead) |
| `check_inbox` | Read unread messages |
| `who` | List agents + last seen |
| `get_history` | Last N messages (post-compaction catch-up) |

## Roles

Roles describe function, not which AI model runs them. Any model can fill any role.

| Role | Function | Lifecycle |
|------|----------|-----------|
| `lead` | Coordinates, reviews, routes tasks | Persistent |
| `researcher` | Reads source, finds bugs, writes analysis | Persistent |
| `coder` | Writes code from specific instructions | Ephemeral |
| `builder` | Builds, runs tests, executes commands | Ephemeral |

## Naming Convention

`<agent-name>` — pick something descriptive. Examples:
- `claude-lead`, `codex-coder`, `gemini-researcher`, `opencode-builder`

## Rules

- Server code only — no project-specific knowledge in this repo
- Migrations in `init_db()` must be additive (ALTER TABLE ADD COLUMN, never DROP)
- Test with `python -m dead_drop.server` before committing

## Dev

```bash
cd ~/projects/dead-drop-teams
uv venv && source .venv/bin/activate && uv pip install -e .
```
