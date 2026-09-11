import base64
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ispace import catalog, courses, previews
from ispace.grouping import TeachingGroup
from ispace.moodle import Discovery, Moodle, Resource, ResourceError
from ispace.state import Store
from ispace.sync import SyncEngine
from ispace.web import create_app

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


class Platform:
    def __init__(self):
        self.files = [('diagram.png', PNG), ('notes.txt', b'Week two notes')]
        self.downloads = 0
        self.errors = []
    def discover(self, course_id):
        return Discovery([Resource('https://school.test/pluginfile.php/1/mod_resource/content/'+name, name, group=TeachingGroup('week:'+str(i), 'Week '+str(i+1), (i+1)*1000)) for i, (name, _) in enumerate(self.files)], self.errors)
    def metadata(self, resource):
        return {'etag': '"v1"'}
    def allowed(self, url):
        return url.startswith('https://school.test/')
    def download(self, resource, target, max_bytes=None):
        self.downloads += 1
        content = dict(self.files)[resource.name]
        if max_bytes is not None and len(content) > max_bytes:
            raise ResourceError('file too large')
        target.write_bytes(content)
        return self.metadata(resource)
    def close(self):
        pass


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path/'state')
    store.refresh_courses([{'id': 1, 'name': 'Computing'}, {'id': 2, 'name': 'Math'}])
    courses.membership(store, [1], True)
    platform = Platform()
    return store, platform, tmp_path


def files(store):
    return catalog.material_page(store, course_id=1)['items']


def sync(store, platform):
    return SyncEngine(store, platform, sleep=lambda _: None).run(store.start_run())


def test_browse_before_binding_does_not_download(setup):
    store, platform, _ = setup
    assert courses.discover(store, platform, 1)['status'] == 'success'
    assert len(files(store)) == 2 and platform.downloads == 0
    assert all(not row['path'] and not row['selected'] for row in files(store))
    assert catalog.groups(store)[0]['folder'] == '01 Week 1'


def test_remove_refresh_restore_preserves_data_and_preferences(setup):
    store, platform, tmp = setup
    courses.discover(store, platform, 1)
    root=tmp/'course';root.mkdir();store.bind(1,str(root),True)
    courses.selection(store,1,[files(store)[0]['id']])
    sync(store,platform)
    local = next(root.rglob('*.png'))
    courses.membership(store,[1],False)
    store.refresh_courses([{'id':1,'name':'Renamed'}])
    assert courses.get(store,1)['membership']=='removed' and not courses.get(store,1)['enabled']
    assert local.exists() and not files(store)
    assert catalog.material_page(store,include_removed=True)['total']==2
    courses.membership(store,[1],True)
    assert courses.get(store,1)['sync_mode']=='selected' and files(store)[0]['selected']
    assert courses.get(store,1)['folder']==str(root)


def test_daily_only_downloads_selected_and_new_files_wait(setup):
    store, platform, tmp = setup
    courses.discover(store,platform,1)
    root=tmp/'course';root.mkdir();store.bind(1,str(root),True)
    courses.selection(store,1,[files(store)[0]['id']])
    assert sync(store,platform)['downloaded']==1
    platform.files.append(('new.txt',b'new lecture'))
    result=sync(store,platform)
    assert result['downloaded']==0 and result['not_selected']==2 and platform.downloads==1
    assert not files(store)[-1]['selected']
    courses.selection(store,1,[])
    assert sync(store,platform)['downloaded']==0 and next(root.rglob('*.png')).exists()


def test_whole_course_mode_keeps_v2_behavior(setup):
    store,platform,tmp=setup
    root=tmp/'course';root.mkdir();store.bind(1,str(root),True)
    courses.selection(store,1,[],mode='all')
    assert sync(store,platform)['downloaded']==2


def test_partial_catalog_keeps_old_files_and_selection(setup):
    store,platform,_=setup
    courses.discover(store,platform,1);chosen=files(store)[1]['id'];courses.selection(store,1,[chosen])
    platform.files=platform.files[:1];platform.errors=['page unavailable']
    assert courses.discover(store,platform,1)['status']=='partial'
    assert len(files(store))==2 and catalog.material(store,chosen)['selected']


def test_selection_cannot_reference_other_courses(setup):
    store,platform,_=setup
    courses.membership(store,[2],True);courses.discover(store,platform,2)
    foreign=catalog.material_page(store,course_id=2)['items'][0]['id']
    with pytest.raises(ValueError): courses.selection(store,1,[foreign])


def test_preview_is_temporary_and_download_reuses_verified_cache(setup):
    store,platform,tmp=setup
    courses.discover(store,platform,1);item=files(store)[0]
    result=previews.prepare(store,item['id'],platform)
    assert result['preview']['temporary'] and platform.downloads==1
    assert not catalog.material(store,item['id'])['path'] and not catalog.material(store,item['id'])['selected']
    root=tmp/'course';root.mkdir();store.bind(1,str(root),True);courses.selection(store,1,[item['id']])
    assert sync(store,platform)['downloaded']==1 and platform.downloads==1
    assert not previews.prepare(store,item['id'])['preview']['temporary']


def test_preview_cache_expiry_and_corruption(setup):
    store,platform,_=setup;courses.discover(store,platform,1);item=files(store)[0]
    previews.prepare(store,item['id'],platform);cache=previews.cached(store,item['id'])
    cache['path'].write_bytes(b'changed')
    assert previews.cached(store,item['id']) is None
    previews.prepare(store,item['id'],platform)
    with store.connect() as db: db.execute('UPDATE preview_cache SET created=?',(time.time()-previews.TTL-1,))
    assert previews.prepare(store,item['id']) is None
    previews.cleanup(store)
    assert not list(previews.directory(store).iterdir())


@pytest.mark.parametrize('name',['danger.html','vector.svg','program.exe','slides.pptx'])
def test_unsupported_preview_never_downloads(setup,name):
    store,platform,_=setup;platform.files=[(name,b'contents')];courses.discover(store,platform,1)
    with pytest.raises(ValueError): previews.prepare(store,files(store)[0]['id'],platform)
    assert platform.downloads==0


def test_preview_size_limit_and_disguised_html(setup,monkeypatch):
    store,platform,_=setup;platform.files=[('fake.png',b'<html>not an image</html>')];courses.discover(store,platform,1)
    with pytest.raises(ValueError): previews.prepare(store,files(store)[0]['id'],platform)
    monkeypatch.setattr(previews,'MAX_BYTES',10)
    with pytest.raises(ResourceError): previews.prepare(store,files(store)[0]['id'],platform)
    assert not list(previews.directory(store).glob('*.part'))


def test_streaming_preview_stops_at_limit(tmp_path):
    transport=httpx.MockTransport(lambda request:httpx.Response(200,content=b'x'*100))
    platform=Moodle(base='https://school.test',transport=transport)
    with pytest.raises(ResourceError): platform.download(Resource('https://school.test/file','x.txt'),tmp_path/'x.part',max_bytes=10)
    platform.close()


def test_membership_route_and_preview_content(setup):
    store,platform,_=setup;courses.discover(store,platform,1);item=files(store)[1]
    previews.prepare(store,item['id'],platform)
    app=create_app(store);client=TestClient(app,base_url='http://127.0.0.1:8765')
    headers={'X-iSpace-Token':client.get('/api/state').json()['csrf']}
    response=client.put('/api/courses/membership',headers=headers,json={'course_ids':[2],'added':True})
    assert response.status_code==200
    content=client.get(f"/api/materials/{item['id']}/preview/content")
    assert content.status_code==200 and 'text/plain' in content.headers['content-type'] and content.text=='Week two notes'
    assert client.get('/api/materials/999/preview/content').status_code==400
    app.state.service.pool.shutdown()


def test_migration_backed_up_once(setup):
    store,_,_=setup
    backup=store.directory/'index-pre-v0.3.sqlite3';before=backup.read_bytes()
    Store(store.directory)
    assert backup.read_bytes()==before
    with store.connect() as db: assert db.execute('PRAGMA user_version').fetchone()[0]==3


def test_selected_file_missing_from_school_is_not_success(setup):
    store,platform,tmp=setup
    courses.discover(store,platform,1);chosen=files(store)[1]['id']
    root=tmp/'course';root.mkdir();store.bind(1,str(root),True)
    courses.selection(store,1,[chosen]);platform.files=platform.files[:1]
    result=sync(store,platform)
    assert result['status']=='partial' and result['failed']==1 and platform.downloads==0
    assert catalog.material(store,chosen)['status']=='failed'
