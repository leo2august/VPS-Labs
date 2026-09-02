from pathlib import Path
import os, re, subprocess
from playwright.sync_api import sync_playwright

service = subprocess.check_output(["systemctl", "show", "vps-audit.service", "-p", "Environment", "--value"], text=True)
def env(name):
    m = re.search(rf"(?:^|\s){name}=([^\s]+)", service)
    if not m: raise RuntimeError(f"missing {name}")
    return m.group(1).strip('"')

user, password = env("LABS_USER"), env("LABS_PASSWORD")
base = "http://127.0.0.1:9118"
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for width, height, out in [(1440, 1000, "qa-gateway-settings-desktop.png"), (390, 844, "qa-gateway-settings-mobile.png")]:
        page = browser.new_page(viewport={"width": width, "height": height})
        errors=[]
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base, wait_until="domcontentloaded")
        page.fill('input[name="username"]', user)
        page.fill('input[name="password"]', password)
        page.click('button[type="submit"]')
        page.wait_for_selector('#overview.active')
        config_nav = page.locator('.nav button[data-page="config"]')
        if width <= 600:
            page.click('.menu')
        config_nav.scroll_into_view_if_needed()
        config_nav.click()
        page.wait_for_selector('.gateway-route-card')
        assert "cx/gpt-5.6-sol" in page.locator('#gatewayRouteGrid').inner_text()
        assert "tamandata" in page.locator('#gatewayRouteGrid').inner_text().lower()
        if width > 600:
            page.screenshot(path=str(Path(__file__).parent/out), full_page=True)
        settings_nav = page.locator('.nav button[data-page="settings"]')
        if width <= 600:
            page.click('.menu')
        settings_nav.scroll_into_view_if_needed()
        settings_nav.click()
        page.wait_for_selector('#labSettingsGrid .setting-choice-card')
        page.click('.settings-tabs button[data-settings-tab="webui"]')
        page.wait_for_selector('#settingsGrid .setting-config-card')
        overflow = page.evaluate('document.documentElement.scrollWidth > document.documentElement.clientWidth')
        assert not overflow, f"horizontal overflow at {width}"
        assert not errors, errors
        if width <= 600:
            page.screenshot(path=str(Path(__file__).parent/out), full_page=True)
        page.close()
    browser.close()
print("gateway/settings browser QA OK: runtime route, settings tabs, no JS errors, no horizontal overflow")
