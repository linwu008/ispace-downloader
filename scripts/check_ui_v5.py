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
        page.goto(origin+'/archives.html');page.locator('#semester').fill('2026 Fall');expect(page.locator('#course option')).to_have_count(1)
        page.get_by_role('button',name='创建档案').click();expect(page.locator('#archives')).to_contain_text('2026 Fall')
        archive=client.get('/api/archives').json()[0]['id'];data=b'Course concepts: functions and variables.'
        upload=client.post('/api/device/archive/prepare',headers=headers,json={'archive_id':archive,'source_key':'fixture','name':'lecture.txt','group_name':'Week 1','bytes':len(data),'sha':hashlib.sha256(data).hexdigest()}).json()['upload_id']
        client.put('/api/device/archive/upload/'+upload,headers=headers,content=data).raise_for_status()
        client.post('/api/device/archive/commit',headers=headers,json={'upload_id':upload,'parts':[{'locator':'正文','text':data.decode()}]}).raise_for_status()
        page.get_by_role('button',name='查看存档').click();expect(page.locator('#detail')).to_contain_text('lecture.txt')
        page.get_by_role('button',name='创建登录后可访问的链接').click();expect(page.locator('#detail input[readonly]')).to_be_visible()
        root.joinpath('.runtime').mkdir(exist_ok=True);page.screenshot(path=str(root/'.runtime/v05-archives.png'),full_page=True)
        page.goto(origin+'/account.html');expect(page.locator('#forgot')).to_be_hidden();expect(page.locator('#message')).to_contain_text('邮件服务暂未启用')
        page.goto(origin+'/download.html');expect(page.locator('#download')).to_contain_text('暂未开放下载')
        mobile=browser.new_context(user_agent='iPhone',viewport={'width':390,'height':844});mp=mobile.new_page();mp.goto(origin+'/download.html');expect(mp.locator('#local-link')).not_to_have_attribute('href','http://127.0.0.1:8765/')
        page.route('**/api/features',lambda route:route.fulfill(json={'archive_enabled':False,'mail_enabled':False,'qa_enabled':False}))
        page.goto(origin+'/archives.html');expect(page.locator('#create')).to_be_hidden();expect(page.locator('#message')).to_contain_text('暂未启用')
        assert not errors,errors
        browser.close()
    print('PASS v0.5 browser: archive creation/upload/display/share, account page, public download status, mobile setup guidance.')
finally:
    process.terminate();process.wait(timeout=10);log.close()
