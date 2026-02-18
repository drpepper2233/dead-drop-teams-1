# Goal / Verification System

> Nothing is done until someone else confirms it.

## Overview

Goals are containers for tasks. A goal represents a deliverable outcome (e.g., "Build House Wire v2 app"). Tasks are the individual work items inside a goal. The verification chain ensures no work is considered complete until independently verified by a different agent.

Flow: **GOAL > TASKS > VERIFY EACH TASK > ALL VERIFIED > VERIFY GOAL**

---

## State Machines

### Task Lifecycle

```
                          reject
                    ┌──────────────────┐
                    v                  │
  pending ──> assigned ──> in_progress ──> review ──> completed ──> verified
                              ^                          │
                              │          reject          │
                              └──────────────────────────┘
```

| State       | Meaning                                      |
|-------------|----------------------------------------------|
| pending     | Created, not yet assigned                    |
| assigned    | Assigned to an agent, not started             |
| in_progress | Agent is actively working                    |
| review      | Builder submitted for review (submit_for_review) |
| completed   | Lead/reviewer approved the work              |
| verified    | Independent verifier confirmed correctness   |

Transitions:
- `pending → assigned` — lead assigns task
- `assigned → in_progress` — assignee starts work
- `in_progress → review` — builder submits for review
- `review → completed` — reviewer approves
- `review → in_progress` — reviewer rejects (with reason)
- `completed → verified` — verifier confirms
- `completed → in_progress` — verifier rejects (with reason)

### Goal Lifecycle

```
                       reject
                  ┌────────────────┐
                  v                │
  open ──> active ──> pending_verify ──> verified
```

| State          | Meaning                                   |
|----------------|-------------------------------------------|
| open           | Goal created, no tasks started yet         |
| active         | At least one task is in progress           |
| pending_verify | All tasks verified, awaiting goal-level check |
| verified       | Lead confirmed the integrated result works |

Transitions:
- `open → active` — first task moves to in_progress
- `active → pending_verify` — all tasks reach verified status
- `pending_verify → verified` — lead confirms integrated result
- `pending_verify → active` — lead rejects (creates new fix tasks)

---

## Verification Rules

### Task Verification

1. **Verifier != Builder**: The agent who built it cannot verify it
2. **Verifier != Approver**: The agent who approved in review cannot also verify
3. **Triple separation**: Builder != Reviewer != Verifier (when team size allows)
4. **Hat conflicts apply**: Verifier implicitly wears Tester hat, so all Tester conflict rules are enforced
5. **Two-rejection escalation**: If the same task is rejected by a verifier twice, it escalates to lead for triage

### Goal Verification

1. **Lead only**: Only the lead can verify a goal
2. **Integration test required**: Lead must test the integrated product, not just check that sub-tasks are verified
3. **Cannot be delegated**: Goal verification is a lead responsibility that cannot be assigned to another agent

---

## MCP Tools

| Tool                 | Args                                         | Description                                              |
|----------------------|----------------------------------------------|----------------------------------------------------------|
| `create_goal`        | `creator, title, description, project`       | Create a new goal container. Returns goal_id.            |
| `link_task_to_goal`  | `task_id, goal_id`                           | Associate an existing task with a goal.                  |
| `goal_status`        | `goal_id`                                    | Show goal state + all linked tasks with their statuses.  |
| `verify_task`        | `agent_name, task_id, notes`                 | Verifier confirms a completed task. Moves to verified.   |
| `reject_verification`| `agent_name, task_id, reason`                | Verifier rejects. Task moves back to in_progress.        |
| `verify_goal`        | `agent_name, goal_id, notes`                 | Lead confirms the integrated goal. Moves to verified.    |

Notes:
- `verify_task` enforces verifier != builder and verifier != approver
- `reject_verification` requires a non-empty reason
- `verify_goal` enforces agent must be lead role
- `goal_status` returns task breakdown: pending / in_progress / review / completed / verified counts

---

## Workflow Examples

### Simple 3-Agent Workflow

Team: Agent A (builder), Agent B (tester/checker), Agent C (lead)

```
1. Lead creates goal: "Build login page"
   └── create_goal(creator="C", title="Build login page")

2. Lead creates and assigns task:
   └── create_task(creator="C", title="Create login form component", assign_to="A")
   └── link_task_to_goal(task_id="TASK-1", goal_id="GOAL-1")

3. Builder A works on TASK-1:
   └── update_task(agent_name="A", task_id="TASK-1", status="in_progress")
   └── ... writes code ...
   └── submit_for_review(agent_name="A", task_id="TASK-1", summary="Built form with validation", files_changed="login.js, login.css")

4. Lead C reviews and approves:
   └── approve_task(agent_name="C", task_id="TASK-1")
   └── Task moves to "completed"

5. Checker B verifies:
   └── Reads files_changed, runs the code, tests behavior
   └── verify_task(agent_name="B", task_id="TASK-1", notes="Form renders correctly, validation works")
   └── Task moves to "verified"

6. All tasks verified → goal moves to pending_verify

7. Lead C does integration test:
   └── Opens app in browser, tests full login flow end-to-end
   └── verify_goal(agent_name="C", goal_id="GOAL-1", notes="Login flow works end-to-end")
   └── GOAL VERIFIED
```

### Rejection Flow

```
1. Builder A submits TASK-1 for review → approved → completed

2. Checker B verifies and finds a bug:
   └── reject_verification(agent_name="B", task_id="TASK-1", reason="Form doesn't validate email format - accepts 'abc' as valid email")
   └── Task moves back to "in_progress"

3. Builder A receives rejection with detailed reason:
   └── Fixes email validation
   └── Re-submits for review

4. Lead C re-approves → completed

5. Checker B re-verifies:
   └── verify_task(agent_name="B", task_id="TASK-1", notes="Email validation now correct")
   └── Task moves to "verified"
```

### Two-Rejection Escalation

```
1. TASK-1 completed, Checker B rejects (first time)
   └── Builder A fixes, re-submits, re-approved

2. Checker B rejects again (second time)
   └── System auto-notifies lead: "TASK-1 rejected twice by B — escalation required"
   └── Lead triages: reassign builder, reassign checker, or intervene directly
```

### Goal Confirmation Flow

```
1. GOAL-1 has 3 tasks: TASK-1 (verified), TASK-2 (verified), TASK-3 (verified)
   └── goal_status shows: all tasks verified → goal moves to pending_verify

2. Lead C does integration verification:
   └── Tests the whole product, not just individual pieces
   └── Finds integration bug: "Tasks work individually but CSS conflicts between components"

3. Lead rejects goal:
   └── Goal moves back to "active"
   └── Lead creates TASK-4: "Fix CSS conflicts between login and dashboard"
   └── link_task_to_goal(task_id="TASK-4", goal_id="GOAL-1")
   └── Existing verified tasks stay verified — no need to re-verify

4. TASK-4 goes through full cycle: build → review → complete → verify

5. All tasks verified again → lead re-tests integration → verify_goal → DONE
```

### Minion Task Verification

```
1. Builder A spawns a minion for grunt work:
   └── Minion completes sub-task, writes output to file

2. Builder A (the pilot) reviews minion output:
   └── submit_for_review(agent_name="A", task_id="TASK-5", summary="Minion generated test data")

3. Checker B (not A) verifies:
   └── The pilot who spawned the minion cannot verify the minion's output
   └── verify_task(agent_name="B", task_id="TASK-5", notes="Test data is valid")
```

---

## Edge Cases

### Regression After Confirmation

A verified task can regress if later changes break it.

- Lead can reopen a verified task: moves it back to `in_progress`
- The original builder is re-assigned by default
- Must go through the full cycle again: review → complete → verify
- Previous verification is invalidated

### Integration Failure at Goal Level

When the goal verification fails but individual tasks are correct:

- Goal moves back to `active`
- Lead creates new fix tasks targeting the integration issue
- Previously verified tasks **stay verified** — they passed individually
- Only new tasks need the full verification cycle
- Goal re-enters `pending_verify` when all tasks (including new ones) are verified

### No Nesting

Goals are flat containers. There is no goal-within-a-goal.

- Structure: `Goal → [Task, Task, Task, ...]`
- If a goal is too large, split it into multiple goals
- Tasks cannot contain sub-tasks — use separate tasks with dependency links instead
- This keeps the system simple and auditable

### Solo Agent Edge Case

When only one agent is available (no one else to verify):

- Lead must verify tasks themselves
- The triple-separation rule relaxes to: builder != verifier (minimum)
- This is a known weakness — solo verification is less reliable than independent verification
