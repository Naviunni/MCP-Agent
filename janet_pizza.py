import json
import os
import subprocess
from typing import List, Dict, Any
from urllib.parse import quote as _urlquote


DOM_TEST_DIR = os.path.join(os.path.dirname(__file__), 'dominos-mcp')
CLI_PATH = os.path.join(DOM_TEST_DIR, 'cli.js')


def _run_node(cmd: List[str]) -> Dict[str, Any]:
    """Run the dominos-mcp/cli.js with given args and return parsed JSON."""
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
        open_stores = [
            s for s in stores
            if s.get('IsOnlineCapable') and s.get('IsDeliveryStore') and s.get('IsOpen') and (s.get('ServiceIsOpen') or {}).get('Delivery')
        ]

        # Prefer specific stores if present (e.g., Northgate #6630)
        preferred_ids = {"6630", 6630}
        prefer = next((s for s in open_stores if s.get('StoreID') in preferred_ids), None)
        if not prefer:
            prefer = next((s for s in stores if s.get('StoreID') in preferred_ids), None)

        rec = prefer or (sorted(open_stores, key=lambda s: s.get('MinDistance', 1e9)) or [stores[0]])[0]
        print("\nNearby stores:")
        for i, s in enumerate(stores[:5]):
            sid = s.get('StoreID', 'unknown')
            addr = (s.get('AddressDescription', 'Unknown address') or '').replace('\n', ', ')
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
        # Extra diagnostics if available
        vr = (price.get('validationResponse') or {}).get('Order') or {}
        pr = (price.get('priceResponse') or {}).get('Order') or {}
        status_items = vr.get('StatusItems') or pr.get('StatusItems') or []
        corrective = vr.get('CorrectiveAction') or pr.get('CorrectiveAction') or {}
        if status_items:
            print("Status items:")
            for s in status_items:
                code = s.get('Code'); msg = s.get('Message')
                print(f"  - {code}: {msg}")
        if corrective:
            print("Suggested corrective actions:")
            for k, v in corrective.items():
                print(f"  - {k}: {v}")
        # Offer quick retry with carryout if delivery not allowed
        err_text = (price.get('error') or '').lower()
        if 'servicemethodnotallowed' in err_text:
            retry = _prompt("Delivery not allowed. Try Carryout instead? (y/n): ")
            if retry.lower() in {"y", "yes"}:
                price = _run_node([
                    "price",
                    "--store", store_id,
                    "--address", address,
                    "--first", first,
                    "--last", last,
                    "--phone", phone,
                    "--email", email,
                    "--items", items_json,
                    "--service", "Carryout",
                ])
                if not price.get('ok'):
                    print(f"❌ Carryout also failed: {price.get('error')}")
                    enc = _urlquote(address)
                    print("\n➡️  You can also order directly:")
                    print(f"  Delivery: https://www.dominos.com/en/pages/order/#/locations/search/?type=Delivery&c={enc}")
                    print(f"  Carryout: https://www.dominos.com/en/pages/order/#/locations/search/?type=Carryout&c={enc}")
                    return
            else:
                enc = _urlquote(address)
                print("\n➡️  You can also order directly:")
                print(f"  Delivery: https://www.dominos.com/en/pages/order/#/locations/search/?type=Delivery&c={enc}")
                print(f"  Carryout: https://www.dominos.com/en/pages/order/#/locations/search/?type=Carryout&c={enc}")
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

    # 6) Collect card details (kept for testing; we do NOT place now)
    print("\n💳 Payment details (for testing — order will NOT be placed)")
    card_number = _prompt("Card number (digits only): ")
    exp = _prompt("Expiration (MM/YY): ")
    cvv = _prompt("CVV: ")
    postal = _prompt("Billing ZIP/Postal code: ")
    tip_in = _prompt("Tip amount (e.g., 3.00) [optional]: ")
    try:
        tip_amt = float(tip_in) if tip_in else 0
    except Exception:
        tip_amt = 0

    print("\n⚠️ Placing the order is currently disabled.")
    print("We have card details, but won’t submit them.")
    print("If you want to actually place the order, uncomment the code in janet_pizza.py.")

    # Example: placing the order via the CLI (DISABLED)
    # place = _run_node([
    #     "place",
    #     "--store", store_id,
    #     "--address", address,
    #     "--first", first,
    #     "--last", last,
    #     "--phone", phone,
    #     "--email", email,
    #     "--items", items_json,
    #     "--cardNumber", card_number,
    #     "--exp", exp,
    #     "--cvv", cvv,
    #     "--postal", postal,
    #     "--tip", str(tip_amt),
    # ])
    # if not place.get("ok"):
    #     print(f"❌ Place order failed: {place.get('error')}")
    # else:
    #     print("✅ Order placed! Confirmation:")
    #     print(json.dumps(place.get('placeResponse'), indent=2))

    # Provide Domino's website links as an alternative
    enc = _urlquote(address)
    print("\n➡️  Prefer ordering on the website? Use:")
    print(f"  Delivery: https://www.dominos.com/en/pages/order/#/locations/search/?type=Delivery&c={enc}")
    print(f"  Carryout: https://www.dominos.com/en/pages/order/#/locations/search/?type=Carryout&c={enc}")
