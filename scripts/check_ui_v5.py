"""v0.5 isolated browser acceptance; never uses personal accounts or files."""
import hashlib
import os
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright, expect, Error as BrowserError

root=Path(__file__).resolve().parent.parent
fixture=Path(tempfile.mkdtemp(prefix='coursenest-v05-'))
origin='http://127.0.0.1:18769'
log=(fixture/'server.log').open('w')
process=subprocess.Popen([r'D:\node.js\node.exe' if os.name=='nt' and Path(r'D:\node.js\node.exe').exists() else 'node',str(root/'cloud/dev.mjs')],env={**os.environ,'PORT':'18769','COURSENEST_DB':str(fixture/'cloud.sqlite')},stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
try:
    client=httpx.Client(base_url=origin,trust_env=False)
    for _ in range(60):
        try:
            if client.get('/api/health').status_code==200:break
        except httpx.HTTPError:pass
        time.sleep(.2)
    else:raise RuntimeError('Local test server failed')
    client.post('/api/auth/register',json={'email':'v05@example.test','password':'12345678','invite':'NEST-LOCAL-04'}).raise_for_status()
    me=client.get('/api/me').json();client.headers['X-CSRF-Token']=me['csrf']
    with sqlite3.connect(fixture/'cloud.sqlite') as db:db.execute('INSERT INTO account_profiles VALUES (?,1,?)',(me['user']['id'],'trial'))
    code=client.post('/api/pairings',json={}).json()['code']
    device=client.post('/api/device/pair',json={'code':code,'name':'Acceptance PC'}).json()
    headers={'Authorization':'Bearer '+device['token']}
    client.post('/api/device/poll',headers=headers,json={'paused':True,'snapshot':{'version':'0.5.0','capabilities':['archive-v1'],'courses':[{'id':1,'name':'Computing','membership':'added','bound':True}],'groups':[],'materials':[]}}).raise_for_status()
    with sync_playwright() as engine:
        try:browser=engine.chromium.launch(headless=True,channel='chromium')
        except BrowserError:browser=engine.chromium.launch(headless=True,channel='msedge')
        context=browser.new_context(viewport={'width':1280,'height':900})
        context.add_cookies([{'name':'cn_session','value':client.cookies['cn_session'],'url':origin}])
        page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        # The single archive UI now uses the storage-independent local index.
        # Existing R2 upload/share behavior remains covered by cloud/test/v05.test.mjs.
        term=client.post('/api/v07/terms',json={'label':'2026 Fall'}).json()
        response=client.post('/api/v07/device/index',headers=headers,json={'term_id':term['id'],'course_id':1,'files':[{'source_key':'fixture','name':'lecture.txt','group_name':'Week 1','bytes':10,'sha':'a'*64,'available':True}],'notes':[]})
        response.raise_for_status()
        page.goto(origin+'/archives.html');expect(page.locator('p').filter(has_text='2026 Fall')).to_be_visible()
        page.get_by_role('button',name='查看内容',exact=True).click();expect(page.locator('.v07-dialog')).to_contain_text('lecture.txt')
        page.locator('.v07-dialog button').first.click()
        root.joinpath('.runtime').mkdir(exist_ok=True);page.screenshot(path=str(root/'.runtime/v05-archives.png'),full_page=True)
        page.goto(origin+'/account.html');expect(page.locator('#forgot')).to_be_hidden();expect(page.locator('#message')).to_contain_text('邮件服务暂未启用')
        page.goto(origin+'/download.html');expect(page.locator('#download')).to_contain_text('暂未开放下载')
        mobile=browser.new_context(user_agent='iPhone',viewport={'width':390,'height':844});mp=mobile.new_page();mp.goto(origin+'/download.html');expect(mp.locator('#local-link')).not_to_have_attribute('href','http://127.0.0.1:8765/')
        page.route('**/api/features',lambda route:route.fulfill(json={'archive_enabled':False,'mail_enabled':False,'qa_enabled':False}))
        page.goto(origin+'/archives.html');expect(page.get_by_role('heading',name='学期存档',exact=True)).to_be_visible();expect(page.get_by_text('原文件尚不支持云端下载和分享。',exact=False)).to_be_visible()
        # Hold session restoration to expose any first-frame login flash.
        for source,label in [('archives.html','返回官网'),('download.html','CourseNest 官网')]:
            page.goto(origin+'/'+source)
            pending=[]
            page.route('**/api/me',lambda route:pending.append(route))
            page.get_by_role('link',name=label,exact=True).click()
            expect(page.locator('#session-loading')).to_be_visible()
            expect(page.locator('#welcome')).to_be_hidden()
            expect(page.locator('#workspace')).to_be_hidden()
            page.wait_for_timeout(300)
            assert pending
            pending.pop().continue_()
            expect(page.locator('#workspace')).to_be_visible()
            expect(page.locator('#welcome')).to_be_hidden()
            expect(page.locator('#session-loading')).to_be_hidden()
            page.unroute('**/api/me')
        page.route('**/api/me',lambda route:route.abort())
        page.goto(origin+'/');expect(page.locator('#session-retry')).to_be_visible();expect(page.locator('#welcome')).to_be_hidden()
        page.unroute('**/api/me');page.locator('#session-retry').click();expect(page.locator('#workspace')).to_be_visible()
        # Internal feature navigation retains this document and unsaved form state.
        page.goto(origin+'/#/settings');expect(page.locator('#workspace')).to_be_visible()
        page.locator('#schedule-time').fill('19:42')
        page.evaluate("window.returnNavigationMarker = 'same-document'; window.scrollTo(0, 250)")
        original_scroll=page.evaluate('scrollY')
        for path,label in [('/archives.html','返回官网'),('/download.html','CourseNest 官网')]:
            page.locator('#sidebar a[href="'+path+'"]').click()
            expect(page.locator('#feature-view')).to_be_visible()
            expect(page.locator('#workspace')).to_be_hidden()
            page.locator('#feature-view').get_by_role('link',name=label,exact=True).click()
            expect(page).to_have_url(origin+'/#/settings')
            expect(page.locator('#workspace')).to_be_visible()
            expect(page.locator('#session-loading')).to_be_hidden()
            expect(page.locator('#welcome')).to_be_hidden()
            assert page.evaluate('window.returnNavigationMarker')=='same-document'
            expect(page.locator('#schedule-time')).to_have_value('19:42')
            assert abs(page.evaluate('scrollY')-original_scroll)<2
        page.locator('#sidebar a[href="/download.html"]').click()
        expect(page.locator('#feature-view')).to_be_visible()
        page.go_back();expect(page.locator('#workspace')).to_be_visible();expect(page).to_have_url(origin+'/#/settings')
        page.go_forward();expect(page.locator('#feature-view')).to_be_visible()
        page.locator('#feature-view').get_by_role('link',name='CourseNest 官网',exact=True).click()
        guest=browser.new_context();gp=guest.new_page();gp.goto(origin+'/');expect(gp.locator('#welcome')).to_be_visible();expect(gp.locator('#session-loading')).to_be_hidden()
        assert not errors,errors
        browser.close()
    print('PASS v0.5 browser: retained archive display, account page, same-document navigation, public download status, mobile setup guidance.')
finally:
    process.terminate();process.wait(timeout=10);log.close()
