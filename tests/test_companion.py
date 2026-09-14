import json
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from ispace import catalog, courses
from ispace.companion import Companion, server_origin
from ispace.service import Service
from ispace.state import Store
from ispace.web import create_app
from test_v3 import Platform


@pytest.fixture
def setup(tmp_path):
    store=Store(tmp_path/'state');store.refresh_courses([{'id':1,'name':'Computing'}])
    courses.membership(store,[1],True)
    root=tmp_path/'course';root.mkdir();store.bind(1,str(root),True)
    platform=Platform();platform.courses=lambda:[{'id':1,'name':'Computing'}]
    vault=SimpleNamespace(cipher=lambda:Fernet(key))
    key=Fernet.generate_key()
    service=Service(store,vault=vault);service.authenticated=lambda:platform
    companion=Companion(store,service);companion.start=lambda:None
    yield companion,store,platform,root
    service.pool.shutdown()


def test_origin_requires_https_except_local():
    assert server_origin('http://127.0.0.1:8787/')=='http://127.0.0.1:8787'
    assert server_origin('https://nest.example.com')=='https://nest.example.com'
    for address in ['http://public.test','https://user:pass@test.com','https://test.com/path','file:///tmp/a','https://test.com?token=secret']:
        with pytest.raises(ValueError):server_origin(address)


def test_device_token_encrypted_and_never_returned_in_status(setup):
    companion,store,_,_=setup
    companion.transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'token':'device-secret','device_id':'one'}))
    result=companion.pair('https://nest.test','PAIR-CODE','My PC')
    assert result['paired'] and 'token' not in result
    assert b'device-secret' not in companion.path.read_bytes()
    assert companion.load()['token']=='device-secret'
    companion.disconnect();assert not companion.path.exists()


def test_remote_selection_sync_reuses_existing_engine_and_preserves_versions(setup):
    companion,store,platform,root=setup
    companion.execute({'kind':'catalog','payload':{'course_id':1}})
    items=catalog.material_page(store)['items'];selected=items[0]['id']
    companion.execute({'kind':'selection','payload':{'course_id':1,'ids':[selected],'mode':'selected'}})
    result=companion.execute({'kind':'sync','payload':{'course_id':1}})
    assert result['downloaded']==1 and len(list(root.rglob('*.png')))==1 and not list(root.rglob('*.txt'))
    assert companion.execute({'kind':'sync','payload':{'course_id':1}})['downloaded']==0
    original=next(root.rglob('*.png'));original.unlink()
    assert companion.execute({'kind':'sync','payload':{'course_id':1}})['downloaded']==1


def test_receipt_prevents_execution_after_lost_acknowledgement(setup):
    companion,_,_,_=setup
    config={'device_id':'one','server':'https://nest.test','token':'secret'}
    job={'id':'job','kind':'refresh_courses','payload':{},'lease_token':'lease'}
    executions=[];companion.execute=lambda job:executions.append(job) or {'status':'success','message':'done'}
    companion.request=lambda *args:(_ for _ in ()).throw(httpx.ConnectError('offline'))
    with pytest.raises(httpx.ConnectError):companion.process(config,job)
    assert len(executions)==1
    companion.request=lambda *args:{'ok':True}
    companion.process(config,{**job,'lease_token':'new-lease'})
    assert len(executions)==1


def test_receipts_scoped_to_device_and_local_commands_not_accepted(setup):
    companion,_,_,_=setup
    with pytest.raises(ValueError):companion.execute({'kind':'shell','payload':{'command':'bad'}})
    with pytest.raises(ValueError):companion.execute({'kind':'sync','payload':{'course_id':99,'folder':'C:\\Windows'}})


def test_snapshot_excludes_school_secrets_and_reports_missing(setup):
    companion,store,_,root=setup
    companion.execute({'kind':'catalog','payload':{'course_id':1}})
    companion.execute({'kind':'selection','payload':{'course_id':1,'ids':[],'mode':'all'}})
    companion.execute({'kind':'sync','payload':{}})
    next(root.rglob('*.png')).unlink()
    snapshot=companion.snapshot({})
    assert any(m['status']=='missing' for m in snapshot['materials'])
    assert 'pluginfile' not in json.dumps(snapshot) and 'password' not in json.dumps(snapshot)


def test_pause_prevents_claims_and_offline_is_visible(setup):
    companion,store,_,_=setup
    companion.save({'server':'https://nest.test','token':'secret','device_id':'one','name':'PC','paused':True})
    messages=[];companion.request=lambda c,p,b:messages.append(b) or {'job':None}
    companion.tick();assert messages[0]['paused']
    companion.request=lambda *args:(_ for _ in ()).throw(httpx.ConnectError('secret-url'))
    companion.tick();assert '恢复网络' in companion.status()['message'] and 'secret-url' not in companion.status()['message']


def test_local_pair_routes_require_csrf_and_validate_body(setup):
    companion,store,_,_=setup
    app=create_app(store,companion.service)
    with TestClient(app,base_url='http://127.0.0.1:8765') as client:
        csrf=client.get('/api/state').json()['csrf']
        assert client.post('/api/companion/pair',json={'server':'http://remote.test','code':'x'}).status_code==403
        response=client.post('/api/companion/pair',headers={'X-iSpace-Token':csrf},json={'server':'http://remote.test','code':'x'})
        assert response.status_code==400
        assert 'HTTPS' in response.json()['detail']


def test_assistant_autostart_uses_supported_application_lifecycle(setup, monkeypatch):
    companion, store, _, _ = setup
    events = []
    monkeypatch.setenv('ISPACE_COMPANION_AUTOSTART', '1')
    monkeypatch.setattr(Companion, 'start', lambda self: events.append('start'))
    monkeypatch.setattr(Companion, 'stop', lambda self: events.append('stop'))
    app = create_app(store, companion.service)
    with TestClient(app) as client:
        assert events == ['start']
    assert events == ['start', 'stop']


def test_website_link_opens_assistant_but_cross_site_apis_stay_blocked(setup):
    companion, store, _, _ = setup
    with TestClient(create_app(store, companion.service), base_url='http://127.0.0.1:8765') as client:
        headers = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'}
        response = client.get('/', headers=headers)
        assert response.status_code == 200
        assert "frame-ancestors 'none'" in response.headers['content-security-policy']
        assert client.get('/api/state', headers=headers).status_code == 403
        assert client.get('/', headers={**headers, 'Sec-Fetch-Dest': 'iframe'}).status_code == 403
        assert client.get('/', headers={**headers, 'Sec-Fetch-Mode': 'cors'}).status_code == 403
        token = client.get('/api/state').json()['csrf']
        assert client.post('/api/companion/pair', headers={**headers, 'X-iSpace-Token': token}, json={'server':'https://example.com','code':'test'}).status_code == 403
        assert client.get('/', headers={**headers, 'Origin': 'https://example.com'}).status_code == 403
        assert client.get('/', headers={**headers, 'Host': 'attacker.example'}).status_code == 403
