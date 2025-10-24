import asyncio
from typing import Dict, Any, List, Tuple

from playwright.async_api import async_playwright, Page

# Global Playwright/browser objects to keep the browser alive across calls
_playwright = None
_browser = None
_context = None
_page = None


async def _ensure_browser(headless: bool = False) -> Page:
    global _playwright, _browser, _context, _page
    if _page is not None:
        try:
            if not _page.is_closed():
                return _page
        except Exception:
            pass
    if _playwright is None:
        _playwright = await async_playwright().start()
    if _browser is None:
        _browser = await _playwright.chromium.launch(headless=headless)
    if _context is None:
        _context = await _browser.new_context()
    _page = await _context.new_page()
    return _page


async def _click_if_present(page: Page, selectors: List[str], timeout: int = 2000) -> bool:
    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, timeout=timeout)
            await el.click()
            return True
        except Exception:
            continue
    return False


async def _fill_if_present(page: Page, selectors: List[str], value: str, timeout: int = 2500) -> bool:
    if not value:
        return False
    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, timeout=timeout)
            await el.fill("")
            await el.type(value)
            return True
        except Exception:
            continue
    return False


async def _leave_browser_open(page: Page) -> None:
    """No-op: the browser is kept open by design (not tied to a context)."""
    print("Browser will remain open. You can close it when finished.")


async def _pick_first_address_suggestion(page: Page) -> bool:
    """After typing street address, pick the first suggestion from the autocomplete list.

    Tries common ARIA listbox/option patterns and falls back to ArrowDown+Enter.
    Returns True if a selection interaction was performed.
    """
    # Wait briefly for any suggestion list to appear
    candidates = [
        "[role='listbox'] [role='option']",
        "ul[role='listbox'] li",
        "div[role='option']",
        "[data-testid*='suggest']",
    ]
    # Try clicking the first visible option
    for sel in candidates:
        try:
            first = page.locator(sel).first
            await first.wait_for(state="visible", timeout=1500)
            await first.click()
            await asyncio.sleep(0.2)
            return True
        except Exception:
            continue
    # Fallback: keyboard navigation
    try:
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.2)
        return True
    except Exception:
        return False


async def _dismiss_any_modal(page: Page) -> bool:
    """Dismiss common upsell/overlay modals by clicking 'No Thanks' or closing/ESC.

    Returns True if an action was taken that likely closed a modal.
    """
    acted = False
    # Prefer explicit negative/close actions
    for sel in [
        "button:has-text('No Thanks')",
        "button:has-text('No thank')",
        "button:has-text('No, thanks')",
        "button:has-text('Maybe later')",
        "button:has-text('Skip')",
        "button:has-text('Close')",
        "[aria-label='Close']",
        "[aria-label*='close' i]",
        "button:has-text('Continue to checkout')",
        "button:has-text('Continue shopping')",
    ]:
        try:
            el = await page.wait_for_selector(sel, timeout=1200)
            await el.click()
            acted = True
            break
        except Exception:
            continue
    if not acted:
        # Try pressing Escape
        try:
            await page.keyboard.press("Escape")
            acted = True
        except Exception:
            pass
    if not acted:
        # Try clicking outside (top-left corner)
        try:
            await page.mouse.click(10, 10)
            acted = True
        except Exception:
            pass
    # Small pause to allow UI to update
    if acted:
        await asyncio.sleep(0.3)
    return acted


async def _handle_carryout_store_selection(page: Page) -> None:
    """Carryout flow: click first 'Select Store', then in the confirmation modal click 'Select Store' again."""
    # Click the first Select Store in the list (button or link)
    try:
        locator = page.locator("button:has-text('Select Store'), a:has-text('Select Store')")
        count = await locator.count()
        if count > 0:
            first = locator.nth(0)
            try:
                await first.scroll_into_view_if_needed()
            except Exception:
                pass
            await first.click()
    except Exception:
        pass

    # Wait for a confirmation modal and click Select Store inside it
    try:
        # Prefer role=dialog or aria-modal containers
        dialog = page.get_by_role("dialog")
        # wait briefly for any dialog to appear
        try:
            await dialog.wait_for(state="visible", timeout=5000)
        except Exception:
            # fall back to text match presence
            try:
                await page.wait_for_selector(r"text=/confirm\s+your\s+carryout\s+time/i", timeout=3000)
            except Exception:
                pass
        # Try clicking the Select Store or Continue button within the dialog first
        dlg_clicked = False
        for sel in [
            "button:has-text('Select Store')",
            "button:has-text('Continue')",
            "button:has-text('Confirm')",
        ]:
            try:
                btn = dialog.locator(sel).first
                await btn.wait_for(state="visible", timeout=2500)
                await btn.click()
                dlg_clicked = True
                break
            except Exception:
                continue
        if not dlg_clicked:
            # Try global search as a fallback
            for sel in [
                "button:has-text('Select Store')",
                "button:has-text('Continue')",
                "button:has-text('Confirm')",
            ]:
                try:
                    btn = await page.wait_for_selector(sel, timeout=2000)
                    await btn.click()
                    dlg_clicked = True
                    break
                except Exception:
                    continue
        # If a modal remains, try a generic dismiss and proceed
        if not dlg_clicked:
            await _dismiss_any_modal(page)
    except Exception:
        pass

def _is_large_or_above(size_text: str) -> bool:
    """Return True if size indicates Large or bigger (e.g., Large, XL, Extra Large)."""
    s = (size_text or "").strip().lower()
    if not s:
        return False
    tokens = [s, s.replace("x-", "x").replace("extra ", "extra")]  # minor normalizations
    checks = [
        lambda t: "large" in t,
        lambda t: "xl" in t,
        lambda t: "xlarge" in t,
        lambda t: "x large" in t,
        lambda t: "extra large" in t,
        lambda t: "extra-large" in t,
    ]
    return any(any(chk(t) for t in tokens) for chk in checks)


async def _click_enabled(page: Page, selectors: List[str], timeout: int = 4000) -> bool:
    """Click the first visible, enabled element matching any selector."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=timeout)
            # Some buttons may be in view but disabled
            try:
                if hasattr(loc, "is_enabled"):
                    enabled = await loc.is_enabled()
                    if not enabled:
                        continue
            except Exception:
                pass
            try:
                await loc.scroll_into_view_if_needed()
            except Exception:
                pass
            await loc.click()
            return True
        except Exception:
            continue
    return False


async def _select_by_label(page: Page, label_keywords: List[str], option_text: str) -> None:
    if not option_text:
        return
    # Try ARIA label or visible label association first
    for key in label_keywords:
        try:
            control = page.get_by_label(key, exact=False)
            await control.select_option(label=option_text)
            return
        except Exception:
            # some controls are button/dropdowns rather than native selects
            try:
                btn = page.get_by_role("button", name=lambda name: key.lower() in name.lower())
                await btn.click()
                await page.get_by_role("option", name=lambda n: option_text.lower() in n.lower()).click()
                return
            except Exception:
                continue

    # Fallback: scan for any select near the words
    for key in label_keywords:
        try:
            # narrow to selects that might correspond
            sel = await page.wait_for_selector("select", timeout=1500)
            try:
                await sel.select_option(label=option_text)
                return
            except Exception:
                continue
        except Exception:
            continue


async def _handle_unavailable_combo(page: Page) -> bool:
    """Detect and dismiss 'This Combination is not available' modal by clicking Continue/OK."""
    try:
        await page.wait_for_selector(r"text=/combination\s+is\s+not\s+available/i", timeout=1500)
    except Exception:
        return False
    clicked = await _click_if_present(
        page,
        [
            "button:has-text('Continue')",
            "button:has-text('OK')",
            "button:has-text('Ok')",
            "button:has-text('Close')",
        ],
        timeout=3000,
    )
    await asyncio.sleep(0.3)
    return clicked

async def _choose_option_button(page: Page, keywords: List[str], value_text: str) -> bool:
    """Best-effort: click a button-like option that matches value_text and is enabled."""
    if not value_text:
        return False
    # Try direct button match
    candidates = [
        f"role=button[name=/{value_text}/i]",
        f"button:has-text('{value_text}')",
    ]
    if await _click_enabled(page, candidates, timeout=1500):
        return True
    # Try within sections labeled by keywords
    for key in keywords:
        try:
            section = page.get_by_text(key, exact=False)
            btn = section.locator(f"xpath=ancestor::*[self::section or self::div][1]").get_by_role("button", name=lambda n: value_text.lower() in (n or "").lower())
            await btn.click()
            return True
        except Exception:
            continue


def _normalize_crust(user_input: str) -> str:
    """Map user crust input to a canonical Papa John's label."""
    if not user_input:
        return "Original Crust"
    s = user_input.strip().lower()
    candidates = [
        "Original Crust",
        "Garlic Epic Stuffed Crust",
        "Epic Stuffed Crust",
        "New York Style Crust",
        "Thin Crust",
    ]
    tokenized: List[Tuple[int, str]] = []
    for c in candidates:
        cl = c.lower()
        score = 0
        # simple token overlap score
        for tok in s.replace("style", "style").replace("stuffeed", "stuffed").replace("tork", "york").split():
            if tok and tok in cl:
                score += 1
        tokenized.append((score, c))
    tokenized.sort(reverse=True)
    best = tokenized[0][1] if tokenized else "Original Crust"
    # special handling for short inputs
    if "thin" in s:
        return "Thin Crust"
    if "york" in s or "ny" in s or "new york" in s:
        return "New York Style Crust"
    if "garlic" in s:
        return "Garlic Epic Stuffed Crust"
    if "stuff" in s and "garlic" not in s:
        return "Epic Stuffed Crust"
    if "orig" in s:
        return "Original Crust"
    return best


async def _select_option_in_any_select(page: Page, option_text: str, *, prefer_numeric: bool = False, prefer_keywords: List[str] | None = None) -> bool:
    """Scan all <select> elements and choose the option by visible label or value.

    prefer_numeric: if True, prefer selects whose options look numeric (for quantity).
    prefer_keywords: list of hint keywords like ['crust'] to favor certain selects.
    """
    prefer_keywords = [k.lower() for k in (prefer_keywords or [])]
    selects = await page.locator("select").all()
    best_match = None
    best_rank = -1
    option_text_l = option_text.lower()
    for sel in selects:
        try:
            # quick visibility check
            try:
                vis = await sel.is_visible()
                if not vis:
                    continue
            except Exception:
                pass
            aria = (await sel.get_attribute("aria-label") or "").lower()
            name_attr = (await sel.get_attribute("name") or "").lower()
            id_attr = (await sel.get_attribute("id") or "").lower()
            opts = await sel.locator("option").all()
            texts = []
            values = []
            numeric_count = 0
            for o in opts:
                t = (await o.text_content() or "").strip()
                v = (await o.get_attribute("value") or "").strip()
                texts.append(t)
                values.append(v)
                if t.isdigit():
                    numeric_count += 1
            if prefer_numeric and numeric_count < 2:
                # likely not a quantity select
                continue
            rank = 0
            if prefer_keywords and any(k in aria or k in name_attr or k in id_attr for k in prefer_keywords):
                rank += 2
            # Also boost if any option looks like crust labels
            if not prefer_numeric and any("crust" in (t or "").lower() for t in texts):
                rank += 1
            # Check if the desired option exists
            found_label = None
            for t in texts:
                if option_text_l in (t or "").lower():
                    found_label = t
                    break
            if found_label is None:
                for v in values:
                    if option_text_l in (v or "").lower():
                        found_label = v
                        break
            if found_label is None:
                continue
            if rank > best_rank:
                best_rank = rank
                best_match = (sel, found_label)
        except Exception:
            continue
    if best_match:
        sel, label = best_match
        try:
            await sel.select_option(label=label)
        except Exception:
            try:
                await sel.select_option(value=label)
            except Exception:
                return False
        return True
    return False


async def _open_combobox_and_pick(page: Page, keywords: List[str], value_text: str) -> bool:
    """Open a combobox/button dropdown by keywords and click an option by text."""
    kws = [k.lower() for k in keywords]
    # Find a combobox or button with accessible name containing keyword
    candidates = [
        "role=combobox",
        "role=button",
        "button",
        "[role='listbox']",
    ]
    for sel in candidates:
        try:
            items = await page.locator(sel).all()
        except Exception:
            continue
        for it in items:
            try:
                name = ((await it.get_attribute("aria-label")) or "")
                if not name:
                    try:
                        name = (await it.inner_text()) or ""
                    except Exception:
                        name = ""
                if not any(k in name.lower() for k in kws):
                    continue
                try:
                    await it.click()
                except Exception:
                    continue
                # Try clicking the option in the now-open list
                try:
                    opt = page.get_by_role("option", name=lambda n: n and value_text.lower() in n.lower())
                    await opt.click()
                    return True
                except Exception:
                    try:
                        await page.get_by_text(value_text, exact=False).click()
                        return True
                    except Exception:
                        pass
            except Exception:
                continue
    return False


async def _scroll_to_most_popular(page: Page) -> None:
    """Scroll to the 'Most Popular' section if visible to increase card match reliability."""
    try:
        sec = page.get_by_text("Most Popular", exact=False)
        await sec.scroll_into_view_if_needed()
        await asyncio.sleep(0.3)
    except Exception:
        pass


async def _open_pizza_details(page: Page, name_tokens: List[str], timeout_ms: int = 10000) -> None:
    """Best-effort open of a pizza's Details/Customize by matching tokens on the card.

    Prefers cards under 'Most Popular' but will fall back to broader search and text clicks.
    """
    await _scroll_to_most_popular(page)
    deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000.0)
    last_error = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            # Search containers likely holding product cards
            containers = [
                "xpath=//section[.//h2[contains(translate(., 'MOSTPOPULAR','mostpopular'),'most popular')]]",
                "xpath=//section//*[contains(translate(., 'MOSTPOPULAR','mostpopular'),'most popular')]/ancestor::section[1]",
                "xpath=//main",
                "xpath=//body",
            ]
            for cont_sel in containers:
                container = page.locator(cont_sel)
                cards = container.locator("xpath=.//article | .//div[contains(@class,'card')] | .//li[contains(@class,'card')]")
                count = await cards.count()
                for i in range(min(count, 100)):
                    c = cards.nth(i)
                    try:
                        txt = (await c.inner_text()).lower()
                    except Exception:
                        continue
                    if all(tok.lower() in txt for tok in name_tokens):
                        try:
                            await c.scroll_into_view_if_needed()
                        except Exception:
                            pass
                        # Try to click a Details/Customize-like control within the card
                        detail_try = [
                            "role=button[name=/details|customize|view/i]",
                            "button:has-text('Details')",
                            "button:has-text('Customize')",
                            "a:has-text('Details')",
                            "a:has-text('Customize')",
                        ]
                        clicked = False
                        for sel in detail_try:
                            try:
                                btn = c.locator(sel).first
                                await btn.wait_for(state="visible", timeout=1200)
                                # ensure enabled if possible
                                try:
                                    if hasattr(btn, "is_enabled") and not await btn.is_enabled():
                                        continue
                                except Exception:
                                    pass
                                await btn.click()
                                clicked = True
                                break
                            except Exception as e:
                                last_error = e
                                continue
                        if clicked:
                            return
                        # Fallback: click the title or the card itself
                        try:
                            title = c.locator("xpath=.//h1|.//h2|.//h3|.//h4").first
                            await title.click()
                            return
                        except Exception:
                            try:
                                await c.click()
                                return
                            except Exception as e:
                                last_error = e
                                continue
            # Scroll and retry
            try:
                await page.mouse.wheel(0, 600)
            except Exception:
                pass
            await asyncio.sleep(0.4)
        except Exception as e:
            last_error = e
            await asyncio.sleep(0.3)
    # Final fallback: click by text anywhere
    try:
        await page.get_by_text(name_tokens[0], exact=False).click()
    except Exception:
        if last_error:
            # Swallow but keep behavior moving forward
            pass


async def handle_order_pizza(params: Dict[str, Any]):
    print("🍕 Pizza ordering Assistant - Papa John's")
    print("Type 'cancel' anytime to abort.")

    def _prompt(msg: str) -> str:
        try:
            return input(msg).strip()
        except KeyboardInterrupt:
            print("\n❌ Cancelled.")
            raise SystemExit(1)

    # 1) Service selection — always Delivery (no prompt)
    service = "Delivery"

    # 2) Address
    street = _prompt("Street Address: ")
    if street.lower() in {"cancel", "quit", "exit"}:
        print("Cancelled.")
        return
    zip_code = _prompt("ZIP Code: ")
    if zip_code.lower() in {"cancel", "quit", "exit"}:
        print("Cancelled.")
        return

    # Launch or reuse a persistent browser page
    page = await _ensure_browser(headless=False)
    if True:

        # Open homepage
        await page.goto("https://www.papajohns.com/", wait_until="domcontentloaded")

        # Accept cookies
        await _click_if_present(
            page,
            [
                "button:has-text('Accept All')",
                "button:has-text('Accept all')",
                "button:has-text('Accept Cookies')",
                "[data-testid*='accept']",
            ],
            timeout=4000,
        )

        # Click Start Your Order
        clicked = await _click_if_present(
            page,
            [
                r"role=button[name=/start\s*your\s*order/i]",
                "button:has-text('Start Your Order')",
                "a:has-text('Start Your Order')",
                "[data-testid*='start-your-order']",
            ],
            timeout=6000,
        )
        if not clicked:
            # Try scanning all buttons/links for the text
            try:
                await page.get_by_text("Start Your Order", exact=False).click()
            except Exception:
                pass

        # Choose service
        if service == "Delivery":
            await _click_if_present(page, ["button:has-text('Delivery')", "[data-testid*='Delivery']"])  # lenient
        else:
            await _click_if_present(page, ["button:has-text('Carryout')", "button:has-text('Pickup')", "[data-testid*='Carryout']"])  # lenient

        # Fill address (Delivery): type street, pick first suggestion, then add ZIP
        await asyncio.sleep(0.5)
        await _fill_if_present(
            page,
            [
                "input[aria-label*='Street' i]",
                "input[name*='street' i]",
                "input[placeholder*='Street' i]",
                "label:has-text('Street') >> .. >> input",
            ],
            street,
        )
        # Try to pick the first address suggestion from the dropdown
        try:
            await _pick_first_address_suggestion(page)
        except Exception:
            pass
        await _fill_if_present(
            page,
            [
                "input[aria-label*='ZIP' i]",
                "input[aria-label*='Postal' i]",
                "input[name*='zip' i]",
                "input[name*='postal' i]",
                "input[placeholder*='ZIP' i]",
            ],
            zip_code,
        )

        # Submit location
        submitted = await _click_if_present(
            page,
            [
                "button:has-text('Submit')",
                "button:has-text('Search')",
                "button:has-text('Continue')",
                "button:has-text('Confirm Location')",
            ],
            timeout=8000,
        )
        if not submitted:
            # Sometimes pressing Enter in the last field might submit
            try:
                await page.keyboard.press("Enter")
                await asyncio.sleep(1)
            except Exception:
                pass

        # Delivery: If Store Closed, schedule plan-ahead; otherwise click Start Your Order to proceed
        closed_shown = False
        try:
            await page.wait_for_selector(r"text=/store\s*closed/i", timeout=4000)
            closed_shown = True
        except Exception:
            closed_shown = False
        if closed_shown:
            await _click_if_present(page, ["button:has-text('Schedule a plan ahead order')", "a:has-text('Schedule')"], timeout=4000)
            await _click_if_present(page, ["button:has-text('Save')", "button:has-text('Continue')"], timeout=4000)
            await _click_if_present(page, ["button:has-text('Start Your Order')"], timeout=6000)
        else:
            await _click_if_present(page, [
                r"role=button[name=/start\s*your\s*order/i]",
                "button:has-text('Start Your Order')",
                "a:has-text('Start Your Order')",
                "[data-testid*='start-your-order']",
            ], timeout=6000)

        # Go directly to selected pizza details URL for reliability
        print("Choose a pizza:")
        print("  1) Pepperoni Pizza")
        print("  2) Sausage Pizza")
        print("  3) Cheese Pizza")
        sel = _prompt("Selection [1]: ") or "1"
        try:
            idx = int(sel)
        except Exception:
            idx = 1
        idx = 1 if idx not in {1,2,3} else idx
        url_map = {
            1: "https://www.papajohns.com/order/menu/pizza/pepperoni-pizza",
            2: "https://www.papajohns.com/order/menu/pizza/sausage-pizza",
            3: "https://www.papajohns.com/order/menu/pizza/cheese-pizza",
        }
        pizza_url = url_map[idx]
        print(f"Opening: {pizza_url}")
        await page.goto(pizza_url, wait_until="domcontentloaded")
        # Wait for details UI to be ready
        try:
            await page.wait_for_selector("text=/size|crust/i, button:has-text('Add to Order'), button:has-text('Add to Cart')", timeout=12000)
        except Exception:
            pass

        # Ask for configuration options
        size = _prompt("Size (e.g., Small/Medium/Large/XL) [Large]: ") or "Large"
        crust = _prompt("Crust (Original/Garlic Epic Stuffed/Epic Stuffed/New York Style/Thin) [Original]: ") or "Original"
        qty_str = _prompt("Quantity [1]: ") or "1"
        try:
            qty = max(1, int(qty_str))
        except Exception:
            qty = 1

        # Configure drop-downs / selectors
        await asyncio.sleep(0.4)
        # First: select Size
        try:
            await _select_by_label(page, ["Size"], size)
        except Exception:
            await _choose_option_button(page, ["Size"], size)
        # Dismiss combo warning if size conflicts
        await _handle_unavailable_combo(page)

        # For Small/Medium, skip further customizations to avoid repeated 'combination not available'
        allow_custom = _is_large_or_above(size)
        if allow_custom:
            # Crust can be a select or a button/combobox; normalize and try both
            crust_norm = _normalize_crust(crust)
            crust_conflicted = False
            if not await _select_option_in_any_select(page, crust_norm, prefer_keywords=["crust"]):
                try:
                    await _select_by_label(page, ["Crust"], crust_norm)
                except Exception:
                    await _choose_option_button(page, ["Crust"], crust_norm)
                    if not await _open_combobox_and_pick(page, ["Crust"], crust_norm):
                        # last-ditch: try any select again without hints
                        await _select_option_in_any_select(page, crust_norm)
            # If the chosen crust caused a warning, skip further customizations
            if await _handle_unavailable_combo(page):
                crust_conflicted = True
            # No crust flavor selection to avoid issues
        else:
            print("Skipping crust customizations for Small/Medium size.")

        # Quantity
        qty_text = str(qty)
        # Quantity is often a dropdown without a label; try selects with numeric options first
        if not await _select_option_in_any_select(page, qty_text, prefer_numeric=True, prefer_keywords=["qty", "quantity"]):
            # Next, try a combobox/button near 'Qty' or 'Quantity'
            if not await _open_combobox_and_pick(page, ["Qty", "Quantity"], qty_text):
                # Fallback to '+' stepper clicks
                for _ in range(qty - 1):
                    clicked = await _click_if_present(page, ["button[aria-label*='increase' i]", "button:has-text('+')"], timeout=800)
                    if not clicked:
                        break

        # Handle 'combination not available' warning if it popped during selection
        await _handle_unavailable_combo(page)

        # Add to Order
        # Ensure Add button is enabled before clicking
        added = await _click_enabled(page, [
            r"role=button[name=/add\s*to\s*(order|cart)/i]",
            "button:has-text('Add to Order')",
            "button:has-text('Add to Cart')",
        ], timeout=8000)
        if not added:
            # Fallback: try selecting common defaults then click again
            try:
                await _choose_option_button(page, ["Size"], "Large")
            except Exception:
                pass
            try:
                await _choose_option_button(page, ["Crust"], "Original")
            except Exception:
                pass
            added = await _click_enabled(page, [
                r"role=button[name=/add\s*to\s*(order|cart)/i]",
                "button:has-text('Add to Order')",
                "button:has-text('Add to Cart')",
            ], timeout=6000)
            if not added:
                # Last attempt without checking enabled
                added = await _click_if_present(page, [
                    "button:has-text('Add to Order')",
                    "button:has-text('Add to Cart')",
                ], timeout=3000)
        # If a 'Combination is not available' modal appears after clicking Add, dismiss and retry once
        if await _handle_unavailable_combo(page):
            added = await _click_enabled(page, [
                r"role=button[name=/add\s*to\s*(order|cart)/i]",
                "button:has-text('Add to Order')",
                "button:has-text('Add to Cart')",
            ], timeout=6000)
        if not added:
            print("❌ Could not click Add to Order. Try choosing options directly in the browser.")
            await _leave_browser_open(page)
            return

        # Go directly to checkout to avoid modal identification issues
        await asyncio.sleep(0.6)
        await page.goto("https://www.papajohns.com/order/checkout", wait_until="domcontentloaded")
        print("🧾 Opened checkout directly. Complete remaining details in the browser.")

        # Keep the browser open until the user closes it manually
        await _leave_browser_open(page)
