# janet_search.py
import os
import re
import asyncio
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession
from openai import AsyncOpenAI

# Load environment variables (BRIGHT_API_TOKEN, WEB_UNLOCKER_ZONE)
load_dotenv()

# OpenAI API key (used if summarizing with OpenAI)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


def _clean_html(text: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


@asynccontextmanager
async def search_session():
    """Connects to Bright Data MCP web search server."""
    token = os.getenv("BRIGHT_API_TOKEN")
    if not token:
        raise ValueError("❌ Missing BRIGHT_API_TOKEN. Add it to your .env file or export it.")

    env = {
        **os.environ,
        "API_TOKEN": token,
        # "WEB_UNLOCKER_ZONE": os.getenv("WEB_UNLOCKER_ZONE", "mcp_unlocker"),
    }

    server = StdioServerParameters(
        command="npx",
        args=["@brightdata/mcp"],
        env=env,
    )

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("🌍 Connected to Bright Data Web MCP server")
            yield session


async def perform_web_search(
    session: ClientSession,
    query: str,
    *,
    use_ollama: bool = True,
    openai_model: str = "gpt-4o-mini",
    ollama_model: str = "llama3",
):
    """Run a web search using Bright Data and summarize results."""
    print(f"🔎 Searching the web for: {query}")
    result = await session.call_tool("search_engine", arguments={"query": query})
    cleaned = []

    # print(result)

    if result and result.content:
        for c in result.content:
            t = getattr(c, "text", None)
            if t:
                cleaned.append(_clean_html(t))

    if not cleaned:
        print("No results or invalid output.")
        return None

    # Print snippets before summarization
    print("\n🌐 Raw snippets:")
    for i, snippet in enumerate(cleaned[:3], start=1):
        print(f"{i}. {snippet[:250]}...\n")

    # Optional LLM summarization respecting model switch
    summary_prompt = (
        f"Summarize these search results into 3–4 concise bullet points:\n\n"
        + "\n".join(cleaned[:5])
    )

    try:
        if use_ollama:
            import ollama
            print(f"🧠 Summarizing with Ollama: {ollama_model}")
            response = ollama.chat(
                model=ollama_model,
                messages=[{"role": "user", "content": summary_prompt}],
            )
            summary = response["message"]["content"].strip()
            print("🧠 Summary:\n" + summary)
        elif OPENAI_API_KEY:
            client = AsyncOpenAI(api_key=OPENAI_API_KEY)
            print(f"🧠 Summarizing with OpenAI: {openai_model}")
            completion = await client.chat.completions.create(
                model=openai_model,
                messages=[{"role": "user", "content": summary_prompt}],
                temperature=0.2,
            )
            summary = completion.choices[0].message.content
            print("🧠 Summary:\n" + summary)
        else:
            # No summarization available
            pass
    except Exception as e:
        print(f"⚠️ Summarization failed: {e}")

    return cleaned
