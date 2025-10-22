import os
import json
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import re

USE_OPENAI = bool(os.getenv("OPENAI_API_KEY"))
OPENAI_MODEL = os.getenv("JANET_MODEL", "gpt-4o-mini")
SENDER_NAME = os.getenv("JANET_SENDER_NAME", "Navya")

# async def interpret_intent(user_text: str) -> dict:
#     """Use OpenAI to parse intent into tool actions."""
#     if not USE_OPENAI:
#         # simple keyword fallback
#         if "send" in user_text and "email" in user_text:
#             return {
#                 "action": "send_email",
#                 "params": {"to": [], "subject": "Hello", "body": user_text}
#             }
#         elif "see" in user_text or "check" in user_text or "response" in user_text:
#             return {"action": "search_emails", "params": {"query": user_text}}
#         return {"action": "unknown", "params": {}}

#     from openai import AsyncOpenAI
#     client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
#     system_prompt = (
#         "You are Janet, an assistant that converts requests into JSON tool calls for Gmail MCP.\n"
#         "Supported actions: send_email, draft_email, read_email, search_emails.\n"
#         "Respond ONLY in valid JSON.\n"
#         "For 'check if I got a response', use 'search_emails' with 'query' set to what to look for."
#     )
#     resp = await client.chat.completions.create(
#         model=OPENAI_MODEL,
#         temperature=0,
#         messages=[
#             {"role": "system", "content": system_prompt},
#             {"role": "user", "content": user_text},
#         ],
#     )
#     try:
#         return json.loads(resp.choices[0].message.content)
#     except Exception:
#         print("⚠️ Could not parse model output, defaulting to search.")
#         return {"action": "search_emails", "params": {"query": user_text}}
async def interpret_intent(user_text: str) -> dict | None:
    """Use the OpenAI model strictly — fail if invalid."""
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    system_prompt = (
    "You are Janet, a personnal assistant for Navya that converts user requests into JSON tool calls "
    "for Gmail MCP.\n\n"
    "Supported actions: send_email, draft_email, read_email, search_emails.\n"
    "Respond ONLY in valid JSON with no explanations. When drafting and sending emails, you may sign them as:\n\nBest, \nNavya\n\n"
    "For send_email: include {\"to\": [emails], \"subject\": string, \"body\": string}.\n"
    "For search_emails: always include a Gmail-style query string that uses fields like "
    "'from:', 'to:', 'subject:', or quoted keywords. Example:\n"
    "  User: check if I got a reply from alice@example.com about the meeting\n"
    "  → {\"action\": \"search_emails\", \"params\": {\"query\": \"from:alice@example.com subject:meeting\"}}\n"
    "If unsure, include both 'from:<address>' and main topic words.\n"
    "If key details (like recipient, subject, query) are missing, leave them empty in the JSON so that the user can be asked interactively.\n"
    # "If not enough information is given, respond with:\n"
    # "  {\"action\": \"invalid\", \"reason\": \"Missing address or topic\"}"
    )

    resp = await client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
    )

    try:
        return json.loads(resp.choices[0].message.content)
    except Exception:
        print("I couldn't interpret that request. Try rephrasing.")
        return None

async def handle_send_email(session: ClientSession, params: dict):
    to = params.get("to", [])
    if isinstance(to, str):
        to = [to]
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
    payload = {"to": to, "subject": subject, "body": body}
    result = await session.call_tool("send_email", arguments=payload)
    print("✅", result.content[0].text if result.content else result)


async def handle_search_and_read(session: ClientSession, params: dict):
    """Search for emails, pick the most relevant reply, and read it."""
    query = params.get("query", "")
    print(f"🔍 Searching emails for: {query}")
    result = await session.call_tool("search_emails", arguments={"query": query})

    if not result.content or not result.content[0].text.strip():
        print("No search results or no content returned.")
        return

    text = result.content[0].text

    # Try to parse JSON first, else fallback to regex text parsing
    messages = []
    try:
        messages = json.loads(text)
    except Exception:
        # Regex-based fallback for plain-text output
        entries = re.split(r"\n\s*\n", text.strip())  # split by blank lines
        for block in entries:
            msg = {}
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

    if not messages:
        print("No messages parsed.")
        return

    # Pick the most relevant reply
    reply = next((m for m in messages if m.get("subject", "").lower().startswith("re:")), None)
    target = reply or messages[0]
    msg_id = target.get("id")

    print(f"📨 Found message: {target.get('subject')} from {target.get('from')} ({target.get('date')})")
    print(f"🆔 Reading message ID: {msg_id}")

    if not msg_id:
        print("⚠️ Could not extract messageId from search results.")
        return

    # Read and show the email content
    read_result = await session.call_tool("read_email", arguments={"messageId": msg_id})
    content = read_result.content[0].text if read_result.content else None
    print("\n--- Email Content ---\n", content or "(no content)")
async def handle_draft_email(session: ClientSession, params: dict):
    """Create a draft email in Gmail via MCP."""
    missing = [f for f in ("to", "subject", "body") if f not in params or not params[f]]
    if missing:
        print(f"❌ Missing required field(s): {', '.join(missing)}. Please rephrase your request.")
        return

    if isinstance(params["to"], str):
        params["to"] = [params["to"]]

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

async def clarify_missing_fields(plan: dict) -> dict | None:
    """Ask the user for missing fields and return an updated plan dict."""
    action = plan.get("action")
    params = plan.get("params", {})
    missing = []

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
    follow = input("Could you provide it now? (or 'yes' or 'cancel') ").strip()
    if follow.lower() in {"cancel", "quit", "exit"}:
        print("Okay, cancelled this request.")
        return None

    # Patch values directly
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

async def main():
    server = StdioServerParameters(
        command="npx",
        args=["@gongrzhe/server-gmail-autoauth-mcp"],
        env=os.environ.copy()
    )

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

                action = plan.get("action")
                params = plan.get("params", {})

                # Reject invalid / incomplete plans
                if action == "invalid":
                    print(f"❌ {plan.get('reason', 'I could not extract enough information.')}")
                    continue
                plan = await clarify_missing_fields(plan)
                if not plan:
                    continue
                action = plan.get("action")
                params = plan.get("params", {})

                if action == "send_email":
                    missing = [f for f in ("to", "subject", "body") if f not in params or not params[f]]
                    if missing:
                        print(f"❌ Missing required field(s): {', '.join(missing)}. Please rephrase your request.")
                        continue
                    if isinstance(params["to"], str):
                        params["to"] = [params["to"]]
                    result = await session.call_tool("send_email", arguments=params)
                    print("✅", result.content[0].text if result.content else result)

                elif action == "search_emails":
                    await handle_search_and_read(session, params)

                elif action == "read_email":
                    msg_id = params.get("messageId")
                    if not msg_id:
                        print("❌ Missing messageId.")
                        continue
                    res = await session.call_tool("read_email", arguments={"messageId": msg_id})
                    print(res.content[0].text if res.content else res)
                elif action == "draft_email":
                    await handle_draft_email(session, params)
                else:
                    print("I didn’t understand that command.")


                # plan = await interpret_intent(text)
                # action = plan.get("action")
                # params = plan.get("params", {})

                # if action == "send_email":
                #     await handle_send_email(session, params)
                # elif action == "search_emails":
                #     await handle_search_and_read(session, params)
                # elif action == "read_email":
                #     msg_id = params.get("messageId")
                #     if not msg_id:
                #         msg_id = input("Enter messageId: ").strip()
                #     res = await session.call_tool("read_email", arguments={"messageId": msg_id})
                #     print(res.content[0].text if res.content else res)
                # else:
                #     print("🤔 I didn’t understand that command.")

if __name__ == "__main__":
    asyncio.run(main())
