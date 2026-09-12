"""Outbound-only CourseNest device connector; school secrets stay in the local Vault."""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from filelock import FileLock, Timeout
from pydantic import BaseModel, Field

from . import catalog, courses
from .state import now


def server_origin(value):
    url = urlsplit(value.strip().rstrip('/'))
    local = url.hostname in {'localhost', '127.0.0.1', '::1'}
    if url.username or url.password or url.query or url.fragment or url.path or not url.hostname:
        raise ValueError('请填写网站首页地址，不要带路径、密码或参数')
    if url.scheme != 'https' and not (local and url.scheme == 'http'):
        raise ValueError('网站地址必须使用 HTTPS；本机验收地址可使用 HTTP')
    return value.strip().rstrip('/')


class Companion:
    def __init__(self, store, service, transport=None):
        self.store, self.service, self.transport = store, service, transport
        self.path = store.directory / 'companion.enc'
        self.stop_event = threading.Event()
        self.thread = None
        with store.connect() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='companion_receipts'").fetchone()
        backup = store.directory / 'index-pre-v0.4.sqlite3'
        if not exists and not backup.exists():
            with sqlite3.connect(store.path) as src, sqlite3.connect(backup) as dst:
                src.backup(dst)
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS companion_receipts (job_id TEXT PRIMARY KEY,result TEXT NOT NULL,created TEXT NOT NULL)')

    def lock(self):
        return FileLock(self.store.directory / 'companion.lock', timeout=0)

    def load(self):
        if not self.path.exists():
            return None
        try:
            return json.loads(self.service.vault.cipher().decrypt(self.path.read_bytes()))
        except Exception as exc:
            raise ValueError('设备配对信息无法读取，请在本机重新配对') from exc

    def save(self, config):
        temporary = self.path.with_suffix('.part')
        temporary.write_bytes(self.service.vault.cipher().encrypt(json.dumps(config).encode()))
        os.replace(temporary, self.path)

    def request(self, config, path, payload):
        with httpx.Client(base_url=config['server'], transport=self.transport, trust_env=False, timeout=20, follow_redirects=False) as client:
            response = client.post('/api' + path, json=payload, headers={'Authorization': 'Bearer ' + config['token']} if config.get('token') else {})
            if response.status_code == 401:
                raise ValueError('设备授权已失效，请在网站解除旧设备后重新配对')
            if response.status_code >= 400:
                try:
                    message = response.json().get('detail', '网站请求失败')
                except Exception:
                    message = '网站请求失败'
                raise ValueError(str(message)[:250])
            if response.is_redirect:
                raise ValueError('网站地址发生跳转，请填写最终的 HTTPS 首页地址')
            return response.json()

    def pair(self, server, code, name):
        with self.lock(), self.service.lock():
            if self.path.exists():
                raise ValueError('本机已配对，请先断开原网站')
            config = {'server': server_origin(server), 'name': name.strip()[:80] or socket.gethostname(), 'paused': False}
            # Verify local secure storage before consuming the single-use pairing code.
            self.service.vault.cipher()
            result = self.request(config, '/device/pair', {'code': code, 'name': config['name']})
            config.update(token=result['token'], device_id=result['device_id'])
            self.save(config)
            self.store.set('companion_status', {'message': '已配对，正在上报课程清单', 'at': now()})
        self.start()
        return self.status()

    def disconnect(self):
        with self.lock(), self.service.lock():
            self.path.unlink(missing_ok=True)
            self.store.set('companion_status', {'message': '本机已断开；如需撤销云端授权，请在网站解除设备', 'at': now()})

    def pause(self, paused):
        with self.lock():
            config = self.load()
            if not config:
                raise ValueError('请先配对网站')
            config['paused'] = bool(paused)
            self.save(config)

    def status(self):
        try:
            config = self.load()
            return {'paired': bool(config), 'server': config['server'] if config else '', 'name': config['name'] if config else '', 'paused': bool(config and config.get('paused')), **self.store.setting('companion_status', {})}
        except ValueError as exc:
            return {'paired': False, 'message': str(exc), 'broken': True}

    def snapshot(self, config):
        values = self.store.courses()
        with self.store.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT id,course_id,group_id,name,selected,status,path,error FROM materials ORDER BY id LIMIT 10000')]
            count = db.execute('SELECT COUNT(*) FROM materials').fetchone()[0]
        for row in rows:
            if row['path'] and not Path(row['path']).is_file():
                row['status'] = 'missing'
            row['path'] = row['path'] or ''
        return {'courses': [{'id': c['id'], 'name': c['name'], 'membership': c['membership'], 'sync_mode': c['sync_mode'], 'bound': bool(c['folder']), 'folder': c['folder'] or '', 'enabled': bool(c['enabled'])} for c in values],
                'groups': [{'id': g['id'], 'course_id': g['course_id'], 'title': g['title'], 'folder': g['folder'], 'position': g['position']} for g in catalog.groups(self.store)],
                'materials': rows, 'auth': self.store.setting('auth_state', 'not_logged_in'), 'paused': config.get('paused', False), 'local_schedule': self.store.setting('schedule_enabled', False), 'truncated': count > len(rows)}

    def execute(self, job):
        kind, payload = job['kind'], job['payload']
        if kind == 'refresh_courses':
            return self.service.execute('courses')
        if kind in {'add_courses', 'remove_courses', 'selection'}:
            with self.service.lock():
                if kind == 'selection':
                    courses.selection(self.store, payload['course_id'], payload['ids'], payload['mode'])
                else:
                    courses.membership(self.store, payload['ids'], kind == 'add_courses')
            return {'status': 'success', 'message': '文件选择已保存' if kind == 'selection' else '课程设置已更新，本地文件保留'}
        if kind == 'catalog':
            courses.get(self.store, payload['course_id'])
            return self.service.execute('catalog', course_id=payload['course_id'])
        if kind == 'sync':
            course_ids = [payload['course_id']] if payload.get('course_id') else None
            if course_ids:
                c = courses.get(self.store, course_ids[0])
                if c['membership'] != 'added' or not c['folder']:
                    raise ValueError('该课程尚未在本机授权目录，请先在助手中设置')
            return self.service.execute('sync', course_ids=course_ids)
        raise ValueError('网站下发的任务不受支持；未执行任何操作')

    def process(self, config, job):
        identifier = config['device_id'] + ':' + job['id']
        with self.store.connect() as db:
            receipt = db.execute('SELECT result FROM companion_receipts WHERE job_id=?', (identifier,)).fetchone()
        if receipt:
            result = json.loads(receipt['result'])
        else:
            finished = threading.Event()

            def renew():
                while not finished.wait(25):
                    try:
                        self.request(config, f"/device/jobs/{job['id']}/heartbeat", {'lease_token': job['lease_token']})
                    except Exception:
                        pass  # Local receipt makes redelivery safe after a connection loss.

            keeper = threading.Thread(target=renew, daemon=True)
            keeper.start()
            try:
                self.store.set('companion_status', {'message': '正在执行网站任务', 'at': now()})
                result = self.execute(job)
            except Exception as exc:
                from .moodle import safe_error
                result = {'status': 'failed', 'message': safe_error(exc)}
            finally:
                finished.set()
            # Save before acknowledgement. A lost reply must not run the command twice.
            with self.store.connect() as db:
                db.execute('INSERT OR REPLACE INTO companion_receipts VALUES (?,?,?)', (identifier, json.dumps(result), now()))
        self.request(config, f"/device/jobs/{job['id']}/complete", {'lease_token': job['lease_token'], 'status': result.get('status', 'failed'), 'result': result})

    def tick(self):
        try:
            with self.lock():
                config = self.load()
                if not config:
                    return
                paused = config.get('paused', False) or self.service.busy()
                reply = self.request(config, '/device/poll', {'snapshot': self.snapshot(config), 'paused': paused})
                if reply.get('job') and not paused:
                    self.process(config, reply['job'])
                    # Report completion without claiming another job in this tick.
                    self.request(config, '/device/poll', {'snapshot': self.snapshot(config), 'paused': True})
                self.store.set('companion_status', {'message': '已暂停接收网站任务' if config.get('paused') else '网站已连接，等待同步任务', 'at': now()})
        except Timeout:
            return
        except httpx.HTTPError:
            self.store.set('companion_status', {'message': '暂时无法连接网站，恢复网络后自动重连', 'at': now()})
        except Exception as exc:
            self.store.set('companion_status', {'message': str(exc)[:250] if isinstance(exc, ValueError) else '助手连接失败，请稍后重试', 'at': now()})

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()

        def loop():
            while not self.stop_event.is_set():
                self.tick()
                self.stop_event.wait(15)

        self.thread = threading.Thread(target=loop, name='coursenest-companion', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()


class PairBody(BaseModel):
    server: str = Field(max_length=500)
    code: str = Field(max_length=30)
    name: str = Field(default='我的 Windows 电脑', max_length=80)


class PauseBody(BaseModel):
    paused: bool


def install_routes(app, store, service):
    companion = Companion(store, service)
    app.state.companion = companion

    @app.get('/api/companion')
    def status():
        return companion.status()

    @app.post('/api/companion/pair')
    def pair(body: PairBody):
        return companion.pair(body.server, body.code, body.name)

    @app.post('/api/companion/disconnect')
    def disconnect():
        companion.disconnect()
        return {'ok': True}

    @app.put('/api/companion/pause')
    def pause(body: PauseBody):
        companion.pause(body.paused)
        return {'ok': True}

    if os.environ.get('ISPACE_COMPANION_AUTOSTART') == '1':
        app.router.add_event_handler('startup', companion.start)
        app.router.add_event_handler('shutdown', companion.stop)
