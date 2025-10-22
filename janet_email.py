"""
Email-related handlers and utilities for Janet (Gmail MCP).

Exports:
- ACTIONS: mapping of action name -> async handler
- clarify_missing_fields(plan): prompts user for missing email fields
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, TypedDict, Callable

from mcp import ClientSession


class EmailParams(TypedDict, total=False):
    to: List[str]
    subject: str
    body: str
    messageId: str


def _parse_search_results(text: str) -> List[Dict[str, Any]]:
    """Parse search results that might be JSON or plain text blocks."""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass

    messages: List[Dict[str, Any]] = []
    entries = re.split(r"\n\s*\n", text.strip())  # split by blank lines
    for block in entries:
        msg: Dict[str, Any] = {}
        id_match = re.search(r"ID:\s*([^\n]+)", block)
        subj_match = re.search(r"Subject:\s*([^\n]+)", block)
        from_match = re.search(r"From:\s*([^\n]+)", block)
        date_match = re.search(r"Date:\s*([^\n]+)", block)
        if id_match:
            msg["id"] = id_match.group(1).strip()
        if subj_match:
            msg["subject"] = subj_match.group(1).strip()
        if from_match:
            msg["from"] = from_match.group(1).strip()
        if date_match:
            msg["date"] = date_match.group(1).strip()
        if msg:
            messages.append(msg)
    return messages


async def handle_send_email(session: ClientSession, params: EmailParams) -> None:
    """Prompt for missing fields and send an email via MCP."""
    to = params.get("to", []) or []
    if isinstance(to, str):  # type: ignore[unreachable]
        to = [to]  # defensive; model may produce string
    if not to:
        to = [input("Recipient email: ").strip()]
    subject = params.get("subject") or input("Subject: ").strip()
    body = params.get("body") or input("Body: ").strip()

    print("\n--- Email Preview ---")
    print("To:", ", ".join(to))
    print("Subject:", subject)
    print("Body:\n", body)
    confirm = input("Send this email? [y/N] ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return
    payload: EmailParams = {"to": to, "subject": subject, "body": body}
    result = await session.call_tool("send_email", arguments=payload)
    print("✅", result.content[0].text if result.content else result)


async def handle_search_and_read(session: ClientSession, params: Dict[str, Any]) -> None:
    """Search for emails, pick the most relevant reply, and read it."""
    query = params.get("query", "")
    print(f"🔍 Searching emails for: {query}")
    result = await session.call_tool("search_emails", arguments={"query": query})

    if not result.content or not result.content[0].text.strip():
        print("No search results or no content returned.")
        return

    messages = _parse_search_results(result.content[0].text)
    if not messages:
        print("No messages parsed.")
        return

    reply = next((m for m in messages if str(m.get("subject", "")).lower().startswith("re:")), None)
    target = reply or messages[0]
    msg_id = target.get("id")

    print(f"📨 Found message: {target.get('subject')} from {target.get('from')} ({target.get('date')})")
    print(f"🆔 Reading message ID: {msg_id}")

    if not msg_id:
        print("⚠️ Could not extract messageId from search results.")
        return

    read_result = await session.call_tool("read_email", arguments={"messageId": msg_id})
    content = read_result.content[0].text if read_result.content else None
    print("\n--- Email Content ---\n", content or "(no content)")


async def handle_draft_email(session: ClientSession, params: EmailParams) -> None:
    """Create a draft email in Gmail via MCP."""
    missing = [f for f in ("to", "subject", "body") if f not in params or not params[f]]
    if missing:
        print(f"❌ Missing required field(s): {', '.join(missing)}. Please rephrase your request.")
        return

    if isinstance(params.get("to"), str):  # defensive
        params["to"] = [str(params["to"])]

    print("\n--- Draft Preview ---")
    print("To:", ", ".join(params["to"]))
    print("Subject:", params["subject"])
    print("Body:\n", params["body"])
    confirm = input("Save this draft? [y/N] ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    result = await session.call_tool("draft_email", arguments=params)
    text = result.content[0].text if result.content else str(result)
    print("📝 Draft created:", text)


async def handle_read_email(session: ClientSession, params: Dict[str, Any]) -> None:
    """Read a single email by messageId via MCP."""
    msg_id = params.get("messageId")
    if not msg_id:
        print("❌ Missing messageId.")
        return
    res = await session.call_tool("read_email", arguments={"messageId": msg_id})
    print(res.content[0].text if res.content else res)


async def clarify_missing_fields(plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Ask the user for missing fields for email-related actions."""
    action = plan.get("action")
    params = plan.get("params", {})
    missing: List[str] = []

    if action == "send_email":
        for f in ("to", "subject", "body"):
            if f not in params or not params[f]:
                missing.append(f)
    elif action == "search_emails":
        if "query" not in params or not params.get("query"):
            missing.append("query")

    if not missing:
        return plan  # complete

    print(f"🤔 I’m missing some information: {', '.join(missing)}.")
    follow = input("Could you provide it now? (or 'cancel') ").strip()
    if follow.lower() in {"cancel", "quit", "exit"}:
        print("Okay, cancelled this request.")
        return None

    for f in missing:
        val = input(f"Please enter {f}: ").strip()
        if not val:
            print("Still incomplete; cancelling.")
            return None
        if f == "to":
            params[f] = [val]
        else:
            params[f] = val

    plan["params"] = params
    return plan


ACTIONS: Dict[str, Callable] = {
    "send_email": handle_send_email,
    "search_emails": handle_search_and_read,
    "read_email": handle_read_email,
    "draft_email": handle_draft_email,
}

