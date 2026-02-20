# EXACT FIX FOR DEAD DROP MCP TOOLS NOT LOADING

## Problem
Dead Drop MCP tools not appearing in Claude Code despite server running.

## Root Cause
**Port mismatch between MCP server and client config:**
- Dead Drop server running on port **9400**
- MCP config files pointing to port **9500**

## Files That Needed Fixing

### 1. `~/.mcp.json` (PRIMARY CONFIG - Claude Code reads this)
**Before:**
```json
{
  "mcpServers": {
    "dead-drop": {
      "type": "http",
      "url": "http://localhost:9500/mcp"  ← WRONG PORT
    }
  }
}
```

**After:**
```json
{
  "mcpServers": {
    "dead-drop": {
      "type": "http",
      "url": "http://localhost:9400/mcp"  ← CORRECT PORT
    }
  }
}
```

### 2. `~/.claude/settings.json` (SECONDARY - also had wrong port)
**Before:**
```json
{
  "mcpServers": {
    "dead-drop": {
      "type": "http",
      "url": "http://localhost:9500/mcp"  ← WRONG PORT
    }
  }
}
```

**After:**
```json
{
  "mcpServers": {
    "dead-drop": {
      "type": "http",
      "url": "http://localhost:9400/mcp"  ← CORRECT PORT
    }
  }
}
```

## Steps Taken

1. **Verified server was running:**
   ```bash
   curl http://localhost:9400/mcp
   # Returned: {"error": "Missing session ID"}
   # This proved server was alive on port 9400
   ```

2. **Found the actual config file:**
   - Initially looked at `~/.claude/settings.json`
   - **Key discovery:** Claude Code actually reads `~/.mcp.json`

3. **Fixed BOTH config files:**
   - Changed port `9500` → `9400` in `~/.mcp.json`
   - Changed port `9500` → `9400` in `~/.claude/settings.json`

4. **Restarted Claude Code**

5. **Verified tools loaded:**
   - All `mcp__dead-drop__*` tools now available

## Key Lesson
**Claude Code reads MCP server config from `~/.mcp.json`, NOT `~/.claude/settings.json`**

## Quick Reference

**Check server status:**
```bash
docker ps | grep dead-drop
curl http://localhost:9400/mcp
```

**Check config:**
```bash
cat ~/.mcp.json
cat ~/.claude/settings.json
```

**If tools not loading:**
1. Verify server running on port 9400
2. Check `~/.mcp.json` has correct port
3. Restart Claude Code
