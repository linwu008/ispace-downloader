"""Platform contracts: never touch the user's real Keychain or login items."""
import base64
import plistlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from ispace import desktop, updater
from ispace.state import Store, data_dir
from ispace.security import Vault
from ispace.web import create_app


@pytest.fixture
def mac(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, 'is_macos', lambda: True)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(desktop.platform, 'machine', lambda: 'arm64')
    monkeypatch.delenv('ISPACE_DATA_DIR', raising=False)
    return tmp_path


def test_data_outside_bundle_and_override(mac, monkeypatch):
    marker = mac / 'readonly-app/Contents/.runtime/data-dir.txt'
    assert data_dir(marker) == mac / 'Library/Application Support/BNBUCourseNest'
    assert not marker.exists()
    monkeypatch.setenv('ISPACE_DATA_DIR', str(mac / 'custom'))
    assert data_dir(marker) == mac / 'custom'


def test_launch_agent_disabled_by_default_and_relocatable(mac, monkeypatch):
    monkeypatch.setattr(desktop, 'application_bundle', lambda: Path('/Applications/CourseNestHelper.app'))
    assert not desktop.autorun_enabled()
    desktop.set_autorun(True)
    data = plistlib.loads(desktop.launch_agent_path().read_bytes())
    assert desktop.autorun_enabled()
    assert data['RunAtLoad'] and not data.get('KeepAlive')
    assert data['ProgramArguments'][-1] == '--login-start'
    monkeypatch.setattr(desktop, 'application_bundle', lambda: mac / 'Applications/CourseNestHelper.app')
    assert not desktop.autorun_enabled()
    desktop.set_autorun(True)
    assert desktop.autorun_enabled()
    desktop.set_autorun(False)
    assert not desktop.launch_agent_path().exists()


def test_finder_and_picker_preserve_special_characters(mac, monkeypatch):
    calls = []
    monkeypatch.setattr(desktop.subprocess, 'Popen', lambda args: calls.append(args))
    name = mac / '学习 $() " folder' / 'notes.pdf'
    desktop.reveal(name)
    assert calls == [['/usr/bin/open', '-R', str(name)]]
    monkeypatch.setattr(desktop, 'applescript', lambda *a, **k: SimpleNamespace(returncode=0, stdout=str(name.parent)+'\n'))
    assert desktop.choose_folder() == str(name.parent)
    monkeypatch.setattr(desktop, 'applescript', lambda *a, **k: SimpleNamespace(returncode=0, stdout='\n'))
    assert desktop.choose_folder() == ''
    monkeypatch.setattr(desktop, 'applescript', lambda *a, **k: SimpleNamespace(returncode=1, stdout=''))
    with pytest.raises(ValueError, match='目录选择器'):
        desktop.choose_folder()


def test_vault_encrypts_and_never_falls_back_on_denial(mac, monkeypatch):
    values = {}
    backend = SimpleNamespace(get_password=lambda s,k: values.get((s,k)),
        set_password=lambda s,k,v: values.__setitem__((s,k),v),
        delete_password=lambda s,k: values.pop((s,k)))
    monkeypatch.setattr(desktop, 'credential_backend', lambda: backend)
    vault = Vault(mac)
    vault.save_credentials('test-user', 'test-secret')
    vault.save_session({'cookies': ['test-cookie']})
    assert b'test-cookie' not in vault.path.read_bytes()
    assert Vault(mac).load_session() == {'cookies': ['test-cookie']}
    def denied(*args):
        from keyring.errors import PasswordSetError
        raise PasswordSetError('denied')
    backend.set_password = denied
    with pytest.raises(Exception, match='denied'):
        vault.save_credentials('new', 'secret')
    assert not (mac / 'credentials.json').exists()


def signed_meta(arch='arm64'):
    private = Ed25519PrivateKey.generate()
    meta = {'platform': 'macos', 'arch': arch, 'version': '99.0.0', 'sha256': 'a'*64,
            'url': f'https://github.com/linwu008/coursenest-releases/releases/download/v99.0.0/CourseNestHelper-99.0.0-macos-{arch}.zip'}
    meta['signature'] = base64.b64encode(private.sign(updater.signed_bytes(meta))).decode()
    public = base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    return meta, public


@pytest.mark.parametrize('arch,valid', [('arm64', True), ('x86_64', False), ('windows-x64', False)])
def test_update_selects_architecture_and_verifies_signature(mac, monkeypatch, arch, valid):
    meta, public = signed_meta(arch)
    verify = updater.verify
    monkeypatch.setattr(updater, 'verify', lambda m: verify(m, public))
    urls = []
    def get(url, **kwargs):
        urls.append(url)
        return httpx.Response(200, json=meta, request=httpx.Request('GET', url))
    monkeypatch.setattr(updater.httpx, 'get', get)
    instance = updater.Updater(Store(mac / 'state'), Mock())
    result = instance.check()
    assert urls[0].endswith('?platform=macos&arch=arm64')
    assert result['status'] == ('available' if valid else 'error')
    with pytest.raises(ValueError, match='Mac'):
        instance.install()
    meta['signature'] = base64.b64encode(b'bad signature').decode()
    assert instance.check()['status'] == 'error'


def test_unpublished_and_offline_update(mac, monkeypatch):
    monkeypatch.setattr(updater.httpx, 'get', lambda url, **k: httpx.Response(200, json={'available': False}, request=httpx.Request('GET', url)))
    instance = updater.Updater(Store(mac / 'state'), Mock())
    assert instance.check()['status'] == 'unpublished'
    monkeypatch.setattr(updater.httpx, 'get', Mock(side_effect=httpx.ConnectError('offline')))
    assert instance.check()['status'] == 'error'


def test_mac_api_rejects_install_and_schedule_but_keeps_settings(mac):
    app = create_app(Store(mac / 'state'))
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        state = client.get('/api/state').json()
        assert state['platform'] == 'macos' and state['arch'] == 'arm64'
        headers = {'X-iSpace-Token': state['csrf']}
        assert client.get('/api/updates').json()['mode'] == 'manual'
        assert client.post('/api/updates/install', headers=headers).status_code == 400
        assert client.put('/api/updates/automatic', headers=headers, json={'enabled':True}).status_code == 400
        assert client.put('/api/schedule', headers=headers, json={'enabled':True,'time':'20:00'}).status_code == 400
        assert client.put('/api/schedule', headers=headers, json={'enabled':False,'time':'20:00'}).status_code == 200
    app.state.service.pool.shutdown()


def test_notification_failure_does_not_interrupt_worker(mac, monkeypatch):
    monkeypatch.setattr(desktop, 'applescript', Mock(side_effect=OSError('unavailable')))
    desktop.notify(Mock(), 'Test message')


def test_cancel_before_response_is_registered_never_reads_body():
    from ispace.moodle import Moodle
    from ispace.cancellation import Cancelled
    class NeverRead(httpx.SyncByteStream):
        closed = False
        def __iter__(self):
            raise AssertionError('canceled response must not be read')
        def close(self):
            self.closed = True
    stream = NeverRead()
    def headers_arrive(request):
        # Deterministically reproduce cancel between send() and registration.
        client.interrupt()
        return httpx.Response(200, stream=stream)
    client = Moodle(transport=httpx.MockTransport(headers_arrive))
    with pytest.raises(Cancelled):
        client.request('GET', '/fixture')
    assert stream.closed
