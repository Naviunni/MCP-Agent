"""
Janet — a simple CLI assistant for Gmail MCP.
"""

from __future__ import annotations

import os
import json
import re
import asyncio
from typing import Any, Dict, List, Optional, TypedDict, Callable

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# -------------------------
# Configuration
# -------------------------

OPENAI_MODEL: str = os.getenv("JANET_MODEL", "gpt-4o-mini")
SENDER_NAME: str = os.getenv("JANET_SENDER_NAME", "Navya")


# -------------------------
# Types
# -------------------------

class Plan(TypedDict, total=False):
    action: str
    params: Dict[str, Any]


class EmailParams(TypedDict, total=False):
    to: List[str]
    subject: str
    body: str
    messageId: str


# -------------------------
# LLM Intent Interpretation
# -------------------------

def _build_system_prompt() -> str:
    return (
        "You are Janet, a personnal assistant for Navya that converts user requests into JSON tool "
        "calls for Gmail MCP.\n\n"
        "Supported actions: send_email, draft_email, read_email, search_emails.\n"
        f"Respond ONLY in valid JSON with no explanations. When drafting and sending emails, you may sign them as:\n\nBest, \n{SENDER_NAME}\n\n"
        "For send_email: include {\"to\": [emails], \"subject\": string, \"body\": string}.\n"
        "For search_emails: always include a Gmail-style query string that uses fields like "
        "'from:', 'to:', 'subject:', or quoted keywords. Example:\n"
        "  User: check if I got a reply from alice@example.com about the meeting\n"
        "  → {\"action\": \"search_emails\", \"params\": {\"query\": \"from:alice@example.com subject:meeting\"}}\n"
        "If unsure, include both 'from:<address>' and main topic words.\n"
        "If key details (like recipient, subject, query) are missing, leave them empty in the JSON so that the user can be asked interactively.\n"
    )


async def interpret_intent(user_text: str) -> Optional[Plan]:
    """Use the OpenAI model — return None if invalid."""
    from openai import AsyncOpenAI  # lazy import to avoid hard dependency at import time

    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    resp = await client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": _build_system_prompt()},
            {"role": "user", "content": user_text},
        ],
    )
    try:
        content = resp.choices[0].message.content
        return json.loads(content) if content else None
    except Exception:
        print("I couldn't interpret that request. Try rephrasing.")
        return None


# -------------------------
# Helpers
# -------------------------

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


# -------------------------
# Action Handlers
# -------------------------

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


# -------------------------
# Interactive clarification
# -------------------------

async def clarify_missing_fields(plan: Plan) -> Optional[Plan]:
    """Ask the user for missing fields and return an updated plan dict."""
    action = plan.get("action")
    params = plan.get("params", {})
    missing: List[str] = []

    if action == "send_email":
        for f in ("to", "subject", "body"):
            if f not in params or not params[f]:
                missing.append(f)
    elif action == "search_emails":
        if "query" not in params or not params["query"]:
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


# -------------------------
# Main loop and dispatch
# -------------------------

async def main() -> None:
    server = StdioServerParameters(
        command="npx",
        args=["@gongrzhe/server-gmail-autoauth-mcp"],
        env=os.environ.copy(),
    )

    handlers: Dict[str, Callable[[ClientSession, Dict[str, Any]], Any]] = {
        "send_email": handle_send_email,
        "search_emails": handle_search_and_read,
        "read_email": handle_read_email,
        "draft_email": handle_draft_email,
    }

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("👋 Janet ready! Type a command (e.g., 'check for response from X')")

            while True:
                text = input("\nYou (or 'quit'): ").strip()
                if text.lower() in {"quit", "exit"}:
                    break

                plan = await interpret_intent(text)
                print("🧩 LLM output:", json.dumps(plan, indent=2))
                if not plan:
                    continue

                if plan.get("action") == "invalid":
                    print(f"❌ {plan.get('reason', 'I could not extract enough information.')}")
                    continue

                plan = await clarify_missing_fields(plan)
                if not plan:
                    continue

                action = plan.get("action") or ""
                params = plan.get("params", {})

                handler = handlers.get(action)
                if not handler:
                    print("I didn’t understand that command.")
                    continue

                await handler(session, params)


if __name__ == "__main__":
    asyncio.run(main())

