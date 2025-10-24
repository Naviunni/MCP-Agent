# 🍕 MCP Pizza Web Server

An MCP server (FastMCP) that drives the Domino's website via Playwright.

Goals
- Prefill address and open the Domino's location search page (Delivery/Carryout).
- Optionally search the menu and add items, then navigate to checkout.
- Keep actions exposed as simple MCP tools you can call from Janet or the `mcp` CLI.

Prereqs
- Python 3.10+
- Install deps:
  - `pip install -r requirements.txt`
  - `python -m playwright install chromium`

Run
- `python pizza_server.py`
- Or with the MCP CLI: `mcp dev` (if configured)

Tools
- `start_browser(headless: bool = False)`
- `open_search(address: str, service: str = "Delivery")`
- `add_item(query: str, quantity: int = 1)`
- `go_checkout()`
- `stop_browser()`

Notes
- Selectors are best-effort; the Domino's UI may change. The server logs steps and errors to help debug.
- For reliable automation across stores and locales, prefer smaller steps (open search, then add items) and confirm visually.
- If you want deeper integration (auto-filling checkout forms), we can add tools like `fill_checkout(first, last, email, phone)` once selectors are validated.
