import json
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from ispace.scheduler import ZONE, next_run, task_xml
from ispace.security import LoginRequired, Vault
from ispace.service import BusyError, Service
from ispace.state import Store
from ispace.web import create_app


class Backend:
    values = None
    def __init__(self):
        self.values = {}
    def get_password(self, service, key):
        return self.values.get((service, key))
    def set_password(self, service, key, value):
        self.values[service, key] = value
    def delete_password(self, service, key):
        del self.values[service, key]


def test_session_encrypted_and_credentials_not_on_disk(tmp_path, monkeypatch):
    vault = Vault(tmp_path)
    backend = Backend()
    monkeypatch.setattr(vault, "backend", lambda: backend)
    state = {"cookies": [{"name": "MoodleSession", "value": "topsecret"}]}
    vault.save_session(state)
    vault.save_credentials("me", "privatepassword")
    assert b"topsecret" not in vault.path.read_bytes()
    assert b"privatepassword" not in vault.path.read_bytes()
    assert vault.load_session() == state
    assert vault.credentials()["username"] == "me"
    vault.clear()
    assert not vault.path.exists() and not backend.values


def test_cross_process_lock(tmp_path):
    store = Store(tmp_path)
    first, second = Service(store), Service(store)
    with first.lock():
        assert second.busy()
        with pytest.raises(BusyError):
            second.execute("sync")
    assert not second.busy()


def test_password_failure_blocks_future_automatic_attempts(tmp_path):
    store = Store(tmp_path)
    class FakeVault:
        def load_session(self): return None
        def credentials(self): return {"username": "me", "password": "wrong"}
    class Expired:
        def __init__(self, state): pass
        def check_login(self): raise LoginRequired("expired")
        def close(self): pass
    calls = []
    def login(*args, **kwargs):
        calls.append(1)
        raise LoginRequired("wrong password")
    service = Service(store, FakeVault(), Expired, login)
    assert service.execute("courses")["status"] == "auth_required"
    assert service.execute("courses")["status"] == "auth_required"
    assert len(calls) == 1


def test_scheduler_time_zone_catchup_and_no_password():
    current = datetime(2026,9,10,21,0,tzinfo=ZONE)
    assert next_run("20:00",current) == "2026-09-11T20:00:00+08:00"
    xml = task_xml("20:00",Path("F:/课程资料/state"),"TEST\\user",current)
    tree = ElementTree.fromstring(xml)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert tree.find("t:Settings/t:StartWhenAvailable",ns).text == "true"
    assert tree.find("t:Settings/t:MultipleInstancesPolicy",ns).text == "IgnoreNew"
    assert tree.find("t:Principals/t:Principal/t:LogonType",ns).text == "InteractiveToken"
    assert 'password' not in xml.lower()


@pytest.mark.parametrize("clock", ["25:00", "12:60", "8:00", "20:00;evil"])
def test_invalid_schedule_time(clock):
    with pytest.raises(ValueError): next_run(clock)


def test_web_local_security_and_initial_state(tmp_path):
    client = TestClient(create_app(Store(tmp_path)),base_url="http://127.0.0.1:8765")
    assert client.get("/").status_code == 200
    state = client.get("/api/state").json()
    assert state["courses"] == [] and state["schedule"]["enabled"] is False
    assert client.post("/api/sync").status_code == 403
    assert client.get("/api/state",headers={"Host":"evil.example"}).status_code == 403
    assert client.get("/api/state",headers={"Origin":"https://evil.example"}).status_code == 403
    headers = {"X-iSpace-Token":state["csrf"]}
    assert client.put("/api/schedule",headers=headers,json={"time":"25:00","enabled":True}).status_code == 422


def test_invalid_login_does_not_echo_password(tmp_path):
    client = TestClient(create_app(Store(tmp_path)),base_url="http://127.0.0.1:8765")
    token = client.get("/api/state").json()["csrf"]
    response = client.post("/api/login",headers={"X-iSpace-Token":token},json={"username":"","password":"do-not-echo"})
    assert response.status_code == 422
    assert "do-not-echo" not in response.text
