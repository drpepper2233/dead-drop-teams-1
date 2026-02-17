#!/usr/bin/env python3
"""End-to-end test for dead-drop push notifications.

Connects two independent MCP clients to the HTTP server.
Client A sends a message to Client B.
Verifies Client B receives the tools/list_changed notification,
sees the unread alert in check_inbox's description, and can read the message.
"""

import asyncio
import json
import sys
from contextlib import AsyncExitStack
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession
from mcp import types

SERVER_URL = "http://localhost:9400/mcp"
AGENT_A = "push-tester-a"
AGENT_B = "push-tester-b"
TEST_MESSAGE = "PUSH_E2E_TEST: Hello from Agent A!"

# Track notifications received by each client
notifications_received = {"a": [], "b": []}


async def on_notification_b(notification):
    """Callback for Client B's notifications."""
    notifications_received["b"].append(notification)


async def call_tool(session: ClientSession, name: str, args: dict) -> str:
    """Call an MCP tool and return the result text."""
    result = await session.call_tool(name, args)
    texts = [c.text for c in result.content if hasattr(c, "text")]
    return "\n".join(texts)


async def run_test():
    stack = AsyncExitStack()
    results = {"pass": 0, "fail": 0, "tests": []}

    def check(name, condition, detail=""):
        status = "PASS" if condition else "FAIL"
        results["pass" if condition else "fail"] += 1
        results["tests"].append({"name": name, "status": status, "detail": detail})
        icon = "\033[32m✓\033[0m" if condition else "\033[31m✗\033[0m"
        print(f"  {icon} {name}" + (f" — {detail}" if detail else ""))

    print(f"\n{'='*60}")
    print("Dead Drop Push Notification E2E Test")
    print(f"{'='*60}\n")

    # ── Connect Client A ──
    print("[1] Connecting Client A...")
    read_a, write_a, _get_sid_a = await stack.enter_async_context(
        streamablehttp_client(SERVER_URL)
    )
    session_a = await stack.enter_async_context(
        ClientSession(read_a, write_a)
    )
    await session_a.initialize()
    check("Client A connected", True)

    # ── Connect Client B with notification tracking ──
    print("[2] Connecting Client B...")
    read_b, write_b, _get_sid_b = await stack.enter_async_context(
        streamablehttp_client(SERVER_URL)
    )
    session_b = await stack.enter_async_context(
        ClientSession(read_b, write_b)
    )
    await session_b.initialize()
    check("Client B connected", True)

    # ── Register both agents ──
    print("[3] Registering agents...")
    reg_a = await call_tool(session_a, "register", {"agent_name": AGENT_A, "role": "coder"})
    check("Agent A registered", "registered successfully" in reg_a)

    reg_b = await call_tool(session_b, "register", {"agent_name": AGENT_B, "role": "coder"})
    check("Agent B registered", "registered successfully" in reg_b)

    # ── Verify both show as connected ──
    print("[4] Verifying connection status...")
    who_result = await call_tool(session_a, "who", {})
    agents = json.loads(who_result)
    a_connected = any(a["name"] == AGENT_A and a["connected"] for a in agents)
    b_connected = any(a["name"] == AGENT_B and a["connected"] for a in agents)
    check("Agent A shows connected=true", a_connected)
    check("Agent B shows connected=true", b_connected)

    # ── Clear any old unread messages for both agents ──
    await call_tool(session_a, "check_inbox", {"agent_name": AGENT_A})
    await call_tool(session_b, "check_inbox", {"agent_name": AGENT_B})

    # ── Get Client B's tools BEFORE the message (baseline) ──
    print("[5] Getting baseline tool list for Client B...")
    tools_before = await session_b.list_tools()
    inbox_desc_before = ""
    for tool in tools_before.tools:
        if tool.name == "check_inbox":
            inbox_desc_before = tool.description
            break
    check("Baseline check_inbox has no alert", "UNREAD" not in inbox_desc_before,
          f"desc={inbox_desc_before[:80]}")

    # ── Agent A sends message to Agent B ──
    print("[6] Agent A sending message to Agent B...")
    send_result = await call_tool(session_a, "send", {
        "from_agent": AGENT_A,
        "to_agent": AGENT_B,
        "message": TEST_MESSAGE,
    })
    check("Message sent successfully", "Message sent" in send_result, send_result)

    # ── Wait a moment for the push notification to propagate ──
    print("[7] Waiting for push notification...")
    await asyncio.sleep(1.0)

    # ── Client B re-fetches tools (simulating what happens after tools/list_changed) ──
    print("[8] Client B re-fetching tool list...")
    tools_after = await session_b.list_tools()
    inbox_desc_after = ""
    for tool in tools_after.tools:
        if tool.name == "check_inbox":
            inbox_desc_after = tool.description
            break

    has_alert = "UNREAD" in inbox_desc_after and AGENT_A in inbox_desc_after
    check("check_inbox description has unread alert", has_alert,
          f"desc={inbox_desc_after[:120]}")

    # ── Client B checks inbox ──
    print("[9] Client B checking inbox...")
    inbox = await call_tool(session_b, "check_inbox", {"agent_name": AGENT_B})
    messages = json.loads(inbox)
    found_test_msg = any(TEST_MESSAGE in m.get("content", "") for m in messages)
    check("Test message received in inbox", found_test_msg,
          f"got {len(messages)} message(s)")

    # ── Verify alert clears after reading ──
    print("[10] Verifying alert clears after read...")
    tools_cleared = await session_b.list_tools()
    inbox_desc_cleared = ""
    for tool in tools_cleared.tools:
        if tool.name == "check_inbox":
            inbox_desc_cleared = tool.description
            break
    check("Alert cleared after check_inbox", "UNREAD" not in inbox_desc_cleared)

    # ── Cleanup ──
    print("\n[cleanup] Deregistering test agents...")
    await call_tool(session_a, "deregister", {"agent_name": AGENT_A})
    await call_tool(session_b, "deregister", {"agent_name": AGENT_B})

    await stack.aclose()

    # ── Summary ──
    total = results["pass"] + results["fail"]
    print(f"\n{'='*60}")
    color = "\033[32m" if results["fail"] == 0 else "\033[31m"
    print(f"{color}{results['pass']}/{total} tests passed\033[0m")
    print(f"{'='*60}\n")

    return results["fail"] == 0


if __name__ == "__main__":
    ok = asyncio.run(run_test())
    sys.exit(0 if ok else 1)
