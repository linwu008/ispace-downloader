"""Smoke-test the actual frozen EXE on an isolated port and database."""
import os
import sys
import json
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

root = Path(__file__).resolve().parent.parent
mac = sys.platform == 'darwin'
package = root / ('dist/CourseNestHelper.app/Contents' if mac else 'dist/CourseNestHelper/_internal')
assert not list((package/'ispace').rglob('*.py')), 'Do not distribute project source files'
fixture = Path(tempfile.mkdtemp(prefix='coursenest-bundle-'))
exe = root / 'dist/CourseNestHelper/CourseNestHelper.exe'
if mac:
    import platform
    from ispace import __version__
    archive = root / f'dist/CourseNestHelper-{__version__}-macos-{platform.machine()}.zip'
    installed = fixture / 'Applications'
    subprocess.run(['/usr/bin/ditto', '-x', '-k', str(archive), str(installed)], check=True)
    bundle = installed / 'CourseNestHelper.app'
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)], check=True)
    exe = bundle / 'Contents/MacOS/CourseNestHelper'
process = subprocess.Popen([str(exe), '-m', 'ispace', '--data-dir', str(fixture), 'serve', '--port', '18768'],
                           cwd=fixture, env={**os.environ,'COURSENEST_NO_BROWSER':'1'},
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
try:
    with httpx.Client(base_url='http://127.0.0.1:18768',trust_env=False,timeout=2) as client:
        for _ in range(90):
            try:
                state=client.get('/api/state').json()
                break
            except (httpx.HTTPError,ValueError):
                if process.poll() is not None:raise RuntimeError('Frozen assistant exited before startup')
                time.sleep(.4)
        else:raise RuntimeError('Frozen assistant did not start')
        from ispace import __version__
        assert state['version']==__version__ and not state['busy']
        assert 'id="pair"' in client.get('/').text
        assert not client.get('/api/companion').json()['paired']
        assert client.get('/static/companion.js').status_code==200
        assert client.get('/static/logo.png').headers['content-type'].startswith('image/')
        assert client.get('/static/en.json').json()['我的课程'] == 'My courses'
        assert client.get('/api/updates').status_code==200
        assert client.post('/api/companion/disconnect').status_code==403
        assert (fixture/'index-pre-v0.4.sqlite3').is_file()
        if mac:
            assert state['platform'] == 'macos'
            assert client.get('/api/updates').json()['mode'] == 'manual'
        print('PASS frozen helper: isolated database, static UI, companion API, CSRF, backup and scheduled-task argument compatibility.')
finally:
    process.terminate();process.wait(timeout=15)

if mac:
    report = fixture / 'browser-check.json'
    subprocess.run([str(exe), 'browser-check', '--report', str(report)], cwd=fixture,
                   env={**os.environ, 'ISPACE_DATA_DIR': str(fixture), 'PATH': '/usr/bin:/bin', 'PLAYWRIGHT_BROWSERS_PATH': '/nonexistent'},
                   timeout=120, check=True)
    result = json.loads(report.read_text())
    assert result['automatic_login'] and result['visible_login']
    print('PASS bundled Chromium: automatic fixture login and visible session restoration without system browser or Python.')
