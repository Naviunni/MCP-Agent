import os
from contextlib import asynccontextmanager
from typing import Dict, Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@asynccontextmanager
async def connect_pizza_server():
    """Launch the Domino's Web MCP server and yield a ready session."""
    # Resolve path to pizza_server.py
    base = os.path.dirname(__file__)
    server_path = os.path.join(base, 'mcp-pizza-web', 'pizza_server.py')
    env = os.environ.copy()
    server = StdioServerParameters(
        command="python",
        args=[server_path],
        env=env,
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def handle_order_pizza_web(session: ClientSession, params: Dict[str, Any]):
    print("🍕 Pizza (Web) — Domino's guided ordering")
    print("Type 'cancel' anytime to abort.")

    # Service and address details
    service = (input("Service (Delivery/Carryout) [Delivery]: ").strip() or "Delivery").title()
    addr_type = (input("Address Type (House/Apartment) [House]: ").strip() or "House").title()
    street = input("Street Address (e.g., 4306 Old College Rd): ").strip()
    apt = input("Suite/Apt (optional): ").strip()
    zip_code = input("ZIP Code (e.g., 77801): ").strip()
    city = input("City (e.g., Bryan): ").strip()
    state = input("State (2 letters, e.g., TX): ").strip().upper()

    if not street or not zip_code or not city or not state:
        print("❌ Missing required address fields.")
        return

    debug = (input("Enable debug screenshots? (y/N): ").strip() or 'n').lower().startswith('y')

    try:
        await session.call_tool("start_browser", arguments={"headless": False})
        if debug:
            await session.call_tool("toggle_debug", arguments={"enabled": True})
        # Open directly to the order overlay per user's suggestion
        print("➡️ Opening order overlay...")
        await session.call_tool("open_order", arguments={"service": service})
        await session.call_tool("wait_for_order_panel", arguments={"timeout_ms": 12000})
        print("➡️ Filling address form...")
        await session.call_tool(
            "fill_address_form",
            arguments={
                "address_type": addr_type,
                "street": street,
                "apt": apt,
                "zip_code": zip_code,
                "city": city,
                "state": state,
            },
        )
        print("➡️ Clicking 'Continue to Delivery' / continue...")
        # Already clicked inside fill step; now confirm location timing screen
        await session.call_tool("confirm_location_now", arguments={})
    except Exception as e:
        print(f"❌ Could not complete location setup: {e}")
        return

    # Navigate to Specialty Pizzas and show a few
    try:
        print("➡️ Navigating to Specialty Pizzas...")
        await session.call_tool("go_specialty_pizzas", arguments={})
        res = await session.call_tool("list_visible_pizzas", arguments={"limit": 12})
        listing = res.content[0].text if res.content else ""
        print("\nSome pizzas we see:")
        print(listing or "(No pizzas detected; you can still try specifying one.)")
    except Exception as e:
        print(f"⚠️ Could not list pizzas: {e}")

    # Ask user to pick a pizza, then crust and size
    name = input("Pizza to open (type part of the name): ").strip()
    if name.lower() in {"cancel", "quit", "exit"}:
        print("Cancelled.")
        return
    try:
        print("➡️ Opening selected pizza...")
        await session.call_tool("open_pizza", arguments={"name_fragment": name})
    except Exception as e:
        print(f"❌ Could not open that pizza: {e}")
        return

    crust = (input("Crust [Hand Tossed/Handmade Pan/New York Style] (default Hand Tossed): ").strip() or "Hand Tossed")
    size = (input("Size [Small/Medium/Large] (default Large): ").strip() or "Large")
    try:
        print("➡️ Configuring crust and size, then adding to cart...")
        await session.call_tool("configure_pizza", arguments={"crust": crust, "size": size})
        await session.call_tool("add_to_cart_and_dismiss_extras", arguments={})
    except Exception as e:
        print(f"❌ Could not configure or add to cart: {e}")
        return

    # Checkout
    try:
        print("➡️ Proceeding to checkout...")
        res = await session.call_tool("go_checkout", arguments={})
        url = res.content[0].text if res.content else ""
        print("🧾 Checkout page:", url)
        print("Fill remaining details directly in the browser.")
    except Exception as e:
        print(f"⚠️ Could not navigate to checkout: {e}")
