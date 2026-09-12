from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from ispace import catalog, history, organize
from ispace.state import Store
from ispace.web import create_app
from test_catalog_v2 import setup, Platform, W1, run


def old_event(store):
    run_id = store.start_run()
    with store.connect() as db:
        return db.execute("INSERT INTO events (run_id,course_id,name,status,message,created) VALUES (?,1,'a.pdf','downloaded','legacy','2026-09-01')", (run_id,)).lastrowid


def test_same_name_groups_keep_distinct_history_locations(setup):
    store, root = setup
    run(store, Platform())
    events = store.history()['events']
    assert len(events) == 2
    assert {e['course_name'] for e in events} == {'Math'}
    assert len({e['group_title'] for e in events}) == 2
    locations = [history.location(store, e['id']) for e in events]
    assert all(p['exists'] and Path(p['path']).is_relative_to(root) for p in locations)
    assert len({p['folder'] for p in locations}) == 2
    store.refresh_courses([{'id': 1, 'name': 'Renamed Math'}])
    assert {e['course_name'] for e in store.history()['events']} == {'Math'}


@pytest.mark.parametrize('ambiguous', [False, True])
def test_old_records_only_resolve_unique_matches(setup, ambiguous):
    store, _ = setup
    run(store, Platform() if ambiguous else Platform((W1,)))
    event_id = old_event(store)
    result = history.location(store, event_id)
    assert result['exists'] is not ambiguous
    assert ('多个同名资料' if ambiguous else '当前保存位置') in result['note']
    assert result['course_name'] == 'Math'


def test_history_retains_exact_version_and_does_not_point_to_newer_file(setup):
    store, _ = setup
    platform = Platform((W1,))
    run(store, platform)
    event_id = store.history()['events'][0]['id']
    original = history.location(store, event_id)['path']
    platform.content = b'updated lecture'
    platform.etag = '"v2"'
    run(store, platform)
    assert history.location(store, event_id)['path'] == original
    latest = catalog.material_page(store)['items'][0]['path']
    assert latest != original
    Path(original).unlink()
    assert not history.location(store, event_id)['exists']
    assert Path(latest).is_file()


def test_history_follows_verified_organization_move(setup):
    store, _ = setup
    run(store, Platform((W1,)))
    event_id = store.history()['events'][0]['id']
    original = history.location(store, event_id)['path']
    group = catalog.groups(store)[0]
    plan = organize.preview(store, group_id=group['id'], folder='自定义第一周', mode='manual')
    result = organize.execute(store, plan['id'], [r['id'] for r in plan['rows'] if r['selectable']])
    assert result['failed'] == 0
    location = history.location(store, event_id)
    assert location['exists'] and Path(location['folder']).name == '自定义第一周'
    assert location['recorded_path'] == original and '新目录' in location['note']
    records = organize.history(store)
    assert records[0]['course_names'] == ['Math']
    assert records[0]['group_titles'] == [W1.title]


@pytest.mark.parametrize('change', ['modified', 'outside'])
def test_unverified_or_outside_files_cannot_be_located(setup, change):
    store, root = setup
    run(store, Platform((W1,)))
    event_id = store.history()['events'][0]['id']
    original = Path(history.location(store, event_id)['path'])
    if change == 'modified':
        original.write_bytes(b'modified locally')
    else:
        outside = root.parent / 'outside.pdf'
        original.replace(outside)
        with store.connect() as db:
            db.execute('UPDATE event_context SET saved_path=? WHERE event_id=?', (str(outside), event_id))
            db.execute('UPDATE material_versions SET path=?', (str(outside),))
    assert not history.location(store, event_id)['exists']


def test_course_failures_do_not_claim_a_file_location(setup):
    store, _ = setup
    run(store, Platform((W1,)))
    store.event(store.start_run(), 1, 'Math', 'failed', 'login expired')
    event = store.history()['events'][0]
    assert event['course_name'] == 'Math' and '课程检查' in event['group_title']
    assert not history.location(store, event['id'])['exists']


def test_location_api_rechecks_before_opening_explorer(setup, monkeypatch):
    store, _ = setup
    run(store, Platform((W1,)))
    event_id = store.history()['events'][0]['id']
    app = create_app(store)
    calls = []
    monkeypatch.setattr('ispace.web.subprocess.Popen', lambda args: calls.append(args))
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        headers = {'X-iSpace-Token': client.get('/api/state').json()['csrf']}
        response = client.get(f'/api/events/{event_id}/location')
        assert response.status_code == 200 and response.json()['exists']
        assert client.get('/api/events/999999/location').status_code == 400
        assert client.post(f'/api/events/{event_id}/locate').status_code == 403
        assert client.post(f'/api/events/{event_id}/locate', headers=headers).status_code == 200
        assert calls == [['explorer.exe', '/select,', response.json()['path']]]
        Path(response.json()['path']).unlink()
        assert client.post(f'/api/events/{event_id}/locate', headers=headers).status_code == 400
        assert len(calls) == 1
    app.state.service.pool.shutdown()


def test_existing_history_is_backed_up_once_and_preserved(setup):
    store, _ = setup
    event_id = old_event(store)
    with store.connect() as db:
        db.execute('DROP TABLE event_context')
    history.ensure_schema(store)
    backup = store.directory / 'index-pre-history-locations.sqlite3'
    with sqlite3.connect(backup) as db:
        assert db.execute('SELECT id FROM events').fetchone()[0] == event_id
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='event_context'").fetchone()
    initial = backup.read_bytes()
    Store(store.directory)
    assert backup.read_bytes() == initial
    assert store.history()['events'][0]['id'] == event_id
