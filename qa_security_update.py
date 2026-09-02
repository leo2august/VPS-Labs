import os, re
from playwright.sync_api import sync_playwright

unit = os.popen("sudo systemctl cat vps-audit.service").read()
def env(name):
    m = re.search(rf'Environment={name}=(?:"([^"]*)"|(\S+))', unit)
    if m: return m.group(1) or m.group(2)
    text=os.popen("sudo cat /home/ubuntu/vps-audit/data/labs.env").read()
    m=re.search(rf'^{name}=(.*)$',text,re.M)
    return (m.group(1).strip().strip('"').strip("'") if m else '')

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
        if label == 'mobile': page.click('.menu')
        page.click('[data-page=security]')
        page.wait_for_selector('#security.active')
        page.wait_for_timeout(500)
        sec_overflow=page.evaluate('document.documentElement.scrollWidth > document.documentElement.clientWidth')
        latest=page.locator('#malwareState').inner_text()
        if label == 'mobile': page.click('.menu')
        page.click('[data-page=update]')
        page.wait_for_selector('#update.active')
        page.wait_for_function("document.querySelector('#updDisk').textContent !== '—'")
        upd_overflow=page.evaluate('document.documentElement.scrollWidth > document.documentElement.clientWidth')
        readiness=page.locator('#updReadiness').inner_text()
        print(label,'security_overflow='+str(sec_overflow),'update_overflow='+str(upd_overflow),'scan='+latest,'readiness='+readiness,'errors='+str(errors))
        page.screenshot(path=f'/tmp/labs-{label}-security.png', full_page=True)
        page.close()
    browser.close()
