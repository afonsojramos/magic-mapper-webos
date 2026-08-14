from pathlib import Path

from playwright.sync_api import sync_playwright


SCREENSHOT = Path("/private/tmp/magic-mapper-dashboard.png")
CATALOG_SCREENSHOT = Path("/private/tmp/magic-mapper-action-catalog.png")


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        headless=True,
        executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    )
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.add_init_script("window.localStorage.clear();")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:8765/index.html")
    page.wait_for_load_state("networkidle")

    assert page.get_by_text("Running", exact=True).is_visible()
    assert page.locator(".mapping-row").count() == 3
    assert page.get_by_role("button", name="Add button").is_enabled()
    page.screenshot(path=str(SCREENSHOT), full_page=True)

    page.locator("[data-edit='alexa']").click()
    assert page.get_by_role("heading", name="Alexa").is_visible()
    assert page.get_by_text("Restore default action", exact=True).is_visible()
    page.evaluate("""
      window.dispatchEvent(new KeyboardEvent('keydown', {keyCode: 461, which: 461, bubbles: true}));
    """)
    assert page.locator("#modal").is_hidden()
    assert page.locator("[data-edit='alexa']").evaluate("element => document.activeElement === element")
    page.evaluate("""
      window.dispatchEvent(new KeyboardEvent('keydown', {keyCode: 461, which: 461, bubbles: true}));
      window.dispatchEvent(new KeyboardEvent('keyup', {keyCode: 461, which: 461, bubbles: true}));
    """)
    assert page.get_by_role("heading", name="Remote buttons").is_visible()

    page.locator("[data-edit='netflix']").click()
    page.get_by_text("Change action", exact=True).click()
    assert page.get_by_role("heading", name="What should it do?").is_visible()
    assert page.locator("[data-category]").count() == 4
    page.wait_for_function("document.activeElement && document.activeElement.dataset.category === 'common'")
    page.screenshot(path=str(CATALOG_SCREENSHOT), full_page=True)
    page.get_by_role("button", name="Picture & screen").click()
    assert page.get_by_role("heading", name="Picture & screen").is_visible()
    assert page.locator("[data-action-id]").count() == 8
    page.wait_for_function("document.activeElement && document.activeElement.dataset.actionId === 'reduce_oled_light'")
    page.get_by_role("button", name="Set OLED light").click()
    assert page.get_by_role("heading", name="Set OLED light").is_visible()
    value = page.locator("[data-field-name='backlight']")
    page.wait_for_function("document.activeElement && document.activeElement.dataset.fieldName === 'backlight'")
    assert value.get_attribute("data-field-value") == "50"
    value.focus()
    page.keyboard.press("ArrowRight")
    assert value.get_attribute("data-field-value") == "51"
    page.evaluate("""
      window.dispatchEvent(new KeyboardEvent('keydown', {keyCode: 461, which: 461, bubbles: true}));
    """)
    assert page.get_by_role("heading", name="Picture & screen").is_visible()
    page.wait_for_function("document.activeElement && document.activeElement.dataset.actionId === 'set_oled_backlight'")
    page.evaluate("window.dispatchEvent(new KeyboardEvent('keydown', {keyCode: 461, which: 461, bubbles: true}));")
    assert page.get_by_role("heading", name="What should it do?").is_visible()
    page.evaluate("window.dispatchEvent(new KeyboardEvent('keydown', {keyCode: 461, which: 461, bubbles: true}));")
    assert page.get_by_role("heading", name="Netflix").is_visible()
    page.evaluate("window.dispatchEvent(new KeyboardEvent('keydown', {keyCode: 461, which: 461, bubbles: true}));")
    assert page.locator("#modal").is_hidden()
    assert page.locator("[data-edit='netflix']").evaluate("element => document.activeElement === element")

    page.get_by_role("button", name="Add button").click()
    assert page.get_by_role("heading", name="Press one remote button").is_visible()
    page.wait_for_selector("[data-category='common']", timeout=5000)
    page.locator("[data-category='common']").click()
    page.locator("[data-action-id='disabled']").click()
    page.wait_for_selector(".mapping-row", timeout=3000)
    assert page.get_by_text("Rakuten TV", exact=True).is_visible()
    assert page.locator(".mapping-row").count() == 4

    page.get_by_role("button", name="Settings").click()
    assert page.get_by_role("heading", name="Settings").is_visible()
    page.get_by_role("button", name="Magic Remote pointer").click()
    assert page.get_by_role("heading", name="Disable the pointer?").is_visible()
    page.keyboard.press("Escape")
    assert page.get_by_role("heading", name="Settings").is_visible()
    assert page.get_by_text("Uninstall Magic Mapper", exact=True).is_visible()
    page.keyboard.press("Escape")
    assert page.locator("#modal").is_hidden()
    assert not errors, errors

    bridge_page = browser.new_page(viewport={"width": 1920, "height": 1080})
    bridge_page.add_init_script("""
      window.PalmServiceBridge = function () {
        this.call = () => setTimeout(() => this.onservicecallback(JSON.stringify({
          returnValue: true,
          stdoutString: JSON.stringify({ok: true, status: {
            active: true, installed: true, config: {netflix: "disabled"}
          }}),
          stderrString: ""
        })), 0);
      };
    """)
    bridge_page.goto("http://127.0.0.1:8765/index.html")
    bridge_page.wait_for_load_state("networkidle")
    assert bridge_page.get_by_text("Running", exact=True).is_visible()
    assert bridge_page.locator(".mapping-row").count() == 1
    browser.close()

print(SCREENSHOT)
