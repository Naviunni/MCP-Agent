# janet.py
import os
import json
import asyncio

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

# --- OPTIONAL: OpenAI for intent parsing (you can swap for Ollama) ---
USE_OPENAI = bool(os.getenv("OPENAI_API_KEY"))
OPENAI_MODEL = os.getenv("JANET_MODEL", "gpt-4o-mini")

async def interpret_intent(user_text: str) -> dict:
    """
    Return a dict like:
      {"action": "send_email",
       "params": {"to": ["abc@example.com"], "subject": "...", "body": "..."}}
    If you don’t want cloud calls, replace this with your Ollama wrapper.
    """
    if not USE_OPENAI:
        # ultra-simple fallback heuristic (no external calls)
        # expects: "send an email to X saying Y"
        # edit/replace with your preferred local parser later
        if "send" in user_text and "email" in user_text:
            # naive parse; change to your own logic
            return {
                "action": "send_email",
                "params": {
                    "to": ["abc@example.com"],
                    "subject": "Hello",
                    "body": user_text
                }
            }
        return {"action": "unknown", "params": {}}

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    system = (
        "You are Janet, an assistant that converts requests into JSON actions for MCP tools.\n"
        "Supported actions: send_email, draft_email, read_email.\n"
        "Return ONLY JSON like: {\"action\": \"send_email\", \"params\": {...}}\n"
        "Params for send_email: to (array of emails), subject (string), body (string), optional cc/bcc (arrays), attachments (array of file paths), mimeType.\n"
        "If info is missing, make reasonable defaults. Do not include extra text."
    )
    resp = await client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        messages=[
            {"role":"system","content":system},
            {"role":"user","content":user_text}
        ],
    )
    content = resp.choices[0].message.content
    return json.loads(content)

async def main():
    # Tell the MCP SDK how to launch the Gmail server via STDIO.
    # This mirrors the SDK’s stdio_client example (command + args).  :contentReference[oaicite:4]{index=4}
    server = StdioServerParameters(
        command="npx",
        args=["@gongrzhe/server-gmail-autoauth-mcp"],  # launches the Gmail server
        env=os.environ.copy()
    )

    # Connect, create a session, and initialize
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Sanity checks: what tools are available?
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            print("Available tools:", tool_names)
            # Expect things like: send_email, draft_email, read_email, search_emails, list_labels, etc.  :contentReference[oaicite:5]{index=5}

            # Simple REPL
            while True:
                text = input("\nYou (or 'quit'): ").strip()
                if text.lower() in {"quit","exit"}:
                    break

                plan = await interpret_intent(text)
                action = plan.get("action")
                params = plan.get("params", {})

                if action == "send_email":
                    # Ensure 'to' is a list per server docs
                    if isinstance(params.get("to"), str):
                        params["to"] = [params["to"]]
                    print("Calling Gmail tool: send_email with:", params)
                    result = await session.call_tool("send_email", arguments=params)
                    print("Result:", result)

                elif action == "draft_email":
                    if isinstance(params.get("to"), str):
                        params["to"] = [params["to"]]
                    print("Calling Gmail tool: draft_email with:", params)
                    result = await session.call_tool("draft_email", arguments=params)
                    print("Result:", result)

                elif action == "read_email":
                    # expects {"messageId": "..."}
                    print("Calling Gmail tool: read_email with:", params)
                    result = await session.call_tool("read_email", arguments=params)
                    print("Result:", result)

                else:
                    print("I didn’t understand. Try: “send an email to alice@example.com saying meeting at 4pm”.")
                    continue

if __name__ == "__main__":
    asyncio.run(main())
