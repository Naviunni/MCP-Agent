import asyncio
import logging
from mcp.server.fastmcp import FastMCP
from playwright.async_api import async_playwright
from datetime import datetime
import re
import os

logger = logging.getLogger("mcp-pizza-web")
logging.basicConfig(level=logging.INFO)

mcp = FastMCP("Dominos Web")

# Playwright async globals
_playwright = None
_browser = None
_context = None
_page = None
_DEBUG = False


def _debug_dir():
    base = os.path.join(os.path.dirname(__file__), "debug")
    os.makedirs(base, exist_ok=True)
    return base


def _safe_label(label: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", label)[:60]


async def _screenshot(label: str):
    if not _DEBUG:
        return None
    page = await _ensure_browser()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    name = f"{ts}_{_safe_label(label)}.png"
    path = os.path.join(_debug_dir(), name)
    try:
        await page.screenshot(path=path, full_page=True)
        return path
    except Exception:
        return None


async def _ensure_browser(headless: bool = False):
    global _playwright, _browser, _context, _page
    if _page and not _page.is_closed():
        return _page
    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(headless=headless)
    _context = await _browser.new_context()
    _page = await _context.new_page()
    return _page


async def _teardown():
    global _playwright, _browser, _context, _page
    try:
        if _context:
            await _context.close()
        if _browser:
            await _browser.close()
        if _playwright:
            await _playwright.stop()
    finally:
        _playwright = _browser = _context = _page = None


@mcp.tool()
async def start_browser(headless: bool = False) -> str:
    """Start a Chromium browser session (persisted across tools)."""
    await _ensure_browser(headless=headless)
    await _screenshot("browser_started")
    return "browser_started"


@mcp.tool()
async def stop_browser() -> str:
    """Stop and cleanup the browser session."""
    await _teardown()
    return "browser_stopped"


@mcp.tool()
async def toggle_debug(enabled: bool = True) -> str:
    """Enable or disable debug screenshots (saved under mcp-pizza-web/debug)."""
    global _DEBUG
    _DEBUG = bool(enabled)
    return f"debug={'on' if _DEBUG else 'off'}"


@mcp.tool()
async def capture_screenshot(label: str = "snapshot") -> str:
    """Capture a screenshot and return the saved file path."""
    path = await _screenshot(label)
    return path or "no_screenshot"


@mcp.tool()
async def open_search(address: str, service: str = "Delivery") -> str:
    """Open Domino's location search with address prefilled.

    service: 'Delivery' or 'Carryout'
    """
    page = await _ensure_browser(headless=False)
    service_type = "Delivery" if service.lower().startswith("d") else "Carryout"
    url = f"https://www.dominos.com/en/pages/order/#/locations/search/?type={service_type}&c={address}"
    logger.info(f"Navigating to {url}")
    await page.goto(url, wait_until="domcontentloaded")
    return page.url


@mcp.tool()
async def open_home() -> str:
    """Open Domino's home page."""
    page = await _ensure_browser(headless=False)
    await page.goto("https://www.dominos.com", wait_until="domcontentloaded")
    return page.url


@mcp.tool()
async def open_order(service: str = "Delivery") -> str:
    """Open Domino's with order overlay visible for the given service.

    service: 'Delivery' or 'Carryout' (aka 'Takeout')
    """
    page = await _ensure_browser(headless=False)
    svc = "order_delivery" if service.lower().startswith("d") else "order_carryout"
    url = f"https://www.dominos.com/?type={svc}"
    await page.goto(url, wait_until="domcontentloaded")
    # Wait for the address sidebar panel (dialog) to be ready
    try:
        await page.wait_for_selector("[data-testid='panel-animation'] [data-headlessui-state='open'], role=dialog", timeout=10000)
    except Exception:
        pass
    await _screenshot("open_order")
    return page.url


@mcp.tool()
async def wait_for_order_panel(timeout_ms: int = 10000) -> str:
    """Wait until the order side panel (dialog) is open."""
    page = await _ensure_browser(headless=False)
    await page.wait_for_selector("[data-testid='panel-animation'] [data-headlessui-state='open'], role=dialog", timeout=timeout_ms)
    await _screenshot("panel_ready")
    return "panel_ready"

@mcp.tool()
async def start_location_flow(service: str = "Delivery") -> str:
    """Click 'Choose Your Location' then select Delivery or Carryout."""
    page = await _ensure_browser(headless=False)
    # Ensure we are on home page header
    if "dominos.com" not in page.url:
        await page.goto("https://www.dominos.com", wait_until="domcontentloaded")

    # Dismiss cookie banners if present
    for sel in [
        "button:has-text('Accept All')",
        "button:has-text('Accept')",
        "button:has-text('I Agree')",
        "button:has-text('Got it')",
    ]:
        try:
            b = await page.wait_for_selector(sel, timeout=1000)
            await b.click()
            break
        except Exception:
            continue

    # Click 'Choose Your Location' from top header
    clicked = False
    selector_attempts = [
        "role=button[name=/choose\s*your\s*location/i]",
        "header >> role=button[name=/choose\s*your\s*location/i]",
        "header >> text=/Choose\s*Your\s*Location/i",
        "button:has-text('Choose Your Location')",
        "a:has-text('Choose Your Location')",
    ]
    for sel in selector_attempts:
        try:
            el = await page.wait_for_selector(sel, timeout=1500)
            await el.click()
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        # Try scanning all header buttons and match by accessible name
        try:
            header = page.locator("header")
            btns = await header.locator("button, a").all()
            for b in btns:
                try:
                    name = (await b.inner_text()).strip().lower()
                    if "choose" in name and "location" in name:
                        await b.click()
                        clicked = True
                        break
                except Exception:
                    continue
        except Exception:
            pass
    if not clicked:
        raise RuntimeError("Could not find 'Choose Your Location' button in header.")

    # Select service type
    svc = service.lower()
    if svc.startswith("d"):
        # Wait for sidebar and Delivery option
        await page.wait_for_timeout(300)
        await (await page.wait_for_selector("button:has-text('Delivery')", timeout=8000)).click()
    else:
        await page.wait_for_timeout(300)
        try:
            await (await page.wait_for_selector("button:has-text('Carryout')", timeout=5000)).click()
        except Exception:
            await (await page.wait_for_selector("button:has-text('Takeout')", timeout=5000)).click()
    return "location_flow_started"


@mcp.tool()
async def fill_address_form(address_type: str, street: str, apt: str, zip_code: str, city: str, state: str) -> str:
    """Fill the address form in the sidebar. address_type like 'House' or 'Apartment'. State should be 2-letter code."""
    page = await _ensure_browser(headless=False)
    # Scope all selectors to the open panel/dialog
    panel = page.locator("[data-testid='panel-animation']").locator("[data-headlessui-state='open']").first
    try:
        if not await panel.count():
            panel = page.get_by_role("dialog")
    except Exception:
        panel = page.get_by_role("dialog")

    # Address type dropdown
    try:
        dd = await panel.wait_for_selector("button:has-text('Address Type'), [aria-label*='Address Type']", timeout=6000)
        await dd.click()
        await panel.get_by_text(address_type, exact=True).click()
    except Exception:
        # Some flows use a select
        try:
            sel = await panel.wait_for_selector("select[aria-label*='Address Type'], select[name*='addressType' i]", timeout=3000)
            await sel.select_option(label=address_type)
        except Exception:
            pass

    # Fill fields via labels
    async def fill_by_label(label, value):
        if not value:
            return
        try:
            el = await panel.get_by_label(label)
            await el.fill(value)
        except Exception:
            # Try placeholder match
            try:
                el = await panel.wait_for_selector(f"input[placeholder*='{label}']", timeout=2000)
                await el.fill(value)
            except Exception:
                # Try name attribute fallbacks
                try:
                    low = label.lower()
                    candidates = [
                        f"input[name*='{low}' i]",
                        "input[name*='address1' i]",
                        "input[name*='address' i]",
                        "input[name*='apt' i]",
                        "input[name*='zip' i]",
                        "input[name*='postal' i]",
                        "input[name*='city' i]",
                    ]
                    for sel in candidates:
                        try:
                            el = await panel.wait_for_selector(sel, timeout=1000)
                            await el.fill(value)
                            return
                        except Exception:
                            continue
                except Exception:
                    pass

    await fill_by_label("Street Address", street)
    await fill_by_label("Suite/Apt", apt)
    await fill_by_label("ZIP Code", zip_code)
    await fill_by_label("City", city)

    # State dropdown (2-letter)
    try:
        sel = await panel.wait_for_selector("select[aria-label*='State'], select[name*='state' i]", timeout=4000)
        await sel.select_option(value=state.upper())
    except Exception:
        # Try clicking and typing
        try:
            dd = await panel.wait_for_selector("button[aria-label*='State']", timeout=2000)
            await dd.click()
            await panel.get_by_text(state.upper(), exact=True).click()
        except Exception:
            pass

    await _screenshot("address_filled_before_continue")
    # Continue to timing screen
    try:
        cont = await panel.wait_for_selector("button:has-text('Continue to Delivery'), button:has-text('Continue'), button:has-text('Submit'), button:has-text('Search')", timeout=6000)
        await cont.click()
    except Exception:
        pass
    await _screenshot("address_filled_after_continue")
    return "address_filled"


@mcp.tool()
async def confirm_location_now() -> str:
    """On the order timing screen, keep 'Now' and click 'Confirm Location'."""
    page = await _ensure_browser(headless=False)
    # Restrict to panel/dialog if present
    panel = page.locator("[data-testid='panel-animation']").locator("[data-headlessui-state='open']").first
    try:
        if not await panel.count():
            panel = page.get_by_role("dialog")
    except Exception:
        panel = page.get_by_role("dialog")
    await _screenshot("before_confirm_location")
    try:
        btn = await panel.wait_for_selector("button:has-text('Confirm Location')", timeout=8000)
        await btn.click()
    except Exception as e:
        raise RuntimeError(f"Could not confirm location: {e}")
    await _screenshot("after_confirm_location")
    return "location_confirmed"


@mcp.tool()
async def go_specialty_pizzas() -> str:
    """Navigate to Specialty Pizzas category."""
    page = await _ensure_browser(headless=False)
    await _screenshot("before_go_specialty")
    try:
        await (await page.wait_for_selector("a:has-text('Specialty Pizzas'), button:has-text('Specialty Pizzas')", timeout=8000)).click()
    except Exception:
        # Some layouts have 'Pizzas' first
        await (await page.wait_for_selector("a:has-text('Pizzas'), button:has-text('Pizzas')", timeout=8000)).click()
    await _screenshot("after_go_specialty")
    return page.url


@mcp.tool()
async def list_visible_pizzas(limit: int = 12) -> str:
    """Return a newline-separated list of visible pizza item names on the current page."""
    page = await _ensure_browser(headless=False)
    names = []
    # Heuristic: capture headings inside product cards
    locs = await page.locator("h2, h3").all()
    for l in locs:
        try:
            t = (await l.inner_text()).strip()
            if len(t) > 0 and any(k in t.lower() for k in ["pizza", "extravag", "pepperoni", "veggie", "hawai", "cheese", "deluxe", "margherita", "meat"]):
                names.append(t)
        except Exception:
            continue
        if len(names) >= limit:
            break
    if not names:
        # Fallback: collect any prominent links
        links = await page.locator("a").all()
        for a in links:
            try:
                t = (await a.inner_text()).strip()
                if len(t) > 0 and len(t) < 60:
                    if any(k in t.lower() for k in ["pizza", "extravag", "pepperoni", "veggie", "hawai", "cheese", "deluxe", "margherita", "meat"]):
                        names.append(t)
            except Exception:
                continue
            if len(names) >= limit:
                break
    text = "\n".join(dict.fromkeys(names))
    await _screenshot("list_visible_pizzas")
    return text


@mcp.tool()
async def open_pizza(name_fragment: str) -> str:
    """Click a pizza by name fragment from the list."""
    page = await _ensure_browser(headless=False)
    await _screenshot("before_open_pizza")
    await (await page.get_by_text(name_fragment, exact=False)).click()
    await _screenshot("after_open_pizza")
    return page.url


@mcp.tool()
async def configure_pizza(crust: str = "Hand Tossed", size: str = "Large") -> str:
    """Select crust and size options by visible text if available."""
    page = await _ensure_browser(headless=False)
    # Crust
    try:
        await (await page.get_by_text(crust, exact=False)).click()
    except Exception:
        pass
    # Size
    try:
        await (await page.get_by_text(size, exact=False)).click()
    except Exception:
        pass
    await _screenshot("configured_pizza")
    return "configured"


@mcp.tool()
async def add_to_cart_and_dismiss_extras() -> str:
    """Click Add to Cart, and if Extra Cheese popup appears, click 'No Thanks'."""
    page = await _ensure_browser(headless=False)
    await _screenshot("before_add_to_cart")
    try:
        await (await page.wait_for_selector("button:has-text('Add to Order'), button:has-text('Add to Cart')", timeout=6000)).click()
    except Exception as e:
        raise RuntimeError(f"Could not find Add button: {e}")
    # Dismiss extra dialogs
    try:
        no = await page.wait_for_selector("button:has-text('No Thanks'), button:has-text('No thank')", timeout=3000)
        await no.click()
    except Exception:
        pass
    await _screenshot("after_add_to_cart")
    return "added"
@mcp.tool()
async def add_item(query: str, quantity: int = 1) -> str:
    """Search menu for the query and click 'Add to Order' for the first result quantity times.

    Assumes you are on a store's menu page (after selecting a store for your address).
    """
    page = await _ensure_browser(headless=False)
    candidates = [
        "input[placeholder*='Search']",
        "input[aria-label*='Search']",
        "input[type='search']",
    ]
    box = None
    for sel in candidates:
        try:
            box = await page.wait_for_selector(sel, timeout=3000)
            if box:
                break
        except Exception:
            continue
    if not box:
        raise RuntimeError("Could not find menu search box. Navigate to the menu and try again.")

    await box.fill("")
    await box.type(query)
    await asyncio.sleep(0.5)

    for _ in range(max(1, quantity)):
        btn = None
        try:
            btn = await page.wait_for_selector("button:has-text('Add to Order')", timeout=5000)
        except Exception:
            try:
                btn = await page.wait_for_selector("button:has-text('Add to order')", timeout=2000)
            except Exception:
                pass
        if not btn:
            raise RuntimeError("No 'Add to Order' button found for current search.")
        await btn.click()
        await asyncio.sleep(0.5)

    return "item_added"


@mcp.tool()
async def go_checkout() -> str:
    """Attempt to navigate to checkout by clicking a checkout button."""
    page = await _ensure_browser(headless=False)
    candidates = [
        "button:has-text('Checkout')",
        "a:has-text('Checkout')",
        "button[aria-label*='Checkout']",
    ]
    await _screenshot("before_checkout")
    for sel in candidates:
        try:
            btn = await page.wait_for_selector(sel, timeout=4000)
            if btn:
                await btn.click()
                await asyncio.sleep(1)
                url = page.url
                await _screenshot("after_checkout")
                return url
        except Exception:
            continue
    raise RuntimeError("Could not find a Checkout button. Ensure items are in the cart.")


if __name__ == "__main__":
    logger.info("Starting MCP Pizza Web Server...")
    mcp.run()
