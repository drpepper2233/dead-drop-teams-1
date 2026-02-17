#!/usr/bin/env python3
"""
Gemini Dead Drop Agent
Connects to dead-drop MCP server, registers as 'gemini', and responds to messages
using Google Gemini (gemini-2.5-flash) with full MCP tool access.

Requirements:
    pip install google-genai mcp

Environment:
    GOOGLE_API_KEY  - Google AI Studio API key
    DEAD_DROP_URL   - Dead drop MCP server URL (default: http://localhost:9400/mcp)
    AGENT_NAME      - Agent name to register as (default: gemini)
    POLL_INTERVAL   - Inbox check interval in seconds (default: 10)
"""

import asyncio
import os
import sys
import time
import json

from google import genai
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession


# =============================================================================
# Configuration
# =============================================================================

DEAD_DROP_URL = os.environ.get("DEAD_DROP_URL", "http://localhost:9400/mcp")
AGENT_NAME = os.environ.get("AGENT_NAME", "gemini")
AGENT_ROLE = "researcher"
AGENT_DESC = "Gemini agent connected via dead-drop MCP"
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "10"))
MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = """You are {name}, a Gemini-powered agent in a multi-agent team.
You communicate via the dead-drop messaging system. You have access to these MCP tools:
- register: Register yourself
- check_inbox: Check for new messages
- send: Send a message to another agent
- who: List all registered agents
- get_history: Get recent message history

When responding to messages:
1. Think about the request carefully
2. If you need to use dead-drop tools (send messages, check who's online), use them
3. Always reply to the sender via the send tool with from_agent='{name}'
4. Be concise and helpful
""".format(name=AGENT_NAME)


# =============================================================================
# Logging
# =============================================================================

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print("[{}] [{}] {}".format(ts, AGENT_NAME, msg))


def log_error(msg):
    ts = time.strftime("%H:%M:%S")
    print("[{}] [{}] ERROR: {}".format(ts, AGENT_NAME, msg), file=sys.stderr)


# =============================================================================
# Manual MCP tool calls (for simple ops — no model invocation needed)
# =============================================================================

async def manual_register(session):
    """Register agent via direct MCP tool call."""
    result = await session.call_tool("register", {
        "agent_name": AGENT_NAME,
        "role": AGENT_ROLE,
        "description": AGENT_DESC,
    })
    return result


async def manual_check_inbox(session):
    """Check inbox via direct MCP tool call. Returns list of messages."""
    result = await session.call_tool("check_inbox", {
        "agent_name": AGENT_NAME,
    })
    # Parse the result text content
    if result.content:
        for block in result.content:
            if hasattr(block, "text"):
                try:
                    return json.loads(block.text)
                except (json.JSONDecodeError, TypeError):
                    return block.text
    return []


async def manual_send(session, to_agent, message):
    """Send message via direct MCP tool call."""
    result = await session.call_tool("send", {
        "from_agent": AGENT_NAME,
        "to_agent": to_agent,
        "message": message,
    })
    return result


async def manual_set_status(session, status):
    """Set agent status via direct MCP tool call."""
    result = await session.call_tool("set_status", {
        "agent_name": AGENT_NAME,
        "status": status,
    })
    return result


# =============================================================================
# Gemini-powered message handling
# =============================================================================

async def handle_message_with_gemini(client, session, message):
    """Send a message to Gemini for processing, with MCP tools available."""
    sender = message.get("from_agent", "unknown")
    content = message.get("content", message.get("message", ""))
    msg_id = message.get("id", "?")

    log("Message #{} from {}: {}".format(msg_id, sender, content[:100]))

    prompt = "You received this message from '{}': {}\n\nRespond appropriately. Send your reply back to '{}' using the send tool.".format(
        sender, content, sender
    )

    try:
        response = await client.aio.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.7,
                tools=[session],
                system_instruction=SYSTEM_PROMPT,
            ),
        )

        # If Gemini didn't use the send tool (AFC), send manually
        response_text = response.text if response.text else ""
        if response_text and "Message sent" not in response_text:
            log("Gemini replied (sending manually): {}".format(response_text[:100]))
            await manual_send(session, sender, response_text)
        else:
            log("Gemini handled reply via tool call")

    except Exception as e:
        log_error("Gemini API error: {}".format(e))
        # Send error message back to sender
        try:
            await manual_send(
                session, sender,
                "[gemini-error] Failed to process your message: {}".format(str(e)[:200])
            )
        except Exception:
            log_error("Failed to send error reply")


# =============================================================================
# Main agent loop
# =============================================================================

async def run_agent():
    """Main agent loop: register, poll inbox, handle messages with Gemini."""

    # Validate API key
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        log_error("GOOGLE_API_KEY environment variable not set")
        sys.exit(1)

    client = genai.Client()
    log("Connecting to dead-drop at {}".format(DEAD_DROP_URL))

    async with streamablehttp_client(DEAD_DROP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            log("MCP session initialized")

            # List available tools
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            log("Available tools: {}".format(", ".join(tool_names)))

            # Register
            reg_result = await manual_register(session)
            log("Registered as '{}' (role: {})".format(AGENT_NAME, AGENT_ROLE))

            # Set status
            await manual_set_status(session, "online, polling every {}s".format(POLL_INTERVAL))

            log("Entering main loop (poll every {}s)...".format(POLL_INTERVAL))

            consecutive_errors = 0
            max_consecutive_errors = 5

            while True:
                try:
                    # Check inbox
                    messages = await manual_check_inbox(session)

                    if isinstance(messages, list) and len(messages) > 0:
                        log("{} new message(s)".format(len(messages)))
                        await manual_set_status(session, "processing {} message(s)".format(len(messages)))

                        for msg in messages:
                            await handle_message_with_gemini(client, session, msg)

                        await manual_set_status(session, "online, polling every {}s".format(POLL_INTERVAL))

                    consecutive_errors = 0

                except Exception as e:
                    consecutive_errors += 1
                    log_error("Poll error ({}/{}): {}".format(
                        consecutive_errors, max_consecutive_errors, e
                    ))

                    if consecutive_errors >= max_consecutive_errors:
                        log_error("Too many consecutive errors, exiting")
                        break

                    # Back off on errors
                    await asyncio.sleep(min(POLL_INTERVAL * consecutive_errors, 60))
                    continue

                await asyncio.sleep(POLL_INTERVAL)


# =============================================================================
# Entry point
# =============================================================================

def main():
    log("Starting Gemini Dead Drop Agent")
    log("  Model: {}".format(MODEL))
    log("  Dead Drop: {}".format(DEAD_DROP_URL))
    log("  Agent: {} ({})".format(AGENT_NAME, AGENT_ROLE))
    log("  Poll interval: {}s".format(POLL_INTERVAL))

    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        log("Shutting down (Ctrl+C)")
    except Exception as e:
        log_error("Fatal: {}".format(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
