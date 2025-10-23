"""
janet_pdf.py — PDF reading and RAG-based querying helpers for Janet.

Adds a lightweight RAG index so we only send relevant PDF snippets to the LLM.
"""

import os
import re
import math
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from typing import Dict, List, Tuple

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Raw PDF text cache (per file)
pdf_cache: Dict[str, str] = {}

# Simple RAG index structures (character-based chunking + TF-IDF cosine)
_CHUNK_SIZE = 1200  # characters per chunk
_CHUNK_OVERLAP = 200  # overlap in characters

_STOPWORDS = {
    "the","a","an","and","or","of","to","in","on","for","with","as","by","at","is","are","was","were",
    "be","been","being","from","that","this","it","its","but","not","no","if","then","than","so","such",
    "can","could","may","might","should","would","will","shall","do","does","did","have","has","had","into",
    "we","you","your","i","he","she","they","them","their","our","us","me","my","mine","yours","his","her"
}

class _Chunk:
    __slots__ = ("id","file","text","tokens","norm")
    def __init__(self, cid: int, file_path: str, text: str, tokens: Counter):
        self.id = cid
        self.file = file_path
        self.text = text
        self.tokens = tokens
        # L2 norm of TF vector (idf applied at query time)
        self.norm = math.sqrt(sum(v*v for v in tokens.values())) or 1.0

# Global RAG state
_chunks: Dict[int, _Chunk] = {}
_file_to_chunk_ids: Dict[str, List[int]] = defaultdict(list)
_df: Counter = Counter()  # document frequency per token
_next_chunk_id: int = 1


def _tokenize(text: str) -> List[str]:
    words = re.findall(r"[A-Za-z0-9_]+", text.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


def _chunk_text(text: str, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> List[str]:
    if not text:
        return []
    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        chunk = text[start:end]
        chunks.append(chunk)
        if end == n:
            break
        start = max(0, end - overlap)
    return chunks


def _remove_file_from_index(file_path: str) -> None:
    """Remove a file’s chunks and DF contributions."""
    global _chunks, _file_to_chunk_ids, _df
    ids = _file_to_chunk_ids.get(file_path, [])
    if not ids:
        return
    for cid in ids:
        ch = _chunks.pop(cid, None)
        if not ch:
            continue
        for term in set(ch.tokens.keys()):
            _df[term] -= 1
            if _df[term] <= 0:
                del _df[term]
    _file_to_chunk_ids[file_path] = []


def _add_file_to_index(file_path: str, text: str) -> None:
    """Add or update a file’s chunks into the global RAG index."""
    global _chunks, _file_to_chunk_ids, _df, _next_chunk_id
    # Remove any previous entries for this file
    _remove_file_from_index(file_path)

    for piece in _chunk_text(text):
        tokens = Counter(_tokenize(piece))
        if not tokens:
            continue
        cid = _next_chunk_id
        _next_chunk_id += 1
        ch = _Chunk(cid, file_path, piece, tokens)
        _chunks[cid] = ch
        _file_to_chunk_ids[file_path].append(cid)
        for term in set(tokens.keys()):
            _df[term] += 1


def _idf(term: str) -> float:
    # add-one smoothing
    df = _df.get(term, 0)
    N = max(1, len(_chunks))
    return math.log((1.0 + N) / (1.0 + df)) + 1.0


def _retrieve_chunks(query: str, top_k: int = 4, max_context_chars: int = 12000) -> List[Tuple[str, str]]:
    """Return a list of (file_path, chunk_text) relevant to query using tf-idf cosine."""
    if not _chunks:
        return []
    q_tokens_list = _tokenize(query)
    if not q_tokens_list:
        return []
    q_tf = Counter(q_tokens_list)
    q_weights = {t: (q_tf[t] * _idf(t)) for t in q_tf}
    q_norm = math.sqrt(sum(w*w for w in q_weights.values())) or 1.0

    scores: List[Tuple[float, int]] = []  # (score, chunk_id)
    query_terms = [t for t in q_weights if t in _df]
    for cid, ch in _chunks.items():
        # dot product over query terms only
        dot = 0.0
        present_terms = 0
        for t in query_terms:
            if t in ch.tokens:
                dot += (q_weights[t] * (ch.tokens[t] * _idf(t)))
                present_terms += 1
        if dot <= 0:
            continue
        # approximate doc tf-idf norm by scaling tf-norm with avg idf of present query terms
        if present_terms:
            avg_idf = sum(_idf(t) for t in query_terms if t in ch.tokens) / present_terms
        else:
            avg_idf = 1.0
        d_norm = ch.norm * avg_idf
        score = dot / (q_norm * (d_norm or 1.0))
        if score > 0:
            scores.append((score, cid))

    scores.sort(reverse=True)
    results: List[Tuple[str, str]] = []
    total_chars = 0
    for _, cid in scores:
        ch = _chunks[cid]
        if total_chars + len(ch.text) > max_context_chars:
            continue
        results.append((ch.file, ch.text))
        total_chars += len(ch.text)
        if len(results) >= top_k:
            break
    return results


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
            _add_file_to_index(file_path, text)
            print(f"✅ Indexed {len(text)} characters from {file_path}")
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

    # Retrieve relevant chunks using a lightweight TF-IDF cosine similarity
    top = _retrieve_chunks(question, top_k=4, max_context_chars=12000)
    if not top:
        print("⚠️ No relevant PDF chunks found; falling back to all cached text.")
        sources_block = "\n".join(pdf_cache.values())[:12000]
    else:
        labeled = []
        for fp, chunk in top:
            labeled.append(f"[Source: {os.path.basename(fp)}]\n{chunk}")
        sources_block = "\n\n".join(labeled)

    prompt = f"""
    You are Janet, an assistant answering based only on the provided PDF contents.
    Use only the information in the context snippets. If the answer is not present, say you are unsure.

    Context snippets (with source labels):
    {sources_block}

    Question:
    {question}

    Provide a short, clear answer and cite source filenames in parentheses when relevant.
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
