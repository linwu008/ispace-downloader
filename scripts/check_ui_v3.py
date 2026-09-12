"""Isolated v0.3 browser acceptance: no real account or course files touched."""
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect, Error as BrowserError

project = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project / 'tests'))
from test_v3 import Platform
from ispace import courses, catalog
from ispace.state import Store
from ispace.web import create_app

fixture = Path(tempfile.mkdtemp(prefix='ispace-v03-ui-'))
store = Store(fixture/'state')
store.refresh_courses([{'id':1,'name':'信息技术 · Introduction to Computing'},{'id':2,'name':'微积分 · Calculus'}])
courses.membership(store,[1],True)
root=fixture/'course';root.mkdir();store.bind(1,str(root),True)
platform=Platform()
platform.courses=lambda:[{'id':1,'name':'信息技术 · Introduction to Computing'},{'id':2,'name':'微积分 · Calculus'}]
courses.selection(store,1,[],mode='all')
courses.discover(store,platform,1)
app=create_app(store)
app.state.service.authenticated=lambda:platform
client=TestClient(app,base_url='http://127.0.0.1:18765')
project.joinpath('.runtime').mkdir(exist_ok=True)

with sync_playwright() as engine:
    try:
        browser=engine.chromium.launch(headless=True,channel="chromium")
    except BrowserError:
        browser=engine.chromium.launch(headless=True,channel="msedge")
    page=browser.new_page(viewport={'width':1440,'height':1000})
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    def route_request(route):
        request=route.request
        path=request.url.split('127.0.0.1:18765',1)[1]
        headers={k:v for k,v in request.headers.items() if k.lower() not in {'host','content-length'}}
        response=client.request(request.method,path,headers=headers,content=request.post_data_buffer)
        route.fulfill(status=response.status_code,headers=dict(response.headers),body=response.content)
    page.route('http://127.0.0.1:18765/**',route_request)
    page.goto('http://127.0.0.1:18765/#/courses',wait_until='networkidle')
    expect(page.locator('.page-view:visible')).to_have_count(1)
    expect(page.locator('.course-card')).to_have_count(1)
    page.screenshot(path=str(project/'.runtime/v03-courses.png'),full_page=True)
    page.locator('#add-course').click()
    page.get_by_label('微积分 · Calculus',exact=False).check()
    page.locator('#confirm-add').click()
    expect(page.locator('.course-card')).to_have_count(2)
    page.locator('.course-card[data-course-id="2"]').click()
    expect(page.locator('.resource-row')).to_have_count(2,timeout=30000)
    assert not courses.get(store,2)['folder'] and platform.downloads==0
    page.locator('[data-close="course-dialog"]').click()
    page.locator('.course-card[data-course-id="1"]').click()
    expect(page.locator('.resource-row')).to_have_count(2)
    page.get_by_label('notes.txt',exact=True).uncheck()
    expect(page.locator('#course-mode')).to_have_value('selected')
    page.wait_for_timeout(4500)
    expect(page.get_by_label('notes.txt',exact=True)).not_to_be_checked()
    page.reload(wait_until='networkidle')
    expect(page.locator('#courses')).to_be_visible()
    page.locator('.course-card[data-course-id="1"]').click()
    expect(page.get_by_label('notes.txt',exact=True)).not_to_be_checked()
    page.locator('#save-selection').click()
    expect(page.locator('#selection-note')).to_contain_text('等待你选择')
    page.locator('.resource-row').first.get_by_role('button',name='预览',exact=True).click()
    expect(page.locator('#file-preview-body img')).to_be_visible(timeout=30000)
    assert page.locator('#file-preview-body img').evaluate('(image)=>image.complete&&image.naturalWidth>0')
    expect(page.locator('#file-preview-note')).to_contain_text('临时预览')
    assert not list(root.rglob('*.png')) and platform.downloads==1
    page.screenshot(path=str(project/'.runtime/v03-preview.png'))
    page.locator('[data-close="file-preview-dialog"]').click()
    page.locator('#download-selected').click()
    expect(page.locator('.resource-row').first.locator('.badge')).to_have_text('已下载',timeout=30000)
    assert len(list(root.rglob('*.png')))==1 and not list(root.rglob('*.txt')) and platform.downloads==1
    page.screenshot(path=str(project/'.runtime/v03-course-dialog.png'))
    # A minimal valid PDF exercises the browser's actual PDF viewer.
    from ispace.moodle import Resource
    from ispace.sync import digest
    pdf_objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>', b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 500 600] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    stream = b'BT /F1 24 Tf 60 500 Td (iSpace PDF preview works) Tj ET'
    pdf_objects.append(b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream')
    pdf=b'%PDF-1.4\n';offsets=[0]
    for i,obj in enumerate(pdf_objects,1):
        offsets.append(len(pdf));pdf+=f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n'
    xref=len(pdf);pdf+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])+f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
    document=root/'preview.pdf';document.write_bytes(pdf)
    pdf_item=catalog.register(store,1,Resource('https://school.test/preview.pdf','preview.pdf'))
    catalog.record(store,pdf_item['id'],document,digest(document),'existing')
    page.evaluate('(item)=>openFilePreview(item)',catalog.material(store,pdf_item['id']))
    expect(page.locator('#file-preview-body iframe')).to_be_visible()
    page.wait_for_timeout(2000)
    page.screenshot(path=str(project/'.runtime/v03-pdf.png'))
    page.locator('[data-close="file-preview-dialog"]').click()
    page.set_viewport_size({'width':390,'height':844})
    assert page.locator('#course-dialog').evaluate('(d)=>d.scrollWidth<=d.clientWidth')
    page.screenshot(path=str(project/'.runtime/v03-mobile.png'))
    page.locator('#course-settings summary').first.click()
    page.locator('#remove-course').click()
    expect(page.locator('.course-card')).to_have_count(1)
    assert len(list(root.rglob('*.png')))==1
    page.locator('#mobile-menu').click()
    page.locator('.nav[href="#/library"]').click()
    expect(page.locator('#library')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.go_back()
    expect(page.locator('#courses')).to_be_visible()
    # Task records keep course/group context even after the course is removed.
    page.locator('#mobile-menu').click()
    page.locator('.nav[href="#/history"]').click()
    expect(page.locator('#events tr').first).to_contain_text('信息技术')
    expect(page.locator('#events tr').first).to_contain_text('Week 1')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path=str(project/'.runtime/v03-history-mobile.png'),full_page=True)
    page.locator('#events').get_by_role('button',name='查看位置').first.click()
    expect(page.locator('#location-fields')).to_contain_text(str(root))
    expect(page.locator('#locate-task-file')).to_be_enabled()
    assert page.locator('#task-location-dialog').evaluate('(d)=>d.scrollWidth<=d.clientWidth')
    page.screenshot(path=str(project/'.runtime/v03-history-location-mobile.png'))
    from unittest.mock import patch
    with patch('ispace.web.subprocess.Popen') as explorer:
        page.locator('#locate-task-file').click()
        expect(page.locator('#location-note')).to_contain_text('已在资源管理器中定位')
        assert explorer.call_count==1 and explorer.call_args.args[0][0]=='explorer.exe'
    page.keyboard.press('Escape')
    expect(page.locator('#task-location-dialog')).not_to_be_visible()
    page.set_viewport_size({'width':1440,'height':1000})
    page.screenshot(path=str(project/'.runtime/v03-history.png'),full_page=True)
    page.locator('#events').get_by_role('button',name='查看位置').first.click()
    expect(page.locator('#location-fields')).to_contain_text(str(root))
    page.screenshot(path=str(project/'.runtime/v03-history-location.png'))
    page.locator('[data-close="task-location-dialog"]').click()
    assert not errors, errors
    browser.close()
app.state.service.pool.shutdown(wait=True)
client.close()
print('v0.3 browser passed: routes, membership, unbound browsing, persistent choices, temporary preview, selected download, cache reuse, mobile, safe removal, history course/group context, verified location dialog and Explorer action.')
