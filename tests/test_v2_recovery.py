from pathlib import Path

import httpx
import pytest

from ispace import catalog, organize
from ispace.grouping import group_for
from ispace.moodle import Moodle, soup_of
from test_catalog_v2 import setup, run, seed_legacy, Platform, W1, W2


def test_module_heading_and_activity_navigation_do_not_change_group(monkeypatch):
    monkeypatch.setattr('ispace.moodle.time.sleep', lambda _: None)
    visited = []
    def handler(request):
        visited.append(str(request.url))
        assert len(visited) < 8
        if request.url.path == '/course/view.php':
            html = '<li id="section-1"><h3 class="sectionname">Week 1</h3><a href="/mod/folder/view.php?id=1">Folder</a></li>'
        elif request.url.path == '/mod/folder/view.php':
            html = '<h2>Lab 2</h2><a href="/pluginfile.php/1/mod_folder/content/a.pdf">File</a><div class="activity-navigation"><a href="/mod/folder/view.php?id=99">Next activity</a></div>'
        else:
            html = ''
        return httpx.Response(200, text='<main role="main">' + html + '</main>')
    adapter = Moodle(base='https://school.test', transport=httpx.MockTransport(handler))
    result = adapter.discover(1)
    assert not result.errors and len(result.resources) == 1
    assert result.resources[0].group.title == 'Week 1'
    assert not any('id=99' in url for url in visited)


def test_disk_full_retains_original_and_retry_completes(setup, monkeypatch):
    store, root = setup
    platform = Platform((W1,))
    old = seed_legacy(store, root, platform)
    run(store, platform)
    plan = organize.preview(store, course_id=1)
    selected = [row['id'] for row in plan['rows']]
    real_copy = organize.atomic_copy
    def full(*args):
        raise OSError(28, 'No space left')
    monkeypatch.setattr(organize, 'atomic_copy', full)
    assert organize.execute(store, plan['id'], selected)['failed'] == 1
    assert old.exists()
    monkeypatch.setattr(organize, 'atomic_copy', real_copy)
    assert organize.execute(store, plan['id'], selected)['failed'] == 0
    assert not old.exists()


def test_interrupted_after_publish_can_resume(setup, monkeypatch):
    store, root = setup
    platform = Platform((W1,))
    old = seed_legacy(store, root, platform)
    run(store, platform)
    plan = organize.preview(store, course_id=1)
    selected = [row['id'] for row in plan['rows']]
    real_copy = organize.atomic_copy
    def interrupted(source, target):
        real_copy(source, target)
        raise OSError('simulated interruption after publication')
    monkeypatch.setattr(organize, 'atomic_copy', interrupted)
    assert organize.execute(store, plan['id'], selected)['failed'] == 1
    assert old.exists() and Path(plan['rows'][0]['target']).exists()
    monkeypatch.setattr(organize, 'atomic_copy', real_copy)
    assert organize.execute(store, plan['id'], selected)['failed'] == 0
    assert not old.exists()


def test_existing_target_version_merges_without_index_collision(setup):
    store, root = setup
    platform = Platform((W1,))
    old = seed_legacy(store, root, platform)
    run(store, platform)
    plan = organize.preview(store, course_id=1)
    row = plan['rows'][0]
    target = Path(row['target'])
    target.write_bytes(old.read_bytes())
    with store.connect() as db:
        db.execute('INSERT INTO material_versions(material_id,path,digest) VALUES (?,?,?)', (row['item_id'], str(target), row['digest']))
    assert organize.execute(store, plan['id'], [row['id']])['failed'] == 0
    assert not old.exists()


def test_update_keeps_versions_and_deleted_group_copy_is_restored(setup):
    store, root = setup
    platform = Platform()
    run(store, platform)
    platform.content = b'updated'
    platform.etag = '"v2"'
    assert run(store, platform)['downloaded'] == 2
    assert len(list(root.rglob('*.pdf'))) == 4
    item = catalog.material_page(store)['items'][0]
    Path(item['path']).unlink()
    transfers = platform.downloads
    assert run(store, platform)['downloaded'] == 1
    assert platform.downloads == transfers
    assert len(list(root.rglob('*.pdf'))) == 4


def test_same_titles_with_different_ids_get_disjoint_folders(setup):
    from ispace.grouping import TeachingGroup
    store, _ = setup
    first = catalog.ensure_group(store, 1, W1)
    second = catalog.ensure_group(store, 1, TeachingGroup('another', W1.title, W1.position))
    assert first['folder'] != second['folder']
    catalog.target_dir(store, first)
    catalog.target_dir(store, second)
