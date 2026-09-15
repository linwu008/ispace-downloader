"""v0.6 guest isolation and helper folder setup acceptance."""
import os, shutil, subprocess, tempfile, time
from pathlib import Path
import httpx
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect, Error
from ispace.state import Store
from ispace.web import create_app

root=Path(__file__).resolve().parent.parent
fixture=Path(tempfile.mkdtemp(prefix='coursenest-v06-'))
origin='http://127.0.0.1:18770'
log=(fixture/'server.log').open('w')
process=subprocess.Popen([shutil.which('node') or r'D:\node.js\node.exe',str(root/'cloud/dev.mjs')],env={**os.environ,'PORT':'18770','COURSENEST_DB':str(fixture/'cloud.sqlite')},stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
store=Store(fixture/'state');store.refresh_courses([{'id':1,'name':'Computing'}])
app=create_app(store);client=TestClient(app,base_url="http://127.0.0.1:18771")
try:
 for _ in range(60):
  try:
   if httpx.get(origin+'/api/health',trust_env=False).status_code==200:break
  except httpx.HTTPError:pass
  time.sleep(.2)
 with sync_playwright() as engine:
  try:browser=engine.chromium.launch(headless=True,channel='chromium')
  except Error:browser=engine.chromium.launch(headless=True,channel='msedge')
  page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(origin);expect(page.locator('#guest-enter')).to_be_visible()
  page.locator('#guest-enter').click();expect(page.locator('#workspace')).to_be_visible()
  requests=[];page.on('request',lambda r:requests.append(r.url) if '/api/' in r.url else None)
  expect(page.locator('#demo-banner')).to_be_visible()
  page.locator('#sync-all').click()
  page.locator('nav a[href="#/history"]').click()
  expect(page.get_by_role('button',name='取消任务')).to_be_visible()
  page.get_by_role('button',name='取消任务').click()
  expect(page.locator('#job-list')).to_contain_text('已取消')
  page.locator('#setup-guide').click();expect(page.locator('#setup-dialog')).to_be_visible()
  page.locator('#demo-folder').fill('D:\\Demo')
  page.locator('#demo-folder-save').click();expect(page.locator('#demo-folder-note')).to_contain_text('模拟')
  page.locator('[data-close="setup-dialog"]').click()
  page.set_viewport_size({'width':390,'height':844})
  assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
  page.screenshot(path=str(root/'.runtime/v06-guest-mobile.png'),full_page=True)
  assert not requests,requests
  page.locator('#demo-exit').click();expect(page.locator('#welcome')).to_be_visible()
  def route_local(route):
   req=route.request;path=req.url.split('127.0.0.1:18771',1)[1]
   headers={k:v for k,v in req.headers.items() if k.lower() not in {'host','content-length'}}
   res=client.request(req.method,path,headers=headers,content=req.post_data_buffer)
   route.fulfill(status=res.status_code,headers=dict(res.headers),body=res.content)
  page.route('http://127.0.0.1:18771/**',route_local)
  page.goto('http://127.0.0.1:18771/');expect(page.locator('#folder-dialog')).to_be_visible()
  page.locator('#folder-kind').select_option('individual');expect(page.locator('#folder-path')).to_be_hidden()
  page.get_by_role('button',name='保存设置',exact=True).click();expect(page.locator('#folder-dialog')).not_to_be_visible()
  page.get_by_role('button',name='设置保存位置',exact=True).click()
  directory=fixture/'downloads';directory.mkdir()
  page.locator('#folder-path').fill(str(directory));page.locator('#check-folder').click()
  expect(page.locator('#folder-status')).to_contain_text('目录可用')
  page.get_by_role('button',name='保存设置',exact=True).click()
  expect(page.locator('#message')).to_contain_text('成功')
  assert store.courses()[0]['folder']==str(directory.resolve())
  page.screenshot(path=str(root/'.runtime/v06-helper.png'),full_page=True)
  assert not errors,errors
  browser.close()
 print('v0.6 browser acceptance passed; demo made zero API requests')
finally:
 client.close();app.state.service.pool.shutdown();process.terminate();process.wait(timeout=10);log.close()
