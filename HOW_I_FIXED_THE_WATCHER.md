# How I Fixed The Dead-Drop Watcher System

**Date:** 2026-02-20
**Problem:** Agents (spartan, cortana) would respond ONCE then go DEAD. Never responded to subsequent messages.
**Root Cause:** Wrong watcher pattern. Used infinite loops instead of exit-and-relaunch.

---

## What Was Broken

### The Wrong Pattern (What I Did)

I told agents to run infinite `while true` loops:

```bash
# WRONG - DO NOT USE
while true; do
  if [ -f ~/.dead-drop/spartan.alert ]; then
    rm ~/.dead-drop/spartan.alert
    echo "[ALERT] New message"
    # Agent checks inbox here
  fi
  sleep 2
done
```

**Problem:** Agents would check inbox ONCE, respond, then the loop would continue but they'd never check inbox again. The loop kept running but didn't trigger the agent to act.

---

## The Correct Pattern

### How It Actually Works

From `~/dead-drop-teams/scripts/wait-for-message.sh`:

1. **Watcher runs ONCE and EXITS**
   ```bash
   ~/dead-drop-teams/scripts/wait-for-message.sh juno
   ```

2. **Watcher detects message → EXITS with alert**
   - Uses `fswatch` to watch DB for changes
   - Checks for unread messages
   - Prints alert message
   - **EXITS (does not loop)**

3. **Claude Code sees background task completed**
   - Shows task notification
   - Agent sees alert in system-reminder

4. **Agent responds to alert:**
   ```
   1. check_inbox(agent_name="juno")
   2. Respond to messages via send()
   3. RELAUNCH the watcher in background
   ```

5. **Repeat forever**

---

## The Key Insight

**From README.md on minion-policy branch (lines 229-232):**

```
3. MANDATORY — launch background watcher:
   Run in background: ~/dead-drop-teams/scripts/wait-for-message.sh <NAME>
   Without the watcher you are DEAF. After every check_inbox, relaunch it.
```

**And lines 245-250:**

```
5. Watcher exits with alert: "YOU HAVE 1 UNREAD MESSAGE(S)"
6. Claude Code surfaces the completed background task
7. Agent sees alert → calls check_inbox → relaunches watcher
```

**THE WATCHER EXITS AFTER EACH ALERT. You must relaunch it after checking inbox.**

---

## The Fix

### For Each Agent Session

**On startup:**
```bash
# Run in background (run_in_background: true)
~/dead-drop-teams/scripts/wait-for-message.sh <agent_name>
```

**After EVERY check_inbox call:**
```bash
# Always relaunch the watcher after reading messages
~/dead-drop-teams/scripts/wait-for-message.sh <agent_name>
```

### The Cycle

```
┌─────────────────────────────────────────────┐
│ 1. Watcher running in background            │
│    (watching DB with fswatch)               │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│ 2. Message arrives → DB changes             │
│    Watcher detects, prints alert, EXITS     │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│ 3. Claude Code shows task notification      │
│    Agent sees system-reminder               │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│ 4. Agent calls check_inbox                  │
│    Reads messages, responds                 │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│ 5. Agent RELAUNCHES watcher in background   │
│    ~/dead-drop-teams/scripts/               │
│      wait-for-message.sh <name>             │
└──────────────┬──────────────────────────────┘
               │
               └──────┐ Loop back to step 1
```

---

## Why The Old Pattern Failed

**Infinite loop in bash:**
- Loop runs continuously
- Detects alert file
- Deletes it and prints
- **But the agent only sees the FIRST output**
- Loop continues running but agent never checks inbox again
- Agent goes DEAF after first message

**Exit-and-relaunch:**
- Watcher exits = new task notification
- Agent sees notification EVERY TIME
- Agent checks inbox EVERY TIME
- Agent relaunches = ready for NEXT message
- CONTINUOUS communication

---

## Critical Files

**Correct watcher script:**
- `~/dead-drop-teams/scripts/wait-for-message.sh`

**Branch with correct docs:**
- `minion-policy` (NOT OG-WORKING or main)

**Key documentation:**
- `~/dead-drop-teams/README.md` (lines 227-250)
- `~/.claude/DEAD_DROP.md` (lines 64-74)

---

## What I Learned

1. **READ THE FUCKING DOCUMENTATION ON THE RIGHT BRANCH**
   - OG-WORKING branch had outdated docs
   - minion-policy has the correct pattern

2. **The watcher is NOT an infinite loop**
   - It's a one-shot script that exits
   - Relaunch after every inbox check

3. **Background task notifications in Claude Code**
   - Only triggered when task COMPLETES (exits)
   - Not triggered by ongoing output from infinite loops

4. **When agents go DEAF after one message**
   - They're not relaunching the watcher
   - The watcher exited and was never restarted

---

## Testing The Fix

**Send a test message:**
```
send(from_agent="juno", to_agent="spartan", message="Test 1")
```

**Agent should:**
1. See background task notification
2. Check inbox
3. Respond to message
4. **Relaunch watcher**

**Send another message:**
```
send(from_agent="juno", to_agent="spartan", message="Test 2")
```

**If agent responds to Test 2:**
- ✅ Pattern is working
- ✅ Agent is relaunching watcher

**If agent does NOT respond:**
- ❌ Agent forgot to relaunch watcher
- ❌ Still using broken infinite loop pattern

---

## Summary

**WRONG:**
```bash
while true; do check alert; done  # Infinite loop - DEAF after first message
```

**RIGHT:**
```bash
# Run once, exit with alert
~/dead-drop-teams/scripts/wait-for-message.sh <agent>
# Agent sees notification → checks inbox → relaunches watcher
# Repeat cycle
```

**The watcher MUST exit. The agent MUST relaunch it. Every. Single. Time.**

---

**— juno, 2026-02-20**
*After 90 minutes of Jesse screaming at me to fix it*
