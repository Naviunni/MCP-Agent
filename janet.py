"""
Janet — a simple CLI assistant for Gmail MCP.
"""

from __future__ import annotations

from datetime import datetime
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
import ollama
from janet_pdf import pdf_session, handle_read_pdfs, handle_query_pdfs
from openai import AsyncOpenAI

from janet_search import perform_web_search, search_session




# -------------------------
# Configuration
# -------------------------

OPENAI_MODEL: str = os.getenv("JANET_MODEL", "gpt-4o-mini")
SENDER_NAME: str = os.getenv("JANET_SENDER_NAME", "Navya")
# ----------------- MODEL CONFIGURATION -----------------
USE_OLLAMA = False  # Set to True to use your local Ollama model instead of GPT-4o
OLLAMA_MODEL = "llama3"  # or "mistral", "phi3", "codellama", etc.
OPENAI_MODEL = "gpt-4o"  # or "gpt-4o-mini" for speed
# -------------------------------------------------------



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
    current_date = datetime.now().strftime("%Y-%m-%d")
    # return (
    #     "You are Janet, a personal assistant for Navya that converts user requests into JSON tool calls for Gmail and Google Calendar MCP.\n\n"
    #     "Supported actions: send_email, draft_email, read_email, search_emails, create_event, list_events, delete_event, read_pdf, query_pdf.\n"
    #     f"Respond ONLY in valid JSON with no explanations. When drafting and sending emails, you may sign them as:\n\nBest, \n{SENDER_NAME}\n\n"
    #     f"For context, today's date: {current_date}\n\n"
    #     "For create_event: include fields summary (string), start (ISO datetime), end (ISO datetime), attendees (array), and optional location.\n"
    #     "For list_events:\n"
    #         " - You must always infer the correct date range from the user query.\n"
    #         " - Output 'start_date' and 'end_date' in ISO 8601 format (e.g., '2025-10-23T00:00:00').\n"
    #         " - If the user says 'today', 'tomorrow', 'this week', 'next week', or specifies a date or range, infer both.\n"
    #         " - If no date is given, use the next 7 days.\n"
    #         "Example:\n"
    #         "User: 'What events do I have for tomorrow?'\n"
    #         "LLM JSON:\n"
    #         "{ \"action\": \"list_events\", \"params\": { \"start_date\": \"2025-10-24T00:00:00\", \"end_date\": \"2025-10-24T23:59:59\" } }\n"
    #         "User: 'Show me events between Oct 25 and Oct 28'\n"
    #         "LLM JSON:\n"
    #         "{ \"action\": \"list_events\", \"params\": { \"start_date\": \"2025-10-25T00:00:00\", \"end_date\": \"2025-10-28T23:59:59\" } }"
    #     "For delete_event: include id (string) or summary (string).\n"
    #     "For send_email: include {\"to\": [emails], \"subject\": string, \"body\": string}.\n"
    #     "For search_emails: always include a Gmail-style query string that uses fields like "
    #     "'from:', 'to:', 'subject:', or quoted keywords. Example:\n"
    #     "  User: check if I got a reply from alice@example.com about the meeting\n"
    #     "  → {\"action\": \"search_emails\", \"params\": {\"query\": \"from:alice@example.com subject:meeting\"}}\n"
    #     "For read_pdf:\n"
    #     " - Include {\"sources\": [{\"path\": \"<file_path>\"}]}.\n"
    #     " - Example: 'Read the pdf abc.pdf' → "
    #     "{\"action\": \"read_pdf\", \"params\": {\"sources\": [{\"path\": \"abc.pdf\"}]}}\n"
    #     " - If the filename or path is missing, leave it blank so the user can be asked.\n"
    #     "For query_pdf:\n"
    #     " - Include {\"question\": string}.\n"
    #     " - Example: 'who is John in abc.pdf?' → "
    #     "{\"action\": \"query_pdf\", \"params\": {\"question\": \"who is John in abc.pdf?\"}}\n"
    #     " - The assistant should only answer questions based on PDFs that have already been read.\n\n"

    #     "If unsure, include both 'from:<address>' and main topic words.\n"
    #     "If key details (like recipient, subject, query) are missing, leave them empty in the JSON so that the user can be asked interactively.\n"
    # )


    # def _build_system_prompt() -> str:
    return (
        "You are Janet, a personal assistant for Navya that converts user requests into JSON tool calls "
        "for Gmail, Google Calendar, and the PDF Reader MCP.\n\n"

        # NEW: Include 'answer' and 'ask_user'
        "Supported actions: send_email, draft_email, read_email, search_emails, create_event, "
        "list_events, delete_event, read_pdf, query_pdf, search_web, ask_user.\n\n"

        f"Respond ONLY in valid JSON with no explanations. When drafting or sending emails, you may sign them as:\n\nBest,\n{SENDER_NAME}\n\n"
        f"For context, today's date: {current_date}\n\n"

        # ---------------- DECISION RULES (IMPORTANT) ----------------
        # These prevent misrouting like your example.
        "Decision rules:\n"
        " - Use search_emails ONLY for questions that explicitly relate to the inbox/mail (e.g., 'did I get a reply', 'find email from...').\n"
        "   If external lookup is required and a web tool exists, ask with {\"action\":\"ask_user\",\"params\":{\"question\":\"Should I search the web?\"}}.\n"
        " - If the question is about PDFs you've already read, use query_pdf.\n"
        " - If key details are missing (recipient, filename, dates, etc.), use {\"action\":\"ask_user\",\"params\":{\"question\":\"<what you need>\"}}.\n\n"

        # ---------------- EMAIL RULES ----------------
        "For send_email: include {\"to\": [emails], \"subject\": string, \"body\": string}.\n"
        "For draft_email: same fields as send_email, but action is 'draft_email'.\n"
        "For read_email: include optional filters like {\"from\": string, \"subject\": string}.\n"
        "For search_emails: always include a Gmail-style query string (from:, to:, subject:, keywords). Example:\n"
        "  User: check if I got a reply from alice@example.com about the meeting\n"
        "  → {\"action\": \"search_emails\", \"params\": {\"query\": \"from:alice@example.com subject:meeting\"}}\n\n"

        # ---------------- CALENDAR RULES ----------------
        "For create_event: include summary (string), start (ISO datetime), end (ISO datetime), attendees (array), and optional location.\n"
        "For list_events:\n"
        " - Always infer the correct date range from the query.\n"
        " - Output 'start_date' and 'end_date' in ISO 8601 format (e.g., '2025-10-23T00:00:00').\n"
        " - If the user says 'today', 'tomorrow', 'this week', 'next week', or gives dates, infer both.\n"
        " - If no date is given, use the next 7 days.\n"
        "Example:\n"
        "User: 'What events do I have for tomorrow?'\n"
        "→ {\"action\": \"list_events\", \"params\": {\"start_date\": \"2025-10-24T00:00:00\", \"end_date\": \"2025-10-24T23:59:59\"}}\n"
        "User: 'Show me events between Oct 25 and Oct 28'\n"
        "→ {\"action\": \"list_events\", \"params\": {\"start_date\": \"2025-10-25T00:00:00\", \"end_date\": \"2025-10-28T23:59:59\"}}\n"
        "For delete_event: include id (string) or summary (string).\n\n"

        # ---------------- PDF READER RULES ----------------
        "For read_pdf:\n"
        " - Include {\"sources\": [{\"path\": \"<file_path>\"}]}.\n"
        " - Example: 'Read the pdf shortStory1.pdf' → "
        "{\"action\": \"read_pdf\", \"params\": {\"sources\": [{\"path\": \"shortStory1.pdf\"}]}}\n"
        " - If the filename/path is missing, use ask_user.\n"
        "For query_pdf:\n"
        " - Include {\"question\": string}.\n"
        "Do not paraphrase or rename or change the user's question and preserve the user's exact wording\n"
        " - Example: 'What is the story in shortStory1.pdf about?' → "
        "{\"action\": \"query_pdf\", \"params\": {\"question\": \"What is the story in shortStory1.pdf about?\"}}\n"
        " - Only answer based on PDFs that have already been read.\n\n"

        #WEB SEARCH RULES
        "For search_web: include {\"query\": string} when the user request requires looking up information online.\n"
        '''- Example: 'What is the latest SpaceX Starship status?'
           -{"action": "search_web", "params": {"query": "latest SpaceX Starship status"}}
           - Use this action when you need real-time or external data not covered by email, calendar or PDFs.\n\n'''

        # ---------------- ASK_USER ----------------
        "For ask_user: include {\"question\": string} when clarification is required.\n"
    )



async def interpret_intent(user_text: str) -> Optional[Plan]:
    """Use the OpenAI model — return None if invalid."""
    from openai import AsyncOpenAI  # lazy import to avoid hard dependency at import time

    # client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    # resp = await client.chat.completions.create(
    #     model=OPENAI_MODEL,
    #     temperature=0,
    #     messages=[
    #         {"role": "system", "content": _build_system_prompt()},
    #         {"role": "user", "content": user_text},
    #     ],
    # )
    # try:
    #     content = resp.choices[0].message.content
    #     return json.loads(content) if content else None
    # except Exception:
    #     print("I couldn't interpret that request. Try rephrasing.")
    #     return None
    system_prompt = _build_system_prompt()
    user_input = user_text
    if USE_OLLAMA:
        # --- Local LLM path (Ollama) ---
        try:
            print(f"🧠 Using local Ollama model: {OLLAMA_MODEL}")
            response = ollama.chat(
                model=OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_input},
                ],
            )
            content = response["message"]["content"].strip()
        except Exception as e:
            print("⚠️ Ollama error:", e)
            return None
    else:
        # --- OpenAI GPT path ---
        print(f"🧠 Using OPENAI model: {OPENAI_MODEL}")
        client = AsyncOpenAI()
        try:
            completion = await client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_input},
                ],
                temperature=0,
            )
            content = completion.choices[0].message.content.strip()
        except Exception as e:
            print("⚠️ OpenAI error:", e)
            return None

    # --- Try to extract valid JSON ---
    try:
        # handle extra text around JSON
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1:
            content = content[start:end+1]
        plan = json.loads(content)
        return plan
    except Exception:
        print("Couldn't parse model output as JSON:")
        print(content)
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
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async with connect_calendar_server() as calendar_session:
                print("👋 Janet ready! Type a command (e.g., 'check for response from X')")

                while True:
                    text = input("\nYou (or 'quit'): ").strip()
                    if text.lower() in {"quit", "exit"}:
                        break
                    if text.lower().startswith("switch model"):
                        global USE_OLLAMA
                        USE_OLLAMA = not USE_OLLAMA
                        print(f"🔁 Switched to {'Ollama (local)' if USE_OLLAMA else 'GPT-4o (OpenAI)'}")
                        continue

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
                    if action == "read_pdf":
                        async with pdf_session() as ps:
                            await handle_read_pdfs(ps, params)

                    elif action == "query_pdf":
                        question = params.get("question", text)
                        await handle_query_pdfs(
                            question,
                            client,
                            use_ollama=USE_OLLAMA,
                            openai_model=OPENAI_MODEL,
                            ollama_model=OLLAMA_MODEL,
                        )
                    elif action == "search_web":
                        query = params.get("query")
                        if not query:
                            print("❓ Missing query for web search.")
                            continue

                        try:
                            from janet_search import search_session, perform_web_search
                            async with search_session() as ss:
                                await perform_web_search(ss, query)
                        except Exception as e:
                            print(f"❌ Web search failed: {e}")

                    elif action == "ask_user":
                        followup = params.get("question", "Could you clarify?")
                        print(f"? {followup}")
                    else:
                        print("I didn’t understand that command.")  


if __name__ == "__main__":
    asyncio.run(main())
