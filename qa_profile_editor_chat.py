import os
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:9118"
USER = os.environ["NUVULABS_USER"]
PASSWORD = os.environ["NUVULABS_PASSWORD"]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for width, height, name in ((1440, 900, "desktop"), (390, 844, "mobile")):
        page = browser.new_page(viewport={"width": width, "height": height})
        errors = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(BASE + "/login", wait_until="networkidle")
        page.locator('input[name="username"]').fill(USER)
        page.locator('input[name="password"]').fill(PASSWORD)
        page.locator("button.submit").click()
        page.wait_for_url(BASE + "/")

        if width < 700:
            page.locator(".mobile-top .menu").click()
        page.locator('[data-page="profiles"]').click()
        page.wait_for_selector(".profile-card")
        page.locator(".profile-card .profile-card-actions button").first.click()
        page.wait_for_selector("#profileWorkspace:not([hidden])")
        assert page.locator("#profileFileList button").count() >= 4
        assert page.locator("#profileDocEditor").is_enabled()
        assert len(page.locator("#profileDocEditor").input_value()) > 0
        page.locator("#profileFileSearch").fill("hermes-agent")
        assert page.locator('#profileFileList button[data-kind="skill"]:visible').count() >= 1
        page.screenshot(path=f"/home/ubuntu/vps-audit/qa-profile-editor-{name}.png", full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")

        page.locator("#profileFileSearch").fill("")
        page.locator("#profileWorkspace .profile-work-head .btn").click()
        if width < 700:
            page.locator(".mobile-top .menu").click()
        page.locator('[data-page="chat"]').click()
        page.wait_for_selector("#chatInput")
        assert page.locator("#chatInput").get_attribute("maxlength") == "30000"
        page.locator("#chatInput").fill("x" * 5000)
        assert "5.000 / 30.000" in page.locator("#chatChars").inner_text()
        page.locator("#chatInput").fill("")
        page.get_by_role("button", name="Table").click()
        assert "| Kolom 1 | Kolom 2 |" in page.locator("#chatInput").input_value()
        rendered = page.evaluate("mdToHtml('| A | B |\\n|---|---|\\n| 1 | 2 |')")
        assert "<table>" in rendered and "<td>1</td>" in rendered
        page.screenshot(path=f"/home/ubuntu/vps-audit/qa-chat-rich-{name}.png", full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
        assert not errors, errors
        page.close()
    browser.close()
print("profile editor + rich chat browser QA OK")
