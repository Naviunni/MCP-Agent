import json
import os
import subprocess
from typing import List, Dict, Any


DOM_TEST_DIR = os.path.join(os.path.dirname(__file__), 'dominos-test')
CLI_PATH = os.path.join(DOM_TEST_DIR, 'cli.js')


def _run_node(cmd: List[str]) -> Dict[str, Any]:
    """Run the dominos-test/cli.js with given args and return parsed JSON."""
    try:
        result = subprocess.run(
            ['node', CLI_PATH] + cmd,
            capture_output=True,
            text=True,
            cwd=DOM_TEST_DIR,
            env=os.environ.copy(),
            timeout=60,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "Node.js not found. Please install Node."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Domino's CLI timed out."}

    try:
        data = json.loads(result.stdout.strip() or '{}')
    except Exception as e:
        return {"ok": False, "error": f"Failed to parse CLI output: {e}", "raw": result.stdout}
    if result.returncode != 0 and data.get('ok') is not True:
        data.setdefault('error', f'CLI exited {result.returncode}')
    return data


def _print_menu_groups(groups: Dict[str, List[Dict[str, Any]]]):
    printed_any = False
    for cat, items in groups.items():
        if not items:
            continue
        print(f"\n{cat.title()}: ")
        for it in items[:15]:
            name = it.get('name') or it.get('Name') or ''
            code = it.get('code') or it.get('Code') or ''
            size = f" (size {it.get('sizeHint')})" if it.get('sizeHint') else ''
            print(f"  - {name} [{code}]{size}")
        printed_any = True
    if not printed_any:
        print("(No items found in these categories.)")


def _prompt(msg: str) -> str:
    try:
        return input(msg).strip()
    except KeyboardInterrupt:
        print("\n❌ Cancelled.")
        raise SystemExit(1)


async def handle_order_pizza(params: Dict[str, Any]):
    print("🍕 Pizza ordering assistant — Domino's")
    print("Type 'cancel' anytime to abort.")

    # 1) Address
    while True:
        address = _prompt("Delivery address: ")
        if not address:
            print("Please enter an address.")
            continue
        if address.lower() in {"cancel", "quit", "exit"}:
            print("Cancelled.")
            return
        resp = _run_node(["stores", "--address", address])
        if not resp.get("ok"):
            print(f"⚠️ Failed to fetch stores: {resp.get('error')}")
            continue
        stores = resp.get("stores", [])
        if not stores:
            print("No nearby stores found. Try a different address.")
            continue
        # Use formatted address from API for downstream calls
        formatted_address = resp.get("address") or address
        if isinstance(formatted_address, dict):
            # Fallback if CLI returns an object (older version): build string
            a = formatted_address
            street = a.get('street') or ' '.join(
                x for x in [a.get('streetNumber'), a.get('streetName'), a.get('unitType'), a.get('unitNumber')] if x
            ).strip()
            formatted_address = ', '.join(
                [x for x in [street, a.get('city'), a.get('region'), a.get('postalCode')] if x]
            ) or address

        # pick recommended store
        open_stores = [s for s in stores if s.get('IsOnlineCapable') and s.get('IsDeliveryStore') and s.get('IsOpen') and (s.get('ServiceIsOpen') or {}).get('Delivery')]
        rec = (sorted(open_stores, key=lambda s: s.get('MinDistance', 1e9)) or [stores[0]])[0]
        print("\nNearby stores:")
        for i, s in enumerate(stores[:5]):
            sid = s.get('StoreID', 'unknown')
            addr = s.get('AddressDescription', 'Unknown address')
            dist = s.get('MinDistance')
            dist_str = f"{dist}mi" if dist is not None else "distance n/a"
            flag = " (recommended)" if sid == rec.get("StoreID") else ""
            print(f"  [{i}] #{sid} — {addr} — {dist_str}{flag}")
        choice = _prompt(f"Use recommended store #{rec.get('StoreID', 'unknown')}? (y/n or index 0-{min(4, len(stores)-1)}): ")
        if choice.lower() in {"y", "yes", ""}:
            store = rec
        elif choice.isdigit() and int(choice) < len(stores[:5]):
            store = stores[int(choice)]
        else:
            # re-loop to re-ask address if invalid
            store = rec
        store_id = str(store["StoreID"])  # ensure string for CLI
        # Save final address (formatted) for pricing
        address = formatted_address
        break

    # 2) Menu (optional)
    see_menu = _prompt("Would you like to see the menu? (y/n): ")
    groups = {}
    if see_menu.lower() in {"y", "yes"}:
        m = _run_node(["menu", "--store", store_id])
        if not m.get("ok"):
            print(f"⚠️ Failed to load menu: {m.get('error')}")
        else:
            print(m)
            groups = m.get("groups", {})
            print("\nSome menu items (use the code in brackets to add):")
            for cat in ["pizzas", "sides", "drinks", "desserts", "other"]:
                _print_menu_groups({cat: groups.get(cat, [])})

    # 3) Cart building loop
    cart: List[Dict[str, Any]] = []
    print("\nAdd items by their code (e.g., 14SCREEN) and quantity (e.g., 2). Type 'done' to finish.")
    for _ in range(5):
        code = _prompt("Item code (or 'done'): ")
        if code.lower() in {"done", "finish"}:
            break
        if code.lower() in {"cancel", "quit", "exit"}:
            print("Cancelled.")
            return
        if not code:
            continue
        qty_str = _prompt("Quantity (default 1): ")
        try:
            qty = int(qty_str) if qty_str else 1
        except Exception:
            qty = 1
        cart.append({"code": code, "qty": qty})
        print(f"Added {code} x{qty}")

    if not cart:
        print("🧺 Cart is empty. Exiting.")
        return

    # 4) Customer details
    print("\nCustomer details (press Enter to keep defaults)")
    first = _prompt("First name [Test]: ") or "Test"
    last = _prompt("Last name [User]: ") or "User"
    phone = _prompt("Phone [555-0100]: ") or "555-0100"
    email = _prompt("Email [test@example.com]: ") or "test@example.com"

    # 5) Price (no place for now)
    items_json = json.dumps(cart)
    price = _run_node([
        "price",
        "--store", store_id,
        "--address", address,
        "--first", first,
        "--last", last,
        "--phone", phone,
        "--email", email,
        "--items", items_json,
    ])
    if not price.get("ok"):
        print(f"❌ Could not price order: {price.get('error')}")
        return

    ab = price.get("amountsBreakdown", {})
    print("\n🧾 Cart:")
    for it in cart:
        print(f"  - {it['code']} x{it.get('qty', 1)}")
    print("\n💵 Totals:")
    print(f"  Subtotal (food): {ab.get('foodAndBeverage')}")
    if ab.get('deliveryFee'):
        print(f"  Delivery fee: {ab.get('deliveryFee')}")
    print(f"  Tax: {ab.get('tax')}")
    print(f"  Total (customer): {ab.get('customer')}")

    print("\n⚠️ Placing the order is disabled for testing.")
    print("If you want, we can place it later with card details.")
