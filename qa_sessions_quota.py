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
        errors=[]; console=[]
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('console', lambda m: console.append(m.text) if m.type == 'error' else None)
        page.goto('http://127.0.0.1:9118/login')
        page.fill('input[name=username]', env('NUVULABS_USER'))
        page.fill('input[name=password]', env('NUVULABS_PASSWORD'))
        page.click('button[type=submit]')
        page.wait_for_selector('#overview.active')
        if label == 'mobile': page.click('.menu')
        page.click('[data-page=sessions]')
        page.wait_for_selector('.telegram-card')
        cards=page.locator('.telegram-card').count()
        online=page.locator('#sessOnline').inner_text()
        page.locator('.telegram-card').first.click()
        page.wait_for_selector('#sessModal.show .chat-row')
        bubbles=page.locator('#sessModal .chat-row').count()
        page.screenshot(path=f'/home/ubuntu/vps-audit/qa-session-{label}.png', full_page=False)
        page.click('#sessModal .modal-head .btn')
        if label == 'mobile': page.click('.menu')
        page.click('[data-page=quota]')
        page.wait_for_selector('.quota-account')
        accounts=page.locator('.quota-account').count()
        page.screenshot(path=f'/home/ubuntu/vps-audit/qa-quota-{label}.png', full_page=False)
        overflow=page.evaluate('document.documentElement.scrollWidth > document.documentElement.clientWidth')
        print(label, f'cards={cards}', f'online={online}', f'bubbles={bubbles}', f'accounts={accounts}', f'overflow={overflow}', f'errors={errors}', f'console={console}')
        page.close()
    browser.close()
