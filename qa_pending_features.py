import os
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:9118"
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for width, height, name in ((1440, 900, "desktop"), (390, 844, "mobile")):
        page = browser.new_page(viewport={"width": width, "height": height})
        errors = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(BASE + "/login", wait_until="networkidle")
        page.locator('input[name="username"]').fill(os.environ["NUVULABS_USER"])
        page.locator('input[name="password"]').fill(os.environ["NUVULABS_PASSWORD"])
        page.locator("button.submit").click()
        page.wait_for_url(BASE + "/")

        if width < 700:
            page.locator(".mobile-top .menu").click()
        page.locator('[data-page="chat"]').click()
        page.locator("#chatExpand").click()
        assert page.locator("body").evaluate("e=>e.classList.contains('chat-fullscreen')")
        box = page.locator("#chatShell").bounding_box()
        assert box and box["height"] >= height - 22, box
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
        page.screenshot(path=f"/home/ubuntu/vps-audit/qa/chat-fullscreen-{name}.png")
        page.keyboard.press("Escape")
        assert not page.locator("body").evaluate("e=>e.classList.contains('chat-fullscreen')")

        if width < 700:
            page.locator(".mobile-top .menu").click()
        page.locator('[data-page="profiles"]').click()
        page.wait_for_selector(".profile-card")
        nondefault = page.locator('.profile-card .profile-card-actions .danger')
        if nondefault.count():
            assert nondefault.first.inner_text() == "Hapus"
        page.locator(".profile-card .profile-card-actions button").first.click()
        page.wait_for_selector("#profileWorkspace:not([hidden])")
        assert page.locator("#profileDocDelete").is_enabled()

        if width < 700:
            page.locator(".mobile-top .menu").click()
        page.locator('[data-page="gateway"]').click()
        page.wait_for_selector(".gateway-context-source", timeout=10000)
        source = page.locator(".gateway-context-source").inner_text()
        assert "state.db" in source and "provider" in source
        page.wait_for_timeout(5200)
        assert page.locator(".gateway-context-source").count() == 1
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
        assert not errors, errors
        page.close()
    browser.close()
print("pending features browser QA OK")
