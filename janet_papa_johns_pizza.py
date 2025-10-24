import asyncio
from typing import Dict, Any, List

from playwright.async_api import async_playwright, Page


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
    print("🍕 Papa John’s web assistant — guided flow")
    print("Type 'cancel' anytime to abort.")

    def _prompt(msg: str) -> str:
        try:
            return input(msg).strip()
        except KeyboardInterrupt:
            print("\n❌ Cancelled.")
            raise SystemExit(1)

    # 1) Service selection
    service = (_prompt("Service (Delivery/Carryout) [Delivery]: ") or "Delivery").title()
    if service not in {"Delivery", "Carryout"}:
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

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

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
                "role=button[name=/start\s*your\s*order/i]",
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

        # Fill address
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
            timeout=6000,
        )
        if not submitted:
            # Sometimes pressing Enter in ZIP might submit
            try:
                await page.keyboard.press("Enter")
                await asyncio.sleep(1)
            except Exception:
                pass

        # If Store Closed, schedule plan-ahead
        try:
            await page.wait_for_selector("text=/store\s*closed/i", timeout=4000)
            # found store closed message
            await _click_if_present(page, ["button:has-text('Schedule a plan ahead order')", "a:has-text('Schedule')"], timeout=4000)
            await _click_if_present(page, ["button:has-text('Save')", "button:has-text('Continue')"], timeout=4000)
            await _click_if_present(page, ["button:has-text('Start Your Order')"], timeout=4000)
        except Exception:
            pass

        # Wait for menu; then offer fixed choices for simplicity
        try:
            await page.wait_for_selector("text=/menu|most\s*popular/i", timeout=12000)
        except Exception:
            pass

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
        token_map = {1: ["pepperoni"], 2: ["sausage"], 3: ["cheese"]}
        name_tokens = token_map[idx]
        print(f"Opening details for: {' '.join(name_tokens).title()} ...")
        await _open_pizza_details(page, name_tokens)
        # Wait for a details context to be present (Size/Crust or Add button)
        try:
            await page.wait_for_selector("text=/size|crust/i, button:has-text('Add to Order'), button:has-text('Add to Cart')", timeout=8000)
        except Exception:
            # One more attempt to click any visible Details/Customize button
            clicked = await _click_enabled(page, [
                "button:has-text('Details')",
                "a:has-text('Details')",
                "button:has-text('Customize')",
            ], timeout=3000)
            if not clicked:
                print("Couldn’t reliably open the details automatically. If it’s visible, please click Details now; I’ll continue.")
                try:
                    await page.wait_for_timeout(4000)
                except Exception:
                    pass

        # Ask for configuration options
        size = _prompt("Size (e.g., Small/Medium/Large/XL) [Large]: ") or "Large"
        crust = _prompt("Crust (e.g., Original, Thin) [Original]: ") or "Original"
        flavor = _prompt("Crust Flavor (e.g., Garlic Parmesan, None) [None]: ") or "None"
        qty_str = _prompt("Quantity [1]: ") or "1"
        try:
            qty = max(1, int(qty_str))
        except Exception:
            qty = 1

        # Configure drop-downs / selectors
        await asyncio.sleep(0.4)
        # Try multiple strategies; skip silently if disabled/not available
        try:
            await _select_by_label(page, ["Size"], size)
        except Exception:
            await _choose_option_button(page, ["Size"], size)
        try:
            await _select_by_label(page, ["Crust"], crust)
        except Exception:
            await _choose_option_button(page, ["Crust"], crust)
        if flavor.lower() != "none":
            try:
                await _select_by_label(page, ["Crust Flavour", "Crust Flavor", "Flavor"], flavor)
            except Exception:
                await _choose_option_button(page, ["Crust Flavour", "Crust Flavor", "Flavor"], flavor)

        # Quantity
        try:
            qty_input = page.get_by_label("Quantity", exact=False)
            await qty_input.fill(str(qty))
        except Exception:
            # try stepper buttons
            for _ in range(qty - 1):
                clicked = await _click_if_present(page, ["button[aria-label*='increase' i]", "button:has-text('+')"], timeout=800)
                if not clicked:
                    break

        # Add to Order
        # Ensure Add button is enabled before clicking
        added = await _click_enabled(page, [
            "role=button[name=/add\s*to\s*(order|cart)/i]",
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
                "role=button[name=/add\s*to\s*(order|cart)/i]",
                "button:has-text('Add to Order')",
                "button:has-text('Add to Cart')",
            ], timeout=6000)
            if not added:
                # Last attempt without checking enabled
                added = await _click_if_present(page, [
                    "button:has-text('Add to Order')",
                    "button:has-text('Add to Cart')",
                ], timeout=3000)
        if not added:
            print("❌ Could not click Add to Order. Try choosing options directly in the browser.")
            await browser.close()
            return

        # Dismiss popup modal by clicking outside or ESC
        await asyncio.sleep(0.8)
        try:
            await page.keyboard.press("Escape")
        except Exception:
            try:
                await page.mouse.click(10, 10)
            except Exception:
                pass

        # Checkout
        await asyncio.sleep(0.6)
        went_checkout = await _click_if_present(page, ["button:has-text('Checkout')", "a:has-text('Checkout')", "[aria-label*='Checkout']"], timeout=6000)
        if went_checkout:
            print("🧾 Reached checkout. Complete remaining details in the browser.")
            # Keep browser open for user to interact
            try:
                await page.wait_for_timeout(10000)
            except Exception:
                pass
        else:
            print("⚠️ Could not navigate to checkout automatically. Please use the cart icon in the browser.")

        # Do not close so user can complete checkout manually
        # await browser.close()
