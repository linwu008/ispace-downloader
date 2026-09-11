import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ispace import catalog, organize
from ispace.grouping import TeachingGroup
from ispace.moodle import Resource, Discovery
from ispace.state import Store
from ispace.sync import SyncEngine, digest
from ispace.web import create_app

W1=TeachingGroup('section:1','Week 1 — Introduction',1000)
W2=TeachingGroup('section:2','Week 2 — Practice',2000)


class Platform:
    def __init__(self, groups=(W1,W2)):
        self.groups=groups
        self.content=b'lecture-content'
        self.etag='"v1"'
        self.downloads=0
    def discover(self, course):
        return Discovery([Resource('https://school.test/pluginfile.php/1/mod_resource/content/1/a.pdf','a.pdf',group=g,source_page='https://school.test/course/view.php?id=1') for g in self.groups])
    def metadata(self, resource): return {'etag':self.etag}
    def download(self, resource, target):
        self.downloads+=1
        target.write_bytes(self.content)
        return self.metadata(resource)


@pytest.fixture
def setup(tmp_path):
    store=Store(tmp_path/'state')
    root=tmp_path/'course'
    root.mkdir()
    store.refresh_courses([{'id':1,'name':'Math'}])
    store.bind(1,str(root),True)
    return store,root


def run(store, platform):
    return SyncEngine(store,platform,sleep=lambda _:None).run(store.start_run())


def seed_legacy(store, root, platform):
    source=platform.discover(1).resources[0]
    path=root/'old.pdf';path.write_bytes(platform.content)
    value=digest(path)
    store.remember(1,source.key,path,value,platform.etag,None,path.stat().st_size)
    with store.connect() as db:
        db.execute('INSERT INTO legacy_resources VALUES (?,?,?,?,?,?,?)',(1,source.key,str(path),value,platform.etag,None,path.stat().st_size))
    return path


def test_cross_group_copy_without_duplicate_transfer(setup):
    store,root=setup;platform=Platform()
    result=run(store,platform)
    assert result['downloaded']==2 and platform.downloads==1
    paths=[Path(i['path']) for i in catalog.material_page(store)['items']]
    assert len({p.parent for p in paths})==2 and all(p.read_bytes()==platform.content for p in paths)
    result=run(store,platform)
    assert result['skipped']==2 and result['downloaded']==0 and platform.downloads==1


def test_legacy_files_stay_until_preview_confirmation(setup):
    store,root=setup;platform=Platform();old=seed_legacy(store,root,platform)
    assert run(store,platform)['pending_organization']==2
    assert old.exists() and list(root.rglob('*.pdf'))==[old]
    plan=organize.preview(store,course_id=1)
    assert old.exists() and len(plan['rows'])==2
    result=organize.execute(store,plan['id'],[r['id'] for r in plan['rows'] if r['selectable']])
    assert result['failed']==0 and not old.exists()
    assert len(list(root.rglob('*.pdf')))==2
    assert run(store,platform)['downloaded']==0 and platform.downloads==0


def test_partial_selection_retains_shared_original_until_all_copies_exist(setup):
    store,root=setup;platform=Platform();old=seed_legacy(store,root,platform);run(store,platform)
    plan=organize.preview(store,course_id=1)
    first,second=[r['id'] for r in plan['rows']]
    assert organize.execute(store,plan['id'],[first])['failed']==0 and old.exists()
    assert organize.execute(store,plan['id'],[second])['failed']==0 and not old.exists()
    assert organize.execute(store,plan['id'],[first,second])['already_done']==2


def test_modified_source_is_skipped_without_overwrite(setup):
    store,root=setup;platform=Platform((W1,));old=seed_legacy(store,root,platform);run(store,platform)
    plan=organize.preview(store,course_id=1);old.write_bytes(b'user notes')
    result=organize.execute(store,plan['id'],[plan['rows'][0]['id']])
    assert result['failed']==1 and old.read_bytes()==b'user notes'
    assert not Path(plan['rows'][0]['target']).exists()


def test_destination_conflict_never_replaced(setup):
    store,root=setup;platform=Platform((W1,));old=seed_legacy(store,root,platform);run(store,platform)
    plan=organize.preview(store,course_id=1)
    target=Path(plan['rows'][0]['target']);target.write_bytes(b'other data')
    assert organize.execute(store,plan['id'],[plan['rows'][0]['id']])['failed']==1
    assert old.exists() and target.read_bytes()==b'other data'


def test_changed_group_config_invalidates_preview(setup):
    store,root=setup;platform=Platform((W1,));seed_legacy(store,root,platform);run(store,platform)
    plan=organize.preview(store,course_id=1)
    with store.connect() as db: db.execute('UPDATE teaching_groups SET revision=revision+1')
    with pytest.raises(ValueError,match='设置已变化'):
        organize.execute(store,plan['id'],[plan['rows'][0]['id']])


def test_manual_directory_requires_preview_and_survives_sync(setup):
    store,root=setup;platform=Platform((W1,));run(store,platform)
    group=catalog.groups(store)[0];old=Path(catalog.material_page(store)['items'][0]['path'])
    plan=organize.preview(store,group_id=group['id'],folder='自定义第一周',mode='manual')
    assert catalog.group_by_id(store,group['id'])['folder']==group['folder']
    assert not (root/'自定义第一周').exists()
    result=organize.execute(store,plan['id'],[r['id'] for r in plan['rows'] if r['selectable']])
    assert result['failed']==0 and not old.exists()
    run(store,platform)
    assert catalog.group_by_id(store,group['id'])['folder']=='自定义第一周'
    assert catalog.group_by_id(store,group['id'])['mode']=='manual'
    assert len(list(root.rglob('*.pdf')))==1


def test_apply_settings_only_keeps_files_pending_on_next_sync(setup):
    store,root=setup;platform=Platform((W1,));run(store,platform)
    group=catalog.groups(store)[0];old=Path(catalog.material_page(store)['items'][0]['path'])
    plan=organize.preview(store,group_id=group['id'],folder='later',mode='manual')
    organize.execute(store,plan['id'],['settings'])
    run(store,platform)
    assert old.exists() and not list((root/'later').glob('*.pdf'))
    assert catalog.material_page(store)['items'][0]['status']=='pending_organize'


def test_manual_material_group_survives_future_discovery(setup):
    store,root=setup;platform=Platform((W1,));run(store,platform)
    target=catalog.ensure_group(store,1,W2)
    item=catalog.material_page(store)['items'][0]
    plan=organize.preview(store,item_id=item['id'],target_group_id=target['id'])
    assert organize.execute(store,plan['id'],[r['id'] for r in plan['rows'] if r['selectable']])['failed']==0
    run(store,platform)
    item=catalog.material(store,item['id'])
    assert item['group_id']==target['id'] and item['manual_group']==1


def test_teacher_rename_keeps_existing_folder_and_auto_reset_is_previewed(setup):
    store,root=setup;platform=Platform((W1,));run(store,platform)
    old=catalog.groups(store)[0]
    new=catalog.ensure_group(store,1,TeachingGroup(W1.key,'Week 1 — New title',1000))
    assert new['folder']==old['folder'] and new['title']!=old['title']
    plan=organize.preview(store,group_id=new['id'],mode='auto')
    organize.execute(store,plan['id'],[r['id'] for r in plan['rows'] if r['selectable']])
    assert 'New title' in catalog.group_by_id(store,new['id'])['folder']


@pytest.mark.parametrize('path',['../outside','.', '.git/private','/absolute'])
def test_manual_paths_cannot_escape_course(setup,path):
    store,root=setup;group=catalog.ensure_group(store,1,W1)
    with pytest.raises(ValueError): organize.preview(store,group_id=group['id'],folder=path,mode='manual')


def test_group_paths_cannot_overlap(setup):
    store,root=setup;first=catalog.ensure_group(store,1,W1);second=catalog.ensure_group(store,1,W2)
    with pytest.raises(ValueError): organize.preview(store,group_id=second['id'],folder=first['folder']+'/nested',mode='manual')


def test_search_filters_pagination_and_order(setup):
    store,root=setup;run(store,Platform((W2,W1)))
    first=catalog.material_page(store,q='week 1')
    assert first['total']==1 and first['items'][0]['group_title'].startswith('Week 1')
    assert catalog.material_page(store,q='a.pdf',page_size=1)['total']==2
    assert catalog.material_page(store,page=2,page_size=1)['items'][0]['group_title'].startswith('Week 2')
    assert catalog.material_page(store,q='%')['total']==0


def test_deleted_material_status_filter(setup):
    store,root=setup;run(store,Platform((W1,)))
    item=catalog.material_page(store)['items'][0];Path(item['path']).unlink()
    assert catalog.material_page(store,status='missing')['total']==1


def test_database_migration_is_repeatable_and_keeps_backup(setup):
    store,root=setup
    backup=store.directory/'index-pre-v0.2.sqlite3'
    before=backup.read_bytes()
    again=Store(store.directory)
    with again.connect() as db: assert db.execute('PRAGMA user_version').fetchone()[0]==3
    assert backup.read_bytes()==before and again.courses()[0]['folder']==str(root)


def test_api_group_changes_only_create_preview(setup):
    store,root=setup;run(store,Platform((W1,)))
    client=TestClient(create_app(store),base_url='http://127.0.0.1:8765')
    state=client.get('/api/state').json();headers={'X-iSpace-Token':state['csrf']}
    group=state['groups'][0]
    response=client.put('/api/groups/'+group['id'],headers=headers,json={'mode':'manual','folder':'new-week'})
    assert response.status_code==200 and response.json()['rows'][0]['action']=='settings'
    assert not (root/'new-week').exists()
    assert client.get('/api/materials?q=Week').json()['total']==1
