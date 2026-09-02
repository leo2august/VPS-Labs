import os
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:9118"
user = os.environ["LABS_USER"]
password = os.environ["LABS_PASSWORD"]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for width, height, name in ((1440, 900, "desktop"), (390, 844, "mobile")):
        page = browser.new_page(viewport={"width": width, "height": height})
        errors = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(BASE + "/login", wait_until="networkidle")
        page.locator('input[name="username"]').fill(user)
        page.locator('input[name="password"]').fill(password)
        page.locator('button.submit').click()
        page.wait_for_url(BASE + "/")
        if width < 700:
            page.locator(".mobile-top .menu").click()
        page.locator('[data-page="profiles"]').click()
        page.wait_for_selector(".profile-card")
        assert page.locator(".profile-card").count() >= 1
        assert page.locator("#profileGrid").evaluate("e => e.scrollWidth <= e.clientWidth + 1")
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
        page.screenshot(path=f"/home/ubuntu/vps-audit/qa-profiles-{name}.png", full_page=True)
        if width < 700:
            page.locator(".mobile-top .menu").click()
        page.locator('[data-page="chat"]').click()
        page.wait_for_selector("#chatProfile")
        assert page.locator("#chatProfile").input_value() == "default"
        assert page.locator("#chatProfile option").count() >= 1
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
        page.screenshot(path=f"/home/ubuntu/vps-audit/qa-chat-profile-{name}.png", full_page=True)
        assert not errors, errors
        page.close()
    browser.close()
print("profiles browser QA OK")
