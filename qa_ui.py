import os, re
from playwright.sync_api import sync_playwright

unit = os.popen("sudo systemctl cat vps-audit.service").read()
def env(name):
    m = re.search(rf'Environment={name}=(?:"([^"]*)"|(\S+))', unit)
    return (m.group(1) or m.group(2)) if m else ''

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for width, height, label in [(1440, 900, 'desktop'), (390, 844, 'mobile')]:
        page = browser.new_page(viewport={'width': width, 'height': height})
        errors=[]
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto('http://127.0.0.1:9118/login')
        page.fill('input[name=username]', env('NUVULABS_USER'))
        page.fill('input[name=password]', env('NUVULABS_PASSWORD'))
        page.click('button[type=submit]')
        page.wait_for_selector('#overview.active')
        if label == 'mobile':
            page.click('.menu')
        page.click('[data-page=storage]')
        page.wait_for_selector('#cacheSize:not(:has-text("Menghitung"))')
        page.click('.menu') if label == 'mobile' else None
        page.click('[data-page=config]')
        page.wait_for_selector('.prov-card')
        page.locator('.prov-card .pc-actions button').first.click()
        page.wait_for_selector('#editModal.show')
        page.click('#editModal .edit-head .btn')
        page.click('.cfg-model-card .btn')
        page.wait_for_selector('#router.active')
        page.fill('#modelSearch','deepseek')
        page.wait_for_timeout(200)
        result=page.locator('#modelResultCount').inner_text()
        overflow=page.evaluate('document.documentElement.scrollWidth > document.documentElement.clientWidth')
        clock=page.locator('#clockTime').inner_text()+'.'+page.locator('#clockSec').inner_text()
        print(label, 'overflow='+str(overflow), 'errors='+str(errors), 'search='+result, 'clock='+clock)
        page.close()
    browser.close()
