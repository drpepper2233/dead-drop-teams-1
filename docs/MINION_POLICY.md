# Minion Spawn Policy

Minions are headless CLI subprocesses spawned by a pilot (interactive Claude Code session) to execute parallelizable grunt work. This document defines the rules governing their use.

## Lead Authority

- The lead sets minion policy per room/project: **enabled** or **disabled**
- When disabled, agents CANNOT spawn minions. Period.
- The lead can set per-agent overrides (e.g. spartan allowed, cortana denied)
- Policy is changed via the `set_spawn_policy` MCP tool (lead-only)
- **Default:** enabled with max 3 minions per pilot

## What Are Minions

- Headless CLI subprocesses spawned by a pilot
- Run `claude -p` (pipe mode) with a single task prompt
- No persistent context, no dead-drop access, no MCP tools
- Report results to parent pilot via stdout only
- Single task lifecycle: spawn, execute, report, die

Minions are disposable workers. They have no memory, no messaging capability, and no awareness of the broader team. The pilot is their only interface to the outside world.

## Spawn Rules

### SPAWN when:

- Task is repetitive/batch (write tests for 10 files, lint everything)
- Task is well-defined with clear inputs/outputs
- Task is independent (doesn't touch shared interfaces or other agents' files)
- Pilot is blocked and has parallelizable grunt work

### DO NOT spawn when:

- Task requires architectural decisions or creativity
- Task touches shared contracts or another agent's files
- Task involves inter-agent communication
- Already at minion cap

## Hard Limits

| Limit | Default | Notes |
|-------|---------|-------|
| Max minions per pilot | 3 | Configurable by lead via `set_spawn_policy` |
| Recursion | Forbidden | Minions cannot spawn minions |
| Dead-drop access | None | Minions cannot use dead-drop messaging |
| Lifetime | Single task | Spawn, execute, report, die |

- Pilot MUST review ALL minion output before committing
- Minions run in pipe mode only (`claude -p`) — no interactive sessions

## Accountability

- **Pilot owns minion output.** Minion breaks something = pilot's fault.
- Pilot reports minion usage to lead
- Lead can audit via the list of `log_minion` entries
- Pilots MUST call `get_spawn_policy` before spawning to check current limits
- Pilots MUST call `log_minion` on spawn and on completion

## MCP Tools

### `set_spawn_policy` (lead only)

Set or update minion spawn policy for the room/project.

| Parameter | Type | Description |
|-----------|------|-------------|
| `enabled` | bool | Whether minion spawning is allowed |
| `max_per_agent` | int | Maximum concurrent minions per pilot |
| `agent_overrides` | dict | Per-agent enable/disable overrides |

### `get_spawn_policy` (any agent)

Query the current minion spawn policy. Returns enabled status, limits, and any agent-specific overrides. Pilots MUST call this before spawning.

### `log_minion` (any agent)

Record a minion spawn or completion event for audit purposes.

| Parameter | Type | Description |
|-----------|------|-------------|
| `agent_name` | str | The pilot who spawned the minion |
| `event` | str | `spawn` or `complete` |
| `task_summary` | str | Brief description of what the minion is doing/did |
| `minion_id` | str | Unique identifier for the minion process |
