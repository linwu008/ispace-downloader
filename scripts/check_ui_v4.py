"""Real HTTP website/companion acceptance using isolated fake school files."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright, expect, Error as BrowserError

project = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project / 'tests'))
from test_v3 import Platform
from ispace import courses
from ispace.companion import Companion
from ispace.service import Service
from ispace.state import Store

fixture = Path(tempfile.mkdtemp(prefix='coursenest-v04-ui-'))
origin = 'http://127.0.0.1:18767'
node = shutil.which('node') or r'D:\node.js\node.exe'
env = {**os.environ, 'PORT':'18767', 'COURSENEST_DB':str(fixture/'cloud.sqlite3')}
log = (fixture/'server.log').open('w')
process = subprocess.Popen([node, str(project/'cloud/dev.mjs')], env=env, stdout=log, stderr=log,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
store = Store(fixture/'state')
school_courses = [{'id':1,'name':'计算导论 · Computing'}, {'id':2,'name':'微积分 · Calculus'}]
store.refresh_courses(school_courses)
courses.membership(store,[1],True)
root = fixture/'course'; root.mkdir(); store.bind(1,str(root),True)
platform = Platform(); platform.courses = lambda:school_courses
key = Fernet.generate_key()
service = Service(store,vault=SimpleNamespace(cipher=lambda:Fernet(key)))
service.authenticated = lambda:platform
companion = Companion(store,service); companion.start = lambda:None
output = project/'.runtime'; output.mkdir(exist_ok=True)

try:
    for _ in range(60):
        try:
            if httpx.get(origin+'/api/health',trust_env=False).status_code==200:break
        except httpx.HTTPError:time.sleep(.2)
    else:raise RuntimeError((fixture/'server.log').read_text())
    with sync_playwright() as engine:
        try:browser = engine.chromium.launch(headless=True,channel='chromium')
        except BrowserError:browser = engine.chromium.launch(headless=True,channel='msedge')
        page = browser.new_page(viewport={'width':1440,'height':1000})
        errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(origin+'/login.html',wait_until='networkidle')
        page.screenshot(path=str(output/'v04-login.png'),full_page=True)
        page.locator('#register-tab').click()
        page.locator('#email').fill('acceptance@example.test')
        page.locator('#password').fill('Acceptance-04-test')
        page.locator('#invite').fill('NEST-LOCAL-04')
        page.locator('#auth-submit').click()
        expect(page.locator('#workspace')).to_be_visible()
        expect(page.locator('#setup-dialog')).to_be_visible()
        page.locator('[data-close="setup-dialog"]').click()
        page.locator('nav a[href="#/devices"]').click()
        page.locator('#new-pair').click()
        expect(page.locator('#pair-code')).to_contain_text('分钟')
        code = page.locator('#pair-code').evaluate('(e)=>e.firstChild.textContent')
        companion.pair(origin,code,'验收用 Windows 电脑'); companion.tick()
        page.reload(wait_until='networkidle')
        expect(page.locator('#device-detail')).to_contain_text('验收用 Windows 电脑')
        page.screenshot(path=str(output/'v04-device.png'),full_page=True)
        page.locator('nav a[href="#/courses"]').click()
        expect(page.locator('.course-card')).to_have_count(1)
        page.locator('.course-card').click()
        page.locator('#read-catalog').click()
        expect(page.locator('#notice')).to_contain_text('任务')
        companion.tick()
        expect(page.locator('.file-row')).to_have_count(2,timeout=15000)
        assert platform.downloads==0 and not list(root.rglob('*.*'))
        page.get_by_label('diagram.png',exact=True).check()
        page.get_by_label('notes.txt',exact=True).uncheck()
        page.wait_for_timeout(5500)
        expect(page.get_by_label('notes.txt',exact=True)).not_to_be_checked()
        page.reload(wait_until='networkidle'); page.locator('.course-card').click()
        expect(page.get_by_label('diagram.png',exact=True)).to_be_checked()
        expect(page.get_by_label('notes.txt',exact=True)).not_to_be_checked()
        page.locator('#download-choice').click()
        expect(page.locator('#selection-count')).to_contain_text('已提交')
        companion.tick()
        expect(page.locator("#download-confirmation")).to_be_visible(timeout=15000)
        page.get_by_role("button",name="开始执行",exact=True).click()
        companion.tick()
        page.locator(".course-card").click()
        expect(page.locator('.file-row').filter(has_text='diagram.png')).to_contain_text('已下载',timeout=15000)
        assert len(list(root.rglob('*.png')))==1 and not list(root.rglob('*.txt'))
        page.screenshot(path=str(output/'v04-course.png'))
        page.locator('.file-row').filter(has_text='diagram.png').get_by_role('button',name='查看位置').click()
        expect(page.locator('#location-body')).to_contain_text(str(root.resolve()))
        page.locator('[data-close="location-dialog"]').click()
        page.locator('#download-choice').click()
        expect(page.locator('#selection-count')).to_contain_text('已提交')
        companion.tick()
        expect(page.locator("#download-confirmation")).to_be_visible(timeout=15000)
        page.get_by_role("button",name="开始执行",exact=True).click()
        companion.tick()
        page.locator(".course-card").click()
        assert len(list(root.rglob('*.png')))==1 and platform.downloads==1
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(output/'v04-mobile.png'))
        page.locator('[data-close="course-dialog"]').click()
        page.set_viewport_size({'width':1440,'height':1000})
        page.locator('#add-courses').click()
        page.get_by_label('微积分 · Calculus',exact=True).check()
        page.locator('#confirm-add').click(); expect(page.locator('#add-dialog')).not_to_be_visible()
        companion.tick()
        expect(page.locator('.course-card')).to_have_count(2,timeout=15000)
        page.locator('.course-card[data-course-id="2"]').click()
        expect(page.locator('#course-note')).to_contain_text('授权目录')
        page.once('dialog',lambda dialog:dialog.accept());page.locator('#remove-course').click()
        expect(page.locator('#course-dialog')).not_to_be_visible();companion.tick()
        expect(page.locator('.course-card')).to_have_count(1,timeout=15000)
        assert len(list(root.rglob('*.png')))==1
        page.locator('nav a[href="#/history"]').click()
        expect(page.locator('#job-list')).to_contain_text('计算导论')
        page.reload(wait_until='networkidle');expect(page.locator('[data-page="history"]')).to_be_visible()
        page.locator('nav a[href="#/overview"]').click()
        page.screenshot(path=str(output/'v04-overview.png'),full_page=True)
        page.go_back();expect(page.locator('[data-page="history"]')).to_be_visible()
        assert not errors,errors
        browser.close()
    print('PASS v0.4: registration, pairing, catalog-only, persistent selection, queued sync, dedup, course membership, location, routing, mobile. No real school files touched.')
finally:
    companion.stop();service.pool.shutdown();process.terminate();process.wait(timeout=15);log.close()
