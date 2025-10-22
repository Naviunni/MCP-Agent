"""
Janet — a simple CLI assistant for Gmail MCP.
"""

from __future__ import annotations

import os
import json
import asyncio
from typing import Any, Dict, List, Optional, TypedDict

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from janet_email import (
    handle_send_email,
    handle_search_and_read,
    handle_read_email,
    handle_draft_email,
    clarify_missing_fields as clarify_email,
)
from janet_calendar import (
    connect_calendar_server,
    handle_create_event,
    handle_list_events,
    handle_delete_event,
)



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


# -------------------------
# LLM Intent Interpretation
# -------------------------

def _build_system_prompt() -> str:
    return (
        "You are Janet, a personal assistant for Navya that converts user requests into JSON tool calls for Gmail and Google Calendar MCP.\n\n"
        "Supported actions: send_email, draft_email, read_email, search_emails, create_event, list_events, delete_event.\n"
        f"Respond ONLY in valid JSON with no explanations. When drafting and sending emails, you may sign them as:\n\nBest, \n{SENDER_NAME}\n\n"
        "For create_event: include fields summary (string), start (ISO datetime), end (ISO datetime), attendees (array), and optional location.\n"
        "For list_events:\n"
            " - You must always infer the correct date range from the user query.\n"
            " - Output 'start_date' and 'end_date' in ISO 8601 format (e.g., '2025-10-23T00:00:00').\n"
            " - If the user says 'today', 'tomorrow', 'this week', 'next week', or specifies a date or range, infer both.\n"
            " - If no date is given, use the next 7 days.\n"
            "Example:\n"
            "User: 'What events do I have for tomorrow?'\n"
            "LLM JSON:\n"
            "{ \"action\": \"list_events\", \"params\": { \"start_date\": \"2025-10-24T00:00:00\", \"end_date\": \"2025-10-24T23:59:59\" } }\n"
            "User: 'Show me events between Oct 25 and Oct 28'\n"
            "LLM JSON:\n"
            "{ \"action\": \"list_events\", \"params\": { \"start_date\": \"2025-10-25T00:00:00\", \"end_date\": \"2025-10-28T23:59:59\" } }"
        "For delete_event: include id (string) or summary (string).\n"
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
# -------------------------
# (Email) Action Handlers are provided by janet_email
# -------------------------


# -------------------------
# Interactive clarification provided by janet_email.clarify_missing_fields


# -------------------------
# Main loop and dispatch
# -------------------------

async def main() -> None:
    server = StdioServerParameters(
        command="npx",
        args=["@gongrzhe/server-gmail-autoauth-mcp"],
        env=os.environ.copy(),
    )

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async with connect_calendar_server() as calendar_session:
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

                    plan = await clarify_email(plan)  # Email-specific clarification
                    if not plan:
                        continue

                    action = plan.get("action") or ""
                    params = plan.get("params", {})

                    if action == "send_email":
                        await handle_send_email(session, params)
                    elif action == "search_emails":
                        await handle_search_and_read(session, params)
                    elif action == "read_email":
                        await handle_read_email(session, params)
                    elif action == "draft_email":
                        await handle_draft_email(session, params)

                    # --- Calendar Tools ---
                    elif action == "create_event":
                        await handle_create_event(calendar_session, params)
                    elif action == "list_events":
                        await handle_list_events(calendar_session, params)
                    elif action == "delete_event":
                        await handle_delete_event(calendar_session, params)
                    else:
                        print("I didn’t understand that command.")  


if __name__ == "__main__":
    asyncio.run(main())
