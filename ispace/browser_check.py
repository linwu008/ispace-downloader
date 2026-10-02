"""Offline bundled-browser diagnostic using a loopback school fixture, no credentials."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def check(report):
    from . import moodle, desktop
    desktop.prepare_browser()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/signed-in' or 'fixture=1' in self.headers.get('Cookie', ''):
                body = '<a href="/login/logout.php">Log out</a>'
            else:
                body = '''<form action="/signed-in" onsubmit="document.cookie='fixture=1; path=/'">
                <input name="username"><input name="password" type="password">
                <button type="submit">Sign in</button></form>'''
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(body.encode())
        def log_message(self, *args):
            pass

    class Vault:
        state = None
        def load_session(self):
            return self.state
        def save_session(self, state):
            self.state = state

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    old = moodle.BASE
    try:
        moodle.BASE = f'http://127.0.0.1:{server.server_port}'
        vault = Vault()
        moodle.browser_login(vault, 'fixture', 'fixture')
        assert any(c['name'] == 'fixture' for c in vault.state['cookies'])
        # A saved fixture session makes the visible login check unattended.
        moodle.browser_login(vault, manual=True)
        Path(report).write_text(json.dumps({'automatic_login': True, 'visible_login': True, **desktop.identity()}))
    finally:
        moodle.BASE = old
        server.shutdown()
        server.server_close()
        thread.join(5)
