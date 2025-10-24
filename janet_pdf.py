# janet_pdf.py
import os
from contextlib import asynccontextmanager
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pdf_cache = {}  # store PDF text for later question answering


@asynccontextmanager
async def pdf_session():
    """
    Connects to the DeepSeekMine PDF Reader MCP (txt_server.py)
    running in stdio mode.
    """
    server = StdioServerParameters(
        command="python",
        args=["mcp-pdf-server/txt_server.py"],  # adjust path if needed
        env=os.environ.copy(),
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def handle_read_pdfs(session: ClientSession, params: dict):
    """
    Calls the MCP 'read_pdf_text' tool to extract text from PDFs and cache it.
    """
    sources = params.get("sources", [])
    if not sources:
        print("⚠️ No PDF sources provided.")
        return

    for src in sources:
        file_path = src.get("path")
        if not file_path:
            continue
        print(f"📘 Reading {file_path} ...")

        try:
            result = await session.call_tool(
                "read_pdf_text",
                arguments={"file_path": file_path, "start_page": 1, "end_page": None},
            )
            text = result.content[0].text.strip() if result.content else ""
            pdf_cache[file_path] = text
            print(f"✅ Cached {len(text)} characters from {file_path}")
        except Exception as e:
            print(f"❌ Error reading {file_path}: {e}")


async def handle_query_pdfs(
    question: str,
    llm_client,
    use_ollama: bool = True,
    openai_model: str = "gpt-4o",
    ollama_model: str = "llama3",
):
    """
    Answers a question based on cached PDF text using the provided LLM client.
    """
    if not pdf_cache:
        print("⚠️ No PDFs loaded yet. Use 'read_pdf' first.")
        return

    combined_text = "\n".join(pdf_cache.values())[:15000]  # limit for token safety
    # print(combined_text)

    prompt = f"""
You are Janet, an assistant answering based only on the provided PDF contents.

PDF context:
{combined_text}

Question:
{question}

Give a short, clear answer based only on the PDFs. If unsure, say so.
"""

    try:
        if use_ollama:
            # Local model via Ollama (synchronous call inside async; acceptable like janet.py)
            import ollama

            print(f"🧠 Using local Ollama model for PDF QA: {ollama_model}")
            response = ollama.chat(
                model=ollama_model,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": prompt},
                ],
            )
            answer = response["message"]["content"].strip()
        else:
            # OpenAI GPT path
            print(f"🧠 Using OpenAI model for PDF QA: {openai_model}")
            response = await llm_client.chat.completions.create(
                model=openai_model,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
            )
            answer = response.choices[0].message.content.strip()

        print(f"\n🧠 Answer based on PDFs:\n{answer}\n")
        return answer
    except Exception as e:
        print(f"❌ Error during LLM PDF query: {e}")
        return None
