import os, json
from pathlib import Path
from playwright.sync_api import sync_playwright

import subprocess
UNIT = subprocess.run(['sudo','systemctl','cat','vps-audit.service'],capture_output=True,text=True,check=True).stdout
ENV = subprocess.run(['sudo','cat','/home/ubuntu/vps-audit/data/labs.env'],capture_output=True,text=True,check=True).stdout
def env(name):
    for line in ENV.splitlines():
        if line.startswith(name+'='): return line.split('=',1)[1]
    raise KeyError(name)

pages=['overview','performance','services','security','storage','network','system','settings','backup','service-ctrl','activity','config','gateway','usage','quota','sessions','chat','logs','skills','memory','router']
with sync_playwright() as p:
  out={}
  for label,width,height in [('desktop',1440,900),('mobile',390,844)]:
    browser=p.chromium.launch(headless=True); page=browser.new_page(viewport={'width':width,'height':height})
    errors=[]; page.on('pageerror',lambda e: errors.append(str(e))); page.on('console',lambda m: errors.append(m.text) if m.type=='error' else None)
    page.goto('http://127.0.0.1:9118/login'); page.fill('input[name=username]',env('LABS_USER')); page.fill('input[name=password]',env('LABS_PASSWORD')); page.click('button[type=submit]'); page.wait_for_selector('#overview.active')
    page.evaluate("document.documentElement.dataset.theme='dark'")
    rows=[]
    for pid in pages:
      page.evaluate("id=>{document.querySelectorAll('.page').forEach(x=>x.classList.toggle('active',x.id===id))}",pid); page.wait_for_timeout(100)
      r=page.evaluate("""id=>{let root=document.getElementById(id),bad=[],els=[...root.querySelectorAll('h1,h2,h3,.card,.metric,.service,.network-kpi,.setting-choice-card,.backup-card')];function lum(c){let m=c.match(/\d+/g);if(!m)return 0;let [r,g,b]=m.map(Number).map(v=>{v/=255;return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4)});return .2126*r+.7152*g+.0722*b}for(let e of els){let s=getComputedStyle(e),fg=lum(s.color),bg=lum(s.backgroundColor);if(e.matches('h1,h2,h3')&&Math.abs(fg-bg)<.12)bad.push(e.textContent.trim().slice(0,40))}return{id,overflow:document.documentElement.scrollWidth-document.documentElement.clientWidth,badTitles:bad,visible:!!root.offsetParent}}""",pid)
      rows.append(r)
    page.screenshot(path=f'/home/ubuntu/vps-audit/qa-dark-{label}.png',full_page=False)
    out[label]={'pages':rows,'errors':errors}
    browser.close()
  print(json.dumps(out,indent=2))
