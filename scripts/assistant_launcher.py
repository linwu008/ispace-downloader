"""Desktop tray launcher; also accepts the existing scheduled-task CLI arguments."""
import multiprocessing
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path


from ispace import desktop

alert = desktop.alert


def main():
    multiprocessing.freeze_support()
    os.environ.setdefault('ISPACE_DATA_DIR', str(desktop.default_data_dir()))
    login_start = sys.argv[1:] == ['--login-start']
    if login_start:
        sys.argv = sys.argv[:1]
        os.environ['COURSENEST_NO_BROWSER'] = '1'
    desktop.prepare_browser()
    if len(sys.argv) > 1:
        # Windows Task Scheduler uses the same -m ispace arguments as Python.
        if sys.argv[1:3] == ['-m', 'ispace']:
            sys.argv = [sys.argv[0], *sys.argv[3:]]
        from ispace.__main__ import main as cli
        cli()
        return
    os.environ['ISPACE_COMPANION_AUTOSTART'] = '1'
    import httpx
    import uvicorn
    import pystray
    from PIL import Image, ImageDraw
    from filelock import Timeout
    from ispace.web import create_app
    from ispace import __version__
    from ispace.updater import preferred_executable
    candidate = preferred_executable(os.environ['ISPACE_DATA_DIR'], __version__)
    if not desktop.is_macos() and getattr(sys, 'frozen', False) and candidate:
        import subprocess
        subprocess.Popen([str(candidate)], creationflags=subprocess.CREATE_NO_WINDOW)
        return
    url = 'http://127.0.0.1:8765'
    def open_ui(icon=None, item=None):
        try:
            state = httpx.get(url+'/api/state', trust_env=False, timeout=3).json()
            paired = httpx.get(url+'/api/companion', trust_env=False, timeout=3).json().get('paired')
            ready = paired and state.get('auth') == 'logged_in' and state.get('folder_setup', {}).get('mode') and not state.get('folder_warning')
        except (httpx.HTTPError, ValueError):
            ready = False
        webbrowser.open('https://bnbucoursenest.cn' if ready else url+'/')
    try:
        response = httpx.get(url + '/api/state', timeout=2, trust_env=False)
        if response.status_code == 200 and response.json().get('version') == __version__:
            open_ui()
            return
        alert('本机 8765 端口已有其他版本运行，请先关闭旧版助手，再启动 CourseNest。')
        return
    except httpx.HTTPError:
        pass
    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8765, access_log=False, log_config=None))
    serving = threading.Thread(target=server.run, name='local-web', daemon=True)
    serving.start()
    for _ in range(60):
        if server.started:
            break
        if not serving.is_alive():
            alert('同步助手启动失败，请检查 8765 端口是否被占用。')
            return
        time.sleep(.2)
    else:
        alert('同步助手启动超时。')
        server.should_exit = True
        return
    picture = Image.new('RGB', (64,64), '#159ab5')
    draw = ImageDraw.Draw(picture)
    draw.rounded_rectangle((12,18,52,49), radius=5, outline='#e5ecd8', width=4)
    draw.line((20,29,44,29), fill='#e5ecd8', width=3)
    draw.line((20,38,38,38), fill='#e5ecd8', width=3)
    try:
        import ispace
        picture = Image.open(Path(ispace.__file__).parent / 'static' / 'logo.png').convert('RGBA').resize((64,64))
    except OSError: pass
    def toggle_autorun(icon, item):
        try:
            desktop.set_autorun(not desktop.autorun_enabled())
            icon.update_menu()
        except (OSError, ValueError):
            alert('无法修改登录后启动设置。Mac 请先将应用移到 Applications 文件夹。')
    def quit_app(icon, item):
        try:
            with app.state.companion.lock(), app.state.service.lock():
                app.state.companion.stop()
                server.should_exit = True
                icon.stop()
        except Timeout:
            alert('仍有任务正在执行，请等待完成后退出，以保证文件完整。')
    tray = pystray.Icon('BNBUCourseNest', picture, 'BNBU CourseNest · v'+__version__, pystray.Menu(
        pystray.MenuItem('打开官网', open_ui, default=True),
        pystray.MenuItem('电脑设置', lambda icon,item:webbrowser.open(url+'/')),
        pystray.MenuItem('登录后启动', toggle_autorun, checked=lambda item: desktop.autorun_enabled()),
        pystray.MenuItem(lambda item: '待确认任务：' + str(len(app.state.companion.pending)), lambda icon, item: webbrowser.open(url+'/')),
        pystray.MenuItem('退出同步助手', quit_app)))
    if os.environ.get('COURSENEST_NO_BROWSER') != '1':
        open_ui()
    def shutdown_update():
        app.state.companion.stop()
        server.should_exit = True
        tray.stop()
    app.state.updater.shutdown = shutdown_update
    app.state.updater.companion = app.state.companion
    threading.Thread(target=app.state.updater.loop, args=(app.state.companion.stop_event,), daemon=True).start()
    def notify_pending():
        store = app.state.companion.store
        seen = set(store.setting('notified_jobs', []))
        while not app.state.companion.stop_event.wait(5):
            if not store.setting('desktop_prompts', True):
                continue
            pending = [p for p in app.state.companion.pending if p['ready'] and not p['dismissed'] and p['id'] not in seen]
            if pending:
                seen.update(p['id'] for p in pending)
                store.set('notified_jobs', sorted(seen)[-500:])
                desktop.notify(tray, '电脑已准备好。打开官网或电脑设置确认任务；可在助手设置关闭电脑提示。')
            desktop.refresh_menu(tray)
    threading.Thread(target=notify_pending, daemon=True).start()
    try:
        tray.run()
    finally:
        app.state.companion.stop()
        server.should_exit = True
        serving.join(timeout=10)
        app.state.service.pool.shutdown(wait=True)


if __name__ == '__main__':
    main()
