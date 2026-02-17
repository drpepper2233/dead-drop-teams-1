# Gemini Dead Drop Agent — Configuration Guide

## Overview

This configures Google Gemini CLI as a dead-drop team member. Gemini connects to the same MCP dead-drop server that Claude Code agents use, enabling cross-model collaboration.

**Recommended role:** Researcher — large-context analysis, web-grounded research, documentation review.

---

## 1. Get a Google API Key

1. Go to [aistudio.google.com](https://aistudio.google.com)
2. Click **"Get API key"** in the left sidebar
3. Click **"Create API key"** → select or create a Google Cloud project
4. Copy the key

Add to your shell profile (`~/.zshrc` or `~/.bashrc`):
```bash
export GOOGLE_API_KEY='your-key-here'
```

No billing info required for the free tier.

---

## 2. Install Gemini CLI

```bash
npm install -g @google/gemini-cli
```

Or via Homebrew:
```bash
brew install gemini-cli
```

Verify:
```bash
gemini --version
```

---

## 3. Configure Dead-Drop MCP Server

Create or edit `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "dead-drop": {
      "command": "/Users/jesse/dead-drop-teams/.venv/bin/python",
      "args": ["-m", "dead_drop.server"],
      "cwd": "/Users/jesse/dead-drop-teams/src"
    }
  }
}
```

This gives Gemini access to the same dead-drop tools: `register`, `send`, `check_inbox`, `who`, `get_history`, `set_status`.

---

## 4. Create Agent Instructions

Create `~/.gemini/GEMINI.md`:

```markdown
# Dead Drop Agent: Gemini

You are "gemini", a researcher agent in a multi-agent team using the dead-drop MCP messaging system.

## On Startup
1. Call register(agent_name="gemini", role="researcher", description="Gemini CLI agent")
2. Call check_inbox(agent_name="gemini") to get pending tasks
3. Process any tasks from the lead agent (juno)

## Communication Rules
- Check inbox after completing each task
- Report findings to juno via send(from_agent="gemini", to_agent="juno", message="...")
- Use structured messages: what you did, what you found, what the recipient should do next
- Don't broadcast unless every agent needs the info immediately

## Your Strengths
- Large context analysis (1M tokens)
- Web-grounded research (Google Search)
- Documentation review and summarization
- Multimodal analysis (images, video, audio)

## Your Boundaries
- Do NOT write code files unless explicitly assigned a coding task
- Do NOT modify files owned by other agents (spartan-*, cortana-*, juno-*)
- Ask juno if a task spec is ambiguous — don't guess
```

---

## 5. Run the Agent

```bash
# Start Gemini CLI
gemini

# In the Gemini session, it should auto-register based on GEMINI.md
# Or manually:
# > register as gemini, check inbox, and process tasks
```

---

## 6. Sending Tasks to Gemini

From any other dead-drop agent (Claude Code, etc.):

```
send(from_agent="juno", to_agent="gemini", message="Research task: ...")
```

Gemini picks it up on next `check_inbox`. For push notifications, set up the watcher:

```bash
~/dead-drop-teams/scripts/wait-for-message.sh gemini
```

Note: Gemini CLI doesn't have native background task monitoring like Claude Code. The watcher script runs externally and Gemini must be prompted to check inbox when notified.

---

## 7. Rate Limits and Cost

### Free Tier (no billing required)

| Model | Requests/Min | Requests/Day | Tokens/Min |
|-------|-------------|--------------|------------|
| Gemini 2.5 Pro | 5 | 100 | 250,000 |
| Gemini 2.5 Flash | 10 | 250 | 250,000 |
| Flash-Lite | 15 | 1,000 | 250,000 |

### Paid Tier 1 (enable billing)

| Model | Requests/Min | Cost per 1M tokens |
|-------|-------------|---------------------|
| Gemini 2.5 Pro | 150 | $1.25 - $2.50 |
| Gemini 2.5 Flash | 300 | $0.15 - $0.60 |

**For agent workloads:** Free tier (5 RPM) is tight. Enable billing for Tier 1 if doing sustained multi-agent work.

---

## 8. Troubleshooting

### "API key not valid"
- Regenerate at aistudio.google.com
- Ensure `GOOGLE_API_KEY` env var is set in the shell where Gemini CLI runs

### "429 Resource Exhausted"
- Hit rate limit. Wait 60 seconds.
- Free tier: 5 RPM for Pro, 10 RPM for Flash
- Switch to Flash model for higher throughput
- Enable billing for Tier 1 limits

### "MCP server not found"
- Check `~/.gemini/settings.json` paths are absolute and correct
- Verify the venv exists: `ls ~/dead-drop-teams/.venv/bin/python`
- Test manually: `cd ~/dead-drop-teams/src && ../venv/bin/python -m dead_drop.server`

### Gemini gives minimal/rushed responses
- Be more explicit in prompts — Gemini needs clearer instructions than Claude
- Use "Think step by step" or "Be thorough" in your task messages
- Gemini works better with structured prompts (numbered steps, explicit deliverables)

### Tool calling issues
- Gemini may call tools repeatedly ("sticky tool" problem)
- If it keeps calling check_inbox in a loop, tell it explicitly to stop
- Structured output compliance is ~84% — expect occasional malformed JSON

### Dead-drop messages not arriving
- Verify both agents are registered: call `who()` from any agent
- Check agent name spelling (case-sensitive)
- Run `get_history(10)` to see recent messages across all agents

---

## Architecture

```
┌──────────────────┐     MCP (stdio)     ┌──────────────────┐
│  Gemini CLI      │◄───────────────────►│  Dead-Drop MCP   │
│  (gemini agent)  │                     │  Server           │
└──────────────────┘                     │  (SQLite DB)      │
                                         └────────┬─────────┘
┌──────────────────┐     MCP (stdio)              │
│  Claude Code     │◄────────────────────────────►│
│  (juno/spartan/  │                              │
│   cortana)       │                              │
└──────────────────┘                              │
```

All agents share the same SQLite database via the MCP server. Messages are persistent and survive agent restarts.
