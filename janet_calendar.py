# Janet_calendar.py
import os
import asyncio
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@asynccontextmanager
async def connect_calendar_server():
    """
    Launches the Google Calendar MCP server and yields a ready session.
    """
    server = StdioServerParameters(
        command="npx",
        args=["@cocal/google-calendar-mcp"],
        env=os.environ.copy(),
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


# ---------- Calendar Actions ----------

async def handle_create_event(session: ClientSession, params: dict):
    """
    Create a calendar event.
    Expected params:
        summary (str), start (ISO datetime), end (ISO datetime),
        attendees (list of emails, optional), location (optional)
    """
    required = ["summary", "start", "end"]
    missing = [f for f in required if f not in params or not params[f]]
    if missing:
        print(f"❌ Missing fields: {', '.join(missing)}")
        return

    # ✅ Ensure correct types for attendees
    if "attendees" in params:
        if isinstance(params["attendees"], str):
            params["attendees"] = [{"email": params["attendees"]}]
        elif isinstance(params["attendees"], list):
            params["attendees"] = [{"email": a} if isinstance(a, str) else a for a in params["attendees"]]
    else:
        params["attendees"] = []

    # ✅ Add default calendarId
    params["calendarId"] = "primary"

    print("\n--- Event Preview ---")
    print("Title:", params["summary"])
    print("Start:", params["start"])
    print("End:", params["end"])
    if params["attendees"]:
        print("Attendees:", ", ".join(a["email"] for a in params["attendees"]))
    if params.get("location"):
        print("Location:", params["location"])

    confirm = input("Create this event? [y/N] ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    # ✅ Call correct tool name for this server
    result = await session.call_tool("create-event", arguments=params)
    text = result.content[0].text if result.content else result
    print("✅", text)


import json
from datetime import datetime
from dateutil import parser as dateparser  # pip install python-dateutil


# async def handle_list_events(session: ClientSession, params: dict | None = None):
#     """
#     List upcoming events for the primary calendar, formatted neatly.
#     """
#     now = datetime.now().replace(microsecond=0)
#     args = {
#         "calendarId": "primary",
#         "timeMin": now.isoformat(),
#         "maxResults": 10,
#     }

#     print(f"🔍 Fetching events since {args['timeMin']}...")

#     try:
#         result = await session.call_tool("list-events", arguments=args)
#     except Exception as e:
#         print("⚠️ Error listing events:", e)
#         return

#     if not result.content or not result.content[0].text:
#         print("No events found.")
#         return

#     raw = result.content[0].text

#     # Some MCP servers return stringified JSON; handle both
#     try:
#         events_json = json.loads(raw)
#         events = events_json.get("events") or events_json.get("items") or []
#     except Exception:
#         # Try to extract valid JSON fragment if it's a long mixed string
#         start = raw.find("[")
#         end = raw.rfind("]")
#         if start != -1 and end != -1:
#             try:
#                 events = json.loads(raw[start:end + 1])
#             except Exception:
#                 print("⚠️ Couldn't parse events properly.")
#                 print(raw[:300], "...")
#                 return
#         else:
#             print("⚠️ No structured event data found.")
#             print(raw[:300], "...")
#             return

#     if not events:
#         print("No events found.")
#         return

#     print("\n📅 Upcoming Events:\n")

#     for ev in events[:10]:
#         title = ev.get("summary", "Untitled Event")
#         start_obj = ev.get("start", {})
#         end_obj = ev.get("end", {})
#         html = ev.get("htmlLink", "")

#         # handle all-day vs dateTime
#         if "dateTime" in start_obj:
#             start = dateparser.parse(start_obj["dateTime"])
#             end = dateparser.parse(end_obj.get("dateTime", start_obj["dateTime"]))
#             time_str = f"{start.strftime('%a, %b %d, %Y, %I:%M %p')} – {end.strftime('%I:%M %p')}"
#         elif "date" in start_obj:
#             start = dateparser.parse(start_obj["date"])
#             time_str = f"{start.strftime('%a, %b %d, %Y')} (All day)"
#         else:
#             time_str = "(No start time)"

#         print(f"• {title} — {time_str}")
#         if html:
#             print(f"  ↪️ {html}")

#     print()


async def handle_list_events(session: ClientSession, params: dict | None = None):
    """
    List calendar events for the given date range.
    Expected params (provided by LLM):
        - start_date: ISO 8601 datetime or natural text
        - end_date: ISO 8601 datetime or natural text
    """
    now = datetime.now()

    # Use LLM-provided date range, or default to today
    start_date_text = (params or {}).get("start_date")
    end_date_text = (params or {}).get("end_date")
    if not start_date_text or not end_date_text:
        print("⚠️ LLM did not specify a date range — defaulting to today.")

    if start_date_text:
        start_dt = dateparser.parse(start_date_text)
    else:
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if end_date_text:
        end_dt = dateparser.parse(end_date_text)
    else:
        end_dt = now.replace(hour=23, minute=59, second=59, microsecond=0)

    args = {
        "calendarId": "primary",
        "timeMin": start_dt.isoformat(),
        "timeMax": end_dt.isoformat(),
        "maxResults": 10,
    }

    print(f"🔍 Fetching events from {args['timeMin']} to {args['timeMax']}...")

    try:
        result = await session.call_tool("list-events", arguments=args)
    except Exception as e:
        print("⚠️ Error listing events:", e)
        return

    if not result.content or not result.content[0].text:
        print("No events found.")
        return

    raw = result.content[0].text
    try:
        events_json = json.loads(raw)
        events = events_json.get("events") or events_json.get("items") or []
    except Exception:
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1:
            try:
                events = json.loads(raw[start:end + 1])
            except Exception:
                print("⚠️ Couldn't parse events properly.")
                return
        else:
            print("⚠️ No structured event data found.")
            return

    if not events:
        print("No events found for that range.")
        return

    print(f"\n📅 Events from {start_dt.strftime('%b %d')} to {end_dt.strftime('%b %d')}:\n")

    for ev in events[:10]:
        title = ev.get("summary", "Untitled Event")
        start_obj = ev.get("start", {})
        end_obj = ev.get("end", {})
        html = ev.get("htmlLink", "")

        if "dateTime" in start_obj:
            start = dateparser.parse(start_obj["dateTime"])
            end = dateparser.parse(end_obj.get("dateTime", start_obj["dateTime"]))
            time_str = f"{start.strftime('%a, %b %d, %Y, %I:%M %p')} – {end.strftime('%I:%M %p')}"
        elif "date" in start_obj:
            start = dateparser.parse(start_obj["date"])
            time_str = f"{start.strftime('%a, %b %d, %Y')} (All day)"
        else:
            time_str = "(No start time)"

        print(f"• {title} — {time_str}")
        if html:
            print(f"  ↪️ {html}")
    print()



async def handle_delete_event(session: ClientSession, params: dict):
    """
    Delete an event by ID or summary.
    """
    if not params.get("id") and not params.get("summary"):
        print("❌ Missing 'id' or 'summary' to delete event.")
        return
    result = await session.call_tool("delete-event", arguments=params)
    text = result.content[0].text if result.content else result
    print("🗑️", text)
